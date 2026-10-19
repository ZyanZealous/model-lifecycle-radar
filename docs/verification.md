# Verification ledger

Date: 2026-10-19 (UTC). Runtime: Windows, Python 3.12. The review-fix run used no credentials. Full fetched vendor pages, state, and reports stay outside Git under `.radar/`.

## Review fixes

1. **Mistral active scope:** switched the catalog to `https://docs.mistral.ai/models.md`. Active cards are discovered from the official `Model/API name/Status` table and all active detail links are fetched through the same injectable, aggregate-bounded fetcher. Exact IDs come only from the labeled API-name cell or detail-page `API names` field. Detail titles must match their overview card; active cards with neither published API ID are excluded, never slug-derived. Missing active/retirement table structure or unknown active status fails closed. Retirement rows are stored only as notices. `mistral-medium-3-5` and `mistral-large-2512` have evidence on their respective official detail pages.
2. **DeepSeek changes:** source is now `https://api-docs.deepseek.com/updates/` (`/updates` could soft-fallback). The parser requires an exact `Change Log` title and at least one dated entry; catalog/doc scope and parser revisions were bumped. `MODEL VERSION` remains display metadata, while callable IDs come only from `MODEL`.
3. **ID boundaries:** Gemini `<br>` and code-cell boundaries produce independent catalog and lifecycle IDs. Tests reject concatenated pseudo-IDs. DeepSeek versions do not enter the callable catalog.
4. **Failed incompatible sources:** an unsuccessful collection preserves the last-good source identity with its data. Recovery under a new revision sends no old validators and emits a rebaseline reminder without model-added/removal churn.
5. **HTTP 304:** a compatible 304 preserves the last-good snapshot and confirms one pending catalog absence. The second confirmation emits one deduplicated disappearance review, with no generic documentation event.
6. **Scheduled cache:** workflow runs at `17 */6 * * *`; restore and save are separate, keys include `github.run_attempt`, and only state that passes `load_state` validation is cached before collection failure propagation. Reports and state artifacts upload with `always()`. Expired/missing cache still resets the observation baseline.
7. **Smoke empty catalogs:** smoke rejects a catalog with no parsed model IDs as unhealthy `empty_catalog` / exit 2, matching collection; zero-model lifecycle/document sources remain valid.

## Commands and results

| Check | Result |
| --- | --- |
| Empty-catalog smoke regression | RED before guard (retirement-only Mistral catalog returned exit 0); GREEN after guard (catalog unhealthy, count unset, `empty_catalog`, exit 2; lifecycle source healthy). |
| New review regressions before fixes | 9 targeted test methods reproduced the six issues; 15 failures recorded in the ignored `.radar/red-regressions.log`. |
| Focused regressions after fixes | 13 targeted tests passed; after the live Mistral layout exposed cards without API names, 4 affected Mistral regressions passed again. |
| `python -m unittest discover -s tests` | 57 offline tests passed. |
| Editable install, CLI help, and synthetic demo | Verified during the initial implementation. The CLI command surface did not change in this fix pass; the full suite also exercises the demo. |
| `python -m model_lifecycle_radar smoke --vendor all --out .radar/final-smoke` | 2026-10-19 21:39:54 UTC: all 6 provider catalogs and 13/13 registered sources healthy. |
| `python -m model_lifecycle_radar collect --state .radar/final-live-state.json --watchlist watchlist.json --out .radar/final-live-collect` | 13/13 sources healthy; 1 current example-watchlist finding; 10 new events. Four were scope/parser rebaseline reminders and six were Gemini lifecycle announcements. No revised-catalog model-added/removal events. |
| `python -m model_lifecycle_radar check --state .radar/final-live-state.json --watchlist watchlist.json --out .radar/final-live-check` | Offline reevaluation: 13/13 healthy; same 1 example finding; 0 new events. |

Final live source results (smoke timestamp above):

| Provider | Current catalog count and source | Lifecycle / change evidence | Status |
| --- | --- | --- | --- |
| OpenAI | 3 IDs — [Models](https://developers.openai.com/api/docs/models) | 124 structured notices; 37 changelog sections | Healthy |
| Anthropic | 4 IDs — [Models overview](https://platform.claude.com/docs/en/models/overview.md) | 43 structured notices | Healthy |
| Gemini | 47 IDs — [Models](https://ai.google.dev/gemini-api/docs/models) | 52 structured notices; 144 changelog sections | Healthy |
| DeepSeek | 2 callable IDs — [Pricing/model details](https://api-docs.deepseek.com/quick_start/pricing/) | 47 sections from [Change Log](https://api-docs.deepseek.com/updates/); parsed title `Change Log`, dated section `2026-10-17` | Healthy |
| Mistral | 37 exact API IDs — [Models overview](https://docs.mistral.ai/models.md) | 43 separate historical retirement notices; lifecycle policy: 7 sections. `mistral-medium-3-5` and `mistral-large-2512` cite their official detail pages. | Healthy |
| xAI | 3 IDs — [Models](https://docs.x.ai/developers/models) | No linked lifecycle/changelog source was verified | Catalog healthy; lifecycle unavailable |

## Limits

- These are public documentation scopes, not account-specific availability. The optional authenticated OpenAI catalog remains omitted; no key is read or needed.
- A document/schema change asks for review; it does not establish a breaking API change. The tool does not choose replacements. Missing catalog IDs are not retirement evidence; disappearance requires two successful comparable observations.
- Mistral’s active overview currently exposes 23 cards; five detail pages lack a published API-name field and have blank API-name cells, so they contribute no callable IDs. Detail requests still run and validate titles. Current callable count is 37 because some model cards publish multiple aliases.
- The example watchlist intentionally contains a placeholder ID, which accounts for the one current finding in collect/check.
- Request caps are 2 MiB per response, 16 MiB aggregate, 15 seconds per request, and 150 seconds per run. Mistral detail requests share the same budget.
- If scheduled Actions cache expires or is unavailable, collection establishes a fresh baseline and cannot reconstruct missed observations. Reports remain available as artifacts.
