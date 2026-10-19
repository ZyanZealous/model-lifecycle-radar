"""Small structure-preserving helpers used by the six official adapters."""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Iterable

from ..fetch import FetchResponse
from ..schema import Evidence, ModelRecord, NoticeRecord, SectionRecord, SourceSpec


class AdapterError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ParsedSnapshot:
    models: list[ModelRecord]
    notices: list[NoticeRecord]
    sections: list[SectionRecord]
    normalized_hash: str
    detail_urls: list[str] = field(default_factory=list)
    detail_titles: dict[str, str] = field(default_factory=dict)
    detail_fallbacks: dict[str, list[ModelRecord]] = field(default_factory=dict)


class Node:
    def __init__(self, tag: str, attrs=()) -> None:
        self.tag = tag.lower()
        self.attrs = {str(k).lower(): str(v) for k, v in attrs}
        self.children: list[Node | str] = []

    def descendants(self) -> Iterable["Node"]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.descendants()


class _TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(body: str) -> Node:
    parser = _TreeParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:
        raise AdapterError("malformed_html", "HTML could not be parsed") from exc
    return parser.root


def node_text(node: Node, *, suppress: frozenset[str] = frozenset({"script", "style", "sup", "svg"})) -> str:
    pieces: list[str] = []
    def visit(item: Node | str) -> None:
        if isinstance(item, str):
            pieces.append(item)
        elif item.tag not in suppress:
            if item.tag == "tr":
                if pieces and pieces[-1] and not pieces[-1].endswith(" | "):
                    pieces.append(" | ")
                for child in item.children:
                    if isinstance(child, Node) and child.tag in {"th", "td"}:
                        before = len(pieces)
                        visit(child)
                        if len(pieces) > before:
                            pieces.append(" | ")
            else:
                for child in item.children:
                    visit(child)
    visit(node)
    return " ".join(" ".join("".join(pieces).split()).split())


def node_text_parts(
    node: Node,
    *,
    split_tags: frozenset[str] = frozenset({"br", "code"}),
    suppress: frozenset[str] = frozenset({"script", "style", "sup", "svg"}),
) -> list[str]:
    """Return cell text fragments without joining explicit HTML ID boundaries."""
    parts: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = normalize_text("".join(current))
        if text:
            parts.append(text)
        current.clear()

    def visit(item: Node | str) -> None:
        if isinstance(item, str):
            current.append(item)
        elif item.tag in suppress:
            return
        elif item.tag == "br":
            flush()
        elif item.tag in split_tags:
            flush()
            for child in item.children:
                visit(child)
            flush()
        else:
            for child in item.children:
                visit(child)

    visit(node)
    flush()
    return parts


def direct_cells(row: Node) -> list[Node]:
    return [child for child in row.children if isinstance(child, Node) and child.tag in {"th", "td"}]


def table_rows(table: Node) -> list[list[Node]]:
    return [direct_cells(row) for row in table.descendants() if row is not table and row.tag == "tr" and direct_cells(row)]


def html_tables(root: Node) -> list[tuple[list[str], list[list[Node]]]]:
    result: list[tuple[list[str], list[list[Node]]]] = []
    tables = [node for node in root.descendants() if node.tag == "table"]
    index = 0
    header_labels = {
        "model", "endpoint", "api", "shutdown date", "release date", "recommended replacement",
        "context", "cost", "version", "deprecationretirement", "feature", "param", "value",
    }
    while index < len(tables):
        table = tables[index]
        rows = table_rows(table)
        if not rows:
            index += 1
            continue
        header_index = None
        for candidate_index, row in enumerate(rows):
            labels = [normalize_text(node_text(cell)).lower() for cell in row]
            if all(cell.tag == "th" for cell in row) or sum(label in header_labels for label in labels) >= 2:
                header_index = candidate_index
                break
        if header_index is None:
            index += 1
            continue
        header_row = rows[header_index]
        headers = [normalize_text(node_text(cell)).lower() for cell in header_row]
        data = [row for row in rows[header_index + 1:] if any(cell.tag == "td" for cell in row)]
        # Some official component libraries render the table header and body as
        # adjacent table elements. Pair only an immediate same-width body table.
        if not data and index + 1 < len(tables):
            next_rows = table_rows(tables[index + 1])
            if next_rows and all(len(row) == len(headers) for row in next_rows) and all(
                any(cell.tag == "td" for cell in row) for row in next_rows
            ):
                data = next_rows
                index += 1
        result.append((headers, data))
        index += 1
    return result


def html_sections(root: Node) -> list[tuple[str, str]]:
    nodes = list(root.descendants())
    content_root = next((n for n in nodes if n.tag == "main"), None)
    if content_root is None:
        content_root = next((n for n in nodes if n.tag == "article"), None)
    if content_root is None:
        content_root = root
    excluded = {"nav", "header", "footer", "aside", "script", "style", "svg", "noscript", "button", "form"}
    sections: list[tuple[str, str]] = []
    current_title = "document"
    current: list[str] = []

    def flush() -> None:
        text = normalize_text("\n".join(current))
        if text:
            sections.append((slug(current_title), text))

    def walk(node: Node) -> None:
        nonlocal current_title, current
        if node.tag in excluded:
            return
        classes = node.attrs.get("class", "").lower()
        if any(marker in classes for marker in ("last-updated", "copy-button", "toolbar", "skip-link", "search-trigger")):
            return
        if node.tag in {"h1", "h2", "h3"}:
            title = normalize_text(node_text(node))
            if title:
                flush()
                current_title, current = title, [title]
            return
        if node.tag in {"p", "li", "tr", "pre", "blockquote"}:
            text = normalize_text(node_text(node))
            if text:
                current.append(text)
            return
        for child in node.children:
            if isinstance(child, Node):
                walk(child)
            elif child.strip():
                text = normalize_text(child)
                if text and node.tag not in excluded:
                    current.append(text)

    walk(content_root)
    flush()
    if not sections:
        text = normalize_text(node_text(content_root))
        if text:
            sections.append(("document", text))
    return sections[:200]


def markdown_sections(body: str) -> list[tuple[str, str]]:
    text = body.lstrip("\ufeff")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4:]
    text = re.sub(r"(?is)<HomeQuickChip\b.*?</HomeQuickChip>", "", text)
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    sections: list[tuple[str, str]] = []
    title = "document"
    lines: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+?)\s*#*\s*$", line)
        if heading:
            content = normalize_text("\n".join(lines))
            if content:
                sections.append((slug(title), content))
            title = normalize_text(heading.group(1))
            lines = [title]
        elif re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line):
            continue
        else:
            lines.append(line)
    content = normalize_text("\n".join(lines))
    if content:
        sections.append((slug(title), content))
    return sections[:200]


def normalize_text(value: str) -> str:
    value = html.unescape(value).replace("\u200b", "").replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (result or "document")[:96]


def markdown_tables(body: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = body.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index < len(lines):
        if "|" not in lines[index]:
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and "|" in lines[index]:
            block.append(lines[index].strip())
            index += 1
        rows = [_split_md_row(line) for line in block]
        rows = [row for row in rows if row and any(cell.strip() for cell in row)]
        if len(rows) >= 2 and _separator_row(rows[1]):
            headers = [normalize_text(_strip_inline(cell)).lower() for cell in rows[0]]
            values = [[normalize_text(cell) for cell in row] for row in rows[2:]]
            tables.append((headers, values))
    return tables


def _split_md_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _separator_row(row: list[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in row)


def _strip_inline(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    return normalize_text(value)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_ID_WITH_FOOTNOTE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\s*\(\d+\)$")


def exact_id(value: str, *, allow_context: bool = False) -> str | None:
    value = normalize_text(value).strip("` ")
    match = _ID_WITH_FOOTNOTE.fullmatch(value)
    if match:
        value = match.group(1)
    if allow_context:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\s*\([^()]*\)", value)
        if match:
            value = match.group(1)
    return value if _ID.fullmatch(value) else None


def code_ids(value: str) -> list[str]:
    return [item for token in re.findall(r"`([^`]+)`", value) if (item := exact_id(token))]


def parse_explicit_date(value: str) -> tuple[date | None, str | None]:
    text = normalize_text(value).strip("`*_ ")
    if not text or re.search(r"(?i)\b(?:no shutdown date|no retirement date|not announced|to be announced|n/?a|none)\b", text):
        return None, None
    qualifier = None
    lower = text.lower()
    if lower.startswith("not sooner than "):
        qualifier = "not_sooner_than"
        text = text[len("not sooner than "):]
    text = re.sub(r"(?i)(\d+)(st|nd|rd|th)\b", r"\1", text)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed, qualifier
        except ValueError:
            continue
    return None, qualifier


def evidence(source: SourceSpec, response: FetchResponse, now: datetime, rule: str, excerpt: str, confidence: str = "structured") -> Evidence:
    digest = hashlib.sha256(response.body).hexdigest()
    return Evidence(response.url, now, rule, normalize_text(excerpt)[:500], digest, confidence)


def make_models(source: SourceSpec, response: FetchResponse, now: datetime, rows: Iterable[tuple[str, str | None, str]], rule: str, *, dedupe: bool = False) -> list[ModelRecord]:
    result: list[ModelRecord] = []
    seen: set[str] = set()
    for model_id, display_name, excerpt in rows:
        if not model_id:
            continue
        if model_id in seen:
            if dedupe:
                continue
            raise AdapterError("duplicate_model_id", "catalog contains duplicate exact IDs")
        seen.add(model_id)
        item_evidence = evidence(source, response, now, rule, excerpt)
        result.append(ModelRecord(model_id, display_name, now, now, item_evidence))
    if not result:
        raise AdapterError("empty_catalog", "no IDs were found in the required labeled catalog structure")
    return result


def make_notices(source: SourceSpec, response: FetchResponse, now: datetime, rows: Iterable[dict], rule: str) -> list[NoticeRecord]:
    result: list[NoticeRecord] = []
    seen: set[tuple[str, str, date | None]] = set()
    for row in rows:
        model_id = row["model_id"]
        status = row.get("status") or "retirement_announced"
        effective_date = row.get("effective_date")
        replacement = row.get("replacement")
        key = (model_id, status, effective_date)
        if key in seen:
            continue
        seen.add(key)
        item_evidence = evidence(source, response, now, rule, row["excerpt"], row.get("confidence", "structured"))
        result.append(NoticeRecord(
            vendor=source.vendor, model_id=model_id, status=status,
            effective_date=effective_date, replacement=replacement, evidence=item_evidence,
            deadline_authoritative=row.get("deadline_authoritative", True),
            date_qualifier=row.get("date_qualifier"), deprecated_date=row.get("deprecated_date"),
        ))
    return result


def make_sections(source: SourceSpec, response: FetchResponse, now: datetime, sections: Iterable[tuple[str, str]], rule: str) -> list[SectionRecord]:
    result: list[SectionRecord] = []
    seen: set[str] = set()
    for section_id, content in sections:
        text = normalize_text(content)
        if not text:
            continue
        base = slug(section_id)
        final_id = base
        suffix = 2
        while final_id in seen:
            final_id = f"{base[:88]}-{suffix}"
            suffix += 1
        seen.add(final_id)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        result.append(SectionRecord(final_id, digest, text[:280], evidence(source, response, now, rule, text[:280], "review_needed")))
    if not result:
        raise AdapterError("missing_document_structure", "no normalized content sections were recognized")
    return result


def normalized_hash(models: list[ModelRecord], notices: list[NoticeRecord], sections: list[SectionRecord]) -> str:
    data = {
        "models": sorted(m.model_id for m in models),
        "notices": sorted((n.model_id, n.status, n.effective_date.isoformat() if n.effective_date else None,
                            n.replacement, n.deadline_authoritative) for n in notices),
        "sections": sorted((s.section_id, s.content_hash) for s in sections),
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot(models: list[ModelRecord], notices: list[NoticeRecord], sections: list[SectionRecord]) -> ParsedSnapshot:
    return ParsedSnapshot(models, notices, sections, normalized_hash(models, notices, sections))


def decode_response(response: FetchResponse) -> str:
    try:
        body = response.body.decode("utf-8-sig", errors="replace")
    except UnicodeError as exc:
        raise AdapterError("invalid_encoding", "response text could not be decoded") from exc
    if body and body.count("\ufffd") / len(body) > 0.02:
        raise AdapterError("invalid_encoding", "response contains too much undecodable text")
    return body
