"""Command-line interface for public collection, offline checks, and smoke runs."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .adapters import AdapterError, default_sources, parse_source, resolve_followups
from .collect import CollectionResult, collect
from .config import load_config, load_watchlist
from .evaluate import Policy, evaluate
from .fetch import BoundedFetcher, FetchError
from .render import write_reports
from .schema import (
    Evidence, Event, Finding, LastGoodSnapshot, ModelRecord, NoticeRecord,
    RadarConfig, RadarState, Report, SourceState, StateError, WatchlistEntry, utc_text,
)
from .state import ConcurrentWriteError, StateLock, load_state, save_state


VENDORS = ("openai", "anthropic", "gemini", "deepseek", "mistral", "xai")


def _policy(value: str) -> Policy:
    try:
        thresholds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return Policy(thresholds)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("use descending integer days such as 30,14,7") from exc


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=".radar/state.json", help="versioned last-good state file")
    parser.add_argument("--watchlist", default="watchlist.json", help="schema-versioned JSON watchlist")
    parser.add_argument("--out", default="reports", help="directory for report.json, report.md, and events.json")
    parser.add_argument("--config", help="optional JSON source configuration")
    parser.add_argument("--retirement-days", type=_policy, default=Policy(), help="descending policy thresholds (default: 30,14,7)")
    parser.add_argument("--fail-on", choices=("off", "warning", "error"), default="error")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar", description="Review official public model catalog and lifecycle changes.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect", help="fetch official sources and update local state")
    _add_common(collect_parser)
    check_parser = subparsers.add_parser("check", help="reevaluate saved last-good evidence offline")
    _add_common(check_parser)
    smoke_parser = subparsers.add_parser("smoke", help="fetch and parse sources without credentials or state changes")
    smoke_parser.add_argument("--vendor", choices=("all", *VENDORS), default="all")
    smoke_parser.add_argument("--out", help="optional directory to save smoke.json")
    smoke_parser.add_argument("--config", help="optional JSON source configuration")
    demo_parser = subparsers.add_parser("demo", help="write a synthetic offline example report")
    demo_parser.add_argument("--out", default="reports/demo")
    return parser


def _fetcher(config: RadarConfig) -> BoundedFetcher:
    return BoundedFetcher(
        request_timeout=config.request_timeout, attempts=config.attempts,
        max_response_bytes=config.max_response_bytes, max_total_bytes=config.max_total_bytes,
        max_run_seconds=config.max_run_seconds, max_redirects=config.max_redirects,
    )


def _exit_code(report: Report, fail_on: str) -> int:
    if report.source_errors:
        return 2
    if fail_on == "off":
        return 0
    severities = {finding.severity for finding in report.current_findings}
    if "error" in severities or (fail_on == "warning" and "warning" in severities):
        return 1
    return 0


def _combine_events(*groups: list[Event]) -> list[Event]:
    by_id: dict[str, Event] = {}
    for group in groups:
        for event in group:
            by_id[event.event_id] = event
    return sorted(by_id.values(), key=lambda item: (item.event_type, item.source_id, item.subject, item.band or ""))


def _failure_report(out: str, mode: str, error_code: str, now: datetime, *, state: RadarState | None = None) -> int:
    report = Report(now, state or RadarState.empty(now), [], [], [{"source_id": mode, "error_code": error_code}], mode)
    try:
        write_reports(out, report)
    except OSError:
        pass
    print(f"{mode} failed ({error_code}); no replacement state was written.", file=sys.stderr)
    return 2


def _run_stateful(args, *, mode: str) -> int:
    now = datetime.now(timezone.utc)
    state_path = Path(args.state)
    try:
        config = load_config(args.config)
        watchlist = load_watchlist(args.watchlist)
    except StateError as exc:
        print(str(exc), file=sys.stderr)
        return _failure_report(args.out, mode, "invalid_config_or_watchlist", now)

    if mode == "check" and not state_path.exists():
        return _failure_report(args.out, mode, "state_missing", now)
    lock_path = Path(str(state_path) + ".lock")
    try:
        with StateLock(lock_path):
            try:
                previous = load_state(state_path, now=now)
            except StateError as exc:
                print(str(exc), file=sys.stderr)
                return _failure_report(args.out, mode, "invalid_state", now)

            if mode == "collect":
                result: CollectionResult = collect(config, previous, fetcher=_fetcher(config), now=now)
                policy = Policy(args.retirement_days.retirement_days, config.event_history_limit)
                report = evaluate(result.state, watchlist, policy, now=now)
                report.mode = "collect"
                report.emitted_events = _combine_events(result.events, report.emitted_events)
                report.source_errors = list(result.source_errors)
            else:
                policy = Policy(args.retirement_days.retirement_days, config.event_history_limit)
                report = evaluate(previous, watchlist, policy, now=now)
                report.mode = "check"
            try:
                save_state(state_path, report.state)
            except (OSError, ValueError) as exc:
                print(f"state write failed: {exc}", file=sys.stderr)
                report.source_errors.append({"source_id": "state", "error_code": "state_write_failed"})
                write_reports(args.out, report)
                return 2
            write_reports(args.out, report)
    except ConcurrentWriteError:
        return _failure_report(args.out, mode, "writer_lock_busy", now)
    except OSError:
        return _failure_report(args.out, mode, "filesystem_error", now)

    code = _exit_code(report, args.fail_on)
    healthy = sum(1 for source in report.state.sources.values() if source.health != "unhealthy")
    total = len(report.state.sources)
    print(f"{mode}: {healthy}/{total} sources healthy; {len(report.current_findings)} current findings; "
          f"{len(report.emitted_events)} new events; reports: {Path(args.out).resolve()}")
    return code


def _smoke(args) -> int:
    now = datetime.now(timezone.utc)
    try:
        config = load_config(args.config)
    except StateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    selected = tuple(source for source in config.sources if args.vendor == "all" or source.vendor == args.vendor)
    smoke_config = RadarConfig(
        sources=selected, request_timeout=config.request_timeout, attempts=config.attempts,
        max_response_bytes=config.max_response_bytes, max_total_bytes=config.max_total_bytes,
        max_run_seconds=config.max_run_seconds, max_redirects=config.max_redirects,
        suspicious_drop_fraction=config.suspicious_drop_fraction,
        event_history_limit=config.event_history_limit,
    )
    fetcher = _fetcher(smoke_config)
    source_results: dict[str, dict] = {}
    for source in selected:
        item = {
            "vendor": source.vendor, "kind": source.kind, "requested_url": source.url,
            "canonical_url": None, "health": "unhealthy", "error_code": None,
            "catalog_count": None, "notice_count": 0, "section_count": 0,
        }
        try:
            response = fetcher.fetch(source.url, source.allowed_hosts, max_bytes=smoke_config.max_response_bytes)
            if response.not_modified:
                raise FetchError("unexpected_not_modified", "smoke does not have a cache baseline")
            parsed = parse_source(source, response, now)
            parsed = resolve_followups(source, parsed, fetcher=fetcher, now=now,
                                       max_bytes=smoke_config.max_response_bytes)
            item.update({
                "canonical_url": response.url, "health": "healthy",
                "catalog_count": len(parsed.models) if source.kind == "catalog" else None,
                "notice_count": len(parsed.notices), "section_count": len(parsed.sections),
            })
        except FetchError as exc:
            item["error_code"] = exc.code
        except AdapterError as exc:
            item["error_code"] = exc.code
        except Exception:
            item["error_code"] = "source_error"
        source_results[source.source_id] = item

    providers = {}
    for vendor in VENDORS:
        catalog = next((item for sid, item in source_results.items() if item["vendor"] == vendor and item["kind"] == "catalog"), None)
        vendor_sources = [item for item in source_results.values() if item["vendor"] == vendor]
        if not vendor_sources:
            continue
        providers[vendor] = {
            "catalog_count": catalog["catalog_count"] if catalog else None,
            "catalog_url": (catalog["canonical_url"] or catalog["requested_url"]) if catalog else None,
            "catalog_health": catalog["health"] if catalog else "unavailable",
            "health": "healthy" if all(item["health"] == "healthy" for item in vendor_sources) else "partial_or_unhealthy",
            "sources": [
                {"source_id": sid, "health": item["health"], "url": item["canonical_url"] or item["requested_url"], "error_code": item["error_code"]}
                for sid, item in source_results.items() if item["vendor"] == vendor
            ],
        }
    output = {
        "schema_version": 1, "generated_at": utc_text(now), "mode": "smoke",
        "providers": providers, "sources": source_results,
    }
    rendered = json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    print(rendered, end="")
    if args.out:
        destination = Path(args.out)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "smoke.json").write_text(rendered, encoding="utf-8")
    failures = [sid for sid, item in source_results.items() if item["health"] != "healthy"]
    return 2 if failures else 0


def _demo(args) -> int:
    now = datetime.now(timezone.utc)
    deadline = now.date() + timedelta(days=7)
    source_url = "https://demo.example.test/models.json"
    evidence = Evidence(source_url, now, "demo.synthetic_lifecycle.v1", "demo-retiring-model shutdown date", "d" * 64, "structured")
    model_evidence = Evidence(source_url, now, "demo.synthetic_catalog.v1", "demo-retiring-model", "c" * 64, "structured")
    model = ModelRecord("demo-retiring-model", "Synthetic model", now, now, model_evidence)
    notice = NoticeRecord("demo", "demo-retiring-model", "retirement_announced", deadline, None, evidence, True)
    snapshot = LastGoodSnapshot(now, "e" * 64, [model], [notice], [], canonical_url=source_url)
    source = SourceState("demo", "catalog", source_url, source_url, "public", "demo-v1", "1", now,
                         "healthy", None, snapshot)
    state = RadarState(1, 1, now, {"demo.catalog": source}, {})
    watchlist = [WatchlistEntry("demo", "demo-retiring-model", "demo-owner", "demo-service")]
    report = evaluate(state, watchlist, Policy(), now=now)
    report.mode = "demo"
    write_reports(args.out, report)
    print(f"demo: synthetic offline report written to {Path(args.out).resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "collect":
        return _run_stateful(args, mode="collect")
    if args.command == "check":
        return _run_stateful(args, mode="check")
    if args.command == "smoke":
        return _smoke(args)
    if args.command == "demo":
        return _demo(args)
    parser.error("unknown command")
    return 2
