"""Paper Research — Application entry point.

启动阶段负责：
- 日志基础设施初始化（控制台 + 文件）
- 运行时配置解析（开发态 vs 发布态路径策略）
- 数据库连接与 schema 初始化
- Repository / Service 装配
- 为 UI 层提供统一的 :class:`AppContext`
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from types import TracebackType
from typing import Self

from app.application.services import (
    PaperLibraryService,
    PaperQueryService,
    SettingsService,
    StatusService,
    SubscriptionService,
    SyncService,
)
from app.infrastructure.db.connection import get_connection
from app.infrastructure.scheduler import SyncScheduler

logger = logging.getLogger(__name__)


class AppContext:
    """应用服务容器。

    为 Flet UI 层提供单一入口，避免各页面直接依赖 repository 或
    自行管理数据库连接生命周期。

    支持上下文管理器协议，确保连接资源在使用后被正确释放::

        with create_app_context() as ctx:
            subs = ctx.subscription_service.list_subscriptions()

    Attributes:
        connection: 已初始化的 SQLite 连接（启用外键 + Row 工厂）
        paper_library_service: 论文库管理服务（删除/解绑）
        paper_query_service: 论文查询聚合服务
        settings_service: 运行期设置读写服务
        status_service: 论文用户状态管理服务
        subscription_service: 订阅管理服务
        sync_service: 同步编排服务
        scheduler: 自动同步调度器（初始为未启动状态）
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        paper_library_service: PaperLibraryService,
        paper_query_service: PaperQueryService,
        settings_service: SettingsService,
        status_service: StatusService,
        subscription_service: SubscriptionService,
        sync_service: SyncService,
        scheduler: SyncScheduler,
    ) -> None:
        self.connection = connection
        self.paper_library_service = paper_library_service
        self.paper_query_service = paper_query_service
        self.settings_service = settings_service
        self.status_service = status_service
        self.subscription_service = subscription_service
        self.sync_service = sync_service
        self.scheduler = scheduler
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭数据库连接、停止调度器并释放 HTTP 客户端。

        关闭顺序：调度器 → HTTP 客户端 → 数据库。
        重复调用是幂等的——已关闭的上下文上再次调用无副作用。

        若调度器未能干净退出（``stop()`` 返回 ``False``），
        资源不会被主动关闭——后台线程可能仍在使用它们。
        由于线程为 daemon，进程退出时 OS 会自动回收。
        """
        if self._closed:
            logger.debug("AppContext.close() called on already-closed context, skipping.")
            return

        logger.info("Shutting down AppContext...")
        clean = self.scheduler.stop()
        if not clean:
            logger.warning(
                "Scheduler did not stop cleanly; "
                "skipping resource teardown to avoid use-after-close."
            )
            self._closed = True
            return

        logger.debug("Closing SyncService (HTTP client)...")
        self.sync_service.close()

        logger.debug("Closing database connection...")
        self.connection.close()

        self._closed = True
        logger.info("AppContext shutdown complete.")

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
    3. 实例化 :class:`SettingsService`
    4. 实例化 :class:`SubscriptionService`（注入 SyncService）
    5. 实例化 :class:`SyncScheduler`（注入 SyncService + SettingsService）

    Args:
        db_path: SQLite 数据库路径；``None`` 时从环境变量或默认路径读取
        auto_init: 是否自动执行 schema 初始化（默认 ``True``）

    Returns:
        装配完成的 :class:`AppContext`

    Raises:
        RuntimeError: 数据库初始化或 service 装配失败时抛出
    """
    try:
        connection = get_connection(db_path, auto_init=auto_init)
        logger.info("Database connection established: %s", db_path or "(default)")
    except Exception as exc:
        logger.critical("Failed to initialize database: %s", exc, exc_info=True)
        raise RuntimeError(f"Database initialization failed: {exc}") from exc

    try:
        paper_library_service = PaperLibraryService(connection)
        paper_query_service = PaperQueryService(connection)
        settings_service = SettingsService(connection)
        status_service = StatusService(connection)
        sync_service = SyncService(connection)
        subscription_service = SubscriptionService(connection, sync_service=sync_service)
        scheduler = SyncScheduler(sync_service, settings_service)

        logger.debug("All services and scheduler assembled.")
    except Exception as exc:
        logger.critical("Failed to assemble services: %s", exc, exc_info=True)
        connection.close()
        raise RuntimeError(f"Service assembly failed: {exc}") from exc

    return AppContext(
        connection=connection,
        paper_library_service=paper_library_service,
        paper_query_service=paper_query_service,
        settings_service=settings_service,
        status_service=status_service,
        subscription_service=subscription_service,
        sync_service=sync_service,
        scheduler=scheduler,
    )


# ---------------------------------------------------------------------------
# Flet desktop entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Flet application entry point.

    启动顺序：
    1. 解析运行时配置（路径、日志级别、开发/发布模式）
    2. 初始化日志系统（文件 + 控制台）
    3. 启动 Flet 桌面应用

    Launch with::

        python -m app.main

    Or run this file directly::

        python app/main.py

    Environment variables:
        ``PAPER_RESEARCH_DEV_MODE=1`` — 使用 ``./runtime/`` 作为数据根目录
        ``PAPER_RESEARCH_DB_PATH=<path>`` — 显式指定数据库路径
        ``PAPER_RESEARCH_LOG_LEVEL=DEBUG`` — 设置日志级别
    """
    from app.infrastructure.config.app_config import AppRuntimeConfig
    from app.infrastructure.logging.setup import setup_logging

    # -- 1. 解析配置（纯路径解析，无 I/O） --
    try:
        config = AppRuntimeConfig.create()
    except Exception as exc:
        # 配置解析失败 → 最小化控制台输出 + 退出
        print(f"[FATAL] Failed to resolve runtime config: {exc}", file=sys.stderr)
        sys.exit(1)

    # -- 2. 创建运行时目录（唯一触发文件系统 I/O 的位置） --
    try:
        config.paths.ensure_dirs()
    except OSError as exc:
        print(f"[FATAL] Cannot create runtime directories: {exc}", file=sys.stderr)
        sys.exit(1)

    # -- 3. 初始化日志（此时目录已确保存在） --
    log_check = setup_logging(config)
    if not log_check.ok:
        print(f"[FATAL] Logging initialization failed: {log_check.fatal_error}", file=sys.stderr)
        sys.exit(1)

    for warning in log_check.warnings:
        logger.warning("Startup warning: %s", warning)

    logger.info("===========================================")
    logger.info("  Paper Research — starting up")
    logger.info("  dev_mode  = %s", config.is_dev_mode)
    logger.info("  data_dir  = %s", config.data_dir)
    logger.info("  db_path   = %s", config.db_path)
    logger.info("  log_file  = %s", config.log_file)
    logger.info("  log_level = %s", config.log_level)
    logger.info("===========================================")

    # -- 3. 启动 Flet --
    import flet as ft

    from app.ui.app_shell import AppShell

    try:
        ft.app(target=AppShell(config=config))
    except Exception as exc:
        logger.critical("Flet application crashed: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Paper Research — exiting.")


if __name__ == "__main__":
    main()
