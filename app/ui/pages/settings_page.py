"""应用设置页面。

提供自动同步开关、同步间隔配置、默认显示偏好等运行期设置编辑，
同时展示调度器运行状态与最近一次自动同步的结果摘要。
"""

from __future__ import annotations

import flet as ft

from app.application.dto.app_settings_dto import AppSettingsDTO
from app.application.dto.scheduler_tick_event import SchedulerTickEvent
from app.main import AppContext
from app.ui.components.notification_bar import show_notification
from app.ui.components.page_content_transition import PageContentTransition


# ---------------------------------------------------------------------------
# Interval presets
# ---------------------------------------------------------------------------

_INTERVAL_OPTIONS: list[tuple[int | None, str]] = [
    (None, "Per‑subscription (each at its own interval)"),
    (15, "Every 15 minutes (all subscriptions)"),
    (30, "Every 30 minutes (all subscriptions)"),
    (60, "Every 1 hour (all subscriptions)"),
    (120, "Every 2 hours (all subscriptions)"),
    (360, "Every 6 hours (all subscriptions)"),
    (720, "Every 12 hours (all subscriptions)"),
    (1440, "Every 24 hours (all subscriptions)"),
]


def build_settings_view(ctx: AppContext, page: ft.Page) -> ft.View:
    """构建设置页面视图。

    Args:
        ctx: 应用服务容器
        page: 当前 Flet 页面实例

    Returns:
        配置完成的 :class:`ft.View`
    """

    current_settings = ctx.settings_service.get_all()

    auto_sync_switch = ft.Switch(
        value=current_settings.auto_sync_enabled,
        label="Enable Auto-Sync",
    )

    interval_dropdown = ft.Dropdown(
        label="Global Sync Interval",
        dense=True,
        width=300,
        options=[
            ft.dropdown.Option(
                key=str(opt[0]) if opt[0] is not None else "__none__",
                text=opt[1],
            )
            for opt in _INTERVAL_OPTIONS
        ],
        value=(
            str(current_settings.global_sync_interval_minutes)
            if current_settings.global_sync_interval_minutes is not None
            else "__none__"
        ),
    )

    show_hidden_switch = ft.Switch(
        value=current_settings.show_hidden_by_default,
        label="Show Hidden Papers by Default",
    )

    scheduler_status_text = ft.Text(
        _scheduler_status_label(ctx.scheduler.last_tick_event),
        size=13,
    )

    last_sync_results = ft.ListView(spacing=4, expand=True, height=200)

    _previous_listener = getattr(page, "_settings_tick_listener", None)
    if _previous_listener is not None:
        ctx.scheduler.remove_tick_listener(_previous_listener)

    def _on_scheduler_tick(event: SchedulerTickEvent) -> None:
        """调度器每次 tick 完成时刷新状态展示。"""
        try:
            _refresh_scheduler_status()
            page.update()
        except Exception:
            pass

    ctx.scheduler.add_tick_listener(_on_scheduler_tick)
    page._settings_tick_listener = _on_scheduler_tick  # type: ignore[attr-defined]

    def _on_save(e: ft.ControlEvent) -> None:
        """收集控件值并持久化设置。"""
        try:
            raw = interval_dropdown.value
            if raw == "__none__" or raw is None:
                interval = None
            else:
                interval = int(raw)

            new_settings = AppSettingsDTO(
                auto_sync_enabled=auto_sync_switch.value,
                global_sync_interval_minutes=interval,
                show_hidden_by_default=show_hidden_switch.value,
                default_list_filters=current_settings.default_list_filters,
                last_open_page=current_settings.last_open_page,
            )
            ctx.settings_service.update(new_settings)

            if auto_sync_switch.value and not ctx.scheduler.is_running:
                ctx.scheduler.start()
            elif not auto_sync_switch.value and ctx.scheduler.is_running:
                ctx.scheduler.stop()

            show_notification(page, "Settings saved.")
            _refresh_scheduler_status()
        except Exception as exc:
            show_notification(page, f"Failed to save settings: {exc}", is_error=True)

    def _refresh_scheduler_status() -> None:
        """刷新调度器状态文本和最近同步结果。"""
        event = ctx.scheduler.last_tick_event
        scheduler_status_text.value = _scheduler_status_label(event)
        last_sync_results.controls = _build_sync_result_rows(event)
        page.update()

    last_sync_results.controls = _build_sync_result_rows(
        ctx.scheduler.last_tick_event
    )

    main_content = ft.Column(
        controls=[
            ft.Text("Auto Sync", size=18, weight=ft.FontWeight.BOLD),
            ft.Text(
                "When enabled, the app periodically checks for new papers "
                "across all enabled subscriptions.",
                size=13,
                color=ft.Colors.GREY_600,
            ),
            auto_sync_switch,
            interval_dropdown,
            ft.Divider(),
            ft.Text("Display", size=18, weight=ft.FontWeight.BOLD),
            show_hidden_switch,
            ft.Divider(),
            ft.FilledButton("Save Settings", on_click=_on_save),
            ft.Divider(),
            ft.Text("Scheduler Status", size=18, weight=ft.FontWeight.BOLD),
            scheduler_status_text,
            ft.Text("Recent Sync Results", size=14, weight=ft.FontWeight.W_500),
            last_sync_results,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    return ft.View(
        route="/settings",
        controls=[PageContentTransition(main_content)],
        appbar=ft.AppBar(
            title=ft.Text("Settings"),
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scheduler_status_label(event: SchedulerTickEvent | None) -> str:
    """从最近事件构建调度器可读状态行。"""
    if event is None:
        return "No sync has been performed yet."
    if event.status == "running":
        return f"Running — started at {_fmt_dt(event.started_at)}"
    if event.status == "completed":
        total = len(event.results)
        fetched = sum(r.fetched_count for r in event.results)
        inserted = sum(r.inserted_count for r in event.results)
        errors = sum(1 for r in event.results if r.error_message)
        parts = [
            f"Last sync: {_fmt_dt(event.finished_at)}",
            f"{total} subscription(s) processed",
            f"{fetched} papers fetched, {inserted} new",
        ]
        if errors:
            parts.append(f"{errors} error(s)")
        return " · ".join(parts)
    if event.status == "failed":
        return (
            f"Last sync failed at {_fmt_dt(event.finished_at)}: "
            f"{event.error_message or 'Unknown error'}"
        )
    return "Idle"


def _build_sync_result_rows(event: SchedulerTickEvent | None) -> list[ft.Control]:
    """从最近事件构建每个订阅的同步结果行。"""
    if event is None or not event.results:
        return [
            ft.Text(
                "No recent sync data.",
                size=12,
                color=ft.Colors.GREY_500,
            )
        ]

    rows: list[ft.Control] = []
    for r in event.results:
        icon = ft.Icons.CHECK_CIRCLE if not r.error_message else ft.Icons.ERROR
        color = ft.Colors.GREEN_700 if not r.error_message else ft.Colors.RED_700
        detail = (
            f"fetched {r.fetched_count}, new {r.inserted_count}, "
            f"updated {r.updated_count}"
        )
        if r.error_message:
            detail += f" — {r.error_message[:120]}"

        rows.append(
            ft.Row(
                controls=[
                    ft.Icon(icon, size=16, color=color),
                    ft.Text(r.subscription_name, size=13, weight=ft.FontWeight.W_500),
                    ft.Text(detail, size=12, color=ft.Colors.GREY_700),
                ],
                spacing=8,
            )
        )

    return rows


def _fmt_dt(dt) -> str:
    """格式化 datetime 为简短可读字符串。"""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")
