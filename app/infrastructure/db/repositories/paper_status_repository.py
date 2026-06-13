from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.domain.models import PaperStatus
from app.infrastructure.db.repositories.paper_repository import (
    _format_dt,
    _parse_dt,
    _utcnow,
)


def _row_to_status(row: sqlite3.Row) -> PaperStatus:
    """将 ``paper_statuses`` 表的行转换为 :class:`PaperStatus` 领域模型"""
    return PaperStatus(
        arxiv_id=row["arxiv_id"],
        is_starred=bool(row["is_starred"]),
        is_read=bool(row["is_read"]),
        is_hidden=bool(row["is_hidden"]),
        rating=row["rating"],
        note=row["note"],
        tags=json.loads(row["tags_json"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _status_to_params(status: PaperStatus) -> dict[str, Any]:
    """将 :class:`PaperStatus` 转换为用于 SQL 的参数字典"""
    return {
        "arxiv_id": status.arxiv_id,
        "is_starred": int(status.is_starred),
        "is_read": int(status.is_read),
        "is_hidden": int(status.is_hidden),
        "rating": status.rating,
        "note": status.note,
        "tags_json": json.dumps(status.tags, ensure_ascii=False),
        "updated_at": _format_dt(status.updated_at),
    }


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
    INSERT OR REPLACE INTO paper_statuses (
        arxiv_id, is_starred, is_read, is_hidden,
        rating, note, tags_json, updated_at
    ) VALUES (
        :arxiv_id, :is_starred, :is_read, :is_hidden,
        :rating, :note, :tags_json, :updated_at
    )
"""

_GET_SQL = "SELECT * FROM paper_statuses WHERE arxiv_id = ?"

_LIST_STARRED_SQL = """
    SELECT * FROM paper_statuses
    WHERE is_starred = 1 AND is_hidden = 0
    ORDER BY updated_at DESC
"""


class PaperStatusRepository:
    """``paper_statuses`` 表的仓储层"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def upsert(self, status: PaperStatus) -> None:
        """插入或更新论文的用户状态

        调用方负责提交事务。
        """
        self._conn.execute(_UPSERT_SQL, _status_to_params(status))

    def get(self, arxiv_id: str) -> PaperStatus | None:
        """返回 *arxiv_id* 对应用户状态，若未找到则返回 ``None``"""
        row = self._conn.execute(_GET_SQL, (arxiv_id,)).fetchone()
        if row is None:
            return None
        return _row_to_status(row)

    def list_starred(self) -> list[PaperStatus]:
        """返回所有已收藏且未被隐藏的状态记录"""
        rows = self._conn.execute(_LIST_STARRED_SQL).fetchall()
        return [_row_to_status(r) for r in rows]
