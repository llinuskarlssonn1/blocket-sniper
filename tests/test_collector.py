import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import blocket_sniper as b


def ad(**changes):
    return dict({'id': '123', 'heading': 'iPhone 15', 'trade_type': 'Säljes',
                 'canonical_url': b.BASE + 'item/123', 'location': 'Göteborg',
                 'timestamp': 1000, 'price': {'amount': 3000, 'currency_code': 'SEK'}}, **changes)


def search(docs):
    state = {'queries': [{'queryKey': [{'scope': 'search'}], 'state': {'data': {
        'docs': docs, 'metadata': {'paging': {'current': 1, 'last': 1}}}}}]}
    return '<script data-react-query-state>' + base64.b64encode(json.dumps(state).encode()).decode() + '</script>'


def detail(description='Fungerar perfekt. Inga sprickor.'):
    product = {'@type': 'Product', 'sku': '123', 'description': 'Truncated',
               'itemCondition': 'UsedCondition', 'offers': {'availability': 'https://schema.org/InStock'}}
    return '<script type="application/ld+json">' + json.dumps(product) + '</script><div class="whitespace-pre-wrap"><p>' + description + '</p></div>'


class CollectorTests(unittest.TestCase):
    def test_configurable_required_phrase(self):
        listing = ad(heading='Skrivbord')
        self.assertIsNone(b.rejection(listing, {'description': 'IKEA Bekant i fint skick'}, 'ikea bekant', 'other'))
        self.assertEqual(b.rejection(listing, {'description': 'Fint skick'}, 'ikea', 'other'), 'missing_must_have')
        self.assertIsNone(b.rejection(listing, must_have='ikea', product_category='other'))
        self.assertIsNone(b.rejection(listing, {'description': 'Fint skick'}, '', 'other'))
        self.assertIsNone(b.rejection(ad(heading='Skal till iPhone'), {'description': 'Fint skick'}, 'skal', 'other'))

    def test_search_settings_and_changes(self):
        from urllib.parse import parse_qs, urlparse

        class Client:
            urls = []
            def get(self, url):
                self.urls.append(url)
                return search([ad(timestamp=100000000)]) if 'search?' in url else detail()

        db = b.connect(':memory:')
        client = Client()
        config = {'pages': 1, 'location_id': '1.300014.311480', 'details_per_poll': 10,
                  'detail_refresh_hours': 24, 'query': 'Apple telefon',
                  'product_category': 'custom', 'must_have': 'iphone'}
        with patch('blocket_sniper.time.time', return_value=100000):
            b.poll(db, client, config)
            params = parse_qs(urlparse(client.urls[0]).query)
            self.assertEqual(params['q'], ['Apple telefon'])
            self.assertEqual(params['product_category'], ['custom'])
            self.assertEqual(db.execute('SELECT status FROM ads').fetchone()[0], 'candidate')
            config.update(must_have='Samsung', product_category='')
            b.poll(db, client, config)
            self.assertNotIn('product_category', parse_qs(urlparse(client.urls[2]).query))
            self.assertEqual(db.execute('SELECT reason FROM ads').fetchone()[0], 'missing_must_have')
        db.close()

    def test_age_boundary_and_invalid_timestamps(self):
        now = 1791273600
        for age, expected in [(86399, None), (86400, None), (86401, 'older_than_24_hours')]:
            self.assertEqual(b.age_rejection(ad(timestamp=(now-age)*1000), now), expected)
        for timestamp in [None, 'yesterday', 0, True, float('nan'), float('inf')]:
            self.assertEqual(b.age_rejection(ad(timestamp=timestamp), now), 'invalid_timestamp')
        self.assertEqual(b.age_rejection(ad(timestamp=(now+1)*1000), now), 'future_timestamp')

    def test_saved_candidate_expires_without_new_search(self):
        db = b.connect(':memory:')
        b.remember(db, ad(timestamp=100000000), 100000)
        row = db.execute('SELECT * FROM ads').fetchone()
        b.save_detail(db, row, {'description': 'Fint skick'}, 'candidate', None, 100000)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidates.json'
            with patch('blocket_sniper.time.time', return_value=186400):
                self.assertEqual(b.export(db, path), 1)
            with patch('blocket_sniper.time.time', return_value=186401):
                self.assertEqual(b.export(db, path), 0)
                self.assertEqual(json.loads(path.read_text()), [])
            row = db.execute('SELECT * FROM ads').fetchone()
            self.assertEqual(row['reason'], 'older_than_24_hours')
            self.assertIsNotNone(row['detail'])
        db.close()

    def test_old_listings_do_not_fetch_details(self):
        class Client:
            def get(self, url):
                if 'search?' not in url:
                    raise AssertionError('Old listing should not fetch details')
                return search([ad(timestamp=100000000)])
        db = b.connect(':memory:')
        config = {'pages': 1, 'location_id': '1.300014.311480', 'details_per_poll': 10,
                  'detail_refresh_hours': 24}
        with patch('blocket_sniper.time.time', return_value=186401):
            b.poll(db, Client(), config)
        self.assertEqual(db.execute('SELECT reason FROM ads').fetchone()[0], 'older_than_24_hours')
        db.close()

    def test_macos_missing_certificates(self):
        with patch('blocket_sniper.ssl.create_default_context') as create, \
                patch('blocket_sniper.sys.platform', 'darwin'), \
                patch.dict('os.environ', {}, clear=True), \
                patch('blocket_sniper.Path.is_file', return_value=True):
            context = create.return_value
            context.cert_store_stats.return_value = {'x509_ca': 0}
            self.assertIs(b.tls_context(), context)
            context.load_verify_locations.assert_called_once_with(cafile='/etc/ssl/cert.pem')

    def test_preserve_existing_or_explicit_trust_store(self):
        for count, environment in [(10, {}), (0, {'SSL_CERT_FILE': '/custom/ca.pem'})]:
            with self.subTest(count=count, environment=environment), \
                    patch('blocket_sniper.ssl.create_default_context') as create, \
                    patch('blocket_sniper.sys.platform', 'darwin'), \
                    patch.dict('os.environ', environment, clear=True):
                create.return_value.cert_store_stats.return_value = {'x509_ca': count}
                b.tls_context()
                create.return_value.load_verify_locations.assert_not_called()

    def test_search_and_empty_results(self):
        self.assertEqual(b.parse_search(search([ad()]))[0][0]['id'], '123')
        self.assertEqual(b.parse_search(search([]))[0], [])
        with self.assertRaises(b.ParseError):
            b.parse_search('<html>Challenge</html>')

    def test_complete_description_and_identity(self):
        self.assertEqual(b.parse_detail(detail(), '123')['description'], 'Fungerar perfekt. Inga sprickor.')
        with self.assertRaises(b.ParseError):
            b.parse_detail(detail(), '999')
        with self.assertRaises(b.ParseError):
            b.parse_detail(detail().split('<div')[0], '123')

    def test_damage_and_negations(self):
        for text in ['Sprucken baksida', 'Face ID fungerar inte', 'Startar inte',
                     'Säljes som reservdelar', 'iCloud-låst', 'Broken screen',
                     'Batteriet behöver bytas', 'Inga sprickor men kameran fungerar inte']:
            with self.subTest(text=text):
                self.assertIsNotNone(b.rejection(ad(), {'description': text}))
        for text in ['Inga sprickor. Fungerar perfekt.', 'Inte trasig.',
                     'Skal och laddare ingår.', 'No cracks.', 'Utan sprickor.']:
            with self.subTest(text=text):
                self.assertIsNone(b.rejection(ad(), {'description': text}))

    def test_accessory_and_wanted(self):
        self.assertEqual(b.rejection(ad(heading='Skal till iPhone 15')), 'accessory')
        self.assertEqual(b.rejection(ad(trade_type='Köpes')), 'not_for_sale')

    def test_dedup_price_history_and_export(self):
        db = b.connect(':memory:')
        b.remember(db, ad(), 1)
        b.remember(db, ad(), 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM ads').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0], 1)
        row = db.execute('SELECT * FROM ads').fetchone()
        b.save_detail(db, row, {'description': 'Fint skick'}, 'candidate', None, 3)
        with tempfile.TemporaryDirectory() as directory, patch('blocket_sniper.time.time', return_value=4):
            path = Path(directory) / 'candidates.json'
            self.assertEqual(b.export(db, path), 1)
            self.assertEqual(json.loads(path.read_text())[0]['price']['amount'], 3000)
            b.remember(db, ad(price={'amount': 2000}), 4)
            self.assertEqual(b.export(db, path), 0)  # Recheck changed ad before exporting.
        self.assertEqual(db.execute('SELECT first_seen FROM ads').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0], 3)
        db.close()

    def test_poll_caches_details_and_rechecks(self):
        class Client:
            calls = []
            description = 'Fint skick'
            def get(self, url):
                self.calls.append(url)
                return search([ad(timestamp=100000000)]) if 'search?' in url else detail(self.description)
        client = Client()
        db = b.connect(':memory:')
        config = {'pages': 1, 'location_id': '1.300014.311480', 'details_per_poll': 10,
                  'detail_refresh_hours': 1}
        with patch('blocket_sniper.time.time', return_value=100000):
            b.poll(db, client, config)
            b.poll(db, client, config)
        self.assertEqual(len(client.calls), 3)
        self.assertIn('location=1.300014.311480', client.calls[0])
        client.description = 'Trasig kamera'
        with patch('blocket_sniper.time.time', return_value=104000):
            b.poll(db, client, config)
        self.assertEqual(db.execute('SELECT status FROM ads').fetchone()[0], 'rejected')
        db.close()


if __name__ == '__main__':
    unittest.main()
