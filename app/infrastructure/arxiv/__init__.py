from __future__ import annotations

from app.infrastructure.arxiv.client import ArxivClient
from app.infrastructure.arxiv.exceptions import (
    ArxivDataContractError,
    ArxivError,
    ArxivParseError,
    ArxivRequestError,
)
from app.infrastructure.arxiv.parser import ArxivFetchResult, parse_feed
from app.infrastructure.arxiv.query_builder import (
    ARXIV_API_BASE_URL,
    QueryInput,
    build_query,
)

__all__ = [
    # exceptions
    "ArxivError",
    "ArxivRequestError",
    "ArxivParseError",
    "ArxivDataContractError",
    # query builder
    "ARXIV_API_BASE_URL",
    "QueryInput",
    "build_query",
    # client
    "ArxivClient",
    # parser
    "ArxivFetchResult",
    "parse_feed",
]
