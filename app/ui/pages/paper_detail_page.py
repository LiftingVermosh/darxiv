"""论文详情页面。

展示论文完整元数据，提供收藏/已读/忽略切换、评分、笔记和标签编辑功能。
"""

from __future__ import annotations

import flet as ft

from app.application.dto.paper_detail import PaperDetailDTO
from app.application.services.exceptions import (
    InvalidPaperStatusError,
    PaperNotFoundError,
)
from app.main import AppContext
from app.ui.components.loading_overlay import build_loading_view
from app.ui.components.notification_bar import show_notification
from app.ui.components.paper_status_bar import build_status_bar


def build_paper_detail_view(ctx: AppContext, page: ft.Page, arxiv_id: str) -> ft.View:
    """构建论文详情视图。

    Args:
        ctx: 应用服务容器
        page: 当前 Flet 页面实例
        arxiv_id: 目标论文 arXiv ID

    Returns:
        配置完成的 :class:`ft.View`
    """

    content_area = ft.Container(content=build_loading_view(), expand=True)

    # -- 加载逻辑 --

    def _load_detail() -> None:
        """重新加载论文详情并重建全部内容。"""
        detail = ctx.paper_query_service.get_paper_detail(arxiv_id)
        if detail is None:
            content_area.content = _build_not_found_view()
            page.update()
            return
        try:
            content_area.content = _build_detail_content(detail)
        except Exception as exc:
            content_area.content = _build_error_view(str(exc))
        page.update()

    # -- 详情内容构建 --

    def _build_detail_content(detail: PaperDetailDTO) -> ft.Column:
        """将 PaperDetailDTO 渲染为完整的详情页内容列。"""

        # ---- 状态回调 ----

        def _on_status_toggle(field: str, value: bool) -> None:
            try:
                if field == "is_starred":
                    ctx.status_service.set_starred(arxiv_id, value)
                elif field == "is_read":
                    ctx.status_service.set_read(arxiv_id, value)
                elif field == "is_hidden":
                    ctx.status_service.set_hidden(arxiv_id, value)
                show_notification(page, "Status updated.")
                _load_detail()
            except PaperNotFoundError:
                show_notification(page, "Paper not found.", is_error=True)
            except InvalidPaperStatusError as exc:
                show_notification(page, str(exc), is_error=True)
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        # ---- 评分选择器 ----

        def _build_rating_row(current_rating: int | None) -> ft.Row:
            """构建 1-5 星评分选择器。"""
            stars: list[ft.IconButton] = []

            def _set_rating(r: int) -> None:
                try:
                    ctx.status_service.update_rating(arxiv_id, r)
                    show_notification(page, f"Rating set to {r}.")
                    _load_detail()
                except Exception as exc:
                    show_notification(page, f"Error: {exc}", is_error=True)

            def _clear_rating(e: ft.ControlEvent) -> None:
                try:
                    ctx.status_service.update_rating(arxiv_id, None)
                    show_notification(page, "Rating cleared.")
                    _load_detail()
                except Exception as exc:
                    show_notification(page, f"Error: {exc}", is_error=True)

            for i in range(1, 6):
                filled = current_rating is not None and i <= current_rating
                stars.append(
                    ft.IconButton(
                        icon=ft.Icons.STAR if filled else ft.Icons.STAR_BORDER,
                        icon_color=ft.Colors.AMBER_700 if filled else ft.Colors.GREY_400,
                        icon_size=28,
                        tooltip=f"Rate {i}",
                        on_click=lambda e, r=i: _set_rating(r),
                    )
                )

            return ft.Row(
                controls=stars
                + [
                    ft.TextButton(
                        "Clear",
                        on_click=_clear_rating,
                        style=ft.ButtonStyle(
                            color=ft.Colors.GREY_500,
                            padding=0,
                        ),
                    ),
                ],
                spacing=2,
            )

        # ---- 笔记保存 ----

        note_field = ft.TextField(
            value=detail.note or "",
            label="Note",
            multiline=True,
            min_lines=3,
            max_lines=8,
            hint_text="Write your notes here...",
        )

        def _save_note(e: ft.ControlEvent) -> None:
            try:
                ctx.status_service.update_note(arxiv_id, note_field.value or None)
                show_notification(page, "Note saved.")
                _load_detail()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        # ---- 标签保存 ----

        tags_field = ft.TextField(
            value=", ".join(detail.tags) if detail.tags else "",
            label="Tags (comma-separated)",
            hint_text="e.g. transformer, survey, important",
        )

        def _save_tags(e: ft.ControlEvent) -> None:
            try:
                tag_list = [
                    t.strip()
                    for t in tags_field.value.split(",")
                    if t.strip()
                ]
                ctx.status_service.update_tags(arxiv_id, tag_list)
                show_notification(page, "Tags saved.")
                _load_detail()
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        # ---- 格式化辅助 ----

        def _fmt_date(d) -> str:
            return d.strftime("%Y-%m-%d %H:%M UTC") if d else "N/A"

        # ---- 组装 ----

        return ft.Column(
            controls=[
                # 标题
                ft.Text(detail.title, size=22, weight=ft.FontWeight.BOLD),
                # 作者
                ft.Text(
                    ", ".join(detail.authors),
                    size=14,
                    color=ft.Colors.GREY_700,
                ),
                # 分类标签
                ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Text(detail.primary_category, size=12, color=ft.Colors.BLUE_700),
                            padding=ft.padding.Padding(left=8, top=4, right=8, bottom=4),
                            bgcolor=ft.Colors.BLUE_50,
                            border_radius=4,
                        ),
                    ]
                    + [
                        ft.Container(
                            content=ft.Text(cat, size=10, color=ft.Colors.GREY_600),
                            padding=ft.padding.Padding(left=6, top=2, right=6, bottom=2),
                            bgcolor=ft.Colors.GREY_100,
                            border_radius=4,
                        )
                        for cat in detail.categories
                        if cat != detail.primary_category
                    ],
                    spacing=6,
                    wrap=True,
                ),
                ft.Divider(),
                # 元数据
                _build_metadata_section(detail, _fmt_date),
                ft.Divider(),
                # 摘要
                ft.Text("Abstract", size=16, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=ft.Text(
                        detail.abstract,
                        size=14,
                        color=ft.Colors.GREY_800,
                    ),
                    padding=ft.padding.Padding(left=0, top=8, right=0, bottom=0),
                ),
                ft.Divider(),
                # 外部链接
                ft.Row(
                    controls=[
                        ft.FilledButton(
                            "Open arXiv Page",
                            icon=ft.Icons.OPEN_IN_BROWSER,
                            on_click=lambda e: page.launch_url(detail.abs_url),
                        ),
                        ft.FilledButton(
                            "Open PDF",
                            icon=ft.Icons.PICTURE_AS_PDF,
                            on_click=lambda e: page.launch_url(detail.pdf_url)
                            if detail.pdf_url
                            else show_notification(page, "No PDF URL available.", is_error=True),
                        ),
                    ],
                    spacing=8,
                ),
                ft.Divider(),
                # 状态编辑
                ft.Text("Status", size=16, weight=ft.FontWeight.BOLD),
                build_status_bar(
                    is_starred=detail.is_starred,
                    is_read=detail.is_read,
                    is_hidden=detail.is_hidden,
                    on_toggle=_on_status_toggle,
                    show_labels=True,
                ),
                # 评分
                ft.Row(
                    controls=[
                        ft.Text("Rating:", size=14, weight=ft.FontWeight.W_500),
                        _build_rating_row(detail.rating),
                    ],
                    spacing=8,
                ),
                # 笔记
                ft.Column(
                    controls=[
                        note_field,
                        ft.FilledButton("Save Note", on_click=_save_note),
                    ],
                    spacing=4,
                ),
                # 标签
                ft.Column(
                    controls=[
                        tags_field,
                        ft.FilledButton("Save Tags", on_click=_save_tags),
                    ],
                    spacing=4,
                ),
                # 底部留白
                ft.Container(height=32),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _build_metadata_section(detail: PaperDetailDTO, fmt_date) -> ft.Column:
        """构建元数据信息区块。"""
        fields = [
            ("Published", fmt_date(detail.published_at)),
            ("Updated", fmt_date(detail.updated_at)),
            ("Version", f"v{detail.latest_version}"),
            ("DOI", detail.doi or "N/A"),
            ("Journal Reference", detail.journal_ref or "N/A"),
            ("Comment", detail.comment or "N/A"),
        ]

        return ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(f"{label}:", size=13, weight=ft.FontWeight.W_500, width=140),
                        ft.Text(value, size=13, color=ft.Colors.GREY_700),
                    ],
                    spacing=4,
                )
                for label, value in fields
            ],
            spacing=6,
        )

    def _on_delete_paper(e: ft.ControlEvent) -> None:
        def _confirm_delete(ce: ft.ControlEvent) -> None:
            page.pop_dialog()
            try:
                msg = ctx.paper_library_service.delete_paper(arxiv_id)
                show_notification(page, msg)
                page.go("/dashboard")
            except Exception as exc:
                show_notification(page, f"Error: {exc}", is_error=True)

        confirm_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete Paper"),
            content=ft.Text(
                "Are you sure you want to permanently delete this paper?\n\n"
                "This will remove all local data including status, "
                "notes, tags, and version history."
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

    # -- 初始加载 --
    _load_detail()

    return ft.View(
        route=f"/paper/{arxiv_id}",
        controls=[content_area],
        appbar=ft.AppBar(
            title=ft.Text("Paper Detail"),
            leading=ft.IconButton(
                icon=ft.Icons.ARROW_BACK,
                tooltip="Back to Dashboard",
                on_click=lambda e: page.go("/dashboard"),
            ),
            actions=[
                ft.IconButton(
                    icon=ft.Icons.DELETE,
                    tooltip="Delete paper",
                    icon_color=ft.Colors.RED_400,
                    on_click=_on_delete_paper,
                ),
            ],
        ),
        scroll=ft.ScrollMode.AUTO,
    )


# ---------------------------------------------------------------------------
# 空态 / 错误态 辅助
# ---------------------------------------------------------------------------


def _build_not_found_view() -> ft.Column:
    """论文不存在时的占位视图。"""
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.SEARCH_OFF, size=48, color=ft.Colors.GREY_400),
            ft.Text(
                "Paper not found.",
                size=14,
                color=ft.Colors.GREY_500,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                "The paper may have been removed or the ID is incorrect.",
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
