from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from .common import (
    AdapterError, Node, code_ids, exact_id, html_sections, html_tables, make_models,
    make_notices, make_sections, markdown_sections, markdown_tables, node_text,
    normalize_text, parse_explicit_date, parse_html, snapshot,
)


def _markdown_link(value: str) -> tuple[str, str] | None:
    match = re.search(r"\[[^\]]+\]\(([^)]+)\)", value)
    if not match:
        return None
    title = re.search(r"\[([^\]]+)\]", value)
    return (title.group(1).strip(), match.group(1).strip()) if title else None


def _markdown_ids(value: str) -> list[str]:
    ids = code_ids(value)
    if ids:
        return ids
    candidate = exact_id(normalize_text(value).strip("` *"))
    return [candidate] if candidate else []


def _detail_url(base_url: str, link: str) -> str:
    candidate = urljoin(base_url, link)
    parsed, base = urlsplit(candidate), urlsplit(base_url)
    if (parsed.scheme != "https" or parsed.hostname != "docs.mistral.ai"
            or parsed.query or parsed.fragment or base.hostname != parsed.hostname
            or not re.fullmatch(r"/models/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:\.md)?", parsed.path)):
        raise AdapterError("invalid_model_detail_link", "active model card link is outside the official model detail path")
    if not parsed.path.endswith(".md"):
        candidate = candidate + ".md"
    return candidate


def _md_catalog(source, response, now):
    body = response.body.decode("utf-8-sig", errors="replace")
    headings = re.findall(r"(?m)^#{1,2}\s+(.+?)\s*#*\s*$", body)
    if not headings or normalize_text(headings[0]).lower() != "models overview":
        raise AdapterError("missing_catalog_structure", "Mistral Models Overview heading was not recognized")

    active_urls: list[str] = []
    active_seen: set[str] = set()
    active_titles: dict[str, str] = {}
    fallback_rows: dict[str, list[tuple[str, str | None, str]]] = {}
    historical_notices: list[dict] = []
    active_table_seen = False
    retirement_table_seen = False
    for headers, rows in markdown_tables(body):
        model_i = next((i for i, header in enumerate(headers) if header == "model"), None)
        api_i = next((i for i, header in enumerate(headers) if header in {"api name", "api names"}), None)
        status_i = next((i for i, header in enumerate(headers) if header == "status"), None)
        retirement_i = next((i for i, header in enumerate(headers) if "retirement" in header), None)
        if model_i is not None and api_i is not None and status_i is not None and retirement_i is None:
            active_table_seen = True
            for row in rows:
                if len(row) <= max(model_i, api_i, status_i):
                    continue
                status = normalize_text(re.sub(r"[`*_]", "", row[status_i])).lower()
                if status in {"deprecated", "retired"}:
                    continue
                if status not in {"ga", "public preview"}:
                    raise AdapterError("unknown_model_status", "Mistral active catalog contains an unrecognized status")
                linked_model = _markdown_link(row[model_i])
                if not linked_model:
                    raise AdapterError("missing_model_detail_link", "an active Mistral model row has no official detail link")
                display_name, link = linked_model
                detail_url = _detail_url(response.url, link)
                if detail_url not in active_seen:
                    active_seen.add(detail_url)
                    active_urls.append(detail_url)
                    active_titles[detail_url] = normalize_text(display_name)
                api_ids = _markdown_ids(row[api_i])
                if api_ids:
                    fallback_rows.setdefault(detail_url, []).extend([
                        (model_id, normalize_text(display_name), f"{display_name} | API name: {model_id}")
                        for model_id in api_ids
                    ])

        # Historical retirement rows are evidence only. They are deliberately
        # never promoted into the active catalog, even when they carry an API ID.
        if model_i is not None and api_i is not None and retirement_i is not None:
            retirement_table_seen = True
            deprecation_i = next((i for i, header in enumerate(headers) if "deprecation" in header), None)
            replacement_i = next((i for i, header in enumerate(headers) if "replacement" in header or "alternative" in header), None)
            for row in rows:
                if len(row) <= max(model_i, api_i, retirement_i):
                    continue
                model_ids = _markdown_ids(row[api_i])
                if not model_ids:
                    continue
                retirement_date, qualifier = parse_explicit_date(normalize_text(re.sub(r"[`*_]", "", row[retirement_i])))
                if retirement_date is None or qualifier:
                    continue
                replacement = None
                if replacement_i is not None and len(row) > replacement_i:
                    replacement_ids = code_ids(row[replacement_i])
                    replacement = replacement_ids[0] if replacement_ids else None
                deprecated_date = None
                if deprecation_i is not None and len(row) > deprecation_i:
                    deprecated_date, _ = parse_explicit_date(normalize_text(re.sub(r"[`*_]", "", row[deprecation_i])))
                historical_notices.append({
                    "model_id": model_ids[0], "status": "retirement_announced",
                    "effective_date": retirement_date, "deprecated_date": deprecated_date,
                    "replacement": replacement, "deadline_authoritative": True,
                    "excerpt": " | ".join(row),
                })

    if not active_table_seen or not active_urls:
        raise AdapterError("missing_catalog_structure", "active Model/API name/Status rows with detail links were not recognized")
    if not retirement_table_seen:
        raise AdapterError("missing_retirement_structure", "Mistral historical retirement table was not recognized")
    sections = make_sections(source, response, now, markdown_sections(body), "mistral.model_catalog_sections.v2")
    notices = make_notices(source, response, now, historical_notices, "mistral.historical_retirement_rows.v2")
    parsed = snapshot([], notices, sections)
    parsed.detail_urls = active_urls
    parsed.detail_titles = active_titles
    parsed.detail_fallbacks = {
        url: make_models(source, response, now, rows, "mistral.api_name_table.v2", dedupe=True)
        for url, rows in fallback_rows.items()
    }
    return parsed


def _legacy_retirement_catalog(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    notices = []
    recognized = False
    for headers, rows in html_tables(root):
        model_index = next((i for i, h in enumerate(headers) if h.strip() == "model"), None)
        api_index = next((i for i, h in enumerate(headers) if h.strip() == "api"), None)
        dates_index = next((i for i, h in enumerate(headers) if "deprecation" in h and "retirement" in h), None)
        if model_index is None or api_index is None or dates_index is None:
            continue
        recognized = True
        alternative_index = next((i for i, h in enumerate(headers) if "alternative" in h or "replacement" in h), None)
        for row in rows:
            if len(row) <= max(model_index, api_index, dates_index):
                continue
            model_ids = code_ids(node_text(row[api_index]))
            if not model_ids:
                candidate = exact_id(node_text(row[api_index]))
                model_ids = [candidate] if candidate else []
            dates = [
                day for node in row[dates_index].descendants() if node.tag == "span"
                if (day := parse_explicit_date(node_text(node))[0]) is not None
            ]
            if not model_ids or len(dates) != 2:
                continue
            replacement = None
            if alternative_index is not None and len(row) > alternative_index:
                candidates = code_ids(node_text(row[alternative_index]))
                replacement = candidates[0] if candidates else None
            notices.append({
                "model_id": model_ids[0], "status": "retirement_announced",
                "effective_date": dates[1], "deprecated_date": dates[0],
                "replacement": replacement, "deadline_authoritative": True,
                "excerpt": " | ".join(node_text(cell) for cell in row),
            })
    if not recognized:
        raise AdapterError("missing_catalog_structure", "Mistral retirement table was not recognized")
    sections = make_sections(source, response, now, html_sections(root), "mistral.historical_retirement_sections.v2")
    return snapshot([], make_notices(source, response, now, notices, "mistral.historical_retirement_rows.v2"), sections)


def parse_catalog(source, response, now):
    body = response.body.decode("utf-8-sig", errors="replace")
    if body.lstrip().startswith("#"):
        return _md_catalog(source, response, now)
    return _legacy_retirement_catalog(source, response, now)


def detail_title(response) -> str:
    body = response.body.decode("utf-8-sig", errors="replace")
    title_match = re.search(r"(?m)^#\s+(.+?)\s*#*\s*$", body)
    if not title_match:
        raise AdapterError("missing_model_card_title", "Mistral detail page title was not recognized")
    return normalize_text(title_match.group(1))


def parse_detail(source, response, now):
    body = response.body.decode("utf-8-sig", errors="replace")
    title = detail_title(response)
    api_line = next((line for line in body.splitlines() if re.match(r"\s*(?:[-*]\s*)?(?:\*\*)?API names?(?:\*\*)?\s*:", line, re.I)), None)
    if not api_line:
        raise AdapterError("missing_model_api_field", "Mistral detail page has no explicit API names field")
    values = re.split(r"(?i)API names?\s*:", api_line, maxsplit=1)[1]
    ids = code_ids(values)
    if not ids:
        raise AdapterError("missing_model_api_ids", "Mistral model card has no code-form exact API names")
    rows = [(model_id, title, f"{title} | API name: {model_id}") for model_id in ids]
    return make_models(source, response, now, rows, "mistral.model_card_api_names.v2", dedupe=True)


def parse_lifecycle(source, response, now):
    root = parse_html(response.body.decode("utf-8-sig", errors="replace"))
    headings = [node_text(node).lower() for node in root.descendants() if node.tag in {"h1", "h2", "h3"}]
    if not any("model lifecycle policy" in heading for heading in headings) or not any("deprecation policy" in heading for heading in headings):
        raise AdapterError("missing_lifecycle_structure", "model lifecycle and deprecation policy headings were not both recognized")
    sections = make_sections(source, response, now, html_sections(root), "mistral.lifecycle_policy_sections.v1")
    return snapshot([], [], sections)
