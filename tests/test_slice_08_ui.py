"""Slice 08: Flet MVP UI — 非 GUI 构造与回调测试。

本测试文件覆盖 UI 组件/页面的构造期行为、回调逻辑与 AppContext 集成，
不会启动真实的 Flet 窗口。

测试分类：
- 纯函数测试（_parse_comma_list 等）
- 组件构造测试（build_loading_view / build_status_bar / build_filter_panel）
- 筛选回调逻辑测试（checkbox tri-state → PaperListFilters 映射）
- 订阅表单 DTO 映射测试
- 页面视图构造测试（含 mock page / ctx）
- AppShell 路由与导航测试
- 集成测试（真实 in-memory DB，完整 UI→服务调用链路）
- 边缘情况测试（空态 / 错误态 / 无数据）
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import flet as ft

from app.application.dto import (
    AppSettingsDTO,
    PaperDetailDTO,
    PaperListFilters,
    PaperListItemDTO,
    PaperStatusPatchInput,
    SubscriptionUpsertInput,
    SyncResultDTO,
)
from app.application.services import (
    PaperQueryService,
    SettingsService,
    StatusService,
    SubscriptionService,
    SyncService,
)
from app.application.services.exceptions import (
    DuplicateSubscriptionNameError,
    InvalidPaperStatusError,
    InvalidSubscriptionRuleError,
    PaperNotFoundError,
    SubscriptionNotFoundError,
)
from app.domain.enums import SyncRunStatus
from app.domain.models import Paper, PaperStatus, Subscription
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories import PaperRepository, SubscriptionRepository
from app.infrastructure.scheduler import SyncScheduler
from app.main import AppContext, create_app_context
from app.ui.app_shell import AppShell
from app.ui.components.filter_panel import build_filter_panel
from app.ui.components.loading_overlay import build_loading_view
from app.ui.components.notification_bar import show_notification
from app.ui.components.paper_status_bar import build_status_bar
from app.ui.components.subscription_form import (
    _parse_comma_list,
    open_subscription_dialog,
)
from app.ui.pages.dashboard_page import build_dashboard_view
from app.ui.pages.paper_detail_page import build_paper_detail_view
from app.ui.pages.subscriptions_page import build_subscriptions_view


# ============================================================================
# Mock Fixtures
# ============================================================================


class _MockPage:
    """Flet Page 的轻量级存根，覆盖 UI 层常用的 page API。

    所有写操作（update / go / open / close / launch_url）
    均记录到内部列表，供测试断言。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._route = "/dashboard"
        self._views: list[ft.View] = []
        self._snackbars: list[ft.SnackBar] = []
        self._dialogs: list[ft.AlertDialog] = []
        self._launched_urls: list[str] = []

        # AppShell 写入的属性
        self.title = ""
        self.theme_mode: ft.ThemeMode | None = None
        self.theme: ft.Theme | None = None
        self.navigation_bar: ft.NavigationBar | None = None
        self.window = _MockWindow()

        # 回调槽
        self.on_route_change = None
        self._route_change_handler = None

    def update(self) -> None:
        self.calls.append("update")

    def go(self, route: str) -> None:
        self.calls.append(f"go:{route}")
        self._route = route

    def open(self, control: ft.Control) -> None:
        self.calls.append("open")
        if isinstance(control, ft.SnackBar):
            self._snackbars.append(control)
        elif isinstance(control, ft.AlertDialog):
            self._dialogs.append(control)

    def close(self, control: ft.Control | None = None) -> None:
        self.calls.append("close")

    def launch_url(self, url: str) -> None:
        self.calls.append(f"launch_url:{url}")
        self._launched_urls.append(url)

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
    """Page.window 存根"""

    width = 1200
    height = 800
    min_width = 800
    min_height = 600


def _make_paper(**overrides: object) -> Paper:
    defaults: dict = {
        "arxiv_id": "2501.00001",
        "version": 1,
        "title": "A Test Paper About Transformers",
        "abstract": "An abstract for testing purposes.",
        "authors": ["Alice Smith", "Bob Jones"],
        "primary_category": "cs.CV",
        "categories": ["cs.CV", "cs.LG"],
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "abs_url": "https://arxiv.org/abs/2501.00001",
        "pdf_url": "https://arxiv.org/pdf/2501.00001",
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return Paper(**defaults)  # type: ignore[arg-type]


def _make_sub(**overrides: object) -> Subscription:
    defaults: dict = {
        "id": "sub-001",
        "name": "CV Daily",
        "enabled": True,
        "categories": ["cs.CV"],
        "include_keywords": ["transformer"],
        "exclude_keywords": [],
        "authors": [],
        "sync_interval_minutes": 1440,
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return Subscription(**defaults)  # type: ignore[arg-type]


def _make_paper_list_item(**overrides: object) -> PaperListItemDTO:
    defaults: dict = {
        "arxiv_id": "2501.00001",
        "title": "A Test Paper",
        "authors_preview": "Alice Smith, Bob Jones",
        "primary_category": "cs.CV",
        "categories": ["cs.CV", "cs.LG"],
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "is_starred": False,
        "is_read": False,
        "is_hidden": False,
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return PaperListItemDTO(**defaults)  # type: ignore[arg-type]


def _make_paper_detail(**overrides: object) -> PaperDetailDTO:
    defaults: dict = {
        "arxiv_id": "2501.00001",
        "latest_version": 1,
        "title": "A Test Paper About Transformers",
        "abstract": "An abstract for testing.",
        "authors": ["Alice Smith", "Bob Jones"],
        "primary_category": "cs.CV",
        "categories": ["cs.CV", "cs.LG"],
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "abs_url": "https://arxiv.org/abs/2501.00001",
        "pdf_url": "https://arxiv.org/pdf/2501.00001",
        "is_starred": False,
        "is_read": False,
        "is_hidden": False,
        "rating": None,
        "note": None,
        "tags": [],
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return PaperDetailDTO(**defaults)  # type: ignore[arg-type]


def _setup_db() -> sqlite3.Connection:
    """创建测试用 in-memory SQLite 连接"""
    return get_connection(":memory:")


def _mock_ctx(conn: sqlite3.Connection) -> AppContext:
    """用 mock service 构建 AppContext（用于构造期测试，不涉及真实数据）"""
    mock_sub_svc = MagicMock(spec=SubscriptionService)
    mock_sub_svc.list_subscriptions.return_value = []
    mock_query_svc = MagicMock(spec=PaperQueryService)
    mock_query_svc.list_papers.return_value = []
    mock_query_svc.get_paper_detail.return_value = None
    mock_status_svc = MagicMock(spec=StatusService)
    mock_sync_svc = MagicMock(spec=SyncService)
    mock_settings_svc = MagicMock(spec=SettingsService)
    mock_settings_svc.get_all.return_value = AppSettingsDTO()
    mock_scheduler = MagicMock(spec=SyncScheduler)
    mock_scheduler.is_running = False
    mock_scheduler.last_tick_event = None
    return AppContext(
        connection=conn,
        paper_query_service=mock_query_svc,
        settings_service=mock_settings_svc,
        status_service=mock_status_svc,
        subscription_service=mock_sub_svc,
        sync_service=mock_sync_svc,
        scheduler=mock_scheduler,
    )


# ============================================================================
# 1. 纯函数测试
# ============================================================================


class CommaListParsingTests(unittest.TestCase):
    """_parse_comma_list() 纯函数测试"""

    def test_single_item(self) -> None:
        self.assertEqual(_parse_comma_list("cs.CV"), ["cs.CV"])

    def test_multiple_items(self) -> None:
        self.assertEqual(
            _parse_comma_list("cs.CV, cs.AI, stat.ML"),
            ["cs.CV", "cs.AI", "stat.ML"],
        )

    def test_empty_string(self) -> None:
        self.assertEqual(_parse_comma_list(""), [])

    def test_whitespace_only(self) -> None:
        self.assertEqual(_parse_comma_list("   "), [])

    def test_trims_items(self) -> None:
        self.assertEqual(
            _parse_comma_list("  hello , world  "),
            ["hello", "world"],
        )

    def test_dedup_case_insensitive(self) -> None:
        """去重规则：区分小写后的去重（保持首次出现的大小写）"""
        result = _parse_comma_list("Hello, hello, HELLO, World")
        self.assertIn("Hello", result)
        self.assertIn("World", result)
        self.assertEqual(len(result), 2)

    def test_skips_empty_segments(self) -> None:
        self.assertEqual(
            _parse_comma_list("a,,b, ,c,"),
            ["a", "b", "c"],
        )

    def test_preserves_order(self) -> None:
        result = _parse_comma_list("z, a, m, b")
        self.assertEqual(result, ["z", "a", "m", "b"])


# ============================================================================
# 2. 组件构造测试
# ============================================================================


class ComponentConstructionTests(unittest.TestCase):
    """各组件的无异常构造测试"""

    def test_loading_view_returns_column_with_ring(self) -> None:
        col = build_loading_view("Please wait...")
        self.assertIsInstance(col, ft.Column)
        self.assertEqual(len(col.controls), 2)
        self.assertIsInstance(col.controls[0], ft.ProgressRing)
        self.assertIsInstance(col.controls[1], ft.Text)
        self.assertEqual(col.controls[1].value, "Please wait...")

    def test_loading_view_default_message(self) -> None:
        col = build_loading_view()
        self.assertIn("Loading", col.controls[1].value)

    def test_status_bar_all_default(self) -> None:
        row = build_status_bar()
        self.assertIsInstance(row, ft.Row)
        # 默认 3 个 IconButton（无 label 时）
        self.assertEqual(len(row.controls), 3)
        for btn in row.controls:
            self.assertIsInstance(btn, ft.IconButton)

    def test_status_bar_starred_filled(self) -> None:
        row = build_status_bar(is_starred=True)
        star_btn = row.controls[0]
        self.assertEqual(star_btn.icon, ft.Icons.STAR)
        self.assertEqual(star_btn.icon_color, ft.Colors.AMBER_700)

    def test_status_bar_read_filled(self) -> None:
        row = build_status_bar(is_read=True)
        read_btn = row.controls[1]
        self.assertEqual(read_btn.icon, ft.Icons.CHECK_CIRCLE)
        self.assertEqual(read_btn.icon_color, ft.Colors.GREEN_700)

    def test_status_bar_hidden_filled(self) -> None:
        row = build_status_bar(is_hidden=True)
        hidden_btn = row.controls[2]
        self.assertEqual(hidden_btn.icon, ft.Icons.VISIBILITY_OFF)
        self.assertEqual(hidden_btn.icon_color, ft.Colors.RED_700)

    def test_status_bar_with_labels(self) -> None:
        row = build_status_bar(show_labels=True)
        self.assertIsInstance(row, ft.Row)
        # 含文字标签时，控件是嵌套 Row
        self.assertGreater(len(row.controls), 0)

    def test_status_bar_callback_fires(self) -> None:
        calls: list[tuple[str, bool]] = []

        def _on_toggle(field: str, value: bool) -> None:
            calls.append((field, value))

        row = build_status_bar(on_toggle=_on_toggle)
        # 模拟点击 star 按钮
        star_btn = row.controls[0]
        # 当前 is_starred=False，点击后应回调 ("is_starred", True)
        star_btn.on_click(None)  # type: ignore[arg-type]
        self.assertEqual(calls, [("is_starred", True)])

    def test_status_bar_callback_toggle_starred_to_unstarred(self) -> None:
        calls: list[tuple[str, bool]] = []

        def _on_toggle(field: str, value: bool) -> None:
            calls.append((field, value))

        row = build_status_bar(is_starred=True, on_toggle=_on_toggle)
        star_btn = row.controls[0]
        star_btn.on_click(None)  # type: ignore[arg-type]
        self.assertEqual(calls, [("is_starred", False)])

    def test_filter_panel_constructs(self) -> None:
        """FilterPanel 构造不抛异常"""
        panel = build_filter_panel(PaperListFilters(), lambda f: None)
        self.assertIsInstance(panel, ft.Container)

    def test_filter_panel_with_existing_filters(self) -> None:
        """带已有筛选条件构造"""
        filters = PaperListFilters(keyword="transformer", is_starred=True)
        panel = build_filter_panel(filters, lambda f: None)
        self.assertIsInstance(panel, ft.Container)


# ============================================================================
# 3. 筛选回调逻辑测试
# ============================================================================


class FilterPanelCallbackTests(unittest.TestCase):
    """FilterPanel _on_apply / _on_clear 的逻辑正确性测试"""

    def setUp(self) -> None:
        self._captured: list[PaperListFilters] = []

    def _on_apply(self, f: PaperListFilters) -> None:
        self._captured.append(f)

    def test_default_apply_sends_empty_filters(self) -> None:
        """默认筛选面板 Apply → 所有字段为 None 的 PaperListFilters"""
        panel = build_filter_panel(PaperListFilters(), self._on_apply)
        # 从 panel 中找到 Apply 按钮并触发其 on_click
        self._trigger_apply(panel)
        self.assertEqual(len(self._captured), 1)
        result = self._captured[0]
        self.assertIsNone(result.keyword)
        self.assertIsNone(result.category)
        self.assertIsNone(result.is_starred)
        self.assertIsNone(result.is_read)
        self.assertIsNone(result.is_hidden)

    def test_clear_resets_and_sends_empty(self) -> None:
        """Clear 按钮 → 重置所有控件并发送空白 PaperListFilters"""
        filters = PaperListFilters(keyword="test", is_starred=True)
        panel = build_filter_panel(filters, self._on_apply)
        self._trigger_clear(panel)
        self.assertEqual(len(self._captured), 1)
        result = self._captured[0]
        self.assertIsNone(result.keyword)
        self.assertIsNone(result.category)
        self.assertIsNone(result.is_starred)
        self.assertIsNone(result.is_read)
        self.assertIsNone(result.is_hidden)

    def test_tristate_checkbox_init_none_stays_none(self) -> None:
        """tri-state Checkbox 初始化 None → 构造不抛异常"""
        filters = PaperListFilters()  # 全 None
        panel = build_filter_panel(filters, self._on_apply)
        self.assertIsInstance(panel, ft.Container)

    # -- helpers --

    @staticmethod
    def _walk_buttons(container: ft.Container) -> list:
        """递归遍历控件树找到所有 Button"""
        buttons: list = []

        def _walk(c: ft.Control) -> None:
            if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)):
                buttons.append(c)
            if hasattr(c, "controls"):
                for child in c.controls:  # type: ignore[union-attr]
                    _walk(child)
            if hasattr(c, "content") and c.content is not None:
                _walk(getattr(c, "content"))

        _walk(container)
        return buttons

    def _trigger_apply(self, panel: ft.Container) -> None:
        for btn in self._walk_buttons(panel):
            if isinstance(btn, ft.FilledButton):
                btn.on_click(None)  # type: ignore[arg-type]
                return

    def _trigger_clear(self, panel: ft.Container) -> None:
        for btn in self._walk_buttons(panel):
            if isinstance(btn, ft.OutlinedButton):
                btn.on_click(None)  # type: ignore[arg-type]
                return


# ============================================================================
# 4. 订阅表单 DTO 映射测试
# ============================================================================


class SubscriptionFormInputMappingTests(unittest.TestCase):
    """订阅表单 → SubscriptionUpsertInput DTO 映射正确性测试"""

    def test_minimal_valid_input(self) -> None:
        inp = SubscriptionUpsertInput(
            name="Minimal Sub",
            categories=["cs.AI"],
        )
        self.assertEqual(inp.name, "Minimal Sub")
        self.assertTrue(inp.enabled)
        self.assertEqual(inp.categories, ["cs.AI"])
        self.assertEqual(inp.include_keywords, [])
        self.assertEqual(inp.sync_interval_minutes, 1440)

    def test_full_input(self) -> None:
        inp = SubscriptionUpsertInput(
            name="Full Sub",
            enabled=False,
            categories=["cs.CV", "cs.LG"],
            include_keywords=["transformer", "attention"],
            exclude_keywords=["medical"],
            authors=["Alice"],
            query_text="cat:cs.CV",
            sync_interval_minutes=720,
        )
        self.assertEqual(inp.name, "Full Sub")
        self.assertFalse(inp.enabled)
        self.assertEqual(inp.sync_interval_minutes, 720)
        self.assertEqual(inp.include_keywords, ["transformer", "attention"])

    def test_name_required_validation(self) -> None:
        """Pydantic 校验：name 不能为空"""
        with self.assertRaises(ValueError):
            SubscriptionUpsertInput(name="", categories=["cs.AI"])

    def test_at_least_one_filter_required(self) -> None:
        """Pydantic 校验：至少需要一个筛选条件"""
        with self.assertRaises(ValueError):
            SubscriptionUpsertInput(name="Test", categories=[], include_keywords=[], authors=[])

    def test_sync_interval_must_be_positive(self) -> None:
        """Pydantic 校验：sync_interval_minutes 必须 > 0"""
        with self.assertRaises(ValueError):
            SubscriptionUpsertInput(
                name="Test", categories=["cs.AI"], sync_interval_minutes=0
            )
        with self.assertRaises(ValueError):
            SubscriptionUpsertInput(
                name="Test", categories=["cs.AI"], sync_interval_minutes=-1
            )

    def test_comma_list_parsed_to_dto_fields(self) -> None:
        """模拟表单逗号分隔输入 → DTO 字段的映射"""
        raw_categories = "cs.CV, cs.AI, stat.ML"
        raw_keywords = "transformer, attention"
        parsed_cats = _parse_comma_list(raw_categories)
        parsed_kws = _parse_comma_list(raw_keywords)
        inp = SubscriptionUpsertInput(
            name="From Form",
            categories=parsed_cats,
            include_keywords=parsed_kws,
        )
        self.assertEqual(inp.categories, ["cs.CV", "cs.AI", "stat.ML"])
        self.assertEqual(inp.include_keywords, ["transformer", "attention"])


# ============================================================================
# 5. PaperStatusPatchInput 映射测试
# ============================================================================


class PaperStatusPatchInputTests(unittest.TestCase):
    """状态编辑表单 → PaperStatusPatchInput DTO 映射测试"""

    def test_default_all_none(self) -> None:
        p = PaperStatusPatchInput()
        self.assertIsNone(p.is_starred)
        self.assertIsNone(p.is_read)
        self.assertIsNone(p.is_hidden)
        self.assertIsNone(p.rating)
        self.assertIsNone(p.note)
        self.assertIsNone(p.tags)

    def test_partial_patch(self) -> None:
        p = PaperStatusPatchInput(is_starred=True, rating=4)
        self.assertTrue(p.is_starred)
        self.assertIsNone(p.is_read)
        self.assertEqual(p.rating, 4)

    def test_hidden_starred_conflict(self) -> None:
        """is_hidden=True 与 is_starred=True 不能同时存在"""
        with self.assertRaises(ValueError):
            PaperStatusPatchInput(is_hidden=True, is_starred=True)

    def test_rating_range(self) -> None:
        PaperStatusPatchInput(rating=1)
        PaperStatusPatchInput(rating=5)
        with self.assertRaises(ValueError):
            PaperStatusPatchInput(rating=0)
        with self.assertRaises(ValueError):
            PaperStatusPatchInput(rating=6)


# ============================================================================
# 6. PaperListFilters 筛选参数对象测试
# ============================================================================


class PaperListFiltersTests(unittest.TestCase):
    """PaperListFilters DTO 构造与校验测试"""

    def test_default_all_none(self) -> None:
        f = PaperListFilters()
        self.assertIsNone(f.keyword)
        self.assertIsNone(f.category)
        self.assertIsNone(f.is_starred)
        self.assertIsNone(f.is_read)
        self.assertIsNone(f.is_hidden)

    def test_keyword_normalized(self) -> None:
        f = PaperListFilters(keyword="  Transformer  ")
        self.assertEqual(f.keyword, "Transformer")

    def test_empty_keyword_becomes_none(self) -> None:
        f = PaperListFilters(keyword="")
        self.assertIsNone(f.keyword)

    def test_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            PaperListFilters(limit=0)
        with self.assertRaises(ValueError):
            PaperListFilters(limit=-5)

    def test_date_range_validation(self) -> None:
        dt1 = datetime(2026, 1, 10, tzinfo=timezone.utc)
        dt2 = datetime(2026, 1, 5, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            PaperListFilters(published_from=dt1, published_to=dt2)


# ============================================================================
# 7. 页面视图构造测试（mock page + ctx）
# ============================================================================


class ViewConstructionTests(unittest.TestCase):
    """各页面 View 对象的无异常构造测试"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.ctx = _mock_ctx(self.conn)
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.conn.close()

    def test_dashboard_view_constructs(self) -> None:
        view = build_dashboard_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        self.assertEqual(view.route, "/dashboard")
        self.assertIsNotNone(view.appbar)
        self.assertGreater(len(view.controls), 0)

    def test_dashboard_view_shows_empty_state(self) -> None:
        """无论文时展示空态而非崩溃"""
        mock_svc = self.ctx.paper_query_service
        mock_svc.list_papers.return_value = []  # type: ignore[union-attr]
        view = build_dashboard_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_dashboard_view_with_papers(self) -> None:
        """有论文时正确构建卡片列表"""
        mock_svc = self.ctx.paper_query_service
        mock_svc.list_papers.return_value = [  # type: ignore[union-attr]
            _make_paper_list_item(arxiv_id="2501.00001", title="First Paper"),
            _make_paper_list_item(arxiv_id="2501.00002", title="Second Paper"),
        ]
        view = build_dashboard_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_subscriptions_view_constructs(self) -> None:
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        self.assertEqual(view.route, "/subscriptions")
        self.assertIsNotNone(view.appbar)

    def test_subscriptions_view_empty_state(self) -> None:
        mock_svc = self.ctx.subscription_service
        mock_svc.list_subscriptions.return_value = []  # type: ignore[union-attr]
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_subscriptions_view_with_subs(self) -> None:
        mock_svc = self.ctx.subscription_service
        mock_svc.list_subscriptions.return_value = [  # type: ignore[union-attr]
            _make_sub(id="s1", name="CV Daily", last_synced_at="2026-06-01T00:00:00Z"),
            _make_sub(id="s2", name="ML Weekly"),
        ]
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_paper_detail_view_constructs(self) -> None:
        mock_svc = self.ctx.paper_query_service
        mock_svc.get_paper_detail.return_value = _make_paper_detail()  # type: ignore[union-attr]
        view = build_paper_detail_view(self.ctx, self.page, "2501.00001")  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)
        self.assertTrue(view.route.endswith("2501.00001"))

    def test_paper_detail_view_not_found(self) -> None:
        mock_svc = self.ctx.paper_query_service
        mock_svc.get_paper_detail.return_value = None  # type: ignore[union-attr]
        view = build_paper_detail_view(self.ctx, self.page, "nonexistent")  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)


# ============================================================================
# 8. AppShell 测试
# ============================================================================


class AppShellTests(unittest.TestCase):
    """AppShell 路由分发、导航栏回调与页面配置测试"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.shell = AppShell()
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.conn.close()

    def _boot(self) -> None:
        """模拟 Flet 启动回调"""
        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=_mock_ctx(self.conn),
        ):
            self.shell(self.page)  # type: ignore[arg-type]

    def test_call_configures_page_title(self) -> None:
        self._boot()
        self.assertEqual(self.page.title, "Paper Research")

    def test_call_configures_window_size(self) -> None:
        self._boot()
        self.assertEqual(self.page.window.width, 1200)
        self.assertEqual(self.page.window.height, 800)
        self.assertEqual(self.page.window.min_width, 800)
        self.assertEqual(self.page.window.min_height, 600)

    def test_call_sets_theme(self) -> None:
        self._boot()
        self.assertEqual(self.page.theme_mode, ft.ThemeMode.LIGHT)
        self.assertIsNotNone(self.page.theme)

    def test_call_installs_navigation_bar(self) -> None:
        self._boot()
        self.assertIsInstance(self.page.navigation_bar, ft.NavigationBar)
        self.assertEqual(len(self.page.navigation_bar.destinations), 3)
        self.assertEqual(
            self.page.navigation_bar.destinations[0].label, "Dashboard"
        )
        self.assertEqual(
            self.page.navigation_bar.destinations[1].label, "Subscriptions"
        )

    def test_call_registers_route_handler(self) -> None:
        self._boot()
        self.assertIsNotNone(self.page.on_route_change)

    def test_initial_route_is_dashboard(self) -> None:
        self._boot()
        self.assertIn("go:/dashboard", self.page.calls)

    def test_route_dashboard_builds_view(self) -> None:
        self._boot()
        self.page.route = "/dashboard"
        if self.page.on_route_change:
            self.page.on_route_change(MagicMock())  # type: ignore[call-arg]
        self.assertGreater(len(self.page.views), 0)

    def test_route_subscriptions_builds_view(self) -> None:
        self._boot()
        self.page.route = "/subscriptions"
        if self.page.on_route_change:
            self.page.on_route_change(MagicMock())  # type: ignore[call-arg]
        self.assertGreater(len(self.page.views), 0)

    def test_route_paper_detail_builds_view(self) -> None:
        self._boot()
        mock_svc = self.shell.ctx.paper_query_service
        mock_svc.get_paper_detail.return_value = _make_paper_detail()  # type: ignore[union-attr]
        self.page.route = "/paper/2501.00001"
        if self.page.on_route_change:
            self.page.on_route_change(MagicMock())  # type: ignore[call-arg]
        self.assertGreater(len(self.page.views), 0)

    def test_unknown_route_redirects_to_dashboard(self) -> None:
        self._boot()
        self.page.route = "/nonexistent"
        if self.page.on_route_change:
            self.page.on_route_change(MagicMock())  # type: ignore[call-arg]
        self.assertTrue(any("go:/dashboard" in c for c in self.page.calls))

    def test_nav_bar_selects_dashboard(self) -> None:
        self._boot()
        # 模拟 NavigationBar 选中 dashboard（index 0）
        if self.page.navigation_bar.on_change:
            nav_mock = MagicMock()
            nav_mock.control.selected_index = 0
            self.page.navigation_bar.on_change(nav_mock)
        self.assertTrue(any("go:/dashboard" in c for c in self.page.calls))

    def test_nav_bar_selects_subscriptions(self) -> None:
        self._boot()
        if self.page.navigation_bar.on_change:
            nav_mock = MagicMock()
            nav_mock.control.selected_index = 1
            self.page.navigation_bar.on_change(nav_mock)
        self.assertTrue(any("go:/subscriptions" in c for c in self.page.calls))


# ============================================================================
# 9. 集成测试（真实 in-memory DB，完整服务链路）
# ============================================================================


class IntegrationTests(unittest.TestCase):
    """用真实 in-memory DB 构造 AppContext，验证 UI→服务调用链路"""

    def setUp(self) -> None:
        self.ctx = create_app_context(":memory:")
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.ctx.close()

    def test_full_subscription_crud_cycle(self) -> None:
        """完整订阅 CRUD 链路"""
        svc = self.ctx.subscription_service

        # 创建
        inp = SubscriptionUpsertInput(name="Integration Test", categories=["cs.CV"])
        sub = svc.create_subscription(inp)
        self.assertEqual(sub.name, "Integration Test")
        self.assertTrue(sub.enabled)
        self.assertIsNone(sub.last_synced_at)

        # 列表
        subs = svc.list_subscriptions()
        self.assertEqual(len(subs), 1)

        # 编辑
        inp2 = SubscriptionUpsertInput(
            name="Updated Name", categories=["cs.CV", "cs.LG"]
        )
        updated = svc.update_subscription(sub.id, inp2)
        self.assertEqual(updated.name, "Updated Name")

        # 停用
        toggled = svc.set_subscription_enabled(sub.id, False)
        self.assertFalse(toggled.enabled)

        # 删除
        svc.delete_subscription(sub.id)
        self.assertEqual(len(svc.list_subscriptions()), 0)

    def test_duplicate_name_is_rejected(self) -> None:
        svc = self.ctx.subscription_service
        svc.create_subscription(
            SubscriptionUpsertInput(name="Unique", categories=["cs.CV"])
        )
        with self.assertRaises(DuplicateSubscriptionNameError):
            svc.create_subscription(
                SubscriptionUpsertInput(name="Unique", categories=["cs.AI"])
            )

    def test_status_toggle_pipeline(self) -> None:
        """完整状态切换链路（需要先有论文）"""
        # 种子论文
        paper = _make_paper(arxiv_id="2501.00042")
        repo = PaperRepository(self.ctx.connection)
        repo.upsert(paper)
        self.ctx.connection.commit()

        # 收藏
        status = self.ctx.status_service.set_starred("2501.00042", True)
        self.assertTrue(status.is_starred)

        # 已读
        status = self.ctx.status_service.set_read("2501.00042", True)
        self.assertTrue(status.is_read)

        # 评分
        status = self.ctx.status_service.update_rating("2501.00042", 4)
        self.assertEqual(status.rating, 4)

        # 笔记
        status = self.ctx.status_service.update_note("2501.00042", "Interesting")
        self.assertEqual(status.note, "Interesting")

        # 标签
        status = self.ctx.status_service.update_tags("2501.00042", ["transformer", "survey"])
        self.assertEqual(status.tags, ["transformer", "survey"])

        # 取消收藏
        status = self.ctx.status_service.set_starred("2501.00042", False)
        self.assertFalse(status.is_starred)

    def test_paper_query_after_seeding(self) -> None:
        """种子论文后可查询"""
        paper = _make_paper(arxiv_id="2501.00099", title="Query Test Paper")
        repo = PaperRepository(self.ctx.connection)
        repo.upsert(paper)
        self.ctx.connection.commit()

        # 列表查询
        papers = self.ctx.paper_query_service.list_papers()
        self.assertGreaterEqual(len(papers), 1)

        # 详情查询
        detail = self.ctx.paper_query_service.get_paper_detail("2501.00099")
        self.assertIsNotNone(detail)
        self.assertEqual(detail.title, "Query Test Paper")

        # 筛选
        filtered = self.ctx.paper_query_service.list_papers(
            PaperListFilters(keyword="Query Test")
        )
        self.assertEqual(len(filtered), 1)

    def test_subscription_view_builds_with_real_ctx(self) -> None:
        """真实 AppContext + subscriptions_page 构建不抛异常"""
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_dashboard_view_builds_with_real_ctx(self) -> None:
        """真实 AppContext + dashboard_page 构建不抛异常"""
        view = build_dashboard_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_paper_detail_view_builds_with_real_ctx(self) -> None:
        """真实 AppContext + paper_detail_page 构建不抛异常（论文不存在场景）"""
        view = build_paper_detail_view(self.ctx, self.page, "nonexistent")  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)


# ============================================================================
# 10. 异常处理与业务规则测试
# ============================================================================


class ExceptionHandlingTests(unittest.TestCase):
    """服务异常映射与 UI 可消费错误信息的测试"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.ctx = create_app_context(":memory:")

    def tearDown(self) -> None:
        self.ctx.close()

    def test_subscription_not_found_exception_message(self) -> None:
        err = SubscriptionNotFoundError("nonexistent-id")
        self.assertIn("nonexistent-id", str(err))
        self.assertIsInstance(err, Exception)

    def test_duplicate_name_exception_message(self) -> None:
        err = DuplicateSubscriptionNameError("CV Daily")
        self.assertIn("CV Daily", str(err))

    def test_invalid_subscription_rule_exception(self) -> None:
        err = InvalidSubscriptionRuleError("Categories must include at least one entry")
        self.assertIn("Categories", str(err))

    def test_paper_not_found_exception_message(self) -> None:
        err = PaperNotFoundError("2501.99999")
        self.assertIn("2501.99999", str(err))

    def test_invalid_status_exception_message(self) -> None:
        err = InvalidPaperStatusError("hidden paper cannot be starred")
        self.assertIn("hidden", str(err))

    def test_delete_nonexistent_subscription_raises(self) -> None:
        with self.assertRaises(SubscriptionNotFoundError):
            self.ctx.subscription_service.delete_subscription("nonexistent")

    def test_status_update_nonexistent_paper_raises(self) -> None:
        with self.assertRaises(PaperNotFoundError):
            self.ctx.status_service.set_starred("nonexistent", True)


# ============================================================================
# 11. 边缘情况测试
# ============================================================================


class EdgeCaseTests(unittest.TestCase):
    """空态、边界值与 data-driven 场景测试"""

    def setUp(self) -> None:
        self.conn = _setup_db()
        self.ctx = create_app_context(":memory:")
        self.page = _MockPage()

    def tearDown(self) -> None:
        self.ctx.close()

    def test_subscription_last_synced_at_default_null(self) -> None:
        """新订阅的 last_synced_at 默认为 None"""
        sub = self.ctx.subscription_service.create_subscription(
            SubscriptionUpsertInput(name="No Sync Yet", categories=["cs.AI"])
        )
        self.assertIsNone(sub.last_synced_at)

    def test_subscription_list_empty_does_not_crash_ui(self) -> None:
        """订阅为空时视图构建不崩溃"""
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_paper_list_empty_does_not_crash_ui(self) -> None:
        """论文为空时视图构建不崩溃"""
        view = build_dashboard_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_paper_detail_not_found_does_not_crash_ui(self) -> None:
        """论文不存在时视图构建不崩溃"""
        view = build_paper_detail_view(self.ctx, self.page, "nonexistent")  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_subscription_with_all_fields_populated(self) -> None:
        """全字段 Subscription 在列表视图中正确渲染"""
        sub = self.ctx.subscription_service.create_subscription(
            SubscriptionUpsertInput(
                name="Full Featured",
                enabled=True,
                categories=["cs.CV", "cs.LG", "stat.ML"],
                include_keywords=["transformer", "attention", "diffusion"],
                exclude_keywords=["medical", "biology"],
                authors=["Alice Smith", "Bob Jones"],
                query_text="cat:cs.CV AND all:transformer",
                sync_interval_minutes=720,
            )
        )
        view = build_subscriptions_view(self.ctx, self.page)  # type: ignore[arg-type]
        self.assertIsInstance(view, ft.View)

    def test_service_exceptions_are_serializable(self) -> None:
        """所有自定义异常可被 str() 序列化，保证 UI 可展示"""
        exceptions = [
            SubscriptionNotFoundError("id-1"),
            DuplicateSubscriptionNameError("name"),
            InvalidSubscriptionRuleError("reason text"),
            PaperNotFoundError("arxiv-1"),
            InvalidPaperStatusError("reason text"),
        ]
        for exc in exceptions:
            msg = str(exc)
            self.assertTrue(isinstance(msg, str))
            self.assertGreater(len(msg), 0)


# ============================================================================
# 12. DTO 序列化与往返测试
# ============================================================================


class DTORoundTripTests(unittest.TestCase):
    """DTO 构造 → 访问字段 → 重新构造 的往返测试"""

    def test_paper_list_item_round_trip(self) -> None:
        d1 = _make_paper_list_item()
        d2 = PaperListItemDTO(
            arxiv_id=d1.arxiv_id,
            title=d1.title,
            authors_preview=d1.authors_preview,
            primary_category=d1.primary_category,
            categories=d1.categories,
            published_at=d1.published_at,
            updated_at=d1.updated_at,
            is_starred=d1.is_starred,
            is_read=d1.is_read,
            is_hidden=d1.is_hidden,
        )
        self.assertEqual(d1.arxiv_id, d2.arxiv_id)
        self.assertEqual(d1.title, d2.title)

    def test_paper_detail_round_trip(self) -> None:
        d1 = _make_paper_detail(rating=3, note="Good paper", tags=["important"])
        d2 = PaperDetailDTO(
            arxiv_id=d1.arxiv_id,
            latest_version=d1.latest_version,
            title=d1.title,
            abstract=d1.abstract,
            authors=d1.authors,
            primary_category=d1.primary_category,
            categories=d1.categories,
            published_at=d1.published_at,
            updated_at=d1.updated_at,
            abs_url=d1.abs_url,
            pdf_url=d1.pdf_url,
            is_starred=d1.is_starred,
            is_read=d1.is_read,
            is_hidden=d1.is_hidden,
            rating=d1.rating,
            note=d1.note,
            tags=d1.tags,
        )
        self.assertEqual(d1.rating, d2.rating)
        self.assertEqual(d1.note, d2.note)
        self.assertEqual(d1.tags, d2.tags)

    def test_sync_result_dto(self) -> None:
        now = datetime.now(timezone.utc)
        dto = SyncResultDTO(
            subscription_id="s1",
            subscription_name="Test",
            status=SyncRunStatus.SUCCESS,
            fetched_count=10,
            inserted_count=5,
            updated_count=2,
            started_at=now,
            finished_at=now,
        )
        self.assertEqual(dto.status, SyncRunStatus.SUCCESS)
        self.assertEqual(dto.inserted_count, 5)
        self.assertIsNone(dto.error_message)

    def test_sync_result_dto_failure(self) -> None:
        now = datetime.now(timezone.utc)
        dto = SyncResultDTO(
            subscription_id="s1",
            subscription_name="Test",
            status=SyncRunStatus.FAILED,
            fetched_count=0,
            inserted_count=0,
            updated_count=0,
            started_at=now,
            finished_at=now,
            error_message="Network error",
        )
        self.assertEqual(dto.status, SyncRunStatus.FAILED)
        self.assertIsNotNone(dto.error_message)
        self.assertIn("Network", dto.error_message)


# ============================================================================
# 13. 回归测试：AppContext + UI 装配不破坏现有切片
# ============================================================================


class Slice08RegressionTests(unittest.TestCase):
    """确保 Slice 08 UI 代码不破坏 Slice 01-07 已有的任何功能"""

    def test_all_services_still_importable(self) -> None:
        from app.application.services import (
            PaperQueryService,
            StatusService,
            SubscriptionService,
            SyncService,
        )
        self.assertTrue(True)

    def test_all_dtos_still_importable(self) -> None:
        from app.application.dto import (
            PaperDetailDTO,
            PaperListFilters,
            PaperListItemDTO,
            PaperStatusPatchInput,
            SubscriptionUpsertInput,
            SyncResultDTO,
        )
        self.assertTrue(True)

    def test_all_models_still_importable(self) -> None:
        from app.domain.models import Paper, PaperStatus, Subscription, SyncRun
        self.assertTrue(True)

    def test_all_enums_still_importable(self) -> None:
        from app.domain.enums import SyncRunStatus, SyncTriggerType
        self.assertTrue(True)

    def test_all_exceptions_still_importable(self) -> None:
        from app.application.services.exceptions import (
            SubscriptionNotFoundError,
            DuplicateSubscriptionNameError,
            InvalidSubscriptionRuleError,
            PaperNotFoundError,
            InvalidPaperStatusError,
        )
        self.assertTrue(True)

    def test_all_repositories_still_importable(self) -> None:
        from app.infrastructure.db.repositories import (
            PaperRepository,
            PaperStatusRepository,
            SubscriptionRepository,
            SyncRunRepository,
        )
        self.assertTrue(True)

    def test_app_context_factory_still_works(self) -> None:
        ctx = create_app_context(":memory:")
        try:
            self.assertIsNotNone(ctx.paper_query_service)
            self.assertIsNotNone(ctx.status_service)
            self.assertIsNotNone(ctx.subscription_service)
            self.assertIsNotNone(ctx.sync_service)
        finally:
            ctx.close()

    def test_all_ui_modules_importable(self) -> None:
        from app.ui.app_shell import AppShell
        from app.ui.components import filter_panel, loading_overlay
        from app.ui.components import notification_bar, paper_status_bar, subscription_form
        from app.ui.pages import dashboard_page, paper_detail_page, subscriptions_page
        self.assertTrue(True)

    def test_slice_01_paper_model_still_creatable(self) -> None:
        p = _make_paper()
        self.assertEqual(p.arxiv_id, "2501.00001")

    def test_slice_01_subscription_model_with_last_synced_at(self) -> None:
        sub = Subscription(
            id="test",
            name="Test",
            categories=["cs.CV"],
            last_synced_at="2026-06-01T00:00:00Z",
        )
        self.assertEqual(sub.last_synced_at, "2026-06-01T00:00:00Z")

    def test_slice_01_paper_status_model_still_creatable(self) -> None:
        ps = PaperStatus(arxiv_id="2501.00001")
        self.assertFalse(ps.is_starred)
        self.assertFalse(ps.is_read)
        self.assertFalse(ps.is_hidden)
