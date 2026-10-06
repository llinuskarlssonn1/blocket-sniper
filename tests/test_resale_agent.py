from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import review_candidates as r


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((r.ROOT / 'resale_agent.json').read_text())
        self.now = datetime(2026, 10, 6, tzinfo=timezone.utc)

    def candidate(self, ad_id, age=0, **changes):
        return dict({'id': ad_id, 'timestamp': (self.now.timestamp()-age)*1000,
                     'price': {'amount': 2000, 'currency_code': 'SEK'},
                     'description': 'Untrusted seller text'}, **changes)

    def test_snapshot_age_currency_duplicates_and_limit(self):
        self.config['max_candidates'] = 1
        items = [self.candidate('older', 86400), self.candidate('new'),
                 self.candidate('expired', 86401), self.candidate('new'),
                 self.candidate('foreign', price={'amount': 200, 'currency_code': 'EUR'}),
                 self.candidate('unknown', timestamp=None)]
        snapshot = r.prepare_snapshot(items, self.config, self.now)
        self.assertEqual([a['id'] for a in snapshot['candidates']], ['new'])
        self.assertEqual(snapshot['deferred'], [{'id': 'older', 'reason': 'max_candidates_limit'}])
        self.assertEqual({x['reason'] for x in snapshot['excluded']},
                         {'older_than_24_hours', 'duplicate_id', 'unsupported_currency', 'invalid_timestamp'})
        self.assertEqual(snapshot['input_count'], 6)

    def test_settings_validation(self):
        r.validate_config(self.config)
        for key, value in [('max_candidates', 0), ('minimum_comparables', True),
                           ('risk_reserve_percent', 100), ('selling_fee_percent', -1),
                           ('minimum_net_profit_sek', float('nan')), ('model', ''),
                           ('candidates_path', '')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.validate_config(dict(self.config, **{key: value}))

    def test_prompt_and_command(self):
        snapshot = r.prepare_snapshot([self.candidate('one')], self.config, self.now)
        prompt = r.build_prompt(self.config, snapshot)
        self.assertIn('Untrusted seller text', prompt)
        self.assertIn('confirmed_sold', prompt)
        self.assertIn('minimum_net_profit_sek', prompt)
        command = r.codex_command(self.config, Path('/tmp/report.md'))
        self.assertIn('web_search="live"', command)
        self.assertIn('read-only', command)
        self.assertNotIn('--model', command)
        self.assertEqual(command[-1], '-')

    def test_dry_run_does_not_invoke_codex_or_edit_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_file = root / 'candidates.json'
            candidate_file.write_text(json.dumps([self.candidate('one')]))
            before = candidate_file.read_bytes()
            config = dict(self.config, candidates_path='candidates.json', reports_dir='reviews')
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config))
            with patch('sys.argv', ['review_candidates.py', '--config', str(config_path), '--dry-run']), \
                    patch('review_candidates.subprocess.run') as run:
                self.assertEqual(r.main(), 0)
                run.assert_not_called()
            self.assertEqual(candidate_file.read_bytes(), before)
            self.assertEqual(len(list((root / 'reviews').glob('*/snapshot.json'))), 1)
            self.assertEqual(len(list((root / 'reviews').glob('*/prompt.md'))), 1)
            self.assertEqual(list((root / 'reviews').glob('*/report.md')), [])


if __name__ == '__main__':
    unittest.main()
