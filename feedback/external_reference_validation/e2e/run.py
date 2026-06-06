"""
Tier 2 端到端：TOC 獨立導航 → gate → Source C 邊界檢查（不靠 parser/dataset 頁碼）。

把兩段串起來實測（之前 toc_nav 與 Source C 是各自驗證）：
  Stage 1  TOC-nav：item → 渲染頁（VLM 讀 TOC + 頁尾序列 + 標題微調）。
  gate     逐 item「標題對帳」：導航頁 ±1 有 'Item N' 標題才算可信、才放行。
  Stage 2  Source C：用**導航頁**(非 dataset 頁) 餵 VLM 比首尾段 vs GT 內文。

產出：端到端良率(gate 通過率) + 用獨立頁時的 head/tail 精確度，並對照 dataset 頁是否一致。

用法：python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --model google/gemini-3-flash-preview --batch 4
"""

from __future__ import annotations

import argparse
import json
import re
import time
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor

from feedback.external_reference_validation.vlm_reader import VLMImageReader
from feedback.external_reference_validation.map_offsets import load_page_texts, norm
from feedback.external_reference_validation.vlm_first_last import (
    HEAD_PROMPT, body_text, strip_heading, tail_kind,
    _score, _png, WIN, DEFAULT_TH, DATASET, clean_response, MAX_TOKENS
)
from feedback.external_reference_validation.toc_nav.coverage import navigate
from feedback.external_reference_validation.toc_nav.toc_extract import extract_toc


# tail 看「最後 1~2 頁」（邊界可能跨頁：item N 尾巴在結束頁或其前一頁底部）
TAIL2_PROMPT = (
    "You are shown the last 1-2 rendered pages (in order) of the region for "
    '"Item {n}. {title}". The next section is "Item {nn}. {ntitle}".\n'
    'Find where Item {n} ends — right before the "Item {nn}" heading if it appears, '
    "otherwise the very bottom of the last page — and transcribe VERBATIM the last 5 lines "
    "of Item {n} before that point. Ignore running headers, page numbers, footers.\n"
    "Output ONLY the transcribed text, no commentary, no quotes, no formatting."
)


def _score_multi(reader, images: list, prompt: str, gt_window: str, th: int, force: bool) -> dict:
    """多圖版 _score：送 images 給 VLM、partial_ratio 比 GT 窗口。空回應/429 退避重試。"""
    last = "?"
    for attempt in range(4):
        try:
            raw = reader.read_images(images, prompt=prompt, max_tokens=MAX_TOKENS,
                                     force=(force or attempt > 0))
            txt = clean_response(raw)
            if txt:
                r = round(fuzz.partial_ratio(gt_window, norm(txt)), 1)
                return {"ratio": r, "pass": r >= th, "pred": txt[:400], "gt": gt_window}
            last = "模型回空內容"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            if not ("429" in last or "empty" in last.lower()):
                return {"error": last}
        if attempt < 3:
            time.sleep(4 * (attempt + 1))
    return {"error": last}


def _heading_top(page_text: str, n: str, within: int = 160) -> bool:
    return re.search(r"\bitem\s+" + re.escape(n.lower()) + r"\b", page_text[:within]) is not None


def find_heading_page(pt: list[str], num: str, title: str, center: int,
                      win: int = 1, exclude: set[int] = frozenset()) -> int | None:
    """
    在 center ±win 內找「Item N + 標題」的標題頁（整頁搜，非只頁首）。
    用 title 第一個實詞，避開內文 'item N' 引用誤判；找到的頁就是 item 真正的起點（順帶修 ±1 導航誤差）。
    exclude：跳過的頁（如 TOC 頁——低編號 item 的導航落點靠近 TOC，會撞到目錄上的同名項）。
    回傳標題頁（離 center 最近者）或 None（找不到 → 棄驗）。
    """
    tw = [w for w in norm(title).split() if len(w) > 2]
    # 取首詞的純字母前綴，避開撇號型態不符（management's↔management's）
    needle = re.match(r"[a-z]+", tw[0]).group(0) if tw and re.match(r"[a-z]+", tw[0]) else ""
    pat = re.compile(r"\bitem\s+" + re.escape(num.lower()) + r"\b[\s.]*" + re.escape(needle))
    for r in sorted(range(max(center - win, 1), min(center + win, len(pt)) + 1),
                    key=lambda r: abs(r - center)):
        if r in exclude:
            continue
        if pat.search(pt[r - 1]):
            return r
    return None


def run(label: str, model: str, th: int, force: bool, batch: int = 3) -> list[dict]:
    folder = DATASET / label
    pt = load_page_texts(folder / f"{label}.pdf")
    content = json.loads((folder / f"{label}_content.json").read_text("utf-8"))
    gt = {it["item_number"]: it for it in content["items"] if it.get("status") == "extracted"}
    order = [it["item_number"] for it in content["items"] if it.get("status") == "extracted"]
    known = {it["item"]: it["start_page"]
             for it in json.loads((folder / f"{label}_pages.json").read_text("utf-8"))["items"]}

    nav = navigate(label, model)["nav"]            # item → 渲染頁（TOC 獨立導航）
    toc_pages = set(extract_toc(label, model)["toc_pages"])   # 排除 TOC 頁，避免低編號 item 撞目錄
    reader = VLMImageReader(
        model=model,
        cache_dir="feedback/external_reference_validation/vlm_cache",
    )
    seq = [n for n in order if n in nav]

    jobs = []
    for i, num in enumerate(seq):
        ct = gt[num]["content_text"]
        bt = body_text(ct)
        if len(bt) < 20:
            continue
        title = gt[num].get("item_title", "")
        kind = tail_kind(ct)
        nav_pg = nav[num]
        hp = find_heading_page(pt, num, title, nav_pg, exclude=toc_pages)   # 標題對帳 + 重定位
        gated = hp is not None
        sp = hp or nav_pg
        rec: dict = {"item": num, "bucket": kind, "nav_page": nav_pg, "start_page": sp,
                     "dataset_page": known.get(num), "gated": gated}

        # head：用（重定位後的）起始頁
        head_gt = strip_heading(bt, num, title)[:WIN]
        head_job = {
            "image": _png(folder, sp),
            "prompt": HEAD_PROMPT.format(n=num, title=title),
            "gt": head_gt,
        }

        # tail：end page 由「下一個 nav item 起點（同樣重定位）」推（prose 桶才驗）
        if kind == "prose" and i + 1 < len(seq) and nav.get(seq[i + 1]):
            nxt = seq[i + 1]
            np_ = find_heading_page(pt, nxt, gt[nxt].get("item_title", ""), nav[nxt],
                                    exclude=toc_pages) or nav[nxt]
            ep = (np_ - 1) if _heading_top(pt[np_ - 1], nxt) else np_
            ep = max(ep, sp)
            rec["end_page"] = ep
            # 看最後 1~2 頁（邊界跨頁）：[ep-1, ep]，但不早於 item 起始頁
            pages = [ep - 1, ep] if ep - 1 >= sp else [ep]
            tail_job = {
                "images": [_png(folder, p) for p in pages],
                "prompt": TAIL2_PROMPT.format(
                    n=num,
                    title=title,
                    nn=nxt,
                    ntitle=gt.get(nxt, {}).get("item_title", "") or "",
                ),
                "gt": bt[-WIN:],
            }
        else:
            tail_job = None
            rec["tail"] = {"skipped": f"{kind}/最後一個"}
        jobs.append({"record": rec, "head_job": head_job, "tail_job": tail_job})

    print(f"  [{label}] {len(jobs)} 個 item，batch={batch}")

    def work(job: dict) -> dict:
        rec = job["record"]
        head = job["head_job"]
        rec["head"] = _score(reader, head["image"], head["prompt"], head["gt"], th, force)
        if job["tail_job"] is not None:
            tail = job["tail_job"]
            rec["tail"] = _score_multi(reader, tail["images"], tail["prompt"], tail["gt"], th, force)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, batch)) as ex:
        return list(ex.map(work, jobs))


def _cell(side: dict) -> str:
    if "ratio" in side:
        return f"{'✅' if side['pass'] else '❌'}{side['ratio']:>5}"
    if "error" in side:
        return f"⚠{side['error'][:14]}"
    return "—skip"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="GDC_2023")
    ap.add_argument("--model", default="google/gemini-3-flash-preview")
    ap.add_argument("--th", type=int, default=DEFAULT_TH)
    ap.add_argument("--batch", type=int, default=3, help="item-level 併發數（預設 3）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    rows = run(args.label, args.model, args.th, args.force, args.batch)
    print(f"\n=== {args.label}  ({args.model}) 端到端（TOC 導航頁 → Source C）===")
    print(f"{'item':>5} | gate | nav→start/ds | {'head':>7} | {'tail':>7}")
    print("-" * 54)
    for r in rows:
        g = "✓" if r["gated"] else "✗"
        pgs = f"{r['nav_page']}→{r['start_page']}/{r['dataset_page']}"
        print(f"{r['item']:>5} |  {g}   | {pgs:>11} | {_cell(r['head']):>7} | {_cell(r['tail']):>7}")

    n = len(rows)
    gated = sum(1 for r in rows if r["gated"])
    samepg = sum(1 for r in rows if r["start_page"] == r["dataset_page"])
    hp = [r["head"] for r in rows if "pass" in r["head"]]
    tp = [r["tail"] for r in rows if "pass" in r["tail"]]
    # gate 內的精確度（放行才算數）
    hp_g = [r["head"] for r in rows if r["gated"] and "pass" in r["head"]]
    tp_g = [r["tail"] for r in rows if r["gated"] and "pass" in r["tail"]]
    print("-" * 50)
    print(f"端到端良率（gate 通過）：{gated}/{n}")
    print(f"重定位後 start == dataset 頁：{samepg}/{n}")
    print(f"精確度(gate 內) head {sum(s['pass'] for s in hp_g)}/{len(hp_g)}"
          f"　tail {sum(s['pass'] for s in tp_g)}/{len(tp_g)}")
    print(f"精確度(全部)  head {sum(s['pass'] for s in hp)}/{len(hp)}"
          f"　tail {sum(s['pass'] for s in tp)}/{len(tp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
