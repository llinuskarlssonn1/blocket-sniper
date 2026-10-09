"""
Network client for interacting with Blocket endpoints.
Handles browser fingerprint spoofing, concurrency limiting, and HTML parsing.
"""

import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional
from curl_cffi import requests

from blocket_sniper.config import DEFAULT_CONFIG, ClientConfig, SearchConfig

logger = logging.getLogger("BlocketSniper.Client")


class BlocketIngestor:
    """
    Asynchronous network engine handling browser fingerprint spoofing,
    session pooling, rate-limiting, and error handling.
    """

    def __init__(
        self,
        max_concurrent_requests: Optional[int] = None,
        client_config: Optional[ClientConfig] = None,
        search_config: Optional[SearchConfig] = None,
    ) -> None:
        self.client_config = client_config or DEFAULT_CONFIG.client
        self.search_config = search_config or DEFAULT_CONFIG.search

        concurrency = (
            max_concurrent_requests
            if max_concurrent_requests is not None
            else self.client_config.max_concurrent_requests
        )

        self.session = requests.AsyncSession(
            impersonate=self.client_config.impersonate_profile
        )
        self.semaphore = asyncio.Semaphore(concurrency)
        self.headers = dict(self.client_config.headers)

    async def fetch_search_page(
        self,
        brand: Optional[str] = None,
        category: Optional[str] = None,
        model: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query the Blocket BAP search API for listings matching criteria.
        Retries on rate-limiting with exponential backoff.
        """
        base_url = self.search_config.base_url
        params = {
            "brand": brand or self.search_config.brand,
            "product_category": category or self.search_config.category,
            "mobile_model": model or self.search_config.model,
        }
        max_retries = retries if retries is not None else self.search_config.retries

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.session.get(
                    url=base_url,
                    headers=self.headers,
                    params=params,
                    timeout=self.client_config.search_timeout,
                )

                if response.status_code == 200:
                    payload = response.json()
                    return payload.get("docs", [])
                elif response.status_code == 429:
                    backoff = 2**attempt
                    logger.warning("Rate limited (429). Exponential backoff: %ds", backoff)
                    await asyncio.sleep(backoff)
                elif response.status_code == 403:
                    logger.error("403 Forbidden: WAF blocked connection.")
                    return []
                else:
                    logger.warning(
                        "Search request failed with status: %d (attempt %d/%d)",
                        response.status_code,
                        attempt,
                        max_retries,
                    )
            except Exception as e:
                logger.error("Network fault during search: %s", e)
                await asyncio.sleep(1)

        return []

    async def fetch_ad_description(self, ad_id: str, url: Optional[str] = None) -> str:
        """
        Fetch ad detail page and extract the full description from SSR hydration state or JSON payload.
        """
        target_url = url or f"https://www.blocket.se/recommerce/forsale/item/{ad_id}"

        async with self.semaphore:
            try:
                response = await self.session.get(
                    target_url,
                    headers=self.headers,
                    timeout=self.client_config.detail_timeout,
                )
                if response.status_code == 404 and "recommerce" in target_url:
                    # Fallback to legacy URL format if recommerce returns 404
                    fallback_url = f"https://www.blocket.se/annons/{ad_id}"
                    response = await self.session.get(
                        fallback_url,
                        headers=self.headers,
                        timeout=self.client_config.detail_timeout,
                    )

                if response.status_code != 200:
                    logger.warning("Ad %s returned HTTP status %d at %s", ad_id, response.status_code, target_url)
                    return ""

                # 1. Try parsing JSON directly (recommerce endpoint returns pure JSON when requested with API headers)
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        desc = payload.get("itemData", {}).get("description")
                        if desc:
                            return desc.strip()
                except Exception:
                    pass

                # 2. Fallback to HTML extraction
                return self._extract_description_from_html(response.text)

            except Exception as e:
                logger.warning("Detail fetch error on ad %s: %s", ad_id, e)

        return ""

    @staticmethod
    def _extract_description_from_html(html_content: str) -> str:
        """
        Target description from HTML/SSR hydration tags, avoiding empty image gallery descriptions.
        """
        if not html_content:
            return ""

        # Target itemData or adViewTypeLabel description
        match = re.search(r'"adViewTypeLabel":"[^"]*","description":"(.*?)"', html_content, re.DOTALL)
        if not match:
            # Target escaped JSON string injected into Next.js/SSR hydration tags
            match = re.search(r'\\"description\\":\\"(.*?)\\"', html_content)
        if not match:
            # Secondary fallback: non-empty description
            match = re.search(r'"description":"([^"]+)"', html_content)

        if match:
            raw_desc = match.group(1)
            try:
                # Resolve escaped unicode (e.g. \u00e4) and special chars
                decoded_desc = raw_desc.encode("utf-8").decode("unicode_escape")
            except Exception:
                decoded_desc = raw_desc
            return html.unescape(decoded_desc).strip()

        return ""

    async def close(self) -> None:
        """Close client session."""
        await self.session.close()

    async def __aenter__(self) -> "BlocketIngestor":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
