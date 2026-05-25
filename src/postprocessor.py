"""
Postprocessor
把 parser 的 RawItem 列表轉換成最終的 ItemResult 列表。

負責：
  1. 偵測 incorporated_by_reference
  2. 判斷 reserved（Item 6、Item 16）
  3. 判斷 not_applicable（Item 4 Mine Safety 等）
  4. 補上缺失的 Item（用 reserved/not_applicable 填）
  5. 正規化標題文字
"""

from __future__ import annotations
import re
from src.models import RawItem, ItemResult, FilingMetadata
from src.patterns import (
    ITEM_META,
    ITEM_NUMBERS,
    HTML_TAG_PATTERN,
    BY_REF_PATTERN,
    NOT_APPLICABLE_PATTERN,
    RESERVED_PATTERN,
    MINE_SAFETY_NA_PATTERN,
)


def _strip_html(text: str) -> str:
    """移除 HTML 標籤，只保留純文字，供 pattern 偵測用。"""
    return HTML_TAG_PATTERN.sub(" ", text)


ANCHOR_MARKER_PATTERN = re.compile(r"\[\[ANCHOR:[^\]]+\]\]")
PAGE_MARKER_PATTERN = re.compile(r"\[\[PAGE:\d+\]\]")


PART_III_BY_REF_ITEMS = {"10", "11", "12", "13", "14"}
PART_III_BY_REF_DECL_PATTERN = re.compile(
    r"documents\s+incorporated\s+by\s+reference.{0,1500}?part\s+iii",
    re.IGNORECASE | re.DOTALL,
)
PROXY_REFERENCE_PATTERN = re.compile(
    r"proxy\s+statement|annual\s+stockholders'?\s+meeting",
    re.IGNORECASE,
)
ITEM_16_SIGNATURE_PATTERN = re.compile(
    r"pursuant\s+to\s+the\s+requirements\s+of\s+section\s+13\s+or\s+15\(d\)|"
    r"\bregistrant\b.{0,80}\bby:\b|"
    r"\bsignatures?\b",
    re.IGNORECASE | re.DOTALL,
)


class PostProcessor:

    def process(
        self,
        raw_items: list[RawItem],
        full_text: str,
        metadata: FilingMetadata,
    ) -> list[ItemResult]:
        """
        主入口：把 RawItem 列表轉成 ItemResult 列表。
        """
        # 建立 item_number → RawItem 的 map
        raw_map: dict[str, RawItem] = {item.item_number: item for item in raw_items}
        part_iii_by_ref_excerpt = self._find_part_iii_by_reference_excerpt(full_text)

        results: list[ItemResult] = []

        for num in ITEM_NUMBERS:
            # 跳過不應存在的 Item
            if num == "1C" and not metadata.has_item_1c:
                continue

            part, std_title = ITEM_META[num]

            if num not in raw_map:
                # Parser 沒找到這個 Item
                result = self._handle_missing(
                    num,
                    part,
                    std_title,
                    metadata,
                    part_iii_by_ref_excerpt,
                )
            else:
                raw = raw_map[num]
                content = self._extract_content(raw, full_text)
                result = self._classify(num, part, std_title, content, raw, metadata)

            results.append(result)

        return results

    # ──────────────────────────────────────────
    # 內部方法
    # ──────────────────────────────────────────

    def _extract_content(self, raw: RawItem, full_text: str) -> str:
        """從 full_text 切出這個 Item 的內容文字"""
        if raw.status_hint and not raw.spans and raw.end_char is None:
            return ""

        if raw.spans:
            parts: list[str] = []
            for span in sorted(raw.spans, key=lambda s: s.start_char):
                snippet = self._remove_internal_markers(
                    full_text[span.start_char:span.end_char]
                ).strip()
                if snippet:
                    parts.append(snippet)
            return "\n\n".join(parts).strip()

        end = raw.end_char if raw.end_char is not None else len(full_text)
        return self._remove_internal_markers(full_text[raw.start_char:end]).strip()

    def _find_part_iii_by_reference_excerpt(self, full_text: str) -> str | None:
        plain = self._normalize_ws(_strip_html(full_text))
        if not plain:
            return None

        search_space = plain[:25000]
        match = PART_III_BY_REF_DECL_PATTERN.search(search_space)
        if not match:
            return None

        snippet = search_space[match.start(): min(len(search_space), match.end() + 400)]
        if not BY_REF_PATTERN.search(snippet):
            return None
        if not PROXY_REFERENCE_PATTERN.search(snippet):
            return None

        return snippet.strip()

    def _classify(
        self,
        num: str,
        part: str,
        std_title: str,
        content: str,
        raw: RawItem,
        metadata: FilingMetadata,
    ) -> ItemResult:
        """判斷這個 Item 的 status"""

        stripped = content.strip()

        if raw.status_hint == "reserved_declared":
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="reserved",
            )

        if raw.status_hint == "none_declared":
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

        if raw.status_hint == "by_reference_declared":
            if not raw.spans:
                return ItemResult(
                    part=part,
                    item_number=num,
                    item_title=std_title,
                    content_text=raw.by_reference_text or None,
                    char_range=None,
                    status="incorporated_by_reference",
                )
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=stripped or None,
                char_range=self._item_char_range(raw),
                status="incorporated_by_reference",
            )

        # 剝掉 HTML tag 後的純文字版本，用於 pattern 偵測與長度判斷。
        # content_text 仍保留原始（含 HTML）以便 Markdown 正確渲染表格。
        plain = _strip_html(stripped)

        # 1. 偵測 incorporated_by_reference（只對 Part III,item 8 檢查）
        #    其他 Part 的 Item 內文也可能出現此字樣（例如引用 Exhibit），
        #    但不代表整個 Item 是空的，不應標記為此 status。
        PART_III_ITEMS = {"8", "10", "11", "12", "13", "14"}
        if num in PART_III_ITEMS and BY_REF_PATTERN.search(plain):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=stripped,
                char_range=self._item_char_range(raw),
                status="incorporated_by_reference",
            )

        if num == "6" and metadata.item_6_is_reserved:
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="reserved",
            )

        # 3. 內容只有 "Reserved" 字樣（用 plain 量長度，避免 HTML 膨脹誤判）
        if len(plain) < 200 and RESERVED_PATTERN.search(plain):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="reserved",
            )

        # 4. 內容實質上只有 "Not Applicable" / "N/A" / "None"
        #    跳過第一行（Item 標題），只看第一行實際內容，
        #    避免後面跟著 Glossary / Table of Contents 等章節導致誤判長度
        plain_body = plain.split("\n", 1)[-1].strip()  # 跳過第一行（Item 標題）
        first_content_line = next(
            (l.strip() for l in plain_body.splitlines() if l.strip()), ""
        )
        if len(first_content_line) < 100 and len(plain_body) <500 and NOT_APPLICABLE_PATTERN.search(first_content_line):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

        # 5. Item 4 Mine Safety：短內容且有 N/A 關鍵字（同樣跳過第一行標題）
        if num == "4" and len(plain_body) < 400 and MINE_SAFETY_NA_PATTERN.search(plain_body):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

        if num == "16" and ITEM_16_SIGNATURE_PATTERN.search(plain):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

        # 6. 正常抽取（保留含 HTML 的原始 stripped，讓表格在 MD 中可渲染）
        return ItemResult(
            part=part,
            item_number=num,
            item_title=std_title,
            content_text=stripped,
            char_range=self._item_char_range(raw),
            status="extracted",
        )

    def _handle_missing(
        self,
        num: str,
        part: str,
        std_title: str,
        metadata: FilingMetadata,
        part_iii_by_ref_excerpt: str | None = None,
    ) -> ItemResult:
        """Parser 沒找到這個 Item 時，根據規則推斷 status"""

        if num == "6" and metadata.item_6_is_reserved:
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="reserved",
            )

        if num in PART_III_BY_REF_ITEMS and part_iii_by_ref_excerpt:
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=part_iii_by_ref_excerpt,
                char_range=None,
                status="incorporated_by_reference",
            )

        # Item 16（Form 10-K Summary）是選填的，沒有也正常
        if num == "16":
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

        # 其他 Item 找不到：標記 missing，表示 parser 解析失敗
        # 與 not_applicable 的差別：
        #   not_applicable = 公司明確說此 Item 不適用
        #   missing        = parser 找不到，可能是格式特殊或解析錯誤
        # TODO: 之後可以改成觸發 LLM fallback 重試
        return ItemResult(
            part=part,
            item_number=num,
            item_title=std_title,
            content_text=None,
            char_range=None,
            status="missing",
        )

    def _normalize_ws(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _remove_internal_markers(self, text: str) -> str:
        text = ANCHOR_MARKER_PATTERN.sub("", text)
        text = PAGE_MARKER_PATTERN.sub("", text)
        return text

    def _item_char_range(self, raw: RawItem) -> tuple[int, int] | None:
        if raw.spans:
            ordered = sorted(raw.spans, key=lambda span: span.start_char)
            return (ordered[0].start_char, ordered[-1].end_char)
        if raw.end_char is None:
            return None
        return (raw.start_char, raw.end_char)
