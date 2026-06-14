"""筛选面板组件。

提供关键词、分类、状态多条件筛选控件，
用户点击 Apply 后将通过回调传递新的 :class:`~app.application.dto.PaperListFilters`。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from app.application.dto import PaperListFilters

# 回调签名：(filters: PaperListFilters) -> None
FilterApplyCallback = Callable[[PaperListFilters], None]

_PRESET_CATEGORIES = [
    "cs.AI",
    "cs.CL",
    "cs.CV",
    "cs.LG",
    "cs.NE",
    "stat.ML",
    "physics",
    "math",
    "q-bio",
    "q-fin",
]


def build_filter_panel(
    current_filters: PaperListFilters,
    on_apply: FilterApplyCallback,
) -> ft.Container:
    """构建筛选控件面板。

    Args:
        current_filters: 当前生效的筛选条件（用于初始化控件值）
        on_apply: 用户点击 Apply 时的回调，传入新的 ``PaperListFilters``

    Returns:
        包含筛选控件与 Apply 按钮的 :class:`ft.Container`
    """

    # -- 控件引用（通过 ref 在回调中读取值） --
    keyword_field = ft.TextField(
        label="Keyword",
        value=current_filters.keyword or "",
        hint_text="Search title / abstract...",
        dense=True,
        width=220,
        text_size=13,
    )

    category_dropdown = ft.Dropdown(
        label="Category",
        dense=True,
        width=150,
        text_size=13,
        options=[ft.dropdown.Option(key="__all__", text="All")]
        + [ft.dropdown.Option(key=c, text=c) for c in _PRESET_CATEGORIES],
        value=current_filters.category if current_filters.category else "__all__",
    )

    starred_check = ft.Checkbox(
        label="Starred",
        value=current_filters.is_starred,
        tristate=True,
    )

    read_check = ft.Checkbox(
        label="Read",
        value=current_filters.is_read,
        tristate=True,
    )

    hidden_check = ft.Checkbox(
        label="Hidden",
        value=current_filters.is_hidden,
        tristate=True,
    )

    # -- 回调逻辑 --

    def _on_apply(e: ft.ControlEvent) -> None:
        """收集控件值，构建 PaperListFilters 并回调。"""
        kw = keyword_field.value.strip() if keyword_field.value else None
        cat = (
            category_dropdown.value
            if category_dropdown.value and category_dropdown.value != "__all__"
            else None
        )

        # Flet Checkbox tristate: None → 不过滤
        def _checkbox_value(chk: ft.Checkbox) -> bool | None:
            return chk.value if chk.value is not None else None

        on_apply(
            PaperListFilters(
                keyword=kw if kw else None,
                category=cat,
                is_starred=_checkbox_value(starred_check),
                is_read=_checkbox_value(read_check),
                is_hidden=_checkbox_value(hidden_check),
            )
        )

    def _on_clear(e: ft.ControlEvent) -> None:
        """重置所有控件为默认值。"""
        keyword_field.value = ""
        category_dropdown.value = "__all__"
        starred_check.value = None
        read_check.value = None
        hidden_check.value = None
        on_apply(PaperListFilters())

    apply_btn = ft.ElevatedButton(
        "Apply",
        on_click=_on_apply,
        height=36,
        style=ft.ButtonStyle(padding=16),
    )

    clear_btn = ft.OutlinedButton(
        "Clear",
        on_click=_on_clear,
        height=36,
        style=ft.ButtonStyle(padding=16),
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        keyword_field,
                        category_dropdown,
                        ft.VerticalDivider(width=1),
                        starred_check,
                        read_check,
                        hidden_check,
                        ft.VerticalDivider(width=1),
                        apply_btn,
                        clear_btn,
                    ],
                    spacing=8,
                    wrap=True,
                ),
            ],
            spacing=4,
        ),
        padding=ft.padding.Padding(left=16, top=8, right=16, bottom=8),
        bgcolor=ft.Colors.GREY_100,
        border_radius=ft.border_radius.BorderRadius(
            top_left=8, top_right=8, bottom_left=0, bottom_right=0
        ),
    )
