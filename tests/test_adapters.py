from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from model_lifecycle_radar.adapters import default_sources, parse_source
from model_lifecycle_radar.fetch import FetchResponse
from model_lifecycle_radar.schema import SourceSpec


ROOT = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
FIXTURES = {
    "openai.catalog": "openai_catalog.html",
    "anthropic.catalog": "anthropic_catalog.md",
    "gemini.catalog": "gemini_catalog.html",
    "deepseek.catalog": "deepseek_catalog.html",
    "mistral.catalog": "mistral_catalog.html",
    "xai.catalog": "xai_catalog.md",
}
EXPECTED = {
    "openai.catalog": {"gpt-6-astra", "gpt-6-luna"},
    "anthropic.catalog": {"claude-fable-5-1", "claude-opus-5-5"},
    "gemini.catalog": {"gemini-4-argon", "gemini-3.8-flash"},
    "deepseek.catalog": {"deepseek-flash", "deepseek-v4-pro", "DeepSeek-V4.1-Flash-1017", "DeepSeek-V4-Pro-0813"},
    "mistral.catalog": {"mistral-medium-2508", "mistral-small-2506"},
    "xai.catalog": {"grok-4.7", "grok-4.20-0309-reasoning"},
}


class CatalogFixtureTests(unittest.TestCase):
    def test_six_official_catalog_layouts_extract_only_structurally_labeled_ids(self):
        sources = {source.source_id: source for source in default_sources()}
        for source_id, filename in FIXTURES.items():
            with self.subTest(source=source_id):
                source = sources[source_id]
                body = (ROOT / filename).read_bytes()
                parsed = parse_source(source, FetchResponse(body, source.url, {}), NOW)
                self.assertEqual({model.model_id for model in parsed.models}, EXPECTED[source_id])
                self.assertTrue(parsed.sections)

    def test_structured_retirement_pages_parse_dates_and_exact_replacements(self):
        sources = {source.source_id: source for source in default_sources()}
        expected = {
            "openai.lifecycle": ("gpt-5.3-codex", "2027-04-01", "gpt-6-sol"),
            "anthropic.lifecycle": ("claude-sonnet-4-5-20250929", "2026-11-30", None),
            "gemini.lifecycle": ("gemini-3.1-flash-tts-preview", "2026-11-17", "gemini-3.8-flash-tts"),
        }
        fixture_map = {
            "openai.lifecycle": "openai_retirements.md",
            "anthropic.lifecycle": "anthropic_retirements.md",
            "gemini.lifecycle": "gemini_retirements.html",
        }
        for source_id, fixture in fixture_map.items():
            with self.subTest(source=source_id):
                source = sources[source_id]
                parsed = parse_source(source, FetchResponse((ROOT / fixture).read_bytes(), source.url, {}), NOW)
                notice = next(n for n in parsed.notices if n.model_id == expected[source_id][0])
                self.assertEqual(notice.effective_date.isoformat(), expected[source_id][1])
                self.assertEqual(notice.replacement, expected[source_id][2])
                self.assertEqual(notice.evidence.confidence, "structured")

    def test_mistral_two_date_cells_map_retirement_to_second_authoritative_date(self):
        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        parsed = parse_source(source, FetchResponse((ROOT / FIXTURES[source.source_id]).read_bytes(), source.url, {}), NOW)
        notice = next(n for n in parsed.notices if n.model_id == "mistral-medium-2508")
        self.assertEqual(notice.effective_date.isoformat(), "2026-08-31")
        self.assertIsNone(notice.replacement)

    def test_no_page_wide_identifier_regex_and_no_tentative_minimum_deadline(self):
        sources = {source.source_id: source for source in default_sources()}
        for source_id, filename in FIXTURES.items():
            parsed = parse_source(sources[source_id], FetchResponse((ROOT / filename).read_bytes(), sources[source_id].url, {}), NOW)
            self.assertFalse(any("not-a" in model.model_id or "unlabeled" in model.model_id for model in parsed.models))
        anthropic = next(s for s in default_sources() if s.source_id == "anthropic.lifecycle")
        parsed = parse_source(anthropic, FetchResponse((ROOT / "anthropic_retirements.md").read_bytes(), anthropic.url, {}), NOW)
        active = next(n for n in parsed.notices if n.model_id == "claude-fable-5-1")
        self.assertIsNone(active.effective_date)
        self.assertFalse(active.deadline_authoritative)
        retired = next(n for n in parsed.notices if n.model_id == "claude-opus-4-1-20250805")
        self.assertEqual(retired.effective_date.isoformat(), "2026-08-05")
        self.assertTrue(retired.deadline_authoritative)

    def test_current_xai_model_cards_use_explicit_detail_links(self):
        source = next(s for s in default_sources() if s.source_id == "xai.catalog")
        parsed = parse_source(source, FetchResponse((ROOT / "xai_catalog.html").read_bytes(), source.url, {}), NOW)
        self.assertEqual({model.model_id for model in parsed.models}, {
            "grok-4.7", "grok-imagine-image-2.0", "grok-imagine-video-1.5",
        })

    def test_custom_json_fingerprint_is_canonical_and_never_parses_model_ids(self):
        source = SourceSpec(
            source_id="company.openapi", vendor="company", kind="custom_schema",
            url="https://api.example.test/openapi.json", allowed_hosts=("api.example.test",),
            parser="json_fingerprint", scope="public", scope_revision="spec-v1", parser_revision="1",
        )
        a = parse_source(source, FetchResponse(b'{"paths":{"/a":{}},"openapi":"3.1.0"}', source.url, {}), NOW)
        b = parse_source(source, FetchResponse(b'{ "openapi":"3.1.0", "paths": {"/a": {}} }', source.url, {}), NOW)
        self.assertEqual(a.sections[0].content_hash, b.sections[0].content_hash)
        self.assertEqual(a.models, [])
        self.assertTrue(any("/a" in section.excerpt for section in a.sections))

    def test_all_verified_change_sources_have_a_small_parseable_fixture(self):
        sources = {source.source_id: source for source in default_sources()}
        fixtures = {
            "deepseek.changes": "deepseek_updates.html",
            "mistral.lifecycle": "mistral_lifecycle.html",
            "openai.changelog": "openai_changelog.html",
            "gemini.changelog": "gemini_changelog.html",
        }
        for source_id, filename in fixtures.items():
            with self.subTest(source=source_id):
                source = sources[source_id]
                parsed = parse_source(source, FetchResponse((ROOT / filename).read_bytes(), source.url, {}), NOW)
                self.assertTrue(parsed.sections)
                self.assertEqual(parsed.models, [])
                self.assertEqual(parsed.notices, [])


if __name__ == "__main__":
    unittest.main()
