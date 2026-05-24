"""
Pipeline
串起所有步驟的主流程：fetch → preprocess → parse → postprocess → output
"""

from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from src.models import (
    FilingInput, FilingOutput, FilingInfo, FilingMetadata, TimingStats,
)
from src.parsers.base import BaseParser
from src.parsers.cross_reference_multispan_parser import CrossReferenceMultiSpanParser
from src.patterns import (
    ITEM_IN_TABLE_PATTERN,
    PAGE_NUMBER_PATTERN,
    PAGE_NUMBER_BARE_PATTERN,
    FINANCIAL_PAGE_PATTERN,
    PAGE_WORD_PATTERN,
    PAGE_HEADER_PATTERN,
    SPLIT_UPPERCASE_PATTERN,
)
from src.parsers.regex_parser import RegexParser
from src.parsers.hybrid import HybridParser
from src.postprocessor import PostProcessor

logger = logging.getLogger(__name__)

USER_AGENT = "10K-Parser contact@example.com"
BASE_URL = "https://www.sec.gov"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class Pipeline:
    """
    10-K 結構化抽取主 Pipeline。

    使用方式：
        pipeline = Pipeline()                          # 預設純 regex
        pipeline = Pipeline(parser=HybridParser(...))  # 自訂 parser
        result = pipeline.run(FilingInput(cik=..., accession_number=...))
    """

    def __init__(self, parser: BaseParser | None = None):
        # 預設用 HybridParser（目前 fallback=None，等同純 regex）
        self.parser: BaseParser = parser or HybridParser(
            primary=RegexParser(),
            fallback=CrossReferenceMultiSpanParser(),
        )
        self.postprocessor = PostProcessor()

    def run(
        self,
        input: FilingInput,
        save_to: Path | str | None = None,
    ) -> FilingOutput:
        """
        執行完整 pipeline。

        Args:
            input:   FilingInput（CIK+Accession 或 URL）
            save_to: 若指定，把 JSON 結果存到這個路徑。
                     可以是：
                       - 目錄路徑 → 自動命名為 {TICKER}_{DATE}_{ACCESSION}.json
                       - 完整檔案路徑 → 直接存到該路徑
        """
        # 1. 取得 filing metadata 和 HTML URL
        metadata, html_url = self._resolve_input(input)
        logger.info(f"處理：{metadata.company_name} ({metadata.cik}) {metadata.accession_number}")

        # 2. 下載 HTML
        t0 = time.perf_counter()
        html = self._fetch_html(html_url)
        fetch_sec = time.perf_counter() - t0

        # 3. 轉換純文字
        t0 = time.perf_counter()
        text = self._preprocess(html)
        preprocess_sec = time.perf_counter() - t0
        logger.info(f"純文字長度：{len(text):,} 字元")

        # 4. Parser 找 Item 位置
        t0 = time.perf_counter()
        parse_result = self.parser.parse(text, metadata)
        parse_sec = time.perf_counter() - t0
        logger.info(
            f"Parser [{parse_result.parser_name}] 信心={parse_result.confidence:.2f}，"
            f"找到 {len(parse_result.raw_items)} 個 Items"
        )
        if parse_result.warnings:
            for w in parse_result.warnings:
                logger.warning(f"  ⚠ {w}")

        # 5. Postprocess → 產出最終 ItemResult
        t0 = time.perf_counter()
        items = self.postprocessor.process(parse_result.raw_items, text, metadata)
        postprocess_sec = time.perf_counter() - t0

        output = FilingOutput(
            filing_info=FilingInfo(
                cik=metadata.cik,
                accession_number=metadata.accession_number,
                company_name=metadata.company_name,
                fiscal_year_end=metadata.fiscal_year_end,
                filer_category=metadata.filer_category,
            ),
            items=items,
            timing=TimingStats(
                fetch_html_sec=round(fetch_sec, 3),
                preprocess_sec=round(preprocess_sec, 3),
                parse_sec=round(parse_sec, 3),
                postprocess_sec=round(postprocess_sec, 3),
            ),
        )

        if save_to is not None:
            self._save(output, metadata, text, Path(save_to))

        return output

    def _save(
        self,
        output: FilingOutput,
        metadata: FilingMetadata,
        full_text: str,
        path: Path,
    ) -> None:
        """把 FilingOutput 存成三個檔案：JSON、結果 MD、原文 MD"""
        # 如果 path 是目錄，自動產生檔名（不含副檔名）
        if path.is_dir() or not path.suffix:
            path.mkdir(parents=True, exist_ok=True)
            acc_clean = metadata.accession_number.replace("-", "")
            stem = f"{metadata.cik}_{metadata.fiscal_year_end}_{acc_clean}"
            json_path    = path / f"{stem}.json"
            md_path      = path / f"{stem}.md"
            fulltext_path = path / f"{stem}_fulltext.md"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            json_path    = path
            md_path      = path.with_suffix(".md")
            fulltext_path = path.with_name(path.stem + "_fulltext.md")

        # 存 JSON
        data = output.model_dump()
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"JSON     已存到：{json_path}")

        # 存結果 MD
        md_path.write_text(self._to_markdown(output), encoding="utf-8")
        logger.info(f"結果 MD  已存到：{md_path}")

        # 存原文 MD
        fulltext_path.write_text(self._to_fulltext_markdown(output, full_text), encoding="utf-8")
        logger.info(f"原文 MD  已存到：{fulltext_path}")

    def _to_markdown(self, output: FilingOutput) -> str:
        """把 FilingOutput 轉成人類可讀的 Markdown 報告"""
        fi = output.filing_info
        lines: list[str] = []

        # Header
        lines += [
            f"# {fi.company_name} — 10-K 結構化抽取結果",
            "",
            "## Filing Info",
            "",
            f"| 欄位 | 值 |",
            f"|---|---|",
            f"| Company | {fi.company_name} |",
            f"| CIK | {fi.cik} |",
            f"| Accession Number | {fi.accession_number} |",
            f"| Fiscal Year End | {fi.fiscal_year_end} |",
            f"| Filer Category | {fi.filer_category or 'N/A'} |",
            f"| 抽取時間 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |",
            "",
        ]

        # Items 摘要表
        lines += [
            "## Items 摘要",
            "",
            "| Part | Item | 標題 | Status | 字元數 |",
            "|---|---|---|---|---|",
        ]
        for item in output.items:
            char_count = (
                len(item.content_text) if item.content_text else 0
            )
            char_str = f"{char_count:,}" if char_count else "—"
            status_icon = {
                "extracted":                 "✅",
                "incorporated_by_reference": "🔗",
                "not_applicable":            "➖",
                "reserved":                  "⬜",
                "missing":                   "❓",
            }.get(item.status, item.status)
            lines.append(
                f"| {item.part} | {item.item_number} | {item.item_title[:40]} "
                f"| {status_icon} {item.status} | {char_str} |"
            )

        lines.append("")

        # 各 Item 內容預覽（只有 extracted 的才顯示）
        lines += ["## Items 內容預覽", ""]
        for item in output.items:
            if item.status != "extracted" or not item.content_text:
                continue
            preview = item.content_text[:300].replace("\n", " ")
            if len(item.content_text) > 300:
                preview += "…"
            lines += [
                f"### Item {item.item_number}：{item.item_title}",
                "",
                f"> {preview}",
                "",
            ]

        return "\n".join(lines)

    def _to_fulltext_markdown(self, output: FilingOutput, full_text: str) -> str:
        """
        把原文依 Item 切割後，逐段輸出成 Markdown。
        讓人工審查時可以直接對照原文與抽取結果。
        """
        fi = output.filing_info
        lines: list[str] = []

        lines += [
            f"# {fi.company_name} — 10-K 原文",
            "",
            f"CIK: {fi.cik} ｜ Accession: {fi.accession_number} ｜ FY End: {fi.fiscal_year_end}",
            "",
            "---",
            "",
        ]

        for item in output.items:
            lines += [
                f"## Item {item.item_number}：{item.item_title}",
                "",
                f"**Part**: {item.part} ｜ **Status**: `{item.status}`",
                "",
            ]

            if item.status == "extracted" and item.content_text:
                lines += [
                    "```",
                    item.content_text,
                    "```",
                    "",
                ]
            elif item.status == "incorporated_by_reference":
                lines += [
                    "```",
                    item.content_text,
                    "```",
                    "",
                ]
            elif item.status == "reserved":
                lines += [
                    "_此 Item 為 Reserved，無實際內容。_",
                    "",
                ]
            elif item.status == "not_applicable":
                lines += [
                    "_此 Item 對該公司不適用（Not Applicable）。_",
                    "",
                ]
            elif item.status == "missing":
                lines += [
                    "_⚠ Parser 未找到此 Item，可能為格式特殊或解析失敗，需人工確認。_",
                    "",
                ]

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _resolve_input(self, input: FilingInput) -> tuple[FilingMetadata, str]:
        """
        從 input 解析出 FilingMetadata 和 HTML URL。
        支援兩種輸入：(cik + accession_number) 或 url。
        """
        if input.url:
            # URL 模式：metadata 只有最基本的資訊
            return FilingMetadata(
                cik="unknown",
                accession_number="unknown",
                company_name="unknown",
                fiscal_year_end="unknown",
            ), input.url

        # CIK + Accession Number 模式
        cik = input.cik.strip().lstrip("0").zfill(10)
        accession = input.accession_number.strip()

        # 從 EDGAR submissions API 拿 metadata
        sub = self._get_submissions(cik)
        company_name = sub.get("name", "Unknown")
        filer_category = sub.get("category", None)

        # 從申報記錄找 fiscal_year_end 和主文件
        fiscal_year_end, primary_doc = self._find_filing_info(sub, accession)

        # 組出 HTML URL
        cik_clean = cik.lstrip("0")
        acc_clean = accession.replace("-", "")
        html_url = f"{BASE_URL}/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_doc}"

        metadata = FilingMetadata(
            cik=cik,
            accession_number=accession,
            company_name=company_name,
            fiscal_year_end=fiscal_year_end,
            filer_category=filer_category,
        )
        return metadata, html_url

    def _get_submissions(self, cik_padded: str) -> dict:
        url = SUBMISSIONS_URL.format(cik=cik_padded)
        resp = self._get(url)
        return resp.json()

    def _find_filing_info(self, sub: dict, accession: str) -> tuple[str, str]:
        """從 submissions JSON 找出指定 accession 的 fiscal_year_end 和主文件名稱"""
        recent = sub.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        # 正規化 accession 格式用於比對
        target = accession.replace("-", "")

        for acc, date, doc in zip(accessions, report_dates, primary_docs):
            if acc.replace("-", "") == target:
                return date, doc

        raise ValueError(f"在 submissions 中找不到 accession: {accession}")

    def _fetch_html(self, url: str) -> bytes:
        logger.info(f"下載 HTML：{url}")
        resp = self._get(url)
        return resp.content

    def _preprocess(self, html: bytes) -> str:
        """
        HTML → 混合文字（保留 <table> 的 HTML 結構）

        非表格部分轉成純文字；<table> 元素保留完整 HTML 字串，
        這樣輸出到 Markdown 時表格仍可正確渲染（Markdown 相容 HTML）。
        """
        soup = BeautifulSoup(html, "lxml")

        # 移除 script / style
        for tag in soup(["script", "style"]):
            tag.decompose()

        # 移除 hidden iXBRL / XBRL header metadata，避免大量隱藏 facts 汙染正文文字。
        for tag in soup.find_all(
            style=lambda value: value and "display:none" in value.replace(" ", "").lower()
        ):
            tag.decompose()

        # 拆除 iXBRL 命名空間標籤（ix:* / xbrli:* 等），保留其文字內容
        # unwrap() 會把標籤本身移除，但子節點留在原位
        for tag in soup.find_all(lambda t: ":" in t.name):
            tag.unwrap()

        # 從 TOC table 收集 item -> fragment 連結，並在正文目標前注入 marker，
        # 讓 fallback parser 可以從純文字中回推正文 offset。
        toc_anchor_ids: set[str] = set()
        for table in soup.find_all("table"):
            table_text_compact = table.get_text("", strip=True)
            item_refs = {m.upper() for m in ITEM_IN_TABLE_PATTERN.findall(table_text_compact)}
            if len(item_refs) < 4:
                continue

            for link in table.find_all("a", href=True):
                href = link.get("href", "")
                if "#" not in href:
                    continue
                toc_anchor_ids.add(href.split("#", 1)[1])

        for anchor_id in toc_anchor_ids:
            target = soup.find(id=anchor_id)
            if target is not None:
                target.insert_before(f"\n[[ANCHOR:{anchor_id}]]\n")

        # ── 用 placeholder 替換所有 <table>，避免 get_text() 破壞結構 ──
        PLACEHOLDER_PREFIX = "\x00TABLE\x00"
        table_html_store: list[str] = []

        # 允許保留的屬性（只保結構，去掉 style/class/width/height 等排版雜訊）
        _KEEP_ATTRS = {"colspan", "rowspan", "href"}

        for table in soup.find_all("table"):
            # compact（無分隔）用於偵測，能正確合併 inline 斷字（"I"+"TEM"="ITEM"）
            table_text_compact = table.get_text("", strip=True)

            # 計算此 table 中出現幾個「不同」的 Item 編號
            # TOC 型 table 通常包含 10+ 個 Item；章節標題 table 通常只有 1–3 個
            item_refs = {m.upper() for m in ITEM_IN_TABLE_PATTERN.findall(table_text_compact)}

            if 0 < len(item_refs) <= 3:
                # 章節標題型 table：轉成純文字，讓 regex parser 能偵測 Item 標題
                # 用換行符分隔後套用斷字修復（"I\nTEM 10." → "ITEM 10."）
                table_text_nl = table.get_text("\n", strip=True)
                fixed = table_text_nl
                while True:
                    new = SPLIT_UPPERCASE_PATTERN.sub(r"\1\2", fixed)
                    if new == fixed:
                        break
                    fixed = new
                table.replace_with(f"\n{fixed}\n")
            else:
                # 資料型 table 或目錄型 table（item_refs >= 4）：清洗屬性後保留 HTML
                for tag in table.find_all(True):
                    tag.attrs = {
                        k: v for k, v in tag.attrs.items()
                        if k in _KEEP_ATTRS
                    }
                idx = len(table_html_store)
                placeholder = f"{PLACEHOLDER_PREFIX}{idx}\x00"
                table_html_store.append(str(table))
                table.replace_with(placeholder)

        # 取得純文字（此時 <table> 已被 placeholder 取代）
        raw_text = soup.get_text(separator="\n")

        # 合併多餘空行，同時保留 placeholder 行不動
        lines = raw_text.splitlines()
        cleaned: list[str] = []
        blank_count = 0
        for line in lines:
            stripped = line.strip()
            if stripped:
                blank_count = 0
                cleaned.append(stripped)
            else:
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append("")

        text = "\n".join(cleaned)

        # ── 修復 HTML inline 斷字（"I\nTEM" → "ITEM"）──
        # 反覆套用直到沒有變化（應對連續多層斷字）
        while True:
            fixed = SPLIT_UPPERCASE_PATTERN.sub(r"\1\2", text)
            if fixed == text:
                break
            text = fixed

        # ── 移除頁碼與頁眉（pattern 定義見 src/patterns.py）──
        text = PAGE_NUMBER_PATTERN.sub("\n", text)
        text = PAGE_NUMBER_BARE_PATTERN.sub("\n", text)
        text = FINANCIAL_PAGE_PATTERN.sub("", text)
        text = PAGE_WORD_PATTERN.sub("\n", text)
        text = PAGE_HEADER_PATTERN.sub("\n", text)

        # ── 把 placeholder 換回原始 HTML table 字串 ──
        for i, table_html in enumerate(table_html_store):
            placeholder = f"{PLACEHOLDER_PREFIX}{i}\x00"
            text = text.replace(placeholder, f"\n\n{table_html}\n\n")

        return text

    def _get(self, url: str) -> requests.Response:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp
