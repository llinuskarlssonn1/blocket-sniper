"""
Filtering engine for marketplace ads.
Applies deterministic, zero-cost rules to discard defectives, parts-only sales,
accessories, and degraded battery health before downstream processing.
"""

import re
from typing import Optional, Set

from blocket_sniper.config import DEFAULT_CONFIG, FilterConfig
from blocket_sniper.models import Listing


class DeterministicFilter:
    """
    Deterministic rule engine. Discards defectives, parts-only sales, and irrelevant
    postings before secondary HTTP calls or LLM analysis.
    """

    CRITICAL_EXCLUSIONS: Set[str] = DEFAULT_CONFIG.filter.critical_exclusions

    # Matches battery health reports strictly below 80% (e.g. 'batteri 78%', 'batterihälsa: 75%')
    BATTERY_SUB_80_REGEX = re.compile(
        DEFAULT_CONFIG.filter.battery_sub_80_pattern,
        re.IGNORECASE,
    )

    def __init__(self, config: Optional[FilterConfig] = None) -> None:
        cfg = config or DEFAULT_CONFIG.filter
        self.min_price = cfg.min_price
        self.exclusions = cfg.critical_exclusions
        self.battery_regex = re.compile(cfg.battery_sub_80_pattern, re.IGNORECASE)

    @classmethod
    def passes_summary_check(
        cls,
        heading: str,
        price: Optional[int],
        min_price: int = 1500,
    ) -> bool:
        """
        Fast title and price check.
        Rejects accessories, boxes, parts, and items below minimum price threshold.
        """
        if not heading:
            return False

        clean_heading = heading.lower()

        # Reject accessories, boxes, and junk pricing
        if price is not None and price < min_price:
            return False

        return not any(term in clean_heading for term in cls.CRITICAL_EXCLUSIONS)

    @classmethod
    def passes_detail_check(cls, description: str) -> bool:
        """
        Deep description check.
        Rejects descriptions mentioning flaws, lockouts, or sub-80% battery health.
        """
        if not description:
            return False

        clean_desc = description.lower()

        if any(term in clean_desc for term in cls.CRITICAL_EXCLUSIONS):
            return False

        if cls.BATTERY_SUB_80_REGEX.search(clean_desc):
            return False

        return True

    def check_summary(self, listing: Listing) -> bool:
        """Evaluate a Listing instance using instance-level configuration."""
        if not listing.heading:
            return False

        clean_heading = listing.heading.lower()
        if listing.price is not None and listing.price < self.min_price:
            return False

        return not any(term in clean_heading for term in self.exclusions)

    def check_detail(self, listing: Listing) -> bool:
        """Evaluate Listing description using instance-level configuration."""
        if not listing.description:
            return False

        clean_desc = listing.description.lower()
        if any(term in clean_desc for term in self.exclusions):
            return False

        if self.battery_regex.search(clean_desc):
            return False

        return True
