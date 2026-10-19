from __future__ import annotations

from .common import (
    AdapterError, Node, exact_id, html_sections, html_tables, make_models,
    make_notices, make_sections, node_text, node_text_parts, parse_explicit_date, parse_html, snapshot,
)


def _cell_ids(cell: Node) -> list[str]:
    ids: list[str] = []
    for part in node_text_parts(cell):
        identifier = exact_id(part)
        if identifier and identifier not in ids:
            ids.append(identifier)
    return ids


def parse_catalog(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    rows = []
    recognized = False
    for headers, data in html_tables(root):
        model_index = next((i for i, h in enumerate(headers) if h.strip() == "model"), None)
        endpoint_index = next((i for i, h in enumerate(headers) if h.strip() == "endpoint"), None)
        if model_index is None or endpoint_index is None:
            continue
        recognized = True
        for row in data:
            if len(row) <= max(model_index, endpoint_index):
                continue
            for model_id in _cell_ids(row[endpoint_index]):
                rows.append((model_id, node_text(row[model_index]) or None, node_text(row[0])))
    if not recognized:
        raise AdapterError("missing_catalog_structure", "model/endpoint table was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "gemini.models_sections.v1")
    models = make_models(source, response, now, rows, "gemini.model_endpoint_table.v1", dedupe=True)
    return snapshot(models, [], sections)


def parse_lifecycle(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    notices = []
    recognized = False
    for headers, data in html_tables(root):
        model_index = next((i for i, h in enumerate(headers) if h.strip() == "model"), None)
        date_index = next((i for i, h in enumerate(headers) if "shutdown date" in h), None)
        if model_index is None or date_index is None:
            continue
        recognized = True
        replacement_index = next((i for i, h in enumerate(headers) if "replacement" in h), None)
        for row in data:
            if len(row) <= max(model_index, date_index):
                continue
            deadline, qualifier = parse_explicit_date(node_text(row[date_index]))
            model_ids = _cell_ids(row[model_index])
            if not model_ids or not deadline:
                continue
            replacement_ids = _cell_ids(row[replacement_index]) if replacement_index is not None and len(row) > replacement_index else []
            for model_id in model_ids:
                notices.append({
                    "model_id": model_id, "status": "retirement_announced",
                    "effective_date": deadline, "replacement": replacement_ids[0] if replacement_ids else None,
                    "deadline_authoritative": qualifier is None, "date_qualifier": qualifier,
                    "excerpt": " | ".join(node_text(cell) for cell in row),
                })
    if not recognized:
        raise AdapterError("missing_lifecycle_structure", "Model/Shutdown date table was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "gemini.deprecation_sections.v1")
    parsed_notices = make_notices(source, response, now, notices, "gemini.shutdown_date_table.v1")
    return snapshot([], parsed_notices, sections)


def parse_docs(source, response, now):
    body = response.body.decode("utf-8-sig", errors="replace")
    root = parse_html(body)
    headings = [node_text(node).lower() for node in root.descendants() if node.tag in {"h1", "h2", "h3"}]
    if not any("release notes" in heading or "changelog" in heading for heading in headings):
        raise AdapterError("missing_document_structure", "Gemini release-notes heading was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "gemini.changelog_sections.v1")
    return snapshot([], [], sections)
