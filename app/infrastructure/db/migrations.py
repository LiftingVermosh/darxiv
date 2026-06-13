from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def initialize_database(
    connection: sqlite3.Connection,
    schema_path: str | None = None,
) -> None:
    """对 *connection* 执行 DDL schema

    schema 从 *schema_path* 读取（若为 ``None`` 则使用内置的 ``schema.sql``）。
    所有语句均使用 ``IF NOT EXISTS``，因此重复调用是幂等的。

    Args:
        connection: 一个已打开的 :class:`sqlite3.Connection`。
        schema_path: 可选的自定义 ``.sql`` 文件路径。
    """
    if schema_path is None:
        schema_path = str(_SCHEMA_PATH)

    sql = Path(schema_path).read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.commit()
