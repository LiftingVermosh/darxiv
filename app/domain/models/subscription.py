from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models._normalization import (    
    _normalize_optional_text, 
    _normalize_required_text
)

def _normalize_list(values: str | list[str] | None, *, lowercase: bool) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        items = [values]
    elif isinstance(values, list):
        items = values
    else:
        raise ValueError("value must be a string or a list of strings")

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("list items must be strings")
        cleaned = item.strip()
        if not cleaned:
            continue
        val = cleaned.lower() if lowercase else cleaned
        if val and val not in seen:
            seen.add(val)
            result.append(val)
    return result


class Subscription(BaseModel):
    """
    用户定义的论文订阅规则，用于决定自动追踪哪些方向的论文

    Args:
        id (str): 订阅规则的唯一标识符
        name (str): 订阅的显示名称（如 "计算机视觉最新追踪"）
        enabled (bool): 是否启用该订阅
        categories (list[str]): 关注的 arXiv 分类白名单
        include_keywords (list[str]): 必须包含的关键词（标题或摘要匹配，不区分大小写）
        exclude_keywords (list[str]): 需排除的关键词（标题或摘要匹配，不区分大小写）
        authors (list[str]): 关注的特定作者列表
        query_text (str | None): 预留字段，用于存储复杂的原始 API 查询字符串
        sync_interval_minutes (int): 同步频率限制（分钟计，默认 1440 为每天一次）
    """
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    enabled: bool = True
    categories: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    query_text: str | None = None
    sync_interval_minutes: int = Field(default=1440, gt=0)

    @field_validator("id", "name", mode="before")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="value")

    @field_validator("query_text", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
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
