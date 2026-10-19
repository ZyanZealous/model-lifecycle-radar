"""Official public model-source adapters with structure-based extraction."""
from __future__ import annotations

import hashlib
import json

from . import anthropic, deepseek, gemini, mistral, openai, xai
from .common import AdapterError, ParsedSnapshot, decode_response, evidence, exact_id, make_models, make_sections, snapshot
from ..fetch import FetchResponse
from ..schema import ModelRecord, SourceSpec


def default_sources() -> tuple[SourceSpec, ...]:
    """Sources verified against official pages captured on 2026-10-19."""
    return (
        SourceSpec("openai.catalog", "openai", "catalog", "https://developers.openai.com/api/docs/models",
                   ("developers.openai.com",), "openai_catalog", "public", "openai-public-v1", "1",
                   "Explicit Model ID fields in official model cards; does not enumerate account entitlements."),
        SourceSpec("openai.lifecycle", "openai", "lifecycle", "https://developers.openai.com/api/docs/deprecations",
                   ("developers.openai.com",), "openai_lifecycle", "public", "openai-public-v1", "1",
                   "Official shutdown-date/model/replacement tables; other changes are document review only."),
        SourceSpec("openai.changelog", "openai", "docs", "https://developers.openai.com/api/docs/changelog",
                   ("developers.openai.com",), "openai_docs", "public", "openai-public-v1", "1",
                   "Normalized official API changelog sections; changes request review, not a breaking-change verdict."),
        SourceSpec("anthropic.catalog", "anthropic", "catalog", "https://platform.claude.com/docs/en/about-claude/models/overview.md",
                   ("platform.claude.com",), "anthropic_catalog", "public", "anthropic-public-v1", "1",
                   "Claude API ID row in the model comparison table; account availability may vary."),
        SourceSpec("anthropic.lifecycle", "anthropic", "lifecycle", "https://platform.claude.com/docs/en/about-claude/model-deprecations.md",
                   ("platform.claude.com",), "anthropic_lifecycle", "public", "anthropic-public-v1", "1",
                   "Structured model status and tentative retirement date tables; tentative dates do not trigger deadline bands."),
        SourceSpec("gemini.catalog", "gemini", "catalog", "https://ai.google.dev/gemini-api/docs/models",
                   ("ai.google.dev",), "gemini_catalog", "public", "gemini-public-v1", "1",
                   "Model/Endpoint tables; does not represent project-specific access."),
        SourceSpec("gemini.lifecycle", "gemini", "lifecycle", "https://ai.google.dev/gemini-api/docs/deprecations",
                   ("ai.google.dev",), "gemini_lifecycle", "public", "gemini-public-v1", "1",
                   "Model/Shutdown date/Recommended replacement tables; only exact shutdown dates become deadlines."),
        SourceSpec("gemini.changelog", "gemini", "docs", "https://ai.google.dev/gemini-api/docs/changelog",
                   ("ai.google.dev",), "gemini_docs", "public", "gemini-public-v1", "1",
                   "Normalized official release-note sections; changes request review, not a breaking-change verdict."),
        SourceSpec("deepseek.catalog", "deepseek", "catalog", "https://api-docs.deepseek.com/quick_start/pricing",
                   ("api-docs.deepseek.com",), "deepseek_catalog", "public", "deepseek-public-v1", "1",
                   "Labeled MODEL and MODEL VERSION rows in the official pricing table."),
        SourceSpec("deepseek.changes", "deepseek", "docs", "https://api-docs.deepseek.com/updates",
                   ("api-docs.deepseek.com",), "deepseek_docs", "public", "deepseek-public-v1", "1",
                   "Normalized official updates-page sections; no dated model retirement schema was verified."),
        SourceSpec("mistral.catalog", "mistral", "catalog", "https://docs.mistral.ai/models",
                   ("docs.mistral.ai",), "mistral_catalog", "public", "mistral-public-v1", "1",
                   "API column in official model table; paired deprecation/retirement dates are parsed only when both are explicit."),
        SourceSpec("mistral.lifecycle", "mistral", "docs", "https://docs.mistral.ai/inference/model-lifecycle",
                   ("docs.mistral.ai",), "mistral_lifecycle", "public", "mistral-public-v1", "1",
                   "Lifecycle policy stages and notice-period sections; generic policy text is not a model-specific deadline."),
        SourceSpec("xai.catalog", "xai", "catalog", "https://docs.x.ai/developers/models",
                   ("docs.x.ai",), "xai_catalog", "public", "xai-public-v1", "1",
                   "Current server-rendered model-detail links; a structured Markdown pricing-table layout is also supported. No catalog-linked lifecycle/changelog page was found in the source review."),
    )


def parse_source(source: SourceSpec, response: FetchResponse, now) -> ParsedSnapshot:
    dispatch = {
        "openai_catalog": openai.parse_catalog,
        "openai_lifecycle": openai.parse_lifecycle,
        "openai_docs": openai.parse_docs,
        "anthropic_catalog": anthropic.parse_catalog,
        "anthropic_lifecycle": anthropic.parse_lifecycle,
        "gemini_catalog": gemini.parse_catalog,
        "gemini_lifecycle": gemini.parse_lifecycle,
        "gemini_docs": gemini.parse_docs,
        "deepseek_catalog": deepseek.parse_catalog,
        "deepseek_docs": deepseek.parse_docs,
        "mistral_catalog": mistral.parse_catalog,
        "mistral_lifecycle": mistral.parse_lifecycle,
        "xai_catalog": xai.parse_catalog,
        "json_catalog": _parse_json_catalog,
        "json_fingerprint": _parse_json_fingerprint,
    }
    parser = dispatch.get(source.parser)
    if parser is None:
        raise AdapterError("unknown_parser", "no parser is registered for this source")
    return parser(source, response, now)


def _parse_json_catalog(source: SourceSpec, response: FetchResponse, now) -> ParsedSnapshot:
    try:
        value = json.loads(decode_response(response))
    except json.JSONDecodeError as exc:
        raise AdapterError("invalid_json", "catalog response is not JSON") from exc
    models_value = value.get("models") if isinstance(value, dict) else None
    if not isinstance(models_value, list) or not models_value:
        raise AdapterError("missing_catalog_structure", "JSON catalog must contain a nonempty models array")
    rows = []
    for item in models_value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise AdapterError("invalid_catalog_shape", "each JSON model needs an exact string id")
        model_id = exact_id(item["id"])
        if not model_id:
            raise AdapterError("invalid_model_id", "JSON catalog contains an invalid model ID")
        display = item.get("display_name", item.get("name"))
        if display is not None and not isinstance(display, str):
            raise AdapterError("invalid_catalog_shape", "model display name must be a string")
        rows.append((model_id, display, json.dumps(item, ensure_ascii=False, sort_keys=True)))
    models = make_models(source, response, now, rows, "custom.json_models_id_field.v1")
    sections = make_sections(source, response, now, [("catalog", ", ".join(m.model_id for m in models))], "custom.json_catalog_fingerprint.v1")
    return snapshot(models, [], sections)


def _parse_json_fingerprint(source: SourceSpec, response: FetchResponse, now) -> ParsedSnapshot:
    try:
        value = json.loads(decode_response(response))
    except json.JSONDecodeError as exc:
        raise AdapterError("invalid_json", "custom source response is not valid JSON") from exc
    sections_data: list[tuple[str, str]] = []
    if isinstance(value, dict) and isinstance(value.get("paths"), dict):
        paths = value["paths"]
        for path, item in sorted(paths.items(), key=lambda pair: str(pair[0])):
            canonical_item = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            methods = ", ".join(sorted(str(key).upper() for key in item if str(key).lower() in {
                "get", "put", "post", "delete", "options", "head", "patch", "trace",
            })) if isinstance(item, dict) else ""
            sections_data.append((f"path {path}", f"endpoint: {path}\nmethods: {methods or 'unspecified'}\n{canonical_item}"))
        components = value.get("components")
        schemas = components.get("schemas") if isinstance(components, dict) else None
        if isinstance(schemas, dict):
            for name, item in sorted(schemas.items(), key=lambda pair: str(pair[0])):
                canonical_item = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                sections_data.append((f"component schema {name}", f"schema: {name}\n{canonical_item}"))
        metadata = {key: item for key, item in value.items() if key not in {"paths", "components"}}
        if metadata:
            canonical = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            sections_data.append(("openapi metadata", canonical))
    else:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sections_data.append(("official-json", canonical))
    section = make_sections(source, response, now, sections_data, "custom.official_json_schema_fingerprint.v1")
    return snapshot([], [], section)


__all__ = ["AdapterError", "ParsedSnapshot", "default_sources", "parse_source"]
