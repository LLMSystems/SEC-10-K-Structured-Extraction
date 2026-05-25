"""
LLM Parser（Stub）
目前是空殼，架構完整，之後填入實際 LLM 呼叫邏輯。

設計原則：
  - 不整份文件丟給 LLM（太貴、太慢、超出 context window）
  - 只對 regex 信心低的片段呼叫 LLM
"""

from __future__ import annotations
from src.models import RawItem, FilingMetadata, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult


class LLMParser(BaseParser):
    """
    LLM 輔助 parser。
    設計上只處理 regex 無法確定的片段，不整份文件送進去。
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        """
        Args:
            model: 使用哪個模型。預設用最便宜的 Haiku，
                   只有真正複雜的 case 才考慮升到 Sonnet。
        """
        self.model = model

    @property
    def name(self) -> str:
        return f"llm:{self.model}"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        """
        TODO: 實作 LLM 呼叫邏輯。

        預計策略：
          1. 把文字切成 chunk（每塊約 4000 token）
          2. 對每個 chunk 問 LLM：「這個片段裡有哪些 Item 標題？從哪個字元開始？」
          3. 合併所有 chunk 的結果
          4. 信心分數由 LLM 的回應確定性決定
        """
        raise NotImplementedError(
            "LLMParser 尚未實作。"
            "請先用 RegexParser 或 HybridParser（以 regex 為主）。"
        )

    def parse_snippet(
        self,
        snippet: str,
        item_number: str,
        context_before: str = "",
        context_after: str = "",
    ) -> RawItem | None:
        """
        TODO: 針對單一可疑片段，讓 LLM 判斷：
          - 這裡是不是 Item {item_number} 的開頭？
          - 如果是，標題文字是什麼？

        這個方法讓 HybridParser 可以只對低信心的 Item 呼叫 LLM，
        而不是整份文件都送進去。
        """
        raise NotImplementedError("LLMParser.parse_snippet 尚未實作")
