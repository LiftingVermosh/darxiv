from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.application.dto.paper_status_patch_input import PaperStatusPatchInput
from app.application.services.exceptions import (
    InvalidPaperStatusError,
    PaperNotFoundError,
)
from app.domain.models import PaperStatus
from app.domain.models._normalization import _normalize_string_list
from app.infrastructure.db.repositories.paper_repository import PaperRepository
from app.infrastructure.db.repositories.paper_status_repository import (
    PaperStatusRepository,
)


class StatusService:
    """论文用户状态的应用层封装。

    负责收藏、已读、忽略、评分、标签和备注等本地用户交互状态的
    读取与写入。所有状态变更统一经过本服务，避免 UI 直接依赖
    :class:`~app.infrastructure.db.repositories.paper_status_repository.PaperStatusRepository`。

    业务约束：
    - 不能为不存在的论文创建状态记录
    - ``rating`` 必须在 ``1..5`` 范围内
    - ``is_hidden=True`` 与 ``is_starred=True`` 互斥
    - 标签在写入前自动去重、去空白
    - ``updated_at`` 由服务层统一刷新

    Args:
        connection: 已打开的 ``sqlite3.Connection``，调用方负责连接生命周期
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._paper_repo = PaperRepository(connection)
        self._status_repo = PaperStatusRepository(connection)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_status(self, arxiv_id: str) -> PaperStatus:
        """返回论文的当前用户状态。

        若状态记录不存在，返回包含默认值的 :class:`PaperStatus` 对象
        （不会持久化到数据库）。

        Args:
            arxiv_id: 论文 ID

        Returns:
            现有状态或默认状态对象
        """
        existing = self._status_repo.get(arxiv_id)
        if existing is not None:
            return existing
        return self._make_default_status(arxiv_id)

    # ------------------------------------------------------------------
    # Boolean toggle methods
    # ------------------------------------------------------------------

    def set_starred(self, arxiv_id: str, value: bool) -> PaperStatus:
        """设置收藏状态。

        Args:
            arxiv_id: 论文 ID
            value: ``True`` 收藏，``False`` 取消收藏

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
            InvalidPaperStatusError: 与 ``is_hidden`` 冲突
        """
        status = self._load_or_default(arxiv_id)
        status.is_starred = value
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    def set_read(self, arxiv_id: str, value: bool) -> PaperStatus:
        """设置已读状态。

        Args:
            arxiv_id: 论文 ID
            value: ``True`` 已读，``False`` 未读

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
        """
        status = self._load_or_default(arxiv_id)
        status.is_read = value
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    def set_hidden(self, arxiv_id: str, value: bool) -> PaperStatus:
        """设置忽略/隐藏状态。

        Args:
            arxiv_id: 论文 ID
            value: ``True`` 忽略，``False`` 取消忽略

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
            InvalidPaperStatusError: 与 ``is_starred`` 冲突
        """
        status = self._load_or_default(arxiv_id)
        status.is_hidden = value
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    # ------------------------------------------------------------------
    # Scalar update methods
    # ------------------------------------------------------------------

    def update_note(self, arxiv_id: str, note: str | None) -> PaperStatus:
        """更新笔记/备注。

        传入 ``None`` 或空字符串均会清空备注。

        Args:
            arxiv_id: 论文 ID
            note: 新的笔记内容；``None`` 表示清空

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
        """
        status = self._load_or_default(arxiv_id)
        status.note = note if note else None
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    def update_rating(self, arxiv_id: str, rating: int | None) -> PaperStatus:
        """设置或清空评分。

        Args:
            arxiv_id: 论文 ID
            rating: 评分（1-5）；``None`` 表示清空

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
            InvalidPaperStatusError: 评分不在 1..5 范围内
        """
        if rating is not None and not (1 <= rating <= 5):
            raise InvalidPaperStatusError(
                f"Rating must be between 1 and 5, got {rating}"
            )

        status = self._load_or_default(arxiv_id)
        status.rating = rating
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    def update_tags(self, arxiv_id: str, tags: list[str]) -> PaperStatus:
        """整体覆盖标签集合。

        写入前自动去重、去空白。

        Args:
            arxiv_id: 论文 ID
            tags: 新的标签列表；空列表表示清空所有标签

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
        """
        status = self._load_or_default(arxiv_id)
        try:
            # 不调 list(tags) —— _normalize_string_list 已支持 str 输入，会按单标签处理
            status.tags = _normalize_string_list(tags, field_name="tags")
        except ValueError as exc:
            raise InvalidPaperStatusError(str(exc))
        status.updated_at = self._now()
        return self._validate_and_persist(status)

    # ------------------------------------------------------------------
    # Batch update (optional aggregate write)
    # ------------------------------------------------------------------

    def patch_status(
        self, arxiv_id: str, patch: PaperStatusPatchInput
    ) -> PaperStatus:
        """聚合写接口：一次请求写入多个状态字段。

        仅 ``patch`` 中非 ``None`` 的字段会被应用；
        ``None`` 字段保持当前值不变。

        Args:
            arxiv_id: 论文 ID
            patch: 包含待修改字段的 :class:`PaperStatusPatchInput`

        Returns:
            更新后的 :class:`PaperStatus`

        Raises:
            PaperNotFoundError: 目标论文不存在
            InvalidPaperStatusError: 写入后的状态违反业务约束
        """
        status = self._load_or_default(arxiv_id)

        # 仅覆盖非 None 字段
        if patch.is_starred is not None:
            status.is_starred = patch.is_starred
        if patch.is_read is not None:
            status.is_read = patch.is_read
        if patch.is_hidden is not None:
            status.is_hidden = patch.is_hidden
        if patch.rating is not None:
            status.rating = patch.rating
        if patch.note is not None:
            status.note = patch.note if patch.note else None
        if patch.tags is not None:
            status.tags = list(patch.tags)

        status.updated_at = self._now()
        return self._validate_and_persist(status)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_or_default(self, arxiv_id: str) -> PaperStatus:
        """获取现有状态或返回默认对象，并确保论文存在。

        Raises:
            PaperNotFoundError: 目标论文不存在
        """
        if self._paper_repo.get(arxiv_id) is None:
            raise PaperNotFoundError(arxiv_id)

        existing = self._status_repo.get(arxiv_id)
        if existing is not None:
            return existing
        return self._make_default_status(arxiv_id)

    def _validate_and_persist(self, status: PaperStatus) -> PaperStatus:
        """校验业务约束后将状态写入数据库并提交事务。

        返回经过 Pydantic 完整规范化后的 :class:`PaperStatus`，
        调用方应使用返回值而非传入的原始 *status* 对象。

        Raises:
            InvalidPaperStatusError: 业务约束校验失败
        """
        try:
            # 通过重新构造触发校验与规范化（Pydantic 的 validators 只在构造时运行）
            validated = PaperStatus(
                arxiv_id=status.arxiv_id,
                is_starred=status.is_starred,
                is_read=status.is_read,
                is_hidden=status.is_hidden,
                rating=status.rating,
                note=status.note,
                tags=status.tags,
                updated_at=status.updated_at,
            )
        except ValueError as exc:
            raise InvalidPaperStatusError(str(exc))

        self._status_repo.upsert(validated)
        self._conn.commit()
        return validated

    @staticmethod
    def _make_default_status(arxiv_id: str) -> PaperStatus:
        """生成默认状态视图。

        默认值约定：
        - ``is_starred = False``
        - ``is_read = False``
        - ``is_hidden = False``
        - ``rating = None``
        - ``note = None``
        - ``tags = []``
        """
        return PaperStatus(arxiv_id=arxiv_id)

    @staticmethod
    def _now() -> datetime:
        """返回当前 UTC 时间"""
        return datetime.now(timezone.utc)
