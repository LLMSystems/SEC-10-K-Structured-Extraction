"""
Tier 2 — 把 5 份高信心 filing 整理成最終資料集。

每份一個資料夾，含：
  <label>.pdf            渲染後的分頁 PDF（VLM / 頁碼對照的來源）
  <label>_pages.json     每個 item 的起始頁 ~ 結束頁（PDF 頁序）
  <label>_content.json   內文：每個 item 的 content_text / char_range / status（人工標註 GT）
  pages/page_NNN.png     每頁 PNG @150dpi（餵 VLM 用；長邊 1650 ≈ Claude vision 上限）

來源：
  PDF       ← feedback/external_reference_validation/out/<stem>/<stem>.pdf            （high_confidence 報告的 pdf 欄位）
  pages     ← feedback/external_reference_validation/report/page_ranges/<label>.json
  content   ← eval_datasets/ground_truth/<ticker>/<year>/*.json

用法：
    python -m feedback.external_reference_validation.build_dataset
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
from pathlib import Path

import fitz  # PyMuPDF

HC_DIR = Path("feedback/external_reference_validation/report/high_confidence")
RANGES_DIR = Path("feedback/external_reference_validation/report/page_ranges")
DATASET_DIR = Path("feedback/external_reference_validation/dataset")
GT_GLOB = "eval_datasets/ground_truth/{ticker}/{year}/*.json"
PNG_DPI = 150  # 長邊 1650 ≈ Claude vision 上限；整頁輸入最佳，再高會被 API 縮回


def render_pages(pdf_path: Path, pages_dir: Path, dpi: int) -> int:
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for i in range(len(doc)):
        doc.load_page(i).get_pixmap(matrix=mat).save(pages_dir / f"page_{i + 1:03d}.png")
    n = len(doc)
    doc.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-png", action="store_true", help="略過每頁 PNG（只放 pdf+json）")
    ap.add_argument("--dpi", type=int, default=PNG_DPI, help=f"PNG 解析度（預設 {PNG_DPI}）")
    args = ap.parse_args()

    reports = [json.loads(Path(p).read_text(encoding="utf-8"))
               for p in sorted(glob.glob(str(HC_DIR / "*.json")))
               if not p.endswith("_selection.json")]

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    index = []

    for r in reports:
        label = r["label"]
        out = DATASET_DIR / label
        out.mkdir(parents=True, exist_ok=True)

        # 1) PDF
        shutil.copy(r["pdf"], out / f"{label}.pdf")

        # 2) 頁碼範圍 json
        shutil.copy(RANGES_DIR / f"{label}.json", out / f"{label}_pages.json")

        # 3) 內文 json（人工標註 GT）
        gt_src = glob.glob(GT_GLOB.format(ticker=r["ticker"], year=r["year"]))[0]
        shutil.copy(gt_src, out / f"{label}_content.json")

        # 4) 每頁 PNG @dpi
        if not args.no_png:
            render_pages(out / f"{label}.pdf", out / "pages", args.dpi)

        ranges = json.loads((RANGES_DIR / f"{label}.json").read_text(encoding="utf-8"))
        index.append({
            "label": label, "company": r.get("company_name"),
            "cik": r.get("cik"), "accession_number": r.get("accession_number"),
            "pdf_pages": r.get("pdf_pages"), "n_items": len(ranges["items"]),
        })
        extra = "" if args.no_png else f" + {r.get('pdf_pages')} pngs"
        print(f"  ✓ {label}: pdf + pages + content{extra}")

    (DATASET_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # README
    lines = [
        "# Tier 2 頁碼資料集（5 份高信心 10-K）", "",
        "每個資料夾一份 filing，含三個檔：", "",
        "- `<label>.pdf` — 渲染後分頁 PDF（VLM / 頁碼對照來源）",
        "- `<label>_pages.json` — 每個 item 的 `start_page` ~ `end_page`（PDF 頁序）",
        "- `<label>_content.json` — 內文：每個 item 的 `content_text` / `char_range` / `status`（人工標註 GT）",
        "- `pages/page_NNN.png` — 每頁 PNG @150dpi（長邊 1650 ≈ Claude vision 上限；整頁輸入最佳）",
        "", "## 內容", "",
        "| filing | 公司 | items | PDF 頁數 |", "|---|---|---|---|",
    ]
    for e in index:
        lines.append(f"| {e['label']} | {e['company']} | {e['n_items']} | {e['pdf_pages']} |")
    (DATASET_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n資料集 → {DATASET_DIR}/（index.json + README.md + {len(index)} 份）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
