from __future__ import annotations

import re

from .common import (
    AdapterError, decode_response, exact_id, html_sections, make_models, make_sections,
    node_text, normalize_text, parse_explicit_date, parse_html, snapshot,
)


def parse_catalog(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    rows = []
    recognized_model = False
    for table in (node for node in root.descendants() if node.tag == "table"):
        model_values: list[str] | None = None
        version_values: list[str] | None = None
        for row in (node for node in table.descendants() if node is not table and node.tag == "tr"):
            cells = [child for child in row.children if hasattr(child, "tag") and child.tag in {"th", "td"}]
            if not cells:
                continue
            label = node_text(cells[0]).upper().replace("\u200b", "").strip()
            if label not in {"MODEL", "MODEL VERSION"}:
                continue
            values = [node_text(cell) for cell in cells[1:]]
            if label == "MODEL":
                recognized_model = True
                model_values = values
            else:
                version_values = values
        if model_values is not None:
            for index, value in enumerate(model_values):
                model_id = exact_id(value)
                if not model_id:
                    continue
                version = version_values[index].strip() if version_values and index < len(version_values) else ""
                rows.append((model_id, version or None,
                             f"MODEL: {model_id}" + (f" | MODEL VERSION: {version}" if version else "")))
    if not recognized_model:
        raise AdapterError("missing_catalog_structure", "labeled MODEL row was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "deepseek.pricing_model_sections.v2")
    models = make_models(source, response, now, rows, "deepseek.model_id_field.v2", dedupe=True)
    return snapshot(models, [], sections)


def parse_docs(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    headings = list(root.descendants())
    has_title = any(
        node.tag == "h1" and re.fullmatch(r"change\s*log", normalize_text(node_text(node)), re.IGNORECASE)
        for node in headings
    )
    dated_entries = []
    for node in headings:
        if node.tag != "h2":
            continue
        marker = re.fullmatch(r"date\s*:\s*(.+)", normalize_text(node_text(node)), re.IGNORECASE)
        if marker and parse_explicit_date(marker.group(1))[0]:
            dated_entries.append(node)
    if not has_title or not dated_entries:
        raise AdapterError("missing_changelog_structure", "DeepSeek Change Log title and dated entries were not both recognized")
    sections = make_sections(source, response, now, html_sections(root), "deepseek.changelog_sections.v2")
    return snapshot([], [], sections)
