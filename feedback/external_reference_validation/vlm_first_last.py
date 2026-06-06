"""
Tier 2 來源 C — VLM 首尾段驗證器（單模型、單份 filing）

對一份 dataset filing 的每個 extracted item：
  head：起始頁 PNG → VLM 轉錄該 item 開頭幾行 → 與 GT 內文「開頭文字塊」比。
  tail：結束頁 PNG → VLM 轉錄該 item 結尾幾行 → 與 GT 內文「結尾文字塊」比。

比對為何用「連續文字塊 + partial_ratio」而非逐行：
  content_text 是文字流，沒有「視覺行」概念；VLM 看的才是版面行。兩者「行」的定義
  對不上（行寬、斷行、子標題都不同），逐行 fuzz.ratio 會假性全 fail。
  改成：兩邊各去 HTML、正規化成連續字串，GT 取頭/尾固定窗口，VLM 取整段轉錄，
  用 fuzz.partial_ratio 找最佳子串對齊——對斷行/長度差不敏感，對「漏抓一段」敏感。

輸出也不要求 JSON：10-K 內文滿是引號，小模型常把 JSON 跳脫寫壞；既然只需文字塊，
直接叫模型吐純文字，解析只去 markdown 圍欄即可，避開跳脫地雷。

table 結尾的 item 尾段去 HTML 後是標籤湯，比對失真，故依尾段分三桶：
  - prose            ：尾段是文字 → head + tail 全驗（乾淨主訊號）
  - table_tail_covered：尾段是 table 但**有後繼 item** → tail 不驗，
                        該邊界由「下一個 item 的 head」覆蓋（同一條分界線）
  - table_tail_last  ：尾段是 table 且**是最後一個 item** → tail 誠實標記未驗證

用法：
    python -m feedback.external_reference_validation.vlm_first_last --label RELL_2025
    python -m feedback.external_reference_validation.vlm_first_last --label RELL_2025 --model google/gemma-4-31b-it --force
"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rapidfuzz import fuzz

from feedback.external_reference_validation.vlm_reader import VLMImageReader, DEFAULT_MODEL
from feedback.external_reference_validation.map_offsets import norm

DATASET = Path("feedback/external_reference_validation/dataset")
OUT_DIR = Path("feedback/external_reference_validation/report/vlm_first_last")
DEFAULT_TH = 75  # 經驗空隙：正確讀 81~100、真錯誤 <=50；75 兩邊留裕度。最終由 harness 校準
WIN = 300  # GT 頭/尾文字塊窗口（字元數，約 5 行）
# 推理模型（如 qwen3.6-27b）會先花上千 token 思考才輸出；max_tokens 太小 → 思考用光額度、
# content 為空（finish_reason=length）。給足 2048 容「推理 + 答案」。非推理模型會早早 stop，無害。
MAX_TOKENS = 2048

HEAD_PROMPT = (
    "You are shown one page from a SEC 10-K filing rendered as an image.\n"
    'This page contains the BEGINNING of "Item {n}. {title}".\n'
    'Find the "Item {n}" heading, then transcribe VERBATIM the first 5 lines of text that come '
    "immediately after it. Include any report title, addressee, or sub-heading exactly as printed — "
    "do NOT skip them; only ignore page running-headers, page numbers, and footers.\n"
    "Output ONLY the transcribed text, no commentary, no quotes, no formatting."
)
# tail 調校結論：gemma 在全 5 份試過兩版措辭都卡在 ~39/50（78%）——增減只是在 filing 間換位置
# （「不要抓表」助 table-heavy 的 GDC/TSLA，卻打壞乾淨的 NFLX）。屬模型能力天花板，非 prompt 問題，
# 故定在最佳的 39/50 版；tail 的真正槓桿是 harness 換強模型。
# 另：next cue 用「文件順序真正相鄰 item」(9B→9C 而非 15) 較正確，未顯著改善，留待 harness 階段再評。
TAIL_PROMPT = (
    "You are shown one page from a SEC 10-K filing rendered as an image.\n"
    'Item "{n}. {title}" ENDS on this page; the next section is "Item {nn}. {ntitle}".\n'
    "First locate the bottom edge of Item {n}'s text:\n"
    '- If the "Item {nn}" heading is visible on this page, Item {n} ends on the line directly ABOVE that heading.\n'
    "- Otherwise Item {n} runs all the way to the very bottom of the page.\n"
    "Then transcribe VERBATIM the LAST 5 lines of Item {n} at that bottom edge — "
    "the FINAL lines right before the section ends, NOT lines from the top or middle of the page. "
    "Ignore running headers, page numbers, footers.\n"
    "Output ONLY the transcribed text, no commentary, no quotes, no formatting."
)

_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_AXIS = re.compile(r"\$?[\d,]+(?:\.\d+)?%?|\d{1,2}/\d{1,2}/\d{2,4}")  # 金額/百分比/日期軸標記


def body_text(content_text: str) -> str:
    """item 內容去 HTML、正規化成連續字串（用於取頭/尾文字塊）。"""
    return norm(_TAG.sub(" ", content_text))


def strip_heading(bt: str, num: str, title: str) -> str:
    """剝掉 GT 開頭的「Item N. 標題」——VLM 被要求略過標題給正文，兩邊要對齊。"""
    for pre in (norm(f"item {num}. {title}"), norm(f"item {num} {title}")):
        if pre and bt.startswith(pre):
            return bt[len(pre):].strip()
    t = norm(title)
    i = bt.find(t) if t else -1
    if 0 <= i <= 120:  # 標題出現在最前段 → 從標題之後切
        return bt[i + len(t):].strip()
    return bt


def _has_axis_run(text: str, run: int = 4) -> bool:
    """是否出現連續 >=run 個金額/日期軸標記（圖表軸特徵；prose 的數字夾在句中、不連續）。"""
    streak = 0
    for tok in text.split():
        if _AXIS.fullmatch(tok.strip(".,*()")):
            streak += 1
            if streak >= run:
                return True
        else:
            streak = 0
    return False


def tail_kind(content_text: str, window: int = 1500, min_alpha: int = 300) -> str:
    """尾段型態：prose / table / figure。table 與 figure 的尾段都無法逐字比對。"""
    ct = content_text.rstrip()
    if ct.endswith(">") or sum(c.isalpha() for c in _TAG.sub(" ", ct[-window:])) < min_alpha:
        return "table"
    if _has_axis_run(_TAG.sub(" ", ct[-400:])):
        return "figure"  # 績效圖等：尾段被壓平成一串軸標記
    return "prose"


def clean_response(raw: str) -> str | None:
    """去 markdown 圍欄與前後空白；空字串回 None（模型沒抓到內容）。"""
    t = _FENCE.sub("", raw.strip()).strip()
    return t or None


def _ask(reader: VLMImageReader, image: Path, prompt: str,
         force: bool, retries: int = 4) -> tuple[str | None, str | None]:
    """
    打一次 VLM + 清理，回 (text, error)。
    暫時性失敗（429 上游限流 / 空回應——某些模型如 qwen 在併發下偶發）退避重試；
    重試時 force 繞過快取（空回應可能已被快取）。非暫時性錯誤直接失敗、不浪費重試。
    """
    last = "?"
    for attempt in range(retries):
        try:
            raw = reader.read_image(image, prompt=prompt, max_tokens=MAX_TOKENS,
                                    force=(force or attempt > 0))
            txt = clean_response(raw)
            if txt:
                return txt, None
            last = "模型回空內容"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            if not ("429" in last or "empty" in last.lower()):
                return None, last  # 非暫時性（auth/slug/格式）→ 直接失敗
        if attempt < retries - 1:
            wait = 4 * (attempt + 1)
            print(f"    重試（{attempt + 1}/{retries}）：{last[:45]}")
            time.sleep(wait)
    return None, last


def _png(folder: Path, page_no: int) -> Path:
    return folder / "pages" / f"page_{page_no:03d}.png"


def next_item_cues(content_items: list[dict]) -> dict[str, tuple[str, str]]:
    """
    依 content.json 的完整 item 順序建立「下一個 item cue」。

    這裡故意不只看 extracted items，因為頁面上真正緊接著出現、能當 stop signal 的，
    也可能是 not_applicable / reserved / incorporated_by_reference 的 item（如 9B / 9C）。
    """
    seq = [it for it in content_items if it.get("item_number")]
    cues: dict[str, tuple[str, str]] = {}
    for cur, nxt in zip(seq, seq[1:]):
        cues[str(cur["item_number"])] = (str(nxt["item_number"]), nxt.get("item_title", "") or "")
    return cues


def _score(reader, image, prompt, gt_window, th, force) -> dict:
    """打一端 + partial_ratio 比對 GT 文字塊。"""
    pred, err = _ask(reader, image, prompt, force)
    if err:
        return {"error": err}
    r = round(fuzz.partial_ratio(gt_window, norm(pred)), 1)
    return {"ratio": r, "pass": r >= th, "pred": pred[:400], "gt": gt_window}


def run_filing(model: str, label: str, th: int, force: bool, batch: int = 3) -> dict:
    folder = DATASET / label
    pages = json.loads((folder / f"{label}_pages.json").read_text(encoding="utf-8"))
    content = json.loads((folder / f"{label}_content.json").read_text(encoding="utf-8"))

    gt = {it["item_number"]: it for it in content["items"] if it.get("status") == "extracted"}
    titles = {k: v.get("item_title", "") for k, v in gt.items()}
    next_cues = next_item_cues(content["items"])
    rng = [it for it in pages["items"] if it.get("start_page") and it.get("end_page")]

    reader = VLMImageReader(model=model, cache_dir="feedback/external_reference_validation/vlm_cache")

    # ── 先建 record 骨架，並收集要打的 VLM jobs（head/tail）──
    records: list[dict] = []
    jobs: list[tuple] = []  # (rec_idx, side, num, image, prompt, gt_window)
    for idx, it in enumerate(rng):
        num = it["item"]
        ct = gt[num]["content_text"] if num in gt else ""
        title = titles.get(num, "")
        bt = body_text(ct)
        has_next = idx + 1 < len(rng)
        nxt = rng[idx + 1] if has_next else None

        kind = tail_kind(ct)
        bucket = ("prose" if kind == "prose"
                  else f"{kind}_tail_covered" if has_next else f"{kind}_tail_last")
        rec: dict = {"item": num, "title": title, "bucket": bucket,
                     "start_page": it["start_page"], "end_page": it["end_page"]}
        ridx = len(records)

        # head（三桶都驗）；GT 先剝標題以對齊 VLM
        if len(bt) >= 20:
            head_gt = strip_heading(bt, num, title)[:WIN]
            jobs.append((ridx, "head", num, _png(folder, it["start_page"]),
                         HEAD_PROMPT.format(n=num, title=title), head_gt))
        else:
            rec["head"] = {"skipped": "GT 內文過短/空"}

        # tail（依桶）
        if bucket == "prose" and len(bt) >= 20:
            nn, ntitle = next_cues.get(num, ("", ""))
            tp = TAIL_PROMPT.format(n=num, title=title,
                                    nn=nn, ntitle=ntitle)
            jobs.append((ridx, "tail", num, _png(folder, it["end_page"]), tp, bt[-WIN:]))
        elif bucket.endswith("_covered"):
            rec["tail"] = {"skipped": f"{kind} 結尾，邊界由下一個 item {nxt['item']} 的 head 覆蓋"}
        else:
            rec["tail"] = {"skipped": f"{kind} 結尾且為最後一個 item → 未驗證"}

        records.append(rec)

    # ── 併發打 VLM（I/O-bound，batch 條同時跑）──
    print(f"  [{label}] {len(jobs)} 個 VLM 呼叫，batch={batch}")

    def work(job: tuple):
        ridx, side, num, image, prompt, gt_window = job
        res = _score(reader, image, prompt, gt_window, th, force)
        tag = f"{res['ratio']}" if "ratio" in res else res.get("error", "?")[:20]
        print(f"    ✓ item {num} {side} = {tag}")
        return ridx, side, res

    with ThreadPoolExecutor(max_workers=max(1, batch)) as ex:
        for ridx, side, res in ex.map(work, jobs):
            records[ridx][side] = res

    return {"label": label, "model": model, "threshold": th, "items": records}


def _cell(side: dict) -> str:
    if "ratio" in side:
        return f"{'✅' if side['pass'] else '❌'}{side['ratio']:>5}"
    if "error" in side:
        return f"⚠{side['error'][:16]}"
    return "—skip"


def print_report(res: dict) -> None:
    print(f"\n=== {res['label']}  ({res['model']}, TH={res['threshold']}) ===")
    print(f"{'item':>5} | {'bucket':<18} | {'head':>8} | {'tail':>8}")
    print("-" * 52)
    for it in res["items"]:
        print(f"{it['item']:>5} | {it['bucket']:<18} | {_cell(it['head']):>8} | {_cell(it['tail']):>8}")

    def rate(key, only=None):
        xs = [it[key] for it in res["items"]
              if (only is None or it["bucket"] == only) and "pass" in it[key]]
        return f"{sum(s['pass'] for s in xs)}/{len(xs)}" if xs else "0/0"

    nb: dict[str, int] = {}
    for it in res["items"]:
        nb[it["bucket"]] = nb.get(it["bucket"], 0) + 1
    print("-" * 52)
    print("桶：" + " ".join(f"{b}={n}" for b, n in sorted(nb.items())))
    print(f"head 通過：{rate('head')}（全 item）")
    print(f"tail 通過：{rate('tail', 'prose')}（僅 prose 桶）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="RELL_2025", help="dataset filing")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model slug")
    ap.add_argument("--th", type=int, default=DEFAULT_TH, help=f"partial_ratio 門檻（預設 {DEFAULT_TH}）")
    ap.add_argument("--batch", type=int, default=3, help="併發 VLM 呼叫數（預設 3）")
    ap.add_argument("--force", action="store_true", help="略過快取重打")
    args = ap.parse_args()

    res = run_filing(args.model, args.label, args.th, args.force, args.batch)
    print_report(res)

    safe = VLMImageReader(model=args.model).safe_model_name(args.model)
    out = OUT_DIR / safe
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n輸出 → {out / f'{args.label}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
