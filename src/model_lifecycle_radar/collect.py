"""Isolated collection, compatibility checks, baselines, and observation events."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .adapters import AdapterError, parse_source
from .fetch import FetchError, FetchResponse
from .schema import (
    Event, LastGoodSnapshot, ModelRecord, RadarConfig, RadarState, SourceSpec,
    SourceState, utc_text,
)


@dataclass
class CollectionResult:
    state: RadarState
    events: list[Event]
    source_errors: list[dict[str, str]]

    @property
    def ok(self) -> bool:
        return not self.source_errors


def _event_id(event_type: str, source_id: str, subject: str, material: dict | None = None, scope: str = "public") -> str:
    payload = {"type": event_type, "source_id": source_id, "scope": scope, "subject": subject, "material": material or {}}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _remember(state: RadarState, event: Event, now: datetime, limit: int) -> bool:
    stamp = utc_text(now)
    previous = state.event_history.get(event.event_id)
    if previous:
        previous["last_seen_at"] = stamp
        return False
    state.event_history[event.event_id] = {
        "first_seen_at": stamp, "last_seen_at": stamp, "event": event.to_dict(),
    }
    if len(state.event_history) > limit:
        ordered = sorted(state.event_history, key=lambda key: state.event_history[key]["first_seen_at"])
        for event_id in ordered[:len(state.event_history) - limit]:
            del state.event_history[event_id]
    return True


def _emit(state: RadarState, events: list[Event], now: datetime, limit: int, event: Event) -> None:
    if _remember(state, event, now, limit):
        events.append(event)


def _compatible(previous: SourceState | None, spec: SourceSpec, canonical_url: str | None = None) -> bool:
    if previous is None or previous.last_good is None:
        return False
    matches = (
        previous.vendor == spec.vendor and previous.kind == spec.kind
        and previous.requested_url == spec.url and previous.scope == spec.scope
        and previous.scope_revision == spec.scope_revision
        and previous.parser_revision == spec.parser_revision
    )
    if canonical_url is not None:
        matches = matches and previous.canonical_url == canonical_url
    return matches


def _headers(previous: SourceState | None, spec: SourceSpec) -> dict[str, str]:
    if not _compatible(previous, spec) or previous.last_good is None:
        return {}
    result = {}
    if previous.last_good.etag:
        result["If-None-Match"] = previous.last_good.etag
    if previous.last_good.last_modified:
        result["If-Modified-Since"] = previous.last_good.last_modified
    return result


def _source_error(
    state: RadarState, spec: SourceSpec, previous: SourceState | None,
    now: datetime, code: str, canonical_url: str | None = None,
) -> SourceState:
    return SourceState(
        vendor=spec.vendor, kind=spec.kind, requested_url=spec.url,
        canonical_url=canonical_url or (previous.canonical_url if previous else None),
        scope=spec.scope, scope_revision=spec.scope_revision,
        parser_revision=spec.parser_revision, last_attempt_at=now.astimezone(timezone.utc),
        health="unhealthy", error_code=code,
        last_good=copy.deepcopy(previous.last_good) if previous else None,
        pending_disappearances=copy.deepcopy(previous.pending_disappearances) if previous else {},
    )


def _failure_event(spec: SourceSpec, code: str) -> Event:
    event_id = _event_id("source.unhealthy", spec.source_id, spec.source_id, {"error_code": code}, spec.scope)
    return Event(event_id, "source.unhealthy", spec.source_id, spec.source_id, "warning",
                 f"Official source collection failed ({code}); last-good data is retained.", scope=spec.scope)


def _model_event(spec: SourceSpec, model: ModelRecord) -> Event:
    material = {"evidence_hash": model.evidence.content_hash}
    event_id = _event_id("model.observed", spec.source_id, model.model_id, material, spec.scope)
    return Event(event_id, "model.observed", spec.source_id, model.model_id, "info",
                 f"{model.model_id} is newly observed in the public catalog; this does not establish its release date.",
                 evidence=model.evidence, scope=spec.scope)


def _disappearance_event(spec: SourceSpec, model_id: str, evidence_hash: str) -> Event:
    event_id = _event_id("model.disappearance_review", spec.source_id, model_id, {"evidence_hash": evidence_hash}, spec.scope)
    return Event(event_id, "model.disappearance_review", spec.source_id, model_id, "warning",
                 f"{model_id} was absent from two successful comparable catalog snapshots; review the source and scope. This is not confirmed retirement.", scope=spec.scope)


def _rebaseline_event(spec: SourceSpec, previous: SourceState, canonical_url: str) -> Event:
    old = {
        "requested_url": previous.requested_url, "canonical_url": previous.canonical_url,
        "scope": previous.scope, "scope_revision": previous.scope_revision,
        "parser_revision": previous.parser_revision,
    }
    new = {
        "requested_url": spec.url, "canonical_url": canonical_url,
        "scope": spec.scope, "scope_revision": spec.scope_revision,
        "parser_revision": spec.parser_revision,
    }
    event_id = _event_id("source.rebaseline_required", spec.source_id, spec.source_id, {"old": old, "new": new}, spec.scope)
    return Event(event_id, "source.rebaseline_required", spec.source_id, spec.source_id, "warning",
                 "Source URL, scope, or parser compatibility changed; the new snapshot establishes a fresh baseline.", scope=spec.scope)


def _section_event(spec: SourceSpec, section_id: str, section_hash: str, excerpt: str, evidence, change: str = "changed") -> Event:
    event_type = "schema.changed_review" if spec.kind == "custom_schema" else "docs.changed_review"
    event_id = _event_id(event_type, spec.source_id, section_id, {"section_hash": section_hash}, spec.scope)
    summary = f"Official {('schema' if spec.kind == 'custom_schema' else 'documentation')} section '{section_id}' was {change}; review affected endpoints and parameters."
    return Event(event_id, event_type, spec.source_id, section_id, "warning" if spec.kind == "custom_schema" else "info",
                 summary, evidence=evidence, scope=spec.scope)


def _notice_key(notice) -> tuple:
    return (notice.model_id, notice.status, notice.effective_date, notice.replacement, notice.deadline_authoritative)


def _lifecycle_event(spec: SourceSpec, notice) -> Event:
    material = {
        "status": notice.status,
        "deadline": notice.effective_date.isoformat() if notice.effective_date else None,
        "evidence_hash": notice.evidence.content_hash,
    }
    event_id = _event_id("lifecycle.announced", spec.source_id, notice.model_id, material, spec.scope)
    summary = f"Official documentation lists {notice.model_id} as {notice.status.replace('_', ' ')}."
    if notice.effective_date:
        summary += f" Listed date: {notice.effective_date.isoformat()}."
    return Event(event_id, "lifecycle.announced", spec.source_id, notice.model_id, "warning",
                 summary, evidence=notice.evidence, scope=spec.scope)


def collect(config: RadarConfig, previous_state: RadarState, *, fetcher, now: datetime) -> CollectionResult:
    """Collect every configured source independently and retain last-good snapshots."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    state = copy.deepcopy(previous_state)
    state.generation += 1
    state.last_run_at = now.astimezone(timezone.utc)
    emitted: list[Event] = []
    errors: list[dict[str, str]] = []

    for spec in config.sources:
        previous = state.sources.get(spec.source_id)
        compatible_without_canonical = _compatible(previous, spec)
        try:
            response: FetchResponse = fetcher.fetch(
                spec.url, spec.allowed_hosts, headers=_headers(previous, spec),
                max_bytes=config.max_response_bytes,
            )
            if response.not_modified:
                if not compatible_without_canonical or previous is None or previous.last_good is None:
                    raise FetchError("unexpected_not_modified", "source returned 304 without compatible last-good state")
                if response.url != previous.canonical_url:
                    raise FetchError("canonical_url_changed", "canonical source URL changed on a cached response")
                state.sources[spec.source_id] = SourceState(
                    spec.vendor, spec.kind, spec.url, response.url, spec.scope,
                    spec.scope_revision, spec.parser_revision, now.astimezone(timezone.utc),
                    "not_modified", None, copy.deepcopy(previous.last_good),
                    copy.deepcopy(previous.pending_disappearances),
                )
                continue
            parsed = parse_source(spec, response, now)
            if spec.kind == "catalog" and not parsed.models:
                raise AdapterError("empty_catalog", "catalog parser returned no model IDs")
            comparable = _compatible(previous, spec, response.url)
            rebaseline = previous is not None and previous.last_good is not None and not comparable
            if comparable and previous and previous.last_good and spec.kind == "catalog":
                old_count = len(previous.last_good.models)
                new_count = len(parsed.models)
                if old_count >= 8 and new_count <= old_count * (1 - config.suspicious_drop_fraction):
                    raise AdapterError("suspicious_count_drop", "catalog count fell sharply; last-good snapshot retained")

            if rebaseline and previous:
                event = _rebaseline_event(spec, previous, response.url)
                _emit(state, emitted, now, config.event_history_limit, event)

            pending: dict[str, int] = {}
            if comparable and previous and previous.last_good:
                old_models = {model.model_id: model for model in previous.last_good.models}
                new_models = {model.model_id: model for model in parsed.models}
                for model_id, current in new_models.items():
                    old = old_models.get(model_id)
                    if old:
                        current.first_seen_at = old.first_seen_at
                    else:
                        _emit(state, emitted, now, config.event_history_limit, _model_event(spec, current))
                possible_missing = old_models.keys() | previous.pending_disappearances.keys()
                for model_id in sorted(possible_missing - new_models.keys()):
                    count = min(previous.pending_disappearances.get(model_id, 0) + 1, 2)
                    pending[model_id] = count
                    if count >= 2:
                        prior = old_models.get(model_id)
                        evidence_hash = prior.evidence.content_hash if prior else "prior-successful-absence"
                        _emit(state, emitted, now, config.event_history_limit,
                              _disappearance_event(spec, model_id, evidence_hash))
                old_notices = {_notice_key(n) for n in previous.last_good.notices}
                for notice in parsed.notices:
                    if _notice_key(notice) not in old_notices:
                        _emit(state, emitted, now, config.event_history_limit, _lifecycle_event(spec, notice))
                old_sections = {section.section_id: section for section in previous.last_good.sections}
                new_sections = {section.section_id: section for section in parsed.sections}
                for section in parsed.sections:
                    old_section = old_sections.get(section.section_id)
                    if old_section is None:
                        _emit(state, emitted, now, config.event_history_limit,
                              _section_event(spec, section.section_id, "added:" + section.content_hash,
                                             section.excerpt, section.evidence, "added"))
                    elif old_section.content_hash != section.content_hash:
                        _emit(state, emitted, now, config.event_history_limit,
                              _section_event(spec, section.section_id, section.content_hash, section.excerpt, section.evidence))
                for section_id, old_section in old_sections.items():
                    if section_id not in new_sections:
                        _emit(state, emitted, now, config.event_history_limit,
                              _section_event(spec, section_id, "removed:" + old_section.content_hash,
                                             old_section.excerpt, old_section.evidence, "removed"))

            models = parsed.models
            for model in models:
                model.last_seen_at = now.astimezone(timezone.utc)
            last_good = LastGoodSnapshot(
                fetched_at=now.astimezone(timezone.utc), normalized_hash=parsed.normalized_hash,
                models=models, notices=parsed.notices, sections=parsed.sections,
                etag=response.header("ETag"), last_modified=response.header("Last-Modified"),
                canonical_url=response.url,
            )
            state.sources[spec.source_id] = SourceState(
                spec.vendor, spec.kind, spec.url, response.url, spec.scope,
                spec.scope_revision, spec.parser_revision, now.astimezone(timezone.utc),
                "healthy", None, last_good, pending,
            )
        except FetchError as exc:
            code = exc.code
            state.sources[spec.source_id] = _source_error(state, spec, previous, now, code)
            errors.append({"source_id": spec.source_id, "error_code": code})
            _emit(state, emitted, now, config.event_history_limit, _failure_event(spec, code))
        except AdapterError as exc:
            state.sources[spec.source_id] = _source_error(state, spec, previous, now, exc.code)
            errors.append({"source_id": spec.source_id, "error_code": exc.code})
            _emit(state, emitted, now, config.event_history_limit, _failure_event(spec, exc.code))
        except Exception:
            # A malformed provider response is isolated from every other source.
            state.sources[spec.source_id] = _source_error(state, spec, previous, now, "source_error")
            errors.append({"source_id": spec.source_id, "error_code": "source_error"})
            _emit(state, emitted, now, config.event_history_limit, _failure_event(spec, "source_error"))

    emitted.sort(key=lambda event: (event.event_type, event.source_id, event.subject, event.band or ""))
    return CollectionResult(state, emitted, errors)
