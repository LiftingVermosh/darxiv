from __future__ import annotations

import sqlite3
import textwrap
import unittest
from unittest.mock import MagicMock

from app.application.dto import SubscriptionUpsertInput, SyncResultDTO
from app.application.services import SubscriptionService, SyncService
from app.application.services.exceptions import (
    DuplicateSubscriptionNameError,
    InvalidSubscriptionRuleError,
    SubscriptionNotFoundError,
    SubscriptionServiceError,
)
from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import Subscription
from app.infrastructure.arxiv import ArxivClient
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories import SubscriptionRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MOCK_FEED_SINGLE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2501.01234v2</id>
        <title>A Transformer Architecture for Vision</title>
        <summary>We propose a novel transformer-based approach.</summary>
        <author><name>Alice Smith</name></author>
        <author><name>Bob Jones</name></author>
        <published>2025-01-15T10:00:00Z</published>
        <updated>2025-01-20T15:30:00Z</updated>
        <arxiv:primary_category scheme="http://arxiv.org/schemas/atom"
                                 term="cs.CV"/>
        <category term="cs.CV"/>
        <category term="cs.LG"/>
        <link href="http://arxiv.org/pdf/2501.01234v2" rel="related"
              title="pdf" type="application/pdf"/>
      </entry>
    </feed>
""")


def _make_input(**overrides) -> SubscriptionUpsertInput:
    defaults = {
        "name": "CV Daily",
        "enabled": True,
        "categories": ["cs.CV"],
        "include_keywords": ["transformer"],
        "exclude_keywords": [],
        "authors": [],
        "sync_interval_minutes": 1440,
    }
    defaults.update(overrides)
    return SubscriptionUpsertInput(**defaults)


def _setup_db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    return conn


def _make_mock_sync_service(conn: sqlite3.Connection) -> SyncService:
    """创建带有 mock ArxivClient 的 SyncService"""
    mock_client = MagicMock(spec=ArxivClient)
    mock_client.fetch.return_value = _MOCK_FEED_SINGLE
    return SyncService(conn, arxiv_client=mock_client)


# ---------------------------------------------------------------------------
# SubscriptionUpsertInput DTO Tests
# ---------------------------------------------------------------------------


class SubscriptionUpsertInputValidationTests(unittest.TestCase):
    """DTO 层输入校验测试"""

    def test_valid_input_passes(self) -> None:
        input_ = _make_input()
        self.assertEqual(input_.name, "CV Daily")
        self.assertEqual(input_.categories, ["cs.CV"])
        self.assertTrue(input_.enabled)

    def test_name_is_required_and_normalized(self) -> None:
        input_ = _make_input(name="  My Feed  ")
        self.assertEqual(input_.name, "My Feed")

    def test_name_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            _make_input(name="   ")

    def test_categories_normalized_deduped(self) -> None:
        input_ = _make_input(categories=["cs.CV", " cs.CV ", "cs.LG"])
        self.assertEqual(input_.categories, ["cs.CV", "cs.LG"])

    def test_include_keywords_lowercased(self) -> None:
        input_ = _make_input(include_keywords=["Transformer", "VISION"])
        self.assertEqual(input_.include_keywords, ["transformer", "vision"])

    def test_exclude_keywords_lowercased(self) -> None:
        input_ = _make_input(exclude_keywords=["JUNK", "Spam"])
        self.assertEqual(input_.exclude_keywords, ["junk", "spam"])

    def test_sync_interval_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            _make_input(sync_interval_minutes=0)

        with self.assertRaises(ValueError):
            _make_input(sync_interval_minutes=-5)

    def test_missing_all_filters_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _make_input(
                categories=[],
                include_keywords=[],
                authors=[],
            )
        self.assertIn("filter criterion", str(ctx.exception))

    def test_only_categories_is_valid(self) -> None:
        input_ = _make_input(
            categories=["cs.CV"],
            include_keywords=[],
            authors=[],
        )
        self.assertEqual(input_.categories, ["cs.CV"])

    def test_only_include_keywords_is_valid(self) -> None:
        input_ = _make_input(
            categories=[],
            include_keywords=["transformer"],
            authors=[],
        )
        self.assertEqual(input_.include_keywords, ["transformer"])

    def test_only_authors_is_valid(self) -> None:
        input_ = _make_input(
            categories=[],
            include_keywords=[],
            authors=["Alice"],
        )
        self.assertEqual(input_.authors, ["Alice"])

    def test_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            SubscriptionUpsertInput(
                name="Test",
                categories=["cs.CV"],
                unknown_field="oops",
            )

    def test_query_text_optional_normalized(self) -> None:
        input_ = _make_input(query_text="  all:electron  ")
        self.assertEqual(input_.query_text, "all:electron")

        input_none = _make_input(query_text=None)
        self.assertIsNone(input_none.query_text)


# ---------------------------------------------------------------------------
# SubscriptionService CRUD Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceCrudTests(unittest.TestCase):
    """订阅 CRUD 测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> SubscriptionService:
        return SubscriptionService(self._conn)

    def test_create_subscription_returns_persisted_entity(self) -> None:
        service = self._service()
        input_ = _make_input(name="CV Daily")

        sub = service.create_subscription(input_)

        self.assertIsInstance(sub, Subscription)
        self.assertIsNotNone(sub.id)
        self.assertTrue(len(sub.id) > 0)
        self.assertEqual(sub.name, "CV Daily")
        self.assertTrue(sub.enabled)
        self.assertEqual(sub.categories, ["cs.CV"])
        self.assertEqual(sub.sync_interval_minutes, 1440)

    def test_create_subscription_persists_to_db(self) -> None:
        service = self._service()
        sub = service.create_subscription(_make_input(name="Persist Test"))

        # 通过 repository 独立验证
        repo = SubscriptionRepository(self._conn)
        loaded = repo.get(sub.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.name, "Persist Test")

    def test_list_subscriptions_returns_all(self) -> None:
        service = self._service()
        service.create_subscription(_make_input(name="A"))
        service.create_subscription(_make_input(name="B"))

        results = service.list_subscriptions()
        self.assertEqual(len(results), 2)
        names = [s.name for s in results]
        self.assertEqual(names, ["A", "B"])  # sorted by name

    def test_get_subscription_found(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(name="Target"))

        found = service.get_subscription(created.id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.name, "Target")

    def test_get_subscription_not_found(self) -> None:
        service = self._service()
        result = service.get_subscription("nonexistent")
        self.assertIsNone(result)

    def test_update_subscription_replaces_all_fields(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(name="Original"))

        updated = service.update_subscription(
            created.id,
            _make_input(
                name="Updated Name",
                categories=["cs.LG"],
                include_keywords=["deep learning"],
                sync_interval_minutes=720,
            ),
        )

        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.name, "Updated Name")
        self.assertEqual(updated.categories, ["cs.LG"])
        self.assertEqual(updated.include_keywords, ["deep learning"])
        self.assertEqual(updated.sync_interval_minutes, 720)

        # Verify persistence
        loaded = service.get_subscription(created.id)
        assert loaded is not None
        self.assertEqual(loaded.name, "Updated Name")

    def test_delete_subscription_removes_it(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(name="To Delete"))

        service.delete_subscription(created.id)

        self.assertIsNone(service.get_subscription(created.id))

    def test_delete_nonexistent_subscription_raises(self) -> None:
        service = self._service()

        with self.assertRaises(SubscriptionNotFoundError) as ctx:
            service.delete_subscription("nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))

    def test_update_nonexistent_subscription_raises(self) -> None:
        service = self._service()

        with self.assertRaises(SubscriptionNotFoundError):
            service.update_subscription("nonexistent", _make_input(name="X"))


# ---------------------------------------------------------------------------
# SubscriptionService Name Uniqueness Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceNameUniquenessTests(unittest.TestCase):
    """订阅名称唯一性约束测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> SubscriptionService:
        return SubscriptionService(self._conn)

    def test_create_duplicate_name_raises(self) -> None:
        service = self._service()
        service.create_subscription(_make_input(name="Unique Name"))

        with self.assertRaises(DuplicateSubscriptionNameError) as ctx:
            service.create_subscription(_make_input(name="Unique Name"))
        self.assertIn("Unique Name", str(ctx.exception))

    def test_update_to_duplicate_name_raises(self) -> None:
        service = self._service()
        a = service.create_subscription(_make_input(name="Subscription A"))
        service.create_subscription(_make_input(name="Subscription B"))

        with self.assertRaises(DuplicateSubscriptionNameError):
            service.update_subscription(
                a.id,
                _make_input(name="Subscription B"),
            )

    def test_update_same_name_is_allowed(self) -> None:
        """更新时保留自己原有的名称应被允许"""
        service = self._service()
        created = service.create_subscription(_make_input(name="Keep Name"))

        updated = service.update_subscription(
            created.id,
            _make_input(name="Keep Name", categories=["cs.LG"]),
        )

        self.assertEqual(updated.name, "Keep Name")
        self.assertEqual(updated.categories, ["cs.LG"])

    def test_db_unique_constraint_rejects_duplicate_name(self) -> None:
        """绕过 service 层直接用 SQL 插入重名订阅时，DB UNIQUE 约束应拦截"""
        service = self._service()
        service.create_subscription(_make_input(name="DB Unique"))

        # 绕过 service，直接通过 repository 写一条同名但不同 ID 的订阅
        repo = SubscriptionRepository(self._conn)
        dup = Subscription(
            id="bypass-id-12345",
            name="DB Unique",
            enabled=True,
            categories=["cs.AI"],
            sync_interval_minutes=60,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            repo.save(dup)
            self._conn.commit()


# ---------------------------------------------------------------------------
# SubscriptionService Enable/Disable Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceEnableToggleTests(unittest.TestCase):
    """订阅启停测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> SubscriptionService:
        return SubscriptionService(self._conn)

    def test_set_subscription_enabled_false(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(enabled=True))
        self.assertTrue(created.enabled)

        toggled = service.set_subscription_enabled(created.id, False)
        self.assertFalse(toggled.enabled)

        loaded = service.get_subscription(created.id)
        assert loaded is not None
        self.assertFalse(loaded.enabled)

    def test_set_subscription_enabled_true(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(enabled=False))
        self.assertFalse(created.enabled)

        toggled = service.set_subscription_enabled(created.id, True)
        self.assertTrue(toggled.enabled)

    def test_set_enabled_on_nonexistent_raises(self) -> None:
        service = self._service()

        with self.assertRaises(SubscriptionNotFoundError):
            service.set_subscription_enabled("nonexistent", True)

    def test_toggle_preserves_other_fields(self) -> None:
        service = self._service()
        created = service.create_subscription(
            _make_input(name="Toggle Me", categories=["cs.AI", "cs.CL"])
        )

        toggled = service.set_subscription_enabled(created.id, False)

        self.assertEqual(toggled.name, "Toggle Me")
        self.assertEqual(toggled.categories, ["cs.AI", "cs.CL"])
        self.assertFalse(toggled.enabled)


# ---------------------------------------------------------------------------
# SubscriptionService Sync Delegation Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceSyncTests(unittest.TestCase):
    """通过 SubscriptionService 触发单订阅同步测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def test_sync_subscription_delegates_to_sync_service(self) -> None:
        service = SubscriptionService(
            self._conn,
            sync_service=_make_mock_sync_service(self._conn),
        )
        created = service.create_subscription(_make_input(name="Sync Me"))

        result = service.sync_subscription(created.id)

        self.assertIsInstance(result, SyncResultDTO)
        self.assertEqual(result.subscription_id, created.id)
        self.assertEqual(result.status, SyncRunStatus.SUCCESS)

    def test_sync_nonexistent_subscription_raises(self) -> None:
        service = SubscriptionService(
            self._conn,
            sync_service=_make_mock_sync_service(self._conn),
        )

        with self.assertRaises(SubscriptionNotFoundError):
            service.sync_subscription("nonexistent")

    def test_last_synced_at_is_written_after_successful_sync(self) -> None:
        """成功同步后 subscriptions.last_synced_at 应被回写"""
        service = SubscriptionService(
            self._conn,
            sync_service=_make_mock_sync_service(self._conn),
        )
        created = service.create_subscription(_make_input(name="Sync Stamp"))

        # 同步前 last_synced_at 应为 NULL
        before = self._conn.execute(
            "SELECT last_synced_at FROM subscriptions WHERE id = ?",
            (created.id,),
        ).fetchone()
        self.assertIsNone(before["last_synced_at"])

        service.sync_subscription(created.id)

        # 同步后 last_synced_at 应被写入
        after = self._conn.execute(
            "SELECT last_synced_at FROM subscriptions WHERE id = ?",
            (created.id,),
        ).fetchone()
        self.assertIsNotNone(after["last_synced_at"])
        # 应为 ISO 8601 格式的时间戳
        self.assertIn("T", after["last_synced_at"])

    def test_failed_sync_does_not_overwrite_last_synced_at(self) -> None:
        """失败同步不会错误地刷新 last_synced_at"""
        # 先成功同步一次，写入 last_synced_at
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.return_value = _MOCK_FEED_SINGLE
        sync_svc = SyncService(self._conn, arxiv_client=mock_client)

        service = SubscriptionService(self._conn, sync_service=sync_svc)
        created = service.create_subscription(_make_input(name="Fail Stamp"))
        service.sync_subscription(created.id)

        first_sync = self._conn.execute(
            "SELECT last_synced_at FROM subscriptions WHERE id = ?",
            (created.id,),
        ).fetchone()["last_synced_at"]
        self.assertIsNotNone(first_sync)

        # 第二次同步强制失败
        mock_client.fetch.side_effect = RuntimeError("Network down")
        result = service.sync_subscription(created.id)
        self.assertEqual(result.status, SyncRunStatus.FAILED)

        # last_synced_at 应保持第一次成功同步的时间戳不变
        after_fail = self._conn.execute(
            "SELECT last_synced_at FROM subscriptions WHERE id = ?",
            (created.id,),
        ).fetchone()["last_synced_at"]
        self.assertEqual(after_fail, first_sync)


# ---------------------------------------------------------------------------
# SubscriptionService Delete with Sync Runs Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceDeleteWithSyncRunsTests(unittest.TestCase):
    """删除带同步记录的订阅"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def test_delete_subscription_with_prior_sync_runs(self) -> None:
        """有历史 sync_runs 的订阅也能被删除（FK RESTRICT 约束处理）"""
        service = SubscriptionService(
            self._conn,
            sync_service=_make_mock_sync_service(self._conn),
        )
        created = service.create_subscription(_make_input(name="Has Runs"))

        # 触发一次同步，生成 sync_run 记录
        service.sync_subscription(created.id)

        # 验证有 sync_run
        runs = self._conn.execute(
            "SELECT COUNT(*) FROM sync_runs WHERE subscription_id = ?",
            (created.id,),
        ).fetchone()
        self.assertGreater(runs[0], 0)

        # 删除应成功（service 内部清理 sync_runs）
        service.delete_subscription(created.id)

        self.assertIsNone(service.get_subscription(created.id))
        # sync_runs 也已被清理
        runs = self._conn.execute(
            "SELECT COUNT(*) FROM sync_runs WHERE subscription_id = ?",
            (created.id,),
        ).fetchone()
        self.assertEqual(runs[0], 0)


# ---------------------------------------------------------------------------
# SubscriptionService Edge Case Tests
# ---------------------------------------------------------------------------


class SubscriptionServiceEdgeCaseTests(unittest.TestCase):
    """边界场景测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> SubscriptionService:
        return SubscriptionService(self._conn)

    def test_list_empty_database_returns_empty_list(self) -> None:
        service = self._service()
        results = service.list_subscriptions()
        self.assertEqual(results, [])

    def test_create_multiple_with_different_names(self) -> None:
        service = self._service()
        service.create_subscription(_make_input(name="Feed A"))
        service.create_subscription(_make_input(name="Feed B"))
        service.create_subscription(_make_input(name="Feed C"))

        self.assertEqual(len(service.list_subscriptions()), 3)

    def test_delete_then_create_same_name(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(name="Recycled"))
        service.delete_subscription(created.id)

        # 同名新订阅应被允许
        new_sub = service.create_subscription(_make_input(name="Recycled"))
        self.assertNotEqual(new_sub.id, created.id)
        self.assertEqual(new_sub.name, "Recycled")

    def test_update_twice_on_same_subscription(self) -> None:
        service = self._service()
        created = service.create_subscription(_make_input(name="First"))

        v2 = service.update_subscription(
            created.id,
            _make_input(name="Second", categories=["cs.LG"]),
        )
        self.assertEqual(v2.name, "Second")

        v3 = service.update_subscription(
            created.id,
            _make_input(name="Third", categories=["cs.CL"]),
        )
        self.assertEqual(v3.name, "Third")
        self.assertEqual(v3.id, created.id)

    def test_subscription_id_is_uuid_format(self) -> None:
        service = self._service()
        sub = service.create_subscription(_make_input())
        # UUID v4: 36 chars with 4 hyphens
        self.assertEqual(len(sub.id), 36)
        self.assertEqual(sub.id.count("-"), 4)

    def test_authors_as_string_converted_to_list(self) -> None:
        input_ = _make_input(authors="Alice Smith")
        self.assertEqual(input_.authors, ["Alice Smith"])

    def test_empty_strings_in_lists_are_filtered_out(self) -> None:
        input_ = _make_input(
            categories=["cs.CV", "  ", ""],
            include_keywords=["transformer", "   "],
        )
        self.assertEqual(input_.categories, ["cs.CV"])
        self.assertEqual(input_.include_keywords, ["transformer"])


# ---------------------------------------------------------------------------
# Exception Hierarchy Tests
# ---------------------------------------------------------------------------


class ExceptionHierarchyTests(unittest.TestCase):
    """异常层级测试"""

    def test_subscription_not_found_is_service_error(self) -> None:
        exc = SubscriptionNotFoundError("sub-x")
        self.assertIsInstance(exc, SubscriptionServiceError)
        self.assertIsInstance(exc, Exception)

    def test_duplicate_name_is_service_error(self) -> None:
        exc = DuplicateSubscriptionNameError("Test")
        self.assertIsInstance(exc, SubscriptionServiceError)

    def test_invalid_rule_is_service_error(self) -> None:
        exc = InvalidSubscriptionRuleError("Bad rule")
        self.assertIsInstance(exc, SubscriptionServiceError)

    def test_exceptions_carry_context(self) -> None:
        exc = SubscriptionNotFoundError("abc-123")
        self.assertEqual(exc.subscription_id, "abc-123")
        self.assertIn("abc-123", str(exc))

        exc2 = DuplicateSubscriptionNameError("My Feed")
        self.assertEqual(exc2.name, "My Feed")


# ---------------------------------------------------------------------------
# AppContext Bootstrap Tests
# ---------------------------------------------------------------------------


class AppContextBootstrapTests(unittest.TestCase):
    """启动装配与连接生命周期测试"""

    def test_create_app_context_with_memory_db(self) -> None:
        from app.main import AppContext, create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIsInstance(ctx, AppContext)
            self.assertIsInstance(ctx.connection, sqlite3.Connection)
            self.assertIsInstance(ctx.subscription_service, SubscriptionService)
            self.assertIsInstance(ctx.sync_service, SyncService)
        finally:
            ctx.close()

    def test_create_app_context_services_are_wired(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            # SubscriptionService 的 _sync_service 应已注入
            self.assertIsNotNone(ctx.subscription_service._sync_service)
        finally:
            ctx.close()

    def test_schema_is_initialized_on_bootstrap(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            # 验证 schema 表存在（包括 UNIQUE 约束）
            tables = ctx.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = {t[0] for t in tables}
            expected = {
                "papers",
                "paper_versions",
                "subscriptions",
                "paper_statuses",
                "sync_runs",
                "app_settings",
            }
            self.assertTrue(expected.issubset(table_names))

            # 验证 subscriptions.name 上有 UNIQUE 约束
            # 新库由 schema.sql 内联 UNIQUE 产生 autoindex；
            # 旧库由 initialize_database() 的显式迁移索引覆盖。
            indexes = ctx.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='subscriptions' "
                "AND (name LIKE 'sqlite_autoindex_subscriptions%' "
                "     OR name = 'uq_subscriptions_name')"
            ).fetchall()
            self.assertGreater(
                len(indexes), 0,
                "subscriptions 表应有 UNIQUE(name) 约束",
            )
        finally:
            ctx.close()

    def test_bootstrap_is_idempotent(self) -> None:
        """多次启动不报错"""
        from app.main import create_app_context

        ctx1 = create_app_context(":memory:")
        # 第二次对同一 :memory: 连接创建新上下文（独立的连接）
        ctx2 = create_app_context(":memory:")

        self.assertIsNotNone(ctx1)
        self.assertIsNotNone(ctx2)

        ctx1.close()
        ctx2.close()

    def test_context_manager_closes_connection(self) -> None:
        """with 语句退出后连接自动关闭"""
        from app.main import create_app_context

        with create_app_context(":memory:") as ctx:
            self.assertFalse(ctx.closed)

        self.assertTrue(ctx.closed)

    def test_close_is_idempotent(self) -> None:
        """重复 close() 不报错"""
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        ctx.close()
        self.assertTrue(ctx.closed)

        # 第二次 close 应无副作用
        ctx.close()
        self.assertTrue(ctx.closed)

    def test_closed_property_after_close(self) -> None:
        """关闭后 closed 属性返回 True"""
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        self.assertFalse(ctx.closed)
        ctx.close()
        self.assertTrue(ctx.closed)

    def test_context_manager_on_exception_still_closes(self) -> None:
        """即使 with 块内发生异常，连接也应被关闭"""
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            with ctx:
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass

        self.assertTrue(ctx.closed)

    def test_old_database_gets_uniqueness_migration(self) -> None:
        """预先存在的数据库（无 UNIQUE 约束）经 initialize_database() 后获得唯一索引"""
        from app.infrastructure.db.migrations import initialize_database

        # 模拟旧库：手动建 subscriptions 表但不含 UNIQUE(name)
        conn = get_connection(":memory:", auto_init=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subscriptions ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  enabled INTEGER NOT NULL,"
            "  categories_json TEXT NOT NULL,"
            "  include_keywords_json TEXT NOT NULL,"
            "  exclude_keywords_json TEXT NOT NULL,"
            "  authors_json TEXT NOT NULL,"
            "  query_text TEXT,"
            "  sync_interval_minutes INTEGER NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  last_synced_at TEXT"
            ")"
        )

        # 确认迁移前没有 name 列上的 UNIQUE 约束
        # PRAGMA index_list: origin='pk' = 主键, origin='u' = UNIQUE 约束
        before_unique = [
            r for r in conn.execute("PRAGMA index_list('subscriptions')").fetchall()
            if r["unique"] and r["origin"] != "pk"
        ]
        self.assertEqual(
            len(before_unique), 0,
            "旧库在迁移前不应有 name 列上的 UNIQUE 约束",
        )

        # 执行 migrate（使用完整 schema.sql + 补充迁移）
        initialize_database(conn)

        # 迁移后应有非主键的 UNIQUE 索引
        after_unique = [
            r for r in conn.execute("PRAGMA index_list('subscriptions')").fetchall()
            if r["unique"] and r["origin"] != "pk"
        ]
        self.assertGreater(
            len(after_unique), 0,
            "旧库经 initialize_database() 后应获得 UNIQUE(name) 约束",
        )

        # 验证约束实际生效
        conn.execute(
            "INSERT INTO subscriptions (id, name, enabled, categories_json, "
            "  include_keywords_json, exclude_keywords_json, authors_json, "
            "  sync_interval_minutes, created_at, updated_at) "
            "VALUES ('id-1', 'DupTest', 1, '[]', '[]', '[]', '[]', 60, 't', 't')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subscriptions (id, name, enabled, categories_json, "
                "  include_keywords_json, exclude_keywords_json, authors_json, "
                "  sync_interval_minutes, created_at, updated_at) "
                "VALUES ('id-2', 'DupTest', 1, '[]', '[]', '[]', '[]', 60, 't', 't')"
            )

        conn.close()

    def test_new_database_has_exactly_one_unique_index(self) -> None:
        """新库不会因迁移产生重复唯一索引"""
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            unique_indexes = [
                r for r in
                ctx.connection.execute("PRAGMA index_list('subscriptions')").fetchall()
                if r["unique"] and r["origin"] != "pk"
            ]
            self.assertEqual(
                len(unique_indexes), 1,
                f"Expected 1 UNIQUE index on subscriptions.name, got {len(unique_indexes)}: "
                f"{[r['name'] for r in unique_indexes]}"
            )
        finally:
            ctx.close()

    def test_migration_fails_with_clear_error_on_duplicate_names(self) -> None:
        """旧库已有重名订阅时，迁移应抛出包含冲突名称的 MigrationError"""
        from app.infrastructure.db.migrations import (
            MigrationError,
            initialize_database,
        )

        conn = get_connection(":memory:", auto_init=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subscriptions ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  enabled INTEGER NOT NULL,"
            "  categories_json TEXT NOT NULL,"
            "  include_keywords_json TEXT NOT NULL,"
            "  exclude_keywords_json TEXT NOT NULL,"
            "  authors_json TEXT NOT NULL,"
            "  query_text TEXT,"
            "  sync_interval_minutes INTEGER NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  last_synced_at TEXT"
            ")"
        )
        # 插入两条同名订阅
        conn.execute(
            "INSERT INTO subscriptions (id, name, enabled, categories_json, "
            "  include_keywords_json, exclude_keywords_json, authors_json, "
            "  sync_interval_minutes, created_at, updated_at) "
            "VALUES ('id-1', 'ConflictName', 1, '[]', '[]', '[]', '[]', 60, 't', 't')"
        )
        conn.execute(
            "INSERT INTO subscriptions (id, name, enabled, categories_json, "
            "  include_keywords_json, exclude_keywords_json, authors_json, "
            "  sync_interval_minutes, created_at, updated_at) "
            "VALUES ('id-2', 'ConflictName', 1, '[]', '[]', '[]', '[]', 60, 't', 't')"
        )

        with self.assertRaises(MigrationError) as ctx:
            initialize_database(conn)

        self.assertIn("ConflictName", str(ctx.exception))
        self.assertIn("duplicate", str(ctx.exception).lower())

        conn.close()

    def test_get_connection_closes_on_migration_failure(self) -> None:
        """get_connection(auto_init=True) 在迁移失败时必须关闭连接防止泄漏"""
        import tempfile
        from pathlib import Path

        from app.infrastructure.db.migrations import MigrationError

        # 使用临时文件数据库模拟旧库 + 重名场景
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            # 创建旧库 schema 并插入重名数据（显式提交到磁盘）
            conn = get_connection(tmp_path, auto_init=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions ("
                "  id TEXT PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  enabled INTEGER NOT NULL,"
                "  categories_json TEXT NOT NULL,"
                "  include_keywords_json TEXT NOT NULL,"
                "  exclude_keywords_json TEXT NOT NULL,"
                "  authors_json TEXT NOT NULL,"
                "  query_text TEXT,"
                "  sync_interval_minutes INTEGER NOT NULL,"
                "  created_at TEXT NOT NULL,"
                "  updated_at TEXT NOT NULL,"
                "  last_synced_at TEXT"
                ")"
            )
            conn.execute(
                "INSERT INTO subscriptions (id, name, enabled, categories_json, "
                "  include_keywords_json, exclude_keywords_json, authors_json, "
                "  sync_interval_minutes, created_at, updated_at) "
                "VALUES ('id-1', 'LeakTest', 1, '[]', '[]', '[]', '[]', 60, 't', 't'),"
                "       ('id-2', 'LeakTest', 1, '[]', '[]', '[]', '[]', 60, 't', 't')"
            )
            conn.commit()
            conn.close()

            # 重新打开 → auto_init 触发迁移 → MigrationError
            with self.assertRaises(MigrationError):
                get_connection(tmp_path, auto_init=True)

            # get_connection 内部已关闭连接，文件不应被锁定：
            # 可以再次用独立连接打开同一文件
            verify_conn = sqlite3.connect(tmp_path)
            verify_conn.close()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_get_connection_normal_path_connection_is_open(self) -> None:
        """正常路径下 get_connection 返回的连接是可用的"""
        conn = get_connection(":memory:", auto_init=True)
        try:
            result = conn.execute("SELECT 1 AS n").fetchone()
            self.assertEqual(result["n"], 1)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Regression: Slice 01-04 imports still intact
# ---------------------------------------------------------------------------


class Slice01to04RegressionTests(unittest.TestCase):
    """确保 Slice 01-04 的契约未被破坏"""

    def test_models_still_importable(self) -> None:
        from app.domain.models import Paper, PaperStatus, Subscription, SyncRun
        self.assertTrue(True)

    def test_enums_still_importable(self) -> None:
        from app.domain.enums import SyncRunStatus, SyncTriggerType
        self.assertTrue(True)

    def test_dtos_still_importable(self) -> None:
        from app.application.dto import (
            PaperDetailDTO,
            PaperListItemDTO,
            SubscriptionUpsertInput,
            SyncResultDTO,
        )
        self.assertTrue(True)

    def test_repositories_still_importable(self) -> None:
        from app.infrastructure.db.repositories import (
            AppSettingsRepository,
            PaperRepository,
            PaperStatusRepository,
            SubscriptionRepository,
            SyncRunRepository,
        )
        self.assertTrue(True)

    def test_arxiv_still_importable(self) -> None:
        from app.infrastructure.arxiv import (
            ArxivClient,
            QueryInput,
            build_query,
            parse_feed,
        )
        self.assertTrue(True)

    def test_services_still_importable(self) -> None:
        from app.application.services import SubscriptionService, SyncService
        self.assertTrue(True)

    def test_domain_models_re_export_still_works(self) -> None:
        from app.domain.models import Subscription
        sub = Subscription(
            id="test",
            name="Test",
            categories=["cs.CV"],
            sync_interval_minutes=60,
        )
        self.assertEqual(sub.name, "Test")


if __name__ == "__main__":
    unittest.main()
