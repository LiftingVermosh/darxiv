"""页面主体内容的安全入场过渡。"""

from __future__ import annotations

import flet as ft


class PageContentTransition(ft.Container):
    """为页面主体提供轻量水平滑入效果。

    只动画内容区，不修改 ``View`` 本身。
    即使客户端不触发动画回调，内容也保持可见，只会停在极小的偏移量上。
    """

    def __init__(
        self,
        content: ft.Control,
        *,
        distance: float = 0.018,
        duration_ms: int = 180,
    ) -> None:
        super().__init__(
            content=content,
            expand=True,
            offset=ft.Offset(distance, 0),
            animate_offset=ft.Animation(
                duration_ms,
                ft.AnimationCurve.EASE_OUT_CUBIC,
            ),
        )

    def did_mount(self) -> None:
        """挂载后将内容区滑回原位。"""
        self.offset = ft.Offset(0, 0)
        self.update()
