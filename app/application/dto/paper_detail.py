from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_arxiv_id,
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_string_list,
    _normalize_datetime
)


class PaperDetailDTO(BaseModel):
    """
    用于详情页展示的论文完整视图

    Args:
        arxiv_id (str): 论文 ID
        latest_version (int): 最新版本号
        ... (继承自 Paper 的元数据字段)
        is_starred (bool): 收藏状态
        is_read (bool): 阅读状态
        is_hidden (bool): 忽略状态
        rating (int | None): 用户评分
        note (str | None): 用户笔记
        tags (list[str]): 用户标签
    """
    model_config = ConfigDict(extra="forbid")
    arxiv_id: str
    latest_version: int = Field(ge=1)
    title: str
    abstract: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    pdf_url: str | None = None
    abs_url: str
    comment: str | None = None
    journal_ref: str | None = None
    doi: str | None = None
    is_starred: bool
    is_read: bool
    is_hidden: bool
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None
    tags: list[str]

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def validate_arxiv_id(cls, value: str) -> str:
        return _normalize_arxiv_id(value)

    @field_validator("title", "abstract", "primary_category", "abs_url", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="value")

    @field_validator("comment", "journal_ref", "doi", "pdf_url", "note", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="value")
    
    @field_validator("published_at", "updated_at", mode="before")
    @classmethod
    def normalize_dates(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("authors", mode="before")
    @classmethod
    def normalize_authors(cls, value: str | list[str]) -> list[str]:
        return _normalize_string_list(value, field_name="authors", require_non_empty=True)

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, value: str | list[str]) -> list[str]:
        return _normalize_string_list(value, field_name="categories", require_non_empty=True)

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: str | list[str] | None) -> list[str]:
        return _normalize_string_list(value, field_name="tags")

    @model_validator(mode="after")
    def validate_category_and_time_consistency(self) -> "PaperDetailDTO":
        if self.primary_category not in self.categories:
            raise ValueError("primary_category must be included in categories")
        if self.updated_at < self.published_at:
            raise ValueError("updated_at must be greater than or equal to published_at")
        return self
