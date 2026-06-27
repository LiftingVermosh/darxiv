"""通知条组件 —— 基于 Flet SnackBar 的轻量级用户反馈。

UI 层统一调用 :func:`show_notification` 展示成功/错误消息，
避免各页面直接操作 SnackBar API。
"""

from __future__ import annotations

import flet as ft


def show_notification(page: ft.Page, message: str, *, is_error: bool = False) -> None:
    """在页面底部弹出通知条，3 秒后自动消失。

    Args:
        page: 当前 Flet 页面实例
        message: 显示的消息文本
        is_error: ``True`` 时使用红色背景，否则使用绿色
    """
    snack = ft.SnackBar(
        content=ft.Text(message, color=ft.Colors.WHITE),
        bgcolor=ft.Colors.RED_700 if is_error else ft.Colors.GREEN_700,
        duration=3000,
    )
    page.snack_bar = snack
    page.snack_bar.open = True
    page.update()
