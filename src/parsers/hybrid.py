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
        Primary parser 信心高但仍缺少部分 item 時，用 TocAnchorParser 補缺。
        只使用 TocAnchorParser（專為補 by_reference / anchor 缺漏設計）；
        不使用 CrossRef / PdfStyle 等 fallback，避免它們做全文重新解析而覆蓋
        primary 已正確找到的 item 位置。
        """
        from src.patterns import ITEM_NUMBERS
        from src.parsers.toc_anchor_parser import TocAnchorParser

        found_nums = {item.item_number for item in primary.raw_items}
        expected = set(ITEM_NUMBERS)
        if not metadata.has_item_1c:
            expected.discard("1C")
        missing = expected - found_nums
        if not missing:
            return primary

        for fallback in self.fallbacks:
            if not isinstance(fallback, TocAnchorParser):
                continue
            fallback_result = fallback.parse(doc, metadata)
            gap_items = [i for i in fallback_result.raw_items if i.item_number in missing]
            if not gap_items:
                break
            logger.info(
                f"gap-fill: adding {[i.item_number for i in gap_items]} "
                f"from {fallback.name}"
            )
            return self._merge(primary, fallback_result, [], fallback)

        return primary

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
