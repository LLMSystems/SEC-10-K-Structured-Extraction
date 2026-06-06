"""
Tier 2 — 批次：全 34 份 GT 渲染 PDF + item→PDF頁碼 對照，並依命中品質分類。

對每份 ground truth：
  1. 解析 EDGAR URL → 渲染 PDF（快取於 feedback/external_reference_validation/out/<stem>.pdf，已存在則略過）。
  2. 用 content_text 比對，把每個 extracted item 對到 PDF 頁。
  3. 統計 exact / fuzzy / miss 與頁碼單調性違規。
  4. 分類：
       - clean ：所有 extracted item 都 exact 命中 且 0 單調違規（完全對應）
       - flawed：有 fuzzy / miss / 單調違規 / 渲染失敗（小瑕疵，需人工看）

輸出：
  feedback/external_reference_validation/report/clean/<TICKER>_<YEAR>.json
  feedback/external_reference_validation/report/flawed/<TICKER>_<YEAR>.json
  feedback/external_reference_validation/report/summary.md
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from src.models import FilingInput
from src.pipeline import Pipeline

from feedback.external_reference_validation.render_demo import fetch_html, render_html_to_pdf
from feedback.external_reference_validation.map_offsets import load_page_texts, body_snippet, find_page

GT_ROOT = Path("eval_datasets/ground_truth")
PDF_DIR = Path("feedback/external_reference_validation/out")
REPORT_DIR = Path("feedback/external_reference_validation/report")


def evaluate_filing(gt_json: Path, pipeline: Pipeline) -> dict:
    """渲染 + 對照一份 filing，回傳結果 dict（含分類）。"""
    data = json.loads(gt_json.read_text(encoding="utf-8"))
    fi = data.get("filing_info", data)
    cik = fi.get("cik")
    accession = fi.get("accession_number")
    ticker = gt_json.parent.parent.name
    year = gt_json.parent.name
    label = f"{ticker}_{year}"

    result: dict = {
        "label": label, "ticker": ticker, "year": year,
        "cik": cik, "accession_number": accession,
        "company_name": fi.get("company_name"),
    }

    # 1) 解析 URL + 渲染 PDF（快取）
    try:
        metadata, html_url = pipeline._resolve_input(
            FilingInput(cik=cik, accession_number=accession)
        )
        acc_clean = metadata.accession_number.replace("-", "")
        stem = f"{metadata.cik}_{metadata.fiscal_year_end}_{acc_clean}"
        pdf_path = PDF_DIR / stem / f"{stem}.pdf"
        if not pdf_path.exists():
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            html_bytes = fetch_html(html_url)
            render_html_to_pdf(html_url, html_bytes, pdf_path)
        result["pdf"] = str(pdf_path)
    except Exception as e:
        result.update(category="flawed", error=f"render: {e}",
                      traceback=traceback.format_exc())
        return result

    # 2) 對照每個 extracted item → PDF 頁
    page_texts = load_page_texts(pdf_path)
    result["pdf_pages"] = len(page_texts)

    # 順序搜尋：item 有序，從前一個命中頁往後找 → 天然跳過最前面的 TOC。
    # 真正值得人看的瑕疵 = miss / fuzzy / out_of_order（只在前面找得到 = 內容位置異常）。
    items_out = []
    n_exact = n_fuzzy = n_miss = out_of_order = 0
    last_page = 0  # 1-based；0 代表還沒命中過
    for it in data.get("items", []):
        if it.get("status") != "extracted":
            continue
        snip = body_snippet(it.get("content_text") or "", it.get("item_title") or "")
        # 允許與前一個同頁（多個 item 可同頁）→ start_from = last_page - 1
        start_from = max(last_page - 1, 0)
        page, how = find_page(snip, page_texts, start_from=start_from)

        flag = None
        if page is None:
            # 往後找不到 → 看是否「只在前面找得到」（順序異常）還是全文 miss
            page_any, how_any = find_page(snip, page_texts, start_from=0)
            if page_any is not None:
                out_of_order += 1
                flag = f"順序異常：只在 p{page_any} 找到（早於前一個 p{last_page}）"
                page, how = page_any, how_any  # 仍記錄找到的頁，供人工判讀
            else:
                n_miss += 1
        else:
            if how == "exact":
                n_exact += 1
            else:
                n_fuzzy += 1
            last_page = page

        items_out.append({
            "item": it.get("item_number"), "page": page,
            "match": how, "flag": flag, "snippet": snip[:50],
        })

    n_extracted = len(items_out)
    result.update(
        n_extracted=n_extracted, n_exact=n_exact, n_fuzzy=n_fuzzy,
        n_miss=n_miss, out_of_order=out_of_order, items=items_out,
    )

    # 3) 分類：完全對應 = 所有 extracted item 都 exact 命中、無 fuzzy/miss/順序異常
    clean = (n_miss == 0 and n_fuzzy == 0 and out_of_order == 0 and n_extracted > 0)
    result["category"] = "clean" if clean else "flawed"
    return result


def main() -> int:
    pipeline = Pipeline()
    gt_files = sorted(GT_ROOT.glob("*/*/*.json"))
    print(f"[batch] 找到 {len(gt_files)} 份 GT\n")

    (REPORT_DIR / "clean").mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "flawed").mkdir(parents=True, exist_ok=True)

    rows = []
    for i, gt in enumerate(gt_files, 1):
        label = f"{gt.parent.parent.name}_{gt.parent.name}"
        print(f"[{i}/{len(gt_files)}] {label} …", flush=True)
        res = evaluate_filing(gt, pipeline)
        out = REPORT_DIR / res["category"] / f"{res['label']}.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(res)
        tag = "✅" if res["category"] == "clean" else "⚠"
        if "error" in res:
            print(f"      {tag} flawed (render error)")
        else:
            print(f"      {tag} {res['category']}: "
                  f"exact={res['n_exact']} fuzzy={res['n_fuzzy']} "
                  f"miss={res['n_miss']} ooo={res['out_of_order']} "
                  f"/ {res['n_extracted']} items, {res['pdf_pages']}p")

    # summary.md
    clean = [r for r in rows if r["category"] == "clean"]
    flawed = [r for r in rows if r["category"] == "flawed"]
    lines = [
        "# Tier 2 item→PDF頁碼 對照：批次結果", "",
        f"- 總數：{len(rows)}",
        f"- ✅ 完全對應 clean：{len(clean)}",
        f"- ⚠ 有小瑕疵 flawed：{len(flawed)}", "",
        "## ⚠ flawed 明細", "",
        "| filing | 問題 |", "|---|---|",
    ]
    for r in flawed:
        if "error" in r:
            issue = f"渲染失敗：{r['error']}"
        else:
            parts = []
            if r["n_miss"]: parts.append(f"miss={r['n_miss']}")
            if r["n_fuzzy"]: parts.append(f"fuzzy={r['n_fuzzy']}")
            if r["out_of_order"]: parts.append(f"順序異常={r['out_of_order']}")
            issue = ", ".join(parts) or "—"
        lines.append(f"| {r['label']} | {issue} |")
    (REPORT_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n[batch] clean={len(clean)} flawed={len(flawed)}")
    print(f"[batch] 報告：{REPORT_DIR}/summary.md、clean/、flawed/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
