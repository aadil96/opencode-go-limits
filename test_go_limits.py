import unittest
from decimal import Decimal
from go_limits import amount, build, changes, parse_console, ratios

DOCS = '''## Usage limits
- The **5-hour limit** is 20% of your monthly allowance. The window starts when you first use your allowance.
- The **weekly limit** is 50% of your monthly allowance. It resets on Monday at 00:00 UTC.
'''
SHARE = {'five_hour': Decimal('0.2'), 'weekly': Decimal('0.5')}


def cell(kind, go, plus):
    spans = ''.join(f'<span data-layer="allowance" data-plan="{plan}"><span data-layer="current-value">{value}</span></span>'
                    for plan, value in (('go', go), ('go-plus', plus)))
    return f'<td data-allowance-type="{kind}"><div aria-hidden="true">{spans}</div><span class="sr-only">Go: {go}; Go Plus: {plus}</span></td>'


def row(model_id, name, requests, usage, note=''):
    note_html = f'<span data-layer="model-note">{note}</span>' if note else ''
    return (f'<tr data-model="{model_id}" data-layer="model-row"><th><span><span data-layer="model-name" title="{name}">{name}</span>{note_html}</span></th>'
            f'<td aria-hidden="true"><div data-layer="bars"></div></td>{cell("requests", *requests)}{cell("usage", *usage)}</tr>')


def page(*rows):
    return ('<table aria-label="Go and Go Plus allowances"><tr data-layer="column-headings"><th>Model</th></tr>'
            + ''.join(rows) + '<tr data-layer="axis-row"><td></td></tr></table>')


CATALOG = {
    'opencode-go': {'models': {
        'kimi-k3': {'cost': {'input': 3, 'output': 15, 'cache_read': 0.3, 'cache_write': 3}},
        'grok-4.7': {'cost': {'input': 2, 'output': 6, 'cache_read': 0.5, 'tiers': [{'input': 4, 'output': 12, 'cache_read': 1, 'tier': {'type': 'context', 'size': 200000}}]}},
        'glm-5.3': {'cost': {'input': 1.4, 'output': 4.4, 'cache_read': 0.26}},
        'beam-free': {'cost': {'input': 0, 'output': 0, 'cache_read': 0}},
        'iceman-free': {'cost': {'input': 0, 'output': 0}},
    }},
    'opencode': {'models': {'beam-free': {}}},
}


class Checks(unittest.TestCase):
    def test_ratios(self):
        self.assertEqual(ratios(DOCS), SHARE)
        self.assertEqual(ratios(DOCS.replace('50%', '40%'))['weekly'], Decimal('0.4'))
        for broken in ['', DOCS.replace('5-hour', 'daily'), DOCS.replace('50%', '0%')]:
            with self.assertRaises(ValueError):
                ratios(broken)

    def test_amount(self):
        self.assertEqual([amount(v) for v in ['$15', '$1.50', '392', '1,234', '1.2K', '∞']], [15.0, 1.5, 392, 1234, 1200, None])
        with self.assertRaises(ValueError):
            amount('soon')

    def test_parse_console(self):
        rows = parse_console(page(row('kimi-k3', 'Kimi K3', ('98', '392'), ('$15', '$60')),
                                  row('beam-free', 'Beam Free', ('∞', '∞'), ('∞', '∞'), note='Limited time')))
        self.assertEqual(rows['kimi-k3'], {'label': 'Kimi K3', 'note': None, 'monthly': {'go': 15.0, 'go_plus': 60.0}, 'requests': {'go': 98, 'go_plus': 392}})
        self.assertEqual(rows['beam-free']['note'], 'Limited time')
        self.assertEqual(rows['beam-free']['monthly'], {'go': None, 'go_plus': None})

    def test_reject_invalid_console(self):
        missing_plan = page(row('kimi-k3', 'Kimi K3', ('98', '392'), ('$15', '$60'))).replace('data-plan="go-plus"', 'data-plan="go-max"')
        duplicate = page(row('kimi-k3', 'Kimi K3', ('1', '2'), ('$1', '$2')), row('kimi-k3', 'Kimi K3', ('1', '2'), ('$1', '$2')))
        for html in ['<table></table>', missing_plan, duplicate, page(row('', 'No id', ('1', '2'), ('$1', '$2')))]:
            with self.assertRaises(ValueError):
                parse_console(html)

    def test_build_prices_limits_keys(self):
        console = parse_console(page(
            row('kimi-k3', 'Kimi K3', ('98', '392'), ('$15', '$60')),
            row('grok-4.7', 'Grok 4.7', ('169', '676'), ('$15', '$60')),
            row('beam-free', 'Beam Free', ('∞', '∞'), ('∞', '∞')),
            row('iceman-free', 'Iceman Free', ('∞', '∞'), ('∞', '∞')),
            row('new-model', 'New Model', ('10', '40'), ('$15', '$60')),
        ))
        models = build(console, SHARE, CATALOG)
        kimi = models['Kimi K3']
        self.assertEqual(kimi['model_key'], 'opencode-go/kimi-k3')
        self.assertEqual(kimi['prices_usd_per_million_tokens'], dict(input=3, output=15, cached_read=0.3, cached_write=3))
        self.assertEqual(kimi['limits_usd'], dict(monthly=15.0, weekly=7.5, five_hour=3.0))
        self.assertEqual(kimi['limits_usd_by_plan']['go_plus'], dict(monthly=60.0, weekly=30.0, five_hour=12.0))
        self.assertEqual(models['Grok 4.7']['price_tiers'], [dict(above_context_tokens=200000, input=4, output=12, cached_read=1, cached_write=None)])
        self.assertEqual(models['Beam Free']['model_key'], 'opencode/beam-free')
        self.assertEqual(models['Iceman Free']['model_key'], 'opencode-go/iceman-free')
        self.assertTrue(models['Beam Free']['unlimited'])
        unknown = models['New Model']
        self.assertIsNone(unknown['prices_usd_per_million_tokens']['input'])
        self.assertIn('prices unknown', unknown['price_note'])

    def test_order_free_then_generous(self):
        console = parse_console(page(
            row('kimi-k3', 'Small', ('1', '2'), ('$15', '$60')),
            row('glm-5.3', 'Large', ('1', '2'), ('$500', '$1,000')),
            row('beam-free', 'Free', ('∞', '∞'), ('∞', '∞')),
            row('grok-4.7', 'Small B', ('1', '2'), ('$15', '$60')),
        ))
        self.assertEqual(list(build(console, SHARE, CATALOG)), ['Free', 'Large', 'Small', 'Small B'])

    def test_changes(self):
        self.assertTrue(changes(None, {'a': 1})['baseline'])
        self.assertEqual(changes({'a': 1, 'b': 2}, {'a': 3, 'c': 4}), {'baseline': False, 'added': ['c'], 'removed': ['b'], 'changed': {'a': {'before': 1, 'after': 3}}})


if __name__ == '__main__':
    unittest.main()
