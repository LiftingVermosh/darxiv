from __future__ import annotations

import re
from typing import Any

import feedparser
from dateutil.parser import parse as parse_date
from pydantic import BaseModel, Field, ValidationError

from app.domain.models import Paper
from app.infrastructure.arxiv.exceptions import (
    ArxivDataContractError,
    ArxivParseError,
)

# ---------------------------------------------------------------------------
# ID extraction
# ---------------------------------------------------------------------------

_ARXIV_ID_FROM_URL = re.compile(r"arxiv\.org/abs/(?P<id>[^\s]+)")
_VERSION_PATTERN = re.compile(r"v(\d+)$")


def _extract_arxiv_id(raw_id: str) -> str:
    """从原始 id 字符串中提取 arXiv 论文标识

    支持 URL 格式（如 ``http://arxiv.org/abs/2501.01234v2``）和裸 ID 格式
    （如 ``2501.01234v2``），返回去除版本后缀的裸 ID。
    """
    match = _ARXIV_ID_FROM_URL.search(raw_id)
    if match:
        return match.group("id")
    # 如果不是 URL，直接返回原始字符串（Paper 的 validator 会进一步处理）
    return raw_id


def _extract_version(raw_id: str) -> int:
    """从原始 arXiv id 字符串中提取版本号，默认返回 1"""
    match = _VERSION_PATTERN.search(raw_id)
    if match:
        return int(match.group(1))
    return 1


# ---------------------------------------------------------------------------
# PDF link extraction
# ---------------------------------------------------------------------------


def _find_pdf_url(entry: dict[str, Any]) -> str | None:
    """从 feedparser 条目的 links 列表中查找 PDF 链接"""
    for link in entry.get("links", []):
        # arXiv PDF links typically have title="pdf" or type="application/pdf"
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            return link.get("href")
    return None


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class ArxivFetchResult(BaseModel):
    """arXiv 查询的解析结果

    Args:
        papers: 从 feed 中解析出的论文列表
        raw_entries_count: feed 中的原始条目数
        query: 本次查询使用的 search_query 字符串
    """

    papers: list[Paper]
    raw_entries_count: int = Field(ge=0)
    query: str


# ---------------------------------------------------------------------------
# Entry → Paper 转换
# ---------------------------------------------------------------------------


def _entry_to_paper(entry: dict[str, Any]) -> Paper:
    """将单个 feedparser 条目转换为 Paper 领域模型

    Args:
        entry: feedparser 解析后的单条 entry 字典

    Returns:
        经过完整校验的 Paper 实例

    Raises:
        ArxivDataContractError: 必要字段缺失或校验失败
    """
    raw_id = entry.get("id", "")

    # -- arxiv_id (extracted from URL) & version ---------------------------
    arxiv_id = _extract_arxiv_id(raw_id)
    version = _extract_version(raw_id)

    # -- authors ----------------------------------------------------------
    authors: list[str] = []
    for author in entry.get("authors", []):
        name = author.get("name", "").strip()
        if name:
            authors.append(name)

    # -- categories -------------------------------------------------------
    categories: list[str] = []
    for tag in entry.get("tags", []):
        term = tag.get("term", "").strip()
        if term:
            categories.append(term)

    # -- primary_category -------------------------------------------------
    primary_category = ""
    arxiv_primary = entry.get("arxiv_primary_category", {})
    if isinstance(arxiv_primary, dict):
        primary_category = arxiv_primary.get("term", "").strip()

    # -- dates ------------------------------------------------------------
    published_str = entry.get("published", "")
    updated_str = entry.get("updated", "")

    try:
        published_at = parse_date(published_str) if published_str else None
    except (ValueError, OverflowError) as exc:
        raise ArxivDataContractError(
            f"Invalid published date {published_str!r} for entry {raw_id!r}",
            cause=exc,
        ) from exc

    try:
        updated_at = parse_date(updated_str) if updated_str else None
    except (ValueError, OverflowError) as exc:
        raise ArxivDataContractError(
            f"Invalid updated date {updated_str!r} for entry {raw_id!r}",
            cause=exc,
        ) from exc

    # -- pdf_url ----------------------------------------------------------
    pdf_url = _find_pdf_url(entry)

    # -- optional fields --------------------------------------------------
    comment = entry.get("arxiv_comment", "").strip() or None
    journal_ref = entry.get("arxiv_journal_ref", "").strip() or None
    doi = entry.get("arxiv_doi", "").strip() or None

    # -- title / abstract -------------------------------------------------
    title = entry.get("title", "").strip()
    abstract = entry.get("summary", "").strip()

    if not raw_id:
        raise ArxivDataContractError("Entry is missing required field: id")
    if not title:
        raise ArxivDataContractError("Entry is missing required field: title")

    try:
        return Paper(
            arxiv_id=arxiv_id,
            version=version,
            title=title,
            abstract=abstract,
            authors=authors,
            primary_category=primary_category,
            categories=categories,
            published_at=published_at,
            updated_at=updated_at,
            pdf_url=pdf_url,
            abs_url=raw_id,
            comment=comment,
            journal_ref=journal_ref,
            doi=doi,
        )
    except ValidationError as exc:
        raise ArxivDataContractError(
            f"Paper validation failed for entry {raw_id!r}",
            cause=exc,
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_feed(raw_xml: str, query: str) -> ArxivFetchResult:
    """将 arXiv API 返回的 Atom XML 解析为内部模型

    Args:
        raw_xml: Atom feed 的原始 XML 文本
        query: 本次请求使用的 arXiv search_query（用于记录与排错）

    Returns:
        包含 Paper 列表的 ArxivFetchResult

    Raises:
        ArxivParseError: XML 解析失败或 feed 结构异常
        ArxivDataContractError: 条目字段无法通过校验
    """
    feed = feedparser.parse(raw_xml)

    if feed.bozo and not feed.entries:
        exc = feed.bozo_exception
        raise ArxivParseError(
            f"Failed to parse arXiv Atom feed: {exc}",
            cause=exc,
        ) from exc

    papers: list[Paper] = []
    for entry in feed.entries:
        paper = _entry_to_paper(entry)
        papers.append(paper)

    return ArxivFetchResult(
        papers=papers,
        raw_entries_count=len(feed.entries),
        query=query,
    )
