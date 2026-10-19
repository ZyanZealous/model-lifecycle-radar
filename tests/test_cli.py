from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from model_lifecycle_radar.cli import main
from model_lifecycle_radar.config import load_config
from model_lifecycle_radar.schema import RadarState, StateError
from model_lifecycle_radar.state import save_state


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1]


class CliTests(unittest.TestCase):
    def test_demo_writes_all_versioned_reports_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "reports"
            self.assertEqual(main(["demo", "--out", str(out)]), 0)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            events = json.loads((out / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], 1)
            self.assertTrue(any(item["finding_type"] == "retirement.threshold" for item in report["current_findings"]))
            self.assertEqual(events["schema_version"], 1)
            self.assertTrue((out / "report.md").exists())

    def test_offline_check_reevaluates_current_watchlist_and_exit_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            watchlist_path = Path(tmp) / "watchlist.json"
            out = Path(tmp) / "reports"
            save_state(state_path, RadarState.empty(NOW))
            watchlist_path.write_text(json.dumps({
                "schema_version": 1,
                "models": [{"vendor": "openai", "model": "gpt-6-luna", "owner": "payments", "service": "billing"}],
            }), encoding="utf-8")
            code = main(["check", "--state", str(state_path), "--watchlist", str(watchlist_path),
                         "--out", str(out), "--fail-on", "warning"])
            self.assertEqual(code, 1)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            finding = report["current_findings"][0]
            self.assertEqual(finding["owner"], "payments")
            self.assertIn("configured scope", finding["summary"])
            self.assertEqual(report["new_events"], [])

    def test_invalid_state_writes_partial_report_and_does_not_overwrite_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            bad = b'{"schema_version":999}'
            state_path.write_bytes(bad)
            watchlist_path = Path(tmp) / "watchlist.json"
            watchlist_path.write_text('{"schema_version":1,"models":[]}', encoding="utf-8")
            out = Path(tmp) / "reports"
            code = main(["check", "--state", str(state_path), "--watchlist", str(watchlist_path), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertEqual(state_path.read_bytes(), bad)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["source_errors"][0]["error_code"], "invalid_state")

    def test_custom_config_adds_only_json_schema_sources(self):
        config = load_config(ROOT / "examples" / "custom-sources.json")
        self.assertIn("company.openapi", {source.source_id for source in config.sources})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"schema_version": 1, "sources": [{
                "source_id": "company.bad", "vendor": "company", "kind": "custom_schema",
                "format": "openapi-json", "url": "https://api.example.com/openapi.json",
            }]}), encoding="utf-8")
            with self.assertRaises(StateError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
