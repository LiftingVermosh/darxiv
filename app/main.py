"""Paper Research — Application entry point.

启动阶段负责：
- 数据库连接与 schema 初始化
- Repository / Service 装配
- 为 UI 层提供统一的 :class:`AppContext`
"""

from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Self

from app.application.services import (
    PaperQueryService,
    StatusService,
    SubscriptionService,
    SyncService,
)
from app.infrastructure.db.connection import get_connection


class AppContext:
    """应用服务容器。

    为 Flet UI 层提供单一入口，避免各页面直接依赖 repository 或
    自行管理数据库连接生命周期。

    支持上下文管理器协议，确保连接资源在使用后被正确释放::

        with create_app_context() as ctx:
            subs = ctx.subscription_service.list_subscriptions()

    Attributes:
        connection: 已初始化的 SQLite 连接（启用外键 + Row 工厂）
        paper_query_service: 论文查询聚合服务
        status_service: 论文用户状态管理服务
        subscription_service: 订阅管理服务
        sync_service: 同步编排服务
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        paper_query_service: PaperQueryService,
        status_service: StatusService,
        subscription_service: SubscriptionService,
        sync_service: SyncService,
    ) -> None:
        self.connection = connection
        self.paper_query_service = paper_query_service
        self.status_service = status_service
        self.subscription_service = subscription_service
        self.sync_service = sync_service
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭数据库连接。

        重复调用是幂等的——已关闭的上下文上再次调用无副作用。
        """
        if not self._closed:
            self.connection.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.close()
        return False

    @property
    def closed(self) -> bool:
        """连接是否已关闭（只读）"""
        return self._closed


def create_app_context(
    db_path: str | None = None,
    *,
    auto_init: bool = True,
) -> AppContext:
    """装配完整的应用上下文。

    按顺序完成：
    1. 获取 SQLite 连接（必要时自动执行 schema 初始化）
    2. 实例化 :class:`SyncService`
    3. 实例化 :class:`SubscriptionService`（注入 SyncService）

    Args:
        db_path: SQLite 数据库路径；``None`` 时从环境变量或默认路径读取
        auto_init: 是否自动执行 schema 初始化（默认 ``True``）

    Returns:
        装配完成的 :class:`AppContext`
    """
    connection = get_connection(db_path, auto_init=auto_init)
    paper_query_service = PaperQueryService(connection)
    status_service = StatusService(connection)
    sync_service = SyncService(connection)
    subscription_service = SubscriptionService(connection, sync_service=sync_service)

    return AppContext(
        connection=connection,
        paper_query_service=paper_query_service,
        status_service=status_service,
        subscription_service=subscription_service,
        sync_service=sync_service,
    )
