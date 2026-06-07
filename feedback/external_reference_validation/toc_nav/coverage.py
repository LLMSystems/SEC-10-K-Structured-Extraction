"""
Tier 2 toc_nav — 覆蓋率檢查：TOC(VLM) + 頁尾頁碼 + 標題微調 → item→渲染頁，對照已知 start_page。

驗這套「獨立導航」在 5 份上是否通用：
  1. VLM 掃前幾頁抓 TOC（item→印刷頁碼）。
  2. 頁尾印刷頁碼 → 渲染索引（deterministic）。
  3. 用 Item N 標題在 base 附近微調。
  4. 對照 dataset 已知 start_page，報完全命中 / ±1。

用法：python -m tier2.toc_nav.coverage
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz

from feedback.external_reference_validation.map_offsets import load_page_texts
from feedback.external_reference_validation.toc_nav.toc_extract import extract_toc
from feedback.external_reference_validation.vlm_first_last import DATASET


LABELS = ["GDC_2023", "NFLX_2025", "RELL_2025", "TSLA_2023", "WMT_2026"]


def _page_candidates(pdf_path: Path) -> list[set[int]]:
    """每頁收集所有「可能是頁碼」的候選整數：字串尾端數字 + 底部 22% 的純數字 word。"""
    doc = fitz.open(pdf_path)
    cands = []
    for i in range(len(doc)):
        pg = doc.load_page(i)
        h = pg.rect.height
        s: set[int] = set()
        m = re.search(r"(\d{1,3})\s*$", pg.get_text().strip())
        if m:
            s.add(int(m.group(1)))
        for w in pg.get_text("words"):
            t = w[4].strip()
            if t.isdigit() and len(t) <= 3 and w[1] > 0.78 * h:
                s.add(int(t))
        cands.append(s)
    doc.close()
    return cands


def footer_map(pdf_path: Path) -> dict[int, int]:
    """
    印刷頁碼 → 渲染索引，用**單調序列擬合**而非單頁猜測。
    頁碼是一條隨渲染頁緩慢遞增的整數序列（offset 會漂、偶爾同頁），競爭數字（表格值/年份）
    因不連續被排除。先找一個「連續兩頁 v, v+1」的錨點定 offset，再前後雙向沿著漂移的 offset 取最近候選。
    """
    cands = _page_candidates(pdf_path)
    n = len(cands)
    p2r: dict[int, int] = {}

    # 錨點：第一個「v 在本頁、v+1 在下一頁」的連續對，定起點 offset = 頁碼 - 渲染索引
    anchor_off = start = None
    for i in range(n - 1):
        for v in sorted(cands[i]):
            if v + 1 in cands[i + 1]:
                anchor_off, start = v - i, i
                break
        if start is not None:
            break
    if start is None:
        return p2r

    off = anchor_off

    def walk(rng, tol):
        nonlocal off
        for i in rng:
            if not cands[i]:
                continue
            exp = i + off
            v = min(cands[i], key=lambda x: abs(x - exp))
            if abs(v - exp) <= tol:
                off = v - i                 # 跟著緩慢漂移更新
                p2r.setdefault(v, i + 1)

    walk(range(start, n), tol=3)            # 往後
    off = anchor_off                        # 重設回錨點 offset
    walk(range(start - 1, -1, -1), tol=2)   # 往前
    return p2r


def printed_to_render(p2r: dict[int, int], printed: int) -> int | None:
    """把印刷頁碼換成渲染索引。footer_map 是錨點，缺的頁用相鄰錨點內插/外插（offset 連續）。"""
    if not p2r:
        return None
    if printed in p2r:
        return p2r[printed]
    keys = sorted(p2r)
    below = [k for k in keys if k < printed]
    above = [k for k in keys if k > printed]
    if below and above:
        lo, hi = below[-1], above[0]
        frac = (printed - lo) / (hi - lo)
        return round(p2r[lo] + frac * (p2r[hi] - p2r[lo]))
    if below:
        return p2r[below[-1]] + (printed - below[-1])   # 往後外插（offset 不變）
    return p2r[above[0]] - (above[0] - printed)          # 往前外插


def heading_page(page_texts: list[str], n: str, base: int, win: int = 2) -> int | None:
    """在 base 附近窗口找含 'item n' 標題（近頁首）的渲染頁。"""
    pat = re.compile(r"\bitem\s+" + re.escape(n.lower()) + r"\b")
    for r in range(max(base - win, 1), min(base + win, len(page_texts)) + 1):
        if pat.search(page_texts[r - 1][:200]):
            return r
    return None


def navigate(label: str, model: str) -> dict:
    folder = DATASET / label
    pt = load_page_texts(folder / f"{label}.pdf")
    toc = extract_toc(label, model)["item_page"]
    p2r = footer_map(folder / f"{label}.pdf")
    nav = {}
    for it, pp in toc.items():
        base = printed_to_render(p2r, pp)
        if base is not None:
            nav[it] = heading_page(pt, it, base) or base
    return {"toc": toc, "p2r": p2r, "nav": nav, "pages": len(pt)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    args = ap.parse_args()

    rows = []
    for label in LABELS:
        r = navigate(label, args.model)
        known = {it["item"]: it["start_page"]
                 for it in json.loads((DATASET / label / f"{label}_pages.json").read_text("utf-8"))["items"]}
        common = [it for it in known if it in r["nav"]]
        exact = sum(1 for it in common if r["nav"][it] == known[it])
        within1 = sum(1 for it in common if abs(r["nav"][it] - known[it]) <= 1)
        rows.append({
            "label": label, "pages": r["pages"], "toc_items": len(r["toc"]),
            "footer_pages": len(r["p2r"]), "known": len(known), "matched": len(common),
            "exact": exact, "within1": within1,
        })
        print(f"\n=== {label} ===")
        print(f"  TOC 抓到 item: {len(r['toc'])}　頁尾有印刷頁碼的頁: {len(r['p2r'])}/{r['pages']}")
        print(f"  對照已知 start_page: {len(common)}/{len(known)} 可比"
              f"　完全命中 {exact}　+/-1 頁內 {within1}")
        if len(r["toc"]) == 0:
            print("  [WARN] 沒抓到 TOC")
        if len(r["p2r"]) == 0:
            print("  [WARN] 渲染頁無頁尾印刷頁碼 -> 此招不適用")

    print("\n" + "=" * 64)
    print(f"{'filing':<11} | toc項 | 頁尾頁 | 可比 | 完全 | +/-1頁")
    print("-" * 64)
    for r in rows:
        print(f"{r['label']:<11} | {r['toc_items']:>4} | {r['footer_pages']:>4}/{r['pages']:<3}"
              f"| {r['matched']:>3}/{r['known']:<2}| {r['exact']:>3} | {r['within1']:>3}")
    te = sum(r["exact"] for r in rows); tw = sum(r["within1"] for r in rows)
    tm = sum(r["matched"] for r in rows)
    print("-" * 64)
    print(f"合計：完全命中 {te}/{tm}　+/-1 頁內 {tw}/{tm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
