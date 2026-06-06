"""
Tier 2 — 渲染 demo：SEC 10-K HTML → 分頁 PDF → 每頁 PNG

目的（對應 feedback/external_reference_validation/validation_plan.md「來源 A 頁碼 / 來源 C VLM」）：
把 EDGAR 上的 10-K HTML 用 headless Chromium 忠實渲染成「人看到的版面」，
產生真實分頁的 PDF，再把每頁轉成 PNG，作為後續餵 VLM / 頁碼對照的輸入。

為什麼用 Playwright（headless Chromium）：
10-K 是重度 CSS / iXBRL 排版的 HTML，純 Python 的 HTML→PDF（weasyprint / PyMuPDF）
還原度不足。Chromium 渲染 = 使用者實際看到的畫面，最貼近「人工驗證」的前提。

用法：
    # 方式一：CIK + Accession Number（重用 pipeline 的 EDGAR 解析）
    python -m feedback.external_reference_validation.render_demo --cik 0000320193 --accession 0000320193-23-000106

    # 方式二：直接給主文件 HTML URL
    python -m feedback.external_reference_validation.render_demo --url https://www.sec.gov/Archives/edgar/data/.../aapl.htm

    # 可選：--out 輸出目錄、--dpi 圖片解析度、--max-pages 只轉前 N 頁（省時試跑）

依賴（已裝）：
    pip install playwright pymupdf
    playwright install chromium
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz  # PyMuPDF
from playwright.sync_api import sync_playwright

# 重用 pipeline 既有的 EDGAR metadata / URL 解析與 User-Agent
from src.models import FilingInput
from src.pipeline import Pipeline, USER_AGENT


def resolve_html_url(args: argparse.Namespace) -> tuple[str, str]:
    """回傳 (html_url, stem)。stem 用於輸出檔名。"""
    if args.url:
        return args.url, "filing"

    pipeline = Pipeline()
    metadata, html_url = pipeline._resolve_input(
        FilingInput(cik=args.cik, accession_number=args.accession)
    )
    acc_clean = metadata.accession_number.replace("-", "")
    stem = f"{metadata.cik}_{metadata.fiscal_year_end}_{acc_clean}"
    print(f"[resolve] {metadata.company_name} → {html_url}")
    return html_url, stem


def fetch_html(html_url: str) -> bytes:
    """用 requests + SEC User-Agent 抓主文件 HTML（單一請求，不會觸發 SEC 速率封鎖）。"""
    import requests

    resp = requests.get(
        html_url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=60,
    )
    resp.raise_for_status()
    print(f"[fetch] HTML {len(resp.content):,} bytes")
    return resp.content


def render_html_to_pdf(html_url: str, html_bytes: bytes, pdf_path: Path) -> None:
    """
    用 headless Chromium 把已抓好的 HTML 渲染成真實分頁 PDF。

    為何不讓 Chromium 直接 goto(html_url)：
    瀏覽器會對 HTML + 所有子資源（圖片 / favicon）併發大量請求，觸發 SEC
    fair-access 速率限制 → 回傳「Undeclared Automated Tool」封鎖頁。
    這裡改用 route 攔截：主文件用我們 requests 抓到的 bytes 餵入，其餘子資源
    直接 abort（不再打 SEC）。代價是圖片 / logo 不顯示，但文字版面忠實重現，
    對「VLM 抓 item 首尾行 / 頁碼對照」已足夠。
    """
    print(f"[render] 渲染 {html_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        def handle(route, request):
            if request.url.rstrip("/") == html_url.rstrip("/"):
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=html_bytes,
                )
            else:
                route.abort()

        page.route("**/*", handle)
        # 保留真實 URL，讓任何剩餘的相對連結錨點計算正確；子資源已被 abort
        page.goto(html_url, wait_until="load", timeout=120_000)
        # page.pdf 只在 headless Chromium 可用。
        # prefer_css_page_size=True：尊重文件自己的 @page 尺寸，而非強制 Letter。
        # SEC iXBRL 文件多半自帶頁面尺寸與分頁，強制套 Letter+margin 會把一個版面頁
        # 拆成多頁、產生半空白頁，且 PDF 頁序對不上文件印製頁碼。用文件自身尺寸後，
        # PDF 分頁貼近原本的印製頁。
        page.pdf(
            path=str(pdf_path),
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()
    print(f"[render] PDF 已存 → {pdf_path}")


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int, max_pages: int | None) -> list[Path]:
    """把 PDF 每頁轉成 PNG。回傳產生的圖片路徑清單。"""
    doc = fitz.open(pdf_path)
    total = len(doc)
    n = total if max_pages is None else min(max_pages, total)
    print(f"[convert] PDF 共 {total} 頁，轉前 {n} 頁，DPI={dpi}")

    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    zoom = dpi / 72.0  # PDF 原生 72 DPI
    matrix = fitz.Matrix(zoom, zoom)
    paths: list[Path] = []
    for i in range(n):
        pix = doc.load_page(i).get_pixmap(matrix=matrix)
        # 1-based 命名，與「頁碼」直覺對齊
        png_path = pages_dir / f"page_{i + 1:03d}.png"
        pix.save(png_path)
        paths.append(png_path)
    doc.close()
    print(f"[convert] {len(paths)} 張 PNG 已存 → {pages_dir}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC 10-K HTML → PDF → 每頁 PNG")
    parser.add_argument("--url", help="主文件 HTML URL")
    parser.add_argument("--cik", help="CIK（搭配 --accession）")
    parser.add_argument("--accession", help="Accession Number（搭配 --cik）")
    parser.add_argument(
        "--out",
        default="feedback/external_reference_validation/out",
        help="輸出根目錄（預設 feedback/external_reference_validation/out）",
    )
    parser.add_argument("--dpi", type=int, default=150, help="PNG 解析度（預設 150）")
    parser.add_argument("--max-pages", type=int, default=None, help="只轉前 N 頁（試跑用）")
    args = parser.parse_args()

    if not args.url and not (args.cik and args.accession):
        parser.error("需提供 --url，或同時提供 --cik 與 --accession")

    html_url, stem = resolve_html_url(args)

    out_dir = Path(args.out) / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{stem}.pdf"

    html_bytes = fetch_html(html_url)
    render_html_to_pdf(html_url, html_bytes, pdf_path)
    pdf_to_pngs(pdf_path, out_dir, dpi=args.dpi, max_pages=args.max_pages)

    print(f"\n✅ 完成。輸出位於：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
