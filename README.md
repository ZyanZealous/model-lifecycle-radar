# Model Lifecycle Radar

Model Lifecycle Radar collects official public model catalogs and lifecycle or change documentation, then creates review files for model owners. It is a Python 3.11+ CLI and importable library with no runtime dependencies.

It makes **no inference calls**, does not authenticate, sends no messages, and never automatically chooses or replaces a model. A public document does not prove that a model is enabled for your account. A model missing from a catalog is not confirmed retired. A changed document or schema is a review reminder, not proof of an API breaking change.

## Quick start

```powershell
python -m pip install --no-deps -e .
# Edit watchlist.json with the exact model IDs, owners, and services you use.
python -m model_lifecycle_radar collect --state .radar/state.json --watchlist watchlist.json --out reports
python -m model_lifecycle_radar check --state .radar/state.json --watchlist watchlist.json --out reports
python -m model_lifecycle_radar check --state .radar/state.json --watchlist watchlist.json --fail-on warning
python -m model_lifecycle_radar smoke --vendor all
python -m model_lifecycle_radar demo --out .radar/demo-report
```

The `collect` command fetches public pages and updates `.radar/state.json`. `check` is offline and reevaluates the saved evidence against the current watchlist and thresholds. Both write `report.json`, `report.md`, and `events.json` under the output directory. The smoke command fetches and parses sources without changing state. The demo writes a synthetic offline example. The installed `radar` command is also available where Python's scripts directory is on `PATH`.

Exit codes are `0` for a healthy run below the selected severity, `1` when `--fail-on` is exceeded, and `2` for collection, configuration, lock, or state errors. A partial collection still writes reports and keeps last-good source data.

## What is monitored

Catalog adapters use provider-specific fields and tables. They do not search pages for arbitrary model-looking strings. At the 2026-10-19 source review, the official pages and change sources were:

| Provider | Public catalog | Lifecycle or change page | Coverage note |
| --- | --- | --- | --- |
| OpenAI | [Models](https://developers.openai.com/api/docs/models) | [Deprecations](https://developers.openai.com/api/docs/deprecations); [API changelog](https://developers.openai.com/api/docs/changelog) | Model-card `Model ID` fields; shutdown tables with exact IDs and dates; changelog sections are review-only. |
| Anthropic | [Models overview](https://platform.claude.com/docs/en/models/overview.md) | [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations.md) | `Claude API ID` comparison row; tentative retirement dates remain marked tentative. |
| Gemini | [Models](https://ai.google.dev/gemini-api/docs/models) | [Deprecations](https://ai.google.dev/gemini-api/docs/deprecations); [Changelog](https://ai.google.dev/gemini-api/docs/changelog) | `Endpoint` cells and structured `Shutdown date` rows; changelog sections are review-only. |
| DeepSeek | [Pricing/model details](https://api-docs.deepseek.com/quick_start/pricing/) | [Updates](https://api-docs.deepseek.com/updates) | Labeled `MODEL` and `MODEL VERSION` rows; update-page changes request review. |
| Mistral | [Models](https://docs.mistral.ai/models) | [Model lifecycle policy](https://docs.mistral.ai/inference/model-lifecycle) | `API` column; paired deprecation/retirement date fields; policy text alone is not a model deadline. |
| xAI | [Models](https://docs.x.ai/developers/models) | No linked lifecycle or changelog page was found during the source review. | IDs come from current official model-detail links (with the earlier Markdown pricing layout supported); catalog sections are fingerprinted for changes. |

All catalog and change pages are normalized into document sections. Any unavailable required source is marked unhealthy; it is never treated as an empty retirement list. See [docs/verification.md](docs/verification.md) for captured parser and live-smoke evidence.

## Baselines, findings, and evidence

- The first successful comparable collection establishes a quiet baseline. It does not announce every current model or document as new.
- Existing official lifecycle notices are evaluated against the watchlist on the first run, so a deadline risk is not hidden by baseline mode.
- A new catalog ID means “newly observed in this public catalog,” not “released today.”
- A missing watched ID is reported as “not verified in configured scope.” A disappearance review requires two successful comparable snapshots. It is never labeled retirement by absence alone.
- Source URL, UTC observation time, parser rule, short evidence excerpt, content hash, and evidence support are included where applicable. Source health is a separate field from extraction support.
- Explicit authoritative dates generate the most urgent active 30/14/7-day band, then an overdue finding. Tentative or “not sooner than” dates do not trigger a deadline band.
- Findings are recalculated on every offline `check`, including when owners, services, watchlists, or `--retirement-days` change. Newly emitted deduplicated events are kept separate from current findings.
- Event history retains up to 1,000 entries by default. Once an old event is pruned, the same signal can be emitted again.
- Review steps ask owners to confirm the official notice, inspect endpoints and parameters, identify a supported candidate, run application regressions, and schedule migration. A candidate is shown only when the source states an exact replacement ID.

Default collection bounds are 15 seconds per request, three total attempts, 2 MiB per response, five official-host redirects, bounded retry delay, 16 MiB and 150 seconds per run. Responses are not automatically decompressed; compressed entities are rejected. Conditional ETag/Last-Modified requests reuse compatible last-good snapshots.

## Watchlist and custom sources

`watchlist.json` has schema version 1:

```json
{
  "schema_version": 1,
  "models": [
    {
      "vendor": "openai",
      "model": "example-exact-model-id",
      "owner": "payments",
      "service": "invoice-extraction"
    }
  ]
}
```

Replace the placeholder with each exact ID from the public catalog and assign a real owner and service. Duplicate `(vendor, model, scope)` entries are rejected.

To add a custom official JSON or OpenAPI JSON fingerprint source, copy [examples/custom-sources.json](examples/custom-sources.json), set the exact HTTPS URL and allowed official hostname, then pass `--config path\to\sources.json`. The custom source is added to the six provider sources. Custom OpenAPI changes are grouped by endpoint path and component schema where available. They request review only; there is no universal breaking-change classifier. Source hosts with URL credentials, query strings, or fragments are rejected. The configuration owner is responsible for verifying the custom publisher.

## Scope decisions and limits

The initial release covers public documentation only. The optional authenticated OpenAI account-visible catalog was deliberately left out: the core workflow is keyless public monitoring, and account-specific credentials, scopes, and pagination deserve a separate reviewed subsystem. `OPENAI_API_KEY` is neither read nor required. A public catalog snapshot is not evidence of one account's access.

Official pages can change layout or become unavailable. Empty catalogs, duplicate IDs, missing expected structure, or a catalog collapse fail closed and retain last-good state. A URL, canonical destination, coverage, scope, or parser revision change requests a fresh baseline instead of creating removals. Review source-health and coverage rows before acting on findings.

No fetched vendor page is committed. Small focused parser fixtures with source URLs and capture date are under `tests/fixtures/`.

## Scheduled collection

`.github/workflows/scheduled-collector.yml` runs weekly and on manual dispatch with `contents: read`, no provider secrets, a concurrency guard, a 20-minute timeout, and separate state/report artifacts. The branch state uses GitHub Actions cache so a later run can restore the latest matching state. Cache eviction or expiration, a missing state file, or a restored state that has been removed starts a fresh baseline; missed history cannot be reconstructed. Reports upload even when collection returns findings or a source error. State artifacts are kept for 30 days and reports for 90 days.

The workflow is an example. Review its branch and artifact visibility before enabling it on a public repository; reports can include owner/service labels from the watchlist.

## Development

```powershell
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
python -m model_lifecycle_radar --help
```

Tests use local fixtures and injected fetchers; they do not access provider endpoints. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
