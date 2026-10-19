from __future__ import annotations

import re

from .common import (
    AdapterError, code_ids, decode_response, exact_id, make_models, make_notices,
    make_sections, markdown_sections, markdown_tables, parse_explicit_date, snapshot,
)


def parse_catalog(source, response, now):
    body = decode_response(response)
    rows = []
    found_api_id = False
    for headers, table_rows in markdown_tables(body):
        if not headers or headers[0] != "feature":
            continue
        display_names = headers
        for row in table_rows:
            if not row or re.sub(r"[^a-z0-9]+", " ", row[0].lower()).strip() != "claude api id":
                continue
            found_api_id = True
            for index, cell in enumerate(row[1:], start=1):
                ids = code_ids(cell)
                if not ids and (item := exact_id(cell)):
                    ids = [item]
                display = display_names[index] if index < len(display_names) else None
                for model_id in ids:
                    rows.append((model_id, display, f"Claude API ID: {cell}"))
    if not found_api_id:
        raise AdapterError("missing_catalog_structure", "Claude API ID row was not found in the comparison table")
    sections = make_sections(source, response, now, markdown_sections(body), "anthropic.models_overview_sections.v1")
    models = make_models(source, response, now, rows, "anthropic.claude_api_id_table.v1")
    return snapshot(models, [], sections)


def parse_lifecycle(source, response, now):
    body = decode_response(response)
    notices = []
    recognized = False
    for headers, rows in markdown_tables(body):
        model_index = next((i for i, h in enumerate(headers) if h in {"api model name", "deprecated model", "model"}), None)
        date_index = next((i for i, h in enumerate(headers) if "retirement date" in h), None)
        if model_index is None or date_index is None:
            continue
        recognized = True
        status_index = next((i for i, h in enumerate(headers) if "state" in h or "status" in h), None)
        deprecated_index = next((i for i, h in enumerate(headers) if h == "deprecated" or "deprecation date" in h), None)
        replacement_index = next((i for i, h in enumerate(headers) if "replacement" in h), None)
        for row in rows:
            if len(row) <= max(model_index, date_index):
                continue
            model_id = exact_id(row[model_index])
            if not model_id:
                continue
            status_cell = row[status_index].lower() if status_index is not None and len(row) > status_index else ""
            raw_date = row[date_index]
            deadline, qualifier = parse_explicit_date(raw_date)
            deprecated_date = None
            if deprecated_index is not None and len(row) > deprecated_index:
                deprecated_date, _ = parse_explicit_date(row[deprecated_index])
            if not deadline and not qualifier and not re.search(r"(?i)deprecated|retired", status_cell):
                continue
            replacement = None
            if replacement_index is not None and len(row) > replacement_index:
                replacement_ids = code_ids(row[replacement_index])
                if not replacement_ids:
                    candidate = exact_id(row[replacement_index])
                    replacement_ids = [candidate] if candidate else []
                replacement = replacement_ids[0] if len(replacement_ids) == 1 else None
            tentative = "tentative" in headers[date_index]
            state = (
                "retired" if "retired" in status_cell
                else "deprecated" if "deprecated" in status_cell
                else "active" if "active" in status_cell
                else "retirement_announced"
            )
            notices.append({
                "model_id": model_id, "status": state,
                "effective_date": deadline if qualifier is None else None,
                "replacement": replacement,
                "deadline_authoritative": deadline is not None and (not tentative or state == "retired") and qualifier is None,
                "date_qualifier": qualifier or ("tentative" if tentative and deadline else None),
                "deprecated_date": deprecated_date,
                "excerpt": " | ".join(row),
            })
    if not recognized:
        raise AdapterError("missing_lifecycle_structure", "model retirement status table was not recognized")
    sections = make_sections(source, response, now, markdown_sections(body), "anthropic.model_deprecations_sections.v1")
    return snapshot([], make_notices(source, response, now, notices, "anthropic.lifecycle_status_table.v1"), sections)
