from __future__ import annotations

from .common import (
    AdapterError, decode_response, exact_id, html_sections, make_models, make_sections,
    node_text, parse_html, snapshot,
)


def parse_catalog(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    rows = []
    recognized_model = False
    recognized_version = False
    for table in (node for node in root.descendants() if node.tag == "table"):
        for row in (node for node in table.descendants() if node is not table and node.tag == "tr"):
            cells = [child for child in row.children if hasattr(child, "tag") and child.tag in {"th", "td"}]
            if not cells:
                continue
            label = node_text(cells[0]).upper().replace("\u200b", "").strip()
            if label not in {"MODEL", "MODEL VERSION"}:
                continue
            is_version = label == "MODEL VERSION"
            recognized_version |= is_version
            recognized_model |= not is_version
            for cell in cells[1:]:
                value = node_text(cell)
                model_id = exact_id(value)
                if model_id:
                    rows.append((model_id, None, f"{label}: {value}"))
    if not recognized_model or not recognized_version:
        raise AdapterError("missing_catalog_structure", "MODEL and MODEL VERSION labeled rows were not both recognized")
    sections = make_sections(source, response, now, html_sections(root), "deepseek.pricing_model_sections.v1")
    models = make_models(source, response, now, rows, "deepseek.model_and_version_rows.v1")
    return snapshot(models, [], sections)


def parse_docs(source, response, now):
    body = decode_response(response)
    root = parse_html(body)
    sections = make_sections(source, response, now, html_sections(root), "deepseek.updates_sections.v1")
    return snapshot([], [], sections)
