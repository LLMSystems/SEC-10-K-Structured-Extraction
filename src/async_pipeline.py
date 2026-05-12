"""
AsyncPipeline
把 Pipeline 的 HTTP I/O 改成 async；CPU-bound 步驟丟進 thread executor。
核心邏輯（preprocess / parse / postprocess）完全沿用 Pipeline。
"""

from __future__ import annotations
import asyncio
import logging
import time

import httpx

from src.pipeline import Pipeline, USER_AGENT, SUBMISSIONS_URL, BASE_URL
from src.models import (
    FilingInput, FilingOutput, FilingInfo, FilingMetadata, TimingStats,
)

logger = logging.getLogger(__name__)


class AsyncPipeline(Pipeline):
    """
    繼承 Pipeline，只替換兩個 HTTP 方法為 async；其餘邏輯不重複實作。
    """

    async def _get_async(self, url: str) -> httpx.Response:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp

    async def _get_submissions_async(self, cik_padded: str) -> dict:
        url = SUBMISSIONS_URL.format(cik=cik_padded)
        resp = await self._get_async(url)
        return resp.json()

    async def _resolve_input_async(
        self, input: FilingInput
    ) -> tuple[FilingMetadata, str]:
        if input.url:
            return FilingMetadata(
                cik="unknown",
                accession_number="unknown",
                company_name="unknown",
                fiscal_year_end="unknown",
            ), input.url

        cik = input.cik.strip().lstrip("0").zfill(10)
        accession = input.accession_number.strip()

        sub = await self._get_submissions_async(cik)
        company_name = sub.get("name", "Unknown")
        filer_category = sub.get("category", None)

        fiscal_year_end, primary_doc = self._find_filing_info(sub, accession)

        cik_clean = cik.lstrip("0")
        acc_clean = accession.replace("-", "")
        html_url = f"{BASE_URL}/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_doc}"

        return FilingMetadata(
            cik=cik,
            accession_number=accession,
            company_name=company_name,
            fiscal_year_end=fiscal_year_end,
            filer_category=filer_category,
        ), html_url

    async def _fetch_html_async(self, url: str) -> bytes:
        logger.info(f"下載 HTML：{url}")
        resp = await self._get_async(url)
        return resp.content

    async def run_async(self, input: FilingInput) -> FilingOutput:
        metadata, html_url = await self._resolve_input_async(input)
        logger.info(
            f"處理：{metadata.company_name} ({metadata.cik}) {metadata.accession_number}"
        )

        t0 = time.perf_counter()
        html = await self._fetch_html_async(html_url)
        fetch_sec = time.perf_counter() - t0

        loop = asyncio.get_running_loop()

        t0 = time.perf_counter()
        text = await loop.run_in_executor(None, self._preprocess, html)
        preprocess_sec = time.perf_counter() - t0
        logger.info(f"純文字長度：{len(text):,} 字元")

        t0 = time.perf_counter()
        parse_result = await loop.run_in_executor(
            None, self.parser.parse, text, metadata
        )
        parse_sec = time.perf_counter() - t0
        logger.info(
            f"Parser [{parse_result.parser_name}] 信心={parse_result.confidence:.2f}，"
            f"找到 {len(parse_result.raw_items)} 個 Items"
        )
        for w in parse_result.warnings:
            logger.warning(f"  ⚠ {w}")

        t0 = time.perf_counter()
        items = await loop.run_in_executor(
            None,
            self.postprocessor.process,
            parse_result.raw_items,
            text,
            metadata,
        )
        postprocess_sec = time.perf_counter() - t0

        return FilingOutput(
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
