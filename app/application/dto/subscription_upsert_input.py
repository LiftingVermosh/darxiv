from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_optional_text,
    _normalize_required_text,
)
from app.domain.models.subscription import _normalize_list


class SubscriptionUpsertInput(BaseModel):
    """订阅创建/更新的输入 DTO

    将 UI 输入与领域模型解耦，由服务层负责将本 DTO 转换为
    :class:`~app.domain.models.Subscription` 领域对象。

    Args:
        name: 订阅显示名称（在库内需保持唯一）
        enabled: 是否启用
        categories: arXiv 分类白名单
        include_keywords: 必须包含的关键词
        exclude_keywords: 需排除的关键词
        authors: 关注的作者
        query_text: 复杂原始查询字符串（可选）
        sync_interval_minutes: 同步间隔（分钟），必须 > 0
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    query_text: str | None = None
    sync_interval_minutes: int = Field(default=1440, gt=0)

    # ------------------------------------------------------------------
    # Normalization validators
    # ------------------------------------------------------------------

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="name")

    @field_validator("query_text", mode="before")
    @classmethod
    def normalize_query_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="query_text")

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, value: str | list[str] | None) -> list[str]:
        return _normalize_list(value, lowercase=False)

    @field_validator("authors", mode="before")
    @classmethod
    def normalize_authors(cls, value: str | list[str] | None) -> list[str]:
        return _normalize_list(value, lowercase=False)

    @field_validator("include_keywords", "exclude_keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value: str | list[str] | None) -> list[str]:
        return _normalize_list(value, lowercase=True)

    # ------------------------------------------------------------------
    # Cross-field validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def validate_at_least_one_filter(self) -> "SubscriptionUpsertInput":
        """确保至少提供一类筛选条件"""
        if not self.categories and not self.include_keywords and not self.authors:
            raise ValueError(
                "At least one filter criterion is required: "
                "categories, include_keywords, or authors"
            )
        return self
