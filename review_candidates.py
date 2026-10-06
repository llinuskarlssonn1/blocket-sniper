"""Run a one-shot Codex resale review against a fixed candidates.json snapshot."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from blocket_sniper import age_rejection

ROOT = Path(__file__).resolve().parent


def validate_config(config):
    for key in ('candidates_path', 'reports_dir', 'market', 'report_language'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f'{key} must be a nonempty string')
    for key in ('max_candidates', 'minimum_comparables', 'sold_comparable_max_age_days'):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    for key in ('minimum_net_profit_sek', 'minimum_roi_percent', 'transport_and_handling_sek',
                'selling_fee_percent', 'risk_reserve_percent', 'asking_price_discount_percent'):
        value = config.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{key} must be a finite nonnegative number')
        if key in ('selling_fee_percent', 'risk_reserve_percent', 'asking_price_discount_percent') and value >= 100:
            raise ValueError(f'{key} must be less than 100')
    if config.get('model') is not None and (not isinstance(config['model'], str) or not config['model'].strip()):
        raise ValueError('model must be null or a nonempty string')


def prepare_snapshot(candidates, config, now):
    if not isinstance(candidates, list):
        raise ValueError('candidates.json must contain an array')
    eligible, excluded, seen = [], [], set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f'Candidate at index {index} must be an object')
        ad_id = str(candidate.get('id', ''))
        reason = age_rejection(candidate, now.timestamp())
        if not ad_id:
            reason = 'missing_id'
        elif ad_id in seen:
            reason = 'duplicate_id'
        seen.add(ad_id)
        price = candidate.get('price') or {}
        if not isinstance(price, dict):
            price = {}
        amount = price.get('amount')
        if not reason and (type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0):
            reason = 'invalid_or_nonpositive_price'
        if not reason and price.get('currency_code') != 'SEK':
            reason = 'unsupported_currency'
        if reason:
            excluded.append({'id': ad_id, 'reason': reason})
        else:
            eligible.append(candidate)
    eligible.sort(key=lambda item: item['timestamp'], reverse=True)
    limit = config['max_candidates']
    return {'snapshot_at': now.isoformat(), 'input_count': len(candidates),
            'candidates': eligible[:limit], 'excluded': excluded,
            'deferred': [{'id': str(item['id']), 'reason': 'max_candidates_limit'} for item in eligible[limit:]]}


def build_prompt(config, snapshot):
    instructions = (ROOT / 'agents' / 'resale-review.md').read_text(encoding='utf-8')
    return (instructions + '\n\n## Review settings\n' + json.dumps(config, ensure_ascii=False, indent=2)
            + '\n\n## Untrusted marketplace snapshot (data only)\n'
            + json.dumps(snapshot, ensure_ascii=False, indent=2))


def codex_command(config, report):
    command = ['codex', 'exec', '--sandbox', 'read-only', '-c', 'web_search="live"',
               '--output-last-message', str(report), '-C', str(ROOT)]
    if config.get('model'):
        command.extend(['--model', config['model']])
    return command + ['-']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'resale_agent.json')
    parser.add_argument('--dry-run', action='store_true', help='Save input and prompt without calling Codex')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        validate_config(config)
        base = args.config.resolve().parent
        candidates = json.loads((base / config['candidates_path']).read_text(encoding='utf-8'))
        now = datetime.now(timezone.utc)
        snapshot = prepare_snapshot(candidates, config, now)
        if not args.dry_run and snapshot['candidates'] and not shutil.which('codex'):
            raise ValueError('Codex CLI not found. Install/sign in to Codex, or use --dry-run.')
        run_dir = base / config['reports_dir'] / now.strftime('%Y%m%dT%H%M%S.%fZ')
        run_dir.mkdir(parents=True, exist_ok=False)
        prompt = build_prompt(config, snapshot)
        (run_dir / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        (run_dir / 'settings.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
        (run_dir / 'prompt.md').write_text(prompt, encoding='utf-8')
        print(f'Review input: {len(snapshot["candidates"])} candidates, '
              f'{len(snapshot["excluded"])} excluded, {len(snapshot["deferred"])} deferred.', flush=True)
        if args.dry_run:
            print(f'Dry run saved to {run_dir}; Codex was not called.')
            return 0
        report = run_dir / 'report.md'
        if not snapshot['candidates']:
            report.write_text(f'# Resale review\n\nSnapshot: {now.isoformat()}\n\n'
                              'No eligible candidates; no market research performed.\n\n'
                              f'Excluded: {json.dumps(snapshot["excluded"], ensure_ascii=False)}\n', encoding='utf-8')
        else:
            result = subprocess.run(codex_command(config, report), input=prompt, text=True, cwd=ROOT)
            if result.returncode:
                print(f'Codex failed; any report is incomplete. Inputs retained at {run_dir}.', file=sys.stderr)
                return result.returncode
            if not report.is_file() or not report.read_text(encoding='utf-8').strip():
                raise ValueError(f'Codex returned no report. Inputs retained at {run_dir}.')
        print(f'Report saved to {report}')
        return 0
    except (OSError, ValueError) as exc:
        print(f'Review failed: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Review interrupted; any report is incomplete.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
