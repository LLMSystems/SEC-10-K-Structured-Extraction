"""
資料模型定義
所有 pipeline 內部與對外的資料結構
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field, model_validator


# ──────────────────────────────────────────────
# 輸入
# ──────────────────────────────────────────────

class FilingInput(BaseModel):
    """
    API 輸入。支援兩種方式：
      1. cik + accession_number
      2. url（直接給 EDGAR 主文件的 URL）
    """
    cik: Optional[str] = None
    accession_number: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def check_input(self) -> FilingInput:
        has_ids = self.cik is not None and self.accession_number is not None
        has_url = self.url is not None
        if not has_ids and not has_url:
            raise ValueError("必須提供 (cik + accession_number) 或 url 其中一種")
        if has_ids and has_url:
            raise ValueError("(cik + accession_number) 和 url 只能擇一")
        return self


# ──────────────────────────────────────────────
# 輸出
# ──────────────────────────────────────────────

ItemStatus = Literal[
    "extracted",               # 成功抽取內容
    "incorporated_by_reference",  # Part III 以引用方式呈現
    "not_applicable",          # 公司明確表示此 Item 不適用
    "reserved",                # SEC 規定此 Item 已 Reserved（如 Item 6）
    "missing",                 # Parser 找不到此 Item（解析失敗，非業務上不適用）
]


class ItemResult(BaseModel):
    """單一 Item 的抽取結果"""
    part: str                          # "Part I", "Part II", ...
    item_number: str                   # "1", "1A", "1C", "7", ...
    item_title: str                    # "Business", "Risk Factors", ...
    content_text: Optional[str]        # 純文字內容；非 extracted 時為 null
    char_range: Optional[tuple[int, int]]  # 在純文字中的起終位置；非 extracted 時為 null
    status: ItemStatus


class FilingInfo(BaseModel):
    """Filing 的基本 metadata"""
    cik: str
    accession_number: str
    company_name: str
    fiscal_year_end: str               # "YYYY-MM-DD"
    filer_category: Optional[str] = None   # "Large accelerated filer", etc.


class TimingStats(BaseModel):
    """各步驟耗時（秒）"""
    fetch_html_sec: float
    preprocess_sec: float
    parse_sec: float
    postprocess_sec: float

    @property
    def total_sec(self) -> float:
        return self.fetch_html_sec + self.preprocess_sec + self.parse_sec + self.postprocess_sec


class FilingOutput(BaseModel):
    """API 輸出"""
    filing_info: FilingInfo
    items: list[ItemResult]
    timing: Optional[TimingStats] = None


# ──────────────────────────────────────────────
# Parser 內部資料結構
# ──────────────────────────────────────────────

@dataclass
class PreprocessedDocument:
    """
    Shared parser input that preserves both the original HTML and the derived
    text representation.
    """
    raw_html: bytes
    normalized_html: str
    text: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageMarker:
    page_number: int
    marker_start: int
    marker_end: int


class RawSpan(BaseModel):
    """Multi-span parser 用的單段文字範圍"""
    start_char: int
    end_char: int
    source: Optional[str] = None


RawItemStatusHint = Literal[
    "none_declared",
    "reserved_declared",
    "by_reference_declared",
]


class RawItem(BaseModel):
    """
    Parser 找到的原始候選 Item，帶信心分數。
    PostProcessor 再根據信心決定是否採用或 fallback。
    """
    item_number: str
    title_text: str                    # 找到的原始標題文字（未正規化）
    start_char: int                    # 在純文字中的起始位置
    end_char: Optional[int] = None    # 在純文字中的結束位置（填到下一個 Item 的 start）
    spans: list[RawSpan] = Field(default_factory=list)
    status_hint: Optional[RawItemStatusHint] = None
    by_reference_text: Optional[str] = None
    confidence: float = 1.0           # 0.0–1.0，parser 對這個切割的信心


class FilingMetadata(BaseModel):
    """
    Pipeline 各步驟共享的 filing 相關資訊，
    用於決定應該有哪些 Items、Item 6/1C 的狀態等。
    """
    cik: str
    accession_number: str
    company_name: str
    fiscal_year_end: str               # "YYYY-MM-DD"
    filer_category: Optional[str] = None
    filing_date: Optional[str] = None  # "YYYY-MM-DD"

    @property
    def has_item_1c(self) -> bool:
        """根據 FY 結束日與申報類別判斷是否應有 Item 1C"""
        from datetime import date
        try:
            fy_end = date.fromisoformat(self.fiscal_year_end)
        except (ValueError, TypeError):
            return False

        large_filer = self.filer_category and "large accelerated" in self.filer_category.lower()
        if large_filer:
            return fy_end >= date(2023, 1, 1)
        else:
            return fy_end >= date(2023, 1, 1)

    @property
    def item_6_is_reserved(self) -> bool:
        """FY 結束日 >= 2021-12-31 時 Item 6 改為 Reserved"""
        from datetime import date
        try:
            fy_end = date.fromisoformat(self.fiscal_year_end)
            return fy_end >= date(2021, 12, 31)
        except (ValueError, TypeError):
            return True  # 保守預設：視為 reserved
