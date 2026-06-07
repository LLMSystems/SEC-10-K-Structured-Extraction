"""
驗證主程式。

對每條規則做兩個測試：
  1. 精確度 / 不誤殺：34 份正確 GT 直接跑規則 → 期望零觸發（FP）。
  2. 偵測率 / 抓得到：在 GT 上注入對應錯誤 → 期望觸發（recall）。

用法：python -m feedback.deterministic_validation.runner
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from .model import load_ground_truth
from .mutations import OPERATORS
from .rules import RULES, GAP_THRESHOLD, CONTENT_FLOOR, CORE_ITEMS, rule_3_coverage


def _fired(rule_fn, parse) -> bool:
    return len(rule_fn(parse)) > 0


def precision(parses) -> dict[str, list[str]]:
    """回傳每條規則在乾淨 GT 上誤觸發的 filing 清單。"""
    fp = {name: [] for name in RULES}
    for p in parses:
        for name, fn in RULES.items():
            if _fired(fn, p):
                fp[name].append(p.filing_id)
    return fp


def recall(parses) -> dict[str, dict]:
    """回傳每條規則、每個 operator 的偵測率。"""
    result = {}
    for name, fn in RULES.items():
        per_op = {}
        for op in OPERATORS[name]:
            total = detected = 0
            for p in parses:
                for mut in op(p):
                    total += 1
                    if _fired(fn, mut.parse):
                        detected += 1
            per_op[op.__name__] = (detected, total)
        result[name] = per_op
    return result


def diagnostics(parses):
    """規則 3 / 4 的關鍵診斷數字（報告用）。"""
    # 規則 3：乾淨 GT 的合法空隙最大值 vs 門檻；漏抓偵測對 item 大小的依賴
    legit = []
    for p in parses:
        items = p.ranged_items()
        for a, b in zip(items, items[1:]):
            legit.append(b.char_range[0] - a.char_range[1])
    # omit_item 偵測率：依「歸哪個子規則管」分桶
    from .mutations import omit_item
    # 桶：[detected, total]
    buckets = {
        "核心 item（任意位置/大小）": [0, 0],     # 由 3b 完整性接管
        "非核心・內部・>門檻": [0, 0],            # 由 3a 空隙接管
        "非核心・邊界或<門檻（界外）": [0, 0],     # 設計上不歸規則 3 管
    }
    for p in parses:
        ranged = p.ranged_items()
        if len(ranged) < 2:
            continue
        boundary_ids = {ranged[0].item_number, ranged[-1].item_number}
        for mut in omit_item(p):
            if mut.target in CORE_ITEMS:
                key = "核心 item（任意位置/大小）"
            elif mut.target_size > GAP_THRESHOLD and mut.target not in boundary_ids:
                key = "非核心・內部・>門檻"
            else:
                key = "非核心・邊界或<門檻（界外）"
            buckets[key][1] += 1
            if _fired(rule_3_coverage, mut.parse):
                buckets[key][0] += 1

    # 規則 4：S 的分佈與安全邊際
    sums = [sum(len(it.content_text or "") for it in p.extracted_items()) for p in parses]

    return {
        "gap_legit_max": max(legit) if legit else 0,
        "gap_threshold": GAP_THRESHOLD,
        "drop_recall_by_size": buckets,
        "S_min": min(sums),
        "S_median": int(st.median(sums)),
        "content_floor": CONTENT_FLOOR,
        "S_margin": round(min(sums) / CONTENT_FLOOR, 1),
    }


def _item_compact(it) -> dict:
    """把 item 壓縮成可檢視的小紀錄（content 只存長度，避免檔案爆大）。"""
    return {
        "item_number": it.item_number,
        "status": it.status,
        "char_range": list(it.char_range) if it.char_range else None,
        "content_len": None if it.content_text is None else len(it.content_text),
    }


def dump_mutants(parses, outdir: str = "feedback/deterministic_validation/mutants"):
    """把每個 operator 產生的所有錯誤 parse 存成檔案（一個 operator 一檔）。"""
    root = Path(outdir)
    manifest = []
    for rule_name, ops in OPERATORS.items():
        for op in ops:
            records = []
            for p in parses:
                for mut in op(p):
                    flagged = _fired(RULES[rule_name], mut.parse)
                    records.append({
                        "meta": {
                            "source": mut.parse.filing_id,
                            "rule": rule_name,
                            "operator": mut.operator,
                            "target": mut.target,
                            "target_size": mut.target_size,
                            "expected_flag": True,       # 注入錯誤 → 理應被抓
                            "actually_flagged": flagged,  # 規則實際是否抓到
                        },
                        "items": [_item_compact(it) for it in mut.parse.items],
                    })
            out = root / rule_name / f"{op.__name__}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            detected = sum(r["meta"]["actually_flagged"] for r in records)
            manifest.append({
                "rule": rule_name, "operator": op.__name__,
                "count": len(records), "detected": detected,
                "file": str(out.relative_to(root)),
            })
            print(f"  寫出 {out}  （{len(records)} 份，抓到 {detected}）")
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(m["count"] for m in manifest)
    print(f"\n共 {total} 份錯誤資料 → {root}/（索引：manifest.json）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", nargs="?", const="feedback/deterministic_validation/mutants", default=None,
                    metavar="OUTDIR", help="把錯誤資料寫成檔案（預設 feedback/deterministic_validation/mutants）")
    args = ap.parse_args()

    parses = load_ground_truth()
    print(f"載入 {len(parses)} 份 Ground Truth\n")

    if args.dump is not None:
        print("產生並寫出錯誤資料：")
        dump_mutants(parses, args.dump)
        return

    fp = precision(parses)
    rc = recall(parses)

    print("=" * 64)
    print("精確度（乾淨 GT 誤觸發 = false positive）")
    print("=" * 64)
    for name in RULES:
        n = len(fp[name])
        mark = "OK" if n == 0 else "FAIL"
        detail = "" if n == 0 else f"  → {fp[name]}"
        print(f"{mark} {name:22} FP = {n}/{len(parses)}{detail}")

    print("\n" + "=" * 64)
    print("偵測率（注入錯誤被抓到 = recall）")
    print("=" * 64)
    for name in RULES:
        print(f"\n{name}")
        for op, (d, t) in rc[name].items():
            pct = f"{100*d/t:.0f}%" if t else "n/a"
            print(f"    {op:14} {d}/{t}  ({pct})")

    print("\n" + "=" * 64)
    print("關鍵診斷")
    print("=" * 64)
    dg = diagnostics(parses)
    print(f"規則 3：乾淨 GT 合法空隙最大 = {dg['gap_legit_max']} 字（門檻 {dg['gap_threshold']}）"
          f" → 安全邊際 {dg['gap_threshold']/max(dg['gap_legit_max'],1):.1f}×")
    print(f"        漏抓偵測率（omit_item）：")
    for k, (d, t) in dg["drop_recall_by_size"].items():
        pct = f"{100*d/t:.0f}%" if t else "n/a"
        print(f"          {k:12} {d}/{t}  ({pct})")
    print(f"規則 4：extracted 內容總和 S：min={dg['S_min']:,}  median={dg['S_median']:,}"
          f"  地板={dg['content_floor']:,}  → 安全邊際 {dg['S_margin']}×")


if __name__ == "__main__":
    main()
