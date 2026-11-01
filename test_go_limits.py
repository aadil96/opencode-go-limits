import unittest
from go_limits import parse, changes, add_model_keys


def page(rows):
    return '''5-hour — 20%; weekly — 50%; monthly — 100%.
<div id="go-pricing-panel-go"><table><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th><th>Monthly limit</th></tr>''' + rows + '</table></div>'


class Checks(unittest.TestCase):
    def test_prices_and_conditions(self):
        data = parse(page('<tr><td>Model (Peak)</td><td><del>$4</del> $2</td><td>$10</td><td>Free</td><td>-</td><td>$15</td></tr>'))
        model = data['Model (Peak)']
        self.assertEqual(model['prices_usd_per_million_tokens'], dict(input=2, output=10, cached_read=0, cached_write=None))
        self.assertEqual(model['limits_usd'], dict(monthly=15, weekly=7.5, five_hour=3))

    def test_unlimited(self):
        model = parse(page('<tr><td>Free model</td><td>Free</td><td>Free</td><td>Free</td><td>-</td><td>Unlimited<br><small>limited time</small></td></tr>'))['Free model']
        self.assertTrue(model['unlimited'])
        self.assertEqual(model['limit_note'], 'limited time')

    def test_reject_invalid(self):
        for html in [page(''), page('<tr><td>broken</td></tr>'), page('').replace('20%', '30%'), page('').replace('</table>', '')]:
            with self.assertRaises(ValueError):
                parse(html)

    def test_model_keys(self):
        catalog = {'opencode-go': {'models': {'deepseek-v4.1-flash': {'name': 'DeepSeek V4.1 Flash'}}}, 'opencode': {'models': {'beam-free': {'name': 'Beam Free'}}}}
        rows = {'DeepSeek V4.1 Flash (Peak)': {'prices_usd_per_million_tokens': {'input': 1, 'output': 2}}, 'Beam Free': {'prices_usd_per_million_tokens': {'input': 0, 'output': 0}}}
        add_model_keys(rows, catalog)
        self.assertEqual(rows['DeepSeek V4.1 Flash (Peak)']['model_key'], 'opencode-go/deepseek-v4.1-flash')
        self.assertEqual(rows['Beam Free']['model_key'], 'opencode/beam-free')
        with self.assertRaises(ValueError):
            add_model_keys({'Unknown': rows['Beam Free']}, catalog)

    def test_free_first_stable_order(self):
        rows = ''.join(
            f'<tr><td>{name}</td><td>{price}</td><td>{price}</td><td>-</td><td>-</td><td>$15</td></tr>'
            for name, price in [('Paid B', '$1'), ('Free B', 'Free'), ('Paid A', '$2'), ('Free A', '$0')]
        )
        self.assertEqual(list(parse(page(rows))), ['Free B', 'Free A', 'Paid B', 'Paid A'])

    def test_generous_allowances_first(self):
        rows = ''.join(
            f'<tr><td>{name}</td><td>{price}</td><td>{price}</td><td>-</td><td>-</td><td>{limit}</td></tr>'
            for name, price, limit in [('Small', '$1', '$15'), ('Large B', '$2', '$100'), ('Free', 'Free', 'Unlimited'), ('Large A', '$3', '$100'), ('Medium', '$1', '$60')]
        )
        self.assertEqual(list(parse(page(rows))), ['Free', 'Large B', 'Large A', 'Medium', 'Small'])

    def test_changes(self):
        self.assertTrue(changes(None, {'a': 1})['baseline'])
        self.assertEqual(changes({'a': 1, 'b': 2}, {'a': 3, 'c': 4}), {'baseline': False, 'added': ['c'], 'removed': ['b'], 'changed': {'a': {'before': 1, 'after': 3}}})


if __name__ == '__main__':
    unittest.main()
