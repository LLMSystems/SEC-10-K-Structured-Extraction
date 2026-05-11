"""
Hybrid Parser（調度器）
先跑 RegexParser，對信心低於門檻的 Item 再呼叫 LLMParser 補強。

這個設計讓你可以：
  - 第一階段：hybrid = HybridParser(primary=regex, fallback=None)
              → 純 regex，LLM 完全不介入
  - 第二階段：hybrid = HybridParser(primary=regex, fallback=llm, threshold=0.7)
              → regex 信心 < 0.7 的 Item 才呼叫 LLM
  - 第三階段：針對特定 edge case 調整 threshold
"""

from __future__ import annotations
import logging
from src.models import RawItem, FilingMetadata
from src.parsers.base import BaseParser, ParseResult

logger = logging.getLogger(__name__)


class HybridParser(BaseParser):
    """
    混合策略調度器。

    Args:
        primary:   主 parser（目前是 RegexParser）
        fallback:  備用 parser（LLMParser，可為 None 表示不啟用）
        threshold: 低於此信心分數才觸發 fallback（預設 0.7）
        item_threshold: 單一 Item 的信心門檻（預設同 threshold）
    """

    def __init__(
        self,
        primary: BaseParser,
        fallback: BaseParser | None = None,
        threshold: float = 0.7,
        item_threshold: float | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.threshold = threshold
        self.item_threshold = item_threshold if item_threshold is not None else threshold

    @property
    def name(self) -> str:
        fallback_name = self.fallback.name if self.fallback else "none"
        return f"hybrid({self.primary.name}+{fallback_name})"

    def parse(self, text: str, metadata: FilingMetadata) -> ParseResult:
        # 先跑主 parser
        primary_result = self.primary.parse(text, metadata)
        logger.info(
            f"[{self.primary.name}] 信心={primary_result.confidence:.2f}，"
            f"找到 {len(primary_result.raw_items)} 個 Items"
        )

        # 如果沒有 fallback 或整體信心夠高，直接回傳
        if self.fallback is None or primary_result.confidence >= self.threshold:
            return primary_result

        # 找出信心不足的 Item
        low_confidence_items = [
            item for item in primary_result.raw_items
            if item.confidence < self.item_threshold
        ]

        if not low_confidence_items:
            return primary_result

        logger.info(
            f"有 {len(low_confidence_items)} 個 Item 信心不足，"
            f"交給 {self.fallback.name} 補強"
        )

        # 對低信心 Item 呼叫 fallback
        # 目前策略：把整份文字丟給 fallback，讓它重新找這些 Item
        # TODO: 改成只傳入低信心 Item 附近的 snippet，節省 LLM token
        fallback_result = self.fallback.parse(text, metadata)

        # 合併結果：用 fallback 的結果取代低信心的 primary 結果
        merged = self._merge(primary_result, fallback_result, low_confidence_items)
        return merged

    def _merge(
        self,
        primary: ParseResult,
        fallback: ParseResult,
        to_replace: list[RawItem],
    ) -> ParseResult:
        """
        用 fallback 的結果取代 primary 中信心不足的 Items。
        保留 primary 中信心足夠的 Items 不動。
        """
        replace_nums = {item.item_number for item in to_replace}
        fallback_map = {item.item_number: item for item in fallback.raw_items}

        merged_items: list[RawItem] = []
        warnings = list(primary.warnings)

        for item in primary.raw_items:
            if item.item_number in replace_nums and item.item_number in fallback_map:
                fb_item = fallback_map[item.item_number]
                logger.info(
                    f"Item {item.item_number}：用 {self.fallback.name} 結果取代 "
                    f"（信心 {item.confidence:.2f} → {fb_item.confidence:.2f}）"
                )
                merged_items.append(fb_item)
            else:
                merged_items.append(item)

        # 加入 fallback 找到但 primary 完全沒找到的 Item
        primary_nums = {item.item_number for item in primary.raw_items}
        for item in fallback.raw_items:
            if item.item_number not in primary_nums:
                logger.info(f"Item {item.item_number}：由 {self.fallback.name} 補充找到")
                merged_items.append(item)
                warnings.append(f"Item {item.item_number} 由 fallback parser 補充")

        # 重新排序
        from src.parsers.regex_parser import ITEM_NUMBERS
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
