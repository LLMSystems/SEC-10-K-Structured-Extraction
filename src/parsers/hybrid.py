from __future__ import annotations

import logging

from src.models import FilingMetadata, RawItem, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult

logger = logging.getLogger(__name__)


class HybridParser(BaseParser):
    """
    Run a primary parser first, then try one or more fallback parsers when the
    primary parser fails entirely or returns low-confidence items.
    """

    def __init__(
        self,
        primary: BaseParser,
        fallback: BaseParser | list[BaseParser] | tuple[BaseParser, ...] | None = None,
        threshold: float = 0.7,
        item_threshold: float | None = None,
    ):
        self.primary = primary
        if fallback is None:
            self.fallbacks: list[BaseParser] = []
        elif isinstance(fallback, (list, tuple)):
            self.fallbacks = list(fallback)
        else:
            self.fallbacks = [fallback]
        self.threshold = threshold
        self.item_threshold = item_threshold if item_threshold is not None else threshold

    @property
    def name(self) -> str:
        fallback_name = ",".join(parser.name for parser in self.fallbacks) if self.fallbacks else "none"
        return f"hybrid({self.primary.name}+{fallback_name})"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        primary_result = self.primary.parse(doc, metadata)
        logger.info(
            f"[{self.primary.name}] confidence={primary_result.confidence:.2f}, "
            f"items={len(primary_result.raw_items)}"
        )

        if not self.fallbacks or primary_result.confidence >= self.threshold:
            # Primary parser is confident, but may still miss individual items.
            # Attempt a targeted gap-fill via fallback parsers for any missing items.
            if self.fallbacks and primary_result.confidence >= self.threshold:
                primary_result = self._try_gap_fill(primary_result, doc, metadata)
            return primary_result

        if not primary_result.raw_items:
            fallback_result = self._run_fallback_chain(doc, metadata)
            if fallback_result is not None:
                fallback_result.warnings = list(primary_result.warnings) + list(fallback_result.warnings)
                return fallback_result
            return primary_result

        low_confidence_items = [
            item for item in primary_result.raw_items
            if item.confidence < self.item_threshold
        ]
        if not low_confidence_items:
            return primary_result

        logger.info(
            f"{len(low_confidence_items)} low-confidence items detected; trying fallback chain"
        )

        for fallback in self.fallbacks:
            fallback_result = fallback.parse(doc, metadata)
            if not fallback_result.raw_items:
                continue
            return self._merge(primary_result, fallback_result, low_confidence_items, fallback)

        return primary_result

    def _try_gap_fill(
        self,
        primary: ParseResult,
        doc: PreprocessedDocument,
        metadata: FilingMetadata,
    ) -> ParseResult:
        """
        Primary parser 信心高但仍缺少部分 item 時，用「補缺專用」的 fallback 補上。

        只使用 TocAnchorParser（補 by_reference / anchor 缺漏）和 BareTableParser
        （補裸表格陳列式缺漏，如信託/SPE 空殼申報）；不使用 CrossRef / PdfStyle 等
        需要全文重新解析的 fallback，避免不必要的計算開銷。

        - TocAnchorParser：標準補缺，傳入 to_replace=[]，_merge 只「新增」primary
          缺漏的 item，不動 primary 已找到的結果。
        - BareTableParser：先偵測「裸表格」結構，偵測不到就回傳空結果，故只在
          確實命中這類版面時才會介入。一旦命中，代表 RegexParser 在這份文件裡
          找到的「標題」多半只是表格陳述文字中偶然出現的 Item 字樣（交叉引用），
          而非真正標題位置；故對它有結果的 item，BareTableParser 的結果優先於
          primary（傳入 result.raw_items 作為 to_replace，_merge 仍只在 fallback
          也找到對應 item 時才覆蓋，找不到的維持 primary 結果，不會整批清空）。
        """
        from src.patterns import ITEM_NUMBERS
        from src.parsers.toc_anchor_parser import TocAnchorParser
        from src.parsers.bare_table_parser import BareTableParser

        found_nums = {item.item_number for item in primary.raw_items}
        expected = set(ITEM_NUMBERS)
        if not metadata.has_item_1c:
            expected.discard("1C")
        missing = expected - found_nums
        if not missing:
            return primary

        result = primary
        for fallback in self.fallbacks:
            if isinstance(fallback, BareTableParser):
                # 只有在 primary 本身覆蓋率明顯偏低時，才允許 BareTableParser 整批覆蓋
                # primary 的結果（如 KTH：找到 2/23，正文確實沒有可辨識標題）。
                # 若 primary 已經找到大半 item（如 ACRL/UMEW 找到 20/22-23），
                # 代表 _has_bare_item_table 多半是誤判（資料表格內偶然出現多個
                # Item 字樣），此時整批覆蓋只會用交叉引用位置去蓋掉正確標題。
                if len(found_nums) >= len(missing):
                    continue
                fallback_result = fallback.parse(doc, metadata)
                if not fallback_result.raw_items:
                    continue
                logger.info(
                    f"bare-table structure detected; preferring {fallback.name} "
                    f"results for {[i.item_number for i in fallback_result.raw_items]}"
                )
                result = self._merge(result, fallback_result, result.raw_items, fallback)
                continue

            if not isinstance(fallback, TocAnchorParser):
                continue
            still_missing = expected - {item.item_number for item in result.raw_items}
            if not still_missing:
                break
            fallback_result = fallback.parse(doc, metadata)
            gap_items = [i for i in fallback_result.raw_items if i.item_number in still_missing]
            if not gap_items:
                continue
            logger.info(
                f"gap-fill: adding {[i.item_number for i in gap_items]} "
                f"from {fallback.name}"
            )
            result = self._merge(result, fallback_result, [], fallback)

        return result

    # Fallback parsers must exceed this confidence to be accepted immediately;
    # below it we keep trying and return the highest-confidence result at the end.
    _FALLBACK_MIN_CONFIDENCE: float = 0.5

    def _run_fallback_chain(
        self,
        doc: PreprocessedDocument,
        metadata: FilingMetadata,
    ) -> ParseResult | None:
        collected_warnings: list[str] = []
        best: ParseResult | None = None
        for fallback in self.fallbacks:
            logger.info(f"[{self.primary.name}] no items found; trying fallback {fallback.name}")
            fallback_result = fallback.parse(doc, metadata)
            if not fallback_result.raw_items:
                collected_warnings.extend(
                    f"[{fallback.name}] {warning}"
                    for warning in fallback_result.warnings
                )
                continue
            if fallback_result.confidence >= self._FALLBACK_MIN_CONFIDENCE:
                fallback_result.warnings = collected_warnings + list(fallback_result.warnings)
                return fallback_result
            # Below threshold — keep as candidate and continue to find a better one
            if best is None or fallback_result.confidence > best.confidence:
                best = fallback_result
                best.warnings = collected_warnings + list(best.warnings)
        return best

    def _merge(
        self,
        primary: ParseResult,
        fallback: ParseResult,
        to_replace: list[RawItem],
        fallback_parser: BaseParser,
    ) -> ParseResult:
        replace_nums = {item.item_number for item in to_replace}
        fallback_map = {item.item_number: item for item in fallback.raw_items}

        merged_items: list[RawItem] = []
        warnings = list(primary.warnings)

        for item in primary.raw_items:
            if item.item_number in replace_nums and item.item_number in fallback_map:
                fb_item = fallback_map[item.item_number]
                logger.info(
                    f"Replacing item {item.item_number} with {fallback_parser.name} "
                    f"({item.confidence:.2f} -> {fb_item.confidence:.2f})"
                )
                merged_items.append(fb_item)
            else:
                merged_items.append(item)

        primary_nums = {item.item_number for item in primary.raw_items}
        for item in fallback.raw_items:
            if item.item_number not in primary_nums:
                logger.info(f"Adding item {item.item_number} from fallback {fallback_parser.name}")
                merged_items.append(item)
                warnings.append(f"Item {item.item_number} added by fallback parser")

        from src.patterns import ITEM_NUMBERS

        order = {n: i for i, n in enumerate(ITEM_NUMBERS)}
        merged_items.sort(key=lambda x: order.get(x.item_number, 99))

        avg_confidence = (
            sum(i.confidence for i in merged_items) / len(merged_items)
            if merged_items else 0.0
        )

        return ParseResult(
            raw_items=merged_items,
            confidence=avg_confidence,
            parser_name=self.name,
            warnings=warnings,
        )
