from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from app.application.dto import (
    PaperDetailDTO,
    PaperListFilters,
    PaperListItemDTO,
)
from app.application.services import PaperQueryService
from app.domain.models import Paper, PaperStatus
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.migrations import initialize_database
from app.infrastructure.db.repositories import PaperRepository, PaperStatusRepository


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
    conn = get_connection(":memory:")
    return conn


# ---------------------------------------------------------------------------
# PaperListFilters DTO Tests
# ---------------------------------------------------------------------------


class PaperListFiltersValidationTests(unittest.TestCase):
    """PaperListFilters 参数对象校验测试"""

    def test_default_construction_all_none(self) -> None:
        f = PaperListFilters()
        self.assertIsNone(f.category)
        self.assertIsNone(f.keyword)
        self.assertIsNone(f.is_starred)
        self.assertIsNone(f.is_read)
        self.assertIsNone(f.is_hidden)
        self.assertIsNone(f.author)
        self.assertIsNone(f.published_from)
        self.assertIsNone(f.published_to)
        self.assertIsNone(f.updated_from)
        self.assertIsNone(f.updated_to)
        self.assertIsNone(f.limit)

    def test_partial_construction(self) -> None:
        f = PaperListFilters(
            category="cs.CV",
            is_starred=True,
            limit=10,
        )
        self.assertEqual(f.category, "cs.CV")
        self.assertTrue(f.is_starred)
        self.assertEqual(f.limit, 10)
        self.assertIsNone(f.keyword)

    def test_limit_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            PaperListFilters(limit=0)

        with self.assertRaises(ValueError):
            PaperListFilters(limit=-5)

    def test_limit_none_is_valid(self) -> None:
        f = PaperListFilters(limit=None)
        self.assertIsNone(f.limit)

    def test_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            PaperListFilters(unknown_field="oops")

    def test_boolean_filters_accept_false(self) -> None:
        f = PaperListFilters(is_starred=False, is_read=False, is_hidden=False)
        self.assertFalse(f.is_starred)
        self.assertFalse(f.is_read)
        self.assertFalse(f.is_hidden)

    def test_datetime_fields_accept_timezone_aware(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        f = PaperListFilters(published_from=dt, updated_to=dt)
        self.assertEqual(f.published_from, dt)
        self.assertEqual(f.updated_to, dt)

    # -- P1: datetime normalization regression --------------------------------

    def test_naive_datetime_normalized_to_utc(self) -> None:
        """传入 naive datetime 时应自动按 UTC 处理，不抛 TypeError"""
        f = PaperListFilters(published_from=datetime(2026, 1, 1))
        self.assertIsNotNone(f.published_from)
        assert f.published_from is not None
        self.assertIsNotNone(f.published_from.tzinfo)
        self.assertEqual(
            f.published_from.utcoffset(),
            timezone.utc.utcoffset(None),
        )

    def test_aware_datetime_converted_to_utc(self) -> None:
        """传入带其他时区的 aware datetime 时应统一转为 UTC"""
        import datetime as dt_lib
        jst = dt_lib.timezone(dt_lib.timedelta(hours=9))
        f = PaperListFilters(published_from=datetime(2026, 1, 1, tzinfo=jst))
        assert f.published_from is not None
        self.assertEqual(
            f.published_from.utcoffset(),
            timezone.utc.utcoffset(None),
        )

    def test_datetime_none_passthrough(self) -> None:
        """None 值直接透传，表示该维度不过滤"""
        f = PaperListFilters(published_from=None, updated_to=None)
        self.assertIsNone(f.published_from)
        self.assertIsNone(f.updated_to)

    # -- P2: text normalization regression -----------------------------------

    def test_text_fields_trim_whitespace(self) -> None:
        """category / keyword / author 前后空格自动去除"""
        f = PaperListFilters(
            category=" cs.CV ",
            keyword="  transformer  ",
            author="  Alice Smith  ",
        )
        self.assertEqual(f.category, "cs.CV")
        self.assertEqual(f.keyword, "transformer")
        self.assertEqual(f.author, "Alice Smith")

    def test_text_fields_empty_to_none(self) -> None:
        """纯空白字符串统一收拢为 None（不过滤）"""
        f = PaperListFilters(
            category="   ",
            keyword="",
            author="\t\n",
        )
        self.assertIsNone(f.category)
        self.assertIsNone(f.keyword)
        self.assertIsNone(f.author)

    def test_text_fields_none_passthrough(self) -> None:
        """None 值直接透传"""
        f = PaperListFilters(category=None, keyword=None, author=None)
        self.assertIsNone(f.category)
        self.assertIsNone(f.keyword)
        self.assertIsNone(f.author)

    def test_internal_whitespace_collapsed(self) -> None:
        """文本内部多空格被压缩为单空格"""
        f = PaperListFilters(keyword="deep   learning", author="Alice\tSmith")
        self.assertEqual(f.keyword, "deep learning")
        self.assertEqual(f.author, "Alice Smith")

    # -- date range cross-field validation ---------------------------------

    def test_published_from_after_published_to_raises(self) -> None:
        """published_from > published_to 应在构造阶段被拦截"""
        with self.assertRaises(ValueError) as ctx:
            PaperListFilters(
                published_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                published_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        self.assertIn("published_from", str(ctx.exception))

    def test_updated_from_after_updated_to_raises(self) -> None:
        """updated_from > updated_to 应在构造阶段被拦截"""
        with self.assertRaises(ValueError) as ctx:
            PaperListFilters(
                updated_from=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_to=datetime(2026, 2, 1, tzinfo=timezone.utc),
            )
        self.assertIn("updated_from", str(ctx.exception))

    def test_published_from_equal_to_published_to_allowed(self) -> None:
        """published_from == published_to 是合法点区间"""
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        f = PaperListFilters(published_from=dt, published_to=dt)
        self.assertEqual(f.published_from, dt)
        self.assertEqual(f.published_to, dt)

    def test_updated_from_equal_to_updated_to_allowed(self) -> None:
        """updated_from == updated_to 是合法点区间"""
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        f = PaperListFilters(updated_from=dt, updated_to=dt)
        self.assertEqual(f.updated_from, dt)
        self.assertEqual(f.updated_to, dt)

    def test_partial_date_range_no_validation_error(self) -> None:
        """只设一端不属于非法区间，不应拦截"""
        # 只有 from，没有 to — 合法
        f1 = PaperListFilters(published_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNotNone(f1.published_from)
        self.assertIsNone(f1.published_to)

        # 只有 to，没有 from — 合法
        f2 = PaperListFilters(published_to=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(f2.published_from)
        self.assertIsNotNone(f2.published_to)


# ---------------------------------------------------------------------------
# PaperQueryService list_papers Tests
# ---------------------------------------------------------------------------


class PaperQueryServiceListPapersEmptyTests(unittest.TestCase):
    """空库与边界场景"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_empty_database_returns_empty_list(self) -> None:
        service = self._service()
        results = service.list_papers()
        self.assertEqual(results, [])

    def test_list_papers_with_none_filters_equivalent_to_default(self) -> None:
        service = self._service()
        results = service.list_papers(None)
        self.assertEqual(results, [])

    def test_list_papers_with_explicit_filters_empty_db(self) -> None:
        service = self._service()
        results = service.list_papers(PaperListFilters(category="cs.CV", limit=5))
        self.assertEqual(results, [])


class PaperQueryServiceListPapersDefaultStatusTests(unittest.TestCase):
    """仅有论文而无 paper_statuses 记录时，自动补全默认状态视图"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_paper_without_status_gets_defaults(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        service = self._service()
        results = service.list_papers()

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.arxiv_id, "2501.00001")
        self.assertEqual(item.title, "A Test Paper")
        self.assertFalse(item.is_starred)
        self.assertFalse(item.is_read)
        self.assertFalse(item.is_hidden)

    def test_paper_with_status_reflects_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        status_repo = PaperStatusRepository(self._conn)
        status_repo.upsert(
            _make_status(is_starred=True, is_read=True, note="Interesting")
        )
        self._conn.commit()

        service = self._service()
        results = service.list_papers()

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertTrue(item.is_starred)
        self.assertTrue(item.is_read)
        self.assertFalse(item.is_hidden)

    def test_mixed_papers_some_with_status_some_without(self) -> None:
        self._paper_repo.upsert(_make_paper(arxiv_id="2501.00001"))
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                title="Paper Two",
                updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
        )
        status_repo = PaperStatusRepository(self._conn)
        status_repo.upsert(
            _make_status(arxiv_id="2501.00001", is_starred=True)
        )
        # 2501.00002 has no status record
        self._conn.commit()

        service = self._service()
        results = service.list_papers()

        self.assertEqual(len(results), 2)
        # 2501.00002 newer first
        self.assertEqual(results[0].arxiv_id, "2501.00002")
        self.assertFalse(results[0].is_starred)  # default

        self.assertEqual(results[1].arxiv_id, "2501.00001")
        self.assertTrue(results[1].is_starred)  # from status


class PaperQueryServiceListPapersCategoryFilterTests(unittest.TestCase):
    """按分类过滤"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                primary_category="cs.CV",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                primary_category="cs.LG",
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                primary_category="cs.CV",
                updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

    def test_filter_by_existing_category(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(category="cs.CV"))
        self.assertEqual(len(results), 2)
        for item in results:
            self.assertEqual(item.primary_category, "cs.CV")

    def test_filter_by_nonexistent_category_returns_empty(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(category="cs.AI"))
        self.assertEqual(results, [])

    def test_no_category_filter_returns_all(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers()
        self.assertEqual(len(results), 3)


class PaperQueryServiceListPapersKeywordFilterTests(unittest.TestCase):
    """关键词过滤（标题 + 摘要，大小写不敏感）"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="Transformer Architecture",
                abstract="We propose a novel transformer design.",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                title="Graph Neural Networks",
                abstract="A survey of graph methods.",
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

    def test_keyword_match_in_title(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(keyword="transformer"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_keyword_match_in_abstract(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(keyword="survey"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00002")

    def test_keyword_case_insensitive(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(keyword="TRANSFORMER"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_keyword_no_match_returns_empty(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(keyword="reinforcement"))
        self.assertEqual(results, [])

    def test_keyword_partial_match(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(keyword="trans"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")


class PaperQueryServiceListPapersAuthorFilterTests(unittest.TestCase):
    """作者过滤（子串匹配，大小写不敏感）"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                authors=["Alice Smith", "Bob Jones"],
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                authors=["Charlie Brown"],
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

    def test_author_exact_match(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(author="Alice Smith"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_author_substring_match(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(author="Alice"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_author_case_insensitive(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(author="alice smith"))
        self.assertEqual(len(results), 1)

    def test_author_no_match(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(author="Nobody"))
        self.assertEqual(results, [])


class PaperQueryServiceListPapersStatusFilterTests(unittest.TestCase):
    """状态过滤（starred、read、hidden）"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00001", is_starred=True)
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00002", is_read=True)
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00003", is_hidden=True)
        )
        self._conn.commit()

    def test_filter_starred(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(is_starred=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_filter_not_starred(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(is_starred=False))
        self.assertEqual(len(results), 2)
        ids = {r.arxiv_id for r in results}
        self.assertNotIn("2501.00001", ids)

    def test_filter_read(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(is_read=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00002")

    def test_filter_hidden(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(is_hidden=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00003")

    def test_hidden_papers_included_by_default(self) -> None:
        """is_hidden 默认不主动过滤，隐藏论文仍出现在列表中"""
        self._seed()
        service = self._service()

        results = service.list_papers()  # no filter
        self.assertEqual(len(results), 3)

    def test_combined_status_filters(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(is_starred=True, is_read=False)
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")


class PaperQueryServiceListPapersDateRangeFilterTests(unittest.TestCase):
    """日期范围过滤"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

    def test_published_from_inclusive(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                published_from=datetime(2026, 3, 1, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(len(results), 2)
        ids = {r.arxiv_id for r in results}
        self.assertIn("2501.00002", ids)
        self.assertIn("2501.00003", ids)

    def test_published_to_inclusive(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                published_to=datetime(2026, 3, 1, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(len(results), 2)
        ids = {r.arxiv_id for r in results}
        self.assertIn("2501.00001", ids)
        self.assertIn("2501.00002", ids)

    def test_published_between(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                published_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                published_to=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00002")

    def test_updated_from(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                updated_from=datetime(2026, 5, 1, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00003")

    def test_date_range_no_match(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                published_from=datetime(2027, 1, 1, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(results, [])


class PaperQueryServiceListPapersSortAndLimitTests(unittest.TestCase):
    """排序与 limit 行为"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

    def test_default_sort_by_updated_at_desc(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers()
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].arxiv_id, "2501.00003")
        self.assertEqual(results[1].arxiv_id, "2501.00002")
        self.assertEqual(results[2].arxiv_id, "2501.00001")

    def test_limit_truncates_results(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(limit=2))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].arxiv_id, "2501.00003")
        self.assertEqual(results[1].arxiv_id, "2501.00002")

    def test_limit_one(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(limit=1))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00003")

    def test_limit_greater_than_total_returns_all(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(PaperListFilters(limit=100))
        self.assertEqual(len(results), 3)

    def test_filter_then_limit(self) -> None:
        """先过滤后截断：limit 作用于过滤后的结果集"""
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                category="cs.CV",  # all three have this
                limit=2,
            )
        )
        self.assertEqual(len(results), 2)
        # 仍按 updated_at DESC
        self.assertEqual(results[0].arxiv_id, "2501.00003")


class PaperQueryServiceListPapersCombinedFilterTests(unittest.TestCase):
    """组合过滤条件"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="Transformer in Vision",
                abstract="A transformer approach for CV tasks.",
                primary_category="cs.CV",
                categories=["cs.CV"],
                authors=["Alice"],
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
                authors=["Bob"],
                published_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 2, 5, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                title="Transformers for NLP",
                abstract="Survey of transformer models.",
                primary_category="cs.CL",
                categories=["cs.CL"],
                authors=["Alice", "Charlie"],
                published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 5, tzinfo=timezone.utc),
            )
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00001", is_starred=True)
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00003", is_starred=True)
        )
        self._conn.commit()

    def test_category_plus_keyword(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(category="cs.CV", keyword="transformer")
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_keyword_plus_author(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(keyword="transformer", author="Charlie")
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00003")

    def test_status_plus_keyword(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(is_starred=True, keyword="transformer")
        )
        self.assertEqual(len(results), 2)
        ids = {r.arxiv_id for r in results}
        self.assertIn("2501.00001", ids)
        self.assertIn("2501.00003", ids)

    def test_all_filters_combined(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_papers(
            PaperListFilters(
                category="cs.CV",
                keyword="transformer",
                is_starred=True,
            )
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")


# ---------------------------------------------------------------------------
# PaperQueryService get_paper_detail Tests
# ---------------------------------------------------------------------------


class PaperQueryServiceDetailTests(unittest.TestCase):
    """论文详情查询"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_get_paper_detail_with_status(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._status_repo.upsert(
            _make_status(is_starred=True, note="Important paper")
        )
        self._conn.commit()

        service = self._service()
        detail = service.get_paper_detail("2501.00001")

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIsInstance(detail, PaperDetailDTO)
        self.assertEqual(detail.arxiv_id, "2501.00001")
        self.assertEqual(detail.title, "A Test Paper")
        self.assertEqual(detail.abstract, "An abstract for testing.")
        self.assertEqual(detail.authors, ["Alice", "Bob"])
        self.assertEqual(detail.primary_category, "cs.CV")
        self.assertEqual(detail.categories, ["cs.CV", "cs.LG"])
        self.assertEqual(detail.latest_version, 1)
        self.assertEqual(detail.abs_url, "https://arxiv.org/abs/2501.00001")
        self.assertTrue(detail.is_starred)
        self.assertFalse(detail.is_read)
        self.assertFalse(detail.is_hidden)
        self.assertEqual(detail.note, "Important paper")
        self.assertEqual(detail.tags, [])
        self.assertIsNone(detail.rating)

    def test_get_paper_detail_without_status_gets_defaults(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        service = self._service()
        detail = service.get_paper_detail("2501.00001")

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertFalse(detail.is_starred)
        self.assertFalse(detail.is_read)
        self.assertFalse(detail.is_hidden)
        self.assertIsNone(detail.rating)
        self.assertIsNone(detail.note)
        self.assertEqual(detail.tags, [])

    def test_get_paper_detail_not_found_returns_none(self) -> None:
        service = self._service()
        detail = service.get_paper_detail("nonexistent")
        self.assertIsNone(detail)

    def test_detail_includes_all_paper_fields(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                pdf_url="https://arxiv.org/pdf/2501.00001.pdf",
                comment="Accepted at CVPR",
                journal_ref="CVPR 2026",
                doi="10.1234/example",
            )
        )
        self._conn.commit()

        service = self._service()
        detail = service.get_paper_detail("2501.00001")

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.pdf_url, "https://arxiv.org/pdf/2501.00001.pdf")
        self.assertEqual(detail.comment, "Accepted at CVPR")
        self.assertEqual(detail.journal_ref, "CVPR 2026")
        self.assertEqual(detail.doi, "10.1234/example")

    def test_detail_has_nullable_fields_as_none(self) -> None:
        self._paper_repo.upsert(_make_paper())  # no pdf_url, comment, etc.
        self._conn.commit()

        service = self._service()
        detail = service.get_paper_detail("2501.00001")

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIsNone(detail.pdf_url)
        self.assertIsNone(detail.comment)
        self.assertIsNone(detail.journal_ref)
        self.assertIsNone(detail.doi)


# ---------------------------------------------------------------------------
# PaperQueryService list_starred_papers Tests
# ---------------------------------------------------------------------------


class PaperQueryServiceListStarredTests(unittest.TestCase):
    """list_starred_papers 便利方法"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def _seed(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00002",
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00003",
                updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            )
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00001", is_starred=True)
        )
        self._status_repo.upsert(
            _make_status(arxiv_id="2501.00003", is_starred=True)
        )
        self._conn.commit()

    def test_list_starred_returns_only_starred(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_starred_papers()
        self.assertEqual(len(results), 2)
        for item in results:
            self.assertTrue(item.is_starred)

    def test_list_starred_with_limit(self) -> None:
        self._seed()
        service = self._service()

        results = service.list_starred_papers(limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00003")  # newest first

    def test_list_starred_empty_when_none_starred(self) -> None:
        self._paper_repo.upsert(_make_paper())
        self._conn.commit()

        service = self._service()
        results = service.list_starred_papers()
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Authors Preview Tests
# ---------------------------------------------------------------------------


class AuthorsPreviewTests(unittest.TestCase):
    """作者预览字符串生成"""

    def test_one_author(self) -> None:
        result = PaperQueryService._build_authors_preview(["Alice"])
        self.assertEqual(result, "Alice")

    def test_two_authors(self) -> None:
        result = PaperQueryService._build_authors_preview(["Alice", "Bob"])
        self.assertEqual(result, "Alice, Bob")

    def test_three_authors(self) -> None:
        result = PaperQueryService._build_authors_preview(
            ["Alice", "Bob", "Charlie"]
        )
        self.assertEqual(result, "Alice, Bob et al.")

    def test_many_authors(self) -> None:
        result = PaperQueryService._build_authors_preview(
            ["A", "B", "C", "D", "E"]
        )
        self.assertEqual(result, "A, B et al.")

    def test_empty_authors(self) -> None:
        result = PaperQueryService._build_authors_preview([])
        self.assertEqual(result, "")

    def test_authors_preview_in_list_item(self) -> None:
        """验证 authors_preview 已正确嵌入 PaperListItemDTO"""
        conn = _setup_db()
        try:
            paper_repo = PaperRepository(conn)
            paper_repo.upsert(
                _make_paper(authors=["Alice", "Bob", "Charlie"])
            )
            conn.commit()

            service = PaperQueryService(conn)
            results = service.list_papers()

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].authors_preview, "Alice, Bob et al.")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# AppContext Bootstrap Tests
# ---------------------------------------------------------------------------


class AppContextPaperQueryServiceTests(unittest.TestCase):
    """启动装配测试 —— PaperQueryService 集成"""

    def test_create_app_context_includes_paper_query_service(self) -> None:
        from app.main import AppContext, create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIsInstance(ctx, AppContext)
            self.assertIsInstance(ctx.paper_query_service, PaperQueryService)
        finally:
            ctx.close()

    def test_paper_query_service_is_functional_after_bootstrap(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            # 验证服务可用
            results = ctx.paper_query_service.list_papers()
            self.assertEqual(results, [])

            detail = ctx.paper_query_service.get_paper_detail("nonexistent")
            self.assertIsNone(detail)
        finally:
            ctx.close()

    def test_paper_query_service_shares_connection(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIs(
                ctx.paper_query_service._conn,
                ctx.connection,
            )
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# PaperListItemDTO Field Completeness Tests
# ---------------------------------------------------------------------------


class PaperListItemDTOFieldCompletenessTests(unittest.TestCase):
    """验证 PaperListItemDTO 包含 UI 所需的全部字段"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)
        self._status_repo = PaperStatusRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_list_item_has_required_fields(self) -> None:
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="A Paper Title",
                authors=["Alice", "Bob"],
                primary_category="cs.CV",
                categories=["cs.CV", "cs.LG"],
                published_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
                updated_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
            )
        )
        self._status_repo.upsert(
            _make_status(
                arxiv_id="2501.00001",
                is_starred=True,
                is_read=False,
                is_hidden=False,
            )
        )
        self._conn.commit()

        service = self._service()
        results = service.list_papers()

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.arxiv_id, "2501.00001")
        self.assertEqual(item.title, "A Paper Title")
        self.assertEqual(item.authors_preview, "Alice, Bob")
        self.assertEqual(item.primary_category, "cs.CV")
        self.assertEqual(item.categories, ["cs.CV", "cs.LG"])
        self.assertIsInstance(item.published_at, datetime)
        self.assertIsInstance(item.updated_at, datetime)
        self.assertTrue(item.is_starred)
        self.assertFalse(item.is_read)
        self.assertFalse(item.is_hidden)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class PaperQueryServiceEdgeCaseTests(unittest.TestCase):
    """边界场景"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    def test_paper_with_single_author(self) -> None:
        self._paper_repo.upsert(_make_paper(authors=["Solo Author"]))
        self._conn.commit()

        service = self._service()
        results = service.list_papers()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].authors_preview, "Solo Author")

    def test_empty_authors_rejected_by_domain_model(self) -> None:
        """Domain model 不允许空作者列表，确保防护层有效"""
        with self.assertRaises(ValueError):
            _make_paper(authors=[])


# ---------------------------------------------------------------------------
# P1/P2 End-to-End Regression Tests
# ---------------------------------------------------------------------------


class PaperListFiltersEndToEndRegressionTests(unittest.TestCase):
    """验证 P1/P2 修复端到端生效：naive datetime + 空白文本在真实查询中正常工作"""

    def setUp(self) -> None:
        self._conn = _setup_db()
        self._paper_repo = PaperRepository(self._conn)

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> PaperQueryService:
        return PaperQueryService(self._conn)

    # -- P1: naive datetime --------------------------------------------------

    def test_naive_datetime_in_list_papers_does_not_raise_typeerror(self) -> None:
        """复现 P1：PaperListFilters(published_from=datetime(2026,1,1)) 后
        调用 list_papers() 不再抛 TypeError"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        # 传入 naive datetime — 修复后不应抛出 TypeError
        results = self._service().list_papers(
            PaperListFilters(published_from=datetime(2026, 1, 1))
        )
        self.assertEqual(len(results), 1)

    def test_naive_datetime_compared_as_utc(self) -> None:
        """naive datetime 被按 UTC 解释，能正确过滤日期边界"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        # published_from=2026-07-01 naive → UTC → 应排除该论文
        results = self._service().list_papers(
            PaperListFilters(published_from=datetime(2026, 7, 1))
        )
        self.assertEqual(len(results), 0)

    # -- P2: whitespace text -------------------------------------------------

    def test_keyword_with_surrounding_whitespace_matches(self) -> None:
        """复现 P2：keyword='  transformer  ' 前后有空格也能匹配"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                title="Transformer Architecture",
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        results = self._service().list_papers(
            PaperListFilters(keyword="  transformer  ")
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].arxiv_id, "2501.00001")

    def test_author_with_surrounding_whitespace_matches(self) -> None:
        """复现 P2：author='  Alice Smith  ' 前后有空格也能匹配"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                authors=["Alice Smith", "Bob"],
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        results = self._service().list_papers(
            PaperListFilters(author="  Alice Smith  ")
        )
        self.assertEqual(len(results), 1)

    def test_category_with_surrounding_whitespace_matches(self) -> None:
        """复现 P2：category=' cs.CV ' 前后有空格也能匹配"""
        self._paper_repo.upsert(
            _make_paper(
                arxiv_id="2501.00001",
                primary_category="cs.CV",
                categories=["cs.CV"],
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        self._conn.commit()

        results = self._service().list_papers(
            PaperListFilters(category=" cs.CV ")
        )
        self.assertEqual(len(results), 1)


# ---------------------------------------------------------------------------
# Regression: Slice 01-05 integrity
# ---------------------------------------------------------------------------


class Slice01to05RegressionTests(unittest.TestCase):
    """确保 Slice 01-05 的契约未被破坏"""

    def test_models_still_importable(self) -> None:
        from app.domain.models import Paper, PaperStatus, Subscription, SyncRun
        self.assertTrue(True)

    def test_enums_still_importable(self) -> None:
        from app.domain.enums import SyncRunStatus, SyncTriggerType
        self.assertTrue(True)

    def test_all_dtos_still_importable(self) -> None:
        from app.application.dto import (
            PaperDetailDTO,
            PaperListFilters,
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
        from app.application.services import (
            PaperQueryService,
            SubscriptionService,
            SyncService,
        )
        self.assertTrue(True)

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

    def test_paper_status_model_still_creatable(self) -> None:
        status = PaperStatus(arxiv_id="2501.01234", is_starred=True)
        self.assertTrue(status.is_starred)
        self.assertFalse(status.is_read)

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
