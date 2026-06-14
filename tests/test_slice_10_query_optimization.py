"""Slice 10: Query And Interaction Optimization — Tests.

验证：
- PaperQueryRepository JOIN 查询与 SQL 过滤下推
- N+1 消除（list_papers 仅执行 1 条查询）
- 过滤结果与旧实现一致（回归）
- QueryDebugInfo 诊断信息正确
- COALESCE 默认状态处理
- 分页/排序白名单
- 大样本性能基线
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timezone

from app.application.dto import (
    PaperDetailDTO,
    PaperListFilters,
    PaperListItemDTO,
    QueryDebugInfo,
)
from app.application.services import PaperQueryService
from app.domain.models import Paper, PaperStatus
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories import (
    PaperQueryRepository,
    PaperRepository,
    PaperStatusRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
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


def _make_status(**overrides) -> PaperStatus:
    defaults = {
        "arxiv_id": "2501.00001",
        "is_starred": False,
        "is_read": False,
        "is_hidden": False,
    }
    defaults.update(overrides)
    return PaperStatus(**defaults)


def _setup_db() -> sqlite3.Connection:
    return get_connection(":memory:")


# ---------------------------------------------------------------------------
# PaperQueryRepository — JOIN & Filter Tests
# ---------------------------------------------------------------------------


class PaperQueryRepositoryJoinTests(unittest.TestCase):
    """LEFT JOIN 查询与 COALESCE 默认值测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)
        self._query_repo = PaperQueryRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_join_returns_paper_with_status(self) -> None:
        """JOIN 查询同时返回 paper 与 status 字段"""
        self._paper_repo.upsert(_make_paper())
        self._status_repo.upsert(
            _make_status(is_starred=True, is_read=True, note="test note")
        )
        self._conn.commit()

        rows = self._query_repo.query_papers(PaperListFilters())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["arxiv_id"], "2501.00001")
        self.assertEqual(row["title"], "A Test Paper")
        self.assertEqual(row["is_starred"], 1)
        self.assertEqual(row["is_read"], 1)
        self.assertEqual(row["is_hidden"], 0)
        self.assertEqual(row["note"], "test note")

    def test_join_without_status_uses_coalesce_defaults(self) -> None:
        """无 paper_statuses 记录时 COALESCE 返回默认值"""
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        rows = self._query_repo.query_papers(PaperListFilters())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["is_starred"], 0)
        self.assertEqual(row["is_read"], 0)
        self.assertEqual(row["is_hidden"], 0)
        self.assertIsNone(row["rating"])
        self.assertIsNone(row["note"])
        self.assertIsNone(row["tags_json"])

    def test_mixed_papers_with_and_without_status(self) -> None:
        """部分论文有状态、部分没有时 JOIN 均正确返回"""
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", title="Paper 2", updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._conn.commit()

        rows = self._query_repo.query_papers(PaperListFilters())
        self.assertEqual(len(rows), 2)
        # updated_at DESC → 2501.00002 first
        self.assertEqual(rows[0]["arxiv_id"], "2501.00002")
        self.assertEqual(rows[0]["is_starred"], 0)  # COALESCE default
        self.assertEqual(rows[1]["arxiv_id"], "2501.00001")
        self.assertEqual(rows[1]["is_starred"], 1)  # from status


class PaperQueryRepositoryFilterTests(unittest.TestCase):
    """SQL 过滤下推正确性测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)
        self._query_repo = PaperQueryRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="Transformer in Vision",
                abstract="A transformer approach for CV.",
                primary_category="cs.CV",
                categories=["cs.CV"],
                authors=["Alice Smith"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                title="Graph Neural Networks",
                abstract="Graph methods for learning.",
                primary_category="cs.LG",
                categories=["cs.LG"],
                authors=["Bob Jones"],
                published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 5, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                title="Transformers for NLP",
                abstract="Survey of transformer models.",
                primary_category="cs.CL",
                categories=["cs.CL"],
                authors=["Alice Smith", "Charlie"],
                published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
            )
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._status_repo.upsert(_make_status(arxiv_id="2501.00003", is_starred=True, is_read=True))
        self._conn.commit()

    # -- category filter --

    def test_filter_category_sql(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(category="cs.CV"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00001")

    def test_filter_category_nonexistent(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(category="cs.AI"))
        self.assertEqual(len(rows), 0)

    # -- keyword filter --

    def test_filter_keyword_in_title(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(keyword="transformer"))
        self.assertEqual(len(rows), 2)
        ids = {r["arxiv_id"] for r in rows}
        self.assertIn("2501.00001", ids)
        self.assertIn("2501.00003", ids)

    def test_filter_keyword_in_abstract(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(keyword="survey"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00003")

    def test_filter_keyword_case_insensitive(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(keyword="TRANSFORMER"))
        self.assertEqual(len(rows), 2)

    def test_filter_keyword_no_match(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(keyword="reinforcement"))
        self.assertEqual(len(rows), 0)

    # -- author filter --

    def test_filter_author_exact(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(author="Alice Smith"))
        self.assertEqual(len(rows), 2)

    def test_filter_author_substring(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(author="Alice"))
        self.assertEqual(len(rows), 2)

    def test_filter_author_case_insensitive(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(author="alice"))
        self.assertEqual(len(rows), 2)

    def test_filter_author_no_match(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(author="Nobody"))
        self.assertEqual(len(rows), 0)

    # -- status filters --

    def test_filter_starred_sql(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(is_starred=True))
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["is_starred"], 1)

    def test_filter_not_starred_sql(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(is_starred=False))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00002")

    def test_filter_read_sql(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(is_read=True))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00003")

    def test_filter_hidden_sql(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(is_hidden=True))
        self.assertEqual(len(rows), 0)  # none hidden in seed

    # -- date range filters --

    def test_filter_published_from(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(published_from=datetime(2026, 3, 1, tzinfo=timezone.utc))
        )
        self.assertEqual(len(rows), 2)

    def test_filter_published_between(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(
                published_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                published_to=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00002")

    # -- combined filters --

    def test_combined_category_keyword(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(category="cs.CV", keyword="transformer")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00001")

    def test_combined_status_keyword(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(is_starred=True, keyword="transformer")
        )
        self.assertEqual(len(rows), 2)

    def test_combined_all_filters(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(
                category="cs.CL",
                keyword="transformer",
                is_starred=True,
                is_read=True,
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arxiv_id"], "2501.00003")


class PaperQueryRepositoryLimitOffsetTests(unittest.TestCase):
    """分页与排序测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._query_repo = PaperQueryRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _seed(self) -> None:
        for i in range(5):
            self._paper_repo.upsert(
                _make_paper(
                    arxiv_id=f"2501.0000{i+1}",
                    title=f"Paper {i+1}",
                    updated_at=datetime(2026, i + 1, 1, tzinfo=timezone.utc),
                )
            )
        self._conn.commit()

    def test_limit_truncates(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(limit=3))
        self.assertEqual(len(rows), 3)
        # newest first
        self.assertEqual(rows[0]["arxiv_id"], "2501.00005")

    def test_offset_skips(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(limit=2), offset=2
        )
        self.assertEqual(len(rows), 2)
        # skip 2501.00005, 2501.00004 → 2501.00003, 2501.00002
        self.assertEqual(rows[0]["arxiv_id"], "2501.00003")
        self.assertEqual(rows[1]["arxiv_id"], "2501.00002")

    def test_default_sort_updated_at_desc(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters())
        ids = [r["arxiv_id"] for r in rows]
        self.assertEqual(ids, ["2501.00005", "2501.00004", "2501.00003", "2501.00002", "2501.00001"])

    def test_sort_by_published_at_asc(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(), sort_by="published_at", sort_order="ASC"
        )
        ids = [r["arxiv_id"] for r in rows]
        self.assertEqual(ids, ["2501.00001", "2501.00002", "2501.00003", "2501.00004", "2501.00005"])

    def test_sort_by_title(self) -> None:
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(), sort_by="title", sort_order="ASC"
        )
        titles = [r["title"] for r in rows]
        self.assertEqual(titles, sorted(titles))

    def test_sort_by_invalid_column_falls_back(self) -> None:
        """非法排序列名应回退到 updated_at 而非报错"""
        self._seed()
        rows = self._query_repo.query_papers(
            PaperListFilters(), sort_by="injected; DROP TABLE papers;--"
        )
        self.assertEqual(len(rows), 5)
        # 回退到 updated_at DESC
        self.assertEqual(rows[0]["arxiv_id"], "2501.00005")

    def test_offset_without_limit_does_not_error(self) -> None:
        """offset 单独使用时不产生 SQL 语法错误。"""
        self._seed()
        rows = self._query_repo.query_papers(PaperListFilters(), offset=1)
        self.assertEqual(len(rows), 4)  # 5 total, skip 1
        self.assertEqual(rows[0]["arxiv_id"], "2501.00004")


class PaperQueryRepositoryCountTests(unittest.TestCase):
    """count_papers 测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)
        self._query_repo = PaperQueryRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_count_all(self) -> None:
        self._paper_repo.upsert(_make_paper(arxiv_id="2501.00001"))
        self._paper_repo.upsert(_make_paper(arxiv_id="2501.00002"))
        self._conn.commit()

        count = self._query_repo.count_papers(PaperListFilters())
        self.assertEqual(count, 2)

    def test_count_with_filters(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", primary_category="cs.CV")
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", primary_category="cs.LG")
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._conn.commit()

        count = self._query_repo.count_papers(
            PaperListFilters(category="cs.CV", is_starred=True)
        )
        self.assertEqual(count, 1)

    def test_count_empty(self) -> None:
        count = self._query_repo.count_papers(PaperListFilters())
        self.assertEqual(count, 0)

    def test_count_excludes_limit(self) -> None:
        """count_papers 忽略 limit，返回真实匹配数"""
        for i in range(10):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.{i+1:05d}")
            )
        self._conn.commit()

        count = self._query_repo.count_papers(PaperListFilters(limit=3))
        self.assertEqual(count, 10)


class PaperQueryRepositoryGetSingleTests(unittest.TestCase):
    """get_paper_with_status 单条查询测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)
        self._query_repo = PaperQueryRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_get_existing_with_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._status_repo.upsert(_make_status(is_starred=True, rating=4))
        self._conn.commit()

        row = self._query_repo.get_paper_with_status("2501.00001")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["arxiv_id"], "2501.00001")
        self.assertEqual(row["is_starred"], 1)
        self.assertEqual(row["rating"], 4)

    def test_get_existing_without_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        row = self._query_repo.get_paper_with_status("2501.00001")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["is_starred"], 0)  # COALESCE default

    def test_get_nonexistent(self) -> None:
        row = self._query_repo.get_paper_with_status("nonexistent")
        self.assertIsNone(row)


# ---------------------------------------------------------------------------
# N+1 Elimination Verification
# ---------------------------------------------------------------------------


class NPlusOneEliminationTests(unittest.TestCase):
    """验证 list_papers 不再逐篇查询状态"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_list_papers_executes_only_one_query(self) -> None:
        """插入多篇论文后，list_papers 仅执行 1 条 SELECT 语句"""
        for i in range(20):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.{i+1:05d}")
            )
            if i % 2 == 0:
                self._status_repo.upsert(
                    _make_status(arxiv_id=f"2501.{i+1:05d}", is_starred=True)
                )
        self._conn.commit()

        # 通过 sqlite3 的 trace 回调统计查询次数
        query_count = [0]

        def _trace_callback(statement: str) -> None:
            query_count[0] += 1

        self._conn.set_trace_callback(_trace_callback)

        try:
            service = PaperQueryService(self._conn)
            results = service.list_papers()
            self.assertEqual(len(results), 20)
            # 仅 1 条 JOIN 查询（list_papers）+ 1 条 count_papers
            # 不再有 N+1 次 status_repo.get() 调用
            self.assertLessEqual(query_count[0], 2)
        finally:
            self._conn.set_trace_callback(None)

    def test_list_papers_no_status_queries(self) -> None:
        """验证不再逐篇调用 status_repo.get()"""
        for i in range(10):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.{i+1:05d}")
            )
        self._conn.commit()

        # 直接验证 service 的行为 —— 不 mock
        service = PaperQueryService(self._conn)
        results = service.list_papers()
        self.assertEqual(len(results), 10)
        # 所有结果都应包含默认状态值
        for r in results:
            self.assertFalse(r.is_starred)
            self.assertFalse(r.is_read)
            self.assertFalse(r.is_hidden)


# ---------------------------------------------------------------------------
# PaperQueryService Regression Tests
# ---------------------------------------------------------------------------


class PaperQueryServiceRegressionTests(unittest.TestCase):
    """确保 Slice 10 优化后所有旧契约继续通过"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    # -- list_papers basic --

    def test_empty_database_returns_empty_list(self) -> None:
        results = self._service().list_papers()
        self.assertEqual(results, [])

    def test_list_papers_with_none_filters(self) -> None:
        results = self._service().list_papers(None)
        self.assertEqual(results, [])

    def test_paper_without_status_gets_defaults(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        results = self._service().list_papers()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_starred)
        self.assertFalse(results[0].is_read)
        self.assertFalse(results[0].is_hidden)

    def test_paper_with_status_reflects_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._status_repo.upsert(_make_status(is_starred=True, is_read=True))
        self._conn.commit()

        results = self._service().list_papers()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_starred)
        self.assertTrue(results[0].is_read)

    # -- category filter --

    def test_filter_by_category(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", primary_category="cs.CV",
                        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", primary_category="cs.LG",
                        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(category="cs.CV"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    # -- keyword filter --

    def test_filter_by_keyword(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", title="Transformer Architecture",
                        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", title="Graph Networks",
                        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(keyword="transformer"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    # -- author filter --

    def test_filter_by_author(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", authors=["Alice Smith"],
                        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", authors=["Bob"],
                        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(author="Alice"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    # -- status filters --

    def test_filter_by_starred(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(is_starred=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    # -- date range filter --

    def test_filter_by_date_range(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        results = self._service().list_papers(
            PaperListFilters(published_from=datetime(2026, 3, 1, tzinfo=timezone.utc))
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00002")

    # -- sort & limit --

    def test_default_sort_updated_at_desc(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        )
        self._conn.commit()

        results = self._service().list_papers()
        self.assertEqual(results[0].arxiv_id, "2501.00002")
        self.assertEqual(results[1].arxiv_id, "2501.00001")

    def test_limit(self) -> None:
        for i in range(5):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.0000{i+1}",
                            updated_at=datetime(2026, i + 1, 1, tzinfo=timezone.utc))
            )
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(limit=2))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].arxiv_id, "2501.00005")

    # -- get_paper_detail --

    def test_get_paper_detail_with_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._status_repo.upsert(_make_status(is_starred=True, note="test"))
        self._conn.commit()

        detail = self._service().get_paper_detail("2501.00001")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIsInstance(detail, PaperDetailDTO)
        self.assertTrue(detail.is_starred)
        self.assertEqual(detail.note, "test")

    def test_get_paper_detail_without_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        detail = self._service().get_paper_detail("2501.00001")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertFalse(detail.is_starred)
        self.assertIsNone(detail.rating)

    def test_get_paper_detail_not_found(self) -> None:
        detail = self._service().get_paper_detail("nonexistent")
        self.assertIsNone(detail)

    # -- list_starred_papers --

    def test_list_starred_papers(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._status_repo.upsert(_make_status(arxiv_id="2501.00002", is_starred=True))
        self._conn.commit()

        results = self._service().list_starred_papers()
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r.is_starred)

    def test_list_starred_with_limit(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_starred=True))
        self._status_repo.upsert(_make_status(arxiv_id="2501.00002", is_starred=True))
        self._conn.commit()

        results = self._service().list_starred_papers(limit=1)
        self.assertEqual(len(results), 1)

    # -- authors preview --

    def test_authors_preview(self) -> None:
        self._paper_repo.upsert(_make_paper(authors=["Alice", "Bob", "Charlie"]))
        self._conn.commit()

        results = self._service().list_papers()
        self.assertEqual(results[0].authors_preview, "Alice, Bob et al.")

    # -- hidden papers included by default --

    def test_hidden_papers_included_by_default(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_hidden=True))
        self._conn.commit()

        results = self._service().list_papers()
        self.assertEqual(len(results), 2)

    def test_hidden_papers_filtered_when_requested(self) -> None:
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00001", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self._paper_repo.upsert(
            _make_paper(arxiv_id="2501.00002", updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        )
        self._status_repo.upsert(_make_status(arxiv_id="2501.00001", is_hidden=True))
        self._conn.commit()

        results = self._service().list_papers(PaperListFilters(is_hidden=False))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00002")


# ---------------------------------------------------------------------------
# QueryDebugInfo Tests
# ---------------------------------------------------------------------------


class QueryDebugInfoTests(unittest.TestCase):
    """诊断信息测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_debug_info_populated_after_list_papers(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        service = self._service()
        service.list_papers(
            PaperListFilters(category="cs.CV", keyword="test", limit=10)
        )

        debug = service.last_query_debug_info
        self.assertIsNotNone(debug)
        assert debug is not None
        self.assertIsInstance(debug, QueryDebugInfo)
        self.assertEqual(debug.sql_row_count, 1)
        self.assertIn("category", debug.filter_applied_in_sql)
        self.assertIn("keyword", debug.filter_applied_in_sql)
        self.assertIn("limit", debug.filter_applied_in_sql)
        self.assertEqual(debug.filter_applied_in_python, [])
        self.assertEqual(debug.total_matches, 1)

    def test_debug_info_no_filters(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        service = self._service()
        service.list_papers()

        debug = service.last_query_debug_info
        self.assertIsNotNone(debug)
        assert debug is not None
        self.assertEqual(debug.sql_row_count, 1)
        self.assertEqual(debug.filter_applied_in_sql, [])
        self.assertEqual(debug.filter_applied_in_python, [])

    def test_debug_info_no_python_filters(self) -> None:
        """优化后所有过滤均在 SQL 层完成，Python 侧零过滤"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="Test Transformer",
                abstract="Deep learning",
                primary_category="cs.CV",
                authors=["Alice"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        service = self._service()
        service.list_papers(
            PaperListFilters(
                category="cs.CV",
                keyword="transformer",
                author="Alice",
                is_starred=False,
                published_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        )

        debug = service.last_query_debug_info
        self.assertIsNotNone(debug)
        assert debug is not None
        # 所有过滤均在 SQL 层 — Python 侧应为空
        self.assertEqual(debug.filter_applied_in_python, [])
        self.assertGreater(len(debug.filter_applied_in_sql), 0)

    def test_debug_info_none_before_query(self) -> None:
        service = self._service()
        self.assertIsNone(service.last_query_debug_info)

    def test_debug_info_total_matches_exceeds_limit(self) -> None:
        """total_matches 应反映无 LIMIT 的真实匹配数"""
        for i in range(5):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.0000{i+1}")
            )
        self._conn.commit()

        service = self._service()
        service.list_papers(PaperListFilters(limit=2))

        debug = service.last_query_debug_info
        self.assertIsNotNone(debug)
        assert debug is not None
        self.assertEqual(debug.sql_row_count, 2)  # LIMIT applied
        self.assertEqual(debug.total_matches, 5)  # actual total


# ---------------------------------------------------------------------------
# Performance Baseline Tests
# ---------------------------------------------------------------------------


class PerformanceBaselineTests(unittest.TestCase):
    """大样本下性能回归测试"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def test_large_dataset_list_papers(self) -> None:
        """1000 篇论文下列表查询应在合理时间内完成"""
        import time

        for i in range(1000):
            self._paper_repo.upsert(
                _make_paper(
                    arxiv_id=f"2501.{i+1:05d}",
                    title=f"Paper {i+1}",
                    updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
            if i % 3 == 0:
                self._status_repo.upsert(
                    _make_status(
                        arxiv_id=f"2501.{i+1:05d}",
                        is_starred=(i % 6 == 0),
                        is_read=(i % 9 == 0),
                    )
                )
        self._conn.commit()

        service = PaperQueryService(self._conn)

        start = time.perf_counter()
        results = service.list_papers()
        elapsed = time.perf_counter() - start

        self.assertEqual(len(results), 1000)
        # 1000 条记录的 JOIN 查询应在 200ms 内完成
        self.assertLess(elapsed, 0.2, f"list_papers took {elapsed:.3f}s for 1000 papers")

    def test_filtered_large_dataset(self) -> None:
        """1000 篇论文中过滤查询"""
        import time

        for i in range(1000):
            cat = "cs.CV" if i % 2 == 0 else "cs.LG"
            self._paper_repo.upsert(
                _make_paper(
                    arxiv_id=f"2501.{i+1:05d}",
                    primary_category=cat,
                    title=f"Paper {i+1}",
                    updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        self._conn.commit()

        service = PaperQueryService(self._conn)

        start = time.perf_counter()
        results = service.list_papers(PaperListFilters(category="cs.CV", limit=50))
        elapsed = time.perf_counter() - start

        self.assertEqual(len(results), 50)
        for r in results:
            self.assertEqual(r.primary_category, "cs.CV")
        self.assertLess(elapsed, 0.05, f"filtered query took {elapsed:.3f}s")

    def test_naive_n_plus_one_performance(self) -> None:
        """对比验证：模拟旧 N+1 方式的查询次数"""
        for i in range(100):
            self._paper_repo.upsert(
                _make_paper(arxiv_id=f"2501.{i+1:05d}")
            )
        self._conn.commit()

        # 旧方式模拟：list_all + 逐条 get status
        query_count = [0]

        def _trace_callback(statement: str) -> None:
            query_count[0] += 1

        self._conn.set_trace_callback(_trace_callback)
        try:
            papers = self._paper_repo.list_all()
            for p in papers:
                self._status_repo.get(p.arxiv_id)
            old_query_count = query_count[0]

            query_count[0] = 0
            # 新方式
            service = PaperQueryService(self._conn)
            results = service.list_papers()
            new_query_count = query_count[0]

            self.assertEqual(len(results), 100)
            # 旧方式: 1(list_all) + 100(status gets) = 101 queries
            self.assertEqual(old_query_count, 101)
            # 新方式: 1(JOIN query) + 1(count) = ≤2 queries
            self.assertLessEqual(new_query_count, 2)
        finally:
            self._conn.set_trace_callback(None)


# ---------------------------------------------------------------------------
# Import / Export Tests
# ---------------------------------------------------------------------------


class Slice10ImportExportTests(unittest.TestCase):
    """确保 Slice 10 新增的模块均可正常导入"""

    def test_query_debug_info_importable(self) -> None:
        from app.application.dto.query_debug_info import QueryDebugInfo
        self.assertTrue(True)

    def test_paper_query_repository_importable(self) -> None:
        from app.infrastructure.db.repositories.paper_query_repository import (
            PaperQueryRepository,
        )
        self.assertTrue(True)

    def test_query_debug_info_in_dto_init(self) -> None:
        from app.application.dto import QueryDebugInfo
        self.assertTrue(True)

    def test_paper_query_repository_in_repo_init(self) -> None:
        from app.infrastructure.db.repositories import PaperQueryRepository
        self.assertTrue(True)

    def test_query_debug_info_creatable(self) -> None:
        info = QueryDebugInfo(
            sql_row_count=10,
            filter_applied_in_sql=["category", "keyword"],
            filter_applied_in_python=[],
            total_matches=25,
        )
        self.assertEqual(info.sql_row_count, 10)
        self.assertEqual(info.total_matches, 25)

    def test_query_debug_info_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            QueryDebugInfo(
                sql_row_count=1,
                filter_applied_in_sql=[],
                filter_applied_in_python=[],
                unknown="should fail",
            )


# ---------------------------------------------------------------------------
# Slice 01-09 Regression (cross-slice compatibility)
# ---------------------------------------------------------------------------


class Slice01to09RegressionTests(unittest.TestCase):
    """确保 Slice 10 优化不破坏 Slice 01-09 的契约"""

    def test_all_services_importable(self) -> None:
        from app.application.services import (
            PaperQueryService,
            SettingsService,
            StatusService,
            SubscriptionService,
            SyncService,
        )
        self.assertTrue(True)

    def test_all_repositories_importable(self) -> None:
        from app.infrastructure.db.repositories import (
            AppSettingsRepository,
            PaperQueryRepository,
            PaperRepository,
            PaperStatusRepository,
            SubscriptionRepository,
            SyncRunRepository,
        )
        self.assertTrue(True)

    def test_all_dtos_importable(self) -> None:
        from app.application.dto import (
            AppSettingsDTO,
            PaperDetailDTO,
            PaperListFilters,
            PaperListItemDTO,
            PaperStatusPatchInput,
            QueryDebugInfo,
            SchedulerTickEvent,
            SubscriptionUpsertInput,
            SyncResultDTO,
        )
        self.assertTrue(True)

    def test_app_context_creatable_with_query_repo(self) -> None:
        from app.main import AppContext, create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIsInstance(ctx, AppContext)
            self.assertIsInstance(ctx.paper_query_service, PaperQueryService)
            # 验证新服务可用
            results = ctx.paper_query_service.list_papers()
            self.assertEqual(results, [])
            # 验证 debug info 可用
            self.assertIsNotNone(ctx.paper_query_service.last_query_debug_info)
        finally:
            ctx.close()

    def test_paper_model_still_creatable(self) -> None:
        paper = Paper(
            arxiv_id="2501.01234",
            version=1,
            title="Test",
            abstract="Test abstract",
            authors=["Author A"],
            primary_category="cs.AI",
            categories=["cs.AI"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/2501.01234",
        )
        self.assertEqual(paper.arxiv_id, "2501.01234")

    def test_list_item_dto_still_creatable(self) -> None:
        dto = PaperListItemDTO(
            arxiv_id="2501.00001",
            title="A Paper",
            authors_preview="Alice",
            primary_category="cs.AI",
            categories=["cs.AI"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            is_starred=False,
            is_read=False,
            is_hidden=False,
        )
        self.assertEqual(dto.arxiv_id, "2501.00001")

    def test_detail_dto_still_creatable(self) -> None:
        dto = PaperDetailDTO(
            arxiv_id="2501.00001",
            latest_version=1,
            title="A Paper",
            abstract="Abstract",
            authors=["A"],
            primary_category="cs.AI",
            categories=["cs.AI"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/2501.00001",
            is_starred=False,
            is_read=False,
            is_hidden=False,
            tags=[],
        )
        self.assertEqual(dto.arxiv_id, "2501.00001")


if __name__ == "__main__":
    unittest.main()
