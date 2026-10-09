"""
Pipeline orchestrator for Blocket Sniper.
Coordinates ingestion, SQLite deduplication, two-tier deterministic filtering,
and detail extraction.
"""

import asyncio
import logging
from typing import Any, List, Optional

from blocket_sniper.client import BlocketIngestor
from blocket_sniper.config import DEFAULT_CONFIG, Config
from blocket_sniper.filters import DeterministicFilter
from blocket_sniper.models import Listing
from blocket_sniper.storage import StorageManager

logger = logging.getLogger("BlocketSniper.Pipeline")


class SniperPipeline:
    """
    End-to-end orchestration:
    Ingestion -> DB Dedup -> Fast Regex Filter -> Detail Extraction -> Deep Regex Filter.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.storage = StorageManager(self.config.storage.db_path)
        self.ingestor = BlocketIngestor(
            client_config=self.config.client,
            search_config=self.config.search,
        )
        self.filter = DeterministicFilter(self.config.filter)

    async def run_cycle(self) -> List[Listing]:
        """
        Execute one complete scraping and filtering cycle:
        1. Ingest raw listings from Blocket search endpoint.
        2. Deduplicate against SQLite database.
        3. Apply Layer 1 summary filter (title keywords & min price).
        4. Fetch detailed descriptions concurrently for surviving ads.
        5. Apply Layer 2 deep regex filter (description flaws & battery health).
        6. Persist processed ads in cache.
        7. Return actionable candidate listings.
        """
        logger.info("Executing marketplace polling cycle...")

        # Fetching tge 
        raw_docs = await self.ingestor.fetch_search_page()
        if not raw_docs:
            logger.info("No listings pulled from search endpoint.")
            return []

        # Step 1: Normalize incoming raw data
        unfiltered_ads: List[Listing] = []
        for doc in raw_docs:
            listing = Listing.from_api_doc(doc)
            if listing:
                unfiltered_ads.append(listing)

        # Step 2: Database deduplication
        all_ids = [ad.id for ad in unfiltered_ads]
        fresh_ids = self.storage.filter_unseen_ids(all_ids)
        fresh_ads = [ad for ad in unfiltered_ads if ad.id in fresh_ids]

        logger.info(
            "Ingested %d total ads. Discarded %d duplicates. %d fresh.",
            len(unfiltered_ads),
            len(unfiltered_ads) - len(fresh_ads),
            len(fresh_ads),
        )

        if not fresh_ads:
            return []

        # Step 3: Layer 1 Summary pre-filter (Heading and minimum pricing)
        l1_survivors: List[Listing] = [
            ad for ad in fresh_ads if self.filter.check_summary(ad)
        ]

        logger.info(
            "%d/%d items passed Layer 1 summary filter.",
            len(l1_survivors),
            len(fresh_ads),
        )
        if not l1_survivors:
            self.storage.record_ads(fresh_ads)
            return []

        # Step 4: Concurrent Detail Acquisition (bounded by Semaphore)
        tasks = [self.ingestor.fetch_ad_description(ad.id, url=ad.url) for ad in l1_survivors]
        descriptions = await asyncio.gather(*tasks)

        # Step 5: Layer 2 Deep Regex pre-filter (Description)
        actionable_leads: List[Listing] = []
        for ad, desc in zip(l1_survivors, descriptions):
            ad.description = desc
            if desc and self.filter.check_detail(ad):
                ad.passed_filter = True
                actionable_leads.append(ad)
            else:
                ad.passed_filter = False

        # Step 6: Mark all inspected records as processed in SQLite
        self.storage.record_ads(fresh_ads)

        logger.info(
            "Cycle complete. Retained %d actionable candidates for deal evaluation.",
            len(actionable_leads),
        )
        return actionable_leads

    async def shutdown(self) -> None:
        """Gracefully release network session and database connections."""
        await self.ingestor.close()
        self.storage.close()

    async def __aenter__(self) -> "SniperPipeline":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.shutdown()
