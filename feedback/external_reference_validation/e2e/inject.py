"""
Tier 2 端到端 §7.3：用 **TOC 獨立導航頁** + **只測 gate 放行 item** 重跑錯誤注入。

關閉原 §7.3 的「樂觀」asterisk：
  原版用 dataset(正確)頁 → 等於假設拿得到正確頁。
  這裡頁碼由 TOC 導航(獨立於被注入的內容)提供 → 模擬真實防禦：
  parser 截斷時導航不跟著錯，仍導到真實頁，截斷因此可偵測。

只汙染 GT 側、重用 e2e 已快取的 VLM 讀 → 零 API。運算子/嚴重度同 vlm_inject_eval。

用法：python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from feedback.external_reference_validation.vlm_reader import VLMImageReader
from feedback.external_reference_validation.vlm_first_last import (
    HEAD_PROMPT, body_text, strip_heading, tail_kind,
    clean_response, _png, WIN, DEFAULT_TH, DATASET,
)
from feedback.external_reference_validation.map_offsets import load_page_texts
from feedback.external_reference_validation.toc_nav.coverage import navigate
from feedback.external_reference_validation.toc_nav.toc_extract import extract_toc
from feedback.external_reference_validation.e2e.run import find_heading_page, _heading_top, TAIL2_PROMPT
from feedback.external_reference_validation.vlm_inject_eval import inj_head, inj_tail, SEVERITIES, HEAD_OPS, TAIL_OPS, _pr


LABELS = ["GDC_2023", "NFLX_2025", "RELL_2025", "TSLA_2023", "WMT_2026"]


def cached(reader: VLMImageReader, image: Path, prompt: str) -> str | None:
    """嚴格只讀快取（e2e 已產生 TOC 導航頁的讀），不打 API。"""
    key = reader.cache_key(image_sha=reader.image_sha256(image), prompt=prompt)
    e = reader._load_cache_entry(key)
    return clean_response(e["text"]) if e else None


def cached_multi(reader: VLMImageReader, images: list, prompt: str) -> str | None:
    """多圖版快取讀（tail 的 [ep-1, ep]）。"""
    joined = "+".join(reader.image_sha256(Path(p)) for p in images)
    key = reader.cache_key(image_sha=joined, prompt=prompt)
    e = reader._load_cache_entry(key)
    return clean_response(e["text"]) if e else None


def eval_filing(label: str, model: str, th: int) -> list[dict]:
    folder = DATASET / label
    pt = load_page_texts(folder / f"{label}.pdf")
    content = json.loads((folder / f"{label}_content.json").read_text("utf-8"))
    gt = {it["item_number"]: it for it in content["items"] if it.get("status") == "extracted"}
    order = [it["item_number"] for it in content["items"] if it.get("status") == "extracted"]
    ct_of = {k: (v.get("content_text") or "") for k, v in gt.items()}

    nav = navigate(label, model)["nav"]
    toc_pages = set(extract_toc(label, model)["toc_pages"])
    reader = VLMImageReader(
        model=model,
        cache_dir="feedback/external_reference_validation/vlm_cache",
    )
    seq = [n for n in order if n in nav]

    rows = []
    for i, num in enumerate(seq):
        ct = ct_of[num]
        bt = body_text(ct)
        if len(bt) < 20:
            continue
        title = gt[num].get("item_title", "")
        kind = tail_kind(ct)
        hp = find_heading_page(pt, num, title, nav[num], exclude=toc_pages)
        if hp is None:        # gate ✗（棄驗）→ 不納入偵測
            continue
        sp = hp
        prev_ct = ct_of.get(seq[i - 1], "") if i > 0 else ""

        # head
        ph = cached(reader, _png(folder, sp), HEAD_PROMPT.format(n=num, title=title))
        if ph:
            clean = _pr(strip_heading(bt, num, title)[:WIN], ph)
            for op in HEAD_OPS:
                for sev in SEVERITIES:
                    inj = _pr(inj_head(ct, num, title, op, sev, prev_ct), ph)
                    rows.append({"op": op, "sev": sev, "applicable": clean >= th,
                                 "detected": clean >= th and inj < th})

        # tail（prose 桶 + 有下一個 nav item）
        if kind == "prose" and i + 1 < len(seq) and nav.get(seq[i + 1]):
            nxt = seq[i + 1]
            np_ = find_heading_page(pt, nxt, gt[nxt].get("item_title", ""), nav[nxt],
                                    exclude=toc_pages) or nav[nxt]
            ep = (np_ - 1) if _heading_top(pt[np_ - 1], nxt) else np_
            ep = max(ep, sp)
            next_ct = ct_of.get(nxt, "")
            pages = [ep - 1, ep] if ep - 1 >= sp else [ep]
            pt_pred = cached_multi(reader, [_png(folder, p) for p in pages],
                                   TAIL2_PROMPT.format(n=num, title=title, nn=nxt,
                                                       ntitle=gt.get(nxt, {}).get("item_title", "") or ""))
            if pt_pred:
                clean = _pr(bt[-WIN:], pt_pred)
                for op in TAIL_OPS:
                    for sev in SEVERITIES:
                        inj = _pr(inj_tail(ct, op, sev, next_ct), pt_pred)
                        rows.append({"op": op, "sev": sev, "applicable": clean >= th,
                                     "detected": clean >= th and inj < th})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--th", type=int, default=DEFAULT_TH)
    args = ap.parse_args()

    rows = []
    for label in LABELS:
        rows += eval_filing(label, args.model, args.th)

    print(f"\n=== 端到端 §7.3 偵測率（TOC 導航頁、僅 gate 放行 item）  {args.model} ===")
    print(f"{'op':<16} | " + " | ".join(f"{s:>7}" for s in SEVERITIES))
    print("-" * 40)
    for op in HEAD_OPS + TAIL_OPS:
        cells = []
        for sev in SEVERITIES:
            sub = [r for r in rows if r["op"] == op and r["sev"] == sev and r["applicable"]]
            det = sum(r["detected"] for r in sub)
            cells.append(f"{det}/{len(sub)}" if sub else "n/a")
        print(f"{op:<16} | " + " | ".join(f"{c:>7}" for c in cells))
    print("\n（對照原 §7.3 用 dataset 頁：truncate 50% ~84–96%、overrun ~82–90%；"
          "此處用獨立導航頁應相當 → 證明偵測力不靠 GT 頁）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
