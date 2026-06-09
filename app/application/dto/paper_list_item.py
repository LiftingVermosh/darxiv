from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_arxiv_id, 
    _normalize_datetime,
    _normalize_required_text, 
    _normalize_string_list
)


class PaperListItemDTO(BaseModel):
    """
    用于列表页（如 Dashboard）展示的论文摘要视图

    Args:
        arxiv_id (str): 论文 ID
        title (str): 论文标题
        authors_preview (str): 处理后的作者简短预览（如 "Author A, Author B et al."）
        primary_category (str): 主分类
        categories (list[str]): 分类列表（用于显示标签）
        published_at (datetime): 发布日期
        updated_at (datetime): 更新日期
        is_starred (bool): 用户收藏状态
        is_read (bool): 用户阅读状态
        is_hidden (bool): 用户忽略状态
    """
    model_config = ConfigDict(extra="forbid")
    arxiv_id: str
    title: str
    authors_preview: str
    primary_category: str
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    is_starred: bool
    is_read: bool
    is_hidden: bool

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def validate_arxiv_id(cls, value: str) -> str:
        return _normalize_arxiv_id(value)

    @field_validator("title", "authors_preview", "primary_category", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="value")

    @field_validator("published_at", "updated_at", mode="before")
    @classmethod
    def normalize_dates(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, value: str | list[str]) -> list[str]:
        return _normalize_string_list(value, field_name="categories", require_non_empty=True)

    @model_validator(mode="after")
    def validate_category_and_time_consistency(self) -> "PaperListItemDTO":
        if self.primary_category not in self.categories:
            raise ValueError("primary_category must be included in categories")
        if self.updated_at < self.published_at:
            raise ValueError("updated_at must be greater than or equal to published_at")
        return self
