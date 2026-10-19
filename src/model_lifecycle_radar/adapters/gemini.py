from __future__ import annotations

from .common import (
    AdapterError, Node, code_ids, exact_id, html_sections, html_tables, make_models,
    make_notices, make_sections, node_text, parse_explicit_date, parse_html, snapshot,
)


def _cell_id(cell: Node) -> str | None:
    ids = code_ids(node_text(cell))
    if ids:
        return ids[0]
    direct = exact_id(node_text(cell))
    if direct:
        return direct
    for node in cell.descendants():
        if node.tag != "a":
            continue
        href = node.attrs.get("href", "")
        tail = href.rstrip("/").split("/")[-1]
        if "models" in href and (identifier := exact_id(tail)):
            return identifier
    return None


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
            model_id = _cell_id(row[endpoint_index])
            if model_id:
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
            model_id = _cell_id(row[model_index])
            if not model_id or not deadline:
                continue
            replacement = None
            if replacement_index is not None and len(row) > replacement_index:
                replacement = _cell_id(row[replacement_index])
            notices.append({
                "model_id": model_id, "status": "retirement_announced",
                "effective_date": deadline, "replacement": replacement,
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
