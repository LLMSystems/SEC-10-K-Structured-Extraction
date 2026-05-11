"""
Parser 抽象介面
所有 parser 實作都繼承 BaseParser，pipeline 只依賴這個介面。
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.models import RawItem, FilingMetadata


@dataclass
class ParseResult:
    """
    Parser 的輸出。
    - raw_items: 找到的 Item 列表，帶信心分數
    - confidence: 整體信心（0–1），hybrid dispatcher 用來決定是否 fallback
    - parser_name: 哪個 parser 產出的（用於 logging 和報告）
    - warnings: 解析過程中的警告（例如找不到某個 Item、信心低於門檻）
    """
    raw_items: list[RawItem]
    confidence: float                   # 0.0–1.0
    parser_name: str
    warnings: list[str] = field(default_factory=list)


class BaseParser(ABC):
    """
    所有 parser 的抽象基底類別。

    子類別實作 parse()，接收純文字和 metadata，回傳 ParseResult。
    不直接操作 HTML——HTML 的清理由 preprocessor 負責。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Parser 的識別名稱，用於 logging"""
        ...

    @abstractmethod
    def parse(self, text: str, metadata: FilingMetadata) -> ParseResult:
        """
        從純文字中找出所有 Item 的位置與內容。

        Args:
            text:     已清理的純文字（由 preprocessor 產出）
            metadata: Filing 的基本資訊，用於決定應有哪些 Items

        Returns:
            ParseResult，包含 raw_items 和整體信心分數
        """
        ...

    def _make_result(
        self,
        raw_items: list[RawItem],
        warnings: list[str] | None = None,
    ) -> ParseResult:
        """
        建立 ParseResult，自動計算整體信心（取所有 item 的平均）。
        子類別可以 override 這個方法來實作自訂的信心計算。
        """
        if not raw_items:
            confidence = 0.0
        else:
            confidence = sum(i.confidence for i in raw_items) / len(raw_items)

        return ParseResult(
            raw_items=raw_items,
            confidence=confidence,
            parser_name=self.name,
            warnings=warnings or [],
        )
