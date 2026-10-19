from __future__ import annotations

import re

from .common import (
    AdapterError, decode_response, exact_id, html_sections, make_models, make_sections,
    markdown_sections, markdown_tables, node_text, parse_html, snapshot,
)


def parse_catalog(source, response, now):
    body = decode_response(response)
    rows = []
    if re.search(r"(?i)<(?:html|main)\b", body):
        root = parse_html(body)
        main = next((node for node in root.descendants() if node.tag == "main"), None)
        if main is None:
            raise AdapterError("missing_catalog_structure", "model-card main content was not found")
        recognized = False
        for node in main.descendants():
            if node.tag != "a":
                continue
            href = node.attrs.get("href", "")
            match = re.fullmatch(r"/developers/models/([A-Za-z0-9][A-Za-z0-9._:-]{1,127})/?", href)
            if match:
                recognized = True
                rows.append((match.group(1), None, f"Official model detail link: {href} ({node_text(node)[:80]})"))
        sections = make_sections(source, response, now, html_sections(root), "xai.models_sections.v1")
        if not recognized:
            raise AdapterError("missing_catalog_structure", "official model-detail links were not recognized")
        models = make_models(source, response, now, rows, "xai.model_detail_link.v1", dedupe=True)
        return snapshot(models, [], sections)

    recognized = False
    for headers, values in markdown_tables(body):
        model_index = next((i for i, h in enumerate(headers) if h.strip() == "model"), None)
        if model_index is None or not any(h.strip() in {"context", "cost"} for h in headers):
            continue
        recognized = True
        for row in values:
            if len(row) <= model_index:
                continue
            model_id = exact_id(row[model_index], allow_context=True)
            if model_id:
                rows.append((model_id, None, f"Model pricing row: {row[model_index]}"))
    if not recognized:
        raise AdapterError("missing_catalog_structure", "model pricing tables were not recognized")
    sections = make_sections(source, response, now, markdown_sections(body), "xai.models_sections.v1")
    models = make_models(source, response, now, rows, "xai.pricing_model_column.v1", dedupe=True)
    return snapshot(models, [], sections)
