# Model Lifecycle Radar design

## Purpose and boundary

Model Lifecycle Radar is a Python 3.11+ standard-library CLI and importable library that collects official public model catalogs and change documentation for six providers. It writes local JSON and Markdown review reports. It makes no inference calls, sends no notifications, and never automatically recommends or replaces a model. Public documentation coverage does not imply account access; a catalog disappearance is not a retirement; a changed document is not proof of an API break.

## Collection and evidence

Each official source has a stable ID, exact URL/host allowlist, scope, parser revision, and provider-specific extraction rule. Parsers require recognizable table, model-card, API-ID, or section structure. Mistral's active model cards are discovered in its current model table; exact API names come from labeled table cells or detail-page fields, while cards publishing neither are excluded from the callable catalog. Historical retirement rows are evidence only. DeepSeek changes require the official `Change Log` heading and dated entries at `/updates/`; its `MODEL VERSION` values are display metadata, not callable IDs. Adapters preserve exact IDs and record a short evidence excerpt, source URL, UTC observation time, rule ID, content hash, and support level (`structured`, `explicit_text`, or `review_needed`). Lifecycle rows require exact model ID, explicit retirement/shutdown wording, and a clear date. Documentation changes and custom official JSON/OpenAPI fingerprint changes request review; they do not assert breaking changes.

HTTP requests are HTTPS-only with a 15-second timeout, three attempts, bounded exponential backoff/jitter and Retry-After, 2 MiB per response, 16 MiB aggregate response limit, five redirects constrained to official hosts, and a 150-second run budget. Mistral detail fetches share the same injected fetcher and aggregate limits as the overview and other sources. Sources fail independently. Empty catalogs, unrecognized structure, duplicate IDs, and suspicious count collapse fail closed. Failed or stale sources retain last-good state and cannot create removals. Source health and evidence confidence are separate.

Each collection result contains source health plus the last-good normalized snapshot. Comparisons require matching source, scope/revision, canonical URL, and compatible parser revision. Incompatible changes request a new baseline. First collection baselines catalog and document hashes without a flood of new events, but evaluates any existing watchlist against authoritative lifecycle data immediately.

## State, evaluation, and output

Versioned state is atomically replaced from a same-directory temporary file after flush/fsync. Mutating CLI commands take an exclusive lock; malformed/unsupported state never gets overwritten. Deterministic event IDs omit observation timestamps. A bounded event history deduplicates announcements. Comparable successful disappearance observations are retained; only the second consecutive one creates a disappearance review. Findings are regenerated from current saved evidence, current watchlist, and policy on every `check`, including offline checks.

Watchlist findings identify owner and service, distinguish “not verified in configured scope” from retirement, and include practical review/migration steps. Deadline policy evaluates overdue and 30/14/7-day boundaries; the most urgent active band is emitted. Threshold/history findings and current watchlist findings are reported separately. CLI commands are `collect`, offline `check`, and `smoke`; all write `report.json`, `report.md`, and `events.json`. Exit status is 0 for healthy results, 1 for policy findings, and 2 for collection/config/state failure (with partial reports on collection failure).

Six public adapters cover OpenAI, Anthropic, Gemini, DeepSeek, Mistral, and xAI. The optional authenticated OpenAI account-visible collector was deliberately deferred from this release: the core workflow is keyless public monitoring, while account credentials, account scopes, and pagination need a separate reviewed subsystem. No API key is read, and account access is never inferred from public docs.

## Compatibility and delivery decisions

- Runtime dependencies: Python standard library only; setuptools build backend; `unittest` tests.
- Official endpoints and parsing layouts are confirmed against current official sources before adapter implementation. Small real-layout fixtures retain provenance; synthetic fixtures exercise drift and failure cases. Full fetched vendor pages are excluded.
- Public collection and CI require no secrets. Scheduled workflow runs every six hours, validates state before saving to an attempt-specific cache key, and always uploads reports; missing/expired restored state is a fresh baseline.
- MIT license, English README, Chinese quickstart, contributor/security guidance, offline CI, example watchlist, and custom official JSON/OpenAPI source configuration ship with the package.
- State, reports, local config, credentials, caches, and scratch data stay out of version control.

## Acceptance criteria

1. Installable package exports `collect`, `evaluate`, and `save_state`; CLI help, demo, collect/check/smoke work. The optional authenticated account scope is omitted with the documented boundary above.
2. Six verified adapters have small provenance fixtures and live smoke reports URL, source health, count, and timestamp.
3. Tests cover baseline behavior, two-good-snapshot disappearance, source/scope/parser rebaseline, deterministic dedupe, retirement dates and thresholds, live offline reevaluation, malformed state, atomic-write failure, lock contention, network bounds, source isolation, ID boundaries, custom schemas, and optional-auth pagination policy.
4. Failed, empty, stale, or shape-drifted sources never overwrite last-good state or create removal events. First-run baseline suppresses catalog/doc spam but still reports watched authoritative deadlines.
5. Full offline suite passes; package install/help/demo and live smoke are recorded in `docs/verification.md`. Every unavailable or failed live source is called out without claiming full coverage.
6. Git diff checks pass and a scoped initial commit contains only intended project files.
