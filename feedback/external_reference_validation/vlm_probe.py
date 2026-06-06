"""
Tier 2 來源 C — 多模態前置查核（§5 阻擋條件）

跑 pass@k harness 之前，先確認候選模型在 OpenRouter 上：
  1. 真的能讀圖（vision）——不是只吃文字；
  2. 聽得懂指令、回得出我們要的 JSON `{"lines": [...]}`；
  3. **能照真實任務的指令做事**——「先定位某個 item 標題，再回它前/後 5 行」。

所以 probe 用的是跟 verifier 一樣的 item 錨定 prompt（只是縮到「一個 item、兩張圖」
便宜地測），而不是更簡單的「這頁前 5 行」——否則閘門測了比生產更容易的題，會給假綠燈。

做法：從某份 dataset 挑一個中段 extracted item（確保是正文、非封面/TOC），
讀它的 item_title 與下一個 item 的 title，對它的起始頁 / 結束頁 PNG 各打一次。
驗證：API 沒報錯、回得出 JSON、lines 非空、且內容真的來自那張圖（PDF 該頁純文字做 grounding）。

過 = 這個 model slug 可進 pass@k harness；不過 = 換榜上同級的 VL 版本。

用法：
    python -m feedback.external_reference_validation.vlm_probe                                   # 預設 gemma-4-31b-it:free
    python -m feedback.external_reference_validation.vlm_probe --models google/gemma-4-31b-it,moonshotai/kimi-k2.6
    python -m feedback.external_reference_validation.vlm_probe --label TSLA_2023 --force
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import fitz  # PyMuPDF
from rapidfuzz import fuzz

from feedback.external_reference_validation.vlm_reader import VLMImageReader, DEFAULT_MODEL
from feedback.external_reference_validation.map_offsets import norm

DATASET = Path("feedback/external_reference_validation/dataset")

# 與 verifier 一致的 item 錨定 prompt（起始頁取標題後 5 行、結束頁取下一標題前 5 行）
HEAD_PROMPT = (
    "You are shown one page from a SEC 10-K filing rendered as an image.\n"
    'This page contains the BEGINNING of "Item {n}. {title}".\n'
    "Find that section's heading on the page, then transcribe VERBATIM the first 5 lines "
    "of its body text that follow the heading (ignore running headers, page numbers, footers).\n"
    'Respond with ONLY a JSON object: {{"lines": ["line 1", "line 2", ...]}}'
)
TAIL_PROMPT = (
    "You are shown one page from a SEC 10-K filing rendered as an image.\n"
    'This page contains the END of "Item {n}. {title}". The next section is "Item {nn}. {ntitle}".\n'
    'Transcribe VERBATIM the last 5 lines of "Item {n}" body text before the next item begins:\n'
    'if the "Item {nn}" heading appears on this page, take the 5 lines just before it; '
    "otherwise use the bottom of the page "
    "(ignore running headers, page numbers, footers).\n"
    'Respond with ONLY a JSON object: {{"lines": ["line 1", "line 2", ...]}}'
)

_JSON = re.compile(r"\{.*\}", re.S)


def next_item_cues(content_items: list[dict]) -> dict[str, tuple[str, str]]:
    """依 content.json 的完整 item 順序建立下一個 item cue（不只 extracted）。"""
    seq = [it for it in content_items if it.get("item_number")]
    cues: dict[str, tuple[str, str]] = {}
    for cur, nxt in zip(seq, seq[1:]):
        cues[str(cur["item_number"])] = (str(nxt["item_number"]), nxt.get("item_title", "") or "")
    return cues


def pick_item(label: str) -> dict:
    """挑一個中段 extracted item，回傳它與下一個 item 的錨定資訊 + 起訖頁 PNG。"""
    folder = DATASET / label
    pages = json.loads((folder / f"{label}_pages.json").read_text(encoding="utf-8"))
    content = json.loads((folder / f"{label}_content.json").read_text(encoding="utf-8"))
    titles = {it["item_number"]: it.get("item_title", "")
              for it in content["items"] if it.get("status") == "extracted"}
    cues = next_item_cues(content["items"])
    rng = [it for it in pages["items"] if it.get("start_page") and it.get("end_page")]
    i = min(len(rng) // 2, len(rng) - 2)  # 中段，且保證有「下一個」
    cur = rng[i]
    nn, ntitle = cues.get(cur["item"], ("", ""))
    return {
        "label": label,
        "n": cur["item"], "title": titles.get(cur["item"], ""),
        "nn": nn, "ntitle": ntitle,
        "start_page": cur["start_page"], "end_page": cur["end_page"],
        "folder": folder,
    }


def page_text(folder: Path, label: str, page_no: int) -> str:
    doc = fitz.open(folder / f"{label}.pdf")
    txt = norm(doc.load_page(page_no - 1).get_text())
    doc.close()
    return txt


def parse_lines(raw: str) -> list[str] | None:
    """從模型回應抽出 lines[]，容忍 ```json 圍欄與前後雜訊。"""
    m = _JSON.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    lines = obj.get("lines")
    if not isinstance(lines, list) or not lines:
        return None
    out = [str(x).strip() for x in lines if str(x).strip()]
    return out or None


def _ask(reader: VLMImageReader, image: Path, prompt: str,
         retries: int, force: bool) -> tuple[str | None, str | None]:
    """回 (raw, error)。429 暫時上游限流會退避重試。"""
    for attempt in range(retries):
        try:
            return reader.read_image(image, prompt=prompt, max_tokens=1024, force=force), None
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if "429" in msg and attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  429 限流，{wait}s 後重試（{attempt + 1}/{retries}）…")
                time.sleep(wait)
                continue
            return None, msg
    return None, "重試後仍 429（上游持續限流）"


def _check_one(reader, image, prompt, folder, label, page_no, retries, force) -> dict:
    """打一次 + 解析 + grounding，回單端結果。"""
    raw, err = _ask(reader, image, prompt, retries, force)
    if err:
        return {"ok": False, "stage": "api", "error": err}
    lines = parse_lines(raw)
    if lines is None:
        return {"ok": False, "stage": "json", "error": "無法解析出非空 lines[]", "raw": raw[:200]}
    grounded = fuzz.partial_ratio(norm(" ".join(lines)), page_text(folder, label, page_no))
    return {"ok": grounded >= 60, "stage": "grounding", "n_lines": len(lines),
            "grounding": round(grounded, 1), "sample": lines[:2]}


def probe_one(model: str, spec: dict, retries: int = 4, force: bool = False) -> dict:
    reader = VLMImageReader(model=model, cache_dir="feedback/external_reference_validation/vlm_cache")
    folder, label = spec["folder"], spec["label"]
    head_img = folder / "pages" / f"page_{spec['start_page']:03d}.png"
    tail_img = folder / "pages" / f"page_{spec['end_page']:03d}.png"
    head_p = HEAD_PROMPT.format(n=spec["n"], title=spec["title"])
    tail_p = TAIL_PROMPT.format(n=spec["n"], title=spec["title"],
                                nn=spec["nn"], ntitle=spec["ntitle"])
    print(f"\n[{model}] head→p{spec['start_page']}")
    head = _check_one(reader, head_img, head_p, folder, label, spec["start_page"], retries, force)
    print(f"[{model}] tail→p{spec['end_page']}")
    tail = _check_one(reader, tail_img, tail_p, folder, label, spec["end_page"], retries, force)
    return {"model": model, "head": head, "tail": tail,
            "ok": head["ok"] and tail["ok"]}


def _verdict(side: dict) -> str:
    if side["ok"]:
        return f"✅ {side['n_lines']}行 grounding={side['grounding']}"
    if side["stage"] == "api":
        return f"❌ API:{side['error'][:45]}"
    if side["stage"] == "json":
        return "❌ 非JSON/無lines[]"
    return f"⚠ grounding={side['grounding']} 偏低（疑似沒讀圖）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=DEFAULT_MODEL,
                    help="逗號分隔的 OpenRouter model slug（預設 gemma-4-31b-it:free）")
    ap.add_argument("--label", default="RELL_2025", help="取樣的 filing")
    ap.add_argument("--force", action="store_true", help="略過快取重打（舊快取可能被截斷）")
    args = ap.parse_args()

    spec = pick_item(args.label)
    print(f"[probe] {spec['label']} — 錨定 Item {spec['n']}「{spec['title']}」"
          f"（起 p{spec['start_page']} / 訖 p{spec['end_page']}），"
          f"下一個 Item {spec['nn']}「{spec['ntitle']}」")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results = [probe_one(m, spec, force=args.force) for m in models]

    print(f"\n{'model':<40} | head            | tail")
    print("-" * 90)
    for r in results:
        print(f"{r['model']:<40} | {_verdict(r['head']):<15} | {_verdict(r['tail'])}")
        if r["head"]["ok"]:
            print(f"{'':<40} |   head e.g. {r['head']['sample']}")
        if r["tail"]["ok"]:
            print(f"{'':<40} |   tail e.g. {r['tail']['sample']}")

    ok = sum(1 for r in results if r["ok"])
    print(f"\n通過（頭尾皆過）：{ok}/{len(results)}（過 = 可進 pass@k harness）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
