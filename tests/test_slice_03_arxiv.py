from __future__ import annotations

import textwrap
import unittest
from datetime import datetime, timezone

from app.domain.models import Subscription
from app.infrastructure.arxiv import (
    ArxivClient,
    ArxivDataContractError,
    ArxivError,
    ArxivFetchResult,
    ArxivParseError,
    ArxivRequestError,
    QueryInput,
    build_query,
    parse_feed,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_subscription(
    *,
    categories: list[str] | None = None,
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    authors: list[str] | None = None,
    query_text: str | None = None,
) -> Subscription:
    return Subscription(
        id="test-sub",
        name="Test Subscription",
        categories=categories or [],
        include_keywords=include_keywords or [],
        exclude_keywords=exclude_keywords or [],
        authors=authors or [],
        query_text=query_text,
    )


_VALID_ATOM = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <title>ArXiv Query: all:transformer</title>
      <entry>
        <id>http://arxiv.org/abs/2501.01234v2</id>
        <title>  A Transformer Architecture for Vision  </title>
        <summary>  We propose a novel\ntransformer-based approach for image\nrecognition.  </summary>
        <author><name>Alice Smith</name></author>
        <author><name>Bob Jones</name></author>
        <published>2025-01-15T10:00:00Z</published>
        <updated>2025-01-20T15:30:00Z</updated>
        <arxiv:primary_category scheme="http://arxiv.org/schemas/atom"
                                 term="cs.CV"/>
        <category term="cs.CV"/>
        <category term="cs.LG"/>
        <category term="cs.CV"/>
        <link href="http://arxiv.org/abs/2501.01234v2" rel="alternate" type="text/html"/>
        <link href="http://arxiv.org/pdf/2501.01234v2" rel="related" title="pdf" type="application/pdf"/>
        <arxiv:comment>Accepted at CVPR 2025</arxiv:comment>
        <arxiv:journal_ref>CVPR 2025</arxiv:journal_ref>
        <arxiv:doi>10.1234/example.2025</arxiv:doi>
      </entry>
    </feed>
""")

_VALID_ATOM_MINIMAL = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.99999v1</id>
        <title>Minimal Paper</title>
        <summary>Minimal abstract.</summary>
        <author><name>Jane Doe</name></author>
        <published>2025-06-01T00:00:00Z</published>
        <updated>2025-06-01T00:00:00Z</updated>
        <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom"
                                 scheme="http://arxiv.org/schemas/atom"
                                 term="cs.AI"/>
        <category term="cs.AI"/>
      </entry>
    </feed>
""")

_VALID_ATOM_NO_PDF = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.55555v3</id>
        <title>Paper Without PDF</title>
        <summary>Abstract.</summary>
        <author><name>Eve Wilson</name></author>
        <published>2025-03-01T00:00:00Z</published>
        <updated>2025-03-10T00:00:00Z</updated>
        <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom"
                                 scheme="http://arxiv.org/schemas/atom"
                                 term="cs.CL"/>
        <category term="cs.CL"/>
        <link href="http://arxiv.org/abs/2501.55555v3" rel="alternate" type="text/html"/>
      </entry>
    </feed>
""")

_MALFORMED_XML = "this is <not>valid< XML >>"

_ATOM_MISSING_ID = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>No ID Paper</title>
        <summary>Abstract.</summary>
        <published>2025-01-01T00:00:00Z</published>
        <updated>2025-01-01T00:00:00Z</updated>
      </entry>
    </feed>
""")

_ATOM_MULTI_ENTRY = textwrap.dedent("""\
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
        <category term="stat.ML"/>
      </entry>
    </feed>
""")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArxivExceptionsTests(unittest.TestCase):
    def test_base_error_stores_message_and_cause(self) -> None:
        cause = ValueError("root")
        err = ArxivError("something failed", cause=cause)
        self.assertEqual(err.message, "something failed")
        self.assertIs(err.cause, cause)
        self.assertIn("something failed", str(err))
        self.assertIn("ValueError", str(err))

    def test_request_error_is_arxiv_error(self) -> None:
        self.assertIsInstance(ArxivRequestError("boom"), ArxivError)

    def test_parse_error_is_arxiv_error(self) -> None:
        self.assertIsInstance(ArxivParseError("boom"), ArxivError)

    def test_data_contract_error_is_arxiv_error(self) -> None:
        self.assertIsInstance(ArxivDataContractError("boom"), ArxivError)

    def test_error_without_cause_drops_caused_by(self) -> None:
        err = ArxivError("clean error")
        self.assertEqual(str(err), "clean error")


# ---------------------------------------------------------------------------
# Query Builder
# ---------------------------------------------------------------------------


class QueryBuilderTests(unittest.TestCase):
    def test_categories_are_or_joined(self) -> None:
        sub = _make_subscription(categories=["cs.CV", "cs.LG"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn("(cat:cs.CV OR cat:cs.LG)", query)

    def test_single_category_no_parens(self) -> None:
        sub = _make_subscription(categories=["cs.CV"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertEqual(query, "cat:cs.CV")

    def test_include_keywords_are_or_grouped(self) -> None:
        sub = _make_subscription(include_keywords=["transformer", "attention"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn('(all:"transformer" OR all:"attention")', query)

    def test_exclude_keywords_are_andnot(self) -> None:
        sub = _make_subscription(
            categories=["cs.CV"],
            exclude_keywords=["survey", "review"],
        )
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn('ANDNOT all:"survey"', query)
        self.assertIn('ANDNOT all:"review"', query)

    def test_categories_with_exclude_does_not_double_and(self) -> None:
        """P1 regression: cat + exclude must not produce 'AND ANDNOT'."""
        sub = _make_subscription(categories=["cs.CV"], exclude_keywords=["survey"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        # Must use "ANDNOT" directly, not "AND ANDNOT"
        self.assertNotIn(" AND ANDNOT", query)
        self.assertIn("cat:cs.CV ANDNOT", query)

    def test_multi_word_keywords_are_quoted(self) -> None:
        """P2 regression: multi-word include/exclude must be quoted phrases."""
        sub = _make_subscription(
            include_keywords=["vision language"],
            exclude_keywords=["low resource"],
        )
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn('all:"vision language"', query)
        self.assertIn('ANDNOT all:"low resource"', query)

    def test_authors_are_quoted_and_or_grouped(self) -> None:
        sub = _make_subscription(authors=["Alice Smith", "Bob Jones"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn('au:"Alice Smith"', query)
        self.assertIn('au:"Bob Jones"', query)
        self.assertIn(" OR ", query)

    def test_categories_and_keywords_are_and_joined(self) -> None:
        sub = _make_subscription(categories=["cs.CV"], include_keywords=["transformer"])
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertIn(" AND ", query)
        self.assertIn("cat:cs.CV", query)
        self.assertIn('all:"transformer"', query)

    def test_query_text_overrides_everything(self) -> None:
        sub = _make_subscription(
            categories=["cs.CV"],
            include_keywords=["transformer"],
            query_text="all:diffusion",
        )
        qi = QueryInput(subscription=sub)
        query, url = build_query(qi)
        self.assertEqual(query, "all:diffusion")
        self.assertNotIn("cat:cs.CV", query)

    def test_empty_subscription_raises_value_error(self) -> None:
        sub = _make_subscription()
        qi = QueryInput(subscription=sub)
        with self.assertRaises(ValueError):
            build_query(qi)

    def test_only_exclude_keywords_raises_value_error(self) -> None:
        """P1 regression: ANDNOT without a positive term is invalid."""
        sub = _make_subscription(exclude_keywords=["survey"])
        qi = QueryInput(subscription=sub)
        with self.assertRaises(ValueError) as ctx:
            build_query(qi)
        self.assertIn("exclude_keywords", str(ctx.exception))

    def test_url_contains_expected_params(self) -> None:
        sub = _make_subscription(categories=["cs.CV"])
        qi = QueryInput(subscription=sub, start=0, max_results=30)
        query, url = build_query(qi)
        self.assertIn("search_query=cat%3Acs.CV", url)
        self.assertIn("start=0", url)
        self.assertIn("max_results=30", url)
        self.assertIn("sortBy=submittedDate", url)
        self.assertIn("sortOrder=descending", url)

    def test_query_input_defaults(self) -> None:
        sub = _make_subscription(categories=["cs.AI"])
        qi = QueryInput(subscription=sub)
        self.assertEqual(qi.start, 0)
        self.assertEqual(qi.max_results, 50)
        self.assertEqual(qi.sort_by, "submittedDate")
        self.assertEqual(qi.sort_order, "descending")

    def test_query_input_rejects_invalid_sort_by(self) -> None:
        sub = _make_subscription(categories=["cs.AI"])
        with self.assertRaises(ValueError):
            QueryInput(subscription=sub, sort_by="invalidField")

    def test_query_input_rejects_invalid_sort_order(self) -> None:
        sub = _make_subscription(categories=["cs.AI"])
        with self.assertRaises(ValueError):
            QueryInput(subscription=sub, sort_order="random")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ParserTests(unittest.TestCase):
    def test_parse_single_entry_with_all_fields(self) -> None:
        result = parse_feed(_VALID_ATOM, "all:transformer")
        self.assertIsInstance(result, ArxivFetchResult)
        self.assertEqual(result.raw_entries_count, 1)
        self.assertEqual(result.query, "all:transformer")
        self.assertEqual(len(result.papers), 1)
        paper = result.papers[0]
        self.assertEqual(paper.arxiv_id, "2501.01234")
        self.assertEqual(paper.version, 2)
        self.assertEqual(paper.title, "A Transformer Architecture for Vision")
        self.assertEqual(paper.abstract, "We propose a novel transformer-based approach for image recognition.")
        self.assertEqual(paper.authors, ["Alice Smith", "Bob Jones"])
        self.assertEqual(paper.primary_category, "cs.CV")
        self.assertEqual(paper.categories, ["cs.CV", "cs.LG"])
        self.assertEqual(paper.pdf_url, "http://arxiv.org/pdf/2501.01234v2")
        self.assertEqual(paper.abs_url, "http://arxiv.org/abs/2501.01234v2")
        self.assertEqual(paper.comment, "Accepted at CVPR 2025")
        self.assertEqual(paper.journal_ref, "CVPR 2025")
        self.assertEqual(paper.doi, "10.1234/example.2025")
        self.assertEqual(paper.published_at, datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(paper.updated_at, datetime(2025, 1, 20, 15, 30, tzinfo=timezone.utc))

    def test_parse_minimal_entry(self) -> None:
        result = parse_feed(_VALID_ATOM_MINIMAL, "cat:cs.AI")
        self.assertEqual(len(result.papers), 1)
        paper = result.papers[0]
        self.assertEqual(paper.arxiv_id, "2501.99999")
        self.assertEqual(paper.version, 1)
        self.assertEqual(paper.title, "Minimal Paper")
        self.assertEqual(paper.authors, ["Jane Doe"])
        self.assertIsNone(paper.pdf_url)

    def test_parse_entry_without_pdf_link(self) -> None:
        result = parse_feed(_VALID_ATOM_NO_PDF, "cat:cs.CL")
        paper = result.papers[0]
        self.assertEqual(paper.version, 3)
        self.assertIsNone(paper.pdf_url)

    def test_parse_multi_entry_feed(self) -> None:
        result = parse_feed(_ATOM_MULTI_ENTRY, "cat:cs.*")
        self.assertEqual(result.raw_entries_count, 2)
        self.assertEqual(len(result.papers), 2)
        self.assertEqual(result.papers[0].title, "Paper One")
        self.assertEqual(result.papers[0].version, 1)
        self.assertEqual(result.papers[1].title, "Paper Two")
        self.assertEqual(result.papers[1].version, 2)
        self.assertEqual(result.papers[1].categories, ["cs.LG", "stat.ML"])

    def test_version_extraction_defaults_to_1(self) -> None:
        result = parse_feed(_VALID_ATOM_MINIMAL, "cat:cs.AI")
        self.assertEqual(result.papers[0].version, 1)

    def test_version_extraction_parses_v_suffix(self) -> None:
        result = parse_feed(_VALID_ATOM, "all:transformer")
        self.assertEqual(result.papers[0].version, 2)

    def test_malformed_xml_raises_parse_error(self) -> None:
        with self.assertRaises(ArxivParseError):
            parse_feed(_MALFORMED_XML, "bad")

    def test_missing_id_raises_data_contract_error(self) -> None:
        with self.assertRaises(ArxivDataContractError):
            parse_feed(_ATOM_MISSING_ID, "test")

    def test_parse_feed_result_has_query_fidelity(self) -> None:
        result = parse_feed(_VALID_ATOM, "all:transformer AND cat:cs.CV")
        self.assertEqual(result.query, "all:transformer AND cat:cs.CV")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ClientTests(unittest.TestCase):
    def test_non_200_response_raises_request_error(self) -> None:
        import httpx
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch.object(httpx.Client, "get", return_value=mock_response):
            client = ArxivClient(timeout=10.0)
            with self.assertRaises(ArxivRequestError) as ctx:
                client.fetch("http://export.arxiv.org/api/query?search_query=...")
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_network_error_wraps_to_request_error(self) -> None:
        import httpx
        from unittest.mock import patch

        with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("refused")):
            client = ArxivClient(timeout=5.0)
            with self.assertRaises(ArxivRequestError) as ctx:
                client.fetch("http://export.arxiv.org/api/query?search_query=...")
        self.assertIn("refused", str(ctx.exception))

    def test_client_supports_context_manager(self) -> None:
        import httpx
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<feed/>"

        with patch.object(httpx.Client, "get", return_value=mock_response):
            with ArxivClient(timeout=5.0) as client:
                result = client.fetch("http://export.arxiv.org/api/query?search_query=...")
        self.assertEqual(result, "<feed/>")


# ---------------------------------------------------------------------------
# Integration smoke test (manual / optional — requires network)
# ---------------------------------------------------------------------------


class IntegrationSmokeTests(unittest.TestCase):
    def test_real_arxiv_api_call_returns_parseable_feed(self) -> None:
        """Smoke test: hit the real arXiv API, parse results into Paper models."""
        sub = _make_subscription(categories=["cs.CV"])
        qi = QueryInput(subscription=sub, max_results=3)
        query, url = build_query(qi)

        client = ArxivClient(timeout=30.0)
        try:
            raw_xml = client.fetch(url)
        except ArxivRequestError as exc:
            self.skipTest(f"arXiv API unavailable: {exc}")

        result = parse_feed(raw_xml, query)
        self.assertIsInstance(result, ArxivFetchResult)
        self.assertGreater(result.raw_entries_count, 0)
        self.assertEqual(len(result.papers), result.raw_entries_count)
        for paper in result.papers:
            self.assertTrue(paper.arxiv_id)
            self.assertTrue(paper.title)
            self.assertEqual(paper.primary_category, "cs.CV")


if __name__ == "__main__":
    unittest.main()
