from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def get_connection(
    db_path: str | None = None,
    *,
    auto_init: bool = True,
) -> sqlite3.Connection:
    """创建并返回一个已启用 Row 工厂与外键约束的 sqlite3 连接

    当 *auto_init* 为 ``True``（默认值）时，如果数据库 schema 尚不存在，
    则会自动对其进行初始化。

    Args:
        db_path: SQLite 数据库文件的路径。当传入 ``None`` 时，路径将从
            ``PAPER_RESEARCH_DB_PATH`` 环境变量读取，若未设置则回退到项目
            根目录下的 ``paper_research.db``。
        auto_init: 为 ``True`` 时，对新建立的连接执行
            :func:`~app.infrastructure.db.migrations.initialize_database`。

    Returns:
        配置了 ``sqlite3.Row`` 的 :class:`sqlite3.Connection`，结果行可通过
        列名访问。
    """
    if db_path is None:
        db_path = os.environ.get("PAPER_RESEARCH_DB_PATH", "paper_research.db")

    # Ensure the parent directory exists for file-based databases.
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    if auto_init:
        from app.infrastructure.db.migrations import initialize_database

        try:
            initialize_database(connection)
        except Exception:
            connection.close()
            raise

    return connection
