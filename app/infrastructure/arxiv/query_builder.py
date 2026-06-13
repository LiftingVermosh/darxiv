from __future__ import annotations

import urllib.parse
from typing import Annotated

from pydantic import BaseModel, Field

from app.domain.models import Subscription

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARXIV_API_BASE_URL = "http://export.arxiv.org/api/query"

_SORT_BY_OPTIONS = ("relevance", "lastUpdatedDate", "submittedDate")
_SORT_ORDER_OPTIONS = ("ascending", "descending")

# ---------------------------------------------------------------------------
# Query input model
# ---------------------------------------------------------------------------


class QueryInput(BaseModel):
    """构造 arXiv API 查询的输入参数

    Args:
        subscription: 订阅规则，定义要追踪的分类、关键词和作者
        start: 结果偏移量（0-based）
        max_results: 单页返回的最大结果数
        sort_by: 排序字段（relevance / lastUpdatedDate / submittedDate）
        sort_order: 排序方向（ascending / descending）
    """

    subscription: Subscription
    start: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)
    sort_by: str = "submittedDate"
    sort_order: str = "descending"

    def model_post_init(self, _context) -> None:
        if self.sort_by not in _SORT_BY_OPTIONS:
            raise ValueError(
                f"sort_by must be one of {_SORT_BY_OPTIONS}, got {self.sort_by!r}"
            )
        if self.sort_order not in _SORT_ORDER_OPTIONS:
            raise ValueError(
                f"sort_order must be one of {_SORT_ORDER_OPTIONS}, got {self.sort_order!r}"
            )


# ---------------------------------------------------------------------------
# Query builder helpers
# ---------------------------------------------------------------------------


def _join_or(field_prefix: str, values: list[str], *, quote: bool = False) -> str | None:
    """将值列表用 OR 连接并加上字段前缀

    Args:
        field_prefix: arXiv 查询前缀（如 ``cat:``、``all:``）
        values: 要连接的字符串列表
        quote: 若为 True，每个值用双引号包裹
    """
    if not values:
        return None
    if quote:
        terms = [f'{field_prefix}"{v}"' for v in values]
    else:
        terms = [f"{field_prefix}{v}" for v in values]
    if len(terms) == 1:
        return terms[0]
    return f"({' OR '.join(terms)})"


def _build_search_query(subscription: Subscription) -> str:
    """从订阅规则构建 arXiv API search_query 参数"""
    positive: list[str] = []
    negative: list[str] = []

    # categories → cat: prefix, OR'd
    cat_group = _join_or("cat:", subscription.categories)
    if cat_group:
        positive.append(cat_group)

    # include_keywords → all: prefix, OR'd, always quoted so multi-word
    # phrases stay as single terms
    kw_group = _join_or("all:", subscription.include_keywords, quote=True)
    if kw_group:
        positive.append(kw_group)

    # authors → au: prefix, quoted, OR'd
    au_group = _join_or("au:", subscription.authors, quote=True)
    if au_group:
        positive.append(au_group)

    # exclude_keywords → ANDNOT all:"...", each individually negated
    for kw in subscription.exclude_keywords:
        negative.append(f'all:"{kw}"')

    if not positive and not negative:
        return ""

    # Reject queries that consist only of exclusions — ANDNOT needs
    # at least one positive term to negate against.
    if not positive:
        raise ValueError(
            "Cannot build a query with only exclude_keywords. "
            "Add at least one category, include_keyword, or author, "
            "or provide a raw query_text."
        )

    # Build: positive groups ANDed together first, then each negative
    # term appended with ANDNOT (not AND).
    parts: list[str] = []
    if positive:
        parts.append(" AND ".join(positive))
    for term in negative:
        parts.append(f"ANDNOT {term}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_query(query_input: QueryInput) -> tuple[str, str]:
    """构建 arXiv API 查询 URL

    Args:
        query_input: 查询输入参数

    Returns:
        (query_display, request_url) —— query_display 是人类可读的查询字符串，
        request_url 是完整的 arXiv API 请求地址

    Raises:
        ValueError: 当订阅未生成任何查询子句，且未提供 query_text 时
    """
    sub = query_input.subscription

    if sub.query_text:
        search_query = sub.query_text
    else:
        search_query = _build_search_query(sub)
        if not search_query:
            raise ValueError(
                f"Subscription {sub.id!r} has no categories, keywords, or authors "
                f"to build a query from. Provide at least one filter or set query_text."
            )

    params = {
        "search_query": search_query,
        "start": str(query_input.start),
        "max_results": str(query_input.max_results),
        "sortBy": query_input.sort_by,
        "sortOrder": query_input.sort_order,
    }
    query_string = urllib.parse.urlencode(params)
    request_url = f"{ARXIV_API_BASE_URL}?{query_string}"

    return search_query, request_url
