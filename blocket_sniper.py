"""Collect public Blocket ads into SQLite and an atomic JSON export (Python 3.10+)."""
import argparse
import base64
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import ssl
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

LOG = logging.getLogger('blocket_sniper')
BASE = 'https://www.blocket.se/recommerce/forsale/'
MAX_AD_AGE_SECONDS = 24 * 60 * 60


class ParseError(ValueError):
    pass


class Page(HTMLParser):
    """Read embedded state and the complete visible description, not SEO snippets."""
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.script = None
        self.description = []
        self.depth = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script = [attrs, '']
        if self.depth and tag not in {'br', 'img', 'hr', 'input', 'meta', 'link', 'source', 'wbr'}:
            self.depth += 1
        elif attrs.get('class', '').split().count('whitespace-pre-wrap'):
            self.depth = 1
        if self.depth and tag in {'p', 'br', 'div'}:
            self.description.append('\n')

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.script is not None:
            self.script[1] += data
        if self.depth:
            self.description.append(data)


def parse_search(html):
    for attrs, text in Page(html).scripts:
        if 'data-react-query-state' not in attrs:
            continue
        try:
            state = json.loads(text if text.lstrip().startswith('{') else base64.b64decode(text))
            for query in state['queries']:
                if any(isinstance(key, dict) and key.get('scope') == 'search'
                       for key in query['queryKey']):
                    data = query['state']['data']
                    if not isinstance(data['docs'], list):
                        raise ParseError('Search docs is not a list')
                    for ad in data['docs']:
                        if not isinstance(ad, dict) or not all(ad.get(k) for k in ('id', 'heading', 'canonical_url')):
                            raise ParseError('Search ad missing required fields')
                    return data['docs'], data['metadata']['paging']
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError('Blocket search data format changed') from exc
    raise ParseError('Search state missing; possible challenge page or layout change')


def parse_detail(html, expected_id):
    page = Page(html)
    for attrs, text in page.scripts:
        if attrs.get('type') != 'application/ld+json':
            continue
        try:
            product = json.loads(text)
        except ValueError:
            continue
        if not isinstance(product, dict) or product.get('@type') != 'Product':
            continue
        if str(product.get('sku')) != str(expected_id):
            raise ParseError('Ad identity mismatch')
        description = ''.join(page.description).strip()
        if not description:
            raise ParseError('Full description missing; refusing truncated SEO description')
        return {'description': description, 'condition': product.get('itemCondition'),
                'availability': product.get('offers', {}).get('availability'),
                'category': next((p.get('value') for p in product.get('additionalProperty', [])
                                  if p.get('name') == 'category'), None)}
    raise ParseError('Product data missing')


# A text screen, not a guarantee of physical condition. Keep reasons for auditing.
DAMAGE = re.compile(
    r'\b(?:trasig\w*|defekt\w*|sprick\w*|sprucken\w*|krossad\w*|'
    r'vattenskad\w*|fuktskad\w*|reservdel\w*|reparationsobjekt|'
    r'icloud[ -]?låst|aktiveringslåst|operatörslåst|'
    r'broken|cracked|faulty|damaged|water damage|for parts|'
    r'activation locked|icloud locked|not working|'
    r'(?:fungerar|funkar|startar|laddar)\s+(?:\w+\s+){0,2}inte|'
    r'(?:face\s?id|touch\s?id)\s+(?:är\s+)?ur funktion|'
    r'behöver\s+(?:bytas|repareras|lagas)|'
    r'(?:fel|problem)\s+med\s+(?:skärm\w*|batteri\w*|kamera\w*|face\s?id|ladd\w*))\b', re.I)
NEGATION = re.compile(r'\b(?:inga|ingen|inget|utan|inte|ej|no|not|without)\s+(?:(?:några|någon|synliga|visible)\s+)?$', re.I)
ACCESSORY = re.compile(r'^(?:(?:nytt?|nya|original|apple)\s+)*(?:skal|fodral|skärmskydd|laddare|kabel|case|charger|kartong)\b|\b(?:skal|fodral|skärmskydd|case)\s+(?:till|för|for)\b', re.I)


def rejection(ad, detail=None, must_have='iphone', product_category='2.93.3217.39'):
    title = ad.get('heading', '')
    if product_category == '2.93.3217.39' and ACCESSORY.search(title):
        return 'accessory'
    if ad.get('trade_type') != 'Säljes':
        return 'not_for_sale'
    if detail and (detail.get('availability') or '').endswith(('OutOfStock', 'SoldOut', 'Discontinued')):
        return 'unavailable'
    if detail and (detail.get('condition') or '').endswith('DamagedCondition'):
        return 'damaged_condition'
    text = title + '\n' + (detail or {}).get('description', '')
    # Wait for the full description before rejecting a missing phrase.
    if detail is not None and must_have.strip().casefold() not in text.casefold():
        return 'missing_must_have'
    for match in DAMAGE.finditer(text):
        if not NEGATION.search(text[max(0, match.start()-40):match.start()]):
            return 'damage: ' + match.group(0).lower()
    if not detail:
        return None
    if not ad.get('price') or ad['price'].get('amount') is None:
        return 'missing_price'
    return None


def age_rejection(ad, now):
    """Blocket's search timestamp is Unix time in milliseconds."""
    timestamp = ad.get('timestamp')
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp <= 0):
        return 'invalid_timestamp'
    published = timestamp / 1000
    if published > now:
        return 'future_timestamp'
    if now - published > MAX_AD_AGE_SECONDS:
        return 'older_than_24_hours'
    return None


def expire_ads(db, now):
    """Age out saved candidates and queued ads, including after failed searches."""
    with db:
        for row in db.execute("SELECT * FROM ads WHERE status IN ('candidate', 'pending')").fetchall():
            reason = age_rejection(json.loads(row['summary']), now)
            if reason:
                db.execute("UPDATE ads SET status='excluded', reason=? WHERE id=?", (reason, row['id']))
                db.execute('INSERT INTO history(ad_id,observed_at,summary,detail,status,reason) VALUES(?,?,?,?,?,?)',
                           (row['id'], now, row['summary'], row['detail'], 'excluded', reason))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS ads (
      id TEXT PRIMARY KEY, summary TEXT NOT NULL, summary_hash TEXT NOT NULL,
      detail TEXT, checked_at REAL, status TEXT NOT NULL, reason TEXT,
      first_seen REAL NOT NULL, last_seen REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS history (
      id INTEGER PRIMARY KEY, ad_id TEXT NOT NULL, observed_at REAL NOT NULL,
      summary TEXT NOT NULL, detail TEXT, status TEXT NOT NULL, reason TEXT);
    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    return db


def remember(db, ad, now, must_have='iphone', product_category='2.93.3217.39'):
    ad_id = str(ad['id'])
    # Do not store search ranking/tracking fields: they change independently of the ad.
    summary = {k: ad.get(k) for k in ('id', 'heading', 'canonical_url', 'location',
               'price', 'image_urls', 'timestamp', 'trade_type', 'coordinates', 'brand', 'memory_size')}
    digest = fingerprint(summary)
    old = db.execute('SELECT * FROM ads WHERE id=?', (ad_id,)).fetchone()
    if old and old['summary_hash'] == digest and old['status'] != 'out_of_search':
        db.execute('UPDATE ads SET last_seen=? WHERE id=?', (now, ad_id))
        return
    reason = age_rejection(summary, now) or rejection(summary, must_have=must_have, product_category=product_category)
    status = 'rejected' if reason else 'pending'
    payload = json.dumps(summary, ensure_ascii=False)
    db.execute('''INSERT INTO ads VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET summary=excluded.summary, summary_hash=excluded.summary_hash,
      detail=NULL, checked_at=NULL, status=excluded.status, reason=excluded.reason,
      last_seen=excluded.last_seen''', (ad_id, payload, digest, status, reason, now, now))
    db.execute('INSERT INTO history(ad_id,observed_at,summary,status,reason) VALUES(?,?,?,?,?)',
               (ad_id, now, payload, status, reason))


def save_detail(db, row, detail, status, reason, now):
    payload = json.dumps(detail, ensure_ascii=False) if detail is not None else None
    if payload != row['detail'] or status != row['status'] or reason != row['reason']:
        db.execute('INSERT INTO history(ad_id,observed_at,summary,detail,status,reason) VALUES(?,?,?,?,?,?)',
                   (row['id'], now, row['summary'], payload, status, reason))
    db.execute('UPDATE ads SET detail=?, checked_at=?, status=?, reason=? WHERE id=?',
               (payload, now, status, reason, row['id']))


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def export(db, path):
    expire_ads(db, time.time())
    candidates = []
    for row in db.execute("SELECT * FROM ads WHERE status='candidate' ORDER BY first_seen DESC"):
        ad = json.loads(row['summary'])
        ad.update(json.loads(row['detail']))
        ad.update(first_seen=iso(row['first_seen']), last_seen=iso(row['last_seen']),
                  detail_checked_at=iso(row['checked_at']), status='candidate')
        candidates.append(ad)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return len(candidates)


def tls_context():
    """Use macOS's CA bundle when framework Python has no installed roots."""
    context = ssl.create_default_context()
    explicit_store = any(name in os.environ for name in ('SSL_CERT_FILE', 'SSL_CERT_DIR'))
    if (sys.platform == 'darwin' and not explicit_store
            and context.cert_store_stats()['x509_ca'] == 0
            and Path('/etc/ssl/cert.pem').is_file()):
        context.load_verify_locations(cafile='/etc/ssl/cert.pem')
    return context


class Client:
    def __init__(self, delay):
        self.delay = delay
        self.last = 0.0
        self.context = tls_context()

    def get(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'www.blocket.se':
            raise ParseError('Unexpected listing URL')
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        request = Request(url, headers={'User-Agent': 'blocket-sniper/0.1', 'Accept-Language': 'sv-SE,sv;q=0.9'})
        with urlopen(request, timeout=25, context=self.context) as response:
            return response.read().decode('utf-8')


def poll(db, client, config):
    query = config.get('query', 'iphone')
    category = config.get('product_category', '2.93.3217.39')
    must_have = config.get('must_have', 'iphone')
    signature = fingerprint([query, category, must_have, config['location_id']])
    previous = db.execute("SELECT value FROM metadata WHERE key='search_signature'").fetchone()
    if previous is None or previous['value'] != signature:
        # Preserve history, but do not export or process ads from a previous search.
        with db:
            db.execute("UPDATE ads SET status='out_of_search', reason='search_changed'")
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('search_signature', ?)", (signature,))
    for page in range(1, config['pages'] + 1):
        params = dict(q=query, location=config['location_id'], sort='PUBLISHED_DESC', page=page)
        if category:
            params['product_category'] = category
        docs, paging = parse_search(client.get(BASE + 'search?' + urlencode(params)))
        with db:
            for ad in docs:
                remember(db, ad, time.time(), must_have, category)
        if page >= paging['last']:
            break
    # Refresh descriptions even when the search summary hasn't changed.
    cutoff = time.time() - config['detail_refresh_hours'] * 3600
    with db:
        db.execute("UPDATE ads SET status='pending' WHERE checked_at < ? AND status IN ('candidate','rejected') AND detail IS NOT NULL", (cutoff,))
    expire_ads(db, time.time())
    rows = db.execute("SELECT * FROM ads WHERE status='pending' ORDER BY COALESCE(checked_at,0), first_seen LIMIT ?",
                      (config['details_per_poll'],)).fetchall()
    for row in rows:
        ad = json.loads(row['summary'])
        if age_rejection(ad, time.time()):
            continue
        try:
            detail = parse_detail(client.get(ad['canonical_url']), row['id'])
            reason = rejection(ad, detail, must_have, category)
            with db:
                save_detail(db, row, detail, 'rejected' if reason else 'candidate', reason, time.time())
        except HTTPError as exc:
            if exc.code not in (404, 410):
                raise
            with db:
                save_detail(db, row, None, 'unavailable', str(exc.code), time.time())
        except ParseError as exc:
            LOG.warning('Ad %s: %s', row['id'], exc)
            with db:
                save_detail(db, row, None, 'pending', str(exc), time.time())


def retry_after(error):
    value = error.headers.get('Retry-After', '') if isinstance(error, HTTPError) else ''
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--once', action='store_true', help='Run a single bounded collection cycle')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    for key, default in [('query', 'iphone'), ('product_category', '2.93.3217.39'), ('must_have', 'iphone')]:
        config.setdefault(key, default)
        if not isinstance(config[key], str):
            parser.error(f'{key} must be a string')
        config[key] = config[key].strip()
    for key in ('poll_seconds', 'pages', 'details_per_poll', 'request_delay_seconds', 'detail_refresh_hours'):
        if not isinstance(config[key], (int, float)) or config[key] <= 0:
            parser.error(f'{key} must be positive')
    for key in ('pages', 'details_per_poll'):
        if not isinstance(config[key], int):
            parser.error(f'{key} must be an integer')
    directory = args.config.resolve().parent / config['data_dir']
    directory.mkdir(parents=True, exist_ok=True)
    db = connect(directory / 'ads.sqlite3')
    client = Client(config['request_delay_seconds'])
    failures = 0
    try:
        while True:
            started = time.monotonic()
            delay = config['poll_seconds']
            failed = False
            try:
                poll(db, client, config)
                failures = 0
            except (HTTPError, URLError, TimeoutError, OSError, ParseError) as exc:
                failures += 1
                failed = True
                delay = max(min(1800, config['poll_seconds'] * 2 ** min(failures, 6)), retry_after(exc))
                LOG.error('Collection failed: %s; retry delay %.0fs', exc, delay)
                if isinstance(exc, HTTPError) and exc.code in (401, 403):
                    LOG.error('Access denied. Stopping; no challenge bypass attempted.')
                    export(db, directory / 'candidates.json')
                    return 1
            count = export(db, directory / 'candidates.json')
            LOG.info('%s candidates saved; statuses: %s', count,
                     dict(db.execute('SELECT status, COUNT(*) FROM ads GROUP BY status').fetchall()))
            if args.once:
                return int(failed)
            # No overlapping cycles or catch-up bursts when a cycle exceeds 30 seconds.
            time.sleep(delay if failed else max(1, delay - (time.monotonic() - started)))
    except KeyboardInterrupt:
        LOG.info('Stopped')
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
