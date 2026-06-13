from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.infrastructure.db.repositories.paper_repository import _utcnow


_GET_SQL = "SELECT value_json FROM app_settings WHERE key = ?"

_SET_SQL = """
    INSERT INTO app_settings (key, value_json, updated_at)
    VALUES (:key, :value_json, :updated_at)
    ON CONFLICT(key) DO UPDATE SET
        value_json = excluded.value_json,
        updated_at = excluded.updated_at
"""

_DELETE_SQL = "DELETE FROM app_settings WHERE key = ?"


class AppSettingsRepository:
    """
    ``app_settings`` 键值表的仓储层

    所有值在内部自动进行 JSON 序列化与反序列化，
    调用方无需直接处理原始 JSON 字符串。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def get(self, key: str) -> Any | None:
        """返回 *key* 对应的反序列化值，若未找到则返回 ``None``"""
        row = self._conn.execute(_GET_SQL, (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row["value_json"])

    def set(self, key: str, value: Any) -> None:
        """
        将 *value* 序列化为 JSON 后存储到 *key*

        当 *value* 无法被 JSON 序列化时，抛出 :exc:`TypeError`。
        """
        value_json = json.dumps(value, ensure_ascii=False)
        self._conn.execute(
            _SET_SQL,
            {"key": key, "value_json": value_json, "updated_at": _utcnow()},
        )

    def delete(self, key: str) -> None:
        """
        移除由 *key* 标识的设置项

        调用方负责提交事务。
        """
        self._conn.execute(_DELETE_SQL, (key,))
