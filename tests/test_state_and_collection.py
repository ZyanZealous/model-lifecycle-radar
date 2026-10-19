from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from model_lifecycle_radar import collect, evaluate, load_state, save_state
from model_lifecycle_radar.evaluate import Policy
from model_lifecycle_radar.schema import (
    Evidence,
    LastGoodSnapshot,
    ModelRecord,
    NoticeRecord,
    RadarConfig,
    RadarState,
    SourceSpec,
    StateError,
    WatchlistEntry,
    watchlist_from_dict,
)
from tests.support import MappingFetcher, json_catalog


NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
SOURCE = SourceSpec(
    source_id="demo.catalog",
    vendor="demo",
    kind="catalog",
    url="https://catalog.example.test/models.json",
    allowed_hosts=("catalog.example.test",),
    parser="json_catalog",
    scope="public",
    scope_revision="public-v1",
    parser_revision="1",
)


def config(source: SourceSpec = SOURCE) -> RadarConfig:
    return RadarConfig(sources=(source,))


def fresh_state() -> RadarState:
    return RadarState.empty(NOW)


def collect_ids(*ids: str, state: RadarState | None = None, source: SourceSpec = SOURCE):
    return collect(
        config(source),
        state or fresh_state(),
        fetcher=MappingFetcher({source.url: json_catalog(*ids)}),
        now=NOW,
    )


class CollectionTests(unittest.TestCase):
    def test_first_collection_is_quiet_baseline_with_healthy_snapshot(self):
        result = collect_ids("alpha", "beta")
        self.assertTrue(result.ok)
        self.assertEqual(result.events, [])
        snapshot = result.state.sources[SOURCE.source_id].last_good
        self.assertEqual({m.model_id for m in snapshot.models}, {"alpha", "beta"})
        self.assertEqual(result.state.generation, 1)

    def test_new_model_emits_once_with_timestamp_independent_id(self):
        initial = collect_ids("alpha").state
        changed = collect_ids("alpha", "beta", state=initial)
        observed = [event for event in changed.events if event.event_type == "model.observed"]
        self.assertEqual(len(observed), 1)
        event_id = observed[0].event_id
        repeated = collect_ids("alpha", "beta", state=changed.state)
        self.assertFalse(any(event.event_id == event_id for event in repeated.events))

    def test_disappearance_requires_two_comparable_good_snapshots(self):
        initial = collect_ids("alpha").state
        first_absence = collect_ids("beta", state=initial)
        self.assertFalse(any(e.event_type == "model.disappearance_review" for e in first_absence.events))
        second_absence = collect_ids("beta", state=first_absence.state)
        removals = [e for e in second_absence.events if e.event_type == "model.disappearance_review"]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0].subject, "alpha")

    def test_failed_source_preserves_last_good_and_cannot_advance_disappearance(self):
        initial = collect_ids("alpha").state
        failure = collect(
            config(), initial,
            fetcher=MappingFetcher({SOURCE.url: OSError("offline")}), now=NOW,
        )
        record = failure.state.sources[SOURCE.source_id]
        self.assertEqual(record.health, "unhealthy")
        self.assertEqual([m.model_id for m in record.last_good.models], ["alpha"])
        self.assertEqual(record.pending_disappearances, {})
        self.assertFalse(failure.ok)

    def test_unrecognized_empty_catalog_is_parser_failure_and_preserves_good_state(self):
        initial = collect_ids("alpha").state
        for malformed in (b"{}", b"{\"models\": []}", b"<html><body>alpha beta</body></html>"):
            result = collect(
                config(), initial,
                fetcher=MappingFetcher({SOURCE.url: malformed}), now=NOW,
            )
            record = result.state.sources[SOURCE.source_id]
            self.assertEqual(record.health, "unhealthy")
            self.assertEqual([m.model_id for m in record.last_good.models], ["alpha"])

    def test_changed_scope_revision_rebaselines_without_removal(self):
        initial = collect_ids("alpha").state
        revised = SourceSpec(**{**SOURCE.__dict__, "scope_revision": "public-v2"})
        result = collect_ids("beta", source=revised, state=initial)
        self.assertTrue(any(e.event_type == "source.rebaseline_required" for e in result.events))
        self.assertFalse(any(e.event_type == "model.disappearance_review" for e in result.events))
        self.assertEqual(result.state.sources[revised.source_id].scope_revision, "public-v2")

    def test_duplicate_identifiers_fail_closed(self):
        body = b'{"models":[{"id":"alpha"},{"id":"alpha"}]}'
        result = collect(config(), fresh_state(), fetcher=MappingFetcher({SOURCE.url: body}), now=NOW)
        self.assertFalse(result.ok)
        self.assertEqual(result.state.sources[SOURCE.source_id].health, "unhealthy")

    def test_missing_known_identifier_starts_new_pending_streak(self):
        initial = collect_ids("alpha").state
        failed = collect(
            config(), initial,
            fetcher=MappingFetcher({SOURCE.url: OSError("timeout")}), now=NOW,
        )
        returned = collect_ids("alpha", state=failed.state)
        self.assertEqual(returned.state.sources[SOURCE.source_id].pending_disappearances, {})


class EvaluationTests(unittest.TestCase):
    def state_with_deadline(self, deadline: date) -> RadarState:
        state = collect_ids("watched-model").state
        evidence = Evidence(
            source_url="https://catalog.example.test/deprecations",
            observed_at=NOW,
            rule_id="demo.lifecycle.table-v1",
            excerpt="watched-model shutdown date",
            content_hash="a" * 64,
            confidence="structured",
        )
        source = state.sources[SOURCE.source_id]
        source.last_good.notices = [
            NoticeRecord(
                vendor="demo", model_id="watched-model", status="retirement_announced",
                effective_date=deadline, replacement=None, evidence=evidence,
            )
        ]
        return state

    def test_first_run_existing_deadline_is_still_actionable(self):
        state = self.state_with_deadline(date(2026, 10, 26))
        report = evaluate(
            state, [WatchlistEntry("demo", "watched-model", "payments", "billing")],
            Policy(), now=NOW,
        )
        self.assertTrue(any(f.finding_type == "retirement.threshold" for f in report.current_findings))
        self.assertTrue(any(e.event_type == "retirement.threshold" for e in report.emitted_events))

    def test_deadline_bands_and_overdue_boundaries(self):
        cases = [
            (30, "30d", "warning"),
            (14, "14d", "warning"),
            (7, "7d", "error"),
            (0, "7d", "error"),
            (-1, "overdue", "error"),
        ]
        watchlist = [WatchlistEntry("demo", "watched-model", "payments", "billing")]
        for days, expected_band, severity in cases:
            with self.subTest(days=days):
                state = self.state_with_deadline(date.fromordinal(NOW.date().toordinal() + days))
                report = evaluate(state, watchlist, Policy(), now=NOW)
                item = next(f for f in report.current_findings if f.finding_type.startswith("retirement."))
                self.assertEqual(item.band, expected_band)
                self.assertEqual(item.severity, severity)

    def test_not_verified_finding_uses_current_owner_without_claiming_retirement(self):
        state = collect_ids("other-model").state
        old = evaluate(
            state, [WatchlistEntry("demo", "watched-model", "finance", "invoices")],
            Policy(), now=NOW,
        )
        finding = next(f for f in old.current_findings if f.finding_type == "model.not_verified")
        self.assertEqual(finding.owner, "finance")
        self.assertIn("configured scope", finding.summary)
        new = evaluate(
            state, [WatchlistEntry("demo", "watched-model", "risk", "payments")],
            Policy(), now=NOW,
        )
        self.assertEqual(next(f for f in new.current_findings if f.finding_type == "model.not_verified").owner, "risk")

    def test_urgent_threshold_events_are_deduplicated_then_escalated(self):
        state = self.state_with_deadline(date(2026, 11, 20))
        watchlist = [WatchlistEntry("demo", "watched-model", "payments", "billing")]
        first = evaluate(state, watchlist, Policy(), now=NOW)
        self.assertFalse(any(e.event_type == "retirement.threshold" for e in first.emitted_events))
        state.sources[SOURCE.source_id].last_good.notices[0].effective_date = date(2026, 11, 2)
        second = evaluate(state, watchlist, Policy(), now=NOW)
        self.assertTrue(any(e.event_type == "retirement.threshold" and e.band == "14d" for e in second.emitted_events))
        again = evaluate(second.state, watchlist, Policy(), now=NOW)
        self.assertFalse(any(e.event_type == "retirement.threshold" for e in again.emitted_events))


class PersistenceTests(unittest.TestCase):
    def test_complete_good_state_roundtrips_through_versioned_json(self):
        state = collect_ids("alpha").state
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(path, state)
            restored = load_state(path)
            self.assertEqual(restored.schema_version, 1)
            self.assertEqual(restored.sources[SOURCE.source_id].last_good.models[0].model_id, "alpha")

    def test_malformed_or_unsupported_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(StateError):
                load_state(path)
            path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
            with self.assertRaises(StateError):
                load_state(path)

    def test_atomic_replace_failure_keeps_original_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            original = fresh_state()
            save_state(path, original)
            before = path.read_bytes()
            changed = copy.deepcopy(original)
            changed.generation += 1
            with patch("model_lifecycle_radar.state.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_state(path, changed)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_exclusive_writer_lock_rejects_second_writer(self):
        from model_lifecycle_radar.state import StateLock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json.lock"
            with StateLock(path):
                with self.assertRaises(Exception):
                    with StateLock(path):
                        pass

    def test_malformed_deadline_flags_watchlist_types_and_credential_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            SourceSpec("demo.catalog", "demo", "catalog", "https://user:secret@catalog.example.test/models.json",
                       ("catalog.example.test",), "json_catalog", "public", "public-v1", "1")
        with self.assertRaises(StateError):
            watchlist_from_dict({"schema_version": 1, "models": [{
                "vendor": 7, "model": "exact-id", "owner": "owner", "service": "service",
            }]})
        evidence = {
            "source_url": "https://catalog.example.test/deprecations",
            "observed_at": "2026-10-19T12:00:00Z", "rule_id": "table-v1",
            "excerpt": "model retirement", "content_hash": "a" * 64, "confidence": "structured",
        }
        with self.assertRaises(StateError):
            NoticeRecord.from_dict({
                "vendor": "demo", "model_id": "exact-id", "status": "retirement_announced",
                "effective_date": "2026-11-01", "replacement": None,
                "deadline_authoritative": "false", "evidence": evidence,
            })


if __name__ == "__main__":
    unittest.main()
