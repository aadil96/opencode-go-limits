#!/usr/bin/env python3
"""Fetch OpenCode Go's published prices and compare the previous snapshot."""
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

URL = 'https://opencode.ai/v2/docs/console/go'
CATALOG_URL = 'https://models.dev/api.json'
STATE = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'opencode-go/last.json'


class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.done = False
        self.rows = []
        self.row = None
        self.cell = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get('id') == 'go-pricing-panel-go':
            self.active = True
        if not self.active or self.done:
            return
        if tag in ('del', 's'):
            self.skip += 1
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = ''
        if tag == 'br' and self.cell is not None:
            self.cell += ' '

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell += data

    def handle_endtag(self, tag):
        if not self.active or self.done:
            return
        if tag in ('del', 's'):
            self.skip -= 1
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(self.cell.split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == 'table':
            self.done = True


def money(value):
    if value == '-':
        return None
    if value == 'Free':
        return 0
    if not re.fullmatch(r'\$\d+(?:\.\d+)?', value):
        raise ValueError(f'Unrecognized price: {value!r}')
    return float(Decimal(value[1:]))


def parse(html):
    # Fail closed if the documented allowance formula changes.
    plain = re.sub('<[^>]+>', ' ', html)
    if not re.search(r'5-hour\s*—\s*20%.*?weekly\s*—\s*50%.*?monthly\s*—\s*100%', plain, re.S):
        raise ValueError('Allowance formula missing or changed')
    parser = Table()
    parser.feed(html)
    expected = ['Model', 'Input', 'Output', 'Cached Read', 'Cached Write', 'Monthly limit']
    if not parser.done or not parser.rows or parser.rows[0] != expected or len(parser.rows) < 2:
        raise ValueError('Go table missing, incomplete, or changed')
    models = {}
    for row in parser.rows[1:]:
        if len(row) != 6 or not row[0] or row[0] in models:
            raise ValueError('Malformed or duplicate model row')
        label, *prices, limit = row
        match = re.fullmatch(r'(\$\d+(?:\.\d+)?|Unlimited)(?:\s+(.*))?', limit)
        if not match:
            raise ValueError(f'Unrecognized limit: {limit!r}')
        monthly = None if match[1] == 'Unlimited' else money(match[1])
        models[label] = {
            'prices_usd_per_million_tokens': dict(zip(['input', 'output', 'cached_read', 'cached_write'], map(money, prices))),
            'limits_usd': {'monthly': monthly, 'weekly': None if monthly is None else float(Decimal(str(monthly)) * Decimal('.5')), 'five_hour': None if monthly is None else float(Decimal(str(monthly)) * Decimal('.2'))},
            'unlimited': monthly is None,
            'limit_note': match[2],
        }
    return dict(sorted(models.items(), key=lambda item: (not (
        item[1]['prices_usd_per_million_tokens']['input'] == 0
        and item[1]['prices_usd_per_million_tokens']['output'] == 0
    ), -float('inf') if item[1]['unlimited'] else -item[1]['limits_usd']['monthly'])))


def add_model_keys(models, catalog):
    def normalized(name):
        return re.sub(r'[^a-z0-9]', '', re.sub(r'\s*\([^)]*\)', '', name).lower())

    for label, row in models.items():
        providers = ['opencode', 'opencode-go'] if row['prices_usd_per_million_tokens']['input'] == 0 and row['prices_usd_per_million_tokens']['output'] == 0 else ['opencode-go']
        for provider in providers:
            matches = [key for key, model in catalog[provider]['models'].items()
                       if normalized(label) in (normalized(key), normalized(model['name']))]
            if len(matches) > 1:
                raise ValueError(f'Ambiguous model key: {label}')
            if matches:
                row['model_key'] = f'{provider}/{matches[0]}'
                break
        else:
            raise ValueError(f'No catalog model key for: {label}')


def changes(old, new):
    if old is None:
        return {'baseline': True, 'added': [], 'removed': [], 'changed': {}}
    return {'baseline': False, 'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()), 'changed': {k: {'before': old[k], 'after': new[k]} for k in sorted(old.keys() & new.keys()) if old[k] != new[k]}}


def main():
    temporary = None
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        # Serialize runs so two agents cannot overwrite each other's baseline.
        import fcntl
        with (STATE.parent / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = json.loads(STATE.read_text()) if STATE.exists() else None
            request = Request(URL, headers={'User-Agent': 'opencode-go-limits/1.0'})
            with urlopen(request, timeout=30) as response:
                models = parse(response.read().decode('utf-8'))
            with urlopen(Request(CATALOG_URL, headers={'User-Agent': 'opencode-go-limits/1.0'}), timeout=30) as response:
                add_model_keys(models, json.load(response))
            result = {'model_key_source_url': CATALOG_URL, 'ok': True, 'schema_version': 1, 'plan': 'Go', 'source_url': URL + '#usage-limits', 'fetched_at': datetime.now(timezone.utc).isoformat(), 'derived_limits': ['weekly', 'five_hour'], 'models': models, 'changes': changes(None if previous is None else previous['models'], models)}
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
