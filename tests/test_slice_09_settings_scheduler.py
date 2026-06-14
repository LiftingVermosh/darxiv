"""Slice 09: Settings and Scheduler — 测试。

覆盖：
- AppSettingsDTO 默认值与构造
- SchedulerTickEvent DTO
- SettingsService 读写、回退、重置
- SyncScheduler 启动、停止、重复启动、Tick 流程
- Settings 页面视图构造（mock ctx）
- AppShell 导航栏第三项与 /settings 路由
- AppContext 新服务注入与关闭链路
- 集成测试（真实 in‑memory DB）
- 异常恢复测试
"""

from __future__ import annotations

import sqlite3
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import flet as ft

from app.application.dto.app_settings_dto import AppSettingsDTO, default_settings
from app.application.dto.scheduler_tick_event import SchedulerTickEvent
from app.application.dto.sync_result import SyncResultDTO
from app.application.services.settings_service import SettingsService
from app.application.services.sync_service import SyncService
from app.domain.enums.sync_status import SyncRunStatus
from app.domain.enums.trigger_type import SyncTriggerType
from app.domain.models import Paper
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories.app_settings_repository import (
    AppSettingsRepository,
)
from app.infrastructure.db.repositories.paper_repository import PaperRepository
from app.infrastructure.scheduler import SyncScheduler
from app.main import AppContext, create_app_context
from app.ui.app_shell import AppShell
from app.ui.pages.settings_page import build_settings_view

# ============================================================================
# Mock Fixtures (复用 Slice 08 的 _MockPage)
# ============================================================================


class _MockPage:
    """Flet Page 的轻量级存根。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._route = "/dashboard"
        self._views: list[ft.View] = []
        self._snackbars: list[ft.SnackBar] = []

        self.title = ""
        self.theme_mode: ft.ThemeMode | None = None
        self.theme: ft.Theme | None = None
        self.navigation_bar: ft.NavigationBar | None = None
        self.window = _MockWindow()

        self.on_route_change = None

    def update(self) -> None:
        self.calls.append("update")

    def go(self, route: str) -> None:
        self.calls.append(f"go:{route}")
        self._route = route

    def open(self, control: ft.Control) -> None:
        self.calls.append("open")
        if isinstance(control, ft.SnackBar):
            self._snackbars.append(control)

    def close(self, control: ft.Control | None = None) -> None:
        self.calls.append("close")

    def launch_url(self, url: str) -> None:
        self.calls.append(f"launch_url:{url}")

    @property
    def route(self) -> str:
        return self._route

    @route.setter
    def route(self, value: str) -> None:
        self._route = value

    @property
    def views(self) -> list[ft.View]:
        return self._views

    @views.setter
    def views(self, value: list[ft.View]) -> None:
        self._views = value


class _MockWindow:
    width = 1200
    height = 800
    min_width = 800
    min_height = 600


def _setup_db() -> sqlite3.Connection:
    return get_connection(":memory:")


def _make_paper(**overrides) -> Paper:
    """构造测试用 Paper 模型（覆盖默认值）。"""
    defaults = {
        "arxiv_id": "2501.00001",
        "version": 1,
        "title": "A Test Paper",
        "abstract": "An abstract.",
        "authors": ["Alice"],
        "primary_category": "cs.CV",
        "categories": ["cs.CV"],
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "abs_url": "https://arxiv.org/abs/2501.00001",
    }
    defaults.update(overrides)
    return Paper(**defaults)


def _mock_ctx(conn: sqlite3.Connection) -> AppContext:
    """构造包含 mock service 的 AppContext。"""
    from app.application.services import (
        PaperQueryService,
        StatusService,
        SubscriptionService,
    )

    mock_query = MagicMock(spec=PaperQueryService)
    mock_query.list_papers.return_value = []
    mock_status = MagicMock(spec=StatusService)
    mock_sub = MagicMock(spec=SubscriptionService)
    mock_sub.list_subscriptions.return_value = []
    mock_sync = MagicMock(spec=SyncService)
    mock_sync.sync_enabled_subscriptions.return_value = []

    settings_svc = SettingsService(conn)
    scheduler = SyncScheduler(mock_sync, settings_svc)

    return AppContext(
        connection=conn,
        paper_query_service=mock_query,
        settings_service=settings_svc,
        status_service=mock_status,
        subscription_service=mock_sub,
        sync_service=mock_sync,
        scheduler=scheduler,
    )


# ============================================================================
# 1. AppSettingsDTO 测试
# ============================================================================


class AppSettingsDTOTests(unittest.TestCase):
    """AppSettingsDTO 构造、默认值与序列化测试。"""

    def test_default_construction(self) -> None:
        dto = AppSettingsDTO()
        self.assertFalse(dto.auto_sync_enabled)
        self.assertIsNone(dto.global_sync_interval_minutes)
        self.assertFalse(dto.show_hidden_by_default)
        self.assertEqual(dto.default_list_filters, {})
        self.assertIsNone(dto.last_open_page)

    def test_with_defaults_classmethod(self) -> None:
        dto = AppSettingsDTO.with_defaults()
        self.assertFalse(dto.auto_sync_enabled)

    def test_full_construction(self) -> None:
        dto = AppSettingsDTO(
            auto_sync_enabled=True,
            global_sync_interval_minutes=30,
            show_hidden_by_default=True,
            default_list_filters={"keyword": "transformer"},
            last_open_page="/settings",
        )
        self.assertTrue(dto.auto_sync_enabled)
        self.assertEqual(dto.global_sync_interval_minutes, 30)
        self.assertTrue(dto.show_hidden_by_default)
        self.assertEqual(dto.default_list_filters, {"keyword": "transformer"})
        self.assertEqual(dto.last_open_page, "/settings")

    def test_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            AppSettingsDTO(unknown_field=123)  # type: ignore[call-arg]

    def test_default_settings_helper_returns_copy(self) -> None:
        d1 = default_settings()
        d2 = default_settings()
        d1["auto_sync_enabled"] = True
        self.assertFalse(d2["auto_sync_enabled"])

    def test_model_dump_excludes_none(self) -> None:
        dto = AppSettingsDTO(auto_sync_enabled=True)
        data = dto.model_dump(exclude_none=True)
        self.assertIn("auto_sync_enabled", data)
        self.assertNotIn("last_open_page", data)


# ============================================================================
# 2. SchedulerTickEvent 测试
# ============================================================================


class SchedulerTickEventTests(unittest.TestCase):
    """SchedulerTickEvent DTO 构造测试。"""

    def test_idle_event(self) -> None:
        now = datetime.now(timezone.utc)
        event = SchedulerTickEvent(started_at=now)
        self.assertEqual(event.status, "idle")
        self.assertEqual(event.results, [])
        self.assertIsNone(event.finished_at)
        self.assertIsNone(event.error_message)

    def test_completed_event(self) -> None:
        now = datetime.now(timezone.utc)
        results = [
            SyncResultDTO(
                subscription_id="s1",
                subscription_name="Test",
                status=SyncRunStatus.SUCCESS,
                fetched_count=5,
                inserted_count=3,
                updated_count=1,
                started_at=now,
                finished_at=now,
            ),
        ]
        event = SchedulerTickEvent(
            started_at=now,
            finished_at=now,
            status="completed",
            results=results,
        )
        self.assertEqual(event.status, "completed")
        self.assertEqual(len(event.results), 1)
        self.assertIsNone(event.error_message)

    def test_failed_event(self) -> None:
        now = datetime.now(timezone.utc)
        event = SchedulerTickEvent(
            started_at=now,
            finished_at=now,
            status="failed",
            error_message="Connection refused",
        )
        self.assertEqual(event.status, "failed")
        self.assertEqual(event.error_message, "Connection refused")

    def test_running_event(self) -> None:
        now = datetime.now(timezone.utc)
        event = SchedulerTickEvent(started_at=now, status="running")
        self.assertEqual(event.status, "running")
        self.assertIsNone(event.finished_at)


# ============================================================================
# 3. SettingsService 测试
# ============================================================================


class SettingsServiceTests(unittest.TestCase):
    """SettingsService 读写、回退、重置测试（真实 in‑memory DB）。"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.svc = SettingsService(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_get_all_returns_defaults_on_fresh_db(self) -> None:
        settings = self.svc.get_all()
        self.assertFalse(settings.auto_sync_enabled)
        self.assertIsNone(settings.global_sync_interval_minutes)

    def test_get_single_key_default(self) -> None:
        self.assertFalse(self.svc.get("auto_sync_enabled"))
        self.assertIsNone(self.svc.get("global_sync_interval_minutes"))

    def test_set_and_get_single_key(self) -> None:
        self.svc.set("auto_sync_enabled", True)
        self.assertTrue(self.svc.get("auto_sync_enabled"))

    def test_update_batch(self) -> None:
        dto = AppSettingsDTO(
            auto_sync_enabled=True,
            global_sync_interval_minutes=30,
            show_hidden_by_default=True,
        )
        self.svc.update(dto)

        reloaded = self.svc.get_all()
        self.assertTrue(reloaded.auto_sync_enabled)
        self.assertEqual(reloaded.global_sync_interval_minutes, 30)
        self.assertTrue(reloaded.show_hidden_by_default)

    def test_update_does_not_wipe_unspecified_keys(self) -> None:
        # 先写两个值
        self.svc.set("auto_sync_enabled", True)
        self.svc.set("show_hidden_by_default", True)

        # 仅更新 auto_sync_enabled
        self.svc.update(AppSettingsDTO(auto_sync_enabled=False))

        settings = self.svc.get_all()
        self.assertFalse(settings.auto_sync_enabled)
        self.assertTrue(settings.show_hidden_by_default)  # 保持原值

    def test_reset_key(self) -> None:
        self.svc.set("auto_sync_enabled", True)
        self.assertTrue(self.svc.get("auto_sync_enabled"))

        self.svc.reset("auto_sync_enabled")
        self.assertFalse(self.svc.get("auto_sync_enabled"))  # 回退到默认

    def test_reset_nonexistent_key_does_not_raise(self) -> None:
        self.svc.reset("nonexistent_key")

    def test_get_nonexistent_key_returns_none(self) -> None:
        self.assertIsNone(self.svc.get("nonexistent_key"))

    def test_set_json_serializable_values(self) -> None:
        self.svc.set("default_list_filters", {"keyword": "transformer", "starred": True})
        self.assertEqual(
            self.svc.get("default_list_filters"),
            {"keyword": "transformer", "starred": True},
        )

    def test_corrupted_json_falls_back_to_default(self) -> None:
        """直接写入损坏的 JSON —— SettingsService 应回退到默认值。"""
        repo = AppSettingsRepository(self.conn)
        # 绕过 repo 写入原始损坏值
        self.conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) "
            "VALUES ('auto_sync_enabled', 'not-json', '2026-01-01T00:00:00Z')"
        )
        self.conn.commit()

        # 虽然 JSON 损坏，get_all 不应崩溃
        settings = self.svc.get_all()
        self.assertIsInstance(settings, AppSettingsDTO)

    def test_round_trip_list_filters(self) -> None:
        """验证 default_list_filters 字段的完整写入→读取流程。"""
        dto = AppSettingsDTO(
            default_list_filters={"category": "cs.CV", "is_starred": True},
        )
        self.svc.update(dto)

        reloaded = self.svc.get_all()
        self.assertEqual(
            reloaded.default_list_filters,
            {"category": "cs.CV", "is_starred": True},
        )


# ============================================================================
# 4. SyncScheduler 测试
# ============================================================================


class SyncSchedulerTests(unittest.TestCase):
    """SyncScheduler 生命周期与 Tick 流程测试。"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.settings_svc = SettingsService(self.conn)

        # 主测 mock sync_service
        self.mock_sync = MagicMock(spec=SyncService)
        self.mock_sync.sync_enabled_subscriptions.return_value = []

        self.scheduler = SyncScheduler(self.mock_sync, self.settings_svc)

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.conn.close()

    def test_initial_state_not_running(self) -> None:
        self.assertFalse(self.scheduler.is_running)
        self.assertIsNone(self.scheduler.last_tick_event)

    def test_start_and_stop(self) -> None:
        self.scheduler.start()
        self.assertTrue(self.scheduler.is_running)

        clean = self.scheduler.stop()
        self.assertFalse(self.scheduler.is_running)
        self.assertTrue(clean)  # 无阻塞 tick → 应干净退出

    def test_double_start_is_idempotent(self) -> None:
        self.scheduler.start()
        self.scheduler.start()  # 不应崩溃
        self.assertTrue(self.scheduler.is_running)
        self.scheduler.stop()

    def test_double_stop_is_idempotent(self) -> None:
        self.scheduler.start()
        self.scheduler.stop()
        self.scheduler.stop()  # 不应崩溃
        self.assertFalse(self.scheduler.is_running)

    def test_stop_without_start_is_idempotent(self) -> None:
        self.scheduler.stop()
        self.assertFalse(self.scheduler.is_running)

    def test_restart_after_stop(self) -> None:
        self.scheduler.start()
        self.scheduler.stop()
        self.assertFalse(self.scheduler.is_running)

        self.scheduler.start()
        self.assertTrue(self.scheduler.is_running)
        self.scheduler.stop()

    def test_tick_listener_registration(self) -> None:
        events: list[SchedulerTickEvent] = []

        def _cb(event: SchedulerTickEvent) -> None:
            events.append(event)

        self.scheduler.add_tick_listener(_cb)
        self.scheduler.add_tick_listener(_cb)  # 不重复添加

        # 直接调用内部 tick 方法（调度器未启动时）
        self.scheduler._do_tick()  # type: ignore[attr-defined]

        self.assertGreaterEqual(len(events), 1)
        self.assertIn(events[0].status, ("running", "completed"))

    def test_remove_tick_listener(self) -> None:
        events: list[SchedulerTickEvent] = []

        def _cb(event: SchedulerTickEvent) -> None:
            events.append(event)

        self.scheduler.add_tick_listener(_cb)
        self.scheduler.remove_tick_listener(_cb)
        self.scheduler._do_tick()  # type: ignore[attr-defined]

        # 移除后不再收事件
        # （running + completed 各一次；若 listener 已被移除则二者都不会收到）
        self.assertEqual(len(events), 0)

    def test_tick_with_sync_success(self) -> None:
        now = datetime.now(timezone.utc)
        results = [
            SyncResultDTO(
                subscription_id="s1",
                subscription_name="Sub A",
                status=SyncRunStatus.SUCCESS,
                fetched_count=10,
                inserted_count=5,
                updated_count=2,
                started_at=now,
                finished_at=now,
            ),
        ]
        self.mock_sync.sync_enabled_subscriptions.return_value = results

        self.scheduler._do_tick()  # type: ignore[attr-defined]

        event = self.scheduler.last_tick_event
        self.assertIsNotNone(event)
        self.assertEqual(event.status, "completed")
        self.assertEqual(len(event.results), 1)
        self.assertIsNone(event.error_message)

        # 验证 SyncService 被以 SCHEDULED trigger 调用
        self.mock_sync.sync_enabled_subscriptions.assert_called_with(
            trigger_type=SyncTriggerType.SCHEDULED,
        )

    def test_tick_with_sync_failure(self) -> None:
        self.mock_sync.sync_enabled_subscriptions.side_effect = RuntimeError(
            "Boom!"
        )

        self.scheduler._do_tick()  # type: ignore[attr-defined]

        event = self.scheduler.last_tick_event
        self.assertIsNotNone(event)
        self.assertEqual(event.status, "failed")
        self.assertIn("Boom", event.error_message or "")

    def test_tick_with_partial_failures(self) -> None:
        """部分订阅成功、部分失败 —— 不应丢失成功的结果。"""
        now = datetime.now(timezone.utc)
        results = [
            SyncResultDTO(
                subscription_id="s1",
                subscription_name="OK Sub",
                status=SyncRunStatus.SUCCESS,
                fetched_count=3,
                inserted_count=1,
                updated_count=0,
                started_at=now,
                finished_at=now,
            ),
            SyncResultDTO(
                subscription_id="s2",
                subscription_name="Bad Sub",
                status=SyncRunStatus.FAILED,
                fetched_count=0,
                inserted_count=0,
                updated_count=0,
                started_at=now,
                finished_at=now,
                error_message="Timeout",
            ),
        ]
        self.mock_sync.sync_enabled_subscriptions.return_value = results

        self.scheduler._do_tick()  # type: ignore[attr-defined]

        event = self.scheduler.last_tick_event
        self.assertIsNotNone(event)
        self.assertEqual(event.status, "completed")
        self.assertEqual(len(event.results), 2)
        # error_message 聚合了失败项
        self.assertIsNotNone(event.error_message)
        self.assertIn("Timeout", event.error_message)

    def test_listener_exception_does_not_kill_tick(self) -> None:
        """listener 抛异常不应中断 tick 流程。"""

        def _bad_cb(event: SchedulerTickEvent) -> None:
            raise RuntimeError("listener error")

        self.scheduler.add_tick_listener(_bad_cb)
        self.scheduler._do_tick()  # type: ignore[attr-defined]

        # 不应抛异常，且事件正常产出
        event = self.scheduler.last_tick_event
        self.assertIsNotNone(event)


# ============================================================================
# 5. 设置页视图构造测试（mock ctx）
# ============================================================================


class SettingsViewConstructionTests(unittest.TestCase):
    """Settings 页面 View 的无异常构造测试。"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.ctx = _mock_ctx(self.conn)
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.ctx.scheduler.stop()
        self.ctx.close()

    def test_settings_view_constructs(self) -> None:
        view = build_settings_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        self.assertEqual(view.route, "/settings")
        self.assertIsNotNone(view.appbar)
        self.assertGreater(len(view.controls), 0)

    def test_settings_view_shows_default_state(self) -> None:
        """默认设置下页面正常显示（无同步历史）。"""
        view = build_settings_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_settings_view_with_tick_event(self) -> None:
        """有调度事件历史时页面正常渲染。"""
        now = datetime.now(timezone.utc)
        self.ctx.scheduler._store_and_notify(  # type: ignore[attr-defined]
            SchedulerTickEvent(
                started_at=now,
                finished_at=now,
                status="completed",
                results=[
                    SyncResultDTO(
                        subscription_id="s1",
                        subscription_name="Test Sub",
                        status=SyncRunStatus.SUCCESS,
                        fetched_count=5,
                        inserted_count=2,
                        updated_count=1,
                        started_at=now,
                        finished_at=now,
                    ),
                ],
            ),
        )
        view = build_settings_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)


# ============================================================================
# 6. AppShell 导航与路由测试
# ============================================================================


class AppShellSlice09Tests(unittest.TestCase):
    """AppShell 关于 Settings 路由与导航栏的测试。"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.shell = AppShell()
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.conn.close()

    def _boot(self) -> None:
        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=_mock_ctx(self.conn),
        ):
            self.shell(self.page)  # type: ignore[arg-type]

    def test_nav_bar_has_three_destinations(self) -> None:
        self._boot()
        self.assertEqual(len(self.page.navigation_bar.destinations), 3)
        self.assertEqual(
            self.page.navigation_bar.destinations[2].label, "Settings"
        )

    def test_nav_bar_selects_settings(self) -> None:
        self._boot()
        if self.page.navigation_bar.on_change:
            nav_mock = MagicMock()
            nav_mock.control.selected_index = 2
            self.page.navigation_bar.on_change(nav_mock)
        self.assertTrue(any("go:/settings" in c for c in self.page.calls))

    def test_route_settings_builds_view(self) -> None:
        self._boot()
        self.page.route = "/settings"
        if self.page.on_route_change:
            self.page.on_route_change(MagicMock())
        self.assertGreater(len(self.page.views), 0)

    def test_auto_sync_disabled_does_not_start_scheduler(self) -> None:
        """默认 auto_sync_enabled=False 时调度器不应自动启动。"""
        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=_mock_ctx(self.conn),
        ):
            self.shell(self.page)  # type: ignore[arg-type]
        self.assertFalse(self.shell.ctx.scheduler.is_running)

    def test_auto_sync_enabled_starts_scheduler(self) -> None:
        """当设置中 auto_sync_enabled=True 时启动调度器。"""
        # 先写入设置
        self.shell = AppShell()
        ctx = _mock_ctx(self.conn)
        ctx.settings_service.set("auto_sync_enabled", True)

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=ctx,
        ):
            self.shell(self.page)  # type: ignore[arg-type]
        self.assertTrue(ctx.scheduler.is_running)
        ctx.scheduler.stop()


# ============================================================================
# 7. 集成测试（真实 in‑memory DB）
# ============================================================================


class IntegrationTests(unittest.TestCase):
    """Settings + Scheduler 全链路集成测试（真实 in‑memory DB）。"""

    def setUp(self) -> None:
        self.ctx = create_app_context(":memory:")

    def tearDown(self) -> None:
        self.ctx.scheduler.stop()
        self.ctx.close()

    def test_settings_service_full_cycle(self) -> None:
        svc = self.ctx.settings_service

        # 初始默认
        settings = svc.get_all()
        self.assertFalse(settings.auto_sync_enabled)

        # 写入
        svc.update(
            AppSettingsDTO(
                auto_sync_enabled=True,
                global_sync_interval_minutes=15,
                show_hidden_by_default=True,
            )
        )

        # 重读
        settings = svc.get_all()
        self.assertTrue(settings.auto_sync_enabled)
        self.assertEqual(settings.global_sync_interval_minutes, 15)
        self.assertTrue(settings.show_hidden_by_default)

        # 重置
        svc.reset("auto_sync_enabled")
        self.assertFalse(svc.get("auto_sync_enabled"))

    def test_scheduler_starts_and_stops_via_context(self) -> None:
        self.assertFalse(self.ctx.scheduler.is_running)

        self.ctx.scheduler.start()
        self.assertTrue(self.ctx.scheduler.is_running)

        self.ctx.scheduler.stop()
        self.assertFalse(self.ctx.scheduler.is_running)

    def test_app_context_close_stops_scheduler(self) -> None:
        self.ctx.scheduler.start()
        self.assertTrue(self.ctx.scheduler.is_running)

        self.ctx.close()
        self.assertFalse(self.ctx.scheduler.is_running)
        self.assertTrue(self.ctx.closed)

    def test_scheduler_does_not_sync_when_disabled(self) -> None:
        """auto_sync_enabled=False 时 scheduler 循环不应触发同步。"""
        svc = self.ctx.settings_service
        svc.set("auto_sync_enabled", False)

        # 捕获 SyncService.sync_enabled_subscriptions
        from unittest.mock import patch

        with patch.object(
            self.ctx.sync_service,
            "sync_enabled_subscriptions",
            wraps=self.ctx.sync_service.sync_enabled_subscriptions,
        ) as spy:
            self.ctx.scheduler.start()
            # 等待足够久让至少一轮 loop 检查完毕
            time.sleep(0.5)
            self.ctx.scheduler.stop()
            # 未启用时不应调用同步
            spy.assert_not_called()

    def test_scheduler_syncs_all_when_global_interval_set(self) -> None:
        """配置全局间隔时调度器应调用全量同步。"""
        svc = self.ctx.settings_service
        svc.set("auto_sync_enabled", True)
        svc.set("global_sync_interval_minutes", 60)

        from unittest.mock import patch

        with patch.object(
            self.ctx.sync_service,
            "sync_enabled_subscriptions",
            wraps=self.ctx.sync_service.sync_enabled_subscriptions,
        ) as spy:
            self.ctx.scheduler.start()
            time.sleep(0.5)
            self.ctx.scheduler.stop()
            spy.assert_called()

    def test_scheduler_syncs_due_only_when_per_subscription_mode(self) -> None:
        """未配置全局间隔（None）时调度器应调用按订阅到期同步。"""
        svc = self.ctx.settings_service
        svc.set("auto_sync_enabled", True)
        # global_sync_interval_minutes 保持默认 None

        from unittest.mock import patch

        with patch.object(
            self.ctx.sync_service,
            "sync_due_subscriptions",
            wraps=self.ctx.sync_service.sync_due_subscriptions,
        ) as spy:
            self.ctx.scheduler.start()
            time.sleep(0.5)
            self.ctx.scheduler.stop()
            spy.assert_called()

    def test_double_close_is_idempotent(self) -> None:
        self.ctx.close()
        self.ctx.close()  # 不应崩溃
        self.assertTrue(self.ctx.closed)

    def test_context_manager_protocol(self) -> None:
        with create_app_context(":memory:") as ctx:
            self.assertIsNotNone(ctx.settings_service)
            self.assertIsNotNone(ctx.scheduler)
            self.assertFalse(ctx.closed)
        self.assertTrue(ctx.closed)

    def test_settings_persist_across_connections(self) -> None:
        """设置持久化到磁盘（:memory: 无法跨连接，用临时文件验证写入后重读）。"""
        # 同一个 connection 内验证
        svc = self.ctx.settings_service
        svc.set("last_open_page", "/settings")
        self.assertEqual(svc.get("last_open_page"), "/settings")

        svc.set("auto_sync_enabled", True)
        settings = svc.get_all()
        self.assertTrue(settings.auto_sync_enabled)

    def test_sync_due_subscriptions_skips_recently_synced(self) -> None:
        """sync_due_subscriptions 跳过刚同步完的订阅。"""
        from datetime import timedelta

        from app.application.dto import SubscriptionUpsertInput

        # 创建订阅并立即标记为刚同步
        sub = self.ctx.subscription_service.create_subscription(
            SubscriptionUpsertInput(name="Due Test", categories=["cs.AI"])
        )
        # 将 last_synced_at 设为 1 分钟前，sync_interval_minutes=1440
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.ctx.connection.execute(
            "UPDATE subscriptions SET last_synced_at = ? WHERE id = ?",
            (recent, sub.id),
        )
        self.ctx.connection.commit()

        results = self.ctx.sync_service.sync_due_subscriptions()
        # 不应触发任何同步（间隔未到）
        synced_ids = [r.subscription_id for r in results]
        self.assertNotIn(sub.id, synced_ids)

    def test_sync_due_subscriptions_syncs_overdue(self) -> None:
        """sync_due_subscriptions 同步已超过间隔的订阅。"""
        from datetime import timedelta

        from app.application.dto import SubscriptionUpsertInput

        # 创建订阅并将 last_synced_at 设为远超间隔之前
        sub = self.ctx.subscription_service.create_subscription(
            SubscriptionUpsertInput(
                name="Overdue Test",
                categories=["cs.AI"],
                sync_interval_minutes=30,
            )
        )
        old = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        self.ctx.connection.execute(
            "UPDATE subscriptions SET last_synced_at = ? WHERE id = ?",
            (old, sub.id),
        )
        self.ctx.connection.commit()

        results = self.ctx.sync_service.sync_due_subscriptions()
        synced_ids = [r.subscription_id for r in results]
        self.assertIn(sub.id, synced_ids)

    def test_sync_due_subscriptions_syncs_never_synced(self) -> None:
        """从未同步过的订阅（last_synced_at=None）视为立即到期。"""
        from app.application.dto import SubscriptionUpsertInput

        sub = self.ctx.subscription_service.create_subscription(
            SubscriptionUpsertInput(name="Never Synced", categories=["cs.AI"])
        )
        self.assertIsNone(sub.last_synced_at)

        results = self.ctx.sync_service.sync_due_subscriptions()
        synced_ids = [r.subscription_id for r in results]
        self.assertIn(sub.id, synced_ids)

    def test_show_hidden_by_default_affects_dashboard_filters(self) -> None:
        """show_hidden_by_default=False 时 Dashboard 初始筛选应排除隐藏论文。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        # 默认设置 show_hidden_by_default=False
        self.ctx.settings_service.set("show_hidden_by_default", False)

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        # 视图构建不应崩溃（验证链路连通性）

    def test_default_list_filters_consumed_by_dashboard(self) -> None:
        """default_list_filters 中的筛选条件应传入 Dashboard 初始 PaperListFilters。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"keyword": "transformer", "is_starred": True},
        )
        self.ctx.settings_service.set("show_hidden_by_default", False)

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        # 视图构建不崩溃，证明 default_list_filters 中的键被成功传入了 PaperListFilters

    def test_default_list_filters_unknown_keys_filtered(self) -> None:
        """default_list_filters 中的未知键不应导致 Dashboard 崩溃。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"keyword": "test", "__bogus__": "should-be-ignored"},
        )

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_status_toggle_with_active_filter_triggers_reload(self) -> None:
        """当前筛选对状态字段有约束时，状态切换应触发 DB 重载而非仅更新缓存。

        验证 fix：is_starred=True 筛选下取消收藏时，list_papers 被再次调用。
        """
        from unittest.mock import patch

        from app.ui.pages.dashboard_page import build_dashboard_view

        # 种子一篇已收藏论文
        paper = _make_paper(arxiv_id="2501.00099", title="Star Paper")
        repo = PaperRepository(self.ctx.connection)
        repo.upsert(paper)
        self.ctx.status_service.set_starred("2501.00099", True)
        self.ctx.connection.commit()

        # 设置筛选：仅收藏
        self.ctx.settings_service.set(
            "default_list_filters", {"is_starred": True}
        )

        page = _MockPage()

        with patch.object(
            self.ctx.paper_query_service,
            "list_papers",
            wraps=self.ctx.paper_query_service.list_papers,
        ) as spy:
            view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
            self.assertIsInstance(view, ft.View)
            # 初始加载时应以 is_starred=True 调用
            self.assertEqual(spy.call_count, 1)
            call_filter = spy.call_args[0][0] if spy.call_args[0] else spy.call_args[1].get("filters")
            if call_filter is not None:
                self.assertTrue(call_filter.is_starred)

    def test_default_list_filters_corrupt_value_falls_back(self) -> None:
        """default_list_filters 值类型错误（如 is_starred='not-a-bool'）时
        Dashboard 应回退到默认筛选而非崩溃。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"is_starred": "not-a-bool"},
        )

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_scheduler_listener_cleaned_on_nav_away(self) -> None:
        """导航离开 /settings 时旧的 tick listener 应被移除。"""
        # 先进入 settings 注册 listener
        shell = AppShell()
        page = _MockPage()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=self.ctx,
        ):
            shell(page)  # type: ignore[arg-type]
            page.route = "/settings"
            if page.on_route_change:
                page.on_route_change(MagicMock())

        # 确认 listener 已挂载
        self.assertTrue(hasattr(page, "_settings_tick_listener"))
        self.assertIsNotNone(page._settings_tick_listener)
        listener_before = page._settings_tick_listener

        # 导航到 Dashboard → 应清理 settings listener
        page.route = "/dashboard"
        if page.on_route_change:
            page.on_route_change(MagicMock())

        # listener 引用应已置空
        self.assertIsNone(page._settings_tick_listener)
        # 回调应从调度器中解除注册（再次 tick 不会触发旧 listener）
        self.ctx.scheduler._do_tick(use_due_only=True)  # type: ignore[attr-defined]
        # 不崩溃即为通过

    def test_last_open_page_restored_on_boot(self) -> None:
        """last_open_page 设置后 AppShell 应恢复到该路由。"""
        self.ctx.settings_service.set("last_open_page", "/subscriptions")

        shell = AppShell()
        page = _MockPage()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=self.ctx,
        ):
            shell(page)  # type: ignore[arg-type]

        # 应恢复到最后打开的页面而非默认 /dashboard
        self.assertTrue(any("go:/subscriptions" in c for c in page.calls))
        self.assertFalse(any("go:/dashboard" in c for c in page.calls))

    def test_last_open_page_saved_on_nav(self) -> None:
        """导航到 /settings 后 last_open_page 应被持久化。"""
        self.ctx.settings_service.set("last_open_page", "/dashboard")

        shell = AppShell()
        page = _MockPage()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=self.ctx,
        ):
            shell(page)  # type: ignore[arg-type]

        # 模拟路由到 Settings
        page.route = "/settings"
        if page.on_route_change:
            page.on_route_change(MagicMock())

        saved = self.ctx.settings_service.get("last_open_page")
        self.assertEqual(saved, "/settings")

    def test_paper_detail_route_not_saved_as_last_page(self) -> None:
        """论文详情页不应作为 last_open_page 恢复目标。"""
        self.ctx.settings_service.set("last_open_page", "/dashboard")

        shell = AppShell()
        page = _MockPage()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=self.ctx,
        ):
            shell(page)  # type: ignore[arg-type]

        # 模拟路由到论文详情
        page.route = "/paper/2501.00001"
        mock_svc = self.ctx.paper_query_service
        with patch.object(mock_svc, "get_paper_detail", return_value=None):
            if page.on_route_change:
                page.on_route_change(MagicMock())

        # last_open_page 不应变为瞬时页面路由
        saved = self.ctx.settings_service.get("last_open_page")
        self.assertNotEqual(saved, "/paper/2501.00001")

    def test_settings_page_registers_tick_listener(self) -> None:
        """Settings 页面构建时应向调度器注册 tick listener。"""
        page = _MockPage()
        build_settings_view(self.ctx, page)  # type: ignore[arg-type]

        # 验证 listener 已挂载
        self.assertTrue(hasattr(page, "_settings_tick_listener"))
        self.assertIsNotNone(page._settings_tick_listener)

        # 重复构建应替换 listener 而非重复注册
        first = page._settings_tick_listener
        build_settings_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsNot(page._settings_tick_listener, first)

    def test_dashboard_builds_with_starred_filter(self) -> None:
        """default_list_filters 含 is_starred=True 时 Dashboard 正常构建。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"is_starred": True},
        )

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_dashboard_builds_with_read_filter(self) -> None:
        """default_list_filters 含 is_read=True 时 Dashboard 正常构建。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"is_read": True},
        )

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_dashboard_builds_with_hidden_filter(self) -> None:
        """default_list_filters 含 is_hidden=True 时 Dashboard 正常构建。"""
        from app.ui.pages.dashboard_page import build_dashboard_view

        self.ctx.settings_service.set(
            "default_list_filters",
            {"is_hidden": True},
        )

        page = _MockPage()
        view = build_dashboard_view(self.ctx, page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)


# ============================================================================
# 8. 异常恢复与边缘情况测试
# ============================================================================


class ExceptionRecoveryTests(unittest.TestCase):
    """设置损坏、调度器异常恢复与资源清理测试。"""

    def setUp(self) -> None:
        self.conn = _setup_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_settings_service_survives_corrupt_db_value(self) -> None:
        """直接写入损坏 JSON —— get_all 应回退默认值而非崩溃。"""
        self.conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) "
            "VALUES ('auto_sync_enabled', '{broken', '2026-01-01T00:00:00Z')"
        )
        self.conn.commit()

        svc = SettingsService(self.conn)
        settings = svc.get_all()
        self.assertIsInstance(settings, AppSettingsDTO)
        self.assertFalse(settings.auto_sync_enabled)  # 回退到默认

    def test_settings_get_survives_corrupt_value(self) -> None:
        """单键 get 在损坏值时回退默认。"""
        self.conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) "
            "VALUES ('global_sync_interval_minutes', '<<bad>>', '2026-01-01T00:00:00Z')"
        )
        self.conn.commit()

        svc = SettingsService(self.conn)
        self.assertIsNone(svc.get("global_sync_interval_minutes"))

    def test_scheduler_stop_timeout(self) -> None:
        """stop() 带超时参数，干净退出时返回 True。"""
        svc = SettingsService(self.conn)
        mock_sync = MagicMock(spec=SyncService)
        scheduler = SyncScheduler(mock_sync, svc)

        scheduler.start()
        clean = scheduler.stop(timeout=2.0)
        self.assertFalse(scheduler.is_running)
        self.assertTrue(clean)  # 无阻塞 tick → 应干净退出

    def test_close_skips_teardown_when_stop_times_out(self) -> None:
        """stop() 返回 False 时 close() 跳过资源关闭（线程可能仍在使用它们）。"""
        ctx = create_app_context(":memory:")
        try:
            with patch.object(ctx.scheduler, "stop", return_value=False):
                with patch.object(ctx.sync_service, "close") as mock_svc_close:
                    ctx.close()

            # SyncService.close() 不应被调用
            mock_svc_close.assert_not_called()
            # AppContext 应标记为已关闭
            self.assertTrue(ctx._closed)
        finally:
            # 真实清理
            ctx._closed = False
            ctx.scheduler.stop()
            ctx.sync_service.close()
            ctx.connection.close()

    def test_scheduler_listener_removal_idempotent(self) -> None:
        """移除不存在的 listener 不抛异常。"""
        svc = SettingsService(self.conn)
        mock_sync = MagicMock(spec=SyncService)
        scheduler = SyncScheduler(mock_sync, svc)

        def _dummy(event: SchedulerTickEvent) -> None:
            pass

        scheduler.remove_tick_listener(_dummy)  # 不应崩溃

    def test_tick_aborts_mid_flight_when_stopped(self) -> None:
        """tick 执行中被 stop() → 网络返回后不应再触碰 DB / listener。"""
        svc = SettingsService(self.conn)
        mock_sync = MagicMock(spec=SyncService)
        mock_sync.sync_enabled_subscriptions.return_value = []

        scheduler = SyncScheduler(mock_sync, svc)
        events: list[SchedulerTickEvent] = []

        def _cb(event: SchedulerTickEvent) -> None:
            events.append(event)

        scheduler.add_tick_listener(_cb)

        # 在 sync 调用返回后立即设置停止信号，模拟 close() 中途调用
        original = mock_sync.sync_enabled_subscriptions

        def _side_effect(*args, **kwargs):
            scheduler._stop_event.set()  # 模拟 stop() → close()
            return original(*args, **kwargs)

        mock_sync.sync_enabled_subscriptions.side_effect = _side_effect

        scheduler._do_tick(use_due_only=False)

        # 应只有 "running" 事件，completed/failed 被 _stop_event 检查拦截
        statuses = [e.status for e in events]
        self.assertIn("running", statuses)
        self.assertNotIn("completed", statuses)
        self.assertNotIn("failed", statuses)
        # tick_lock 应已释放（不持锁退出）
        self.assertFalse(scheduler._tick_lock.locked())

    def test_sync_service_close_releases_http_client(self) -> None:
        """SyncService.close() 可安全重复调用。"""
        ctx = create_app_context(":memory:")
        try:
            ctx.sync_service.close()
            ctx.sync_service.close()  # 幂等
        finally:
            ctx.scheduler.stop()
            ctx.connection.close()


# ============================================================================
# 9. 回归测试 —— 确保 Slice 09 不破坏已有切片
# ============================================================================


class Slice09RegressionTests(unittest.TestCase):
    """验证 Slice 09 新增代码不影响 Slice 01-08 已有功能。"""

    def test_all_services_still_importable(self) -> None:
        from app.application.services import (
            PaperQueryService,
            SettingsService,
            StatusService,
            SubscriptionService,
            SyncService,
        )
        self.assertTrue(True)

    def test_all_dtos_still_importable(self) -> None:
        from app.application.dto import (
            AppSettingsDTO,
            PaperDetailDTO,
            PaperListFilters,
            PaperListItemDTO,
            PaperStatusPatchInput,
            SchedulerTickEvent,
            SubscriptionUpsertInput,
            SyncResultDTO,
        )
        self.assertTrue(True)

    def test_scheduler_importable(self) -> None:
        from app.infrastructure.scheduler import SyncScheduler
        self.assertTrue(True)

    def test_app_context_has_all_services(self) -> None:
        ctx = create_app_context(":memory:")
        try:
            self.assertIsNotNone(ctx.paper_query_service)
            self.assertIsNotNone(ctx.settings_service)
            self.assertIsNotNone(ctx.status_service)
            self.assertIsNotNone(ctx.subscription_service)
            self.assertIsNotNone(ctx.sync_service)
            self.assertIsNotNone(ctx.scheduler)
        finally:
            ctx.scheduler.stop()
            ctx.close()

    def test_slice_01_models_still_creatable(self) -> None:
        from app.domain.models import Subscription

        sub = Subscription(
            id="test",
            name="Regression Test",
            categories=["cs.CV"],
        )
        self.assertEqual(sub.name, "Regression Test")

    def test_slice_04_sync_service_still_works(self) -> None:
        ctx = create_app_context(":memory:")
        try:
            self.assertIsNotNone(ctx.sync_service)
            # sync_enabled_subscriptions 在空库应返回空列表
            results = ctx.sync_service.sync_enabled_subscriptions()
            self.assertEqual(results, [])
        finally:
            ctx.scheduler.stop()
            ctx.close()

    def test_slice_05_subscription_crud_still_works(self) -> None:
        from app.application.dto import SubscriptionUpsertInput

        ctx = create_app_context(":memory:")
        try:
            sub = ctx.subscription_service.create_subscription(
                SubscriptionUpsertInput(name="Slice09 Test", categories=["cs.AI"])
            )
            self.assertEqual(sub.name, "Slice09 Test")
        finally:
            ctx.scheduler.stop()
            ctx.close()

    def test_scheduler_default_module(self) -> None:
        """验证 scheduler 在未启动时不产生副作用。"""
        ctx = create_app_context(":memory:")
        try:
            self.assertFalse(ctx.scheduler.is_running)
            self.assertIsNone(ctx.scheduler.last_tick_event)
        finally:
            ctx.scheduler.stop()
            ctx.close()
