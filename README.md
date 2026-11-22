# OpenCode Go Limits

**Current OpenCode Go models, prices, and allowances—as agent-friendly JSON.**

A small, dependency-free Python command that reads OpenCode's published Go allowances, adds token prices and model keys from Models.dev, and reports what changed since your last successful run.

- Free models first, then highest monthly allowance.
- Usable model keys such as `opencode-go/deepseek-v4.1-flash` and `opencode/beam-free`.
- Input, output, and cache prices per million tokens, plus long-context price tiers.
- Go and Go Plus monthly allowances, derived weekly and five-hour limits, and estimated requests per 5 hours.
- Added, removed, and changed pricing rows.
- JSON output on success and failure; no API key required.

This is an independent utility, not affiliated with OpenCode. It reports **published plan allowances**, not your account usage or remaining balance.

## Quick start

Requires Python 3.8+ on Linux or macOS. No packages to install. `jq` is optional.

```sh
git clone https://github.com/aadil96/opencode-go-limits.git
cd opencode-go-limits
python3 go_limits.py
```

Every run fetches three sources:

- [Console Go page](https://opencode.ai/console/go#limits) for monthly allowances and estimated requests per plan.
- [Go documentation](https://opencode.ai/docs/console/go.md) for the 5-hour and weekly shares of the monthly allowance.
- [Models.dev catalog](https://models.dev/api.json) for token prices and provider/model keys. OpenCode renders its own price table in the browser only, so the published Models.dev prices are used instead.

## Agent and terminal usage

```sh
# Full JSON result
python3 go_limits.py

# Changes since previous successful run
python3 go_limits.py | jq '.changes'

# Unique model keys, preserving first-seen order
python3 go_limits.py | jq '[.models[].model_key] | reduce .[] as $k ([]; if index($k) then . else . + [$k] end)'

# Model keys and monthly dollar allowances
python3 go_limits.py | jq '.models[] | {model_key, monthly_usd: .limits_usd.monthly}'
```

For automation, inspect the command's exit status or the JSON `ok` field. Use `set -o pipefail` in a shell pipeline when you need fetch failures to propagate past `jq`.

## Output contract

Successful output contains:

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success |
| `schema_version` | Output format version, currently `1` |
| `source_format` | Snapshot format; a snapshot in another format restarts as a baseline |
| `plan` | `Go`: the plan that `limits_usd` describes |
| `plans` | Plans in `limits_usd_by_plan`: `go`, `go_plus` |
| `source_url`, `formula_source_url`, `price_source_url`, `model_key_source_url` | Sources used |
| `fetched_at` | UTC timestamp |
| `derived_limits` | Fields calculated from monthly allowance |
| `derived_ratios` | Shares read from the docs, e.g. `{"five_hour": 0.2, "weekly": 0.5}` |
| `models` | Rows keyed by the console display label |
| `changes` | Baseline flag, added/removed labels, before/after changed rows |

Each row contains `model_key`, `prices_usd_per_million_tokens`, `price_tiers`, `price_note`, `limits_usd`, `limits_usd_by_plan`, `est_requests_per_5h`, `unlimited`, and `limit_note`.

- Prices are USD per million tokens. `null` prices mean unknown, not free. A model that is missing from the Models.dev `opencode-go` catalog has all prices `null` and a `price_note`.
- `price_tiers` lists higher prices that apply above `above_context_tokens`.
- `limits_usd` (Go plan) and each `limits_usd_by_plan` entry contain `monthly`, `weekly`, and `five_hour`. Weekly and five-hour use `derived_ratios`; the docs currently state 50% and 20%.
- Unlimited rows have `unlimited: true` and `null` allowance values.
- Console row notes such as `Limited time` remain in `limit_note`.
- Paid models use `opencode-go`. Zero input/output price models prefer `opencode` when present, otherwise `opencode-go`.
- Rows emit free models first, then descending monthly allowance; unlimited ranks above finite allowances. Ties retain source order. JSON consumers may reorder object keys.

“Most generous” means largest dollar allowance, not estimated token capacity. Token prices differ between models.

## Snapshots and changes

The first successful run establishes a baseline with empty change lists. Subsequent runs compare against the last successful snapshot, then replace it.

Default snapshot:

```text
~/.local/state/opencode-go/last.json
```

With `XDG_STATE_HOME` set, storage moves to `$XDG_STATE_HOME/opencode-go/last.json`. To run with isolated state:

```sh
XDG_STATE_HOME="$(mktemp -d)" python3 go_limits.py
```

Writes use atomic replacement; a file lock serializes concurrent runs. Changes compare values, so sorting alone does not count as a pricing change. Only the latest snapshot is retained.

## Failures and limitations

Exit code **0** indicates success. Exit code **1** returns JSON such as:

```json
{"ok": false, "error": {"type": "ValueError", "message": "Allowance formula missing or changed: weekly limit"}}
```

Fetch failures, changed allowance formulas, and malformed or changed allowance tables preserve the previous snapshot. Old data is never silently returned as current. Each network request has a 30-second timeout.

The parser depends on the console allowance table's `data-model`, `data-plan`, and `data-allowance-type` markup, and on the docs sentence "The 5-hour limit is N% of your monthly allowance" (same for weekly). If any of these change, the run fails and keeps the previous snapshot. A structurally valid upstream table that accidentally omits rows cannot be distinguished from genuine removals. Review removals before acting on them. Prose conditions are not extracted. Examples: temporary discounts and temporary higher limits in the docs' model notes. Models.dev prices are a third-party catalog and can lag behind OpenCode's own price table.

No account access, model switching, background scheduling, full history, or MCP server is included.

## Development

```sh
python3 -m unittest -v
```

Tests cover the allowance formula, value formats, console parsing, unlimited allowances, invalid pages, prices and tiers, model keys, unknown prices, sorting, and change detection. Tests are offline; run the command separately for a live source check.

## License

MIT. See [LICENSE](LICENSE).
