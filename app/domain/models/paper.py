from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_arxiv_id,
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_string_list,
    _normalize_datetime,
)


class Paper(BaseModel):
    """
    arXiv 论文的客观元数据

    Args:
        arxiv_id (str): arXiv 论文的 ID
        version (int): 论文的版本号
        title (str): 论文的标题
        abstract (str): 论文的摘要
        authors (list[str]): 论文的作者列表
        primary_category (str): 论文的主要分类
        categories (list[str]): 论文的其他分类
        published_at (datetime): 论文的发布日期
        updated_at (datetime): 论文的更新日期
        pdf_url (str | None): 论文的 PDF 链接
        abs_url (str): 论文的摘要链接
        comment (str | None): 论文的评论
        journal_ref (str | None): 论文的期刊引用
        doi (str | None): 论文的 DOI
    """
    model_config = ConfigDict(extra="forbid")
    arxiv_id: str
    version: int = Field(ge=1)
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

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def normalize_arxiv_id(cls, value: str) -> str:
        return _normalize_arxiv_id(value)

    @field_validator("title", "abstract", "primary_category", "abs_url", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="value")

    @field_validator("comment", "journal_ref", "doi", "pdf_url", mode="before")
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

    @model_validator(mode="after")
    def validate_primary_category(self) -> "Paper":
        if self.primary_category not in self.categories:
            raise ValueError("primary_category must be included in categories")
        if self.updated_at < self.published_at:
            raise ValueError("updated_at must be greater than or equal to published_at")
        return self
    
