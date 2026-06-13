from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from app.application.dto import PaperDetailDTO, PaperListItemDTO, SyncResultDTO
from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import Paper, PaperStatus, Subscription, SyncRun


class Slice01ModelTests(unittest.TestCase):
    def test_paper_normalizes_id_text_and_preserves_author_order(self) -> None:
        paper = Paper(
            arxiv_id="2501.01234v2",
            version=2,
            title="  A   Paper Title  ",
            abstract="  Multi\nline   abstract  ",
            authors=["Bob", " Alice ", "Bob"],
            primary_category="cs.CV",
            categories=["cs.CV", "cs.LG", "cs.CV"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            pdf_url=" https://arxiv.org/pdf/2501.01234 ",
            abs_url=" https://arxiv.org/abs/2501.01234v2 ",
        )

        self.assertEqual(paper.arxiv_id, "2501.01234")
        self.assertEqual(paper.title, "A Paper Title")
        self.assertEqual(paper.abstract, "Multi line abstract")
        self.assertEqual(paper.authors, ["Bob", "Alice"])
        self.assertEqual(paper.categories, ["cs.CV", "cs.LG"])

    def test_paper_rejects_invalid_id(self) -> None:
        with self.assertRaises(ValidationError):
            Paper(
                arxiv_id="bad-id",
                version=1,
                title="Valid title",
                abstract="Valid abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
            )

    def test_paper_normalizes_naive_datetimes_to_utc(self) -> None:
        paper = Paper(
            arxiv_id="2501.01234",
            version=1,
            title="Valid title",
            abstract="Valid abstract",
            authors=["Alice"],
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 2),
            abs_url="https://arxiv.org/abs/2501.01234",
        )

        self.assertEqual(paper.published_at.tzinfo, timezone.utc)
        self.assertEqual(paper.updated_at.tzinfo, timezone.utc)

    def test_subscription_normalizes_keywords_and_preserves_categories(self) -> None:
        subscription = Subscription(
            id="cv-daily",
            name="  CV Daily  ",
            enabled=True,
            categories=["cs.CV", " cs.LG "],
            include_keywords=["Transformer", " transformer "],
            exclude_keywords=["Survey", " survey "],
            authors=["Alice Smith", " Alice Smith "],
            query_text="  all:vision  ",
            sync_interval_minutes=30,
        )

        self.assertEqual(subscription.id, "cv-daily")
        self.assertEqual(subscription.name, "CV Daily")
        self.assertEqual(subscription.categories, ["cs.CV", "cs.LG"])
        self.assertEqual(subscription.include_keywords, ["transformer"])
        self.assertEqual(subscription.exclude_keywords, ["survey"])
        self.assertEqual(subscription.authors, ["Alice Smith"])
        self.assertEqual(subscription.query_text, "all:vision")

    def test_subscription_invalid_text_type_raises_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            Subscription(id=123, name="ok")

    def test_paper_status_rejects_hidden_starred(self) -> None:
        with self.assertRaises(ValidationError):
            PaperStatus(
                arxiv_id="2501.01234",
                is_hidden=True,
                is_starred=True,
            )

    def test_paper_status_normalizes_arxiv_id(self) -> None:
        status = PaperStatus(arxiv_id="2501.01234v3")
        self.assertEqual(status.arxiv_id, "2501.01234")

    def test_paper_status_normalizes_updated_at_to_utc(self) -> None:
        status = PaperStatus(
            arxiv_id="2501.01234",
            updated_at=datetime(2026, 1, 1),
        )
        self.assertEqual(status.updated_at.tzinfo, timezone.utc)

    def test_sync_run_requires_error_message_on_failure(self) -> None:
        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-1",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                status=SyncRunStatus.FAILED,
                trigger_type=SyncTriggerType.MANUAL,
            )

    def test_sync_run_rejects_invalid_time_order_and_running_finished_at(self) -> None:
        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-2",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                status=SyncRunStatus.SUCCESS,
                trigger_type=SyncTriggerType.MANUAL,
            )

        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-3",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
                status=SyncRunStatus.RUNNING,
                trigger_type=SyncTriggerType.MANUAL,
            )

    def test_sync_run_rejects_blank_identifiers_and_blank_failure_message(self) -> None:
        with self.assertRaises(ValidationError):
            SyncRun(
                id=" ",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                trigger_type=SyncTriggerType.MANUAL,
            )

        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-4",
                subscription_id=" ",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                trigger_type=SyncTriggerType.MANUAL,
            )

        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-5",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                status=SyncRunStatus.FAILED,
                trigger_type=SyncTriggerType.MANUAL,
                error_message="   ",
            )

    def test_dto_contracts_match_slice_plan(self) -> None:
        detail = PaperDetailDTO(
            arxiv_id="2501.01234",
            latest_version=1,
            title="Paper",
            abstract="Abstract",
            authors=["Alice"],
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            abs_url="https://arxiv.org/abs/2501.01234",
            is_starred=False,
            is_read=False,
            is_hidden=False,
            tags=[],
        )
        list_item = PaperListItemDTO(
            arxiv_id="2501.01234",
            title="Paper",
            authors_preview="Alice et al.",
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_starred=False,
            is_read=False,
            is_hidden=False,
        )
        result = SyncResultDTO(
            subscription_id="cv-daily",
            subscription_name="CV Daily",
            status=SyncRunStatus.SUCCESS,
            fetched_count=10,
            inserted_count=5,
            updated_count=2,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(detail.latest_version, 1)
        self.assertEqual(list_item.categories, ["cs.CV"])
        self.assertEqual(result.status, SyncRunStatus.SUCCESS)

    def test_models_and_dtos_forbid_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            Paper(
                arxiv_id="2501.01234",
                version=1,
                title="Paper",
                abstract="Abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
                unexpected="x",
            )

        with self.assertRaises(ValidationError):
            Subscription(id="cv-daily", name="CV Daily", unexpected="x")

        with self.assertRaises(ValidationError):
            PaperStatus(arxiv_id="2501.01234", unexpected="x")

        with self.assertRaises(ValidationError):
            SyncRun(
                id="run-1",
                subscription_id="cv-daily",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                trigger_type=SyncTriggerType.MANUAL,
                unexpected="x",
            )

        with self.assertRaises(ValidationError):
            PaperListItemDTO(
                arxiv_id="2501.01234",
                title="Paper",
                authors_preview="Alice et al.",
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_starred=False,
                is_read=False,
                is_hidden=False,
                unexpected="x",
            )

        with self.assertRaises(ValidationError):
            PaperDetailDTO(
                arxiv_id="2501.01234",
                latest_version=1,
                title="Paper",
                abstract="Abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
                is_starred=False,
                is_read=False,
                is_hidden=False,
                tags=[],
                unexpected="x",
            )

        with self.assertRaises(ValidationError):
            SyncResultDTO(
                subscription_id="cv-daily",
                subscription_name="CV Daily",
                status=SyncRunStatus.SUCCESS,
                fetched_count=0,
                inserted_count=0,
                updated_count=0,
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                unexpected="x",
            )

    def test_dtos_normalize_naive_datetimes_to_utc(self) -> None:
        list_item = PaperListItemDTO(
            arxiv_id="2501.01234",
            title="Paper",
            authors_preview="Alice et al.",
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
            is_starred=False,
            is_read=False,
            is_hidden=False,
        )
        detail = PaperDetailDTO(
            arxiv_id="2501.01234",
            latest_version=1,
            title="Paper",
            abstract="Abstract",
            authors=["Alice"],
            primary_category="cs.CV",
            categories=["cs.CV"],
            published_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
            abs_url="https://arxiv.org/abs/2501.01234",
            is_starred=False,
            is_read=False,
            is_hidden=False,
            tags=[],
        )
        result = SyncResultDTO(
            subscription_id="cv-daily",
            subscription_name="CV Daily",
            status=SyncRunStatus.SUCCESS,
            fetched_count=0,
            inserted_count=0,
            updated_count=0,
            started_at=datetime(2026, 1, 1),
            finished_at=datetime(2026, 1, 1),
        )

        self.assertEqual(list_item.published_at.tzinfo, timezone.utc)
        self.assertEqual(detail.updated_at.tzinfo, timezone.utc)
        self.assertEqual(result.started_at.tzinfo, timezone.utc)

    def test_dtos_reject_invalid_versions_and_ratings(self) -> None:
        with self.assertRaises(ValidationError):
            PaperDetailDTO(
                arxiv_id="2501.01234v2",
                latest_version=0,
                title="Paper",
                abstract="Abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
                is_starred=False,
                is_read=False,
                is_hidden=False,
                tags=[],
            )

        with self.assertRaises(ValidationError):
            PaperDetailDTO(
                arxiv_id="2501.01234",
                latest_version=1,
                title="Paper",
                abstract="Abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.CV"],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
                is_starred=False,
                is_read=False,
                is_hidden=False,
                rating=6,
                tags=[],
            )

        with self.assertRaises(ValidationError):
            PaperDetailDTO(
                arxiv_id="2501.01234",
                latest_version=1,
                title="Paper",
                abstract="Abstract",
                authors=["Alice"],
                primary_category="cs.CV",
                categories=["cs.LG"],
                published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                abs_url="https://arxiv.org/abs/2501.01234",
                is_starred=False,
                is_read=False,
                is_hidden=False,
                tags=[],
            )

        with self.assertRaises(ValidationError):
            PaperListItemDTO(
                arxiv_id="2501.01234",
                title="Paper",
                authors_preview="Alice et al.",
                primary_category="cs.CV",
                categories=[],
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_starred=False,
                is_read=False,
                is_hidden=False,
            )

        with self.assertRaises(ValidationError):
            PaperListItemDTO(
                arxiv_id="2501.01234",
                title="Paper",
                authors_preview="Alice et al.",
                primary_category="cs.CV",
                categories=["cs.LG"],
                published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_starred=False,
                is_read=False,
                is_hidden=False,
            )


if __name__ == "__main__":
    unittest.main()
