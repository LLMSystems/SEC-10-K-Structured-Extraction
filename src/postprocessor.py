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
    FIN_STMT_BY_REF_PATTERN,
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
                spans=self._item_spans(raw),
                status="incorporated_by_reference",
            )

        # 剝掉 HTML tag 後的純文字版本，用於 pattern 偵測與長度判斷。
        # content_text 仍保留原始（含 HTML）以便 Markdown 正確渲染表格。
        plain = _strip_html(stripped)

        # 0. 內容實質為空（去 HTML 後沒有任何文字）→ 不適用（如 Item 16 留白）
        if not plain.strip():
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="not_applicable",
            )

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
                spans=self._item_spans(raw),
                status="incorporated_by_reference",
            )

        # 1b. Item 8 財報以「見 F-pages / 另頁」方式呈現：內容只是一段短指標
        #     （如 "See Index to Consolidated Financial Statements"、"appear on
        #     pages 162-314"），實際報表置於文件他處。內容夠長時代表報表就在本段，
        #     不應誤判，故加長度上限。
        if num == "8" and len(plain) < 1000 and self._looks_like_financial_by_reference(plain):
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=stripped,
                char_range=self._item_char_range(raw),
                spans=self._item_spans(raw),
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
        #    標題可能佔多行，且文件用的標題未必等於標準標題（例 Item 16 用
        #    "SUMMARY" 而非 "Form 10-K Summary"），因此不依賴標題比對：
        #        ITEM 16.
        #        SUMMARY
        #        None
        #    判定條件（內容夠短的前提下）：
        #      (a) 任一行「整行就是」N/A token（None / N/A / Not Applicable），或
        #      (b) 跳過編號行後的第一行內容本身是 N/A 語句（如 "Not applicable to ..."）。
        plain_body = plain.split("\n", 1)[-1].strip()       # 給 Mine Safety 用
        if self._looks_not_applicable(plain, num, std_title):
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
        # Guard: if stripped content is very short after all pattern checks fail,
        # the parser likely only found a TOC entry, not real content → missing.
        if len(plain) < 50:
            return ItemResult(
                part=part,
                item_number=num,
                item_title=std_title,
                content_text=None,
                char_range=None,
                status="missing",
            )
        return ItemResult(
            part=part,
            item_number=num,
            item_title=std_title,
            content_text=stripped,
            char_range=self._item_char_range(raw),
            spans=self._item_spans(raw),
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

    # 「整行就是」不適用宣告的 token（容許結尾標點）
    _NA_TOKENS = {"none", "n/a", "na", "not applicable"}

    def _is_na_token(self, line: str) -> bool:
        norm = self._normalize_ws(line).strip(" .;:").lower()
        return norm in self._NA_TOKENS

    # 實質長句的字數門檻：超過此長度且非標題/非 NA 宣告的行，視為「有實質內容」
    _SUBSTANTIVE_LEN = 80

    def _looks_not_applicable(self, plain: str, num: str, std_title: str) -> bool:
        """
        判斷「去 HTML 後的內容」是否實質上只是不適用宣告（None / N/A /
        Not Applicable）。為 _classify 與外部（如 GT 標註正規化腳本）共用的
        單一事實來源，確保 parser 與 ground truth 用同一套 NA 判斷。

        SEC 文件的標題很髒（換行、斷字、標點、大小寫不一），靠「精準跳過標題」
        太脆弱，故改用：

          內容夠短（< 500 字）
          且 有 N/A 宣告（任一行是 None/N/A token，或短行含 N/A 語句）
          且 扣掉標題行與 N/A 行後，沒有任何「實質長句」（>= 80 字的非標題、
             非 N/A 行）

        最後一條用來區分像 Item 15 那種「多段內容裡夾了一行 None.（指財報附表）」
        的情況——它有一句實質長句（"The responses ... set forth under Item 8
        above"），因此不算整個 Item 不適用。標題以「子字串模糊比對」忽略，
        容忍換行/標點/斷字。
        """
        lines = self._lines_after_item_heading(plain, num)
        body = "\n".join(lines)
        if not body or len(body) >= 500:
            return False

        # 合併成單行（折疊換行），讓被斷行的 N/A 片語（"Not\napplicable."）也能命中。
        body_joined = self._normalize_ws(" ".join(lines))
        has_na = (
            any(self._is_na_token(l) for l in lines)
            or any(len(l) < 100 and NOT_APPLICABLE_PATTERN.search(l) is not None for l in lines)
            or (len(body_joined) < 120 and NOT_APPLICABLE_PATTERN.search(body_joined) is not None)
        )
        if not has_na:
            return False

        title_norm = self._normalize_ws(std_title).lower()
        substantive_chars = 0
        for line in lines:
            norm = self._normalize_ws(line).lower()
            if self._is_na_token(line) or NOT_APPLICABLE_PATTERN.search(norm):
                continue                                  # N/A 宣告行
            if title_norm and (norm in title_norm or title_norm in norm):
                continue                                  # 標題（含換行/標點變體）
            substantive_chars += len(norm)
            # Single long line OR accumulated short lines both signal real content.
            # Preprocessors sometimes break inline links across lines, producing
            # many sub-80-char fragments that together form a substantive sentence.
            if len(norm) >= self._SUBSTANTIVE_LEN or substantive_chars >= self._SUBSTANTIVE_LEN:
                return False                              # 有實質內容 → 不是純 NA
        return True

    def _looks_like_financial_by_reference(self, plain: str) -> bool:
        """Item 8 財報以「見另頁 / F-pages」方式呈現（內容只是指標而非實際報表）。
        與 _classify 共用，確保 parser 與 ground truth 用同一套判斷。"""
        body = self._normalize_ws(plain)
        return FIN_STMT_BY_REF_PATTERN.search(body) is not None

    def _lines_after_item_heading(self, plain: str, num: str) -> list[str]:
        """只跳過開頭連續的「ITEM N.」編號行，回傳其後的非空白行（保留標題行，
        由 _looks_not_applicable 自行以子字串比對處理）。"""
        item_heading_re = re.compile(rf"^item\s+{re.escape(num)}\b", re.IGNORECASE)
        lines = [l.strip() for l in plain.splitlines() if l.strip()]
        idx = 0
        while idx < len(lines) and item_heading_re.match(self._normalize_ws(lines[idx]).lower()):
            idx += 1
        return lines[idx:]

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

    def _item_spans(self, raw: RawItem) -> list[tuple[int, int]]:
        if raw.spans:
            return [(s.start_char, s.end_char) for s in sorted(raw.spans, key=lambda s: s.start_char)]
        if raw.end_char is not None:
            return [(raw.start_char, raw.end_char)]
        return []
