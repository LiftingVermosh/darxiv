from __future__ import annotations

import sqlite3
from typing import Any

from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import SyncRun
from app.infrastructure.db.repositories.paper_repository import (
    _format_dt,
    _parse_dt,
)


def _row_to_sync_run(row: sqlite3.Row) -> SyncRun:
    """将 ``sync_runs`` 表的行转换为 :class:`SyncRun` 领域模型"""
    return SyncRun(
        id=row["id"],
        subscription_id=row["subscription_id"],
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]) if row["finished_at"] else None,
        status=SyncRunStatus(row["status"]),
        trigger_type=SyncTriggerType(row["trigger_type"]),
        fetched_count=row["fetched_count"],
        inserted_count=row["inserted_count"],
        updated_count=row["updated_count"],
        error_message=row["error_message"],
    )


def _sync_run_to_params(sync_run: SyncRun) -> dict[str, Any]:
    """将 :class:`SyncRun` 转换为用于 SQL 的参数字典"""
    return {
        "id": sync_run.id,
        "subscription_id": sync_run.subscription_id,
        "trigger_type": sync_run.trigger_type.value,
        "started_at": _format_dt(sync_run.started_at),
        "finished_at": (
            _format_dt(sync_run.finished_at)
            if sync_run.finished_at
            else None
        ),
        "status": sync_run.status.value,
        "fetched_count": sync_run.fetched_count,
        "inserted_count": sync_run.inserted_count,
        "updated_count": sync_run.updated_count,
        "error_message": sync_run.error_message,
    }


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_INSERT_SQL = """
    INSERT OR REPLACE INTO sync_runs (
        id, subscription_id, trigger_type, started_at, finished_at,
        status, fetched_count, inserted_count, updated_count, error_message
    ) VALUES (
        :id, :subscription_id, :trigger_type, :started_at, :finished_at,
        :status, :fetched_count, :inserted_count, :updated_count, :error_message
    )
"""

_GET_SQL = "SELECT * FROM sync_runs WHERE id = ?"

_LIST_BY_SUB_SQL = """
    SELECT * FROM sync_runs
    WHERE subscription_id = ?
    ORDER BY started_at DESC
"""


class SyncRunRepository:
    """``sync_runs`` 表的仓储层"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def insert(self, sync_run: SyncRun) -> None:
        """插入（或完全替换）一条同步运行记录

        调用方负责提交事务。
        """
        self._conn.execute(_INSERT_SQL, _sync_run_to_params(sync_run))

    def update(self, sync_run: SyncRun) -> None:
        """:meth:`insert` 的别名 —— 完全替换对应行"""
        self.insert(sync_run)

    def get(self, sync_run_id: str) -> SyncRun | None:
        """返回指定 *sync_run_id* 的同步运行记录，若未找到则返回 ``None``"""
        row = self._conn.execute(_GET_SQL, (sync_run_id,)).fetchone()
        if row is None:
            return None
        return _row_to_sync_run(row)

    def list_by_subscription(
        self, subscription_id: str
    ) -> list[SyncRun]:
        """返回指定 *subscription_id* 的所有同步运行记录，按开始时间降序排列"""
        rows = self._conn.execute(
            _LIST_BY_SUB_SQL, (subscription_id,)
        ).fetchall()
        return [_row_to_sync_run(r) for r in rows]
