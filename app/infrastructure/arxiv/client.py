from __future__ import annotations

import httpx

from app.infrastructure.arxiv.exceptions import ArxivRequestError
from app.infrastructure.arxiv.query_builder import ARXIV_API_BASE_URL

_DEFAULT_USER_AGENT = "PaperResearch/0.1"


class ArxivClient:
    """arXiv API 的轻量 HTTP 客户端

    Args:
        base_url: arXiv API 基地址
        timeout: 请求超时（秒）
        user_agent: User-Agent 头，arXiv 要求标识身份
    """

    def __init__(
        self,
        base_url: str = ARXIV_API_BASE_URL,
        timeout: float = 30.0,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    def fetch(self, url: str) -> str:
        """获取原始 Atom XML 文本

        Args:
            url: 完整的 arXiv API 请求 URL

        Returns:
            Atom feed 的原始 XML 字符串

        Raises:
            ArxivRequestError: 网络错误、超时或非 200 响应
        """
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ArxivRequestError(
                f"arXiv API request failed: {exc}",
                cause=exc,
            ) from exc

        if response.status_code != 200:
            body_excerpt = response.text[:500]
            raise ArxivRequestError(
                f"arXiv API returned HTTP {response.status_code}: {body_excerpt}",
            )

        return response.text

    def close(self) -> None:
        """关闭底层 HTTP 客户端"""
        self._client.close()

    def __enter__(self) -> "ArxivClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()
