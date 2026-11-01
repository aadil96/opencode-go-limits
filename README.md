# OpenCode Go Limits

**Current OpenCode Go models, prices, and allowances—as agent-friendly JSON.**

A small, dependency-free Python command that reads the official Go documentation, resolves model keys from Models.dev, and reports what changed since your last successful run.

- Free models first, then highest monthly allowance.
- Usable model keys such as `opencode-go/deepseek-v4.1-flash` and `opencode/beam-free`.
- Input, output, and cache prices per million tokens.
- Monthly allowances plus derived weekly and five-hour limits.
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

Every run fetches both sources:

- [OpenCode Go documentation](https://opencode.ai/v2/docs/console/go#usage-limits) for Go pricing and allowances.
- [Models.dev catalog](https://models.dev/api.json) for provider/model keys.

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
| `plan` | `Go` only |
| `source_url`, `model_key_source_url` | Sources used |
| `fetched_at` | UTC timestamp |
| `derived_limits` | Fields calculated from monthly allowance |
| `models` | Pricing rows keyed by original display label |
| `changes` | Baseline flag, added/removed labels, before/after changed rows |

Each pricing row contains `model_key`, `prices_usd_per_million_tokens`, `limits_usd`, `unlimited`, and `limit_note`.

- Prices are USD per million tokens. `null` cache prices mean unavailable.
- `limits_usd` contains `monthly`, `weekly`, and `five_hour`. Weekly is 50% of monthly; five-hour is 20%.
- Unlimited rows have `unlimited: true` and `null` allowance values.
- Context-size, peak-hour, and discount conditions remain in display labels; table allowance notes remain in `limit_note`.
- Multiple pricing variants share a model key. **Their allowances must not be added together.**
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
{"ok": false, "error": {"type": "ValueError", "message": "No catalog model key for: Example"}}
```

Fetch failures, changed allowance formulas, malformed tables, and unknown or ambiguous model mappings preserve the previous snapshot. Old data is never silently returned as current. Each network request has a 30-second timeout.

The parser depends on the documentation's Go panel and table structure. A structurally valid upstream table that accidentally omits rows cannot be distinguished from genuine removals. Review removals before acting on them. Prose conditions outside pricing rows are not extracted. Catalog matching ignores punctuation and parenthetical conditions; ambiguous matches fail rather than guess.

No account access, model switching, background scheduling, full history, or MCP server is included.

## Development

```sh
python3 -m unittest -v
```

Tests cover parsing, discounts, unlimited allowances, invalid pages, model keys, sorting, and change detection. Tests are offline; run the command separately for a live source check.

## License

MIT. See [LICENSE](LICENSE).
