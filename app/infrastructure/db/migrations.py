from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


# ---------------------------------------------------------------------------
# Migration error
# ---------------------------------------------------------------------------


class MigrationError(Exception):
    """Raised when a targeted migration cannot be applied automatically."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initialize_database(
    connection: sqlite3.Connection,
    schema_path: str | None = None,
) -> None:
    """对 *connection* 执行 DDL schema 与定向迁移。

    schema 从 *schema_path* 读取（若为 ``None`` 则使用内置的 ``schema.sql``）。
    所有 DDL 语句均使用 ``IF NOT EXISTS``，因此重复调用是幂等的。

    在 schema DDL 之后会执行定向迁移步骤，以覆盖在旧版本 schema 上
    创建的数据库。每个迁移步骤都会先检查是否需要执行，避免重复。

    Args:
        connection: 一个已打开的 :class:`sqlite3.Connection`。
        schema_path: 可选的自定义 ``.sql`` 文件路径。

    Raises:
        MigrationError: 当迁移步骤因数据冲突无法自动完成时。
    """
    if schema_path is None:
        schema_path = str(_SCHEMA_PATH)

    sql = Path(schema_path).read_text(encoding="utf-8")
    connection.executescript(sql)

    # ------------------------------------------------------------------
    # Targeted migrations for databases created before schema additions.
    # Each step first checks whether it is needed, then checks for data
    # conflicts before applying.
    # ------------------------------------------------------------------
    _migrate_unique_subscription_name(connection)
    _migrate_subscription_papers(connection)
    _migrate_provenance_state(connection)

    connection.commit()


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------


def _migrate_unique_subscription_name(connection: sqlite3.Connection) -> None:
    """Ensure a UNIQUE constraint exists on ``subscriptions.name``.

    - New databases: ``schema.sql`` already declares ``UNIQUE(name)`` inline,
      which creates an autoindex.  Nothing to do.
    - Old databases without an existing unique constraint: a unique index is
      created, provided no duplicate names already exist.
    - Old databases with duplicate names: a :exc:`MigrationError` is raised
      listing the conflicting names so the user can resolve them manually.
    """
    # Check whether a non-primary-key unique index already covers the column.
    # (origin 'u' = inline UNIQUE constraint, origin 'c' = CREATE INDEX)
    existing = [
        r for r in connection.execute("PRAGMA index_list('subscriptions')").fetchall()
        if r["unique"] and r["origin"] != "pk"
    ]
    if existing:
        return  # already covered — nothing to migrate

    # Pre-check for duplicate names that would block index creation.
    dupes = connection.execute(
        "SELECT name, COUNT(*) AS cnt FROM subscriptions "
        "GROUP BY name HAVING cnt > 1"
    ).fetchall()
    if dupes:
        dup_details = ", ".join(
            f"'{r['name']}' ({r['cnt']} occurrences)" for r in dupes
        )
        raise MigrationError(
            "Cannot add UNIQUE constraint on subscriptions.name: "
            f"duplicate names exist — {dup_details}. "
            "Resolve duplicates manually before upgrading."
        )

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_name "
        "ON subscriptions(name)"
    )


def _migrate_subscription_papers(connection: sqlite3.Connection) -> None:
    """Ensure the ``subscription_papers`` table exists.

    - New databases: ``schema.sql`` already declares the table inline. Nothing to do.
    - Old databases: create the table if it does not already exist.
    """
    connection.execute(
        "CREATE TABLE IF NOT EXISTS subscription_papers ("
        "  subscription_id   TEXT NOT NULL"
        "                    REFERENCES subscriptions(id) ON DELETE CASCADE,"
        "  arxiv_id          TEXT NOT NULL"
        "                    REFERENCES papers(arxiv_id) ON DELETE CASCADE,"
        "  first_seen_at     TEXT NOT NULL,"
        "  last_seen_at      TEXT NOT NULL,"
        "  last_sync_run_id  TEXT,"
        "  PRIMARY KEY (subscription_id, arxiv_id)"
        ")"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscription_papers_arxiv_id "
        "ON subscription_papers (arxiv_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscription_papers_subscription_id "
        "ON subscription_papers (subscription_id)"
    )


def _migrate_provenance_state(connection: sqlite3.Connection) -> None:
    """Add ``provenance_state`` column and mark pre-existing papers as legacy.

    - New databases: ``schema.sql`` already declares the column with
      DEFAULT 'attributed'. Nothing to do.
    - Old databases: add the column if missing, then set all papers that
      already exist to ``'legacy_unattributed'`` so they are never incorrectly
      treated as orphans during subscription deletion.

    Papers brought in by new syncs will have their provenance corrected to
    ``'attributed'`` when a real ``subscription_papers`` link is written.
    """
    # Check if column already exists
    col = connection.execute(
        "PRAGMA table_info('papers')"
    ).fetchall()
    col_names = {r["name"] for r in col}
    if "provenance_state" in col_names:
        return

    connection.execute(
        "ALTER TABLE papers ADD COLUMN provenance_state "
        "TEXT NOT NULL DEFAULT 'attributed'"
    )

    # Mark all papers that pre-date this migration as legacy
    connection.execute(
        "UPDATE papers SET provenance_state = 'legacy_unattributed'"
    )
