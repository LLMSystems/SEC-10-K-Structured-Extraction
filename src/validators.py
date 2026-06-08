"""
Validator
獨立於 parser 的驗證層：重新核對結構不變量，捕捉「parser 說成功、實際卻錯」
的靜默錯誤。輸入 raw_items（乾淨幾何 + spans + confidence）、最終 items
（status 分類）、text、metadata、parse_result，輸出 QualityReport。

設計見 docs（P1 規則集）：
  Rule 0  range 合法性          （raw_items, error）
  Rule 2  順序單調              （raw_items, warning；多 span 路徑降為 info）
  Rule 3' 過大 item            （raw_items, warning）
  Rule 4  必要 item 缺失        （final items, error / warning）
  Rule 5  status↔欄位契約       （final items, error）
  Rule 6  extracted 近乎空      （final items, error）
  Rule 7  文件長度地板          （text, error / warning）
  Rule 8  低信心                （raw_items + parse_result, info）
  Rule 10 char range 重疊       （raw_items, warning）
"""

from __future__ import annotations

import re

from src.models import (FilingMetadata, ItemResult, QualityReport, RawItem,
                        ValidationFlag)
from src.parsers.base import ParseResult
from src.patterns import HTML_TAG_PATTERN, ITEM_NUMBERS

# ── 門檻（依 eval_datasets 的逐 Item 剖面 analysis/item_profile.md 校準）──

# 各 item 的「正常」status 集合（剖面觀察值）。status 不在集合內即為異常。
# 例：Item 3 可為 not_applicable（無訴訟）、Item 8 可為 by_reference（財報見 F-pages）、
#     Part III(10–14) 以 by_reference 為主，missing 屬異常。
ITEM_EXPECTED_STATUS: dict[str, set[str]] = {
    "1":  {"extracted"},
    "1A": {"extracted"},
    "1B": {"not_applicable", "extracted"},
    "1C": {"extracted", "not_applicable", "missing"},   # 2023+ 新規，過渡期較不穩
    "2":  {"extracted", "not_applicable"},      # 部分大型金融控股公司物業非屬重大，可寫 N/A
    "3":  {"extracted", "not_applicable"},
    "4":  {"not_applicable", "extracted", "missing"},
    "5":  {"extracted"},
    "6":  {"reserved", "extracted", "not_applicable", "missing"},
    "7":  {"extracted"},
    "7A": {"extracted", "not_applicable"},              # SRC 常為 N/A
    "8":  {"extracted", "incorporated_by_reference"},
    "9":  {"not_applicable", "extracted", "missing"},
    "9A": {"extracted"},
    "9B": {"not_applicable", "extracted"},
    "9C": {"not_applicable", "missing", "extracted"},
    "10": {"incorporated_by_reference", "extracted"},
    "11": {"incorporated_by_reference", "extracted"},
    "12": {"incorporated_by_reference", "extracted"},
    "13": {"incorporated_by_reference", "extracted"},
    "14": {"incorporated_by_reference", "extracted"},
    "15": {"extracted"},
    "16": {"not_applicable", "extracted"},
}
# status 異常時視為 error 的核心 item（剖面顯示這些必須有實質內容/處理）。
# 1/1A/2/5/7/9A/15 剖面 100% extracted；3 允許 N/A；8 允許 by_reference。
CORE_ITEMS = {"1", "1A", "2", "3", "5", "7", "8", "9A", "15"}

NATURALLY_LARGE_ITEMS = {"1", "1A", "7", "8"}  # 剖面 median 皆 ≥ 40k，過大檢查放寬
OVERSIZED_RATIO = 0.45                         # 單一 item 佔全體可讀跨度比例上限
OVERSIZED_MIN_READABLE = 50_000                # 過大檢查的絕對下限（可讀字元）
OVERSIZED_MIN_ITEMS = 6                         # item 太少時不做過大檢查
EXTRACTED_MIN_READABLE = 50                     # extracted 至少要有的可讀字元
ITEM8_MIN_EXTRACTED_READABLE = 40_000           # Item 8 內嵌財報剖面 p5≈74k，低於此疑似 by_ref 指標
# Item 1A 風險因子長度隨規模放大（剖面：非SRC median 70k vs SRC 41k，1.73×），
# 是唯一資料支撐夠強、值得分層的 item。門檻設在各桶觀測最小值之下避免誤判：
# 非SRC min≈27.5k、SRC min≈30k。大公司要求較高下限，小公司多給餘地。
ITEM1A_MIN_NON_SRC = 25_000                     # 非SRC 的 1A 低於此 → 疑似過短
ITEM1A_MIN_SRC = 20_000                         # SRC 的 1A 低於此 → 疑似過短
DOC_FLOOR_ERROR = 30_000                        # 全文可讀字元低於此 → error（剖面最小全文 >100k）
DOC_FLOOR_WARN = 80_000                         # 低於此 → warning
CONFIDENCE_THRESHOLD = 0.7
ITEM_COVERAGE_WARN = 0.50   # 所有 item 可讀字數加總佔全文比例低於此 → warning
ITEM_COVERAGE_ERROR = 0.25  # 低於此 → error

_ANCHOR_MARKER = re.compile(r"\[\[ANCHOR:[^\]]+\]\]")
_PAGE_MARKER = re.compile(r"\[\[PAGE:\d+\]\]")

_CANONICAL_ORDER = {num: i for i, num in enumerate(ITEM_NUMBERS)}


def _readable_length(text: str) -> int:
    """可讀長度：移除注入標記與 HTML 標籤後的字元數，避免表格 / marker 灌水。"""
    if not text:
        return 0
    cleaned = _ANCHOR_MARKER.sub(" ", text)
    cleaned = _PAGE_MARKER.sub(" ", cleaned)
    cleaned = HTML_TAG_PATTERN.sub(" ", cleaned)
    return len(cleaned.strip())


def _geometry(raw: RawItem) -> list[tuple[int, int]]:
    """取得 RawItem 的字元範圍（統一成 span 清單）。沒有可用幾何時回空 list。"""
    if raw.spans:
        return [(s.start_char, s.end_char) for s in raw.spans]
    if raw.end_char is not None:
        return [(raw.start_char, raw.end_char)]
    return []


class Validator:

    def validate(
        self,
        raw_items: list[RawItem],
        items: list[ItemResult],
        text: str,
        metadata: FilingMetadata,
        parse_result: ParseResult,
    ) -> QualityReport:
        text_len = len(text)
        flags: list[ValidationFlag] = []
        # 多 span 路徑（cross-reference / pdf-style）才會有 spans；regex 不會。
        # 這類文件的 SEC item 物理順序可能合法地不照編號，故順序檢查降為 info。
        has_spans = any(item.spans for item in raw_items)

        flags += self._rule0_range_validity(raw_items, text_len)
        flags += self._rule2_ordering(raw_items, has_spans)
        coverage_ratio = 0.0
        oversized_flags, coverage_ratio = self._rule3_oversized(raw_items, text)
        flags += oversized_flags
        flags += self._rule4_item_status(items, metadata)
        flags += self._rule_item8_financials(items)
        flags += self._rule_1a_undersized(items, metadata)
        flags += self._rule5_status_contract(items)
        flags += self._rule6_extracted_empty(items)
        flags += self._rule7_document_length(text)
        flags += self._rule8_low_confidence(raw_items, parse_result)
        flags += self._rule9_total_coverage(items, text, has_spans)
        flags += self._rule10_char_range_overlap(raw_items, has_spans)

        return self._build_report(
            flags, items, parse_result, metadata, coverage_ratio
        )

    # ── Rule 0：range 合法性 ───────────────────────────────────
    def _rule0_range_validity(
        self, raw_items: list[RawItem], text_len: int
    ) -> list[ValidationFlag]:
        flags: list[ValidationFlag] = []
        for raw in raw_items:
            for start, end in _geometry(raw):
                if not (0 <= start < end <= text_len):
                    flags.append(ValidationFlag(
                        code="range_invalid",
                        severity="error",
                        item_number=raw.item_number,
                        message=(
                            f"Item {raw.item_number} 的 range 不合法："
                            f"start={start}, end={end}, text_len={text_len}"
                            "（很可能是標題定位錯誤或座標被夾成 0）"
                        ),
                        detail={"start": start, "end": end, "text_len": text_len},
                    ))
        return flags

    # ── Rule 2：順序單調 ───────────────────────────────────────
    def _rule2_ordering(
        self, raw_items: list[RawItem], has_spans: bool
    ) -> list[ValidationFlag]:
        positioned: list[tuple[str, int]] = []
        for raw in raw_items:
            geom = _geometry(raw)
            if geom:
                positioned.append((raw.item_number, geom[0][0]))

        positioned.sort(key=lambda x: _CANONICAL_ORDER.get(x[0], 99))

        severity = "info" if has_spans else "warning"
        flags: list[ValidationFlag] = []
        prev_num, prev_pos = None, -1
        for num, pos in positioned:
            if pos < prev_pos:
                flags.append(ValidationFlag(
                    code="ordering_violation",
                    severity=severity,
                    item_number=num,
                    message=(
                        f"Item {num} 的物理位置({pos})排在前一個 "
                        f"Item {prev_num}({prev_pos})之前，違反編號順序"
                    ),
                    detail={"item_pos": pos, "prev_item": prev_num, "prev_pos": prev_pos},
                ))
            else:
                prev_num, prev_pos = num, pos
        return flags

    # ── Rule 3'：過大 item + coverage ──────────────────────────
    def _rule3_oversized(
        self, raw_items: list[RawItem], text: str
    ) -> tuple[list[ValidationFlag], float]:
        lengths: dict[str, int] = {}
        raw_span_total = 0
        envelope_start, envelope_end = None, None

        for raw in raw_items:
            geom = _geometry(raw)
            if not geom:
                continue
            item_readable = sum(_readable_length(text[s:e]) for s, e in geom)
            lengths[raw.item_number] = item_readable
            for s, e in geom:
                raw_span_total += (e - s)
                envelope_start = s if envelope_start is None else min(envelope_start, s)
                envelope_end = e if envelope_end is None else max(envelope_end, e)

        coverage_ratio = 0.0
        if envelope_start is not None and envelope_end and envelope_end > envelope_start:
            coverage_ratio = round(raw_span_total / (envelope_end - envelope_start), 4)

        flags: list[ValidationFlag] = []
        total_readable = sum(lengths.values())
        if total_readable > 0 and len(lengths) >= OVERSIZED_MIN_ITEMS:
            for num, length in lengths.items():
                if num in NATURALLY_LARGE_ITEMS:
                    continue
                ratio = length / total_readable
                if ratio > OVERSIZED_RATIO and length > OVERSIZED_MIN_READABLE:
                    flags.append(ValidationFlag(
                        code="oversized_item",
                        severity="warning",
                        item_number=num,
                        message=(
                            f"Item {num} 佔全體可讀內容 {ratio:.0%}"
                            f"（{length:,} 字），疑似吞併了漏抓的鄰近 Item"
                        ),
                        detail={"readable_len": length, "ratio": round(ratio, 4)},
                    ))
        return flags, coverage_ratio

    @staticmethod
    def _is_src(metadata: FilingMetadata) -> bool:
        """判斷是否可能為 Smaller Reporting Company。

        EDGAR 的 filer_category 有時不完整（例如僅標 "Emerging growth company"
        卻未列 "Smaller reporting company"，但二者實務上高度重疊——會寫
        "Not applicable for smaller reporting companies" 的 EGC 幾乎都是 SRC）。
        故將 EGC 也視為可能的 SRC，避免誤判為 error。
        """
        category_lower = (metadata.filer_category or "").lower()
        return "smaller reporting" in category_lower or "emerging growth" in category_lower

    # ── Rule 4：per-item status 是否符合剖面預期 ────────────────
    def _rule4_item_status(
        self, items: list[ItemResult], metadata: FilingMetadata
    ) -> list[ValidationFlag]:
        """依 ITEM_EXPECTED_STATUS 檢查每個 item 的 status 是否落在「正常」集合。
        核心 item 異常 → error；其餘 → warning。1A 對 SRC 可豁免，降為 warning。"""
        is_src = self._is_src(metadata)
        flags: list[ValidationFlag] = []
        for item in items:
            num, status = item.item_number, item.status
            expected = ITEM_EXPECTED_STATUS.get(num)
            if expected is None or status in expected:
                continue

            if num in CORE_ITEMS:
                # SRC 在法規上可省略 Item 1A，降為 warning
                if num == "1A" and is_src:
                    severity, code = "warning", "recommended_item_missing"
                else:
                    severity, code = "error", "required_item_missing"
                msg = f"核心 Item {num} 的 status 異常（status={status}，預期 {sorted(expected)}）"
            else:
                severity, code = "warning", "unexpected_item_status"
                msg = f"Item {num} 的 status 異常（status={status}，預期 {sorted(expected)}）"

            flags.append(ValidationFlag(
                code=code,
                severity=severity,
                item_number=num,
                message=msg,
                detail={"status": status, "expected": sorted(expected)},
            ))
        return flags

    # ── Rule 4b：Item 8 財報過短（疑似未偵測的 by_reference 指標）──
    def _rule_item8_financials(self, items: list[ItemResult]) -> list[ValidationFlag]:
        flags: list[ValidationFlag] = []
        for item in items:
            if item.item_number != "8" or item.status != "extracted":
                continue
            rl = _readable_length(item.content_text or "")
            if rl < ITEM8_MIN_EXTRACTED_READABLE:
                flags.append(ValidationFlag(
                    code="item8_undersized",
                    severity="warning",
                    item_number="8",
                    message=(
                        f"Item 8 標記 extracted 但可讀內容僅 {rl:,} 字"
                        f"（正常內嵌財報剖面 p5≈74k），疑似未被偵測的 by_reference 指標"
                    ),
                    detail={"readable_len": rl, "expected_min": ITEM8_MIN_EXTRACTED_READABLE},
                ))
        return flags

    # ── Rule 4c：Item 1A 風險因子過短（門檻分 SRC / 非SRC）──────
    def _rule_1a_undersized(
        self, items: list[ItemResult], metadata: FilingMetadata
    ) -> list[ValidationFlag]:
        """1A 是唯一長度明顯隨規模放大的 item，故門檻分兩段：
        非SRC 下限較高（大公司 1A 太短更可疑），SRC 下限較低。"""
        is_src = self._is_src(metadata)
        floor = ITEM1A_MIN_SRC if is_src else ITEM1A_MIN_NON_SRC

        flags: list[ValidationFlag] = []
        for item in items:
            if item.item_number != "1A" or item.status != "extracted":
                continue
            rl = _readable_length(item.content_text or "")
            if rl < floor:
                flags.append(ValidationFlag(
                    code="item1a_undersized",
                    severity="warning",
                    item_number="1A",
                    message=(
                        f"Item 1A 可讀內容僅 {rl:,} 字，低於"
                        f"{'SRC' if is_src else '非SRC'}下限 {floor:,}，疑似被截斷或漏抓"
                    ),
                    detail={"readable_len": rl, "floor": floor, "is_src": is_src},
                ))
        return flags

    # ── Rule 5：status↔欄位契約 ────────────────────────────────
    def _rule5_status_contract(self, items: list[ItemResult]) -> list[ValidationFlag]:
        flags: list[ValidationFlag] = []
        for item in items:
            num, status = item.item_number, item.status
            has_content = bool(item.content_text and item.content_text.strip())
            has_range = item.char_range is not None

            if status == "extracted":
                if not has_content or not has_range:
                    flags.append(ValidationFlag(
                        code="status_field_contract",
                        severity="error",
                        item_number=num,
                        message=(
                            f"Item {num} status=extracted 但缺內容或 range"
                            f"（has_content={has_content}, has_range={has_range}）"
                        ),
                        detail={"status": status, "has_content": has_content, "has_range": has_range},
                    ))
            elif status in ("reserved", "not_applicable", "missing"):
                if has_content or has_range:
                    flags.append(ValidationFlag(
                        code="status_field_contract",
                        severity="error",
                        item_number=num,
                        message=(
                            f"Item {num} status={status} 卻帶有內容或 range"
                            f"（has_content={has_content}, has_range={has_range}）"
                        ),
                        detail={"status": status, "has_content": has_content, "has_range": has_range},
                    ))
            # incorporated_by_reference：契約寬鬆，不檢查
        return flags

    # ── Rule 6：extracted 近乎空 ───────────────────────────────
    def _rule6_extracted_empty(self, items: list[ItemResult]) -> list[ValidationFlag]:
        flags: list[ValidationFlag] = []
        for item in items:
            if item.status != "extracted":
                continue
            readable = _readable_length(item.content_text or "")
            if readable < EXTRACTED_MIN_READABLE:
                flags.append(ValidationFlag(
                    code="extracted_empty",
                    severity="error",
                    item_number=item.item_number,
                    message=(
                        f"Item {item.item_number} status=extracted 但可讀內容僅 "
                        f"{readable} 字，幾乎為空"
                    ),
                    detail={"readable_len": readable},
                ))
        return flags

    # ── Rule 7：文件長度地板 ───────────────────────────────────
    def _rule7_document_length(self, text: str) -> list[ValidationFlag]:
        readable = _readable_length(text)
        if readable < DOC_FLOOR_ERROR:
            return [ValidationFlag(
                code="document_too_short",
                severity="error",
                message=(
                    f"全文可讀長度僅 {readable:,} 字，遠低於正常 10-K，"
                    "很可能抓錯主文件或 preprocess 清掉了內容"
                ),
                detail={"readable_len": readable},
            )]
        if readable < DOC_FLOOR_WARN:
            return [ValidationFlag(
                code="document_short",
                severity="warning",
                message=f"全文可讀長度 {readable:,} 字偏短，建議人工確認",
                detail={"readable_len": readable},
            )]
        return []

    # ── Rule 8：低信心 ─────────────────────────────────────────
    def _rule8_low_confidence(
        self, raw_items: list[RawItem], parse_result: ParseResult
    ) -> list[ValidationFlag]:
        flags: list[ValidationFlag] = []
        if parse_result.confidence < CONFIDENCE_THRESHOLD:
            flags.append(ValidationFlag(
                code="low_confidence_parse",
                severity="info",
                message=(
                    f"整份解析信心 {parse_result.confidence:.2f} 低於門檻 "
                    f"{CONFIDENCE_THRESHOLD}"
                ),
                detail={"confidence": round(parse_result.confidence, 4)},
            ))
        for raw in raw_items:
            if raw.confidence < CONFIDENCE_THRESHOLD:
                flags.append(ValidationFlag(
                    code="low_confidence_item",
                    severity="info",
                    item_number=raw.item_number,
                    message=f"Item {raw.item_number} 信心 {raw.confidence:.2f} 偏低",
                    detail={"confidence": round(raw.confidence, 4)},
                ))
        return flags

    # ── Rule 9：所有 item 可讀字數加總應接近全文 ──────────────────
    def _rule9_total_coverage(
        self, items: list[ItemResult], text: str, has_spans: bool = False
    ) -> list[ValidationFlag]:
        """各 item content_text 可讀字數加總應佔全文可讀字數的合理比例。
        差距過大表示有大段文字未被任何 item 捕獲（常見於 parser 切割失敗）。
        注意：incorporated_by_reference / not_applicable 等無 content_text，
        故門檻寬鬆設在 50%（warning）和 25%（error）。"""
        doc_readable = _readable_length(text)
        if doc_readable == 0:
            return []

        item_total = sum(
            _readable_length(item.content_text)
            for item in items
            if item.content_text
        )
        ratio = item_total / doc_readable

        if ratio > 1.0:
            return [ValidationFlag(
                code="overlapping_item_coverage",
                severity="warning" if has_spans else "error",
                message=(
                    f"所有 item 可讀字數合計 {item_total:,}，"
                    f"超過全文 {doc_readable:,} 字（{ratio:.0%}），"
                    "疑似 item 範圍重疊或邊界計算錯誤"
                ),
                detail={
                    "item_readable_total": item_total,
                    "doc_readable": doc_readable,
                    "ratio": round(ratio, 4),
                },
            )]
        if ratio < ITEM_COVERAGE_ERROR:
            return [ValidationFlag(
                code="low_item_coverage",
                severity="error",
                message=(
                    f"所有 item 可讀字數合計 {item_total:,}，"
                    f"僅佔全文 {doc_readable:,} 字的 {ratio:.0%}，"
                    "疑似大量內容未被任何 Item 捕獲（parser 切割嚴重失敗）"
                ),
                detail={
                    "item_readable_total": item_total,
                    "doc_readable": doc_readable,
                    "ratio": round(ratio, 4),
                },
            )]
        if ratio < ITEM_COVERAGE_WARN:
            return [ValidationFlag(
                code="low_item_coverage",
                severity="warning",
                message=(
                    f"所有 item 可讀字數合計 {item_total:,}，"
                    f"佔全文 {doc_readable:,} 字的 {ratio:.0%}，偏低，"
                    "建議確認是否有大段內容未被捕獲"
                ),
                detail={
                    "item_readable_total": item_total,
                    "doc_readable": doc_readable,
                    "ratio": round(ratio, 4),
                },
            )]
        return []

    # ── Rule 10：char range 重疊 ───────────────────────────────
    def _rule10_char_range_overlap(
        self, raw_items: list[RawItem], has_spans: bool = False
    ) -> list[ValidationFlag]:
        """任意兩個不同 item 的實際 span 不應重疊。
        多 span item 的不同 span 屬於同一 item，跳過不比。
        重疊表示同一段原文被兩個 Item 各自包含，通常是邊界定位錯誤。
        has_spans=True 時（cross-reference 格式）降為 info，因共享頁面是正常現象。"""
        segments: list[tuple[str, int, int]] = []
        for raw in raw_items:
            for start, end in _geometry(raw):
                segments.append((raw.item_number, start, end))

        segments.sort(key=lambda x: x[1])

        severity = "info" if has_spans else "warning"
        flags: list[ValidationFlag] = []
        for i in range(len(segments)):
            num_a, start_a, end_a = segments[i]
            for j in range(i + 1, len(segments)):
                num_b, start_b, end_b = segments[j]
                if start_b >= end_a:
                    break
                if num_a == num_b:
                    continue
                overlap = min(end_a, end_b) - start_b
                if overlap > 0:
                    flags.append(ValidationFlag(
                        code="char_range_overlap",
                        severity=severity,
                        item_number=num_a,
                        message=(
                            f"Item {num_a}（{start_a}–{end_a}）與 "
                            f"Item {num_b}（{start_b}–{end_b}）重疊 {overlap:,} 字"
                        ),
                        detail={
                            "item_a": num_a, "start_a": start_a, "end_a": end_a,
                            "item_b": num_b, "start_b": start_b, "end_b": end_b,
                            "overlap_chars": overlap,
                        },
                    ))
        return flags

    # ── 聚合 ───────────────────────────────────────────────────
    def _build_report(
        self,
        flags: list[ValidationFlag],
        items: list[ItemResult],
        parse_result: ParseResult,
        metadata: FilingMetadata,
        coverage_ratio: float,
    ) -> QualityReport:
        counts = {"error": 0, "warning": 0, "info": 0}
        for flag in flags:
            counts[flag.severity] += 1

        missing_items = [
            item.item_number for item in items if item.status == "missing"
        ]
        missing_required = sorted(
            {
                flag.item_number
                for flag in flags
                if flag.code in ("required_item_missing", "recommended_item_missing")
                and flag.item_number is not None
            },
            key=lambda n: _CANONICAL_ORDER.get(n, 99),
        )
        found_count = sum(
            1 for item in items
            if item.status in ("extracted", "incorporated_by_reference")
        )

        errors, warnings = counts["error"], counts["warning"]
        score = max(0.0, round(1.0 - 0.2 * errors - 0.05 * warnings, 4))

        return QualityReport(
            is_valid=errors == 0,
            score=score,
            parser_name=parse_result.parser_name,
            parser_confidence=round(parse_result.confidence, 4),
            expected_item_count=len(items),
            found_item_count=found_count,
            missing_items=missing_items,
            missing_required_items=missing_required,
            coverage_ratio=coverage_ratio,
            counts=counts,
            flags=flags,
        )
