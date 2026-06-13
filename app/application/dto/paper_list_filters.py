from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_datetime,
    _normalize_optional_text,
)


class PaperListFilters(BaseModel):
    """论文列表查询的过滤与排序参数对象。

    所有字段均可选；未设置的字段不参与过滤，返回全部论文。

    Args:
        category: 按主分类精确匹配（自动 trim 前后空格，空串 → None）
        keyword: 对标题和摘要做大小写不敏感包含搜索（MVP 阶段）（自动 trim）
        is_starred: 按收藏状态过滤
        is_read: 按阅读状态过滤
        is_hidden: 按隐藏状态过滤（默认不过滤，由调用方显式指定）
        author: 按作者名称做大小写不敏感子串匹配（自动 trim）
        published_from: 发布日期下界（含）； naive datetime 统一按 UTC 处理
        published_to: 发布日期上界（含）； naive datetime 统一按 UTC 处理
        updated_from: 更新日期下界（含）； naive datetime 统一按 UTC 处理
        updated_to: 更新日期上界（含）； naive datetime 统一按 UTC 处理
        limit: 返回记录上限
    """
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    keyword: str | None = None
    is_starred: bool | None = None
    is_read: bool | None = None
    is_hidden: bool | None = None
    author: str | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None
    limit: int | None = Field(default=None, ge=1)

    # -- text normalization -------------------------------------------------

    @field_validator("category", "keyword", "author", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None) -> str | None:
        """自动 trim 前后空格；空串统一收拢为 ``None``"""
        return _normalize_optional_text(value, field_name="filter text")

    # -- datetime normalization --------------------------------------------

    @field_validator(
        "published_from", "published_to",
        "updated_from", "updated_to",
        mode="before",
    )
    @classmethod
    def normalize_datetime_fields(cls, value: Any) -> datetime | None:
        """将 naive datetime 强制按 UTC 处理，aware datetime 统一转 UTC。

        ``None`` 直接透传（表示该维度不过滤）。
        """
        if value is None:
            return None
        return _normalize_datetime(value)

    # -- cross-field validation ---------------------------------------------

    @model_validator(mode="after")
    def validate_date_ranges(self) -> "PaperListFilters":
        """确保日期区间语义合法：下界 ≤ 上界"""
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError(
                f"published_from ({self.published_from}) must be <= "
                f"published_to ({self.published_to})"
            )
        if (
            self.updated_from is not None
            and self.updated_to is not None
            and self.updated_from > self.updated_to
        ):
            raise ValueError(
                f"updated_from ({self.updated_from}) must be <= "
                f"updated_to ({self.updated_to})"
            )
        return self
