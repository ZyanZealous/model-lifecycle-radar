"""Versioned records and validation shared by collectors, CLI, and users."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


STATE_SCHEMA_VERSION = 1
WATCHLIST_SCHEMA_VERSION = 1


class StateError(ValueError):
    """State or configuration is malformed or uses an unsupported schema."""


def _require_string(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _validate_public_url(value: str, name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must be an HTTPS URL without credentials, query, or fragment")


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    if not isinstance(value, str):
        raise StateError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateError("timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise StateError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StateError(f"{field_name} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StateError(f"{field_name} must be an ISO date") from exc


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    vendor: str
    kind: str
    url: str
    allowed_hosts: tuple[str, ...]
    parser: str
    scope: str
    scope_revision: str
    parser_revision: str
    coverage: str = ""

    def __post_init__(self) -> None:
        _require_string(self.source_id, "source_id")
        _require_string(self.vendor, "vendor")
        _require_string(self.url, "source URL")
        _require_string(self.parser, "parser")
        _require_string(self.scope, "scope")
        _require_string(self.scope_revision, "scope_revision")
        _require_string(self.parser_revision, "parser_revision")
        _validate_public_url(self.url, "source URL")
        if "." not in self.source_id:
            raise ValueError("source_id must be a stable vendor-qualified identifier")
        if self.kind not in {"catalog", "lifecycle", "docs", "custom_schema"}:
            raise ValueError("unsupported source kind")
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source URLs must be HTTPS and cannot contain user info")
        if parsed.query or parsed.fragment:
            raise ValueError("source URLs cannot contain query strings or fragments")
        hosts = tuple(h.lower().rstrip(".") for h in self.allowed_hosts)
        if not hosts or parsed.hostname.lower() not in hosts:
            raise ValueError("requested host must be present in allowed_hosts")
        if any(not h or "/" in h or ":" in h for h in hosts):
            raise ValueError("allowed_hosts must contain hostnames only")
        object.__setattr__(self, "allowed_hosts", hosts)


@dataclass(frozen=True)
class RadarConfig:
    sources: tuple[SourceSpec, ...]
    request_timeout: float = 15.0
    attempts: int = 3
    max_response_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 16 * 1024 * 1024
    max_run_seconds: float = 150.0
    max_redirects: int = 5
    suspicious_drop_fraction: float = 0.5
    event_history_limit: int = 1000

    def __post_init__(self) -> None:
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source IDs must be unique")
        if isinstance(self.request_timeout, bool) or not isinstance(self.request_timeout, (int, float)) or not 0 < self.request_timeout <= 15:
            raise ValueError("request_timeout must be between 0 and 15 seconds")
        if not isinstance(self.attempts, int) or isinstance(self.attempts, bool) or not 1 <= self.attempts <= 3:
            raise ValueError("attempts must be between 1 and 3")
        if not isinstance(self.max_response_bytes, int) or isinstance(self.max_response_bytes, bool) or not 1 <= self.max_response_bytes <= 2 * 1024 * 1024:
            raise ValueError("max_response_bytes must be at most 2 MiB")
        if not isinstance(self.max_total_bytes, int) or isinstance(self.max_total_bytes, bool) or self.max_total_bytes < self.max_response_bytes:
            raise ValueError("max_total_bytes must cover at least one response")
        if self.max_total_bytes > 64 * 1024 * 1024:
            raise ValueError("max_total_bytes must be at most 64 MiB")
        if isinstance(self.max_run_seconds, bool) or not isinstance(self.max_run_seconds, (int, float)) or not 1 <= self.max_run_seconds <= 300:
            raise ValueError("max_run_seconds must be between 1 and 300 seconds")
        if not isinstance(self.max_redirects, int) or isinstance(self.max_redirects, bool) or not 1 <= self.max_redirects <= 5:
            raise ValueError("max_redirects must be between 1 and 5")
        if isinstance(self.suspicious_drop_fraction, bool) or not isinstance(self.suspicious_drop_fraction, (int, float)) or not 0 < self.suspicious_drop_fraction < 1:
            raise ValueError("suspicious_drop_fraction must be between 0 and 1")
        if not isinstance(self.event_history_limit, int) or isinstance(self.event_history_limit, bool) or not 1 <= self.event_history_limit <= 10000:
            raise ValueError("event_history_limit must be between 1 and 10000")


@dataclass(frozen=True)
class Evidence:
    source_url: str
    observed_at: datetime
    rule_id: str
    excerpt: str
    content_hash: str
    confidence: str
    published_at: str | None = None

    def __post_init__(self) -> None:
        _require_string(self.source_url, "evidence source_url")
        _validate_public_url(self.source_url, "evidence source_url")
        _require_string(self.rule_id, "evidence rule_id")
        _require_string(self.content_hash, "evidence content_hash")
        if len(self.content_hash) != 64 or any(char not in "0123456789abcdef" for char in self.content_hash.lower()):
            raise ValueError("evidence content_hash must be a SHA-256 hex digest")
        if self.confidence not in {"structured", "explicit_text", "review_needed"}:
            raise ValueError("unsupported evidence confidence")
        if self.observed_at.tzinfo is None:
            raise ValueError("evidence observed_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "observed_at": utc_text(self.observed_at),
            "published_at": self.published_at,
            "rule_id": self.rule_id,
            "excerpt": self.excerpt[:500],
            "content_hash": self.content_hash,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        try:
            if not isinstance(value, dict):
                raise ValueError
            return cls(
                source_url=_require_string(value["source_url"], "source_url"), observed_at=parse_datetime(value["observed_at"]),
                rule_id=_require_string(value["rule_id"], "rule_id"), excerpt=_require_string(value["excerpt"], "excerpt", allow_empty=True),
                content_hash=_require_string(value["content_hash"], "content_hash"), confidence=_require_string(value["confidence"], "confidence"),
                published_at=value.get("published_at"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid evidence record") from exc


@dataclass
class ModelRecord:
    model_id: str
    display_name: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    evidence: Evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "first_seen_at": utc_text(self.first_seen_at),
            "last_seen_at": utc_text(self.last_seen_at),
            "evidence": self.evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelRecord":
        try:
            model_id = str(value["model_id"])
            if not model_id.strip() or any(c.isspace() for c in model_id):
                raise ValueError
            display = value.get("display_name")
            if display is not None and not isinstance(display, str):
                raise ValueError
            return cls(model_id, display, parse_datetime(value["first_seen_at"]),
                       parse_datetime(value["last_seen_at"]), Evidence.from_dict(value["evidence"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid model record") from exc


@dataclass
class NoticeRecord:
    vendor: str
    model_id: str
    status: str
    effective_date: date | None
    replacement: str | None
    evidence: Evidence
    deadline_authoritative: bool = True
    date_qualifier: str | None = None
    deprecated_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor, "model_id": self.model_id, "status": self.status,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "deprecated_date": self.deprecated_date.isoformat() if self.deprecated_date else None,
            "replacement": self.replacement,
            "deadline_authoritative": self.deadline_authoritative,
            "date_qualifier": self.date_qualifier,
            "evidence": self.evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NoticeRecord":
        try:
            if not isinstance(value, dict):
                raise ValueError
            model_id = _require_string(value["model_id"], "model_id")
            if not model_id.strip() or any(c.isspace() for c in model_id):
                raise ValueError
            replacement = value.get("replacement")
            if replacement is not None and (not isinstance(replacement, str) or any(c.isspace() for c in replacement)):
                raise ValueError
            authoritative = value.get("deadline_authoritative", False)
            if not isinstance(authoritative, bool):
                raise ValueError
            return cls(
                vendor=_require_string(value["vendor"], "vendor"), model_id=model_id, status=_require_string(value["status"], "status"),
                effective_date=_date(value.get("effective_date"), "effective_date"),
                replacement=replacement, evidence=Evidence.from_dict(value["evidence"]),
                deadline_authoritative=authoritative,
                date_qualifier=value.get("date_qualifier"),
                deprecated_date=_date(value.get("deprecated_date"), "deprecated_date"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid lifecycle notice") from exc


@dataclass
class SectionRecord:
    section_id: str
    content_hash: str
    excerpt: str
    evidence: Evidence

    def to_dict(self) -> dict[str, Any]:
        return {"section_id": self.section_id, "content_hash": self.content_hash,
                "excerpt": self.excerpt[:500], "evidence": self.evidence.to_dict()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SectionRecord":
        try:
            return cls(str(value["section_id"]), str(value["content_hash"]),
                       str(value["excerpt"]), Evidence.from_dict(value["evidence"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid normalized section") from exc


@dataclass
class LastGoodSnapshot:
    fetched_at: datetime
    normalized_hash: str
    models: list[ModelRecord] = field(default_factory=list)
    notices: list[NoticeRecord] = field(default_factory=list)
    sections: list[SectionRecord] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    canonical_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": utc_text(self.fetched_at), "normalized_hash": self.normalized_hash,
            "etag": self.etag, "last_modified": self.last_modified, "canonical_url": self.canonical_url,
            "models": [m.to_dict() for m in self.models],
            "notices": [n.to_dict() for n in self.notices],
            "normalized_sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LastGoodSnapshot":
        try:
            models = [ModelRecord.from_dict(m) for m in value["models"]]
            ids = [m.model_id for m in models]
            if len(ids) != len(set(ids)):
                raise ValueError
            return cls(
                fetched_at=parse_datetime(value["fetched_at"]), normalized_hash=str(value["normalized_hash"]),
                models=models, notices=[NoticeRecord.from_dict(n) for n in value["notices"]],
                sections=[SectionRecord.from_dict(s) for s in value["normalized_sections"]],
                etag=value.get("etag"), last_modified=value.get("last_modified"),
                canonical_url=value.get("canonical_url"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid last-good snapshot") from exc


@dataclass
class SourceState:
    vendor: str
    kind: str
    requested_url: str
    canonical_url: str | None
    scope: str
    scope_revision: str
    parser_revision: str
    last_attempt_at: datetime
    health: str
    error_code: str | None = None
    last_good: LastGoodSnapshot | None = None
    pending_disappearances: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor, "kind": self.kind, "requested_url": self.requested_url,
            "canonical_url": self.canonical_url, "scope": self.scope,
            "scope_revision": self.scope_revision, "parser_revision": self.parser_revision,
            "last_attempt_at": utc_text(self.last_attempt_at), "health": self.health,
            "error_code": self.error_code,
            "last_good": self.last_good.to_dict() if self.last_good else None,
            "pending_disappearances": dict(self.pending_disappearances),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceState":
        try:
            if not isinstance(value, dict):
                raise ValueError
            health = str(value["health"])
            if health not in {"healthy", "unhealthy", "not_modified"}:
                raise ValueError
            pending = value.get("pending_disappearances", {})
            if not isinstance(pending, dict) or any(not isinstance(k, str) or not k or not isinstance(v, int) or isinstance(v, bool) or v < 1 for k, v in pending.items()):
                raise ValueError
            requested_url = _require_string(value["requested_url"], "requested_url")
            _validate_public_url(requested_url, "requested_url")
            canonical_url = value.get("canonical_url")
            if canonical_url is not None:
                canonical_url = _require_string(canonical_url, "canonical_url")
                _validate_public_url(canonical_url, "canonical_url")
            error_code = value.get("error_code")
            if error_code is not None:
                error_code = _require_string(error_code, "error_code")
            return cls(
                vendor=_require_string(value["vendor"], "vendor"), kind=_require_string(value["kind"], "kind"),
                requested_url=requested_url, canonical_url=canonical_url,
                scope=_require_string(value["scope"], "scope"), scope_revision=_require_string(value["scope_revision"], "scope_revision"),
                parser_revision=_require_string(value["parser_revision"], "parser_revision"),
                last_attempt_at=parse_datetime(value["last_attempt_at"]), health=health,
                error_code=error_code,
                last_good=LastGoodSnapshot.from_dict(value["last_good"]) if value.get("last_good") else None,
                pending_disappearances={str(k): v for k, v in pending.items()},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid source state") from exc


@dataclass
class RadarState:
    schema_version: int
    generation: int
    last_run_at: datetime
    sources: dict[str, SourceState] = field(default_factory=dict)
    event_history: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def empty(cls, now: datetime) -> "RadarState":
        return cls(STATE_SCHEMA_VERSION, 0, now.astimezone(timezone.utc), {}, {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "generation": self.generation,
            "last_run_at": utc_text(self.last_run_at),
            "sources": {k: v.to_dict() for k, v in sorted(self.sources.items())},
            "event_history": {k: v for k, v in sorted(self.event_history.items())},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RadarState":
        if not isinstance(value, dict):
            raise StateError("state root must be a JSON object")
        if value.get("schema_version") != STATE_SCHEMA_VERSION:
            raise StateError("unsupported state schema version")
        try:
            generation = value["generation"]
            if not isinstance(generation, int) or generation < 0:
                raise ValueError
            sources = value["sources"]
            history = value["event_history"]
            if not isinstance(sources, dict) or not isinstance(history, dict):
                raise ValueError
            parsed_sources = {str(k): SourceState.from_dict(v) for k, v in sources.items()}
            parsed_history: dict[str, dict[str, Any]] = {}
            for event_id, record in history.items():
                if not isinstance(record, dict) or not isinstance(record.get("event"), dict):
                    raise ValueError
                parsed_history[str(event_id)] = {
                    "first_seen_at": utc_text(parse_datetime(record["first_seen_at"])),
                    "last_seen_at": utc_text(parse_datetime(record["last_seen_at"])),
                    "event": record["event"],
                }
            return cls(STATE_SCHEMA_VERSION, generation, parse_datetime(value["last_run_at"]),
                       parsed_sources, parsed_history)
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, StateError):
                raise
            raise StateError("invalid state contents") from exc


@dataclass(frozen=True)
class WatchlistEntry:
    vendor: str
    model: str
    owner: str
    service: str
    scope: str = "public"

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() and v == v.strip() for v in (self.vendor, self.model, self.owner, self.service, self.scope)):
            raise ValueError("watchlist vendor, model, owner, service, and scope are required")
        if any(char.isspace() for char in self.model):
            raise ValueError("watchlist model must be an exact ID without whitespace")


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    source_id: str
    subject: str
    severity: str
    summary: str
    band: str | None = None
    owner: str | None = None
    service: str | None = None
    evidence: Evidence | None = None
    scope: str = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "event_type": self.event_type,
            "source_id": self.source_id, "subject": self.subject,
            "severity": self.severity, "band": self.band,
            "summary": self.summary, "owner": self.owner, "service": self.service, "scope": self.scope,
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


@dataclass(frozen=True)
class Finding:
    finding_id: str
    finding_type: str
    vendor: str
    model_id: str | None
    severity: str
    summary: str
    owner: str | None = None
    service: str | None = None
    band: str | None = None
    effective_date: date | None = None
    days_remaining: int | None = None
    source_url: str | None = None
    replacement: str | None = None
    steps: tuple[str, ...] = ()
    evidence: Evidence | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id, "finding_type": self.finding_type,
            "vendor": self.vendor, "model_id": self.model_id, "severity": self.severity,
            "summary": self.summary, "owner": self.owner, "service": self.service,
            "band": self.band,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "days_remaining": self.days_remaining, "source_url": self.source_url,
            "replacement": self.replacement, "steps": list(self.steps),
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


@dataclass
class Report:
    generated_at: datetime
    state: RadarState
    current_findings: list[Finding]
    emitted_events: list[Event]
    source_errors: list[dict[str, str]] = field(default_factory=list)
    mode: str = "check"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": utc_text(self.generated_at), "mode": self.mode,
            "state_generation": self.state.generation,
            "sources": {
                sid: {
                    "vendor": source.vendor, "kind": source.kind,
                    "requested_url": source.requested_url,
                    "health": source.health, "error_code": source.error_code,
                    "last_attempt_at": utc_text(source.last_attempt_at),
                    "canonical_url": source.canonical_url,
                    "scope": source.scope, "scope_revision": source.scope_revision,
                    "parser_revision": source.parser_revision,
                    "catalog_count": len(source.last_good.models) if source.last_good and source.kind == "catalog" else None,
                    "notice_count": len(source.last_good.notices) if source.last_good else None,
                    "section_count": len(source.last_good.sections) if source.last_good else None,
                }
                for sid, source in sorted(self.state.sources.items())
            },
            "current_findings": [f.to_dict() for f in self.current_findings],
            "new_events": [e.to_dict() for e in self.emitted_events],
            "source_errors": self.source_errors,
        }


def watchlist_from_dict(value: Any) -> list[WatchlistEntry]:
    if not isinstance(value, dict) or value.get("schema_version") != WATCHLIST_SCHEMA_VERSION:
        raise StateError("watchlist schema_version must be 1")
    entries = value.get("models")
    if not isinstance(entries, list):
        raise StateError("watchlist models must be an array")
    result: list[WatchlistEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise StateError("each watchlist model must be an object")
        try:
            for key in ("vendor", "model", "owner", "service"):
                if not isinstance(entry.get(key), str):
                    raise ValueError
            if "scope" in entry and not isinstance(entry["scope"], str):
                raise ValueError
            item = WatchlistEntry(
                vendor=entry["vendor"], model=entry["model"],
                owner=entry["owner"], service=entry["service"],
                scope=entry.get("scope", "public"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError("invalid watchlist model entry") from exc
        key = (item.vendor, item.model, item.scope)
        if key in seen:
            raise StateError("duplicate watchlist model entry")
        seen.add(key)
        result.append(item)
    return result


def state_path(path: str | Path) -> Path:
    return Path(path).expanduser()
