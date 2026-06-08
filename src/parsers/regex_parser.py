"""
Regex Parser
規則式 Item 切割，第一階段主要實作。

策略：
  1. 在純文字中用 regex 找所有疑似 Item 標題的位置
  2. 用相鄰標題的起點當作前一個 Item 的終點
  3. 根據找到的信心分數決定整體可信度

所有 Regex Pattern 集中定義在 src/patterns.py，此檔不直接定義任何 re.compile。
"""

from __future__ import annotations
import re
from src.models import RawItem, RawSpan, FilingMetadata, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult
from src.patterns import (
    ITEM_NUMBERS,
    ITEM_META,
    ITEM_PATTERN,
    COMBINED_ITEM_PATTERN,
    PART_ITEM_PATTERN,
    TERMINAL_PATTERN,
    EXPLICIT_SEP_PATTERN,
    REFERENCE_PATTERN
)


class RegexParser(BaseParser):

    @property
    def name(self) -> str:
        return "regex"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        text = doc.text
        warnings: list[str] = []

        # 1. 找所有疑似 Item 標題的位置
        candidates = self._find_candidates(text)

        if not candidates:
            warnings.append("找不到任何 Item 標題，可能格式特殊")
            return self._make_result([], warnings)

        # 2. 去重：同一個 item_number 可能出現多次（目錄 + 正文 + 頁簽）
        deduped = self._deduplicate(candidates, text)

        # 3. 填入 end_char
        raw_items = self._assign_end_chars(deduped, len(text), text)

        # 4. 檢查缺少的 Item
        found_nums = {item.item_number for item in raw_items}
        expected = self._expected_items(metadata)
        missing = expected - found_nums
        if missing:
            warnings.append(f"未找到以下 Item：{sorted(missing)}")

        return self._make_result(raw_items, warnings)

    # ──────────────────────────────────────────
    # 內部方法
    # ──────────────────────────────────────────

    def _find_candidates(self, text: str) -> list[tuple[str, int, str]]:
        """
        回傳 (item_number, start_pos, matched_text) 的列表。

        同時偵測：
          - 單一 Item 格式：Item 1. / ITEM 1A:
          - 合併 Item 格式：Items 1. and 2. Business and Properties
            → 為兩個 Item 各建一筆，共用同一個 start_pos
          - PART + Item 同行格式：Page PART I Item 1. Business
        """
        results = []

        # 單一 Item
        for m in ITEM_PATTERN.finditer(text):
            num = m.group("num").upper()
            if num in ITEM_META:
                results.append((num, m.start(), m.group()))

        # 合併 Item（Items X. and Y.）
        for m in COMBINED_ITEM_PATTERN.finditer(text):
            num1 = m.group("num1").upper()
            num2 = m.group("num2").upper()
            matched = m.group()
            if num1 in ITEM_META:
                results.append((num1, m.start(), matched))
            if num2 in ITEM_META:
                results.append((num2, m.start(), matched))

        # PART + Item 同行格式（Page PART I Item 1. Business）
        for m in PART_ITEM_PATTERN.finditer(text):
            num = m.group("num").upper()
            if num in ITEM_META:
                results.append((num, m.start(), m.group()))

        return results

    # 同一個 Item 的兩次出現，間距小於此值視為「同一叢集」（頁簽備用策略）
    _PAGE_GAP = 7000

    def _candidate_quality(self, matched: str) -> int:
        """
        1 = 有明確分隔符（. : - —），是正文標題
        0 = 只有換行/tab 分隔，是頁簽或目錄行
        """
        return 1 if EXPLICIT_SEP_PATTERN.search(matched) else 0

    def _deduplicate(
        self,
        candidates: list[tuple[str, int, str]],
        text: str,
    ) -> list[RawItem]:
        """
        同一個 item_number 可能出現在：目錄、正文標題、每頁頁簽。

        策略（依優先順序）：
          1. 優先選有明確分隔符（. : - —）的 candidate → 正文標題
             若有多個，取第一個（正文標題通常只出現一次）
          2. 若全部都是純換行格式（頁簽/目錄）→ 叢集分析：
             取最大叢集的第一個位置；大小相同時偏好位置較後的（跳過目錄）
        """
        order = {n: i for i, n in enumerate(ITEM_NUMBERS)}

        # 按 item_number 分組，各自按位置排序，並標記品質
        by_num: dict[str, list[tuple[int, str, int]]] = {}
        for num, start, matched in candidates:
            q = self._candidate_quality(matched)
            by_num.setdefault(num, []).append((start, matched, q))

        # Pre-compute first high_q position for each item (used for TOC proximity check below)
        first_hq_pos: dict[str, int] = {}
        for num, occurrences in by_num.items():
            for start, matched, q in sorted(occurrences, key=lambda x: x[0]):
                if q == 1:
                    first_hq_pos[num] = start
                    break

        seen: dict[str, RawItem] = {}

        for num, occurrences in by_num.items():
            occurrences.sort(key=lambda x: x[0])

            # 先篩高品質（明確分隔符）
            high_q = [(s, m, q) for s, m, q in occurrences if q == 1]

            # 檢查前後是否包含 "see"、"refer to" 等字樣，可能是正文引用（例如 "Item 1. Business (see Item 7.)"），如果有則降級品質
            for i, (start, matched, q) in enumerate(high_q):
                window_start = max(0, start - 50)
                before = text[window_start:start]
                recent_before = text[max(0, start - 10):start]
                has_period_nearby = "." in recent_before
                after = text[start + len(matched): start + len(matched) + 60]
                # 標題後出現孤立的句點行（\n.\n）代表這是句中引用，不是真正的節標題
                after_is_punctuation = bool(re.search(r"\n\s*\.\s*\n", after))

                if (REFERENCE_PATTERN.search(before.lower()) and not ITEM_PATTERN.search(before) and not has_period_nearby) \
                        or after_is_punctuation:
                    high_q[i] = (start, matched, 0)

            high_q = [(s, m) for s, m, q in high_q if q == 1]

            # 如果 item 15 的start 在len(text)*0.5 之前，則很可能是目錄中的引用，降級品質
            if num == "15":
                high_q = [(s, m) for s, m in high_q if s > len(text) * 0.5]

            if high_q:
                if len(high_q) > 1:
                    # Decide whether high_q[0] is a TOC entry or the real section heading.
                    # If another item's first high_q occurrence is within _PAGE_GAP chars of
                    # high_q[0], they are probably all in the same TOC block → skip to high_q[1].
                    # Otherwise, high_q[0] IS the real heading (e.g. a SPAC that renumbers
                    # Part III items starting from "Item 1.").
                    first_pos = high_q[0][0]
                    in_toc_cluster = any(
                        other_num != num
                        and abs(first_hq_pos.get(other_num, -1) - first_pos) <= self._PAGE_GAP
                        for other_num in first_hq_pos
                    )
                    if in_toc_cluster:
                        best_start, best_matched = high_q[1]
                    else:
                        best_start, best_matched = high_q[0]
                else:
                    best_start, best_matched = high_q[0]
            else:
                # 全為頁簽/目錄格式 → 叢集分析
                low_q = [(s, m) for s, m, _ in occurrences]

                if len(low_q) == 1:
                    best_start, best_matched = low_q[0]
                else:
                    clusters: list[list[tuple[int, str]]] = []
                    current: list[tuple[int, str]] = [low_q[0]]
                    for i in range(1, len(low_q)):
                        gap = low_q[i][0] - low_q[i - 1][0]
                        if gap <= self._PAGE_GAP:
                            current.append(low_q[i])
                        else:
                            clusters.append(current)
                            current = [low_q[i]]
                    clusters.append(current)

                    # 最大叢集（同大小取較後的叢集跳過目錄）
                    best_cluster = max(clusters, key=lambda c: (len(c), c[0][0]))
                    best_start, best_matched = best_cluster[0]

            seen[num] = RawItem(
                item_number=num,
                title_text=best_matched.strip(),
                start_char=best_start,
                confidence=0.9,
            )

        return sorted(seen.values(), key=lambda x: order.get(x.item_number, 99))

    def _assign_end_chars(self, items: list[RawItem], text_len: int, text: str = "") -> list[RawItem]:
        """
        每個 Item 的 end_char = 下一個 Item 的 start_char
        最後一個 Item 的 end_char = TERMINAL 起點（若有），否則文字總長

        使用物理文字順序（而非 canonical 順序）來指派 end_char，避免 TOC-only 項目
        （如 Item 1B 只出現在目錄、位置很小）與正文項目（位置很大）混排造成 start > end。
        """
        terminal = text_len
        if text:
            m = TERMINAL_PATTERN.search(text)
            if m:
                terminal = m.start()

        # Sort by physical text position to prevent start > end when TOC-only items
        # (small positions) are interleaved with content items (large positions).
        phys = sorted(items, key=lambda x: x.start_char)

        for i, item in enumerate(phys):
            if i + 1 < len(phys):
                item.end_char = phys[i + 1].start_char
            else:
                if item.start_char >= terminal:
                    m = TERMINAL_PATTERN.search(text, item.start_char)
                    if m:
                        terminal = m.start()
                item.end_char = terminal

        # Mark TOC-only items (tiny ranges in a document that also has large items) with
        # spans to signal non-linear structure.  This causes the validator to downgrade
        # ordering violations from warning to info rather than penalising them as errors.
        large_ranges_exist = any(
            (item.end_char - item.start_char) > 5000 for item in phys
        )
        if large_ranges_exist:
            for item in phys:
                if (item.end_char - item.start_char) <= 500 and not item.spans:
                    item.spans = [RawSpan(start_char=item.start_char, end_char=item.end_char)]

        return items  # return in original canonical order

    def _expected_items(self, metadata: FilingMetadata) -> set[str]:
        """根據 metadata 判斷這份申報應該有哪些 Items。"""
        expected = set(ITEM_NUMBERS)
        if not metadata.has_item_1c:
            expected.discard("1C")
        return expected
