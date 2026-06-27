"""订阅创建/编辑表单对话框。

在页面上打开一个 :class:`ft.AlertDialog`，收集订阅规则字段，
通过 Pydantic 校验后回调调用方执行创建或更新。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft
from pydantic import ValidationError

from app.application.dto import SubscriptionUpsertInput
from app.domain.models import Subscription


def _parse_comma_list(text: str) -> list[str]:
    """将逗号分隔的字符串解析为去空白、去重的列表。"""
    if not text.strip():
        return []
    items = [item.strip() for item in text.split(",") if item.strip()]
    # 保序去重
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def open_subscription_dialog(
    page: ft.Page,
    *,
    existing: Subscription | None = None,
    on_save: Callable[[SubscriptionUpsertInput], None],
) -> None:
    """打开订阅表单对话框。

    Args:
        page: 当前 Flet 页面实例
        existing: 现有订阅对象（编辑模式）；``None`` 表示创建模式
        on_save: 校验通过后的回调，接收 :class:`SubscriptionUpsertInput`
    """
    is_edit = existing is not None

    # -- 错误信息占位 --
    error_text = ft.Text("", color=ft.Colors.RED_700, size=13)

    # -- 表单字段 --
    name_field = ft.TextField(
        label="Name *",
        value=existing.name if existing else "",
        hint_text="e.g. Computer Vision Weekly",
        dense=True,
    )

    categories_field = ft.TextField(
        label="Categories (comma-separated)",
        value=", ".join(existing.categories) if existing else "",
        hint_text="e.g. cs.CV, cs.AI, stat.ML",
        dense=True,
    )

    include_kw_field = ft.TextField(
        label="Include Keywords (comma-separated)",
        value=", ".join(existing.include_keywords) if existing else "",
        hint_text="e.g. transformer, attention",
        dense=True,
    )

    exclude_kw_field = ft.TextField(
        label="Exclude Keywords (comma-separated)",
        value=", ".join(existing.exclude_keywords) if existing else "",
        hint_text="e.g. medical, biology",
        dense=True,
    )

    authors_field = ft.TextField(
        label="Authors (comma-separated)",
        value=", ".join(existing.authors) if existing else "",
        hint_text="e.g. Kaiming He, Yann LeCun",
        dense=True,
    )

    query_text_field = ft.TextField(
        label="Raw Query (advanced)",
        value=existing.query_text or "" if existing else "",
        hint_text="arXiv API query string (optional)",
        dense=True,
    )

    sync_interval_field = ft.TextField(
        label="Sync Interval (minutes)",
        value=str(existing.sync_interval_minutes) if existing else "1440",
        hint_text="1440 = daily",
        dense=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        width=200,
    )

    enabled_switch = ft.Switch(
        label="Enabled",
        value=existing.enabled if existing else True,
    )

    # -- 构建对话框 --

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Edit Subscription" if is_edit else "New Subscription"),
        content=ft.Column(
            controls=[
                name_field,
                categories_field,
                include_kw_field,
                exclude_kw_field,
                authors_field,
                query_text_field,
                sync_interval_field,
                enabled_switch,
                error_text,
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            height=450,
            width=500,
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.FilledButton("Save", on_click=lambda e: _handle_save()),
        ],
    )

    def _handle_save() -> None:
        """收集表单值、校验并回调。"""
        error_text.value = ""

        # 解析 syn_interval
        sync_interval = 1440
        raw_interval = sync_interval_field.value.strip()
        if raw_interval:
            try:
                sync_interval = int(raw_interval)
            except ValueError:
                error_text.value = "Sync interval must be a number."
                page.update()
                return

        # 构建输入
        try:
            input_ = SubscriptionUpsertInput(
                name=name_field.value,
                enabled=enabled_switch.value,
                categories=_parse_comma_list(categories_field.value),
                include_keywords=_parse_comma_list(include_kw_field.value),
                exclude_keywords=_parse_comma_list(exclude_kw_field.value),
                authors=_parse_comma_list(authors_field.value),
                query_text=query_text_field.value.strip() or None,
                sync_interval_minutes=sync_interval,
            )
        except ValidationError as exc:
            # 提取第一条用户可读错误
            messages: list[str] = []
            for err in exc.errors():
                loc = " → ".join(str(p) for p in err["loc"])
                messages.append(f"{loc}: {err['msg']}")
            error_text.value = "\n".join(messages[:3])
            page.update()
            return
        except ValueError as exc:
            error_text.value = str(exc)
            page.update()
            return

        # 校验通过
        page.pop_dialog()
        on_save(input_)

    page.show_dialog(dialog)
