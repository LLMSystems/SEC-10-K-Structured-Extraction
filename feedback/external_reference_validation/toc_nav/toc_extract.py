"""
Tier 2 — 用 VLM 從渲染頁抽 TOC（item → 印刷頁碼）。prototype。

做法（依討論）：
  - TOC 可能跨頁 → 掃前 N 頁，每頁都丟給 VLM。
  - 一律 VLM、不 regex 先行。prompt：是 TOC 就逐列回 `item | 頁碼`，不是就回 NONE。
  - 彙整所有「非 NONE」頁的列 → 文件宣告的 item→印刷頁碼。

用法：
    python -m tier2.toc_nav.toc_extract --label GDC_2023 --model google/gemini-3-flash-preview
"""

from __future__ import annotations

import argparse
import re

from feedback.external_reference_validation.vlm_reader import VLMImageReader
from feedback.external_reference_validation.vlm_first_last import DATASET, _png, clean_response

PROMPT = (
    "You are shown one page from a SEC 10-K filing.\n"
    "If this page is part of the Table of Contents (it lists Items with their page numbers), "
    "output one line per entry in exactly this form:\n"
    "  <item> | <page>\n"
    "where <item> is the Item identifier exactly as printed (e.g. 1, 1A, 7A) and <page> is its "
    "printed page number. Include every Item row visible on this page, in order. Output ONLY these lines.\n"
    "If this page is NOT a table of contents, output exactly: NONE"
)

_ROW = re.compile(r"^\s*(?:item\s*)?([0-9]{1,2}[A-Z]?)\.?\s*[|｜:]\s*(\d{1,3})\s*$", re.I)


def parse_rows(text: str) -> list[tuple[str, int]]:
    rows = []
    for ln in (text or "").splitlines():
        m = _ROW.match(ln)
        if m:
            rows.append((m.group(1).upper(), int(m.group(2))))
    return rows


def extract_toc(label: str, model: str, scan: int = 7, force: bool = False) -> dict:
    folder = DATASET / label
    reader = VLMImageReader(
        model=model,
        cache_dir="feedback/external_reference_validation/vlm_cache",
    )
    toc_pages, item_page = [], {}
    for pno in range(1, scan + 1):
        img = _png(folder, pno)
        if not img.exists():
            break
        raw = clean_response(reader.read_image(img, prompt=PROMPT, max_tokens=2048, force=force)) or ""
        rows = parse_rows(raw)
        is_none = raw.strip().upper().startswith("NONE")
        print(f"  p{pno:>2}: {'NONE' if is_none and not rows else f'{len(rows)} rows'}"
              + (f"  {rows[:3]}{'...' if len(rows) > 3 else ''}" if rows else ""))
        if rows:
            toc_pages.append(pno)
            for it, pg in rows:
                item_page.setdefault(it, pg)  # 跨頁時保留首見
    return {"label": label, "model": model, "toc_pages": toc_pages, "item_page": item_page}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="GDC_2023")
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--scan", type=int, default=7, help="掃前幾頁找 TOC")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    print(f"[toc] {args.label}  model={args.model}  掃前 {args.scan} 頁")
    res = extract_toc(args.label, args.model, args.scan, args.force)
    print(f"\nTOC 頁: {res['toc_pages']}")
    print(f"item → 印刷頁碼（{len(res['item_page'])} 項）:")
    for it, pg in res["item_page"].items():
        print(f"  Item {it:>3} → {pg}")
    # 單調性自我檢查
    pgs = list(res["item_page"].values())
    mono = all(b >= a for a, b in zip(pgs, pgs[1:]))
    print(f"\n單調性（頁碼不遞減）：{'✓' if mono else '✗ 有倒退，TOC 讀取可疑'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
