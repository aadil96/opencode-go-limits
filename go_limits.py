#!/usr/bin/env python3
"""Fetch OpenCode Go's published prices and allowances and compare the previous snapshot."""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

DOCS_URL = 'https://opencode.ai/docs/console/go.md'
CONSOLE_URL = 'https://opencode.ai/console/go'
CATALOG_URL = 'https://models.dev/api.json'
STATE = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'opencode-go/last.json'
# Bump when snapshot rows stop being comparable; older snapshots then restart as a baseline.
SOURCE_FORMAT = 2
PLANS = {'go': 'go', 'go-plus': 'go_plus'}
AGENT = {'User-Agent': 'opencode-go-limits/2.0'}


def ratios(markdown):
    """Read the 5-hour and weekly shares of the monthly allowance from the Go docs."""
    found = {}
    for key, label in (('five_hour', '5-hour'), ('weekly', 'weekly')):
        match = re.search(label + r' limit\W*is (\d+(?:\.\d+)?)% of (?:your|the) monthly allowance', markdown, re.I)
        if not match:
            raise ValueError(f'Allowance formula missing or changed: {label} limit')
        found[key] = Decimal(match[1]) / 100
    if not all(0 < share <= 1 for share in found.values()):
        raise ValueError(f'Implausible allowance formula: {found}')
    return found


def amount(text):
    """Parse a console allowance cell: '$15', '$1.50', '392', '1.2K', or '∞' (unlimited -> None)."""
    text = text.strip()
    if text == '∞':
        return None
    match = re.fullmatch(r'(\$)?(\d[\d,]*(?:\.\d+)?)([KM])?', text)
    if not match:
        raise ValueError(f'Unrecognized allowance: {text!r}')
    value = Decimal(match[2].replace(',', '')) * {None: 1, 'K': 1000, 'M': 1000000}[match[3]]
    return float(value) if match[1] else int(value)


class Allowances(HTMLParser):
    """Collect rows of the console's 'Go and Go Plus allowances' table."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.capture = None
        self.kind = None
        self.plan = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr' and attrs.get('data-layer') == 'model-row':
            self.row = {'id': attrs.get('data-model'), 'name': '', 'note': '', 'values': {}}
        if self.row is None:
            return
        layer = attrs.get('data-layer')
        if tag == 'td' and 'data-allowance-type' in attrs:
            self.kind = attrs['data-allowance-type']
        if layer == 'allowance':
            self.plan = attrs.get('data-plan')
        if layer in ('model-name', 'model-note'):
            self.capture = 'name' if layer == 'model-name' else 'note'
        if layer == 'current-value':
            self.capture = 'value'
            self.row['values'][(self.kind, self.plan)] = ''

    def handle_data(self, data):
        if self.row is None or self.capture is None:
            return
        if self.capture == 'value':
            self.row['values'][(self.kind, self.plan)] += data
        else:
            self.row[self.capture] += data

    def handle_endtag(self, tag):
        if self.row is None:
            return
        if tag == 'span':
            self.capture = None
        if tag == 'td':
            self.kind = None
        if tag == 'tr':
            self.rows.append(self.row)
            self.row = None


def parse_console(html):
    parser = Allowances()
    parser.feed(html)
    if not parser.rows:
        raise ValueError('Go allowance table missing or changed')
    rows = {}
    for row in parser.rows:
        name = ' '.join(row['name'].split())
        if not row['id'] or not name or row['id'] in rows:
            raise ValueError(f'Malformed or duplicate allowance row: {row["id"]!r}')
        expected = {(kind, plan) for kind in ('requests', 'usage') for plan in PLANS}
        if set(row['values']) != expected:
            raise ValueError(f'Allowance columns changed for {name}: {sorted(map(str, row["values"]))}')
        rows[row['id']] = {
            'label': name,
            'note': ' '.join(row['note'].split()) or None,
            'monthly': {PLANS[plan]: amount(row['values'][('usage', plan)]) for plan in PLANS},
            'requests': {PLANS[plan]: amount(row['values'][('requests', plan)]) for plan in PLANS},
        }
    return rows


def limits(monthly, share):
    if monthly is None:
        return {'monthly': None, 'weekly': None, 'five_hour': None}
    base = Decimal(str(monthly))
    return {'monthly': monthly, 'weekly': float(base * share['weekly']), 'five_hour': float(base * share['five_hour'])}


def prices(cost):
    return {'input': cost.get('input'), 'output': cost.get('output'),
            'cached_read': cost.get('cache_read'), 'cached_write': cost.get('cache_write')}


def build(console, share, catalog):
    go_models = catalog['opencode-go']['models']
    zen_models = catalog.get('opencode', {}).get('models', {})
    models = {}
    for model_id, row in console.items():
        entry = go_models.get(model_id)
        cost = (entry or {}).get('cost')
        price = prices(cost) if cost else dict.fromkeys(['input', 'output', 'cached_read', 'cached_write'])
        free = price['input'] == 0 and price['output'] == 0
        provider = 'opencode' if free and model_id in zen_models else 'opencode-go'
        if row['label'] in models:
            raise ValueError(f'Duplicate model label: {row["label"]}')
        models[row['label']] = {
            'model_key': f'{provider}/{model_id}',
            'prices_usd_per_million_tokens': price,
            'price_tiers': [{'above_context_tokens': tier['tier']['size'], **prices(tier)}
                            for tier in (cost or {}).get('tiers', []) if tier.get('tier', {}).get('type') == 'context'],
            'price_note': None if cost else 'Not in Models.dev opencode-go catalog; prices unknown',
            'limits_usd': limits(row['monthly']['go'], share),
            'limits_usd_by_plan': {plan: limits(row['monthly'][plan], share) for plan in PLANS.values()},
            'est_requests_per_5h': row['requests'],
            'unlimited': row['monthly']['go'] is None,
            'limit_note': row['note'],
        }
    return dict(sorted(models.items(), key=lambda item: (
        not (item[1]['prices_usd_per_million_tokens']['input'] == 0 and item[1]['prices_usd_per_million_tokens']['output'] == 0),
        -float('inf') if item[1]['unlimited'] else -item[1]['limits_usd']['monthly'])))


def changes(old, new):
    if old is None:
        return {'baseline': True, 'added': [], 'removed': [], 'changed': {}}
    return {'baseline': False, 'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()), 'changed': {k: {'before': old[k], 'after': new[k]} for k in sorted(old.keys() & new.keys()) if old[k] != new[k]}}


def fetch(url):
    with urlopen(Request(url, headers=AGENT), timeout=30) as response:
        return response.read().decode('utf-8')


def main():
    temporary = None
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        # Serialize runs so two agents cannot overwrite each other's baseline.
        import fcntl
        with (STATE.parent / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = json.loads(STATE.read_text()) if STATE.exists() else None
            if previous is not None and previous.get('source_format') != SOURCE_FORMAT:
                previous = None
            share = ratios(fetch(DOCS_URL))
            models = build(parse_console(fetch(CONSOLE_URL)), share, json.loads(fetch(CATALOG_URL)))
            result = {
                'ok': True, 'schema_version': 1, 'source_format': SOURCE_FORMAT, 'plan': 'Go', 'plans': list(PLANS.values()),
                'source_url': CONSOLE_URL + '#limits', 'formula_source_url': DOCS_URL,
                'price_source_url': CATALOG_URL, 'model_key_source_url': CATALOG_URL,
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'derived_limits': ['weekly', 'five_hour'],
                'derived_ratios': {key: float(value) for key, value in share.items()},
                'models': models,
                'changes': changes(None if previous is None else previous['models'], models),
            }
            encoded = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
            with tempfile.NamedTemporaryFile(mode='w', dir=STATE.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
            temporary.replace(STATE)
            print(encoded, end='')
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': {'type': type(exc).__name__, 'message': str(exc)}}))
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
