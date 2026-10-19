"""Load bounded collection settings, official-source extensions, and watchlists."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import default_sources
from .schema import RadarConfig, SourceSpec, StateError, watchlist_from_dict


def load_config(path: str | Path | None = None) -> RadarConfig:
    if path is None:
        return RadarConfig(default_sources())
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("source config is unreadable or invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise StateError("source config schema_version must be 1")
    extras = value.get("sources", [])
    if not isinstance(extras, list):
        raise StateError("source config sources must be an array")
    sources = list(default_sources())
    seen = {source.source_id for source in sources}
    for entry in extras:
        if not isinstance(entry, dict):
            raise StateError("each custom source must be an object")
        try:
            source_id = str(entry["source_id"])
            if source_id in seen:
                raise StateError("custom source ID conflicts with a built-in source")
            if entry.get("kind") != "custom_schema" or entry.get("format") not in {"json", "openapi-json"}:
                raise StateError("custom sources must be JSON/OpenAPI JSON fingerprint sources")
            source = SourceSpec(
                source_id=source_id, vendor=str(entry["vendor"]), kind="custom_schema",
                url=str(entry["url"]), allowed_hosts=tuple(str(x) for x in entry["allowed_hosts"]),
                parser="json_fingerprint", scope=str(entry.get("scope", "custom_public")),
                scope_revision=str(entry.get("scope_revision", "1")),
                parser_revision=str(entry.get("parser_revision", "1")),
                coverage=str(entry.get("coverage", "Custom official JSON fingerprint; change requires review.")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, StateError):
                raise
            raise StateError("invalid custom source; require HTTPS URL, official_hosts, JSON format, and revision") from exc
        sources.append(source)
        seen.add(source.source_id)
    options = {
        "request_timeout": value.get("request_timeout", 15.0),
        "attempts": value.get("attempts", 3),
        "max_response_bytes": value.get("max_response_bytes", 2 * 1024 * 1024),
        "max_total_bytes": value.get("max_total_bytes", 16 * 1024 * 1024),
        "max_run_seconds": value.get("max_run_seconds", 150.0),
        "max_redirects": value.get("max_redirects", 5),
        "suspicious_drop_fraction": value.get("suspicious_drop_fraction", 0.5),
        "event_history_limit": value.get("event_history_limit", 1000),
    }
    try:
        return RadarConfig(tuple(sources), **options)
    except (TypeError, ValueError) as exc:
        raise StateError("source config contains an invalid bounded-network setting") from exc


def load_watchlist(path: str | Path) -> list:
    try:
        value: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("watchlist is unreadable or invalid JSON") from exc
    return watchlist_from_dict(value)
