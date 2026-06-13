from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.domain.models import Subscription
from app.infrastructure.db.repositories.paper_repository import (
    _utcnow,
)


def _row_to_subscription(row: sqlite3.Row) -> Subscription:
    """将 ``subscriptions`` 表的行转换为 :class:`Subscription` 领域模型"""
    return Subscription(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        categories=json.loads(row["categories_json"]),
        include_keywords=json.loads(row["include_keywords_json"]),
        exclude_keywords=json.loads(row["exclude_keywords_json"]),
        authors=json.loads(row["authors_json"]),
        query_text=row["query_text"],
        sync_interval_minutes=row["sync_interval_minutes"],
    )


def _subscription_to_params(
    subscription: Subscription,
    *,
    created_at: str,
    updated_at: str,
    last_synced_at: str | None,
) -> dict[str, Any]:
    """将 :class:`Subscription` 转换为用于 SQL 的参数字典"""
    return {
        "id": subscription.id,
        "name": subscription.name,
        "enabled": int(subscription.enabled),
        "categories_json": json.dumps(subscription.categories, ensure_ascii=False),
        "include_keywords_json": json.dumps(
            subscription.include_keywords, ensure_ascii=False
        ),
        "exclude_keywords_json": json.dumps(
            subscription.exclude_keywords, ensure_ascii=False
        ),
        "authors_json": json.dumps(subscription.authors, ensure_ascii=False),
        "query_text": subscription.query_text,
        "sync_interval_minutes": subscription.sync_interval_minutes,
        "created_at": created_at,
        "updated_at": updated_at,
        "last_synced_at": last_synced_at,
    }


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

_SAVE_SQL = """
    INSERT INTO subscriptions (
        id, name, enabled,
        categories_json, include_keywords_json, exclude_keywords_json,
        authors_json, query_text, sync_interval_minutes,
        created_at, updated_at, last_synced_at
    ) VALUES (
        :id, :name, :enabled,
        :categories_json, :include_keywords_json, :exclude_keywords_json,
        :authors_json, :query_text, :sync_interval_minutes,
        :created_at, :updated_at, :last_synced_at
    )
    ON CONFLICT(id) DO UPDATE SET
        name = excluded.name,
        enabled = excluded.enabled,
        categories_json = excluded.categories_json,
        include_keywords_json = excluded.include_keywords_json,
        exclude_keywords_json = excluded.exclude_keywords_json,
        authors_json = excluded.authors_json,
        query_text = excluded.query_text,
        sync_interval_minutes = excluded.sync_interval_minutes,
        updated_at = excluded.updated_at
"""

_GET_SQL = "SELECT * FROM subscriptions WHERE id = ?"

_LIST_ALL_SQL = "SELECT * FROM subscriptions ORDER BY name"

_LIST_ENABLED_SQL = (
    "SELECT * FROM subscriptions WHERE enabled = 1 ORDER BY name"
)

_DELETE_SQL = "DELETE FROM subscriptions WHERE id = ?"


class SubscriptionRepository:
    """``subscriptions`` 表的仓储层"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def save(self, subscription: Subscription) -> None:
        """插入或更新 *subscription*

        ``created_at`` 和 ``last_synced_at`` 从已有行中保留；
        ``updated_at`` 始终设为当前 UTC 时间。
        """
        now = _utcnow()
        params = _subscription_to_params(
            subscription,
            created_at=now,
            updated_at=now,
            last_synced_at=None,
        )
        self._conn.execute(_SAVE_SQL, params)

    def get(self, subscription_id: str) -> Subscription | None:
        """返回具有指定 *subscription_id* 的订阅，若未找到则返回 ``None``"""
        row = self._conn.execute(_GET_SQL, (subscription_id,)).fetchone()
        if row is None:
            return None
        return _row_to_subscription(row)

    def list_all(self) -> list[Subscription]:
        """返回所有订阅，按名称排序"""
        rows = self._conn.execute(_LIST_ALL_SQL).fetchall()
        return [_row_to_subscription(r) for r in rows]

    def list_enabled(self) -> list[Subscription]:
        """仅返回已启用的订阅"""
        rows = self._conn.execute(_LIST_ENABLED_SQL).fetchall()
        return [_row_to_subscription(r) for r in rows]

    def delete(self, subscription_id: str) -> None:
        """移除由 *subscription_id* 标识的订阅

        调用方负责提交事务。
        """
        self._conn.execute(_DELETE_SQL, (subscription_id,))
