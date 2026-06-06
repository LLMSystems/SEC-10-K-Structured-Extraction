"""
Tier 2 §7.3 — 偵測力評估：對 GT 邊界注入錯誤，看驗證器是否如預期 FAIL。

設計（見 detection_eval_plan.md）：
  - 只汙染 GT 側（＝ parser 的邊界宣稱）；image/VLM 不動。
  - VLM 轉錄 survey 時已存 vlm_cache → **嚴格只讀快取，零 API**。
  - 運算子：truncate_tail/head（少抓）、overrun_head/tail（越界進相鄰 item）。
  - 嚴重度：win(≈5行/300字) / 25% / 50%（刻意不測 1~3 行小截斷）。
  - detected ⟺ (clean_ratio ≥ TH) ∧ (inj_ratio < TH)；純門檻跨越。

輸出：feedback/external_reference_validation/report/detection/<model>.json + summary.md

用法：
    python -m feedback.external_reference_validation.vlm_inject_eval                 # 預設排行前 5
    python -m feedback.external_reference_validation.vlm_inject_eval --models google/gemini-3-flash-preview
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from rapidfuzz import fuzz

from feedback.external_reference_validation.vlm_reader import VLMImageReader
from feedback.external_reference_validation.map_offsets import norm
from feedback.external_reference_validation.vlm_first_last import (
    body_text, strip_heading, tail_kind,
    clean_response, _png, WIN, DEFAULT_TH, DATASET,
)

OUT_DIR = Path("feedback/external_reference_validation/report/detection")
LABELS = ["RELL_2025", "GDC_2023", "NFLX_2025", "TSLA_2023", "WMT_2026"]
TOP5 = [
    "google/gemini-3-flash-preview", "qwen/qwen3.6-plus",
    "google/gemini-3.1-flash-lite", "moonshotai/kimi-k2.6", "qwen/qwen3.6-27b",
]
SEVERITIES = ["50lines", "50%"]  # 50 行(絕對) + item body 一半(比例)；皆為「實質截斷」
HEAD_OPS = ["truncate_head", "overrun_head"]
TAIL_OPS = ["truncate_tail", "overrun_tail"]


def _pr(gt: str, pred: str) -> float:
    return round(fuzz.partial_ratio(gt, norm(pred)), 1)


def _lines(ct: str) -> list[str]:
    return [ln for ln in ct.split("\n") if ln.strip()]


def _k(sev: str, n: int) -> int:
    """該嚴重度要丟/借幾行（相對總行數 n）。"""
    if sev == "50lines":
        return min(50, max(0, n - 1))      # 短 item(<50行) → 留 1 行，幾乎全砍
    return max(1, round(n * 0.5))           # 50%：丟一半行


def inj_head(ct: str, num: str, title: str, op: str, sev: str, prev_ct: str) -> str:
    """注入後的 head 窗口（以 content_text 行為單位操作，再過 body_text/strip_heading）。"""
    L = _lines(ct)
    if op == "truncate_head":               # 頭少抓 k 行 → 標題連同前段被砍，窗口變後面段落
        ct2 = "\n".join(L[_k(sev, len(L)):])
    else:                                   # overrun_head：把前一個 item 末 k 行接到開頭
        PL = _lines(prev_ct)
        ct2 = "\n".join(PL[-_k(sev, len(PL)):] + L)
    return strip_heading(body_text(ct2), num, title)[:WIN]


def inj_tail(ct: str, op: str, sev: str, next_ct: str) -> str:
    """注入後的 tail 窗口。"""
    L = _lines(ct)
    if op == "truncate_tail":               # 尾巴少抓 k 行 → 窗口變更早的段落
        ct2 = "\n".join(L[: len(L) - _k(sev, len(L))])
    else:                                   # overrun_tail：把下一個 item 前 k 行接到結尾
        NL = _lines(next_ct)
        ct2 = "\n".join(L + NL[: _k(sev, len(NL))])
    return body_text(ct2)[-WIN:]


_HEAD_NUM = re.compile(r'BEGINNING of "Item ([^."]+)\.')
_TAIL_NUM = re.compile(r'Item "([^."]+)\.[^"]*" ENDS on this page')


def build_cache_index(reader: VLMImageReader) -> dict:
    """
    掃一次該模型的快取，建索引：(png 檔名, side, item編號) → text。
    item 編號**從 prompt 精準抽出**（不靠標題子字串——tail prompt 也含下一個 item 的標題，會誤撞）。
    依首/尾 prompt 特徵分類，繞開 cue 措辭漂移（pred 都是該頁首/尾段轉錄）。
    """
    # key = (filing, png, side, item)。**必含 filing**——每個 filing 都有 page_004.png，
    # cache_dir 是全 filing 共用，只用檔名會跨 filing 撞鍵（抓到別份的 pred）。
    # 同 key 多個歷史 prompt 版本 → 取 created_at 最新（對得上 survey 的 pred）。
    idx: dict[tuple[str, str, str, str], tuple[int, str]] = {}
    if not reader.cache_dir.exists():
        return {}
    for f in reader.cache_dir.glob("*.json"):
        try:
            e = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        p = e.get("prompt") or ""
        mh, mt = _HEAD_NUM.search(p), _TAIL_NUM.search(p)
        if mh:
            side, num = "head", mh.group(1)
        elif mt:
            side, num = "tail", mt.group(1)
        else:
            continue
        # source_path = .../dataset/<label>/pages/<png>（可能是 Windows 反斜線）
        segs = str(e.get("source_path", "")).replace("\\", "/").split("/")
        if len(segs) < 3:
            continue
        label, name = segs[-3], segs[-1]
        txt = clean_response(e.get("text") or "")
        ts = int(e.get("created_at") or 0)
        if label and name and txt:
            key = (label, name, side, str(num))
            if key not in idx or ts >= idx[key][0]:
                idx[key] = (ts, txt)
    return {k: v[1] for k, v in idx.items()}


def cached_pred(idx: dict, image: Path, side: str, num: str) -> str | None:
    """從索引取該 (filing, 頁, side, item) 的快取 pred。"""
    return idx.get((image.parent.parent.name, image.name, side, str(num)))


def eval_model(model: str, th: int) -> dict:
    reader = VLMImageReader(model=model, cache_dir="tier2/vlm_cache")
    idx = build_cache_index(reader)
    rows = []          # 每筆：filing/item/side/op/sev/clean_ratio/inj_ratio/detected/applicable
    fp_head = fp_tail = n_head = n_tail = 0
    no_cache = 0
    blind_tail = 0     # table/figure 桶 → tail 不驗，tail 錯誤注入了也抓不到

    for label in LABELS:
        folder = DATASET / label
        pages = json.loads((folder / f"{label}_pages.json").read_text("utf-8"))
        content = json.loads((folder / f"{label}_content.json").read_text("utf-8"))
        gt = {it["item_number"]: it for it in content["items"] if it.get("status") == "extracted"}
        titles = {k: v.get("item_title", "") for k, v in gt.items()}
        # 鄰居原始 content_text（給 overrun 借文字，以行為單位）
        ct_of = {k: (v.get("content_text") or "") for k, v in gt.items()}
        rng = [it for it in pages["items"] if it.get("start_page") and it.get("end_page")]
        order = [it["item"] for it in rng]

        for i, it in enumerate(rng):
            num = it["item"]
            ct = ct_of.get(num, "")
            bt = body_text(ct)
            if len(bt) < 20:
                continue
            title = titles.get(num, "")
            kind = tail_kind(ct)
            # overrun 借「相鄰 extracted item」的內文（一定非空；cue 的 nn 可能是空內文的 by_reference）
            prev_ct = ct_of.get(order[i - 1], "") if i > 0 else ""
            next_ct = ct_of.get(order[i + 1], "") if i + 1 < len(order) else ""

            # ── head：所有桶都驗 ──
            ph = cached_pred(idx, _png(folder, it["start_page"]), "head", num)
            if ph is None:
                no_cache += 1
            else:
                n_head += 1
                clean = _pr(strip_heading(bt, num, title)[:WIN], ph)
                if clean < th:
                    fp_head += 1
                for op in HEAD_OPS:
                    for sev in SEVERITIES:
                        inj = _pr(inj_head(ct, num, title, op, sev, prev_ct), ph)
                        rows.append({"filing": label, "item": num, "side": "head",
                                     "op": op, "sev": sev, "clean": clean, "inj": inj,
                                     "applicable": clean >= th,
                                     "detected": clean >= th and inj < th})

            # ── tail：只 prose 桶有驗；table/figure 桶記為盲區 ──
            if kind != "prose":
                blind_tail += 1
                continue
            pt = cached_pred(idx, _png(folder, it["end_page"]), "tail", num)
            if pt is None:
                no_cache += 1
                continue
            n_tail += 1
            clean = _pr(bt[-WIN:], pt)
            if clean < th:
                fp_tail += 1
            for op in TAIL_OPS:
                for sev in SEVERITIES:
                    inj = _pr(inj_tail(ct, op, sev, next_ct), pt)
                    rows.append({"filing": label, "item": num, "side": "tail",
                                 "op": op, "sev": sev, "clean": clean, "inj": inj,
                                 "applicable": clean >= th,
                                 "detected": clean >= th and inj < th})

    # 聚合：每 (op, sev) 的偵測率
    agg = {}
    for op in HEAD_OPS + TAIL_OPS:
        for sev in SEVERITIES:
            sub = [r for r in rows if r["op"] == op and r["sev"] == sev and r["applicable"]]
            det = sum(r["detected"] for r in sub)
            agg[f"{op}/{sev}"] = {"detected": det, "applicable": len(sub),
                                  "rate": round(det / len(sub), 3) if sub else None}

    return {
        "model": model, "th": th,
        "fp": {"head": f"{fp_head}/{n_head}", "tail": f"{fp_tail}/{n_tail}"},
        "blind_tail_items": blind_tail, "no_cache": no_cache,
        "detection": agg, "rows": rows,
    }


def print_model(res: dict) -> None:
    print(f"\n=== {res['model']}  (TH={res['th']}) ===")
    print(f"誤殺 FP  head {res['fp']['head']}  tail {res['fp']['tail']}"
          f"   | tail 盲區(table/figure) {res['blind_tail_items']} item"
          + (f"   | ⚠ no_cache={res['no_cache']}" if res['no_cache'] else ""))
    print(f"{'op':<16} | " + " | ".join(f"{s:>6}" for s in SEVERITIES))
    print("-" * 46)
    for op in HEAD_OPS + TAIL_OPS:
        cells = []
        for sev in SEVERITIES:
            a = res["detection"][f"{op}/{sev}"]
            cells.append("  n/a " if a["rate"] is None else f"{a['rate']*100:4.0f}% ")
        print(f"{op:<16} | " + "| ".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(TOP5), help="逗號分隔 slug（預設排行前 5）")
    ap.add_argument("--th", type=int, default=DEFAULT_TH)
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for m in models:
        res = eval_model(m, args.th)
        (OUT_DIR / f"{VLMImageReader(model=m).safe_model_name(m)}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
        print_model(res)
        results.append(res)

    # summary.md：模型 × op×sev 偵測率
    lines = ["# Tier 2 §7.3 偵測力評估（錯誤注入，零 API）", "",
             f"TH={args.th}；detected ⟺ clean PASS 且注入後 FAIL。嚴重度 win≈300字 / 25% / 50%。", "",
             "## 偵測率（每模型）", ""]
    cols = [f"{op}/{sev}" for op in HEAD_OPS + TAIL_OPS for sev in SEVERITIES]
    lines.append("| 模型 | FP head | FP tail | " + " | ".join(cols) + " |")
    lines.append("|---|---|---|" + "---|" * len(cols))
    for r in results:
        cells = []
        for c in cols:
            a = r["detection"][c]
            cells.append("n/a" if a["rate"] is None else f"{a['rate']*100:.0f}%")
        lines.append(f"| {r['model']} | {r['fp']['head']} | {r['fp']['tail']} | "
                     + " | ".join(cells) + " |")
    lines += ["", f"> tail 盲區（table/figure 桶不驗 tail）：各模型 {results[0]['blind_tail_items']} 個 item，"
              "其 truncate_tail/overrun_tail 注入了也抓不到（覆蓋缺口）。"]
    (OUT_DIR / "summary.md").write_text("\n".join(lines), "utf-8")
    print(f"\n輸出 → {OUT_DIR}/（{len(results)} 模型 + summary.md）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
