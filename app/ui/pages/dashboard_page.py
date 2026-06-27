"""论文列表仪表盘页面。

提供多条件筛选的论文列表视图，支持快捷状态切换与详情页跳转。

状态切换优化：
- 当前筛选对被切换字段无约束时：更新本地缓存 DTO + 从缓存重建 UI（零 DB 查询）
- 当前筛选对被切换字段有约束时：触发全量 DB 重载，保证列表与筛选条件一致
- 同步后刷新：通过 ``refresh_current_list()`` 回调触发重载
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from app.application.dto import PaperListFilters
from app.application.dto.paper_list_item import PaperListItemDTO
from app.application.services.exceptions import (
    InvalidPaperStatusError,
    PaperNotFoundError,
)
from app.main import AppContext
from app.ui.components.filter_panel import build_filter_panel
from app.ui.components.loading_overlay import build_loading_view
from app.ui.components.notification_bar import show_notification
from app.ui.components.paper_status_bar import build_status_bar

logger = logging.getLogger(__name__)


def build_dashboard_view(ctx: AppContext, page: ft.Page) -> ft.View:
    """构建论文列表仪表盘视图。

    Args:
        ctx: 应用服务容器
        page: 当前 Flet 页面实例

    Returns:
        配置完成的 :class:`ft.View`
    """

    # -- 可变状态 --
    _SAFE_FILTER_KEYS = {
        "keyword", "category", "is_starred", "is_read",
        "is_hidden", "author", "limit",
    }
    default_filters_dict = ctx.settings_service.get("default_list_filters") or {}
    filter_kwargs = {
        k: v for k, v in default_filters_dict.items()
        if k in _SAFE_FILTER_KEYS
    }
    show_hidden_default = ctx.settings_service.get("show_hidden_by_default")
    if not show_hidden_default and filter_kwargs.get("is_hidden") is not False:
        filter_kwargs["is_hidden"] = False
    elif show_hidden_default:
        filter_kwargs["is_hidden"] = None

    try:
        current_filters = PaperListFilters(**filter_kwargs)
    except Exception:
        logger.warning(
            "Failed to apply default_list_filters %r, falling back to defaults.",
            filter_kwargs,
        )
        current_filters = PaperListFilters(
            is_hidden=None if show_hidden_default else False,
        )

    # 内存缓存：arxiv_id → PaperListItemDTO
    _paper_cache: dict[str, PaperListItemDTO] = {}

    list_view = ft.ListView(spacing=6, expand=True, padding=16)
    content_area = ft.Container(content=build_loading_view(), expand=True)

    # -- 核心：从 DB 加载并重建 UI --

    def _load_papers(filters: PaperListFilters | None = None) -> None:
        """从数据库加载论文列表并完全重建卡片。"""
        nonlocal current_filters
        if filters is not None:
            current_filters = filters
        try:
            papers = ctx.paper_query_service.list_papers(current_filters)
        except Exception as exc:
            content_area.content = _build_error_view(str(exc))
            page.update()
            return

        # 刷新内存缓存
        _paper_cache.clear()
        for p in papers:
            _paper_cache[p.arxiv_id] = p

        _render_from_cache()

    # -- 从缓存重建 UI（无 DB 查询） --

    def _render_from_cache() -> None:
        """从 ``_paper_cache`` 重建列表 UI。

        与 ``_load_papers`` 不同，本方法不触发数据库查询，
        适用于本地状态更新后的即时 UI 刷新。
        """
        if not _paper_cache:
            content_area.content = _build_empty_view()
        else:
            # 保持与数据库一致的排序（updated_at DESC）
            sorted_papers = sorted(
                _paper_cache.values(),
                key=lambda p: p.updated_at,
                reverse=True,
            )
            list_view.controls = [
                _build_paper_card(p) for p in sorted_papers
            ]
            content_area.content = list_view
        page.update()

    # -- 单卡片构建 --

    def _build_paper_card(paper: PaperListItemDTO) -> ft.Card:
        """为单篇论文构建列表卡片。

        状态条通过闭包捕获当前 *paper* 的状态值，
        切换时通过委托函数更新缓存并重建 UI。
        """

        pub_date = (
            paper.published_at.strftime("%Y-%m-%d")
            if paper.published_at
            else "N/A"
        )

        def _on_status_toggle(field: str, value: bool) -> None:
            """状态切换回调：持久化 → 更新缓存 → 必要时重载。"""
            arxiv_id = paper.arxiv_id
            try:
                if field == "is_starred":
                    ctx.status_service.set_starred(arxiv_id, value)
                elif field == "is_read":
                    ctx.status_service.set_read(arxiv_id, value)
                elif field == "is_hidden":
                    ctx.status_service.set_hidden(arxiv_id, value)
                show_notification(
                    page,
                    f"Paper '{paper.title[:40]}...' updated.",
                )

                # 更新内存缓存中的 DTO
                cached = _paper_cache.get(arxiv_id)
                if cached is not None:
                    if field == "is_starred":
                        cached.is_starred = value
                    elif field == "is_read":
                        cached.is_read = value
                    elif field == "is_hidden":
                        cached.is_hidden = value

                # 若当前筛选对切换字段有约束，缓存中可能残留不再匹配的卡片，
                # 必须回源 DB 重载以保证列表与筛选条件一致。
                _status_filter_active: dict[str, bool] = {
                    "is_starred": current_filters.is_starred is not None,
                    "is_read": current_filters.is_read is not None,
                    "is_hidden": current_filters.is_hidden is not None,
                }
                if _status_filter_active.get(field, False):
                    _load_papers()
                    return

                # 无约束时：从缓存重建 UI（零 DB 查询）
                _render_from_cache()
            except PaperNotFoundError:
                show_notification(page, "Paper not found.", is_error=True)
            except InvalidPaperStatusError as exc:
                show_notification(page, str(exc), is_error=True)
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        def _on_card_click(e: ft.ControlEvent) -> None:
            page.go(f"/paper/{paper.arxiv_id}")

        def _on_delete_paper(e: ft.ControlEvent) -> None:
            e.control.disabled = True
            page.update()
            try:
                msg = ctx.paper_library_service.delete_paper(
                    paper.arxiv_id,
                )
                show_notification(page, msg)
                _load_papers()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)
                e.control.disabled = False
                page.update()

        cat_chips = [
            ft.Container(
                content=ft.Text(cat, size=10, color=ft.Colors.BLUE_700),
                padding=ft.padding.Padding(left=6, top=2, right=6, bottom=2),
                bgcolor=ft.Colors.BLUE_50,
                border_radius=4,
            )
            for cat in paper.categories[:3]
        ]

        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Column(
                                    controls=[
                                        ft.TextButton(
                                            paper.title,
                                            on_click=_on_card_click,
                                            style=ft.ButtonStyle(
                                                color=ft.Colors.BLUE_900,
                                                padding=0,
                                            ),
                                            tooltip="View details",
                                        ),
                                        ft.Row(
                                            controls=[
                                                ft.Text(
                                                    paper.authors_preview,
                                                    size=12,
                                                    color=ft.Colors.GREY_700,
                                                ),
                                                ft.Text(" · ", size=12, color=ft.Colors.GREY_400),
                                                ft.Text(
                                                    pub_date,
                                                    size=12,
                                                    color=ft.Colors.GREY_500,
                                                ),
                                            ],
                                            spacing=0,
                                            wrap=True,
                                        ),
                                        ft.Row(controls=cat_chips, spacing=4, wrap=True),
                                    ],
                                    spacing=4,
                                    expand=True,
                                ),
                                ft.Row(
                                    controls=[
                                        build_status_bar(
                                            is_starred=paper.is_starred,
                                            is_read=paper.is_read,
                                            is_hidden=paper.is_hidden,
                                            on_toggle=_on_status_toggle,
                                        ),
                                        ft.IconButton(
                                            icon=ft.Icons.DELETE_OUTLINE,
                                            icon_size=18,
                                            icon_color=ft.Colors.RED_300,
                                            tooltip="Delete paper",
                                            on_click=_on_delete_paper,
                                        ),
                                    ],
                                    spacing=4,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                    ],
                ),
                padding=12,
                on_click=_on_card_click,
            ),
        )

    # -- 筛选回调 --
    def _on_filter_apply(filters: PaperListFilters) -> None:
        _load_papers(filters)

    # -- 公开刷新入口 --

    def refresh_current_list() -> None:
        """刷新当前列表（从 DB 重载）。

        供同步完成后调用，确保 UI 反映最新数据。
        """
        _load_papers()

    # -- 初始加载 --
    _load_papers()

    v = ft.View(
        route="/dashboard",
        controls=[
            build_filter_panel(current_filters, on_apply=_on_filter_apply),
            content_area,
        ],
        appbar=ft.AppBar(
            title=ft.Text("Dashboard"),
            actions=[
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    tooltip="Refresh list",
                    on_click=lambda e: _load_papers(),
                ),
            ],
        ),
    )

    # 将刷新入口挂载到 view.data，供外部（如 AppShell 同步回调）访问
    v.data = {"refresh_current_list": refresh_current_list}

    return v


# ---------------------------------------------------------------------------
# 空态 / 错误态 辅助
# ---------------------------------------------------------------------------


def _build_empty_view() -> ft.Column:
    """无论文时的占位视图。"""
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.ARTICLE_OUTLINED, size=48, color=ft.Colors.GREY_400),
            ft.Text(
                "No papers found.",
                size=14,
                color=ft.Colors.GREY_500,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                "Sync a subscription to fetch papers,\nor adjust your filters.",
                size=12,
                color=ft.Colors.GREY_400,
                text_align=ft.TextAlign.CENTER,
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )


def _build_error_view(message: str) -> ft.Column:
    """加载失败时的错误占位视图。"""
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.RED_400),
            ft.Text(message, size=14, color=ft.Colors.RED_700, text_align=ft.TextAlign.CENTER),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )
