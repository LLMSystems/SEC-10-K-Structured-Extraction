"""
TocAnchorParser
處理正文沒有 "Item X." 節標題，但 TOC 有 item-to-anchor 對應的 10-K。

典型格式（Morgan Stanley、Intel 等）：
  TOC 表格：[Title(link) | Part? | Item# | Page(link)]
  正文：[[ANCHOR:fragment_id]] 標記已由 preprocessor 注入

流程：
  1. 掃描 HTML TOC 表格 → {item_number: fragment_id}
  2. 掃描 text [[ANCHOR:...]] 標記 → {fragment_id: char_position}
  3. 建立 RawItem 列表（start = anchor pos，end = 下一個 item start）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.models import FilingMetadata, RawItem, RawSpan, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult
from src.patterns import ITEM_META, ITEM_NUMBERS

ANCHOR_MARKER_RE = re.compile(r"\[\[ANCHOR:([^\]]+)\]\]")

# TOC header row 的識別：必須含 "Item" 欄 + "Page" 欄
TOC_HEADER_ITEM_RE  = re.compile(r"\bitem\b", re.IGNORECASE)
TOC_HEADER_PAGE_RE  = re.compile(r"\bpage\b",  re.IGNORECASE)

# 合法的 Item 編號（與 ITEM_NUMBERS 同步）
_ITEM_NUM_SET = set(ITEM_NUMBERS)
ITEM_NUM_RE = re.compile(
    r"^(1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16)$",
    re.IGNORECASE,
)

NONE_RE     = re.compile(r"\b(?:none|not\s+applicable|n/?a)\b",    re.IGNORECASE)
RESERVED_RE = re.compile(r"\breserved\b",                           re.IGNORECASE)
BY_REF_RE   = re.compile(
    r"incorporat(?:ed|ion)\s+(?:herein\s+)?by\s+reference|proxy\s+statement",
    re.IGNORECASE,
)

# TOC 表格最少要有幾個含 item-number 的 row 才算是真正的 TOC（排除頁眉小表格）
_TOC_MIN_ITEM_ROWS = 3


@dataclass
class _TocEntry:
    item_number: str
    title_text: str
    fragment_id: str          # 從 <a href="#..."> 取得
    status_hint: str | None = None
    by_reference_text: str | None = None


class TocAnchorParser(BaseParser):
    """
    適用於正文無 "Item X." 節標題、靠 TOC anchor href 定位的 10-K。
    """

    @property
    def name(self) -> str:
        return "toc_anchor"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        html   = doc.normalized_html or ""
        text   = doc.text
        warnings: list[str] = []

        # 1. 建立 fragment → text offset 對應（已由 preprocessor 注入 [[ANCHOR:...]]）
        frag_to_pos = {
            m.group(1): m.end()
            for m in ANCHOR_MARKER_RE.finditer(text)
        }
        if not frag_to_pos:
            return self._make_result([], ["No ANCHOR markers in text; not a TOC-anchor style filing"])

        # 2. 解析 TOC 表格，取得 {item_number: _TocEntry}
        entries = self._parse_toc_tables(html, frag_to_pos)
        if not entries:
            return self._make_result([], ["No TOC tables with item-anchor mapping found"])

        if len(entries) < 3:
            warnings.append(f"Only {len(entries)} items found in TOC; may be incomplete")

        # 3. 建立 RawItem 列表
        raw_items = self._build_raw_items(entries, frag_to_pos, text, metadata)
        return self._make_result(raw_items, warnings)

    # ── TOC 表格解析 ──────────────────────────────────────────────

    def _parse_toc_tables(
        self,
        html: str,
        frag_to_pos: dict[str, int],
    ) -> dict[str, _TocEntry]:
        """掃描 HTML 中所有 TOC 表格，合併成 {item_number: entry}。"""
        soup = BeautifulSoup(html, "html.parser")
        entries: dict[str, _TocEntry] = {}

        for table in soup.find_all("table"):
            if not self._is_toc_table(table):
                continue
            for entry in self._parse_single_toc_table(table, frag_to_pos):
                new_pos = frag_to_pos.get(entry.fragment_id, -1)
                existing = entries.get(entry.item_number)
                old_pos = frag_to_pos.get(existing.fragment_id, -1) if existing else -1
                # 有 anchor 的 entry 以位置比較決定；沒有 anchor 的 by-reference entry
                # 只在尚無任何 entry 時才加入（有直接 anchor 的 entry 優先）
                if new_pos > old_pos or (new_pos < 0 and entry.status_hint and old_pos < 0):
                    entries[entry.item_number] = entry

        return entries

    def _is_toc_table(self, table) -> bool:
        """
        判斷是否為 10-K TOC 表格：
        - header row 同時含 "Item" 欄和 "Page" 欄
        - 且有足夠多含 item number 的資料 row
        """
        rows = table.find_all("tr")
        if len(rows) < _TOC_MIN_ITEM_ROWS + 1:
            return False

        # 前三 row 找 header
        has_header = False
        for row in rows[:3]:
            cells = self._row_cell_texts(row)
            flat  = " ".join(cells)
            if TOC_HEADER_ITEM_RE.search(flat) and TOC_HEADER_PAGE_RE.search(flat):
                has_header = True
                break
        if not has_header:
            return False

        # 計算含 item number 的 row 數
        item_row_count = 0
        for row in rows:
            cells = self._row_cell_texts(row)
            if any(ITEM_NUM_RE.fullmatch(c) for c in cells):
                item_row_count += 1
        return item_row_count >= _TOC_MIN_ITEM_ROWS

    def _parse_single_toc_table(
        self,
        table,
        frag_to_pos: dict[str, int],
    ) -> list[_TocEntry]:
        """解析單一 TOC 表格，回傳所有 _TocEntry。"""
        entries: list[_TocEntry] = []

        for row in table.find_all("tr"):
            cells = self._row_cell_texts(row)
            if not cells:
                continue

            # 找 item number 所在欄
            item_num = None
            for cell in cells:
                m = ITEM_NUM_RE.fullmatch(cell.strip())
                if m:
                    item_num = m.group(1).upper()
                    break
            if item_num not in _ITEM_NUM_SET:
                continue

            # 取第一個有 href="#..." 的 <a> 作為 fragment
            fragment_id = ""
            title_text  = ITEM_META.get(item_num, ("", item_num))[1]
            for a in row.find_all("a", href=True):
                href = a.get("href", "")
                if "#" in href:
                    frag = href.split("#", 1)[1]
                    if frag in frag_to_pos:
                        fragment_id = frag
                        t = self._normalize_ws(a.get_text(" ", strip=True))
                        if t:
                            title_text = t
                        break

            # status hint 偵測（從整列文字判斷）
            row_text = self._normalize_ws(" ".join(cells))
            status_hint: str | None = None
            by_ref_text: str | None = None

            if RESERVED_RE.search(row_text) and not re.search(r"\d", row_text):
                status_hint = "reserved_declared"
            elif NONE_RE.search(row_text) and not re.search(r"\d", row_text):
                status_hint = "none_declared"
            elif BY_REF_RE.search(row_text):
                status_hint = "by_reference_declared"
                by_ref_text = row_text

            if not fragment_id:
                # TOC row 有 item 編號但無直接 anchor 連結（例如以 [b] 腳注代替）。
                # 若有 by-reference 或 reserved/none 指標，建立無位置的狀態 entry，
                # 讓 HybridParser 的 gap-fill 能補上這個 item（以 by_reference 狀態）。
                footnote_marker = bool(re.search(r'\[[a-z]\]', row_text))
                if status_hint or footnote_marker:
                    entries.append(_TocEntry(
                        item_number=item_num,
                        title_text=title_text,
                        fragment_id="",
                        status_hint=status_hint or "by_reference_declared",
                        by_reference_text=by_ref_text,
                    ))
                continue

            entries.append(_TocEntry(
                item_number=item_num,
                title_text=title_text,
                fragment_id=fragment_id,
                status_hint=status_hint,
                by_reference_text=by_ref_text,
            ))

        return entries

    # ── RawItem 建立 ──────────────────────────────────────────────

    def _build_raw_items(
        self,
        entries: dict[str, _TocEntry],
        frag_to_pos: dict[str, int],
        text: str,
        metadata: FilingMetadata,
    ) -> list[RawItem]:
        ordered = [num for num in ITEM_NUMBERS if num in entries]
        if not metadata.has_item_1c and "1C" in ordered:
            ordered.remove("1C")

        # Build position map (only items with valid anchors)
        pos_map: dict[str, int] = {}
        for num in ordered:
            pos = frag_to_pos.get(entries[num].fragment_id, -1)
            if pos >= 0:
                pos_map[num] = pos

        # Pre-compute end_chars using physical text order → guarantees no overlap.
        # When multiple items share the same anchor (e.g. "Items 10-14." pointing to one
        # fragment), skip same-position entries and find the next DISTINCT position so all
        # co-anchored items get the full content range rather than a zero-length span.
        text_sorted = sorted(pos_map.items(), key=lambda x: x[1])
        text_order_ends: dict[str, int] = {}
        for i, (n, pos) in enumerate(text_sorted):
            end = len(text)
            for j in range(i + 1, len(text_sorted)):
                if text_sorted[j][1] != pos:
                    end = text_sorted[j][1]
                    break
            text_order_ends[n] = end

        raw_items: list[RawItem] = []
        for num in ordered:
            entry = entries[num]
            start = pos_map.get(num)

            if start is None:
                # 無 anchor 但有 status_hint（如 by_reference_declared）→
                # 建立無內容範圍的虛擬 RawItem，讓 postprocessor 轉為 incorporated_by_reference
                if entry.status_hint:
                    raw_items.append(RawItem(
                        item_number=num,
                        title_text=entry.title_text,
                        start_char=0,
                        end_char=None,  # None → _geometry 回傳 [] 跳過 range/ordering 檢查
                        status_hint=entry.status_hint,
                        by_reference_text=entry.by_reference_text,
                        confidence=0.85,
                        spans=[],  # 空 spans → postprocessor 走 by_reference 路徑
                    ))
                continue

            confidence = 0.82 if entry.status_hint else 0.78

            end = text_order_ends[num]
            raw_items.append(RawItem(
                item_number=num,
                title_text=entry.title_text,
                start_char=start,
                end_char=end,
                status_hint=entry.status_hint,
                by_reference_text=entry.by_reference_text,
                confidence=confidence,
                # Single span marks this parser as non-linear so Rule 2 ordering
                # violations are downgraded to info (TOC-anchor filings intentionally
                # place items like 5, 6 after 7-9 in the document body).
                spans=[RawSpan(start_char=start, end_char=end)],
            ))

        return raw_items

    # ── 工具 ──────────────────────────────────────────────────────

    def _row_cell_texts(self, row) -> list[str]:
        texts = [
            self._normalize_ws(cell.get_text(" ", strip=True).replace("\xa0", " "))
            for cell in row.find_all(["td", "th"])
        ]
        return [t for t in texts if t]

    def _normalize_ws(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
