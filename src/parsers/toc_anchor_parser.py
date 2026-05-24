"""
TOC Anchor Parser

處理正文不用 "Item X" 標題、但 TOC 保留 item -> fragment anchor 的 filing。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.models import RawItem, FilingMetadata
from src.parsers.base import BaseParser, ParseResult
from src.patterns import HTML_TAG_PATTERN, ITEM_NUMBERS, ITEM_META

ANCHOR_MARKER_PATTERN = re.compile(r"\[\[ANCHOR:(?P<frag>[^\]]+)\]\]")
TABLE_SNIPPET_PATTERN = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
TOC_ITEM_PATTERN = re.compile(
    r"\bItem\s+(1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16)\.?\b",
    re.IGNORECASE,
)
PART_PATTERN = re.compile(r"^\s*Part\s+[IVX]+\b", re.IGNORECASE)
PAGE_NUMBER_PATTERN = re.compile(r"^\d+$")
TOC_LABEL = "table of contents"
SIGNATURES_LABEL = "signatures"
NO_TARGET_ROW_PATTERN = re.compile(r"\b(?:none|not\s+applicable|n/?a|reserved)\b", re.IGNORECASE)
ROW_CARRY_BREAK_PATTERN = re.compile(r"^\s*(?:signatures?|part\s+[ivx]+)\b", re.IGNORECASE)

TITLE_STOPWORDS = {
    "about",
    "accounting",
    "and",
    "business",
    "certain",
    "common",
    "condition",
    "data",
    "directors",
    "disclosure",
    "disclosures",
    "equity",
    "fees",
    "financial",
    "for",
    "form",
    "holders",
    "independence",
    "information",
    "management",
    "market",
    "matters",
    "operations",
    "other",
    "our",
    "ownership",
    "principal",
    "proceedings",
    "purchases",
    "qualitative",
    "quantitative",
    "registrant",
    "related",
    "results",
    "safety",
    "security",
    "services",
    "statements",
    "stockholder",
    "summary",
    "supplementary",
    "that",
    "the",
    "with",
}

ITEM_HINT_PHRASES: dict[str, tuple[str, ...]] = {
    "1A": ("risk factors",),
    "1B": ("unresolved staff comments",),
    "1C": ("cybersecurity",),
    "2": ("properties",),
    "3": ("legal proceedings",),
    "4": ("mine safety",),
    "5": ("stockholder matters", "issuer purchases"),
    "7": (
        "management's discussion and analysis",
        "managements discussion and analysis",
        "md&a",
    ),
    "7A": ("market risk",),
    "8": ("financial statements", "supplemental details", "supplementary data"),
    "9": ("disagreements with accountants",),
    "9A": ("controls and procedures",),
    "9B": ("other information",),
    "9C": ("foreign jurisdictions", "prevent inspections"),
    "10": ("executive officers", "corporate governance", "directors"),
    "11": ("executive compensation",),
    "12": ("beneficial owners", "stockholder matters"),
    "13": ("related transactions", "director independence"),
    "14": ("principal accountant fees",),
    "15": ("exhibits", "financial statement schedules", "financial statements"),
    "16": ("form 10-k summary",),
}


@dataclass
class TocLink:
    page_num: int | None
    fragment_id: str


class TocAnchorParser(BaseParser):
    @property
    def name(self) -> str:
        return "toc_anchor"

    def parse(self, text: str, metadata: FilingMetadata) -> ParseResult:
        warnings: list[str] = []

        if "[[ANCHOR:" not in text:
            warnings.append("找不到 anchor marker，無法啟用 TOC anchor parser")
            return self._make_result([], warnings)

        raw_items = self._extract_items_from_toc(text)
        if not raw_items:
            warnings.append("找不到可用的 TOC item -> anchor 對應")
            return self._make_result([], warnings)

        raw_items = self._assign_end_chars_by_text_order(raw_items, text)

        found_nums = {item.item_number for item in raw_items}
        expected = self._expected_items(metadata)
        missing = expected - found_nums
        if missing:
            warnings.append(f"TOC anchor parser 未找到以下 Item：{sorted(missing)}")

        return ParseResult(
            raw_items=raw_items,
            confidence=sum(item.confidence for item in raw_items) / len(raw_items),
            parser_name=self.name,
            warnings=warnings,
        )

    def _extract_items_from_toc(self, text: str) -> list[RawItem]:
        frag_to_start = {
            match.group("frag"): match.start()
            for match in ANCHOR_MARKER_PATTERN.finditer(text)
        }
        frag_to_body_pos = {
            match.group("frag"): self._advance_to_content_start(text, match.end())
            for match in ANCHOR_MARKER_PATTERN.finditer(text)
        }

        item_to_links: dict[str, list[TocLink]] = {}

        for table_match in TABLE_SNIPPET_PATTERN.finditer(text):
            table_html = table_match.group(0)
            if not self._looks_like_toc_table(table_html):
                continue

            table_soup = BeautifulSoup(table_html, "html.parser")
            current_items: list[str] = []

            for row in table_soup.find_all("tr"):
                row_text = self._normalize_ws(row.get_text(" ", strip=True))
                if not row_text:
                    continue

                item_numbers = [
                    match.group(1).upper()
                    for match in TOC_ITEM_PATTERN.finditer(row_text)
                    if match.group(1).upper() in ITEM_META
                ]
                if item_numbers:
                    current_items = item_numbers
                elif PART_PATTERN.match(row_text) or self._breaks_item_carry(row_text):
                    current_items = []

                if not current_items:
                    continue

                row_links = self._extract_row_links(row, frag_to_start)
                if not row_links:
                    if item_numbers and self._row_explicitly_has_no_target(row_text):
                        current_items = []
                    continue

                for item_number in current_items:
                    item_to_links.setdefault(item_number, []).extend(row_links)

        raw_items: list[RawItem] = []
        for item_number, links in item_to_links.items():
            link = self._choose_best_link(item_number, links, frag_to_start, text)
            if link is None:
                continue

            raw_items.append(
                RawItem(
                    item_number=item_number,
                    title_text=f"[[ANCHOR:{link.fragment_id}]]",
                    start_char=frag_to_body_pos[link.fragment_id],
                    confidence=0.75,
                )
            )

        raw_items.sort(key=lambda item: item.start_char)
        return raw_items

    def _looks_like_toc_table(self, table_html: str) -> bool:
        item_refs = {
            match.group(1).upper()
            for match in TOC_ITEM_PATTERN.finditer(table_html)
            if match.group(1).upper() in ITEM_META
        }
        return len(item_refs) >= 4

    def _extract_row_links(self, row, frag_to_pos: dict[str, int]) -> list[TocLink]:
        links: list[TocLink] = []

        for link in row.find_all("a", href=True):
            href = link.get("href", "")
            if "#" not in href:
                continue

            fragment_id = href.split("#", 1)[1]
            if fragment_id not in frag_to_pos:
                continue

            page_num = self._parse_page_num(link.get_text(" ", strip=True))
            links.append(TocLink(page_num=page_num, fragment_id=fragment_id))

        return links

    def _choose_best_link(
        self,
        item_number: str,
        links: list[TocLink],
        frag_to_pos: dict[str, int],
        text: str,
    ) -> TocLink | None:
        unique_links: list[TocLink] = []
        seen_fragments: set[str] = set()

        for link in links:
            if link.fragment_id in seen_fragments:
                continue
            seen_fragments.add(link.fragment_id)
            unique_links.append(link)

        if not unique_links:
            return None

        scored_links = sorted(
            unique_links,
            key=lambda link: (
                -self._score_link(item_number, link, frag_to_pos, text),
                link.page_num is None,
                link.page_num if link.page_num is not None else 10**9,
                frag_to_pos[link.fragment_id],
            ),
        )
        return scored_links[0]

    def _assign_end_chars_by_text_order(self, items: list[RawItem], text: str) -> list[RawItem]:
        if not items:
            return items

        terminal = len(text)
        for idx, item in enumerate(items):
            if idx + 1 < len(items):
                item.end_char = items[idx + 1].start_char
            else:
                item.end_char = terminal
        return items

    def _expected_items(self, metadata: FilingMetadata) -> set[str]:
        expected = set(ITEM_NUMBERS)
        if not metadata.has_item_1c:
            expected.discard("1C")
        return expected

    def _parse_page_num(self, text: str) -> int | None:
        normalized = self._normalize_ws(text)
        return int(normalized) if PAGE_NUMBER_PATTERN.match(normalized) else None

    def _normalize_ws(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _row_explicitly_has_no_target(self, row_text: str) -> bool:
        return bool(NO_TARGET_ROW_PATTERN.search(row_text))

    def _breaks_item_carry(self, row_text: str) -> bool:
        return bool(ROW_CARRY_BREAK_PATTERN.match(row_text))

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

            if text[pos:pos + len(SIGNATURES_LABEL)].lower() == SIGNATURES_LABEL:
                pos += len(SIGNATURES_LABEL)
                continue

            table_match = TABLE_SNIPPET_PATTERN.match(text, pos)
            if table_match:
                if skip_leading_table or self._is_navigation_table(table_match.group(0)):
                    pos = table_match.end()
                    skip_leading_table = False
                    continue

            break

        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def _score_link(
        self,
        item_number: str,
        link: TocLink,
        frag_to_pos: dict[str, int],
        text: str,
    ) -> int:
        start = frag_to_pos[link.fragment_id]
        preview_html = text[start:start + 1500]
        preview_plain = self._preview_plain_text(preview_html)
        leading_plain = preview_plain[:220]
        title_terms = self._title_terms(item_number)
        hint_phrases = ITEM_HINT_PHRASES.get(item_number, ())
        score = 0

        if leading_plain.startswith(TOC_LABEL):
            score -= 12
        elif TOC_LABEL in leading_plain:
            score -= 8

        if leading_plain.startswith(SIGNATURES_LABEL):
            score -= 15
        elif SIGNATURES_LABEL in leading_plain:
            score -= 10

        if preview_html.lstrip().lower().startswith("<table"):
            score -= 4

        for phrase in hint_phrases:
            if phrase in preview_plain:
                score += 10
                if phrase in leading_plain:
                    score += 4

        matched_terms = 0
        for term in title_terms:
            if term in preview_plain:
                matched_terms += 1
                score += 3
                if term in leading_plain:
                    score += 1

        if matched_terms == 0 and not any(phrase in preview_plain for phrase in hint_phrases):
            score -= 3

        if link.page_num is not None:
            score -= min(link.page_num // 40, 3)

        return score

    def _title_terms(self, item_number: str) -> tuple[str, ...]:
        _, title = ITEM_META[item_number]
        terms: list[str] = []

        for term in re.findall(r"[a-z0-9]+", title.lower()):
            if len(term) <= 3:
                continue
            if term in TITLE_STOPWORDS:
                continue
            terms.append(term)

        return tuple(dict.fromkeys(terms))

    def _preview_plain_text(self, preview_html: str) -> str:
        preview_no_markers = ANCHOR_MARKER_PATTERN.sub(" ", preview_html)
        preview_no_tags = HTML_TAG_PATTERN.sub(" ", preview_no_markers)
        return self._normalize_ws(preview_no_tags).lower()

    def _is_navigation_table(self, table_html: str) -> bool:
        plain = self._preview_plain_text(table_html)[:220]
        return TOC_LABEL in plain or plain.startswith(SIGNATURES_LABEL)
