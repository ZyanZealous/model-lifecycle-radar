from __future__ import annotations

import re

from .common import (
    AdapterError, Node, code_ids, exact_id, html_sections, html_tables, make_models,
    make_notices, make_sections, node_text, parse_explicit_date, parse_html, snapshot,
)


def _cell_dates(cell: Node):
    values = [node_text(node) for node in cell.descendants() if node.tag == "span"]
    parsed = []
    for value in values:
        day, qualifier = parse_explicit_date(value)
        if day and not qualifier:
            parsed.append(day)
    return parsed


def _exact_cell_id(cell: Node) -> str | None:
    ids = code_ids(node_text(cell))
    if ids:
        return ids[0]
    return exact_id(node_text(cell))


def parse_catalog(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    models = []
    notices = []
    recognized = False
    for headers, rows in html_tables(root):
        model_index = next((i for i, h in enumerate(headers) if h.strip() == "model"), None)
        api_index = next((i for i, h in enumerate(headers) if h.strip() == "api"), None)
        if model_index is None or api_index is None:
            continue
        recognized = True
        dates_index = next((i for i, h in enumerate(headers) if "deprecation" in h and "retirement" in h), None)
        alternative_index = next((i for i, h in enumerate(headers) if "alternative" in h or "replacement" in h), None)
        for row in rows:
            if len(row) <= max(model_index, api_index):
                continue
            model_id = _exact_cell_id(row[api_index])
            if not model_id:
                continue
            display = node_text(row[model_index]) or None
            models.append((model_id, display, f"{display or ''} | API ID: {model_id}"))
            if dates_index is None or len(row) <= dates_index:
                continue
            dates = _cell_dates(row[dates_index])
            if len(dates) != 2:
                continue
            replacement = None
            if alternative_index is not None and len(row) > alternative_index:
                # The visible column is usually a marketing name; only exact API IDs qualify.
                replacement = _exact_cell_id(row[alternative_index])
            notices.append({
                "model_id": model_id, "status": "retirement_announced",
                "effective_date": dates[1], "replacement": replacement,
                "deadline_authoritative": True,
                "excerpt": " | ".join(node_text(cell) for cell in row),
            })
    if not recognized:
        raise AdapterError("missing_catalog_structure", "Model/API catalog table was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "mistral.model_catalog_sections.v1")
    parsed_models = make_models(source, response, now, models, "mistral.api_column.v1", dedupe=True)
    parsed_notices = make_notices(source, response, now, notices, "mistral.deprecation_retirement_columns.v1")
    return snapshot(parsed_models, parsed_notices, sections)


def parse_lifecycle(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    headings = [node_text(node).lower() for node in root.descendants() if node.tag in {"h1", "h2", "h3"}]
    if not any("model lifecycle policy" in heading for heading in headings) or not any("deprecation policy" in heading for heading in headings):
        raise AdapterError("missing_lifecycle_structure", "model lifecycle and deprecation policy headings were not both recognized")
    sections = make_sections(source, response, now, html_sections(root), "mistral.lifecycle_policy_sections.v1")
    return snapshot([], [], sections)
