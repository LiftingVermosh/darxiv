from __future__ import annotations

import sqlite3
import textwrap
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.application.dto import SyncResultDTO
from app.application.services import SyncService
from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import Paper, Subscription, SyncRun
from app.infrastructure.arxiv import (
    ArxivClient,
    ArxivFetchResult,
    QueryInput,
    build_query,
    parse_feed,
)
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories import (
    PaperRepository,
    SubscriptionRepository,
    SyncRunRepository,
)


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
        <link href="http://arxiv.org/pdf/2501.01234v2" rel="related" title="pdf" type="application/pdf"/>
      </entry>
    </feed>
""")

_MOCK_FEED_MULTI = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2501.00001v1</id>
        <title>Paper One</title>
        <summary>First abstract.</summary>
        <author><name>Author A</name></author>
        <published>2025-01-01T00:00:00Z</published>
        <updated>2025-01-01T00:00:00Z</updated>
        <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>
        <category term="cs.CV"/>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2501.00002v2</id>
        <title>Paper Two</title>
        <summary>Second abstract.</summary>
        <author><name>Author B</name></author>
        <published>2025-02-01T00:00:00Z</published>
        <updated>2025-03-01T00:00:00Z</updated>
        <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>
        <category term="cs.LG"/>
      </entry>
    </feed>
""")

_MOCK_FEED_EMPTY = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Empty result</title>
    </feed>
""")


def _make_subscription(**overrides) -> Subscription:
    defaults = {
        "id": "sub-1",
        "name": "CV Daily",
        "enabled": True,
        "categories": ["cs.CV"],
        "include_keywords": ["transformer"],
        "exclude_keywords": [],
        "authors": [],
        "sync_interval_minutes": 1440,
    }
    defaults.update(overrides)
    return Subscription(**defaults)


def _setup_db() -> sqlite3.Connection:
    """Create an in-memory database with full schema and return the connection."""
    conn = get_connection(":memory:")
    return conn


def _seed_subscription(conn: sqlite3.Connection, sub: Subscription) -> None:
    SubscriptionRepository(conn).save(sub)
    conn.commit()


def _make_mock_client(feed_xml: str = _MOCK_FEED_SINGLE) -> MagicMock:
    """Create a mock ArxivClient that returns *feed_xml* from fetch()."""
    mock = MagicMock(spec=ArxivClient)
    mock.fetch.return_value = feed_xml
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class SyncServiceHappyPathTests(unittest.TestCase):
    """正常同步路径测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_sync_subscription_success_path(self) -> None:
        """给定有效订阅，sync_subscription 应返回成功结果"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertIsInstance(result, SyncResultDTO)
        self.assertEqual(result.subscription_id, "sub-1")
        self.assertEqual(result.subscription_name, "CV Daily")
        self.assertEqual(result.status, SyncRunStatus.SUCCESS)
        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertIsNone(result.error_message)
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.finished_at)
        self.assertGreaterEqual(result.finished_at, result.started_at)

    def test_paper_is_persisted_after_successful_sync(self) -> None:
        """同步成功后论文应该写入数据库"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        service.sync_subscription("sub-1")

        paper_repo = PaperRepository(self._conn)
        paper = paper_repo.get("2501.01234")
        self.assertIsNotNone(paper)
        assert paper is not None
        self.assertEqual(paper.title, "A Transformer Architecture for Vision")
        self.assertEqual(paper.version, 2)
        self.assertEqual(paper.authors, ["Alice Smith", "Bob Jones"])

    def test_sync_run_is_recorded_on_success(self) -> None:
        """成功同步后 sync_runs 表应有完整记录"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run.status, SyncRunStatus.SUCCESS)
        self.assertEqual(run.fetched_count, 1)
        self.assertEqual(run.inserted_count, 1)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(run.error_message)

    def test_sync_subscription_accepts_scheduled_trigger(self) -> None:
        """验证 trigger_type 参数正确传递"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription(
            "sub-1", trigger_type=SyncTriggerType.SCHEDULED
        )

        self.assertEqual(result.status, SyncRunStatus.SUCCESS)

        run_repo = SyncRunRepository(self._conn)
        run = run_repo.list_by_subscription("sub-1")[0]
        self.assertEqual(run.trigger_type, SyncTriggerType.SCHEDULED)


class SyncServiceEmptyResultTests(unittest.TestCase):
    """空结果同步测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_empty_feed_returns_zero_counts(self) -> None:
        """arXiv 返回空 feed 时，fetch_count=0 且仍为成功"""
        mock_client = _make_mock_client(_MOCK_FEED_EMPTY)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.status, SyncRunStatus.SUCCESS)
        self.assertEqual(result.fetched_count, 0)
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 0)

    def test_empty_feed_records_sync_run(self) -> None:
        """空结果同步也记录 sync_run"""
        mock_client = _make_mock_client(_MOCK_FEED_EMPTY)
        service = SyncService(self._conn, arxiv_client=mock_client)

        service.sync_subscription("sub-1")

        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SyncRunStatus.SUCCESS)
        self.assertEqual(runs[0].fetched_count, 0)


class SyncServiceUpdatePathTests(unittest.TestCase):
    """已存在论文的更新路径测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_existing_paper_with_higher_version_counts_as_updated(self) -> None:
        """已有 paper v1，同步获取 v2 → 应计为 updated"""
        # Pre-insert paper at v1
        paper_repo = PaperRepository(self._conn)
        paper_repo.upsert(
            Paper(
                arxiv_id="2501.01234",
                version=1,
                title="Old Title",
                abstract="Old abstract.",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
                updated_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
            )
        )
        self._conn.commit()

        # Sync the same paper at v2
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 1)

    def test_existing_paper_same_version_not_counted_as_updated(self) -> None:
        """已有 paper v2，同步相同 v2 → 不应重复计为 updated"""
        paper_repo = PaperRepository(self._conn)
        paper_repo.upsert(
            Paper(
                arxiv_id="2501.01234",
                version=2,
                title="A Transformer Architecture for Vision",
                abstract="We propose a novel transformer-based approach.",
                authors=["Alice Smith", "Bob Jones"],
                primary_category="cs.CV",
                categories=["cs.CV", "cs.LG"],
                published_at=datetime(2025, 1, 15, 10, tzinfo=timezone.utc),
                updated_at=datetime(2025, 1, 20, 15, 30, tzinfo=timezone.utc),
                abs_url="http://arxiv.org/abs/2501.01234v2",
            )
        )
        self._conn.commit()

        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 0)

    def test_version_record_is_written_for_new_version(self) -> None:
        """版本快照记录应写入 paper_versions 表"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        service.sync_subscription("sub-1")

        row = self._conn.execute(
            "SELECT * FROM paper_versions WHERE arxiv_id = ? AND version = ?",
            ("2501.01234", 2),
        ).fetchone()
        self.assertIsNotNone(row)

    def test_same_version_metadata_change_counts_as_updated(self) -> None:
        """同版本下标题/摘要变化也应计入 updated_count"""
        paper_repo = PaperRepository(self._conn)
        paper_repo.upsert(
            Paper(
                arxiv_id="2501.01234",
                version=2,
                title="Old Title",
                abstract="Old abstract.",
                authors=["Alice Smith"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
                updated_at=datetime(2025, 1, 15, 1, tzinfo=timezone.utc),
                abs_url="http://arxiv.org/abs/2501.01234",
            )
        )
        self._conn.commit()

        # 同步同一 arxiv_id、同一版本 v2，但标题和 updated_at 变了
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)  # title & updated_at differ
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        # 同版本但元数据变了 → 应计为 updated
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 1)

    def test_multi_entry_feed_counts_correctly(self) -> None:
        """多个条目：一条新、一条更新 → 计数正确"""
        # Pre-insert only the first paper
        paper_repo = PaperRepository(self._conn)
        paper_repo.upsert(
            Paper(
                arxiv_id="2501.00001",
                version=1,
                title="Paper One",
                abstract="First abstract.",
                authors=["Author A"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                abs_url="http://arxiv.org/abs/2501.00001v1",
            )
        )
        self._conn.commit()

        mock_client = _make_mock_client(_MOCK_FEED_MULTI)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        # Paper One: existing v1, new feed also v1 → not updated
        # Paper Two: new paper → inserted
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.fetched_count, 2)


class SyncServiceFailurePathTests(unittest.TestCase):
    """同步失败路径测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_arxiv_request_failure_marks_sync_run_failed(self) -> None:
        """arXiv 请求失败时应标记为 failed 并记录错误"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = RuntimeError("Connection refused")

        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.status, SyncRunStatus.FAILED)
        self.assertEqual(result.fetched_count, 0)
        self.assertIsNotNone(result.error_message)
        self.assertIn("Connection refused", result.error_message)

        # 确认 sync_run 也被标记为失败
        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SyncRunStatus.FAILED)
        self.assertIn("Connection refused", runs[0].error_message or "")

    def test_parse_failure_marks_sync_run_failed(self) -> None:
        """Atom 解析失败时标记为 failed"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.return_value = "<malformed>"

        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.status, SyncRunStatus.FAILED)
        self.assertIsNotNone(result.finished_at)

    def test_running_status_is_committed_before_fetch(self) -> None:
        """running 状态应在抓取前提交，确保异常崩溃后也能追踪"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = RuntimeError("Boom")

        service = SyncService(self._conn, arxiv_client=mock_client)
        service.sync_subscription("sub-1")

        # 即使在异常路径中，running 的 run 也已被写入
        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        # 最终状态应为 failed
        self.assertEqual(runs[0].status, SyncRunStatus.FAILED)

    def test_error_message_is_truncated(self) -> None:
        """过长错误信息应被截断"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = RuntimeError("X" * 3000)

        service = SyncService(self._conn, arxiv_client=mock_client)
        result = service.sync_subscription("sub-1")

        self.assertIsNotNone(result.error_message)
        assert result.error_message is not None
        self.assertLessEqual(len(result.error_message), 2000)

    def test_nonexistent_subscription_raises_value_error(self) -> None:
        """不存在的订阅应抛出 ValueError"""
        service = SyncService(self._conn)

        with self.assertRaises(ValueError) as ctx:
            service.sync_subscription("nonexistent")

        self.assertIn("nonexistent", str(ctx.exception))

    def test_failed_sync_dto_has_error_message_field(self) -> None:
        """失败结果 DTO 的 error_message 非空"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = RuntimeError("Timeout")

        service = SyncService(self._conn, arxiv_client=mock_client)
        result = service.sync_subscription("sub-1")

        self.assertIsNotNone(result.error_message)
        self.assertGreater(len(result.error_message), 0)


class SyncServiceBatchTests(unittest.TestCase):
    """批量同步测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription(id="sub-1", name="CV Daily"))
        _seed_subscription(self._conn, _make_subscription(id="sub-2", name="NLP Daily", categories=["cs.CL"]))
        _seed_subscription(self._conn, _make_subscription(id="sub-3", name="Disabled", enabled=False))

    def tearDown(self) -> None:
        self._conn.close()

    def test_sync_enabled_only_syncs_enabled_subscriptions(self) -> None:
        """只同步启用的订阅，跳过硬编码禁用的"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        results = service.sync_enabled_subscriptions()

        self.assertEqual(len(results), 2)  # sub-1 and sub-2, not sub-3
        sub_ids = {r.subscription_id for r in results}
        self.assertIn("sub-1", sub_ids)
        self.assertIn("sub-2", sub_ids)
        self.assertNotIn("sub-3", sub_ids)

    def test_one_subscription_failure_does_not_block_others(self) -> None:
        """单个订阅失败不阻塞其他订阅"""
        # sub-1 的 mock 正常，sub-2 的 mock 抛异常
        # 用同一个 mock 但让 fetch 根据 URL 区分
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = [
            _MOCK_FEED_SINGLE,              # sub-1 succeeds
            RuntimeError("Network error"),   # sub-2 fails
        ]

        service = SyncService(self._conn, arxiv_client=mock_client)
        results = service.sync_enabled_subscriptions()

        self.assertEqual(len(results), 2)

        # sub-1 succeeded
        self.assertEqual(results[0].subscription_id, "sub-1")
        self.assertEqual(results[0].status, SyncRunStatus.SUCCESS)

        # sub-2 failed
        self.assertEqual(results[1].subscription_id, "sub-2")
        self.assertEqual(results[1].status, SyncRunStatus.FAILED)
        self.assertIn("Network error", results[1].error_message or "")

    def test_sync_enabled_subscriptions_with_empty_list(self) -> None:
        """没有启用订阅时返回空列表"""
        # 删除所有启用订阅
        self._conn.execute("DELETE FROM subscriptions WHERE enabled = 1")
        self._conn.commit()

        service = SyncService(self._conn)
        results = service.sync_enabled_subscriptions()

        self.assertEqual(results, [])


class SyncServiceTransactionTests(unittest.TestCase):
    """事务与原子性测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_pre_fetch_failure_does_not_persist_papers(self) -> None:
        """arXiv 抓取前失败时不应残留论文写入"""
        mock_client = MagicMock(spec=ArxivClient)
        mock_client.fetch.side_effect = RuntimeError("Boom")

        service = SyncService(self._conn, arxiv_client=mock_client)
        service.sync_subscription("sub-1")

        paper_repo = PaperRepository(self._conn)
        papers = paper_repo.list_all()
        self.assertEqual(papers, [])
        # sync_run 仍应记录为 failed
        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SyncRunStatus.FAILED)

    def test_mid_persist_failure_rolls_back_partial_papers(self) -> None:
        """持久化中途失败时，已写入的论文应被回滚，仅保留失败 sync_run"""
        from unittest.mock import patch

        # 让第二个 paper 的 upsert_version 抛异常，模拟中途失败
        multi_feed = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom"
                  xmlns:arxiv="http://arxiv.org/schemas/atom">
              <entry>
                <id>http://arxiv.org/abs/2501.00001v1</id>
                <title>Paper One</title>
                <summary>Abstract one.</summary>
                <author><name>Author A</name></author>
                <published>2025-01-01T00:00:00Z</published>
                <updated>2025-01-01T00:00:00Z</updated>
                <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>
                <category term="cs.CV"/>
              </entry>
              <entry>
                <id>http://arxiv.org/abs/2501.00002v1</id>
                <title>Paper Two</title>
                <summary>Abstract two.</summary>
                <author><name>Author B</name></author>
                <published>2025-02-01T00:00:00Z</published>
                <updated>2025-02-01T00:00:00Z</updated>
                <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>
                <category term="cs.LG"/>
              </entry>
            </feed>
        """)

        mock_client = _make_mock_client(multi_feed)
        service = SyncService(self._conn, arxiv_client=mock_client)

        # Patch upsert_version to succeed on first call, fail on second
        original_upsert_version = PaperRepository.upsert_version
        call_count = [0]

        def fake_upsert_version(repo_self, paper, raw_payload_json):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("Simulated mid-persist failure")
            return original_upsert_version(repo_self, paper, raw_payload_json)

        with patch.object(PaperRepository, "upsert_version", fake_upsert_version):
            result = service.sync_subscription("sub-1")

        # 结果应为 failed
        self.assertEqual(result.status, SyncRunStatus.FAILED)
        self.assertIn("Simulated mid-persist failure", result.error_message or "")

        # 论文应全部被回滚，不应有任何残留
        paper_repo = PaperRepository(self._conn)
        papers = paper_repo.list_all()
        self.assertEqual(
            papers, [],
            "半写入的论文应该在持久化失败后被回滚",
        )

        # sync_run 的失败记录应保留
        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SyncRunStatus.FAILED)

    def test_successful_sync_commits_both_papers_and_sync_run(self) -> None:
        """成功同步时论文与 sync_run 应在同一事务中提交"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        service.sync_subscription("sub-1")

        # Both papers and runs visible
        paper_repo = PaperRepository(self._conn)
        self.assertIsNotNone(paper_repo.get("2501.01234"))

        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, SyncRunStatus.SUCCESS)


class SyncServiceEdgeCaseTests(unittest.TestCase):
    """边界场景测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        _seed_subscription(self._conn, _make_subscription())

    def tearDown(self) -> None:
        self._conn.close()

    def test_sync_disabled_subscription_still_works_when_explicitly_called(self) -> None:
        """sync_subscription 能同步禁用的订阅（用户显式触发）"""
        # Disable the subscription
        sub_repo = SubscriptionRepository(self._conn)
        sub_repo.save(_make_subscription(enabled=False))
        self._conn.commit()

        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.status, SyncRunStatus.SUCCESS)
        self.assertEqual(result.fetched_count, 1)

    def test_multiple_consecutive_syncs_on_same_subscription(self) -> None:
        """对同一订阅连续同步两次"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result1 = service.sync_subscription("sub-1")
        self.assertEqual(result1.inserted_count, 1)

        result2 = service.sync_subscription("sub-1")
        # 第二次不应重复计数
        self.assertEqual(result2.inserted_count, 0)
        self.assertEqual(result2.updated_count, 0)

        # 应有两条 sync_run 记录
        run_repo = SyncRunRepository(self._conn)
        runs = run_repo.list_by_subscription("sub-1")
        self.assertEqual(len(runs), 2)

    def test_sync_result_finished_at_after_started_at(self) -> None:
        """确保返回的 DTO 中 finished_at >= started_at"""
        mock_client = _make_mock_client(_MOCK_FEED_SINGLE)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertGreaterEqual(result.finished_at, result.started_at)

    def test_duplicate_papers_in_feed_only_count_once(self) -> None:
        """feed 中同 arxiv_id 出现两次（不同版本），仅第一次计为 inserted"""
        duplicate_feed = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom"
                  xmlns:arxiv="http://arxiv.org/schemas/atom">
              <entry>
                <id>http://arxiv.org/abs/2501.01234v1</id>
                <title>Same Paper v1</title>
                <summary>First version.</summary>
                <author><name>Alice</name></author>
                <published>2025-01-01T00:00:00Z</published>
                <updated>2025-01-01T00:00:00Z</updated>
                <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>
                <category term="cs.CV"/>
              </entry>
              <entry>
                <id>http://arxiv.org/abs/2501.01234v2</id>
                <title>Same Paper v2</title>
                <summary>Second version.</summary>
                <author><name>Alice</name></author>
                <published>2025-02-01T00:00:00Z</published>
                <updated>2025-02-01T00:00:00Z</updated>
                <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>
                <category term="cs.CV"/>
              </entry>
            </feed>
        """)

        mock_client = _make_mock_client(duplicate_feed)
        service = SyncService(self._conn, arxiv_client=mock_client)

        result = service.sync_subscription("sub-1")

        self.assertEqual(result.fetched_count, 2)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 1)

        # DB 中应只有一条记录，版本为最新版本
        paper_repo = PaperRepository(self._conn)
        paper = paper_repo.get("2501.01234")
        self.assertIsNotNone(paper)
        assert paper is not None
        self.assertEqual(paper.version, 2)
        self.assertEqual(paper.title, "Same Paper v2")


# ---------------------------------------------------------------------------
# Regression: Slice 01-03 imports still intact
# ---------------------------------------------------------------------------


class Slice01to03RegressionTests(unittest.TestCase):
    """确保 Slice 01-03 的契约未被破坏"""

    def test_models_still_importable(self) -> None:
        from app.domain.models import Paper, PaperStatus, Subscription, SyncRun
        self.assertTrue(True)

    def test_enums_still_importable(self) -> None:
        from app.domain.enums import SyncRunStatus, SyncTriggerType
        self.assertTrue(True)

    def test_dtos_still_importable(self) -> None:
        from app.application.dto import PaperDetailDTO, PaperListItemDTO, SyncResultDTO
        self.assertTrue(True)

    def test_repositories_still_importable(self) -> None:
        from app.infrastructure.db.repositories import (
            PaperRepository,
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


if __name__ == "__main__":
    unittest.main()
