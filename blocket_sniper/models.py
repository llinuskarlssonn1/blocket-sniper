"""
Data models for Blocket Sniper.
Provides strongly typed structures for marketplace listings.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Listing:
    """Represents a marketplace advertisement on Blocket."""

    id: str
    heading: str
    price: Optional[int] = None
    location: Optional[str] = None
    url: str = ""
    description: str = ""
    passed_filter: bool = False

    @classmethod
    def from_api_doc(cls, doc: Dict[str, Any]) -> Optional["Listing"]:
        """
        Construct a Listing instance from raw JSON doc returned by the search API.
        Returns None if no valid ID is found.
        """
        raw_id = doc.get("id") or doc.get("ad_id")
        if not raw_id:
            return None

        ad_id = str(raw_id).strip()
        if not ad_id:
            return None

        # Price can be nested: {"amount": 5000} or integer
        price_raw = doc.get("price")
        price: Optional[int] = None
        if isinstance(price_raw, dict):
            price_val = price_raw.get("amount")
        else:
            price_val = price_raw

        if price_val is not None:
            try:
                price = int(price_val)
            except (ValueError, TypeError):
                price = None

        heading = doc.get("heading") or ""
        location = doc.get("location")
        canonical_url = doc.get("canonical_url")
        url = canonical_url or f"https://www.blocket.se/recommerce/forsale/item/{ad_id}"

        return cls(
            id=ad_id,
            heading=heading,
            price=price,
            location=location,
            url=url,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert listing to dictionary format for backward compatibility."""
        return {
            "id": self.id,
            "heading": self.heading,
            "price": self.price,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "passed_filter": self.passed_filter,
        }

    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-like access (e.g. listing['id']) for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """Allow 'key in listing' check."""
        return hasattr(self, key)

    def format_summary(self) -> str:
        """Format a concise human-readable summary of the listing."""
        price_str = f"{self.price:,} SEK".replace(",", " ") if self.price is not None else "Price not set"
        return f"[{self.id}] {self.heading} - {price_str}"
