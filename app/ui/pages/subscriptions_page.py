"""订阅管理页面。

提供订阅列表查看、新建、编辑、删除、启停以及手动同步功能。
所有数据操作经过 :class:`~app.main.AppContext` 暴露的 service 层。
"""

from __future__ import annotations

import asyncio

import flet as ft

from app.application.dto import SubscriptionUpsertInput
from app.application.services.exceptions import (
    DuplicateSubscriptionNameError,
    InvalidSubscriptionRuleError,
    SubscriptionNotFoundError,
)
from app.domain.models import Subscription
from app.main import AppContext
from app.ui.components.loading_overlay import build_loading_view
from app.ui.components.notification_bar import show_notification
from app.ui.components.subscription_form import open_subscription_dialog


def build_subscriptions_view(ctx: AppContext, page: ft.Page) -> ft.View:
    """构建订阅管理页面视图。

    Args:
        ctx: 应用服务容器
        page: 当前 Flet 页面实例

    Returns:
        配置完成的 :class:`ft.View`
    """

    # -- 可变内容容器（在加载/列表/空态之间切换） --
    list_view = ft.ListView(spacing=8, expand=True, padding=16)
    content_area = ft.Container(content=build_loading_view(), expand=True)

    # -- 加载逻辑 --

    def _load_subscriptions() -> None:
        """从 service 层加载订阅列表并重新构建卡片。"""
        try:
            subs = ctx.subscription_service.list_subscriptions()
        except Exception as exc:
            content_area.content = _build_error_view(str(exc))
            page.update()
            return

        if not subs:
            content_area.content = _build_empty_view()
        else:
            list_view.controls = [_build_sub_card(s) for s in subs]
            content_area.content = list_view
        page.update()

    # -- 单卡片构建 --

    def _build_sub_card(sub: Subscription) -> ft.Card:
        """为单个订阅构建展示卡片。"""

        # 摘要文本
        parts: list[str] = []
        if sub.categories:
            parts.append(f"Categories: {', '.join(sub.categories)}")
        if sub.include_keywords:
            parts.append(f"Include: {', '.join(sub.include_keywords)}")
        if sub.exclude_keywords:
            parts.append(f"Exclude: {', '.join(sub.exclude_keywords)}")
        if sub.authors:
            parts.append(f"Authors: {', '.join(sub.authors)}")
        summary = " | ".join(parts) if parts else "No filters configured"

        last_synced = (
            f"Last synced: {sub.last_synced_at}"
            if sub.last_synced_at
            else "Never synced"
        )

        sync_btn_ref = ft.Ref[ft.IconButton]()

        # -- 事件处理器 --

        def _on_toggle_enabled(e: ft.ControlEvent) -> None:
            try:
                ctx.subscription_service.set_subscription_enabled(
                    sub.id, e.control.value
                )
                show_notification(
                    page,
                    f"Subscription '{sub.name}' {'enabled' if e.control.value else 'disabled'}.",
                )
                _load_subscriptions()
            except SubscriptionNotFoundError:
                show_notification(page, "Subscription not found.", is_error=True)
                _load_subscriptions()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        def _on_sync(e: ft.ControlEvent) -> None:
            btn = sync_btn_ref.current
            if btn is not None:
                btn.disabled = True
                btn.icon = ft.Icons.HOURGLASS_EMPTY
            page.update()

            async def _do_sync() -> None:
                """在 executor 线程执行阻塞同步，await 后回到主线程更新 UI。"""
                loop = asyncio.get_running_loop()
                try:
                    result = await loop.run_in_executor(
                        None, ctx.subscription_service.sync_subscription, sub.id
                    )
                    if result.error_message:
                        show_notification(
                            page,
                            f"Sync '{sub.name}' failed: {result.error_message}",
                            is_error=True,
                        )
                    else:
                        show_notification(
                            page,
                            f"Sync '{sub.name}': "
                            f"fetched {result.fetched_count}, "
                            f"new {result.inserted_count}, "
                            f"updated {result.updated_count}",
                        )
                except Exception as exc:
                    show_notification(page, f"Sync failed: {exc}", is_error=True)
                finally:
                    if btn is not None:
                        btn.disabled = False
                        btn.icon = ft.Icons.SYNC
                    _load_subscriptions()

            page.run_task(_do_sync)

        def _on_edit(e: ft.ControlEvent) -> None:
            open_subscription_dialog(
                page,
                existing=sub,
                on_save=lambda input_: _handle_update(sub.id, input_),
            )

        def _on_view_papers(e: ft.ControlEvent) -> None:
            page.go(f"/subscriptions/{sub.id}/papers")

        def _on_delete(e: ft.ControlEvent) -> None:
            def _confirm_delete(ce: ft.ControlEvent) -> None:
                page.pop_dialog()
                try:
                    ctx.subscription_service.delete_subscription(sub.id)
                    show_notification(page, f"Deleted '{sub.name}'.")
                    _load_subscriptions()
                except SubscriptionNotFoundError:
                    show_notification(
                        page, "Subscription not found.", is_error=True
                    )
                    _load_subscriptions()
                except Exception as exc:
                    show_notification(page, f"Error: {exc}", is_error=True)

            confirm_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete Subscription"),
                content=ft.Text(
                    f"Are you sure you want to delete '{sub.name}'?\n\n"
                    f"This will also remove associated sync run records "
                    f"and any papers that no longer belong to any subscription."
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                    ft.FilledButton(
                        "Delete",
                        on_click=_confirm_delete,
                        style=ft.ButtonStyle(color=ft.Colors.RED_700),
                    ),
                ],
            )
            page.show_dialog(confirm_dialog)

        # -- 卡片布局 --

        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            sub.name,
                                            weight=ft.FontWeight.BOLD,
                                            size=16,
                                        ),
                                        ft.Text(summary, size=12, color=ft.Colors.GREY_700),
                                        ft.Text(last_synced, size=11, color=ft.Colors.GREY_500),
                                    ],
                                    spacing=4,
                                    expand=True,
                                ),
                                ft.Column(
                                    controls=[
                                        ft.Switch(
                                            value=sub.enabled,
                                            on_change=_on_toggle_enabled,
                                            label="Enabled",
                                        ),
                                        ft.Row(
                                            controls=[
                                                ft.IconButton(
                                                    icon=ft.Icons.LIST,
                                                    tooltip="View Papers",
                                                    icon_size=18,
                                                    on_click=_on_view_papers,
                                                ),
                                                ft.IconButton(
                                                    ref=sync_btn_ref,
                                                    icon=ft.Icons.SYNC,
                                                    tooltip="Sync Now",
                                                    icon_size=18,
                                                    on_click=_on_sync,
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.EDIT,
                                                    tooltip="Edit",
                                                    icon_size=18,
                                                    on_click=_on_edit,
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.DELETE,
                                                    tooltip="Delete",
                                                    icon_size=18,
                                                    icon_color=ft.Colors.RED_400,
                                                    on_click=_on_delete,
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
                padding=16,
            ),
        )

    # -- CRUD 回调 --

    def _handle_create(input_: SubscriptionUpsertInput) -> None:
        try:
            sub = ctx.subscription_service.create_subscription(input_)
            show_notification(page, f"Created subscription '{sub.name}'.")
            _load_subscriptions()
        except DuplicateSubscriptionNameError:
            show_notification(
                page, "A subscription with this name already exists.", is_error=True
            )
        except InvalidSubscriptionRuleError as exc:
            show_notification(page, f"Invalid rule: {exc}", is_error=True)
        except Exception as exc:
            show_notification(page, f"Error: {exc}", is_error=True)

    def _handle_update(sub_id: str, input_: SubscriptionUpsertInput) -> None:
        try:
            sub = ctx.subscription_service.update_subscription(sub_id, input_)
            show_notification(page, f"Updated subscription '{sub.name}'.")
            _load_subscriptions()
        except SubscriptionNotFoundError:
            show_notification(page, "Subscription not found.", is_error=True)
            _load_subscriptions()
        except DuplicateSubscriptionNameError:
            show_notification(
                page, "A subscription with this name already exists.", is_error=True
            )
        except InvalidSubscriptionRuleError as exc:
            show_notification(page, f"Invalid rule: {exc}", is_error=True)
        except Exception as exc:
            show_notification(page, f"Error: {exc}", is_error=True)

    # -- 初始加载 --
    _load_subscriptions()

    return ft.View(
        route="/subscriptions",
        controls=[content_area],
        appbar=ft.AppBar(
            title=ft.Text("Subscriptions"),
            actions=[
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    tooltip="New Subscription",
                    on_click=lambda e: open_subscription_dialog(
                        page,
                        existing=None,
                        on_save=_handle_create,
                    ),
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# 空态 / 错误态 辅助
# ---------------------------------------------------------------------------


def _build_empty_view() -> ft.Column:
    """无订阅时的占位视图。"""
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.SUBSCRIPTIONS_OUTLINED, size=48, color=ft.Colors.GREY_400),
            ft.Text(
                "No subscriptions yet.\nClick + to create one.",
                size=14,
                color=ft.Colors.GREY_500,
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
