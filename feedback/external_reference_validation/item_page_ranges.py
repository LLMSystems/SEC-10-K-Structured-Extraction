"""
Tier 2 — 抓每個 item 的「起始頁 ~ 結束頁」(PDF 頁序)。

對 high_confidence/ 選出的 filing：
  起始頁：item 內容**開頭**的獨特片段命中的頁。
  結束頁：item 內容**結尾**的獨特片段命中的頁（從起始頁往後找）。

順序搜尋（item 有序）天然跳過 TOC；並做三項自洽檢查：
  - start <= end                （item 不可能結束在開始之前）
  - end[i] <= start[i+1]         （不可越過下一個 item 的起點）
  - 起訖錨點是否唯一命中         （唯一 = 頁碼零歧義）

輸出 feedback/external_reference_validation/report/page_ranges/<label>.json，並印出可讀表格。

用法：
    python -m feedback.external_reference_validation.item_page_ranges                 # 全部 high_confidence
    python -m feedback.external_reference_validation.item_page_ranges --label WMT_2026
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path

from rapidfuzz import fuzz

from feedback.external_reference_validation.map_offsets import load_page_texts, head_snippet, tail_snippet, find_page, norm

HC_DIR = Path("feedback/external_reference_validation/report/high_confidence")
OUT_DIR = Path("feedback/external_reference_validation/report/page_ranges")
GT_GLOB = "eval_datasets/ground_truth/{ticker}/{year}/*.json"
_TAG = re.compile(r"<[^>]+>")


def _heading_at_top(page_text: str, item_number: str, within: int = 120) -> bool:
    """下一個 item 的標題是否落在該頁開頭（容忍頁首 running header）→ 判斷是否頁首換頁。"""
    pat = re.compile(r"\bitem\s+" + re.escape(item_number.lower()) + r"\b")
    m = pat.search(page_text)
    return m is not None and m.start() < within


def _body_text(content_text: str) -> str:
    """GT item 內容去 HTML + 正規化，便於與 PDF 頁文字直接比對。"""
    return norm(_TAG.sub(" ", content_text))


def _continued_before_next_heading(
    current_text: str,
    next_page_text: str,
    next_item: str,
    min_alpha: int = 80,
    window: int = 350,
    threshold: int = 88,
) -> bool:
    """
    下一個 extracted item 所在頁，若其標題前還有一大段文字，判斷那段是否其實屬於「目前 item 的續文」。

    這是修正 tail_snippet 常見的低估情況：
      - GT 的最後幾行落在「下一頁最上面」
      - 但 tail_snippet 只抓到前一頁較早的一條唯一命中句，導致 end_page 少 1 頁

    做法：
      1. 先找到 next_item 標題在頁上的位置；
      2. 取其前面的文字（也就是「真正換到 next_item 之前」那塊）；
      3. 用這塊最後 window 字與 current_text 尾段做 fuzzy，比對夠高才視為續文。
    """
    pat = re.compile(r"\bitem\s+" + re.escape(next_item.lower()) + r"\b")
    m = pat.search(next_page_text)
    if m is None or m.start() <= 0:
        return False

    prefix = next_page_text[:m.start()].strip()
    if sum(ch.isalpha() for ch in prefix) < min_alpha:
        return False

    probe = prefix[-window:] if len(prefix) > window else prefix
    tail_zone = current_text[-max(window * 3, 900):]
    score = max(fuzz.partial_ratio(probe, tail_zone), fuzz.ratio(probe, tail_zone))
    return score >= threshold


def compute_ranges(report: dict) -> dict:
    ticker, year = report["ticker"], report["year"]
    gt = json.loads(Path(glob.glob(GT_GLOB.format(ticker=ticker, year=year))[0])
                    .read_text(encoding="utf-8"))
    page_texts = load_page_texts(Path(report["pdf"]))

    # ── 第一輪：每個 extracted item 的起始頁（內容開頭的獨特錨點）──
    items = []
    last_start = 0
    for it in gt.get("items", []):
        if it.get("status") != "extracted":
            continue
        ct = it.get("content_text") or ""
        title = it.get("item_title") or ""
        hsnip, hhits = head_snippet(ct, title, page_texts)
        start_page, _ = find_page(hsnip, page_texts, start_from=max(last_start - 1, 0))
        tsnip, thits = tail_snippet(ct, title, page_texts)  # 結尾錨點（給最後一個 item 與下界）
        if start_page:
            last_start = start_page
        items.append({
            "item": it["item_number"], "content_text": ct,
            "start_page": start_page, "start_unique": hhits == 1,
            "_tsnip": tsnip, "_tunique": thits == 1,
        })

    # ── 第二輪：結束頁 ──
    # 主錨點：item「自己的結尾文字」落在哪頁（從自己起點往後找）——最精準、不受後續 item 影響。
    # 上界：下一個 extracted item 起點回推得的頁（防 tail 錨點不可靠時過衝）。
    # 取兩者較小：tail 錨點唯一命中且不超過上界時採用（修掉兩個 bug：
    #   ① 中間夾 by_reference item 時，用「下一個 extracted 起點」會 overshoot；
    #   ② 下一個 item 標題非文字（如 Item 8 審計報告頁）時，頁首偵測失效不回退）。
    # 另補第三種低估：tail_snippet 可能抓到前一頁較早的唯一句，但 item 真正尾段其實續到下一頁、
    # 且下一個 item 標題在該頁中段以下。這時需把 end_page 升到 next_based。
    for idx, cur in enumerate(items):
        sp = cur["start_page"]
        from_p = max((sp or 1) - 1, 0)
        tail_page, _ = find_page(cur["_tsnip"], page_texts, start_from=from_p) if cur["_tsnip"] else (None, "")

        next_based = None
        if idx + 1 < len(items):
            nxt = items[idx + 1]
            np_ = nxt["start_page"]
            if np_:
                fresh = _heading_at_top(page_texts[np_ - 1], nxt["item"])
                next_based = (np_ - 1) if fresh else np_

        # tail 唯一命中且未超過上界 → 先採用；但若下一個 item 所在頁的標題前文字其實還像目前 item 的
        # 續文，代表 tail_page 低估了跨頁尾段，應升到 next_based。
        if tail_page and cur["_tunique"] and (next_based is None or tail_page <= next_based):
            end_page = tail_page
            if (
                next_based is not None
                and tail_page < next_based
                and _continued_before_next_heading(_body_text(cur["content_text"]),
                                                  page_texts[next_based - 1], nxt["item"])
            ):
                end_page = next_based
        else:
            end_page = next_based if next_based is not None else tail_page

        # 往回跳過尾端空白頁（item 之間的分頁空白不該算進前一個 item）
        if sp and end_page:
            while end_page > sp and not page_texts[end_page - 1].strip():
                end_page -= 1
            end_page = max(end_page, sp)
        cur["end_page"] = end_page

    # 自洽檢查 + 清掉內部欄位
    warnings = []
    for a, b in zip(items, items[1:]):
        if a["end_page"] and b["start_page"] and a["end_page"] > b["start_page"]:
            warnings.append(
                f"item {a['item']} 結束 p{a['end_page']} > item {b['item']} 起始 p{b['start_page']}")
    clean_items = [{
        "item": it["item"], "start_page": it["start_page"], "end_page": it["end_page"],
        "start_unique": it["start_unique"], "end_unique": it["_tunique"],
    } for it in items]

    return {
        "label": report["label"], "pdf_pages": report["pdf_pages"],
        "items": clean_items, "warnings": warnings,
    }


def print_table(res: dict) -> None:
    print(f"\n=== {res['label']}  ({res['pdf_pages']}p) ===")
    print(f"{'item':>5} | {'pages':>9} | uniq(起/訖)")
    print("-" * 36)
    for it in res["items"]:
        rng = f"{it['start_page']}–{it['end_page']}"
        u = f"{'✓' if it['start_unique'] else '·'}/{'✓' if it['end_unique'] else '·'}"
        print(f"{it['item']:>5} | {rng:>9} | {u}")
    if res["warnings"]:
        print("  ⚠ " + "; ".join(res["warnings"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", help="只跑某一份（如 WMT_2026），預設全部")
    args = ap.parse_args()

    reports = [json.loads(Path(p).read_text(encoding="utf-8"))
               for p in sorted(glob.glob(str(HC_DIR / "*.json")))
               if not p.endswith("_selection.json")]
    if args.label:
        reports = [r for r in reports if r["label"] == args.label]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for r in reports:
        res = compute_ranges(r)
        (OUT_DIR / f"{res['label']}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print_table(res)

    print(f"\n輸出 → {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
