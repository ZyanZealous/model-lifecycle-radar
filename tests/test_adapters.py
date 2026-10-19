from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from model_lifecycle_radar.adapters import default_sources, parse_source, resolve_followups
from model_lifecycle_radar.adapters.common import AdapterError
from model_lifecycle_radar.fetch import FetchError, FetchResponse
from model_lifecycle_radar.schema import SourceSpec


ROOT = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
FIXTURES = {
    "openai.catalog": "openai_catalog.html",
    "anthropic.catalog": "anthropic_catalog.md",
    "gemini.catalog": "gemini_catalog.html",
    "deepseek.catalog": "deepseek_catalog.html",
    "mistral.catalog": "mistral_models_overview.md",
    "xai.catalog": "xai_catalog.md",
}
EXPECTED = {
    "openai.catalog": {"gpt-6-astra", "gpt-6-luna"},
    "anthropic.catalog": {"claude-fable-5-1", "claude-opus-5-5"},
    "gemini.catalog": {"gemini-4-argon", "gemini-3.8-flash"},
    "deepseek.catalog": {"deepseek-flash", "deepseek-v4-pro"},
    "mistral.catalog": {"mistral-medium-3-5", "mistral-medium-3", "mistral-medium-latest", "mistral-large-2512", "mistral-large-latest"},
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
                if source_id == "mistral.catalog":
                    from tests.support import MappingFetcher
                    parsed = resolve_followups(source, parsed, fetcher=MappingFetcher({
                        "https://docs.mistral.ai/models/mistral-medium-3-5-26-04.md": (ROOT / "mistral_medium_detail.md").read_bytes(),
                        "https://docs.mistral.ai/models/mistral-large-3-25-12.md": (ROOT / "mistral_large_detail.md").read_bytes(),
                        "https://docs.mistral.ai/models/timestral-1-0-209m.md": (ROOT / "mistral_timestral_detail.md").read_bytes(),
                    }), now=NOW, max_bytes=2 * 1024 * 1024)
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
        parsed = parse_source(source, FetchResponse((ROOT / "mistral_catalog.html").read_bytes(), source.url, {}), NOW)
        self.assertEqual(parsed.models, [])
        notice = next(n for n in parsed.notices if n.model_id == "mistral-medium-2508")
        self.assertEqual(notice.effective_date.isoformat(), "2026-08-31")
        self.assertIsNone(notice.replacement)

    def test_mistral_catalog_uses_current_markdown_scope_and_parser_revision(self):
        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        self.assertEqual(source.url, "https://docs.mistral.ai/models.md")
        self.assertGreaterEqual(int(source.parser_revision), 2)
        self.assertIn("active model cards", source.coverage.lower())

    def test_mistral_unrecognized_active_status_fails_closed(self):
        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        body = (ROOT / "mistral_models_overview.md").read_bytes().replace(b"| GA |", b"| Previewing |", 1)
        with self.assertRaises(AdapterError) as raised:
            parse_source(source, FetchResponse(body, source.url, {}), NOW)
        self.assertEqual(raised.exception.code, "unknown_model_status")

    def test_mistral_missing_retirement_table_fails_closed(self):
        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        body = (ROOT / "mistral_models_overview.md").read_bytes().split(b"## Deprecated and retired models", 1)[0]
        with self.assertRaises(AdapterError) as raised:
            parse_source(source, FetchResponse(body, source.url, {}), NOW)
        self.assertEqual(raised.exception.code, "missing_retirement_structure")

    def test_mistral_active_cards_resolve_exact_ids_from_injected_detail_pages(self):
        from model_lifecycle_radar import collect
        from model_lifecycle_radar.schema import RadarConfig, RadarState
        from tests.support import MappingFetcher

        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        overview = (ROOT / "mistral_models_overview.md").read_bytes()
        medium_url = "https://docs.mistral.ai/models/mistral-medium-3-5-26-04.md"
        large_url = "https://docs.mistral.ai/models/mistral-large-3-25-12.md"
        timestral_url = "https://docs.mistral.ai/models/timestral-1-0-209m.md"
        fetcher = MappingFetcher({
            source.url: overview,
            medium_url: (ROOT / "mistral_medium_detail.md").read_bytes(),
            large_url: (ROOT / "mistral_large_detail.md").read_bytes(),
            timestral_url: (ROOT / "mistral_timestral_detail.md").read_bytes(),
        })

        result = collect(RadarConfig((source,)), RadarState.empty(NOW), fetcher=fetcher, now=NOW)
        self.assertTrue(result.ok, result.source_errors)
        models = {model.model_id: model for model in result.state.sources[source.source_id].last_good.models}
        model_ids = set(models)
        self.assertTrue({"mistral-medium-3-5", "mistral-large-2512"} <= model_ids)
        self.assertEqual(models["mistral-medium-3-5"].evidence.source_url, medium_url)
        self.assertEqual(models["mistral-large-2512"].evidence.source_url, large_url)
        self.assertNotIn("mistral-medium-3-5-26-04", model_ids)
        self.assertNotIn("mistral-medium-2505", model_ids)
        self.assertNotIn("timestral-1-0-209m", model_ids)
        notices = result.state.sources[source.source_id].last_good.notices
        self.assertEqual([(notice.model_id, notice.effective_date.isoformat()) for notice in notices],
                         [("mistral-medium-2505", "2026-08-31")])
        self.assertEqual(set(fetcher.calls), {source.url, medium_url, large_url, timestral_url})

    def test_mistral_detail_failure_is_source_failure_and_retains_last_good(self):
        from model_lifecycle_radar import collect
        from model_lifecycle_radar.schema import RadarConfig, RadarState
        from tests.support import MappingFetcher

        source = next(s for s in default_sources() if s.source_id == "mistral.catalog")
        medium_url = "https://docs.mistral.ai/models/mistral-medium-3-5-26-04.md"
        large_url = "https://docs.mistral.ai/models/mistral-large-3-25-12.md"
        overview = (ROOT / "mistral_models_overview.md").read_bytes()
        good = {
            source.url: overview,
            medium_url: (ROOT / "mistral_medium_detail.md").read_bytes(),
            large_url: (ROOT / "mistral_large_detail.md").read_bytes(),
            "https://docs.mistral.ai/models/timestral-1-0-209m.md": (ROOT / "mistral_timestral_detail.md").read_bytes(),
        }
        initial = collect(RadarConfig((source,)), RadarState.empty(NOW), fetcher=MappingFetcher(good), now=NOW)
        previous_ids = {model.model_id for model in initial.state.sources[source.source_id].last_good.models}

        partial = dict(good)
        partial[large_url] = FetchError("http_503", "synthetic upstream failure")
        failed = collect(RadarConfig((source,)), initial.state, fetcher=MappingFetcher(partial), now=NOW)
        record = failed.state.sources[source.source_id]
        self.assertFalse(failed.ok)
        self.assertEqual(record.health, "unhealthy")
        self.assertEqual(record.error_code, "http_503")
        self.assertEqual({model.model_id for model in record.last_good.models}, previous_ids)

    def test_gemini_br_separated_endpoints_remain_two_exact_ids(self):
        source = next(s for s in default_sources() if s.source_id == "gemini.catalog")
        parsed = parse_source(source, FetchResponse((ROOT / "gemini_multi_endpoint.html").read_bytes(), source.url, {}), NOW)
        ids = {model.model_id for model in parsed.models}
        self.assertEqual(ids, {"gemini-3.5-transcribe", "gemini-3.5-transcribe-live"})
        self.assertNotIn("gemini-3.5-transcribegemini-3.5-transcribe-live", ids)

    def test_gemini_br_separated_lifecycle_cells_preserve_each_exact_id(self):
        source = next(s for s in default_sources() if s.source_id == "gemini.lifecycle")
        response = FetchResponse((ROOT / "gemini_lifecycle_multi_endpoint.html").read_bytes(), source.url, {})
        parsed = parse_source(source, response, NOW)
        self.assertEqual({notice.model_id for notice in parsed.notices}, {"gemini-old-a", "gemini-old-b"})
        self.assertEqual({notice.replacement for notice in parsed.notices}, {"gemini-new-a"})

    def test_deepseek_model_version_is_display_metadata_not_callable_id(self):
        source = next(s for s in default_sources() if s.source_id == "deepseek.catalog")
        parsed = parse_source(source, FetchResponse((ROOT / FIXTURES[source.source_id]).read_bytes(), source.url, {}), NOW)
        by_id = {model.model_id: model for model in parsed.models}
        self.assertEqual(set(by_id), {"deepseek-flash", "deepseek-v4-pro"})
        self.assertEqual(by_id["deepseek-flash"].display_name, "DeepSeek-V4.1-Flash-1017")

    def test_deepseek_changes_uses_trailing_slash_and_new_parser_revision(self):
        source = next(s for s in default_sources() if s.source_id == "deepseek.changes")
        self.assertEqual(source.url, "https://api-docs.deepseek.com/updates/")
        self.assertGreaterEqual(int(source.parser_revision), 2)

    def test_deepseek_changes_rejects_soft_fallback_and_undated_pages(self):
        source = next(s for s in default_sources() if s.source_id == "deepseek.changes")
        for fixture in ("deepseek_homepage.html", "deepseek_undated.html"):
            with self.subTest(fixture=fixture), self.assertRaises(AdapterError):
                parse_source(source, FetchResponse((ROOT / fixture).read_bytes(), source.url, {}), NOW)

    def test_no_page_wide_identifier_regex_and_no_tentative_minimum_deadline(self):
        sources = {source.source_id: source for source in default_sources()}
        for source_id, filename in FIXTURES.items():
            source = sources[source_id]
            parsed = parse_source(source, FetchResponse((ROOT / filename).read_bytes(), source.url, {}), NOW)
            if source_id == "mistral.catalog":
                from tests.support import MappingFetcher
                parsed = resolve_followups(source, parsed, fetcher=MappingFetcher({
                    "https://docs.mistral.ai/models/mistral-medium-3-5-26-04.md": (ROOT / "mistral_medium_detail.md").read_bytes(),
                    "https://docs.mistral.ai/models/mistral-large-3-25-12.md": (ROOT / "mistral_large_detail.md").read_bytes(),
                    "https://docs.mistral.ai/models/timestral-1-0-209m.md": (ROOT / "mistral_timestral_detail.md").read_bytes(),
                }), now=NOW, max_bytes=2 * 1024 * 1024)
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
