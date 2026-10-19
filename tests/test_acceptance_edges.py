from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from model_lifecycle_radar import collect, evaluate, load_state, save_state
from model_lifecycle_radar.adapters import default_sources
from model_lifecycle_radar.fetch import FetchResponse
from model_lifecycle_radar.schema import RadarConfig, RadarState, SourceSpec, WatchlistEntry
from tests.support import MappingFetcher, json_catalog


NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
CATALOG = SourceSpec("demo.catalog", "demo", "catalog", "https://catalog.example.test/models.json",
                     ("catalog.example.test",), "json_catalog", "public", "public-v1", "1")


class AcceptanceEdgeTests(unittest.TestCase):
    def test_large_catalog_drop_fails_closed_without_removing_last_good(self):
        start = collect(RadarConfig((CATALOG,)), RadarState.empty(NOW),
                        fetcher=MappingFetcher({CATALOG.url: json_catalog(*[f"model-{i}" for i in range(12)])}), now=NOW)
        failed = collect(RadarConfig((CATALOG,)), start.state,
                         fetcher=MappingFetcher({CATALOG.url: json_catalog("model-0")}), now=NOW)
        record = failed.state.sources[CATALOG.source_id]
        self.assertEqual(record.health, "unhealthy")
        self.assertEqual(len(record.last_good.models), 12)
        self.assertEqual(record.error_code, "suspicious_count_drop")
        self.assertFalse(any(e.event_type == "model.disappearance_review" for e in failed.events))

    def test_changed_parser_revision_and_canonical_url_each_require_rebaseline(self):
        start = collect(RadarConfig((CATALOG,)), RadarState.empty(NOW),
                        fetcher=MappingFetcher({CATALOG.url: json_catalog("alpha")}), now=NOW)
        revised = SourceSpec(**{**CATALOG.__dict__, "parser_revision": "2"})
        result = collect(RadarConfig((revised,)), start.state,
                         fetcher=MappingFetcher({CATALOG.url: json_catalog("beta")}), now=NOW)
        self.assertTrue(any(e.event_type == "source.rebaseline_required" for e in result.events))
        self.assertFalse(any(e.event_type == "model.disappearance_review" for e in result.events))

        class Redirected:
            def fetch(self, url, allowed_hosts, **kwargs):
                return FetchResponse(json_catalog("beta"), "https://catalog.example.test/v2/models.json", {})
        # Use the canonical URL that was stored for the second source and move it once more.
        result2 = collect(RadarConfig((revised,)), result.state, fetcher=Redirected(), now=NOW)
        self.assertTrue(any(e.event_type == "source.rebaseline_required" for e in result2.events))

    def test_failed_revision_preserves_last_good_identity_until_rebaseline(self):
        class ResponseFetcher:
            def __init__(self, response): self.response=response; self.headers=[]
            def fetch(self, url, allowed_hosts, *, headers=None, **kwargs):
                self.headers.append(dict(headers or {}))
                if isinstance(self.response, Exception): raise self.response
                return self.response

        initial_fetcher = ResponseFetcher(FetchResponse(json_catalog("alpha"), CATALOG.url, {"ETag": '"v1"'}))
        initial = collect(RadarConfig((CATALOG,)), RadarState.empty(NOW), fetcher=initial_fetcher, now=NOW)
        revised = SourceSpec(**{**CATALOG.__dict__, "parser_revision": "2"})
        failed_fetcher = ResponseFetcher(OSError("offline"))
        failed = collect(RadarConfig((revised,)), initial.state, fetcher=failed_fetcher, now=NOW)
        retained = failed.state.sources[CATALOG.source_id]
        self.assertEqual(retained.health, "unhealthy")
        self.assertEqual(retained.parser_revision, "1")
        self.assertEqual(retained.requested_url, CATALOG.url)
        self.assertEqual(failed_fetcher.headers, [{}])

        recovery_fetcher = ResponseFetcher(FetchResponse(json_catalog("beta"), CATALOG.url, {}))
        recovered = collect(RadarConfig((revised,)), failed.state, fetcher=recovery_fetcher, now=NOW)
        event_types = [event.event_type for event in recovered.events]
        self.assertEqual(recovery_fetcher.headers, [{}])
        self.assertIn("source.rebaseline_required", event_types)
        self.assertNotIn("model.observed", event_types)
        self.assertNotIn("model.disappearance_review", event_types)
        self.assertEqual(recovered.state.sources[CATALOG.source_id].parser_revision, "2")

    def test_one_bad_provider_does_not_abort_another(self):
        other = SourceSpec("demo.schema", "demo", "custom_schema", "https://schema.example.test/openapi.json",
                           ("schema.example.test",), "json_fingerprint", "public", "schema-v1", "1")
        config = RadarConfig((CATALOG, other))
        state = RadarState.empty(NOW)
        result = collect(config, state, fetcher=MappingFetcher({
            CATALOG.url: b'{"unexpected":[]}',
            other.url: b'{"openapi":"3.1.0","paths":{}}',
        }), now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.state.sources[CATALOG.source_id].health, "unhealthy")
        self.assertEqual(result.state.sources[other.source_id].health, "healthy")
        self.assertIsNotNone(result.state.sources[other.source_id].last_good)

    def test_custom_schema_change_emits_review_only(self):
        spec = SourceSpec("company.openapi", "company", "custom_schema", "https://api.example.test/openapi.json",
                          ("api.example.test",), "json_fingerprint", "public", "spec-v1", "1")
        config = RadarConfig((spec,))
        initial = collect(config, RadarState.empty(NOW), fetcher=MappingFetcher({
            spec.url: b'{"openapi":"3.1.0","paths":{"/invoices":{"get":{}}}}'
        }), now=NOW)
        changed = collect(config, initial.state, fetcher=MappingFetcher({
            spec.url: b'{"openapi":"3.1.0","paths":{"/invoices":{"post":{}}}}'
        }), now=NOW)
        schema_event = next(e for e in changed.events if e.event_type == "schema.changed_review")
        self.assertIn("/invoices", schema_event.evidence.excerpt)
        self.assertFalse(any("breaking" in e.event_type for e in changed.events))

    def test_etag_304_keeps_last_good_and_uses_conditional_headers(self):
        from model_lifecycle_radar.schema import RadarState
        config = RadarConfig((CATALOG,))
        class ConditionalFetcher:
            def __init__(self, response): self.response=response; self.headers=[]
            def fetch(self, url, allowed_hosts, *, headers=None, **kwargs):
                self.headers.append(dict(headers or {})); return self.response
        first_fetcher = ConditionalFetcher(FetchResponse(json_catalog("alpha"), CATALOG.url,
                                                           {"ETag":"\"v1\"", "Last-Modified":"Mon, 19 Oct 2026 12:00:00 GMT"}))
        first = collect(config, RadarState.empty(NOW), fetcher=first_fetcher, now=NOW)
        second_fetcher = ConditionalFetcher(FetchResponse(b"", CATALOG.url, {"ETag":"\"v1\""}, 304))
        second = collect(config, first.state, fetcher=second_fetcher, now=NOW)
        record = second.state.sources[CATALOG.source_id]
        self.assertEqual(second_fetcher.headers[0]["If-None-Match"], '"v1"')
        self.assertEqual(second_fetcher.headers[0]["If-Modified-Since"], "Mon, 19 Oct 2026 12:00:00 GMT")
        self.assertEqual(record.health, "not_modified")
        self.assertEqual([m.model_id for m in record.last_good.models], ["alpha"])
        self.assertTrue(second.ok)

    def test_compatible_304_confirms_pending_absence_once_without_document_change(self):
        class ConditionalFetcher:
            def __init__(self, response): self.response=response; self.headers=[]
            def fetch(self, url, allowed_hosts, *, headers=None, **kwargs):
                self.headers.append(dict(headers or {})); return self.response

        config = RadarConfig((CATALOG,))
        initial = collect(config, RadarState.empty(NOW), fetcher=ConditionalFetcher(
            FetchResponse(json_catalog("alpha"), CATALOG.url, {"ETag": '"v1"'})
        ), now=NOW)
        first_absence = collect(config, initial.state, fetcher=ConditionalFetcher(
            FetchResponse(json_catalog("beta"), CATALOG.url, {"ETag": '"v2"'})
        ), now=NOW)
        self.assertEqual(first_absence.state.sources[CATALOG.source_id].pending_disappearances, {"alpha": 1})

        second_absence = collect(config, first_absence.state, fetcher=ConditionalFetcher(
            FetchResponse(b"", CATALOG.url, {"ETag": '"v2"'}, 304)
        ), now=NOW)
        removals = [event for event in second_absence.events if event.event_type == "model.disappearance_review"]
        self.assertEqual([event.subject for event in removals], ["alpha"])
        record = second_absence.state.sources[CATALOG.source_id]
        self.assertEqual(record.health, "not_modified")
        self.assertEqual(record.pending_disappearances, {"alpha": 2})
        self.assertEqual([model.model_id for model in record.last_good.models], ["beta"])
        self.assertFalse(any(event.event_type.startswith("docs.") for event in second_absence.events))

        repeated = collect(config, second_absence.state, fetcher=ConditionalFetcher(
            FetchResponse(b"", CATALOG.url, {"ETag": '"v2"'}, 304)
        ), now=NOW)
        self.assertFalse(any(event.event_type == "model.disappearance_review" for event in repeated.events))

    def test_policy_change_reevaluates_saved_state_without_fetch(self):
        from model_lifecycle_radar.evaluate import Policy
        from model_lifecycle_radar.schema import Evidence, LastGoodSnapshot, ModelRecord, NoticeRecord, RadarState, SourceState
        evidence = Evidence("https://catalog.example.test/deprecations", NOW, "row", "alpha retirement", "b"*64, "structured")
        model_evidence = Evidence(CATALOG.url, NOW, "id", "alpha", "c"*64, "structured")
        model = ModelRecord("alpha", None, NOW, NOW, model_evidence)
        notice = NoticeRecord("demo", "alpha", "retirement_announced", NOW.date() + timedelta(days=2), None, evidence)
        good = LastGoodSnapshot(NOW, "d"*64, [model], [notice], [], canonical_url=CATALOG.url)
        source = SourceState("demo", "catalog", CATALOG.url, CATALOG.url, "public", "public-v1", "1", NOW, "healthy", None, good)
        state = RadarState(1, 1, NOW, {CATALOG.source_id: source}, {})
        # Two days from the injected clock selects the alternate 2-day band.
        report = evaluate(state, [WatchlistEntry("demo", "alpha", "owner", "service")], Policy((30, 2, 1)), now=NOW)
        finding = next(f for f in report.current_findings if f.finding_type == "retirement.threshold")
        self.assertEqual(finding.band, "2d")
        self.assertTrue(report.emitted_events)
        self.assertEqual(report.to_dict()["schema_version"], 1)

    def test_official_source_registry_has_six_catalogs_and_canonical_hosts(self):
        sources = default_sources()
        catalogs = [source for source in sources if source.kind == "catalog"]
        self.assertEqual({source.vendor for source in catalogs}, {"openai", "anthropic", "gemini", "deepseek", "mistral", "xai"})
        for source in sources:
            self.assertTrue(source.url.startswith("https://"))
            self.assertIn(source.url.split("/")[2], source.allowed_hosts)

    def test_scheduled_workflow_persists_validated_state_before_failure_propagation(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/scheduled-collector.yml").read_text(encoding="utf-8")
        checks = {
            "six-hour schedule": 'cron: "17 */6 * * *"' in workflow,
            "separate cache restore": "actions/cache/restore@v4" in workflow,
            "separate cache save": "actions/cache/save@v4" in workflow,
            "attempt-specific immutable cache key": "github.run_attempt" in workflow,
            "state validation": "load_state" in workflow,
            "state artifact uploaded always": "name: Upload state for audit" in workflow and "if: always()" in workflow,
            "report artifact uploaded always": "name: Upload reports" in workflow and "if: always()" in workflow,
            "save occurs before failure propagation": (
                "actions/cache/save@v4" in workflow
                and workflow.index("actions/cache/save@v4") < workflow.index("name: Propagate collection status")
            ),
        }
        for name, passed in checks.items():
            with self.subTest(contract=name):
                self.assertTrue(passed, name)


if __name__ == "__main__":
    unittest.main()
