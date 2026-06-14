"""最小自动同步调度器。

在后台线程中周期性检查是否启用自动同步，若启用则调用
:meth:`SyncService.sync_enabled_subscriptions()` 并产出
:class:`~app.application.dto.scheduler_tick_event.SchedulerTickEvent`
供 UI 层消费。

设计目标：
- 单进程单线程，不与 Flet 主线程竞争
- 可停止、可重复启动、可观察
- 调度执行失败不杀死循环
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from app.application.dto.scheduler_tick_event import SchedulerTickEvent
from app.application.services.settings_service import SettingsService
from app.application.services.sync_service import SyncService
from app.domain.enums.trigger_type import SyncTriggerType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SchedulerTickCallback = Callable[[SchedulerTickEvent], None]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_INTERVAL_SECONDS = 10.0  # 停用状态下的轮询间隔
_CHECK_INTERVAL_SECONDS = 300.0  # 按订阅间隔模式下的检查频率（5 分钟）


# ---------------------------------------------------------------------------
# SyncScheduler
# ---------------------------------------------------------------------------


class SyncScheduler:
    """在后台线程中周期性触发全量自动同步。

    调度策略：
    - 每次循环先读取最新设置，允许运行期切换开关或调整频率
    - ``auto_sync_enabled=False`` 时每 10s 轮询一次，不执行同步
    - 频率由 ``global_sync_interval_minutes`` 控制，未配置时默认为 60 分钟
    - 单次同步失败仅记录日志并产出错误事件，不会终止循环

    Args:
        sync_service: 已注入 Repository 的同步编排服务
        settings_service: 运行期设置读写服务
    """

    def __init__(
        self,
        sync_service: SyncService,
        settings_service: SettingsService,
    ) -> None:
        self._sync_service = sync_service
        self._settings_service = settings_service

        # -- 线程控制 --
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._tick_lock = threading.Lock()  # 防止 tick 并发执行，同时供 stop() 等待

        # -- 状态 --
        self._lock = threading.Lock()
        self._running = False
        self._last_tick_event: SchedulerTickEvent | None = None

        # -- 回调 --
        self._listeners: list[SchedulerTickCallback] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """调度循环是否正在执行（线程安全）。"""
        with self._lock:
            return self._running

    @property
    def last_tick_event(self) -> SchedulerTickEvent | None:
        """最近一次调度完成的事件（线程安全），供 UI 轮询。"""
        with self._lock:
            return self._last_tick_event

    def start(self) -> None:
        """启动调度循环。

        重复调用是幂等的：若已在运行则直接返回。
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._loop,
            name="sync-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("SyncScheduler started.")

    def stop(self, timeout: float = 30.0) -> bool:
        """停止调度循环并等待后台线程退出。

        先发送停止信号，随后等待正在执行的 tick 完成（通过 ``_tick_lock``），
        最后 join 线程。已在运行时返回的 tick 不会进入下一次循环。

        已停止时调用为幂等操作。

        Args:
            timeout: 等待线程退出的最大秒数（默认 30s）

        Returns:
            ``True`` 表示干净退出；``False`` 表示超时，后台线程可能仍在运行
        """
        with self._lock:
            if not self._running:
                return True
            self._running = False

        self._stop_event.set()
        clean = True

        # 等待正在执行的 tick 结束（防 close() 提前关闭 DB）
        if not self._tick_lock.acquire(timeout=timeout):
            logger.warning(
                "Timed out waiting for in-flight scheduler tick to finish."
            )
            clean = False
        else:
            self._tick_lock.release()

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                clean = False

        logger.info("SyncScheduler stopped (clean=%s).", clean)
        return clean

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def add_tick_listener(self, callback: SchedulerTickCallback) -> None:
        """注册调度完成回调。

        回调在调度线程中被调用，实现方需自行处理线程安全。
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_tick_listener(self, callback: SchedulerTickCallback) -> None:
        """移除已注册的回调。"""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """主循环：轮询设置 → 判定同步模式 → 执行 → 等待间隔。

        两种模式：
        - **全局间隔模式** (``global_sync_interval_minutes`` 已配置):
          按固定间隔对所有启用订阅全量执行 :meth:`sync_enabled_subscriptions`。
        - **按订阅间隔模式** (``global_sync_interval_minutes`` 为 ``None``):
          高频检查（每 5 分钟），调用 :meth:`sync_due_subscriptions`
          仅同步到达各自 ``sync_interval_minutes`` 的订阅。
        """
        while not self._stop_event.is_set():
            try:
                settings = self._settings_service.get_all()
            except Exception:
                logger.exception("Failed to read settings in scheduler loop.")
                self._stop_event.wait(_POLL_INTERVAL_SECONDS)
                continue

            if settings.auto_sync_enabled:
                if settings.global_sync_interval_minutes is not None:
                    # 全局间隔：定时全量同步
                    self._do_tick(use_due_only=False)
                    interval_seconds = (
                        settings.global_sync_interval_minutes * 60.0
                    )
                else:
                    # 按订阅间隔：高频检查，仅同步到期订阅
                    self._do_tick(use_due_only=True)
                    interval_seconds = _CHECK_INTERVAL_SECONDS
            else:
                interval_seconds = _POLL_INTERVAL_SECONDS

            # 分段等待以便及时响应停止信号
            self._stop_event.wait(interval_seconds)

    def _do_tick(self, *, use_due_only: bool = False) -> None:
        """执行一次完整的同步周期。

        持有 ``_tick_lock`` 期间执行，既防止多个 tick 并发，
        也为 ``stop()`` 提供同步点——后者会在返回前等待此锁释放。

        Args:
            use_due_only: ``True`` 时调用 :meth:`sync_due_subscriptions`
                （仅同步到达各自间隔的订阅）；``False`` 时调用
                :meth:`sync_enabled_subscriptions`（全量同步）。
        """
        if not self._tick_lock.acquire(blocking=False):
            # 上一轮 tick 尚未完成，跳过本轮
            return
        try:
            started_at = datetime.now(timezone.utc)

            # 通知 running 状态
            running_event = SchedulerTickEvent(
                started_at=started_at,
                status="running",
            )
            self._store_and_notify(running_event)

            try:
                if use_due_only:
                    results = self._sync_service.sync_due_subscriptions(
                        trigger_type=SyncTriggerType.SCHEDULED,
                    )
                else:
                    results = self._sync_service.sync_enabled_subscriptions(
                        trigger_type=SyncTriggerType.SCHEDULED,
                    )

                # 网络调用期间可能已被 stop() → close() 拆毁资源，
                # 此时不再触碰 DB 或通知 listener，直接退出
                if self._stop_event.is_set():
                    return

                finished_at = datetime.now(timezone.utc)

                errors = [r.error_message for r in results if r.error_message]
                event = SchedulerTickEvent(
                    started_at=started_at,
                    finished_at=finished_at,
                    status="completed",
                    results=results,
                    error_message="; ".join(errors) if errors else None,
                )

            except Exception as exc:
                if self._stop_event.is_set():
                    return
                logger.exception("Scheduler tick failed.")
                finished_at = datetime.now(timezone.utc)
                event = SchedulerTickEvent(
                    started_at=started_at,
                    finished_at=finished_at,
                    status="failed",
                    error_message=str(exc),
                )

            self._store_and_notify(event)
        finally:
            self._tick_lock.release()

    def _store_and_notify(self, event: SchedulerTickEvent) -> None:
        """线程安全地存储最近事件并通知所有监听器。"""
        with self._lock:
            self._last_tick_event = event

        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                logger.exception("Scheduler tick listener raised.")
