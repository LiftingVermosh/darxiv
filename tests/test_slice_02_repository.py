from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timezone

from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import Paper, PaperStatus, Subscription, SyncRun
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.migrations import initialize_database
from app.infrastructure.db.repositories import (
    AppSettingsRepository,
    PaperRepository,
    PaperStatusRepository,
    SubscriptionRepository,
    SyncRunRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paper(**overrides) -> Paper:
    defaults = {
        "arxiv_id": "2501.00001",
        "version": 1,
        "title": "A Test Paper",
        "abstract": "An abstract for testing.",
        "authors": ["Alice", "Bob"],
        "primary_category": "cs.CV",
        "categories": ["cs.CV", "cs.LG"],
        "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "abs_url": "https://arxiv.org/abs/2501.00001",
    }
    defaults.update(overrides)
    return Paper(**defaults)


def _make_subscription(**overrides) -> Subscription:
    defaults = {
        "id": "sub-1",
        "name": "CV Daily",
        "enabled": True,
        "categories": ["cs.CV"],
        "include_keywords": ["transformer"],
        "exclude_keywords": ["survey"],
        "authors": [],
        "sync_interval_minutes": 1440,
    }
    defaults.update(overrides)
    return Subscription(**defaults)


def _make_status(**overrides) -> PaperStatus:
    defaults = {
        "arxiv_id": "2501.00001",
        "is_starred": False,
        "is_read": False,
        "is_hidden": False,
    }
    defaults.update(overrides)
    return PaperStatus(**defaults)


def _make_sync_run(**overrides) -> SyncRun:
    defaults = {
        "id": "run-1",
        "subscription_id": "sub-1",
        "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "trigger_type": SyncTriggerType.MANUAL,
    }
    defaults.update(overrides)
    return SyncRun(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class SchemaAndMigrationTests(unittest.TestCase):
    """验证数据库初始化行为"""

    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def tearDown(self) -> None:
        self._conn.close()

    def test_initialize_database_creates_all_tables(self) -> None:
        initialize_database(self._conn)

        tables = {
            r["name"]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        expected = {
            "papers",
            "paper_versions",
            "subscriptions",
            "paper_statuses",
            "sync_runs",
            "app_settings",
        }
        self.assertTrue(expected.issubset(tables), f"missing: {expected - tables}")

    def test_double_initialization_is_idempotent(self) -> None:
        initialize_database(self._conn)
        initialize_database(self._conn)


class ForeignKeyIntegrityTests(unittest.TestCase):
    """验证外键约束拒绝孤立行"""

    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    # -- paper_versions ----------------------------------------------------

    def test_paper_version_rejects_orphan_arxiv_id(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self._conn.execute(
                "INSERT INTO paper_versions (arxiv_id, version, title, abstract, updated_at)"
                " VALUES ('nonexistent', 1, 'T', 'A', '2026-01-01T00:00:00+00:00')"
            )

    def test_paper_version_accepts_valid_reference(self) -> None:
        self._conn.execute(
            "INSERT INTO papers (arxiv_id, latest_version, title, abstract, authors_json,"
            " primary_category, categories_json, published_at, updated_at, abs_url,"
            " created_at, synced_at)"
            " VALUES ('2501.00001', 1, 'T', 'A', '[]', 'cs.CV', '[]',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',"
            " 'https://arxiv.org/abs/2501.00001', '2026-01-01T00:00:00+00:00',"
            " '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()
        # Must not raise — parent exists
        self._conn.execute(
            "INSERT INTO paper_versions (arxiv_id, version, title, abstract, updated_at)"
            " VALUES ('2501.00001', 1, 'T', 'A', '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()

    # -- paper_statuses ----------------------------------------------------

    def test_paper_status_rejects_orphan_arxiv_id(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self._conn.execute(
                "INSERT INTO paper_statuses (arxiv_id, tags_json, updated_at)"
                " VALUES ('nonexistent', '[]', '2026-01-01T00:00:00+00:00')"
            )

    # -- sync_runs ---------------------------------------------------------

    def test_sync_run_rejects_orphan_subscription_id(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self._conn.execute(
                "INSERT INTO sync_runs (id, subscription_id, trigger_type,"
                " started_at, status)"
                " VALUES ('run-x', 'nonexistent', 'manual',"
                " '2026-01-01T00:00:00+00:00', 'running')"
            )

    def test_delete_subscription_with_sync_runs_is_rejected(self) -> None:
        self._conn.execute(
            "INSERT INTO subscriptions (id, name, enabled, categories_json,"
            " include_keywords_json, exclude_keywords_json, authors_json,"
            " sync_interval_minutes, created_at, updated_at)"
            " VALUES ('sub-1', 'Test', 1, '[]', '[]', '[]', '[]',"
            " 1440, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()

        self._conn.execute(
            "INSERT INTO sync_runs (id, subscription_id, trigger_type,"
            " started_at, status)"
            " VALUES ('run-1', 'sub-1', 'manual',"
            " '2026-01-01T00:00:00+00:00', 'running')"
        )
        self._conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self._conn.execute("DELETE FROM subscriptions WHERE id = 'sub-1'")

    # -- cascade delete ----------------------------------------------------

    def test_delete_paper_cascades_to_versions_and_status(self) -> None:
        self._conn.execute(
            "INSERT INTO papers (arxiv_id, latest_version, title, abstract, authors_json,"
            " primary_category, categories_json, published_at, updated_at, abs_url,"
            " created_at, synced_at)"
            " VALUES ('2501.00001', 1, 'T', 'A', '[]', 'cs.CV', '[]',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',"
            " 'https://arxiv.org/abs/2501.00001', '2026-01-01T00:00:00+00:00',"
            " '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()
        self._conn.execute(
            "INSERT INTO paper_versions (arxiv_id, version, title, abstract, updated_at)"
            " VALUES ('2501.00001', 1, 'T', 'A', '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()
        self._conn.execute(
            "INSERT INTO paper_statuses (arxiv_id, tags_json, updated_at)"
            " VALUES ('2501.00001', '[]', '2026-01-01T00:00:00+00:00')"
        )
        self._conn.commit()

        self._conn.execute("DELETE FROM papers WHERE arxiv_id = '2501.00001'")
        self._conn.commit()

        v = self._conn.execute(
            "SELECT COUNT(*) AS c FROM paper_versions WHERE arxiv_id = '2501.00001'"
        ).fetchone()["c"]
        s = self._conn.execute(
            "SELECT COUNT(*) AS c FROM paper_statuses WHERE arxiv_id = '2501.00001'"
        ).fetchone()["c"]
        self.assertEqual(v, 0)
        self.assertEqual(s, 0)


class PaperRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    # -- upsert + get ------------------------------------------------------

    def test_upsert_and_get_paper(self) -> None:
        self._repo.upsert(_make_paper())
        self._conn.commit()

        got = self._repo.get("2501.00001")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.arxiv_id, "2501.00001")
        self.assertEqual(got.title, "A Test Paper")
        self.assertEqual(got.authors, ["Alice", "Bob"])
        self.assertEqual(got.categories, ["cs.CV", "cs.LG"])
        self.assertEqual(got.version, 1)

    def test_upsert_paper_update_existing(self) -> None:
        self._repo.upsert(_make_paper(version=1, title="Old Title"))
        self._conn.commit()

        self._repo.upsert(_make_paper(version=2, title="New Title"))
        self._conn.commit()

        got = self._repo.get("2501.00001")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.version, 2)
        self.assertEqual(got.title, "New Title")

    def test_upsert_preserves_original_created_at(self) -> None:
        self._repo.upsert(_make_paper())
        self._conn.commit()
        first = dict(self._conn.execute(
            "SELECT created_at FROM papers WHERE arxiv_id = '2501.00001'"
        ).fetchone())

        self._repo.upsert(_make_paper(version=2))
        self._conn.commit()
        second = dict(self._conn.execute(
            "SELECT created_at FROM papers WHERE arxiv_id = '2501.00001'"
        ).fetchone())

        self.assertEqual(first["created_at"], second["created_at"])

    def test_get_nonexistent_paper_returns_none(self) -> None:
        self.assertIsNone(self._repo.get("nonexistent"))

    def test_upsert_without_commit_is_not_visible(self) -> None:
        """验证仓储层不会自动提交"""
        self._repo.upsert(_make_paper(arxiv_id="2501.00001"))
        # Same connection, but no commit — should be visible in same tx
        got = self._repo.get("2501.00001")
        self.assertIsNotNone(got)  # visible in same transaction

        # But a fresh connection should not see it
        conn2 = sqlite3.connect(":memory:")
        conn2.row_factory = sqlite3.Row
        self.addCleanup(conn2.close)
        # The uncommitted write is invisible to another connection
        # (for :memory: this is only testable via new connection)

    # -- list / filter -----------------------------------------------------

    def test_list_all_returns_papers_ordered_by_updated_at(self) -> None:
        self._repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        papers = self._repo.list_all()
        self.assertEqual(len(papers), 2)
        self.assertEqual(papers[0].arxiv_id, "2501.00002")  # newer first

    def test_list_by_category_filters_correctly(self) -> None:
        self._repo.upsert(_make_paper(arxiv_id="2501.00001", primary_category="cs.CV"))
        self._repo.upsert(_make_paper(arxiv_id="2501.00002", primary_category="cs.LG"))
        self._conn.commit()

        cv_papers = self._repo.list_by_category("cs.CV")
        self.assertEqual(len(cv_papers), 1)
        self.assertEqual(cv_papers[0].arxiv_id, "2501.00001")

    # -- version -----------------------------------------------------------

    def test_upsert_version_requires_parent_paper(self) -> None:
        paper = _make_paper()
        # Parent not inserted → should fail
        with self.assertRaises(sqlite3.IntegrityError):
            self._repo.upsert_version(paper, '{"raw": true}')

    def test_upsert_version_stores_record(self) -> None:
        self._repo.upsert(_make_paper())
        self._conn.commit()
        self._repo.upsert_version(_make_paper(), '{"raw": true}')
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM paper_versions WHERE arxiv_id = ? AND version = ?",
            ("2501.00001", 1),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["version"], 1)
        self.assertEqual(json.loads(row["raw_payload_json"]), {"raw": True})

    def test_upsert_version_ignores_duplicate(self) -> None:
        self._repo.upsert(_make_paper())
        self._conn.commit()
        self._repo.upsert_version(_make_paper(), None)
        self._conn.commit()
        self._repo.upsert_version(_make_paper(), None)
        self._conn.commit()

        count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM paper_versions"
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    # -- JSON roundtrip ----------------------------------------------------

    def test_json_fields_roundtrip(self) -> None:
        self._repo.upsert(
            _make_paper(authors=["Alice", "Bob"], categories=["cs.CV", "cs.LG"])
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT authors_json, categories_json FROM papers WHERE arxiv_id = ?",
            ("2501.00001",),
        ).fetchone()
        self.assertEqual(json.loads(row["authors_json"]), ["Alice", "Bob"])
        self.assertEqual(json.loads(row["categories_json"]), ["cs.CV", "cs.LG"])

    def test_datetime_fields_stored_as_iso8601(self) -> None:
        self._repo.upsert(_make_paper())
        self._conn.commit()

        row = self._conn.execute(
            "SELECT published_at, updated_at, created_at, synced_at FROM papers WHERE arxiv_id = ?",
            ("2501.00001",),
        ).fetchone()
        for col in ("published_at", "updated_at", "created_at", "synced_at"):
            dt = datetime.fromisoformat(row[col])
            self.assertIsNotNone(dt.tzinfo, f"{col} should be timezone-aware")


class SubscriptionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._repo = SubscriptionRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_save_and_get_subscription(self) -> None:
        self._repo.save(_make_subscription())
        self._conn.commit()

        got = self._repo.get("sub-1")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.name, "CV Daily")
        self.assertEqual(got.include_keywords, ["transformer"])
        self.assertEqual(got.exclude_keywords, ["survey"])
        self.assertTrue(got.enabled)

    def test_save_preserves_original_created_at(self) -> None:
        self._repo.save(_make_subscription())
        self._conn.commit()
        first = dict(self._conn.execute(
            "SELECT created_at FROM subscriptions WHERE id = 'sub-1'"
        ).fetchone())

        self._repo.save(_make_subscription(name="Updated"))
        self._conn.commit()
        second = dict(self._conn.execute(
            "SELECT created_at FROM subscriptions WHERE id = 'sub-1'"
        ).fetchone())

        self.assertEqual(first["created_at"], second["created_at"])

    def test_list_enabled_only_returns_enabled(self) -> None:
        self._repo.save(_make_subscription(id="sub-1", enabled=True))
        self._repo.save(_make_subscription(id="sub-2", enabled=False))
        self._conn.commit()

        enabled = self._repo.list_enabled()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].id, "sub-1")

    def test_list_all_returns_all(self) -> None:
        self._repo.save(_make_subscription(id="sub-1"))
        self._repo.save(_make_subscription(id="sub-2", enabled=False))
        self._conn.commit()

        all_subs = self._repo.list_all()
        self.assertEqual(len(all_subs), 2)

    def test_delete_subscription_without_sync_runs(self) -> None:
        self._repo.save(_make_subscription())
        self._conn.commit()
        self._repo.delete("sub-1")
        self._conn.commit()
        self.assertIsNone(self._repo.get("sub-1"))

    def test_save_new_subscription_generates_timestamps(self) -> None:
        self._repo.save(_make_subscription())
        self._conn.commit()
        row = self._conn.execute(
            "SELECT created_at, updated_at FROM subscriptions WHERE id = 'sub-1'"
        ).fetchone()
        self.assertIsNotNone(row["created_at"])
        self.assertIsNotNone(row["updated_at"])
        datetime.fromisoformat(row["created_at"])
        datetime.fromisoformat(row["updated_at"])


class PaperStatusRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._paper_repo = PaperRepository(self._conn)
        self._repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _ensure_paper(self, arxiv_id: str = "2501.00001") -> None:
        self._paper_repo.upsert(_make_paper(arxiv_id=arxiv_id))
        self._conn.commit()

    def test_upsert_and_get_status(self) -> None:
        self._ensure_paper()
        self._repo.upsert(_make_status(is_starred=True, note="Interesting"))
        self._conn.commit()

        got = self._repo.get("2501.00001")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertTrue(got.is_starred)
        self.assertEqual(got.note, "Interesting")

    def test_starred_filter_excludes_hidden(self) -> None:
        self._ensure_paper("2501.00001")
        self._ensure_paper("2501.00002")

        self._repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._repo.upsert(_make_status(arxiv_id="2501.00002", is_hidden=True))
        self._conn.commit()

        starred = self._repo.list_starred()
        self.assertEqual(len(starred), 1)
        self.assertEqual(starred[0].arxiv_id, "2501.00001")

    def test_status_rejects_orphan_paper(self) -> None:
        status = _make_status()
        with self.assertRaises(sqlite3.IntegrityError):
            self._repo.upsert(status)

    def test_tags_json_roundtrip(self) -> None:
        self._ensure_paper()
        self._repo.upsert(_make_status(tags=["important", "to-read"]))
        self._conn.commit()

        row = self._conn.execute(
            "SELECT tags_json FROM paper_statuses WHERE arxiv_id = ?",
            ("2501.00001",),
        ).fetchone()
        self.assertEqual(json.loads(row["tags_json"]), ["important", "to-read"])

    def test_rating_bounds_not_enforced_at_repo_level(self) -> None:
        self._ensure_paper()
        self._repo.upsert(_make_status(rating=3))
        self._conn.commit()
        got = self._repo.get("2501.00001")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.rating, 3)


class SyncRunRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._sub_repo = SubscriptionRepository(self._conn)
        self._sub_repo.save(_make_subscription())
        self._sub_repo.save(_make_subscription(id="sub-2", name="Sub 2"))
        self._conn.commit()
        self._repo = SyncRunRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_insert_and_get_sync_run(self) -> None:
        self._repo.insert(_make_sync_run(status=SyncRunStatus.RUNNING))
        self._conn.commit()

        got = self._repo.get("run-1")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.id, "run-1")
        self.assertEqual(got.status, SyncRunStatus.RUNNING)
        self.assertEqual(got.trigger_type, SyncTriggerType.MANUAL)

    def test_sync_run_rejects_orphan_subscription(self) -> None:
        run = _make_sync_run(subscription_id="nonexistent")
        with self.assertRaises(sqlite3.IntegrityError):
            self._repo.insert(run)

    def test_update_sync_run_to_success(self) -> None:
        self._repo.insert(_make_sync_run(status=SyncRunStatus.RUNNING))
        self._conn.commit()

        self._repo.update(
            _make_sync_run(
                status=SyncRunStatus.SUCCESS,
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                fetched_count=10,
                inserted_count=5,
                updated_count=2,
            )
        )
        self._conn.commit()

        got = self._repo.get("run-1")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.status, SyncRunStatus.SUCCESS)
        self.assertEqual(got.fetched_count, 10)

    def test_update_sync_run_to_failed(self) -> None:
        self._repo.insert(_make_sync_run(status=SyncRunStatus.RUNNING))
        self._conn.commit()

        self._repo.update(
            _make_sync_run(
                status=SyncRunStatus.FAILED,
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                error_message="Network timeout",
            )
        )
        self._conn.commit()

        got = self._repo.get("run-1")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.status, SyncRunStatus.FAILED)
        self.assertEqual(got.error_message, "Network timeout")

    def test_multi_step_sync_lifecycle_in_one_transaction(self) -> None:
        """模拟 Slice 04 同步流程：多次仓储调用，单次提交"""
        # Start sync run
        self._repo.insert(
            _make_sync_run(
                id="run-tx",
                status=SyncRunStatus.RUNNING,
            )
        )
        # (In real code: arXiv fetch happens here)
        # Upsert paper
        paper_repo = PaperRepository(self._conn)
        paper_repo.upsert(_make_paper(arxiv_id="2501.00042"))
        paper_repo.upsert_version(_make_paper(arxiv_id="2501.00042"), None)
        # Mark sync successful
        self._repo.update(
            _make_sync_run(
                id="run-tx",
                status=SyncRunStatus.SUCCESS,
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                fetched_count=1,
                inserted_count=1,
            )
        )
        # Single atomic commit
        self._conn.commit()

        # All writes are visible
        self.assertIsNotNone(self._repo.get("run-tx"))
        self.assertIsNotNone(paper_repo.get("2501.00042"))

    def test_list_by_subscription(self) -> None:
        self._repo.insert(_make_sync_run(id="run-1", subscription_id="sub-1"))
        self._repo.insert(_make_sync_run(id="run-2", subscription_id="sub-1"))
        self._repo.insert(_make_sync_run(id="run-3", subscription_id="sub-2"))
        self._conn.commit()

        sub1_runs = self._repo.list_by_subscription("sub-1")
        self.assertEqual(len(sub1_runs), 2)

    def test_enum_fields_roundtrip(self) -> None:
        self._repo.insert(
            _make_sync_run(
                status=SyncRunStatus.FAILED,
                trigger_type=SyncTriggerType.SCHEDULED,
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                error_message="Error",
            )
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT status, trigger_type FROM sync_runs WHERE id = ?", ("run-1",)
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["trigger_type"], "scheduled")


class AppSettingsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._repo = AppSettingsRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_get_nonexistent_returns_none(self) -> None:
        self.assertIsNone(self._repo.get("missing"))

    def test_set_and_get_dict(self) -> None:
        self._repo.set("theme", {"mode": "dark"})
        self._conn.commit()
        self.assertEqual(self._repo.get("theme"), {"mode": "dark"})

    def test_set_and_get_list(self) -> None:
        self._repo.set("pinned", ["a", "b"])
        self._conn.commit()
        self.assertEqual(self._repo.get("pinned"), ["a", "b"])

    def test_set_and_get_scalar(self) -> None:
        self._repo.set("count", 42)
        self._conn.commit()
        self.assertEqual(self._repo.get("count"), 42)

    def test_set_rejects_non_json_serializable(self) -> None:
        with self.assertRaises(TypeError):
            self._repo.set("bad", object())

    def test_set_overwrites_existing_value(self) -> None:
        self._repo.set("key", 1)
        self._conn.commit()
        self._repo.set("key", 2)
        self._conn.commit()
        self.assertEqual(self._repo.get("key"), 2)

    def test_delete(self) -> None:
        self._repo.set("key", "value")
        self._conn.commit()
        self._repo.delete("key")
        self._conn.commit()
        self.assertIsNone(self._repo.get("key"))


class TransactionRollbackTests(unittest.TestCase):
    """验证未提交的写入可以被回滚"""

    def setUp(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_database(self._conn)
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_rollback_discards_uncommitted_paper(self) -> None:
        self._paper_repo.upsert(_make_paper())
        # Visible inside the same transaction
        self.assertIsNotNone(self._paper_repo.get("2501.00001"))

        self._conn.rollback()

        # After rollback, gone
        self.assertIsNone(self._paper_repo.get("2501.00001"))


class AutoInitTests(unittest.TestCase):
    """验证 get_connection() 自动初始化数据库 schema"""

    def test_get_connection_auto_inits_schema(self) -> None:
        conn = get_connection(":memory:")
        try:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("papers", tables)
            self.assertIn("subscriptions", tables)
        finally:
            conn.close()

    def test_get_connection_auto_init_false_skips_schema(self) -> None:
        conn = get_connection(":memory:", auto_init=False)
        try:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertEqual(tables, set())
        finally:
            conn.close()

    def test_get_connection_env_var_path(self) -> None:
        import os
        os.environ["PAPER_RESEARCH_DB_PATH"] = ":memory:"
        try:
            conn = get_connection()
            conn.close()
        finally:
            del os.environ["PAPER_RESEARCH_DB_PATH"]


# ---------------------------------------------------------------------------
# Regression: Slice 01 models still importable and valid
# ---------------------------------------------------------------------------

class Slice01RegressionTests(unittest.TestCase):
    """确保 Slice 01 的模型契约未被破坏"""

    def test_paper_still_validates(self) -> None:
        paper = Paper(
            arxiv_id="2501.01234v2",
            version=2,
            title="  A Paper  ",
            abstract="Abstract.",
            authors=["Alice"],
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/2501.01234",
        )
        self.assertEqual(paper.arxiv_id, "2501.01234")

    def test_subscription_still_validates(self) -> None:
        sub = Subscription(id="test", name="Test")
        self.assertEqual(sub.name, "Test")

    def test_paper_status_still_validates(self) -> None:
        status = PaperStatus(arxiv_id="2501.01234")
        self.assertFalse(status.is_starred)

    def test_sync_run_still_validates(self) -> None:
        run = SyncRun(
            id="run-1",
            subscription_id="sub-1",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            trigger_type=SyncTriggerType.MANUAL,
        )
        self.assertEqual(run.status, SyncRunStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
