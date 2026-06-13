from __future__ import annotations

import sqlite3
import uuid

from app.application.dto import SubscriptionUpsertInput, SyncResultDTO
from app.application.services.exceptions import (
    DuplicateSubscriptionNameError,
    InvalidSubscriptionRuleError,
    SubscriptionNotFoundError,
)
from app.application.services.sync_service import SyncService
from app.domain.models import Subscription
from app.infrastructure.db.repositories import SubscriptionRepository


# ---------------------------------------------------------------------------
# SubscriptionService
# ---------------------------------------------------------------------------


class SubscriptionService:
    """订阅管理的应用层封装。

    负责订阅 CRUD、启停、规则校验与单订阅手动同步入口。
    所有跨仓储写操作在本服务层统一提交事务。

    Args:
        connection: 已打开的 ``sqlite3.Connection``，调用方负责连接生命周期
        sync_service: 可选；用于委托单订阅同步。若未提供则自动创建
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        sync_service: SyncService | None = None,
    ) -> None:
        self._conn = connection
        self._sub_repo = SubscriptionRepository(connection)
        self._sync_service = sync_service or SyncService(connection)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_subscriptions(self) -> list[Subscription]:
        """返回所有订阅（按名称排序）"""
        return self._sub_repo.list_all()

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        """按 ID 获取单个订阅，不存在时返回 ``None``"""
        return self._sub_repo.get(subscription_id)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_subscription(self, input_: SubscriptionUpsertInput) -> Subscription:
        """创建新订阅。

        Args:
            input_: 已验证的订阅输入 DTO

        Returns:
            持久化后的 :class:`~app.domain.models.Subscription`

        Raises:
            DuplicateSubscriptionNameError: 库内已存在同名订阅
        """
        self._ensure_name_unique(input_.name, exclude_id=None)

        sub = Subscription(
            id=str(uuid.uuid4()),
            name=input_.name,
            enabled=input_.enabled,
            categories=input_.categories,
            include_keywords=input_.include_keywords,
            exclude_keywords=input_.exclude_keywords,
            authors=input_.authors,
            query_text=input_.query_text,
            sync_interval_minutes=input_.sync_interval_minutes,
        )
        try:
            self._sub_repo.save(sub)
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise DuplicateSubscriptionNameError(input_.name)
        return sub

    def update_subscription(
        self,
        subscription_id: str,
        input_: SubscriptionUpsertInput,
    ) -> Subscription:
        """更新已有订阅的全部规则字段。

        保留原 ID、``created_at`` 和 ``last_synced_at``；
        ``updated_at`` 会被刷新为当前 UTC 时间。

        Args:
            subscription_id: 要更新的订阅 ID
            input_: 新的订阅字段值

        Returns:
            更新后的 :class:`~app.domain.models.Subscription`

        Raises:
            SubscriptionNotFoundError: 指定 ID 不存在
            DuplicateSubscriptionNameError: 新名称与其他订阅冲突
        """
        existing = self._require_subscription(subscription_id)
        self._ensure_name_unique(input_.name, exclude_id=subscription_id)

        updated = Subscription(
            id=subscription_id,
            name=input_.name,
            enabled=input_.enabled,
            categories=input_.categories,
            include_keywords=input_.include_keywords,
            exclude_keywords=input_.exclude_keywords,
            authors=input_.authors,
            query_text=input_.query_text,
            sync_interval_minutes=input_.sync_interval_minutes,
        )
        try:
            self._sub_repo.save(updated)
            self._conn.commit()
        except sqlite3.IntegrityError:
            raise DuplicateSubscriptionNameError(input_.name)
        return updated

    def set_subscription_enabled(
        self,
        subscription_id: str,
        enabled: bool,
    ) -> Subscription:
        """启用或停用指定订阅。

        Args:
            subscription_id: 订阅 ID
            enabled: ``True`` 启用，``False`` 停用

        Returns:
            更新后的 :class:`~app.domain.models.Subscription`

        Raises:
            SubscriptionNotFoundError: 指定 ID 不存在
        """
        existing = self._require_subscription(subscription_id)

        toggled = Subscription(
            id=existing.id,
            name=existing.name,
            enabled=enabled,
            categories=existing.categories,
            include_keywords=existing.include_keywords,
            exclude_keywords=existing.exclude_keywords,
            authors=existing.authors,
            query_text=existing.query_text,
            sync_interval_minutes=existing.sync_interval_minutes,
        )
        self._sub_repo.save(toggled)
        self._conn.commit()
        return toggled

    def delete_subscription(self, subscription_id: str) -> None:
        """删除指定订阅。

        由于 ``sync_runs`` 表对 ``subscriptions`` 存在外键约束
        （``ON DELETE RESTRICT``），需先清理关联的同步运行记录。

        MVP 阶段仅删除订阅本身及其同步记录，不级联删除历史论文。

        Args:
            subscription_id: 要删除的订阅 ID

        Raises:
            SubscriptionNotFoundError: 指定 ID 不存在（幂等行为：
                调用方可选择捕获该异常以实现幂等删除）
        """
        self._require_subscription(subscription_id)

        # 清理同步运行记录（FK RESTRICT 约束要求）
        self._conn.execute(
            "DELETE FROM sync_runs WHERE subscription_id = ?",
            (subscription_id,),
        )
        self._sub_repo.delete(subscription_id)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_subscription(self, subscription_id: str) -> SyncResultDTO:
        """手动触发单订阅同步。

        内部委托给 :class:`~SyncService`，并确保目标订阅存在。

        Args:
            subscription_id: 要同步的订阅 ID

        Returns:
            包含插入/更新计数的 :class:`~SyncResultDTO`

        Raises:
            SubscriptionNotFoundError: 指定 ID 不存在
        """
        self._require_subscription(subscription_id)
        return self._sync_service.sync_subscription(subscription_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_subscription(self, subscription_id: str) -> Subscription:
        """获取订阅，不存在时抛出 :exc:`SubscriptionNotFoundError`"""
        sub = self._sub_repo.get(subscription_id)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id)
        return sub

    def _ensure_name_unique(
        self,
        name: str,
        exclude_id: str | None,
    ) -> None:
        """确保 *name* 在库内唯一。

        Args:
            name: 待检查的订阅名称
            exclude_id: 排除的订阅 ID（用于更新场景，允许保留自己的名称）
        """
        for sub in self._sub_repo.list_all():
            if sub.name == name and sub.id != exclude_id:
                raise DuplicateSubscriptionNameError(name)
