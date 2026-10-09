"""
Storage and deduplication layer backed by SQLite.
Ensures network requests and evaluations are never wasted on previously seen items.
"""

import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from blocket_sniper.models import Listing


class StorageManager:
    """
    SQLite-backed deduplication layer.
    Tracks seen advertisements across runs to avoid redundant processing.
    """

    def __init__(self, db_path: str = "sniper_cache.db") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Create the seen_ads table if it does not exist."""
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_ads (
                    ad_id TEXT PRIMARY KEY,
                    heading TEXT,
                    price INTEGER,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    evaluated BOOLEAN DEFAULT 0
                )
                """
            )

    def filter_unseen_ids(self, candidate_ids: Iterable[str]) -> Set[str]:
        """
        Given a collection of ad IDs, returns the subset of IDs that have
        never been recorded in the database.
        """
        candidates = list(candidate_ids)
        if not candidates:
            return set()

        seen_ids: Set[str] = set()
        chunk_size = 500  # Guard against SQLite parameter limits

        with self.conn:
            for i in range(0, len(candidates), chunk_size):
                chunk = candidates[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                query = f"SELECT ad_id FROM seen_ads WHERE ad_id IN ({placeholders})"
                cursor = self.conn.execute(query, chunk)
                seen_ids.update(row[0] for row in cursor.fetchall())

        return set(candidates) - seen_ids

    def record_ads(self, ads: Iterable[Union[Listing, Dict[str, Any]]]) -> None:
        """
        Persist advertisements into the seen_ads table.
        Accepts either Listing instances or dictionary objects.
        """
        records = []
        for ad in ads:
            if isinstance(ad, Listing):
                records.append((
                    ad.id,
                    ad.heading,
                    ad.price,
                    1 if ad.passed_filter else 0,
                ))
            elif isinstance(ad, dict) and ad.get("id"):
                records.append((
                    str(ad["id"]),
                    ad.get("heading", ""),
                    ad.get("price"),
                    1 if ad.get("passed_filter") else 0,
                ))

        if not records:
            return

        with self.conn:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO seen_ads (ad_id, heading, price, evaluated)
                VALUES (?, ?, ?, ?)
                """,
                records,
            )

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()

    def __enter__(self) -> "StorageManager":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
