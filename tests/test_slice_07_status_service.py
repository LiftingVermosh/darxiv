from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from app.application.dto import PaperStatusPatchInput
from app.application.services import StatusService
from app.application.services.exceptions import (
    InvalidPaperStatusError,
    PaperNotFoundError,
)
from app.domain.models import Paper, PaperStatus
from app.infrastructure.db.connection import get_connection
from app.infrastructure.db.repositories import PaperRepository


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


def _setup_db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    return conn


def _seed_paper(conn: sqlite3.Connection, **overrides) -> Paper:
    """插入一篇论文并提交事务，返回领域对象"""
    paper = _make_paper(**overrides)
    repo = PaperRepository(conn)
    repo.upsert(paper)
    conn.commit()
    return paper


# ---------------------------------------------------------------------------
# PaperStatusPatchInput DTO Tests
# ---------------------------------------------------------------------------


class PaperStatusPatchInputValidationTests(unittest.TestCase):
    """PaperStatusPatchInput 参数对象校验测试"""

    def test_default_construction_all_none(self) -> None:
        p = PaperStatusPatchInput()
        self.assertIsNone(p.is_starred)
        self.assertIsNone(p.is_read)
        self.assertIsNone(p.is_hidden)
        self.assertIsNone(p.rating)
        self.assertIsNone(p.note)
        self.assertIsNone(p.tags)

    def test_partial_construction(self) -> None:
        p = PaperStatusPatchInput(is_starred=True, rating=3)
        self.assertTrue(p.is_starred)
        self.assertEqual(p.rating, 3)
        self.assertIsNone(p.is_read)
        self.assertIsNone(p.note)

    def test_rating_must_be_between_1_and_5(self) -> None:
        with self.assertRaises(ValueError):
            PaperStatusPatchInput(rating=0)

        with self.assertRaises(ValueError):
            PaperStatusPatchInput(rating=6)

    def test_rating_none_is_valid(self) -> None:
        p = PaperStatusPatchInput(rating=None)
        self.assertIsNone(p.rating)

    def test_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            PaperStatusPatchInput(unknown_field="oops")

    def test_note_empty_string_becomes_none(self) -> None:
        p = PaperStatusPatchInput(note="")
        self.assertIsNone(p.note)

    def test_note_whitespace_becomes_none(self) -> None:
        p = PaperStatusPatchInput(note="   ")
        self.assertIsNone(p.note)

    def test_note_with_content_preserved(self) -> None:
        p = PaperStatusPatchInput(note="Interesting paper")
        self.assertEqual(p.note, "Interesting paper")

    def test_note_trim_whitespace(self) -> None:
        p = PaperStatusPatchInput(note="  hello world  ")
        self.assertEqual(p.note, "hello world")

    def test_tags_deduplication(self) -> None:
        p = PaperStatusPatchInput(tags=["tag1", "tag2", "tag1", "TAG1"])
        # case-sensitive dedup
        self.assertEqual(p.tags, ["tag1", "tag2", "TAG1"])

    def test_tags_whitespace_trim_and_empty_filter(self) -> None:
        p = PaperStatusPatchInput(tags=["  tag1  ", "", "  ", "tag2"])
        self.assertEqual(p.tags, ["tag1", "tag2"])

    def test_tags_none_passthrough(self) -> None:
        p = PaperStatusPatchInput(tags=None)
        self.assertIsNone(p.tags)

    def test_hidden_and_starred_conflict_in_patch(self) -> None:
        """在同一个 patch 中同时设置 is_hidden=True 和 is_starred=True 应报错"""
        with self.assertRaises(ValueError) as ctx:
            PaperStatusPatchInput(is_hidden=True, is_starred=True)
        self.assertIn("hidden", str(ctx.exception))

    def test_hidden_false_and_starred_true_allowed(self) -> None:
        p = PaperStatusPatchInput(is_hidden=False, is_starred=True)
        self.assertTrue(p.is_starred)
        self.assertFalse(p.is_hidden)

    def test_hidden_true_and_starred_false_allowed(self) -> None:
        p = PaperStatusPatchInput(is_hidden=True, is_starred=False)
        self.assertTrue(p.is_hidden)
        self.assertFalse(p.is_starred)


# ---------------------------------------------------------------------------
# StatusService get_status Tests
# ---------------------------------------------------------------------------


class StatusServiceGetStatusTests(unittest.TestCase):
    """状态读取"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_get_status_returns_default_when_no_record(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.get_status("2501.00001")
        self.assertIsInstance(status, PaperStatus)
        self.assertEqual(status.arxiv_id, "2501.00001")
        self.assertFalse(status.is_starred)
        self.assertFalse(status.is_read)
        self.assertFalse(status.is_hidden)
        self.assertIsNone(status.rating)
        self.assertIsNone(status.note)
        self.assertEqual(status.tags, [])

    def test_get_status_returns_existing_record(self) -> None:
        _seed_paper(self._conn)
        from app.infrastructure.db.repositories import PaperStatusRepository

        repo = PaperStatusRepository(self._conn)
        repo.upsert(
            PaperStatus(
                arxiv_id="2501.00001",
                is_starred=True,
                is_read=True,
                note="Great paper",
            )
        )
        self._conn.commit()

        service = self._service()
        status = service.get_status("2501.00001")
        self.assertTrue(status.is_starred)
        self.assertTrue(status.is_read)
        self.assertEqual(status.note, "Great paper")

    def test_get_status_does_not_require_paper_existence(self) -> None:
        """get_status 是纯读取，不检查论文是否存在（只返回默认状态）"""
        service = self._service()
        status = service.get_status("2501.99999")
        self.assertIsInstance(status, PaperStatus)
        self.assertEqual(status.arxiv_id, "2501.99999")


# ---------------------------------------------------------------------------
# StatusService set_starred Tests
# ---------------------------------------------------------------------------


class StatusServiceSetStarredTests(unittest.TestCase):
    """收藏状态开关"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_set_starred_true_on_new_status(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.set_starred("2501.00001", True)
        self.assertTrue(status.is_starred)
        # 验证持久化
        reloaded = service.get_status("2501.00001")
        self.assertTrue(reloaded.is_starred)

    def test_set_starred_false_on_existing_starred(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)
        status = service.set_starred("2501.00001", False)
        self.assertFalse(status.is_starred)

        reloaded = service.get_status("2501.00001")
        self.assertFalse(reloaded.is_starred)

    def test_set_starred_toggles_correctly(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        # Toggle on
        s1 = service.set_starred("2501.00001", True)
        self.assertTrue(s1.is_starred)

        # Toggle off
        s2 = service.set_starred("2501.00001", False)
        self.assertFalse(s2.is_starred)

        # Toggle back on
        s3 = service.set_starred("2501.00001", True)
        self.assertTrue(s3.is_starred)


# ---------------------------------------------------------------------------
# StatusService set_read Tests
# ---------------------------------------------------------------------------


class StatusServiceSetReadTests(unittest.TestCase):
    """已读状态开关"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_set_read_true(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.set_read("2501.00001", True)
        self.assertTrue(status.is_read)

        reloaded = service.get_status("2501.00001")
        self.assertTrue(reloaded.is_read)

    def test_set_read_false(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.set_read("2501.00001", True)
        status = service.set_read("2501.00001", False)
        self.assertFalse(status.is_read)

    def test_set_read_does_not_affect_other_fields(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)
        service.set_read("2501.00001", True)

        status = service.get_status("2501.00001")
        self.assertTrue(status.is_starred)
        self.assertTrue(status.is_read)
        self.assertFalse(status.is_hidden)


# ---------------------------------------------------------------------------
# StatusService set_hidden Tests
# ---------------------------------------------------------------------------


class StatusServiceSetHiddenTests(unittest.TestCase):
    """忽略/隐藏状态开关"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_set_hidden_true(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.set_hidden("2501.00001", True)
        self.assertTrue(status.is_hidden)

        reloaded = service.get_status("2501.00001")
        self.assertTrue(reloaded.is_hidden)

    def test_set_hidden_false(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.set_hidden("2501.00001", True)
        status = service.set_hidden("2501.00001", False)
        self.assertFalse(status.is_hidden)

    def test_set_hidden_true_on_starred_paper_raises(self) -> None:
        """已收藏论文不能被隐藏"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.set_hidden("2501.00001", True)
        self.assertIn("hidden", str(ctx.exception))

    def test_set_hidden_false_on_starred_paper_allowed(self) -> None:
        """取消隐藏总是允许的"""
        _seed_paper(self._conn)
        service = self._service()

        # 先设为隐藏（此时未收藏）
        service.set_hidden("2501.00001", True)

        # 取消隐藏 — 总是允许
        status = service.set_hidden("2501.00001", False)
        self.assertFalse(status.is_hidden)


# ---------------------------------------------------------------------------
# StatusService update_note Tests
# ---------------------------------------------------------------------------


class StatusServiceUpdateNoteTests(unittest.TestCase):
    """笔记/备注更新"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_update_note_set(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.update_note("2501.00001", "Very interesting")
        self.assertEqual(status.note, "Very interesting")

        reloaded = service.get_status("2501.00001")
        self.assertEqual(reloaded.note, "Very interesting")

    def test_update_note_clear_with_none(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_note("2501.00001", "Some note")
        status = service.update_note("2501.00001", None)
        self.assertIsNone(status.note)

    def test_update_note_clear_with_empty_string(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_note("2501.00001", "Some note")
        status = service.update_note("2501.00001", "")
        self.assertIsNone(status.note)

    def test_update_note_overwrite(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_note("2501.00001", "First note")
        service.update_note("2501.00001", "Updated note")

        status = service.get_status("2501.00001")
        self.assertEqual(status.note, "Updated note")

    def test_update_note_whitespace_only_returns_normalized_none(self) -> None:
        """P2 回归：update_note 返回值应与数据库一致——空格串应被规范化为 None"""
        _seed_paper(self._conn)
        service = self._service()

        returned = service.update_note("2501.00001", "   ")
        self.assertIsNone(returned.note)

        reloaded = service.get_status("2501.00001")
        self.assertIsNone(reloaded.note)


# ---------------------------------------------------------------------------
# StatusService update_rating Tests
# ---------------------------------------------------------------------------


class StatusServiceUpdateRatingTests(unittest.TestCase):
    """评分更新"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_update_rating_set(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.update_rating("2501.00001", 4)
        self.assertEqual(status.rating, 4)

        reloaded = service.get_status("2501.00001")
        self.assertEqual(reloaded.rating, 4)

    def test_update_rating_boundary_values(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        # Rating 1 (minimum)
        s1 = service.update_rating("2501.00001", 1)
        self.assertEqual(s1.rating, 1)

        # Rating 5 (maximum)
        s2 = service.update_rating("2501.00001", 5)
        self.assertEqual(s2.rating, 5)

    def test_update_rating_clear_with_none(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_rating("2501.00001", 3)
        status = service.update_rating("2501.00001", None)
        self.assertIsNone(status.rating)

    def test_update_rating_invalid_zero_raises(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.update_rating("2501.00001", 0)
        self.assertIn("Rating", str(ctx.exception))

    def test_update_rating_invalid_six_raises(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.update_rating("2501.00001", 6)
        self.assertIn("Rating", str(ctx.exception))

    def test_update_rating_invalid_negative_raises(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.update_rating("2501.00001", -1)
        self.assertIn("Rating", str(ctx.exception))


# ---------------------------------------------------------------------------
# StatusService update_tags Tests
# ---------------------------------------------------------------------------


class StatusServiceUpdateTagsTests(unittest.TestCase):
    """标签覆盖更新"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_update_tags_set(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.update_tags(
            "2501.00001", ["deep-learning", "transformer"]
        )
        self.assertEqual(status.tags, ["deep-learning", "transformer"])

        reloaded = service.get_status("2501.00001")
        self.assertEqual(reloaded.tags, ["deep-learning", "transformer"])

    def test_update_tags_deduplicates(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.update_tags(
            "2501.00001", ["tag1", "tag2", "tag1", "tag2"]
        )
        self.assertEqual(status.tags, ["tag1", "tag2"])

    def test_update_tags_filters_blank_and_whitespace(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.update_tags(
            "2501.00001", ["  tag1  ", "", "  ", "tag2"]
        )
        self.assertEqual(status.tags, ["tag1", "tag2"])

    def test_update_tags_empty_list_clears_tags(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_tags("2501.00001", ["tag1", "tag2"])
        status = service.update_tags("2501.00001", [])
        self.assertEqual(status.tags, [])

    def test_update_tags_overwrite(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.update_tags("2501.00001", ["old"])
        service.update_tags("2501.00001", ["new1", "new2"])

        status = service.get_status("2501.00001")
        self.assertEqual(status.tags, ["new1", "new2"])

    def test_update_tags_non_string_items_raises_invalid_status_error(self) -> None:
        """P2 回归：非法标签元素应抛出 InvalidPaperStatusError 而非裸 ValueError"""
        _seed_paper(self._conn)
        service = self._service()

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.update_tags("2501.00001", ["valid", 123, "also"])
        self.assertIn("tags", str(ctx.exception))

    def test_update_tags_single_string_treated_as_one_tag(self) -> None:
        """P2 回归：传入单个字符串应作为单标签处理，不被拆成字符列表"""
        _seed_paper(self._conn)
        service = self._service()

        returned = service.update_tags("2501.00001", "  tag1  ")
        self.assertEqual(returned.tags, ["tag1"])

        reloaded = service.get_status("2501.00001")
        self.assertEqual(reloaded.tags, ["tag1"])


# ---------------------------------------------------------------------------
# StatusService patch_status Tests
# ---------------------------------------------------------------------------


class StatusServicePatchStatusTests(unittest.TestCase):
    """聚合写接口"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_patch_single_field(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.patch_status(
            "2501.00001", PaperStatusPatchInput(is_starred=True)
        )
        self.assertTrue(status.is_starred)
        self.assertFalse(status.is_read)  # unchanged default

    def test_patch_multiple_fields(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        status = service.patch_status(
            "2501.00001",
            PaperStatusPatchInput(
                is_starred=True,
                is_read=True,
                rating=5,
                note="Excellent",
                tags=["important"],
            ),
        )
        self.assertTrue(status.is_starred)
        self.assertTrue(status.is_read)
        self.assertEqual(status.rating, 5)
        self.assertEqual(status.note, "Excellent")
        self.assertEqual(status.tags, ["important"])

    def test_patch_none_fields_preserve_existing_values(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        # First set some fields
        service.patch_status(
            "2501.00001",
            PaperStatusPatchInput(is_starred=True, rating=3, note="hello"),
        )

        # Now patch only is_read — other fields unchanged
        status = service.patch_status(
            "2501.00001",
            PaperStatusPatchInput(is_read=True),
        )
        self.assertTrue(status.is_starred)  # preserved
        self.assertTrue(status.is_read)  # new
        self.assertEqual(status.rating, 3)  # preserved
        self.assertEqual(status.note, "hello")  # preserved

    def test_patch_none_preserves_existing_note(self) -> None:
        """None 表示"不修改"——已有 note 应保持原值"""
        _seed_paper(self._conn)
        service = self._service()

        service.update_note("2501.00001", "some note")
        status = service.patch_status(
            "2501.00001", PaperStatusPatchInput(note=None)
        )
        self.assertEqual(status.note, "some note")  # preserved

    def test_patch_none_preserves_existing_rating(self) -> None:
        """None 表示"不修改"——已有 rating 应保持原值"""
        _seed_paper(self._conn)
        service = self._service()

        service.update_rating("2501.00001", 4)
        status = service.patch_status(
            "2501.00001", PaperStatusPatchInput(rating=None)
        )
        self.assertEqual(status.rating, 4)  # preserved

    def test_patch_hidden_starred_conflict(self) -> None:
        """在 patch 中 is_hidden=True 与已有 is_starred=True 冲突"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.patch_status(
                "2501.00001",
                PaperStatusPatchInput(is_hidden=True),
            )
        self.assertIn("hidden", str(ctx.exception))

    def test_patch_hidden_false_allowed_on_starred(self) -> None:
        """patch 中 is_hidden=False（取消隐藏）在 starred 论文上允许"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)
        status = service.patch_status(
            "2501.00001",
            PaperStatusPatchInput(is_hidden=False),
        )
        self.assertFalse(status.is_hidden)

    def test_patch_tags_single_string_treated_as_one_tag(self) -> None:
        """patch_status 的 tags 也接受字符串，与 update_tags 语义一致"""
        _seed_paper(self._conn)
        service = self._service()

        status = service.patch_status(
            "2501.00001",
            PaperStatusPatchInput(tags="  single-tag  "),
        )
        self.assertEqual(status.tags, ["single-tag"])


# ---------------------------------------------------------------------------
# StatusService PaperNotFound Tests
# ---------------------------------------------------------------------------


class StatusServicePaperNotFoundTests(unittest.TestCase):
    """所有写操作在论文不存在时应拒绝"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_set_starred_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError) as ctx:
            service.set_starred("nonexistent", True)
        self.assertIn("nonexistent", str(ctx.exception))

    def test_set_read_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.set_read("nonexistent", True)

    def test_set_hidden_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.set_hidden("nonexistent", True)

    def test_update_note_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.update_note("nonexistent", "note")

    def test_update_rating_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.update_rating("nonexistent", 3)

    def test_update_tags_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.update_tags("nonexistent", ["tag"])

    def test_patch_status_nonexistent_paper_raises(self) -> None:
        service = self._service()
        with self.assertRaises(PaperNotFoundError):
            service.patch_status(
                "nonexistent",
                PaperStatusPatchInput(is_starred=True),
            )


# ---------------------------------------------------------------------------
# StatusService UpdatedAt Tests
# ---------------------------------------------------------------------------


class StatusServiceUpdatedAtTests(unittest.TestCase):
    """updated_at 时间戳维护"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_updated_at_is_set_on_write(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        before = datetime.now(timezone.utc)
        status = service.set_starred("2501.00001", True)
        after = datetime.now(timezone.utc)

        self.assertIsNotNone(status.updated_at)
        self.assertGreaterEqual(status.updated_at, before)
        self.assertLessEqual(status.updated_at, after)

    def test_updated_at_refreshes_on_each_write(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        s1 = service.set_starred("2501.00001", True)
        # 稍等确保时间戳不同
        import time
        time.sleep(0.01)

        s2 = service.set_read("2501.00001", True)

        self.assertGreater(s2.updated_at, s1.updated_at)

    def test_updated_at_changes_on_patch(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        s1 = service.patch_status(
            "2501.00001", PaperStatusPatchInput(is_starred=True)
        )

        import time
        time.sleep(0.01)

        s2 = service.patch_status(
            "2501.00001", PaperStatusPatchInput(is_read=True)
        )

        self.assertGreater(s2.updated_at, s1.updated_at)


# ---------------------------------------------------------------------------
# StatusService Hidden-Starred Conflict Tests (cross-method)
# ---------------------------------------------------------------------------


class StatusServiceHiddenStarredConflictTests(unittest.TestCase):
    """跨方法的 hidden + starred 冲突校验"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_set_starred_true_on_hidden_paper_raises(self) -> None:
        """已隐藏论文不能被收藏"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_hidden("2501.00001", True)

        with self.assertRaises(InvalidPaperStatusError) as ctx:
            service.set_starred("2501.00001", True)
        self.assertIn("hidden", str(ctx.exception))

    def test_set_starred_false_on_hidden_paper_allowed(self) -> None:
        """取消收藏总是允许"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_hidden("2501.00001", True)

        # 取消收藏（虽然是 False，原来也是 False）不冲突
        status = service.set_starred("2501.00001", False)
        self.assertFalse(status.is_starred)

    def test_unhide_then_star_allowed(self) -> None:
        """先取消隐藏，再收藏 — 合法"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_hidden("2501.00001", True)
        service.set_hidden("2501.00001", False)
        status = service.set_starred("2501.00001", True)
        self.assertTrue(status.is_starred)
        self.assertFalse(status.is_hidden)

    def test_unstar_then_hide_allowed(self) -> None:
        """先取消收藏，再隐藏 — 合法"""
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)
        service.set_starred("2501.00001", False)
        status = service.set_hidden("2501.00001", True)
        self.assertTrue(status.is_hidden)
        self.assertFalse(status.is_starred)


# ---------------------------------------------------------------------------
# StatusService Persistence Round-Trip Tests
# ---------------------------------------------------------------------------


class StatusServicePersistenceRoundTripTests(unittest.TestCase):
    """验证状态变更确实持久化到数据库"""

    def setUp(self) -> None:
        self._conn = _setup_db()

    def tearDown(self) -> None:
        self._conn.close()

    def _service(self) -> StatusService:
        return StatusService(self._conn)

    def test_status_persisted_across_service_instances(self) -> None:
        """不同 StatusService 实例（共享同一连接）能看到彼此写入的数据"""
        _seed_paper(self._conn)
        svc1 = StatusService(self._conn)
        svc1.set_starred("2501.00001", True)

        svc2 = StatusService(self._conn)
        status = svc2.get_status("2501.00001")
        self.assertTrue(status.is_starred)

    def test_all_fields_round_trip(self) -> None:
        _seed_paper(self._conn)
        service = self._service()

        service.set_starred("2501.00001", True)
        service.set_read("2501.00001", True)
        service.update_rating("2501.00001", 4)
        service.update_note("2501.00001", "Test note")
        service.update_tags("2501.00001", ["ai", "ml"])

        status = service.get_status("2501.00001")
        self.assertTrue(status.is_starred)
        self.assertTrue(status.is_read)
        self.assertFalse(status.is_hidden)
        self.assertEqual(status.rating, 4)
        self.assertEqual(status.note, "Test note")
        self.assertEqual(status.tags, ["ai", "ml"])


# ---------------------------------------------------------------------------
# AppContext Bootstrap Tests
# ---------------------------------------------------------------------------


class AppContextStatusServiceTests(unittest.TestCase):
    """启动装配测试 —— StatusService 集成"""

    def test_create_app_context_includes_status_service(self) -> None:
        from app.main import AppContext, create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIsInstance(ctx, AppContext)
            self.assertIsInstance(ctx.status_service, StatusService)
        finally:
            ctx.close()

    def test_status_service_is_functional_after_bootstrap(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            # 验证 get_status 对不存在论文返回默认值（使用有效 arxiv_id 格式）
            status = ctx.status_service.get_status("2501.99999")
            self.assertIsInstance(status, PaperStatus)
            self.assertFalse(status.is_starred)
        finally:
            ctx.close()

    def test_status_service_shares_connection(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            self.assertIs(
                ctx.status_service._conn,
                ctx.connection,
            )
        finally:
            ctx.close()

    def test_status_service_write_through_app_context(self) -> None:
        from app.main import create_app_context

        ctx = create_app_context(":memory:")
        try:
            # 需要先有论文才能写状态
            repo = PaperRepository(ctx.connection)
            repo.upsert(_make_paper())
            ctx.connection.commit()

            status = ctx.status_service.set_starred("2501.00001", True)
            self.assertTrue(status.is_starred)
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Regression: Slice 01-06 integrity
# ---------------------------------------------------------------------------


class Slice01to06RegressionTests(unittest.TestCase):
    """确保 Slice 01-06 的契约未被破坏"""

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
            PaperStatusPatchInput,
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
            StatusService,
            SubscriptionService,
            SyncService,
        )
        self.assertTrue(True)

    def test_exceptions_still_importable(self) -> None:
        from app.application.services.exceptions import (
            DuplicateSubscriptionNameError,
            InvalidPaperStatusError,
            InvalidSubscriptionRuleError,
            PaperNotFoundError,
            StatusServiceError,
            SubscriptionNotFoundError,
            SubscriptionServiceError,
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

    def test_hidden_starred_conflict_still_enforced_by_model(self) -> None:
        """PaperStatus 模型本身的 is_hidden+is_starred 冲突校验仍在"""
        with self.assertRaises(ValueError):
            PaperStatus(arxiv_id="2501.00001", is_hidden=True, is_starred=True)


if __name__ == "__main__":
    unittest.main()
