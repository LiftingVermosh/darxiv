"""订阅论文列表页面。

展示特定订阅已抓取的论文列表，复用 Dashboard 的卡片样式。
支持从当前订阅解绑论文，以及全局删除。
"""

from __future__ import annotations

import flet as ft

from app.application.dto import PaperListFilters
from app.application.dto.paper_list_item import PaperListItemDTO
from app.application.services.exceptions import (
    PaperNotFoundError,
    SubscriptionNotFoundError,
)
from app.main import AppContext
from app.ui.components.loading_overlay import build_loading_view
from app.ui.components.notification_bar import show_notification
from app.ui.components.paper_status_bar import build_status_bar


def build_subscription_papers_view(
    ctx: AppContext, page: ft.Page, subscription_id: str
) -> ft.View:
    """构建订阅论文列表视图。

    Args:
        ctx: 应用服务容器
        page: 当前 Flet 页面实例
        subscription_id: 目标订阅 ID

    Returns:
        配置完成的 :class:`ft.View`
    """

    # -- 加载订阅元数据 --
    sub = ctx.subscription_service.get_subscription(subscription_id)
    if sub is None:
        return ft.View(
            route=f"/subscriptions/{subscription_id}/papers",
            controls=[
                _build_centered_empty_state(
                    icon=ft.Icons.SEARCH_OFF,
                    title="Subscription not found.",
                ),
            ],
            appbar=ft.AppBar(
                title=ft.Text("Subscription Papers"),
                leading=ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    tooltip="Back to Subscriptions",
                    on_click=lambda e: page.go("/subscriptions"),
                ),
            ),
        )

    list_view = ft.ListView(spacing=6, expand=True, padding=16)
    content_area = ft.Container(content=build_loading_view(), expand=True)

    # -- 加载逻辑 --

    def _load_papers() -> None:
        """从 service 层加载该订阅的论文列表并重建卡片。"""
        try:
            filters = PaperListFilters(subscription_id=subscription_id)
            papers = ctx.paper_query_service.list_papers(filters)
        except Exception as exc:
            content_area.content = ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                content=ft.Text(
                    f"Error: {exc}",
                    size=14,
                    color=ft.Colors.RED_700,
                ),
            )
            page.update()
            return

        if not papers:
            content_area.content = _build_centered_empty_state(
                icon=ft.Icons.ARTICLE_OUTLINED,
                title="No attributed papers for this subscription yet.",
                subtitle=(
                    "Papers from before the upgrade lack source records "
                    "and won't appear here.\n"
                    "Re-sync this subscription to build provenance."
                ),
            )
        else:
            list_view.controls = [
                _build_paper_card(p) for p in papers
            ]
            content_area.content = list_view
        page.update()

    # -- 单卡片构建 --

    def _build_paper_card(paper: PaperListItemDTO) -> ft.Card:
        """为单篇论文构建列表卡片，复用 Dashboard 样式。"""

        pub_date = (
            paper.published_at.strftime("%Y-%m-%d")
            if paper.published_at
            else "N/A"
        )

        def _on_card_click(e: ft.ControlEvent) -> None:
            page.go(f"/paper/{paper.arxiv_id}")

        def _on_unlink_from_sub(e: ft.ControlEvent) -> None:
            """从当前订阅解绑论文。"""
            try:
                msg = ctx.paper_library_service.delete_paper(
                    paper.arxiv_id,
                    subscription_id=subscription_id,
                )
                show_notification(page, msg)
                _load_papers()
            except PaperNotFoundError:
                show_notification(page, "Paper not found.", is_error=True)
                _load_papers()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        def _on_delete_global(e: ft.ControlEvent) -> None:
            """全局删除论文。"""
            try:
                msg = ctx.paper_library_service.delete_paper(
                    paper.arxiv_id,
                )
                show_notification(page, msg)
                _load_papers()
            except PaperNotFoundError:
                show_notification(page, "Paper not found.", is_error=True)
                _load_papers()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

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
                                                ft.Text(
                                                    " · ",
                                                    size=12,
                                                    color=ft.Colors.GREY_400,
                                                ),
                                                ft.Text(
                                                    pub_date,
                                                    size=12,
                                                    color=ft.Colors.GREY_500,
                                                ),
                                            ],
                                            spacing=0,
                                            wrap=True,
                                        ),
                                        ft.Row(
                                            controls=cat_chips,
                                            spacing=4,
                                            wrap=True,
                                        ),
                                    ],
                                    spacing=4,
                                    expand=True,
                                ),
                                ft.Column(
                                    controls=[
                                        build_status_bar(
                                            is_starred=paper.is_starred,
                                            is_read=paper.is_read,
                                            is_hidden=paper.is_hidden,
                                            on_toggle=None,  # read-only in this view
                                        ),
                                        ft.Row(
                                            controls=[
                                                ft.IconButton(
                                                    icon=ft.Icons.LINK_OFF,
                                                    icon_size=16,
                                                    icon_color=ft.Colors.ORANGE_400,
                                                    tooltip="Unlink from this subscription",
                                                    on_click=_on_unlink_from_sub,
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.DELETE_OUTLINE,
                                                    icon_size=16,
                                                    icon_color=ft.Colors.RED_300,
                                                    tooltip="Delete permanently",
                                                    on_click=_on_delete_global,
                                                ),
                                            ],
                                            spacing=2,
                                        ),
                                    ],
                                    spacing=4,
                                    horizontal_alignment=ft.CrossAxisAlignment.END,
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

    # -- 初始加载 --
    _load_papers()

    return ft.View(
        route=f"/subscriptions/{subscription_id}/papers",
        controls=[content_area],
        appbar=ft.AppBar(
            title=ft.Text(f"Papers: {sub.name}"),
            leading=ft.IconButton(
                icon=ft.Icons.ARROW_BACK,
                tooltip="Back to Subscriptions",
                on_click=lambda e: page.go("/subscriptions"),
            ),
        ),
    )


def _build_centered_empty_state(
    *,
    icon: str,
    title: str,
    subtitle: str | None = None,
) -> ft.Container:
    """构建订阅页统一的居中空态。"""
    controls: list[ft.Control] = [
        ft.Icon(
            icon,
            size=48,
            color=ft.Colors.GREY_400,
        ),
        ft.Text(
            title,
            size=14,
            color=ft.Colors.GREY_500,
            text_align=ft.TextAlign.CENTER,
        ),
    ]
    if subtitle:
        controls.append(
            ft.Text(
                subtitle,
                size=12,
                color=ft.Colors.GREY_400,
                text_align=ft.TextAlign.CENTER,
            )
        )

    return ft.Container(
        expand=True,
        alignment=ft.Alignment(0, 0),
        content=ft.Column(
            controls=controls,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )
