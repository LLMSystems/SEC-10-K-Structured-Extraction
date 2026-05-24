"""
Cross-reference multi-span parser.

專門處理 Intel 這類用 Form 10-K Cross-Reference Index 把單一 item
映射到多個 page references 的 filing。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.models import FilingMetadata, RawItem, RawSpan
from src.parsers.base import BaseParser, ParseResult
from src.patterns import ITEM_META, ITEM_NUMBERS

ANCHOR_MARKER_PATTERN = re.compile(r"\[\[ANCHOR:(?P<frag>[^\]]+)\]\]")
TABLE_SNIPPET_PATTERN = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
ITEM_ROW_PATTERN = re.compile(
    r"\bItem\s+(1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16)\.?\b",
    re.IGNORECASE,
)
PART_ROW_PATTERN = re.compile(r"^\s*Part\s+[IVX]+\b", re.IGNORECASE)
SIGNATURES_ROW_PATTERN = re.compile(r"^\s*Signatures?\b", re.IGNORECASE)
PAGE_TOKEN_PATTERN = re.compile(r"\d+\s*-\s*\d+|\d+")
BY_REFERENCE_MARKER_PATTERN = re.compile(r"\(\s*a\s*\)", re.IGNORECASE)
NONE_DECLARED_PATTERN = re.compile(r"\b(?:none|not\s+applicable|n/?a)\b", re.IGNORECASE)
RESERVED_DECLARED_PATTERN = re.compile(r"\[\s*reserved\s*\]|\breserved\b", re.IGNORECASE)
TOC_LABEL = "table of contents"


@dataclass
class PageLink:
    page_num: int
    fragment_id: str


@dataclass
class PageReference:
    start_page: int
    end_page: int
    source_text: str
    start_fragment_id: str | None = None
    end_fragment_id: str | None = None
    by_reference_marker: bool = False


@dataclass
class ItemEntry:
    item_number: str
    title_text: str
    references: list[PageReference] = field(default_factory=list)
    status_hint: str | None = None


class CrossReferenceMultiSpanParser(BaseParser):
    @property
    def name(self) -> str:
        return "cross_reference_multispan"

    def parse(self, text: str, metadata: FilingMetadata) -> ParseResult:
        warnings: list[str] = []

        frag_to_body_pos = {
            match.group("frag"): self._advance_to_content_start(text, match.end())
            for match in ANCHOR_MARKER_PATTERN.finditer(text)
        }
        if not frag_to_body_pos:
            warnings.append("找不到 anchor marker，無法啟用 cross-reference parser")
            return self._make_result([], warnings)

        table_html = self._select_cross_reference_table(text)
        if table_html is None:
            warnings.append("找不到 Form 10-K Cross-Reference Index table")
            return self._make_result([], warnings)

        entries, page_to_pos = self._parse_cross_reference_table(table_html, frag_to_body_pos)
        if not entries:
            warnings.append("Cross-reference table 未解析出任何 item")
            return self._make_result([], warnings)

        raw_items = self._build_raw_items(entries, page_to_pos, frag_to_body_pos, text, metadata)
        if not raw_items:
            warnings.append("Cross-reference parser 未能建立任何 RawItem")
            return self._make_result([], warnings)

        found_nums = {item.item_number for item in raw_items}
        expected = self._expected_items(metadata)
        missing = expected - found_nums
        if missing:
            warnings.append(f"Cross-reference parser 未找到以下 Item：{sorted(missing)}")

        return self._make_result(raw_items, warnings)

    def _select_cross_reference_table(self, text: str) -> str | None:
        best_score = -1
        best_table: str | None = None

        for match in TABLE_SNIPPET_PATTERN.finditer(text):
            table_html = match.group(0)
            table_lower = table_html.lower()
            item_refs = {
                row_match.group(1).upper()
                for row_match in ITEM_ROW_PATTERN.finditer(table_html)
                if row_match.group(1).upper() in ITEM_META
            }
            if len(item_refs) < 8:
                continue

            page_refs = len(re.findall(r"\bpages?\b", table_lower))
            link_count = len(re.findall(r'href="[^"]+#', table_lower))
            nearby_context = text[max(0, match.start() - 800):match.start()].lower()
            score = (len(item_refs) * 5) + page_refs + link_count
            if "cross-reference index" in nearby_context:
                score += 30
            if "signatures" in table_lower:
                score += 5

            if score > best_score:
                best_score = score
                best_table = table_html

        return best_table

    def _parse_cross_reference_table(
        self,
        table_html: str,
        frag_to_body_pos: dict[str, int],
    ) -> tuple[dict[str, ItemEntry], dict[int, int]]:
        soup = BeautifulSoup(table_html, "html.parser")
        entries: dict[str, ItemEntry] = {}
        page_to_pos: dict[int, int] = {}
        current_item: str | None = None

        for row in soup.find_all("tr"):
            row_text = self._normalize_ws(row.get_text(" ", strip=True))
            if not row_text:
                continue

            if row_text.lower().startswith("item number"):
                continue
            if PART_ROW_PATTERN.match(row_text):
                current_item = None
                continue
            if SIGNATURES_ROW_PATTERN.match(row_text):
                current_item = None
                continue

            for link in self._extract_page_links(row):
                if link.fragment_id in frag_to_body_pos:
                    page_to_pos.setdefault(link.page_num, frag_to_body_pos[link.fragment_id])

            item_match = ITEM_ROW_PATTERN.search(row_text)
            if item_match:
                current_item = item_match.group(1).upper()
                entry = entries.setdefault(
                    current_item,
                    ItemEntry(
                        item_number=current_item,
                        title_text=self._extract_item_title(row, current_item),
                    ),
                )
                refs, status_hint = self._extract_row_references(row)
                entry.references.extend(refs)
                entry.status_hint = self._merge_status_hint(entry.status_hint, status_hint)
                continue

            if current_item is None:
                continue

            entry = entries.setdefault(
                current_item,
                ItemEntry(
                    item_number=current_item,
                    title_text=ITEM_META[current_item][1],
                ),
            )
            refs, status_hint = self._extract_row_references(row)
            entry.references.extend(refs)
            entry.status_hint = self._merge_status_hint(entry.status_hint, status_hint)

        return entries, page_to_pos

    def _build_raw_items(
        self,
        entries: dict[str, ItemEntry],
        page_to_pos: dict[int, int],
        frag_to_body_pos: dict[str, int],
        text: str,
        metadata: FilingMetadata,
    ) -> list[RawItem]:
        ordered_pages = sorted(page_to_pos)
        raw_items: list[RawItem] = []

        for item_number in ITEM_NUMBERS:
            if item_number == "1C" and not metadata.has_item_1c:
                continue
            entry = entries.get(item_number)
            if entry is None:
                continue

            spans = self._build_spans_for_item(
                entry,
                page_to_pos,
                frag_to_body_pos,
                ordered_pages,
                len(text),
            )
            if spans:
                start_char = spans[0].start_char
                end_char = spans[-1].end_char
                confidence = 0.82
            else:
                start_char = 0
                end_char = None
                confidence = 0.72 if entry.status_hint else 0.0

            raw_items.append(
                RawItem(
                    item_number=item_number,
                    title_text=entry.title_text or ITEM_META[item_number][1],
                    start_char=start_char,
                    end_char=end_char,
                    spans=spans,
                    status_hint=entry.status_hint,
                    confidence=confidence,
                )
            )

        return raw_items

    def _build_spans_for_item(
        self,
        entry: ItemEntry,
        page_to_pos: dict[int, int],
        frag_to_body_pos: dict[str, int],
        ordered_pages: list[int],
        terminal_pos: int,
    ) -> list[RawSpan]:
        spans: list[RawSpan] = []

        for ref in entry.references:
            start_pos = None
            if ref.start_fragment_id is not None:
                start_pos = frag_to_body_pos.get(ref.start_fragment_id)
            if start_pos is None:
                start_pos = page_to_pos.get(ref.start_page)
            if start_pos is None:
                continue

            end_pos = self._next_page_start_after(ref.end_page, page_to_pos, ordered_pages)
            if end_pos is None:
                end_pos = terminal_pos

            if end_pos <= start_pos:
                continue

            spans.append(
                RawSpan(
                    start_char=start_pos,
                    end_char=end_pos,
                    source=ref.source_text,
                )
            )

        return self._merge_spans(spans)

    def _extract_item_title(self, row, item_number: str) -> str:
        cell_texts = [self._normalize_ws(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        cell_texts = [text for text in cell_texts if text]
        for text in cell_texts:
            if ITEM_ROW_PATTERN.search(text):
                continue
            if self._looks_like_reference_cell(text):
                continue
            return text.rstrip(":").strip() or ITEM_META[item_number][1]
        return ITEM_META[item_number][1]

    def _extract_row_references(self, row) -> tuple[list[PageReference], str | None]:
        cell_texts = [self._normalize_ws(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        cell_texts = [text for text in cell_texts if text]
        ref_text = cell_texts[-1] if cell_texts else ""
        if not ref_text:
            return [], None

        if RESERVED_DECLARED_PATTERN.search(ref_text) and not PAGE_TOKEN_PATTERN.search(ref_text):
            return [], "reserved_declared"

        if NONE_DECLARED_PATTERN.search(ref_text) and not PAGE_TOKEN_PATTERN.search(ref_text):
            return [], "none_declared"

        if BY_REFERENCE_MARKER_PATTERN.fullmatch(ref_text):
            return [], "by_reference_declared"

        links = self._extract_page_links(row)
        page_tokens = PAGE_TOKEN_PATTERN.findall(ref_text)
        if not page_tokens:
            return [], None

        refs: list[PageReference] = []
        link_idx = 0
        has_by_reference_marker = bool(BY_REFERENCE_MARKER_PATTERN.search(ref_text))

        for token in page_tokens:
            if "-" in token:
                start_page_text, end_page_text = [part.strip() for part in token.split("-", 1)]
                start_page = int(start_page_text)
                end_page = int(end_page_text)
                start_fragment_id, link_idx = self._consume_matching_link(links, link_idx, start_page)
                end_fragment_id, link_idx = self._consume_matching_link(links, link_idx, end_page)
                refs.append(
                    PageReference(
                        start_page=start_page,
                        end_page=end_page,
                        source_text=token,
                        start_fragment_id=start_fragment_id,
                        end_fragment_id=end_fragment_id,
                        by_reference_marker=has_by_reference_marker,
                    )
                )
            else:
                page_num = int(token)
                fragment_id, link_idx = self._consume_matching_link(links, link_idx, page_num)
                refs.append(
                    PageReference(
                        start_page=page_num,
                        end_page=page_num,
                        source_text=token,
                        start_fragment_id=fragment_id,
                        end_fragment_id=fragment_id,
                        by_reference_marker=has_by_reference_marker,
                    )
                )

        return refs, None

    def _extract_page_links(self, row) -> list[PageLink]:
        links: list[PageLink] = []

        for anchor in row.find_all("a", href=True):
            href = anchor.get("href", "")
            if "#" not in href:
                continue

            page_text = self._normalize_ws(anchor.get_text(" ", strip=True))
            if not page_text.isdigit():
                continue

            links.append(
                PageLink(
                    page_num=int(page_text),
                    fragment_id=href.split("#", 1)[1],
                )
            )

        return links

    def _consume_matching_link(
        self,
        links: list[PageLink],
        start_idx: int,
        page_num: int,
    ) -> tuple[str | None, int]:
        for idx in range(start_idx, len(links)):
            if links[idx].page_num == page_num:
                return links[idx].fragment_id, idx + 1

        if start_idx < len(links):
            return links[start_idx].fragment_id, start_idx + 1

        return None, start_idx

    def _next_page_start_after(
        self,
        end_page: int,
        page_to_pos: dict[int, int],
        ordered_pages: list[int],
    ) -> int | None:
        for page_num in ordered_pages:
            if page_num > end_page and page_num in page_to_pos:
                return page_to_pos[page_num]
        return None

    def _merge_spans(self, spans: list[RawSpan]) -> list[RawSpan]:
        if not spans:
            return []

        ordered = sorted(spans, key=lambda span: (span.start_char, span.end_char))
        merged: list[RawSpan] = [ordered[0]]

        for span in ordered[1:]:
            current = merged[-1]
            if span.start_char <= current.end_char:
                current.end_char = max(current.end_char, span.end_char)
                if current.source and span.source:
                    current.source = f"{current.source}, {span.source}"
                continue
            merged.append(span)

        return merged

    def _merge_status_hint(self, existing: str | None, new: str | None) -> str | None:
        if existing == "reserved_declared" or new == "reserved_declared":
            return "reserved_declared" if existing or new else None
        if existing == "none_declared" or new == "none_declared":
            return "none_declared" if existing or new else None
        if new is not None:
            return new
        return existing

    def _looks_like_reference_cell(self, text: str) -> bool:
        return bool(
            PAGE_TOKEN_PATTERN.search(text)
            or NONE_DECLARED_PATTERN.search(text)
            or RESERVED_DECLARED_PATTERN.search(text)
            or BY_REFERENCE_MARKER_PATTERN.search(text)
        )

    def _advance_to_content_start(self, text: str, pos: int) -> int:
        skip_leading_table = False

        while pos < len(text):
            while pos < len(text) and text[pos].isspace():
                pos += 1

            nested_anchor = ANCHOR_MARKER_PATTERN.match(text, pos)
            if nested_anchor:
                pos = nested_anchor.end()
                continue

            if text[pos:pos + len(TOC_LABEL)].lower() == TOC_LABEL:
                pos += len(TOC_LABEL)
                skip_leading_table = True
                continue

            if SIGNATURES_ROW_PATTERN.match(text[pos:pos + 40]):
                pos += len("Signatures")
                continue

            table_match = TABLE_SNIPPET_PATTERN.match(text, pos)
            if table_match:
                if skip_leading_table or TOC_LABEL in table_match.group(0).lower():
                    pos = table_match.end()
                    skip_leading_table = False
                    continue

            break

        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def _expected_items(self, metadata: FilingMetadata) -> set[str]:
        expected = set(ITEM_NUMBERS)
        if not metadata.has_item_1c:
            expected.discard("1C")
        return expected

    def _normalize_ws(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
