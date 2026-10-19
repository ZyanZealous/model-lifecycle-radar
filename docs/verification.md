# Verification ledger

Date: 2026-10-19 (UTC). Runtime: Windows, Python 3.12.

## Decisions and coverage

- The provided Sol design was materialized in `docs/superpowers/specs/2026-10-19-model-lifecycle-radar-design.md`; implementation tasks are recorded in `docs/superpowers/plans/2026-10-19-model-lifecycle-radar.md`.
- Collection is public and keyless. The optional authenticated OpenAI account catalog was omitted because account credentials, account scope, and pagination need a separate reviewed subsystem. No authentication or inference code is present.
- The source registry contains six catalogs and seven lifecycle/document review sources. OpenAI and Gemini changelog candidates were verified during implementation and added. No unlinked xAI lifecycle URL was guessed; its model page is monitored for section changes.
- Fixtures are focused excerpts/transcriptions with provenance. Full vendor pages and generated state/reports are excluded from the repository.

## Commands and results

| Check | Result |
| --- | --- |
| Tests-first run: `python -m unittest discover -s tests -v` before implementation | Expected red: package imports were absent. |
| `python -m unittest discover -s tests -v` | 43 offline tests passed. |
| `python -m pip install --no-deps -e .` | Editable package built and installed; no runtime dependencies. |
| `python -m model_lifecycle_radar --help`; `python -m model_lifecycle_radar demo --out .radar/demo` | Help worked; demo wrote schema-versioned JSON, Markdown, and events with a synthetic deadline finding. |
| `python -m model_lifecycle_radar smoke --vendor all --out .radar/smoke-final` | 2026-10-19 21:15:20 UTC: 6/6 catalogs and 13/13 registered sources healthy. |
| `python -m model_lifecycle_radar collect --state .radar/live-state.json --watchlist watchlist.json --out .radar/live-collect-final` | 13/13 healthy; 1 current finding from the intentionally unconfigured example watchlist ID; 53 new evidence-backed events against the saved working snapshot; exit 0. |
| `python -m model_lifecycle_radar check --state .radar/live-state.json --watchlist watchlist.json --out .radar/live-check-final` | Offline reevaluation: 13/13 sources healthy; the same 1 example-watchlist finding; 0 new events; exit 0. |

Final public provider smoke:

| Provider | Catalog | Lifecycle / document review | Status |
| --- | --- | --- | --- |
| OpenAI | 3 model IDs — [Models](https://developers.openai.com/api/docs/models) | Deprecations: 124 structured notices; changelog: 37 sections | Healthy |
| Anthropic | 4 model IDs — [Models overview](https://platform.claude.com/docs/en/models/overview.md) | Model deprecations: 43 structured notices | Healthy |
| Gemini | 46 model IDs — [Models](https://ai.google.dev/gemini-api/docs/models) | Deprecations: 52 structured notices; changelog: 144 sections | Healthy |
| DeepSeek | 4 model IDs — [Pricing/model details](https://api-docs.deepseek.com/quick_start/pricing/) | Updates: 3 normalized sections; no dated model notices parsed | Healthy |
| Mistral | 43 model IDs — [Models](https://docs.mistral.ai/models); 43 paired-date catalog notices | Lifecycle policy: 7 normalized sections; no model-specific dates parsed | Healthy |
| xAI | 3 model IDs — [Models](https://docs.x.ai/developers/models) | No catalog-linked lifecycle/changelog source found in the source review | Catalog healthy; lifecycle coverage unavailable |

## Parser and policy limits

- Public model IDs come only from provider-specific structural cues such as labeled API/model ID fields, table columns, or explicit official model-detail links. Empty or structurally changed pages fail closed.
- The captured xAI Markdown layout differed from the live HTML model-card page. The adapter accepts explicit official `/developers/models/<id>` links; the final live count is 3. It does not scan page text for arbitrary model-like strings.
- Anthropic's “not sooner than” and explicitly tentative retirement dates remain context and do not trigger deadlines. Mistral's paired dates use the retirement column only; marketing-name alternatives are not treated as API IDs.
- First compatible snapshots establish baselines for catalog/document changes. Existing watchlist retirement evidence is still evaluated on the first run. Disappearance requires two consecutive good snapshots.
- The example watchlist intentionally contains a placeholder ID; replace it with an exact model ID before relying on findings for a real service.
- On the final collection, 53 events were emitted from changes relative to the saved working snapshot: Anthropic lifecycle 21, Gemini catalog 21 and lifecycle 2, OpenAI catalog 3, and xAI catalog 6. The following offline check emitted no new events.
- Scheduled workflow state restoration is best-effort. Missing or expired state establishes a fresh baseline and cannot reconstruct missed observations. Reports are uploaded even when collection fails.
