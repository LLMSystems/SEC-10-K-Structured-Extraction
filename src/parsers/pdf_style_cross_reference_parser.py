from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.models import FilingMetadata, RawItem, RawSpan, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult
from src.patterns import ITEM_META, ITEM_NUMBERS

TABLE_SNIPPET_PATTERN = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
ANCHOR_MARKER_PATTERN = re.compile(r"\[\[ANCHOR:(?P<frag>[^\]]+)\]\]")
HEADING_PATTERN = re.compile(r"form\s+10-k\s+cross-reference\s+index", re.IGNORECASE)
NUMERIC_ITEM_LABEL_PATTERN = re.compile(
    r"^(1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16)\.\s*$",
    re.IGNORECASE,
)
PART_HEADER_PATTERN = re.compile(r"^Part\s+[IVX]+\s*$", re.IGNORECASE)
STAR_ONLY_PATTERN = re.compile(r"^\*{1,5}$")
STAR_SUFFIX_PATTERN = re.compile(r"(?P<marker>\*{1,5})\s*$")
PAGE_RANGE_PATTERN = re.compile(r"\d+\s*(?:\u2013|-)\s*\d+")
PAGE_TOKEN_PATTERN = re.compile(r"\d+\s*(?:\u2013|-)\s*\d+|\d+")
PAGE_NUMBER_PATTERN = re.compile(r"\b\d+\b")
NONE_DECLARED_PATTERN = re.compile(r"\b(?:none|not\s+applicable|n/?a)\b", re.IGNORECASE)
RESERVED_DECLARED_PATTERN = re.compile(r"\breserved\b", re.IGNORECASE)
BY_REFERENCE_TEXT_PATTERN = re.compile(
    r"incorporat(?:ed|ion)\s+(?:herein\s+)?by\s+reference|proxy\s+statement",
    re.IGNORECASE,
)


@dataclass
class CandidateTable:
    html: str
    start_char: int
    end_char: int
    kind: str


@dataclass
class ParsedEntry:
    item_number: str
    title_text: str
    part: str | None = None
    reference_chunks: list[str] = field(default_factory=list)
    row_texts: list[str] = field(default_factory=list)
    status_hint: str | None = None
    star_marker: str | None = None
    by_reference_text: str | None = None

    @property
    def reference_text(self) -> str:
        return " ".join(chunk for chunk in self.reference_chunks if chunk).strip()


class PdfStyleCrossReferenceParser(BaseParser):
    @property
    def name(self) -> str:
        return "pdf_style_cross_reference"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        text = doc.text
        html_source = doc.normalized_html or text
        warnings: list[str] = []

        heading_match = HEADING_PATTERN.search(html_source)
        if not heading_match:
            return self._make_result([], ["Missing PDF-style cross-reference index heading"])

        tables = self._collect_candidate_tables(html_source, text, heading_match.end())
        if not tables:
            return self._make_result([], ["Missing PDF-style cross-reference index tables"])

        entries, footnotes = self._parse_tables(tables)
        if not entries:
            return self._make_result([], ["Unable to parse item rows from PDF-style cross-reference tables"])

        self._apply_footnotes(entries, footnotes)
        frag_to_body_pos = {
            match.group("frag"): match.end()
            for match in ANCHOR_MARKER_PATTERN.finditer(text)
        }
        page_start_to_pos = self._coerce_page_start_map(doc.extra.get("page_start_to_pos"))
        anchor_page_to_pos = self._build_page_to_pos(html_source, frag_to_body_pos)
        raw_items = self._build_raw_items(
            text,
            tables,
            entries,
            metadata,
            page_start_to_pos,
            anchor_page_to_pos,
        )
        if not raw_items:
            warnings.append("PDF-style cross-reference parser found structure but produced no RawItem")
            return self._make_result([], warnings)

        unresolved = [
            item.item_number for item in raw_items
            if not item.status_hint and item.end_char is None and not item.spans
        ]
        if unresolved:
            warnings.append(f"Recovered TOC structure only for items without body spans: {sorted(unresolved)}")

        return self._make_result(raw_items, warnings)

    def _collect_candidate_tables(
        self,
        html_source: str,
        text_source: str,
        heading_end: int,
    ) -> list[CandidateTable]:
        candidates: list[CandidateTable] = []
        seen_index_table = False
        search_limit = min(len(html_source), heading_end + 30000)
        text_cursor = 0

        for match in TABLE_SNIPPET_PATTERN.finditer(html_source, heading_end):
            if match.start() > search_limit:
                break

            table_html = match.group(0)
            kind = self._classify_table(table_html)
            if kind is None:
                if seen_index_table:
                    break
                continue

            text_start = text_source.find(table_html, text_cursor)
            if text_start >= 0:
                text_end = text_start + len(table_html)
                text_cursor = text_end
            else:
                text_start = -1
                text_end = -1

            candidates.append(
                CandidateTable(
                    html=table_html,
                    start_char=text_start,
                    end_char=text_end,
                    kind=kind,
                )
            )
            if kind == "index":
                seen_index_table = True

        return candidates

    def _classify_table(self, table_html: str) -> str | None:
        soup = BeautifulSoup(table_html, "html.parser")
        item_rows = 0
        part_rows = 0
        footnote_rows = 0

        for row in soup.find_all("tr"):
            cell_texts = self._row_cell_texts(row)
            if not cell_texts:
                continue

            first = cell_texts[0]
            if PART_HEADER_PATTERN.fullmatch(first):
                part_rows += 1
                continue
            if NUMERIC_ITEM_LABEL_PATTERN.fullmatch(first):
                item_rows += 1
                continue
            if STAR_ONLY_PATTERN.fullmatch(first) and len(cell_texts) >= 2:
                footnote_rows += 1

        if item_rows >= 2 or (item_rows >= 1 and part_rows >= 1):
            return "index"
        if footnote_rows >= 1:
            return "footnote"
        return None

    def _parse_tables(
        self,
        tables: list[CandidateTable],
    ) -> tuple[dict[str, ParsedEntry], dict[str, str]]:
        entries: dict[str, ParsedEntry] = {}
        footnotes: dict[str, str] = {}
        current_part: str | None = None
        current_entry: ParsedEntry | None = None

        for table in tables:
            soup = BeautifulSoup(table.html, "html.parser")
            for row in soup.find_all("tr"):
                cell_texts = self._row_cell_texts(row)
                if not cell_texts:
                    continue

                first = cell_texts[0]
                row_text = self._normalize_ws(" ".join(cell_texts))

                if PART_HEADER_PATTERN.fullmatch(first):
                    current_part = first
                    current_entry = None
                    continue

                if STAR_ONLY_PATTERN.fullmatch(first) and len(cell_texts) >= 2:
                    footnotes[first] = self._normalize_ws(" ".join(cell_texts[1:]))
                    current_entry = None
                    continue

                item_match = NUMERIC_ITEM_LABEL_PATTERN.fullmatch(first)
                if item_match:
                    item_number = item_match.group(1).upper()
                    title_text, reference_text = self._split_item_row(cell_texts[1:], item_number)
                    entry = entries.setdefault(
                        item_number,
                        ParsedEntry(
                            item_number=item_number,
                            title_text=title_text or ITEM_META[item_number][1],
                            part=current_part,
                        ),
                    )
                    if title_text:
                        entry.title_text = title_text
                    entry.row_texts.append(row_text)
                    if reference_text:
                        entry.reference_chunks.append(reference_text)
                    self._apply_inline_status(entry, title_text, reference_text)
                    current_entry = entry
                    continue

                if current_entry and self._looks_like_continuation_row(cell_texts):
                    current_entry.row_texts.append(row_text)
                    current_entry.reference_chunks.append(cell_texts[-1])

        return entries, footnotes

    def _split_item_row(self, trailing_cells: list[str], item_number: str) -> tuple[str, str]:
        if not trailing_cells:
            return ITEM_META[item_number][1], ""

        if len(trailing_cells) == 1:
            only = trailing_cells[0]
            if self._looks_like_reference_text(only):
                return ITEM_META[item_number][1], only
            return only, ""

        ref_idx: int | None = None
        for idx in range(len(trailing_cells) - 1, -1, -1):
            if self._looks_like_reference_text(trailing_cells[idx]):
                ref_idx = idx
                break

        if ref_idx is None:
            return " ".join(trailing_cells).strip(), ""

        title_parts = trailing_cells[:ref_idx]
        reference_text = " ".join(trailing_cells[ref_idx:]).strip()
        title_text = " ".join(title_parts).strip() or ITEM_META[item_number][1]
        return title_text, reference_text

    def _apply_inline_status(self, entry: ParsedEntry, title_text: str, reference_text: str) -> None:
        title = self._normalize_ws(title_text)
        ref = self._normalize_ws(reference_text)

        if RESERVED_DECLARED_PATTERN.fullmatch(title) or (
            RESERVED_DECLARED_PATTERN.search(ref) and not PAGE_NUMBER_PATTERN.search(ref)
        ):
            entry.status_hint = "reserved_declared"
            return

        if NONE_DECLARED_PATTERN.search(ref) and not PAGE_NUMBER_PATTERN.search(ref):
            entry.status_hint = "none_declared"
            return

        star_match = STAR_ONLY_PATTERN.fullmatch(ref) or STAR_SUFFIX_PATTERN.search(ref)
        if star_match:
            marker = ref if STAR_ONLY_PATTERN.fullmatch(ref) else star_match.group("marker")
            entry.star_marker = marker

    def _apply_footnotes(self, entries: dict[str, ParsedEntry], footnotes: dict[str, str]) -> None:
        for entry in entries.values():
            if entry.status_hint is not None:
                continue
            if not entry.star_marker:
                continue

            footnote_text = footnotes.get(entry.star_marker, "")
            if BY_REFERENCE_TEXT_PATTERN.search(footnote_text):
                entry.status_hint = "by_reference_declared"
                entry.by_reference_text = footnote_text

    def _build_raw_items(
        self,
        text: str,
        tables: list[CandidateTable],
        entries: dict[str, ParsedEntry],
        metadata: FilingMetadata,
        page_start_to_pos: dict[int, int],
        anchor_page_to_pos: dict[int, int],
    ) -> list[RawItem]:
        ordered_items = [num for num in ITEM_NUMBERS if num in entries]
        if not metadata.has_item_1c and "1C" in ordered_items:
            ordered_items.remove("1C")

        heading_match = HEADING_PATTERN.search(text)
        search_start = heading_match.end() if heading_match else 0
        if tables and tables[0].start_char >= 0:
            search_start = tables[0].start_char
        segment_end = len(text)
        if tables and tables[-1].end_char >= 0:
            segment_end = tables[-1].end_char
        ordered_page_starts = sorted(page_start_to_pos)
        ordered_anchor_pages = sorted(anchor_page_to_pos)
        title_positions: dict[str, int] = {}

        for item_number in ordered_items:
            entry = entries[item_number]
            pos = self._find_entry_position(
                text=text,
                entry=entry,
                search_start=search_start,
                search_end=segment_end,
            )
            title_positions[item_number] = pos
            if pos >= 0:
                search_start = pos + max(1, len(entry.title_text) // 2)

        raw_items: list[RawItem] = []
        for idx, item_number in enumerate(ordered_items):
            entry = entries[item_number]
            spans = self._build_spans_from_page_markers(
                entry,
                page_start_to_pos,
                ordered_page_starts,
                len(text),
            )
            if not spans:
                spans = self._build_spans_for_entry(
                    entry,
                    anchor_page_to_pos,
                    ordered_anchor_pages,
                    len(text),
                )
            if spans:
                start_char = spans[0].start_char
                end_char = spans[-1].end_char
            else:
                start_char = title_positions.get(item_number, -1)
                end_char = None
            if not spans and start_char >= 0:
                next_positions = [
                    title_positions[num]
                    for num in ordered_items[idx + 1:]
                    if title_positions.get(num, -1) > start_char
                ]
                if next_positions:
                    end_char = next_positions[0]
                else:
                    end_char = segment_end

            confidence = 0.9 if spans else (0.88 if entry.status_hint else 0.72)
            raw_items.append(
                RawItem(
                    item_number=item_number,
                    title_text=entry.title_text or ITEM_META[item_number][1],
                    start_char=max(start_char, 0),
                    end_char=end_char,
                    spans=spans,
                    status_hint=entry.status_hint,
                    by_reference_text=entry.by_reference_text,
                    confidence=confidence,
                )
            )

        return raw_items

    def _coerce_page_start_map(self, value) -> dict[int, int]:
        if not isinstance(value, dict):
            return {}

        page_start_to_pos: dict[int, int] = {}
        for key, raw_pos in value.items():
            try:
                page_num = int(key)
                pos = int(raw_pos)
            except (TypeError, ValueError):
                continue
            if pos < 0:
                continue
            page_start_to_pos[page_num] = pos

        return page_start_to_pos

    def _find_entry_position(
        self,
        text: str,
        entry: ParsedEntry,
        search_start: int,
        search_end: int,
    ) -> int:
        candidates = [entry.title_text]
        if entry.row_texts:
            candidates.extend(entry.row_texts)
        candidates.append(f"{entry.item_number}.")

        for candidate in candidates:
            needle = self._normalize_ws(candidate)
            if not needle:
                continue
            pos = text.find(needle, search_start, search_end)
            if pos >= 0:
                return pos

        return -1

    def _build_page_to_pos(
        self,
        html_source: str,
        frag_to_body_pos: dict[str, int],
    ) -> dict[int, int]:
        if not frag_to_body_pos:
            return {}

        page_to_pos: dict[int, int] = {}
        for match in TABLE_SNIPPET_PATTERN.finditer(html_source):
            soup = BeautifulSoup(match.group(0), "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "")
                if "#" not in href:
                    continue
                page_text = self._normalize_ws(anchor.get_text(" ", strip=True))
                if not page_text.isdigit():
                    continue

                fragment_id = href.split("#", 1)[1]
                if fragment_id not in frag_to_body_pos:
                    continue

                page_num = int(page_text)
                page_to_pos.setdefault(page_num, frag_to_body_pos[fragment_id])

        return page_to_pos

    def _build_spans_for_entry(
        self,
        entry: ParsedEntry,
        page_to_pos: dict[int, int],
        ordered_pages: list[int],
        terminal_pos: int,
    ) -> list[RawSpan]:
        if not page_to_pos:
            return []

        spans: list[RawSpan] = []
        for start_page, end_page, source_text in self._parse_reference_ranges(entry.reference_text):
            start_pos = page_to_pos.get(start_page)
            if start_pos is None:
                continue

            end_pos = self._next_page_start_after(end_page, page_to_pos, ordered_pages)
            if end_pos is None:
                end_pos = terminal_pos
            if end_pos <= start_pos:
                continue

            spans.append(
                RawSpan(
                    start_char=start_pos,
                    end_char=end_pos,
                    source=source_text,
                )
            )

        return self._merge_spans(spans)

    def _build_spans_from_page_markers(
        self,
        entry: ParsedEntry,
        page_start_to_pos: dict[int, int],
        ordered_pages: list[int],
        terminal_pos: int,
    ) -> list[RawSpan]:
        if not page_start_to_pos:
            return []

        spans: list[RawSpan] = []
        for start_page, end_page, source_text in self._parse_reference_ranges(entry.reference_text):
            start_pos = page_start_to_pos.get(start_page)
            if start_pos is None:
                continue

            end_pos = self._next_page_boundary(end_page + 1, page_start_to_pos, ordered_pages)
            if end_pos is None:
                end_pos = self._next_page_boundary(end_page, page_start_to_pos, ordered_pages, strictly_greater=True)
            if end_pos is None:
                end_pos = terminal_pos
            if end_pos <= start_pos:
                continue

            spans.append(
                RawSpan(
                    start_char=start_pos,
                    end_char=end_pos,
                    source=source_text,
                )
            )

        return self._merge_spans(spans)

    def _parse_reference_ranges(self, reference_text: str) -> list[tuple[int, int, str]]:
        cleaned = self._normalize_ws(reference_text.replace("*", " "))
        tokens = PAGE_TOKEN_PATTERN.findall(cleaned)
        refs: list[tuple[int, int, str]] = []

        for token in tokens:
            if "\u2013" in token or "-" in token:
                parts = re.split(r"(?:\u2013|-)", token, maxsplit=1)
                start_page = int(parts[0].strip())
                end_page = int(parts[1].strip())
                refs.append((start_page, end_page, token))
            else:
                page_num = int(token)
                refs.append((page_num, page_num, token))

        return refs

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

    def _next_page_boundary(
        self,
        page_num: int,
        page_start_to_pos: dict[int, int],
        ordered_pages: list[int],
        *,
        strictly_greater: bool = False,
    ) -> int | None:
        for candidate in ordered_pages:
            if strictly_greater:
                if candidate <= page_num:
                    continue
            else:
                if candidate < page_num:
                    continue

            pos = page_start_to_pos.get(candidate)
            if pos is not None:
                return pos

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

    def _looks_like_continuation_row(self, cell_texts: list[str]) -> bool:
        nonempty = [text for text in cell_texts if text]
        if not nonempty:
            return False
        if NUMERIC_ITEM_LABEL_PATTERN.fullmatch(nonempty[0]):
            return False
        if PART_HEADER_PATTERN.fullmatch(nonempty[0]):
            return False
        if STAR_ONLY_PATTERN.fullmatch(nonempty[0]):
            return False
        return self._looks_like_reference_text(nonempty[-1]) and len(nonempty) <= 2

    def _looks_like_reference_text(self, text: str) -> bool:
        compact = self._normalize_ws(text)
        return bool(
            compact
            and (
                PAGE_RANGE_PATTERN.search(compact)
                or NONE_DECLARED_PATTERN.search(compact)
                or RESERVED_DECLARED_PATTERN.search(compact)
                or STAR_ONLY_PATTERN.fullmatch(compact)
                or STAR_SUFFIX_PATTERN.search(compact)
                or PAGE_NUMBER_PATTERN.fullmatch(compact)
            )
        )

    def _row_cell_texts(self, row) -> list[str]:
        texts = [
            self._normalize_ws(cell.get_text(" ", strip=True).replace("\xa0", " "))
            for cell in row.find_all(["td", "th"])
        ]
        return [text for text in texts if text]

    def _normalize_ws(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
