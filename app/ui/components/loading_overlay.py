"""加载状态占位组件。

在数据尚未就绪时提供统一的加载视觉反馈，
各页面无需自行拼装 ProgressRing。
"""

from __future__ import annotations

import flet as ft


def build_loading_view(message: str = "Loading...") -> ft.Column:
    """返回一个居中排列的加载指示器。

    Args:
        message: 加载中提示文本，默认为 ``"Loading..."``

    Returns:
        包含 :class:`ft.ProgressRing` 与提示文字的居中 :class:`ft.Column`
    """
    return ft.Column(
        controls=[
            ft.ProgressRing(width=40, height=40),
            ft.Text(message, style=ft.TextStyle(size=14, color=ft.Colors.GREY_600)),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )
