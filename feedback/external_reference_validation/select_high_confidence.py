"""
Tier 2 — 從 clean 的 filing 中挑出「信心最高」的 N 份頁碼 GT。

光是「全 exact 命中」還不夠。更強的信心訊號是 **唯一性**：
每個 item 的 body 片段在整份 PDF 只出現在「一頁」→ 頁碼對應不可能挑錯
（沒有 TOC / 交叉引用 / 重複措辭造成的跨頁歧義）。

信心分數 = 唯一命中的 item 數 / extracted item 數（1.0 = 每個 item 都唯一定位）。
依分數排序，取前 N 份，複製到 feedback/external_reference_validation/report/high_confidence/。

用法：
    python -m feedback.external_reference_validation.select_high_confidence --n 10
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
from pathlib import Path

from feedback.external_reference_validation.map_offsets import load_page_texts, best_snippet, find_page

CLEAN_DIR = Path("feedback/external_reference_validation/report/clean")
OUT_DIR = Path("feedback/external_reference_validation/report/high_confidence")
GT_GLOB = "eval_datasets/ground_truth/{ticker}/{year}/*.json"


def score_filing(report: dict) -> dict:
    """重算每個 item 的唯一性，回傳信心統計。"""
    ticker, year = report["ticker"], report["year"]
    gt_path = glob.glob(GT_GLOB.format(ticker=ticker, year=year))[0]
    gt = json.loads(Path(gt_path).read_text(encoding="utf-8"))
    page_texts = load_page_texts(Path(report["pdf"]))

    n_items = n_unique = 0
    min_gap = None  # 與最近鄰 item 的最小頁距（頁距大 = 邊界更不易混淆）
    last_page = 0
    detail = []
    for it in gt.get("items", []):
        if it.get("status") != "extracted":
            continue
        n_items += 1
        # 在前幾個候選行中挑命中頁數最少的，降低跨頁歧義
        snip, n_hits = best_snippet(
            it.get("content_text") or "", it.get("item_title") or "", page_texts)
        page, _ = find_page(snip, page_texts, start_from=max(last_page - 1, 0))
        if n_hits == 1:
            n_unique += 1
        if page is not None:
            if last_page:
                gap = page - last_page
                min_gap = gap if min_gap is None else min(min_gap, gap)
            last_page = page
        detail.append({"item": it["item_number"], "page": page, "hit_pages": n_hits})

    return {
        "label": report["label"], "ticker": ticker, "year": year,
        "company_name": report.get("company_name"),
        "pdf_pages": report.get("pdf_pages"),
        "n_items": n_items, "n_unique": n_unique,
        "confidence": round(n_unique / n_items, 4) if n_items else 0.0,
        "min_page_gap": min_gap,
        "detail": detail,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="挑幾份")
    ap.add_argument("--diverse", action="store_true",
                    help="公司多樣優先：每家公司只取信心最高的一份，再取前 N 家")
    args = ap.parse_args()

    reports = [json.loads(Path(p).read_text(encoding="utf-8"))
               for p in sorted(glob.glob(str(CLEAN_DIR / "*.json")))]
    scored = [score_filing(r) for r in reports]

    # 排序：信心(唯一率)降冪 → item 數降冪(覆蓋更全) → 頁數降冪
    scored.sort(key=lambda s: (s["confidence"], s["n_items"], s["pdf_pages"] or 0),
                reverse=True)

    if args.diverse:
        # 每家公司只保留信心最高的一份（scored 已排序，首見即最高）
        seen: set[str] = set()
        deduped = []
        for s in scored:
            if s["ticker"] in seen:
                continue
            seen.add(s["ticker"])
            deduped.append(s)
        scored = deduped

    print(f"{'rank':>4} | {'filing':<12} | conf  | unique/items | pages | min_gap")
    print("-" * 70)
    for i, s in enumerate(scored, 1):
        marker = " ←選" if i <= args.n else ""
        print(f"{i:>4} | {s['label']:<12} | {s['confidence']:.2f} | "
              f"{s['n_unique']:>2}/{s['n_items']:<2}        | {s['pdf_pages']:>4}  | "
              f"{s['min_page_gap']}{marker}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob("*.json"):
        f.unlink()
    chosen = scored[: args.n]
    for s in chosen:
        src = CLEAN_DIR / f"{s['label']}.json"
        shutil.copy(src, OUT_DIR / f"{s['label']}.json")
    (OUT_DIR / "_selection.json").write_text(
        json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")

    companies = sorted({s["ticker"] for s in chosen})
    print(f"\n挑出 {len(chosen)} 份 → {OUT_DIR}")
    print(f"涵蓋公司：{', '.join(companies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
