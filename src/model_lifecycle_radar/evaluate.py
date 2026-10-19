"""Offline watchlist evaluation and deterministic event deduplication."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .schema import Event, Finding, RadarState, Report, WatchlistEntry, utc_text


@dataclass(frozen=True)
class Policy:
    retirement_days: tuple[int, ...] = (30, 14, 7)
    event_history_limit: int = 1000

    def __post_init__(self) -> None:
        if not self.retirement_days or any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in self.retirement_days):
            raise ValueError("retirement thresholds must be nonnegative integers")
        if tuple(sorted(set(self.retirement_days), reverse=True)) != self.retirement_days:
            raise ValueError("retirement thresholds must be unique and descending")
        if not isinstance(self.event_history_limit, int) or isinstance(self.event_history_limit, bool) or not 1 <= self.event_history_limit <= 10000:
            raise ValueError("event_history_limit must be between 1 and 10000")


def _identifier(data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        oldest = sorted(state.event_history, key=lambda key: state.event_history[key]["first_seen_at"])
        for key in oldest[:len(state.event_history) - limit]:
            del state.event_history[key]
    return True


def _watchlist_items(watchlist: Iterable[WatchlistEntry] | dict) -> list[WatchlistEntry]:
    if isinstance(watchlist, dict):
        from .schema import watchlist_from_dict
        return watchlist_from_dict(watchlist)
    result = list(watchlist)
    if any(not isinstance(item, WatchlistEntry) for item in result):
        raise ValueError("watchlist must contain WatchlistEntry records")
    return result


def _steps(replacement: str | None) -> tuple[str, ...]:
    candidate = f"Review the explicitly listed candidate {replacement}; validate it against current vendor docs." if replacement else "Identify a supported candidate from current official vendor docs; no replacement is inferred."
    return (
        "Confirm the official lifecycle notice and its applicability to your configured scope.",
        "Inspect the affected API endpoints, parameters, and model-specific behavior.",
        candidate,
        "Run application regression tests against the chosen candidate.",
        "Schedule the migration and record a rollback owner.",
    )


def evaluate(
    state: RadarState,
    watchlist: Iterable[WatchlistEntry] | dict,
    policy: Policy | None = None,
    *,
    now: datetime,
) -> Report:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    policy = policy or Policy()
    current_state = copy.deepcopy(state)
    current_state.last_run_at = now.astimezone(timezone.utc)
    items = _watchlist_items(watchlist)
    findings: list[Finding] = []
    emitted: list[Event] = []
    errors: list[dict[str, str]] = []

    for source_id, source in sorted(current_state.sources.items()):
        if source.health == "unhealthy":
            errors.append({"source_id": source_id, "error_code": source.error_code or "source_unhealthy"})
            findings.append(Finding(
                _identifier({"type": "source.unhealthy", "source": source_id, "code": source.error_code}),
                "source.unhealthy", source.vendor, None, "warning",
                f"Official source could not be collected ({source.error_code or 'unknown error'}); last-good data was retained.",
                source_url=source.canonical_url or source.requested_url,
            ))

    for entry in sorted(items, key=lambda item: (item.vendor, item.model, item.scope, item.owner, item.service)):
        catalog_sources = [
            (sid, source) for sid, source in current_state.sources.items()
            if source.vendor == entry.vendor and source.kind == "catalog" and source.scope == entry.scope
        ]
        present = any(
            source.health != "unhealthy" and source.last_good is not None
            and any(model.model_id == entry.model for model in source.last_good.models)
            for _, source in catalog_sources
        )
        catalog_unhealthy = not catalog_sources or all(source.health == "unhealthy" or source.last_good is None for _, source in catalog_sources)
        if not present:
            identity = {"type": "model.not_verified", "vendor": entry.vendor, "model": entry.model, "scope": entry.scope}
            findings.append(Finding(
                _identifier(identity), "model.not_verified", entry.vendor, entry.model,
                "warning" if catalog_unhealthy else "info",
                f"{entry.model} is not verified in the configured scope '{entry.scope}'.",
                entry.owner, entry.service,
                source_url=(catalog_sources[0][1].canonical_url or catalog_sources[0][1].requested_url) if catalog_sources else None,
                steps=_steps(None),
            ))

        matching_notices = []
        for sid, source in current_state.sources.items():
            if source.vendor != entry.vendor or source.scope != entry.scope or source.last_good is None:
                continue
            for notice in source.last_good.notices:
                if notice.model_id == entry.model:
                    matching_notices.append((sid, source, notice))
        for source_id, source, notice in sorted(matching_notices, key=lambda x: (x[0], x[2].status, x[2].effective_date or datetime.min.date())):
            evidence_id = notice.evidence.content_hash
            if notice.status in {"deprecated", "retired", "retirement_announced"}:
                notice_finding_id = _identifier({
                    "type": "lifecycle.announced", "source": source_id, "scope": entry.scope,
                    "model": entry.model, "status": notice.status, "evidence": evidence_id,
                })
                summary = f"Official documentation lists {entry.model} as {notice.status.replace('_', ' ')}."
                if notice.effective_date:
                    qualifier = " (tentative)" if not notice.deadline_authoritative else ""
                    summary += f" Listed date: {notice.effective_date.isoformat()}{qualifier}."
                elif notice.date_qualifier == "not_sooner_than":
                    summary += " The source gives a not-sooner-than date; it is not treated as a deadline."
                findings.append(Finding(
                    notice_finding_id, "lifecycle.announced", entry.vendor, entry.model,
                    "warning", summary, entry.owner, entry.service,
                    effective_date=notice.effective_date,
                    source_url=notice.evidence.source_url, replacement=notice.replacement,
                    steps=_steps(notice.replacement), evidence=notice.evidence,
                ))
            if notice.effective_date is None or not notice.deadline_authoritative:
                continue
            days = (notice.effective_date - now.date()).days
            if days < 0:
                event_type, band, severity = "retirement.overdue", "overdue", "error"
            elif days <= policy.retirement_days[-1]:
                event_type, band, severity = "retirement.threshold", f"{policy.retirement_days[-1]}d", "error"
            else:
                active = min((threshold for threshold in policy.retirement_days if days <= threshold), default=None)
                if active is None:
                    continue
                event_type, band = "retirement.threshold", f"{active}d"
                severity = "error" if active == min(policy.retirement_days) else "warning"
            summary = (
                f"{entry.model} has an official retirement date of {notice.effective_date.isoformat()} "
                f"({('overdue' if days < 0 else f'{days} days remaining')})."
            )
            finding_type = "retirement.overdue" if days < 0 else "retirement.threshold"
            finding_id = _identifier({
                "type": finding_type, "source": source_id, "scope": entry.scope,
                "model": entry.model, "date": notice.effective_date.isoformat(), "band": band,
            })
            findings.append(Finding(
                finding_id, finding_type, entry.vendor, entry.model, severity, summary,
                entry.owner, entry.service, band, notice.effective_date, days,
                notice.evidence.source_url, notice.replacement, _steps(notice.replacement), notice.evidence,
            ))
            event_id = _identifier({
                "type": event_type, "source": source_id, "scope": entry.scope,
                "model": entry.model, "evidence": evidence_id,
                "deadline": notice.effective_date.isoformat(), "band": band,
            })
            event = Event(event_id, event_type, source_id, entry.model, severity, summary,
                          band, entry.owner, entry.service, notice.evidence, entry.scope)
            if _remember(current_state, event, now, policy.event_history_limit):
                emitted.append(event)

    severity_order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda item: (severity_order.get(item.severity, 3), item.vendor, item.model_id or "", item.finding_type, item.finding_id))
    emitted.sort(key=lambda item: (severity_order.get(item.severity, 3), item.event_type, item.source_id, item.subject, item.band or ""))
    return Report(now.astimezone(timezone.utc), current_state, findings, emitted, errors)
