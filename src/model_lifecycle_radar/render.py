"""Compact JSON and Markdown report writers."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .schema import Report, utc_text


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def render_markdown(report: Report) -> str:
    rows = [
        "# Model Lifecycle Radar report",
        "",
        f"- Mode: `{report.mode}`",
        f"- Generated: `{utc_text(report.generated_at)}`",
        f"- State generation: `{report.state.generation}`",
        "",
        "## Sources",
        "",
        "| Source | Vendor | Kind | Health | Catalog IDs | Last attempt |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for source_id, source in sorted(report.state.sources.items()):
        count = len(source.last_good.models) if source.last_good else "—"
        rows.append(f"| `{source_id}` | {source.vendor} | {source.kind} | **{source.health}** | {count} | `{utc_text(source.last_attempt_at)}` |")
    if not report.state.sources:
        rows.append("| — | — | — | no baseline | — | — |")

    rows.extend(["", "## Current findings", ""])
    if report.current_findings:
        for finding in report.current_findings:
            owner = f" Owner: **{finding.owner}**; service: `{finding.service}`." if finding.owner else ""
            rows.append(f"### [{finding.severity.upper()}] {finding.finding_type}: {finding.vendor}/{finding.model_id or 'source'}")
            rows.append("")
            rows.append(f"{finding.summary}{owner}")
            if finding.effective_date:
                rows.append(f"Listed date: `{finding.effective_date.isoformat()}`.")
            if finding.source_url:
                rows.append(f"Evidence: <{finding.source_url}>")
            if finding.replacement:
                rows.append(f"Source-stated replacement: `{finding.replacement}`.")
            if finding.steps:
                rows.append("")
                rows.extend(f"- {step}" for step in finding.steps)
            if finding.evidence:
                rows.append("")
                rows.append(f"> Evidence excerpt: {finding.evidence.excerpt.replace(chr(10), ' ')[:260]}")
            rows.append("")
    else:
        rows.append("No current watchlist findings.")

    rows.extend(["", "## Newly emitted events", ""])
    if report.emitted_events:
        rows.append("| Type | Severity | Subject | Summary |")
        rows.append("| --- | --- | --- | --- |")
        for event in report.emitted_events:
            summary = event.summary.replace("|", "\\|").replace("\n", " ")
            rows.append(f"| `{event.event_type}` | {event.severity} | `{event.subject}` | {summary} |")
    else:
        rows.append("No new deduplicated events.")

    rows.extend(["", "## Collection errors", ""])
    if report.source_errors:
        rows.extend(f"- `{item['source_id']}`: `{item['error_code']}`" for item in report.source_errors)
    else:
        rows.append("None.")
    rows.append("")
    return "\n".join(rows)


def write_reports(out_dir: str | Path, report: Report) -> tuple[Path, Path, Path]:
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "report.json"
    markdown_path = destination / "report.md"
    events_path = destination / "events.json"
    _atomic_text(report_path, json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_text(markdown_path, render_markdown(report))
    event_payload = {
        "schema_version": 1,
        "generated_at": utc_text(report.generated_at),
        "events": [event.to_dict() for event in report.emitted_events],
    }
    _atomic_text(events_path, json.dumps(event_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report_path, markdown_path, events_path
