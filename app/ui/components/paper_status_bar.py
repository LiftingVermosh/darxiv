"""论文状态操作栏组件。

提供收藏、已读、忽略三个布尔状态的快速切换按钮，
供列表页与详情页复用。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

# 回调签名：(field_name: str, new_value: bool) -> None
StatusToggleCallback = Callable[[str, bool], None]


def build_status_bar(
    *,
    is_starred: bool = False,
    is_read: bool = False,
    is_hidden: bool = False,
    on_toggle: StatusToggleCallback | None = None,
    show_labels: bool = False,
) -> ft.Row:
    """构建状态切换按钮行。

    三个按钮分别控制收藏、已读、忽略状态。
    激活态使用填充图标，未激活使用镂空图标。

    Args:
        is_starred: 当前是否已收藏
        is_read: 当前是否已读
        is_hidden: 当前是否已隐藏
        on_toggle: 状态切换回调，接收 ``(field_name, new_value)``
        show_labels: 是否在图标旁显示文字标签

    Returns:
        包含三个 :class:`ft.IconButton` 的 :class:`ft.Row`
    """

    def _handle_star(e: ft.ControlEvent) -> None:
        if on_toggle:
            on_toggle("is_starred", not is_starred)

    def _handle_read(e: ft.ControlEvent) -> None:
        if on_toggle:
            on_toggle("is_read", not is_read)

    def _handle_hidden(e: ft.ControlEvent) -> None:
        if on_toggle:
            on_toggle("is_hidden", not is_hidden)

    star_icon = ft.Icons.STAR if is_starred else ft.Icons.STAR_BORDER
    star_color = ft.Colors.AMBER_700 if is_starred else ft.Colors.GREY_500
    star_tooltip = "Unstar" if is_starred else "Star"

    read_icon = ft.Icons.CHECK_CIRCLE if is_read else ft.Icons.CHECK_CIRCLE_OUTLINE
    read_color = ft.Colors.GREEN_700 if is_read else ft.Colors.GREY_500
    read_tooltip = "Mark unread" if is_read else "Mark read"

    hidden_icon = ft.Icons.VISIBILITY_OFF if is_hidden else ft.Icons.VISIBILITY
    hidden_color = ft.Colors.RED_700 if is_hidden else ft.Colors.GREY_500
    hidden_tooltip = "Unhide" if is_hidden else "Hide"

    buttons = [
        ft.IconButton(
            icon=star_icon,
            icon_color=star_color,
            tooltip=star_tooltip,
            on_click=_handle_star,
            icon_size=20,
        ),
        ft.IconButton(
            icon=read_icon,
            icon_color=read_color,
            tooltip=read_tooltip,
            on_click=_handle_read,
            icon_size=20,
        ),
        ft.IconButton(
            icon=hidden_icon,
            icon_color=hidden_color,
            tooltip=hidden_tooltip,
            on_click=_handle_hidden,
            icon_size=20,
        ),
    ]

    if show_labels:
        return ft.Row(
            controls=[
                ft.Row(
                    controls=[buttons[0], ft.Text("Star", size=12)],
                    spacing=4,
                ),
                ft.Row(
                    controls=[buttons[1], ft.Text("Read", size=12)],
                    spacing=4,
                ),
                ft.Row(
                    controls=[buttons[2], ft.Text("Hide", size=12)],
                    spacing=4,
                ),
            ],
            spacing=12,
        )
    return ft.Row(controls=buttons, spacing=4)
