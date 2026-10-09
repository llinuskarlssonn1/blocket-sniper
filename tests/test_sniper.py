"""
Unit tests for blocket_sniper modules using standard library unittest.
"""

import os
import shutil
import tempfile
import unittest

from blocket_sniper.config import Config, FilterConfig
from blocket_sniper.filters import DeterministicFilter
from blocket_sniper.models import Listing
from blocket_sniper.storage import StorageManager
from blocket_sniper.client import BlocketIngestor


class TestListingModel(unittest.TestCase):
    def test_from_api_doc_valid(self):
        doc = {
            "id": "123456",
            "heading": "iPhone 15 Pro",
            "price": {"amount": 7500},
            "location": "Stockholm",
        }
        listing = Listing.from_api_doc(doc)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.id, "123456")
        self.assertEqual(listing.heading, "iPhone 15 Pro")
        self.assertEqual(listing.price, 7500)
        self.assertEqual(listing.location, "Stockholm")
        self.assertEqual(listing.url, "https://www.blocket.se/recommerce/forsale/item/123456")

    def test_from_api_doc_numeric_price(self):
        doc = {"id": "999", "heading": "iPhone 14", "price": 4000}
        listing = Listing.from_api_doc(doc)
        self.assertIsNotNone(listing)
        self.assertEqual(listing.price, 4000)

    def test_from_api_doc_missing_id(self):
        doc = {"heading": "No ID"}
        self.assertIsNone(Listing.from_api_doc(doc))

    def test_dict_compatibility(self):
        doc = {"id": "100", "heading": "Test Phone", "price": 5000}
        listing = Listing.from_api_doc(doc)
        self.assertIsNotNone(listing)
        # Test dict indexing
        self.assertEqual(listing["id"], "100")
        self.assertEqual(listing["heading"], "Test Phone")
        self.assertEqual(listing["price"], 5000)
        # Test 'in' check
        self.assertTrue("heading" in listing)


class TestDeterministicFilter(unittest.TestCase):
    def test_passes_summary_valid(self):
        self.assertTrue(
            DeterministicFilter.passes_summary_check(
                "iPhone 15 Pro 128GB Fin skick", price=5000, min_price=1500
            )
        )

    def test_fails_summary_low_price(self):
        self.assertFalse(
            DeterministicFilter.passes_summary_check(
                "iPhone 15 Pro Skal", price=200, min_price=1500
            )
        )

    def test_fails_summary_exclusions(self):
        exclusions = {"trasig", "defekt", "låst", "icloud", "kartong", "reservdelar"}
        filter_instance = DeterministicFilter(FilterConfig(critical_exclusions=exclusions))
        for bad_word in exclusions:
            heading = f"iPhone 15 Pro ({bad_word})"
            listing = Listing(id="1", heading=heading, price=3000)
            self.assertFalse(
                filter_instance.check_summary(listing),
                f"Expected '{bad_word}' to fail summary check",
            )

    def test_passes_detail_valid(self):
        description = "Säljer min iPhone i nyskick. Batterihälsa 92%. Sparsamt använd."
        self.assertTrue(DeterministicFilter.passes_detail_check(description))

    def test_fails_detail_bad_battery(self):
        descriptions = [
            "Batteri 75%",
            "Batterihälsa: 78 %",
            "Battery 65%",
            "batteri 79%",
        ]
        for desc in descriptions:
            self.assertFalse(
                DeterministicFilter.passes_detail_check(desc),
                f"Expected sub-80 battery in '{desc}' to fail",
            )

    def test_passes_detail_good_battery(self):
        descriptions = [
            "Batteri 85%",
            "Batterihälsa 80%",
            "Batterihälsa 100%",
            "Battery: 95%",
        ]
        for desc in descriptions:
            self.assertTrue(
                DeterministicFilter.passes_detail_check(desc),
                f"Expected good battery in '{desc}' to pass",
            )

    def test_fails_detail_exclusions(self):
        filter_instance = DeterministicFilter(
            FilterConfig(critical_exclusions={"trasig", "icloud"})
        )
        l1 = Listing(id="1", heading="Phone", description="Telefonen är trasig i glaset.")
        l2 = Listing(id="2", heading="Phone", description="iCloud låst telefon")
        self.assertFalse(filter_instance.check_detail(l1))
        self.assertFalse(filter_instance.check_detail(l2))


class TestStorageManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cache.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_filter_unseen_and_record(self):
        storage = StorageManager(self.db_path)

        ids = ["101", "102", "103"]
        # Initially all unseen
        self.assertEqual(storage.filter_unseen_ids(ids), {"101", "102", "103"})

        # Record ads
        ad1 = Listing(id="101", heading="Phone 1", price=3000, passed_filter=True)
        ad2 = {"id": "102", "heading": "Phone 2", "price": 4000, "passed_filter": False}
        storage.record_ads([ad1, ad2])

        # Check unseen again
        fresh = storage.filter_unseen_ids(ids)
        self.assertEqual(fresh, {"103"})

        storage.close()


class TestDescriptionParser(unittest.TestCase):
    def test_extract_escaped_description(self):
        html_payload = r'<html><body><script>{\"description\":\"Fin telefon med \u00e5\u00e4\u00f6!\"}</script></body></html>'
        extracted = BlocketIngestor._extract_description_from_html(html_payload)
        self.assertIn("Fin telefon med åäö!", extracted)

    def test_extract_unescaped_description(self):
        html_payload = '<html><body>{"description":"Fin telefon i bra skick"}</body></html>'
        extracted = BlocketIngestor._extract_description_from_html(html_payload)
        self.assertEqual(extracted, "Fin telefon i bra skick")


if __name__ == "__main__":
    unittest.main()
