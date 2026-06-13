from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.domain.models import Paper


def _row_to_paper(row: sqlite3.Row) -> Paper:
    """将 ``papers`` 表的行转换为 :class:`Paper` 领域模型"""
    return Paper(
        arxiv_id=row["arxiv_id"],
        version=row["latest_version"],
        title=row["title"],
        abstract=row["abstract"],
        authors=json.loads(row["authors_json"]),
        primary_category=row["primary_category"],
        categories=json.loads(row["categories_json"]),
        published_at=_parse_dt(row["published_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        pdf_url=row["pdf_url"],
        abs_url=row["abs_url"],
        comment=row["comment"],
        journal_ref=row["journal_ref"],
        doi=row["doi"],
    )


def _paper_to_params(paper: Paper, *, created_at: str, synced_at: str) -> dict[str, Any]:
    """将 :class:`Paper` 转换为用于 SQL 插入的参数字典"""
    return {
        "arxiv_id": paper.arxiv_id,
        "latest_version": paper.version,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors_json": json.dumps(paper.authors, ensure_ascii=False),
        "primary_category": paper.primary_category,
        "categories_json": json.dumps(paper.categories, ensure_ascii=False),
        "published_at": _format_dt(paper.published_at),
        "updated_at": _format_dt(paper.updated_at),
        "pdf_url": paper.pdf_url,
        "abs_url": paper.abs_url,
        "comment": paper.comment,
        "journal_ref": paper.journal_ref,
        "doi": paper.doi,
        "created_at": created_at,
        "synced_at": synced_at,
    }


# ---------------------------------------------------------------------------
# Shared datetime helpers (used across all repositories)
# ---------------------------------------------------------------------------

def _format_dt(dt: datetime) -> str:
    """将 datetime 序列化为 UTC ISO 8601 字符串"""
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    """将 UTC ISO 8601 字符串解析回带时区信息的 datetime"""
    return datetime.fromisoformat(value)


def _utcnow() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_INSERT_PAPER_SQL = """
    INSERT INTO papers (
        arxiv_id, latest_version, title, abstract, authors_json,
        primary_category, categories_json, published_at, updated_at,
        pdf_url, abs_url, comment, journal_ref, doi,
        created_at, synced_at
    ) VALUES (
        :arxiv_id, :latest_version, :title, :abstract, :authors_json,
        :primary_category, :categories_json, :published_at, :updated_at,
        :pdf_url, :abs_url, :comment, :journal_ref, :doi,
        :created_at, :synced_at
    )
    ON CONFLICT(arxiv_id) DO UPDATE SET
        latest_version = excluded.latest_version,
        title = excluded.title,
        abstract = excluded.abstract,
        authors_json = excluded.authors_json,
        primary_category = excluded.primary_category,
        categories_json = excluded.categories_json,
        published_at = excluded.published_at,
        updated_at = excluded.updated_at,
        pdf_url = excluded.pdf_url,
        abs_url = excluded.abs_url,
        comment = excluded.comment,
        journal_ref = excluded.journal_ref,
        doi = excluded.doi,
        synced_at = excluded.synced_at
"""

_INSERT_VERSION_SQL = """
    INSERT OR IGNORE INTO paper_versions (
        arxiv_id, version, title, abstract, updated_at, raw_payload_json
    ) VALUES (
        :arxiv_id, :version, :title, :abstract, :updated_at, :raw_payload_json
    )
"""

_GET_SQL = """
    SELECT * FROM papers WHERE arxiv_id = ?
"""

_LIST_SQL = """
    SELECT * FROM papers ORDER BY updated_at DESC
"""

_LIST_BY_CATEGORY_SQL = """
    SELECT * FROM papers WHERE primary_category = ? ORDER BY updated_at DESC
"""


class PaperRepository:
    """``papers`` 表与 ``paper_versions`` 表的仓储层"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # -- papers -------------------------------------------------------------

    def upsert(self, paper: Paper) -> None:
        """插入 *paper*，如果已存在则替换当前快照

        已有行的 ``created_at`` 时间戳在更新时会被保留；
        ``synced_at`` 始终设为当前 UTC 时间。

        调用方负责提交事务。
        """
        now = _utcnow()
        params = _paper_to_params(paper, created_at=now, synced_at=now)
        self._conn.execute(_INSERT_PAPER_SQL, params)

    def get(self, arxiv_id: str) -> Paper | None:
        """返回由 *arxiv_id* 标识的论文，若未找到则返回 ``None``"""
        row = self._conn.execute(_GET_SQL, (arxiv_id,)).fetchone()
        if row is None:
            return None
        return _row_to_paper(row)

    def list_all(self) -> list[Paper]:
        """返回所有论文，按最近更新时间降序排列"""
        rows = self._conn.execute(_LIST_SQL).fetchall()
        return [_row_to_paper(r) for r in rows]

    def list_by_category(self, category: str) -> list[Paper]:
        """返回主分类等于 *category* 的论文列表"""
        rows = self._conn.execute(
            _LIST_BY_CATEGORY_SQL, (category,)
        ).fetchall()
        return [_row_to_paper(r) for r in rows]

    # -- paper_versions -----------------------------------------------------

    def upsert_version(
        self, paper: Paper, raw_payload_json: str | None
    ) -> None:
        """记录一个版本快照，若已存在则静默忽略

        调用方负责提交事务。
        """
        self._conn.execute(
            _INSERT_VERSION_SQL,
            {
                "arxiv_id": paper.arxiv_id,
                "version": paper.version,
                "title": paper.title,
                "abstract": paper.abstract,
                "updated_at": _format_dt(paper.updated_at),
                "raw_payload_json": raw_payload_json,
            },
        )
