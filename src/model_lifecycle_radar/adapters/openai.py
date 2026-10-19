from __future__ import annotations

import re

from .common import (
    AdapterError, Node, code_ids, decode_response, exact_id, html_sections, html_tables,
    make_models, make_notices, make_sections, markdown_sections, markdown_tables,
    node_text, parse_explicit_date, parse_html, snapshot,
)


def _labeled_model_values(root: Node):
    for parent in root.descendants():
        children = [child for child in parent.children if isinstance(child, Node)]
        for index, child in enumerate(children[:-1]):
            if child.tag == "div" and node_text(child).strip() == "Model ID":
                value_node = children[index + 1]
                values = [exact_id(x) for x in code_ids(node_text(value_node))]
                if not values:
                    values = [exact_id(node_text(value_node))]
                for model_id in values:
                    if model_id:
                        yield model_id, None, f"Model ID: {node_text(value_node)}"


def parse_catalog(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    sections = make_sections(source, response, now, html_sections(root), "openai.model_catalog_sections.v1")
    models = make_models(source, response, now, _labeled_model_values(root), "openai.explicit_model_id_field.v1")
    return snapshot(models, [], sections)


def _tables(body: str):
    if re.search(r"(?i)<(?:html|table|main|article)\b", body):
        return [(headers, [[node_text(cell) for cell in row] for row in rows])
                for headers, rows in html_tables(parse_html(body))]
    return markdown_tables(body)


def parse_lifecycle(source, response, now):
    body = decode_response(response)
    tables = _tables(body)
    notices: list[dict] = []
    recognized = False
    for headers, rows in tables:
        date_index = next((i for i, h in enumerate(headers) if "shutdown date" in h or "retirement date" in h), None)
        model_index = next((i for i, h in enumerate(headers) if "model" in h or "system" in h), None)
        if date_index is None or model_index is None:
            continue
        recognized = True
        replacement_index = next((i for i, h in enumerate(headers) if "replacement" in h or "substitute" in h), None)
        for row in rows:
            if len(row) <= max(date_index, model_index):
                continue
            deadline, qualifier = parse_explicit_date(row[date_index])
            if not deadline:
                continue
            cell = row[model_index]
            ids = code_ids(cell)
            if not ids:
                ids = [value for value in [exact_id(part.strip()) for part in cell.split(",")] if value]
            replacement = None
            if replacement_index is not None and len(row) > replacement_index:
                exact = code_ids(row[replacement_index])
                if not exact:
                    one = exact_id(row[replacement_index])
                    exact = [one] if one else []
                replacement = exact[0] if len(exact) == 1 else None
            excerpt = " | ".join(row)
            for model_id in ids:
                notices.append({
                    "model_id": model_id, "status": "retirement_announced",
                    "effective_date": deadline, "replacement": replacement,
                    "deadline_authoritative": qualifier is None,
                    "date_qualifier": qualifier, "excerpt": excerpt,
                })
    if not recognized:
        raise AdapterError("missing_lifecycle_structure", "no explicit shutdown/retirement table was recognized")
    if re.search(r"(?i)deprecated|deprecation|retir|shutdown", body) is None:
        raise AdapterError("missing_lifecycle_wording", "expected explicit retirement wording was absent")
    sections_raw = html_sections(parse_html(body)) if re.search(r"(?i)<(?:html|table|main|article)\b", body) else markdown_sections(body)
    sections = make_sections(source, response, now, sections_raw, "openai.deprecation_sections.v1")
    return snapshot([], make_notices(source, response, now, notices, "openai.shutdown_date_table.v1"), sections)


def parse_docs(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    headings = [node_text(node).lower() for node in root.descendants() if node.tag in {"h1", "h2", "h3"}]
    if not any("changelog" in heading for heading in headings):
        raise AdapterError("missing_document_structure", "OpenAI changelog heading was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "openai.changelog_sections.v1")
    return snapshot([], [], sections)
