"""
Configuration settings for the Blocket Sniper bot.
Centralizes API endpoints, browser headers, search parameters, and filter rules.
"""

from dataclasses import dataclass, field
from typing import Dict, Set


@dataclass
class StorageConfig:
    """Database persistence configuration."""
    db_path: str = "sniper_cache.db"


@dataclass
class FilterConfig:
    """Rules and exclusions for deal evaluation."""
    min_price: int = 1500

    critical_exclusions: Set[str] = field(
        default_factory=lambda: {
            "trasig",
            
        }
    )

    # Matches battery health reports strictly below 80% (e.g. 'batteri 78%', 'batterihälsa: 75%')
    battery_sub_80_pattern: str = (
        r"(?:batteri(?:hälsa)?|battery)\D*(?:[0-7][0-9]|10)\s*%"
    )


@dataclass
class SearchConfig:
    """Blocket marketplace search parameters."""
    base_url: str = (
        "https://www.blocket.se/recommerce/forsale/search/api/search/SEARCH_ID_BAP_COMMON"
    )
    brand: str = "631"                # Apple
    category: str = "2.93.3217.39"     # Mobiltelefoner
    # 6 = iPhone 15
    # 5 = iphone 14
    model: str = "6"                  # iPhone
    retries: int = 3


@dataclass
class ClientConfig:
    """Network, rate limiting, and browser fingerprint impersonation settings."""
    max_concurrent_requests: int = 4
    search_timeout: float = 10.0
    detail_timeout: float = 8.0
    impersonate_profile: str = "chrome110"

    headers: Dict[str, str] = field(
        default_factory=lambda: {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.blocket.se/",
            "Sec-Ch-Ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
        }
    )


@dataclass
class Config:
    """Root configuration combining all sub-configurations."""
    storage: StorageConfig = field(default_factory=StorageConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    client: ClientConfig = field(default_factory=ClientConfig)


# Default shared configuration instance
DEFAULT_CONFIG = Config()
