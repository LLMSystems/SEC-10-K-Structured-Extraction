"""
Tier 2 — item → PDF 頁碼 對照 demo（來源 A 頁碼檢查的基礎）

核心事實：
`doc.text`（preprocess 後的純文字）與渲染出的 PDF 是兩個獨立產物，
字元 offset 之間**沒有**直接對應；Chromium 的 page.pdf 也不吐「字元→頁」資訊。
所以不能用位置算，要用**內容比對**搭橋：
  1. 用 PyMuPDF 取 PDF 每頁的純文字。
  2. 拿每個 item 的內容片段（body，不含標題，避免命中 TOC）去比對命中哪一頁。
  3. 命中的頁 = 該 item 的「起始 PDF 頁」。

得到的是 **PDF 頁序**（1..N），這對來源 A 的「頁碼單調遞增」不變量已足夠
——不需要文件頁尾印製的頁碼。

用法：
    python -m feedback.external_reference_validation.map_offsets \
        --pdf feedback/external_reference_validation/out/<stem>/<stem>.pdf \
        --json eval_datasets/ground_truth/AAPL/2023/<stem>.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz  # PyMuPDF
from rapidfuzz import fuzz

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """正規化：\\xa0 與連續空白併成單一空格、去頭尾、小寫。比對前兩邊都要過。"""
    return _WS.sub(" ", s.replace("\xa0", " ")).strip().lower()


def load_page_texts(pdf_path: Path) -> list[str]:
    """取 PDF 每頁的正規化純文字。"""
    doc = fitz.open(pdf_path)
    texts = [norm(doc.load_page(i).get_text()) for i in range(len(doc))]
    doc.close()
    return texts


_HTML_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^(part\s+[ivxlc]+|item\s+\d+[a-z]?\b)", re.I)


def body_snippet(content_text: str, item_title: str = "", length: int = 70) -> str:
    """
    從 item 內容取一段「body 片段」用於比對，必須避開兩個陷阱：

    1. **TOC 偽命中**：item 標題（如 "Market for ... Common Equity ..."）
       同時出現在目錄頁。若片段落在標題上，會命中 TOC 而非內文頁。
       → 丟掉開頭的 Part/Item 標題行、與 item_title 相符的行、過短行，
       只取真正的內文 prose（TOC 沒有內文 → body 片段天生 TOC-proof）。
       注意：標題可能不以 "Item N." 開頭（如 "5. Market for ..." 或直接標題本文），
       單靠 regex 擋不住，故額外用 GT 的 item_title 精準比對剔除。
    2. **HTML table**：preprocess 把表格以 HTML 字串保留在 content_text（如 Item 8），
       正規化後是標籤湯，PDF 渲染文字裡沒有。→ 先去掉 HTML 標籤再取。
    """
    for snip in iter_body_snippets(content_text, item_title, length):
        return snip  # 第一段夠長的內文 prose
    # 全是標題 / 表格時退而用整段正規化
    return norm(_HTML_TAG.sub(" ", content_text))[:length]


def iter_body_snippets(content_text: str, item_title: str = "", length: int = 70):
    """
    依序產生可用的「body 片段」候選（套用與 body_snippet 相同的過濾）。
    供 best_snippet 在多個候選中挑最獨特的一個。
    """
    text = _HTML_TAG.sub(" ", content_text)
    title_norm = norm(item_title)
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        nln = norm(ln)
        if _HEADING.match(ln) or len(nln) < 25:
            continue  # "Item 5." / "Part II" / 過短行
        if title_norm and (title_norm in nln or nln in title_norm):
            continue  # 標題行
        yield nln[:length]


def body_lines(content_text: str, item_title: str = "", length: int = 70) -> list[str]:
    """item 內容的全部 body 候選行（已過濾標題 / 過短 / HTML）。"""
    return list(iter_body_snippets(content_text, item_title, length))


def _hits(snip: str, page_texts: list[str]) -> int:
    return sum(1 for pt in page_texts if snip in pt)


def head_snippet(
    content_text: str, item_title: str, page_texts: list[str], k: int = 8
) -> tuple[str, int]:
    """
    取「起始錨點」：在開頭 k 個候選行中挑命中頁數最少的（理想唯一）。
    回傳 (snippet, 命中頁數)。對天生重複內容（如 Item 8 財報）最少仍可能 > 1。
    """
    lines = body_lines(content_text, item_title)[:k]
    best: tuple[str, int] | None = None
    for snip in lines:
        h = _hits(snip, page_texts)
        if best is None or h < best[1]:
            best = (snip, h)
        if h == 1:
            break
    return best if best is not None else ("", 0)


def tail_snippet(
    content_text: str, item_title: str, page_texts: list[str], k: int = 8
) -> tuple[str, int]:
    """
    取「結束錨點」：從**最後一行往回**找，優先取最靠近結尾、又唯一命中的行
    （唯一 → 結束頁零歧義）；若末段都不唯一，退而用最後一行。
    """
    lines = body_lines(content_text, item_title)
    if not lines:
        return "", 0
    tail = lines[-k:]
    fallback = (tail[-1], _hits(tail[-1], page_texts))  # 最後一行
    for snip in reversed(tail):  # 從最後一行往回
        if _hits(snip, page_texts) == 1:
            return snip, 1
    return fallback


# 向後相容：舊呼叫點用的「最獨特起始片段」
def best_snippet(content_text: str, item_title: str, page_texts: list[str],
                 max_candidates: int = 8) -> tuple[str, int]:
    return head_snippet(content_text, item_title, page_texts, k=max_candidates)


def find_page(
    snippet: str,
    page_texts: list[str],
    start_from: int = 0,
    fuzzy_threshold: int = 88,
) -> tuple[int | None, str]:
    """
    回傳 (PDF 頁序 1-based, 命中方式)。先試精確子字串，失敗再 fuzzy。
    start_from（0-based 頁索引）：只從這頁起往後找。
    用於「順序搜尋」——GT 的 item 本來就有序，從前一個 item 命中的頁往後找，
    可天然跳過最前面的 TOC（避免標題型 snippet 命中目錄頁的偽命中）。
    """
    if not snippet:
        return None, "empty-snippet"
    n = len(page_texts)
    # 1) 精確子字串
    for i in range(start_from, n):
        if snippet in page_texts[i]:
            return i + 1, "exact"
    # 2) fuzzy 後備（容忍數字格式 / 標點 / 斷字差異）
    best_i, best = -1, 0.0
    for i in range(start_from, n):
        sc = fuzz.partial_ratio(snippet, page_texts[i])
        if sc > best:
            best, best_i = sc, i
    if best >= fuzzy_threshold:
        return best_i + 1, f"fuzzy({best:.0f})"
    return None, f"miss(best={best:.0f})"


def main() -> int:
    ap = argparse.ArgumentParser(description="item → PDF 頁碼 對照 demo")
    ap.add_argument("--pdf", required=True, help="render_demo 產出的 PDF")
    ap.add_argument("--json", required=True, help="含 items[].content_text 的結果 / GT JSON")
    args = ap.parse_args()

    page_texts = load_page_texts(Path(args.pdf))
    print(f"[load] PDF 共 {len(page_texts)} 頁\n")

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    items = data.get("items", [])

    print(f"{'item':>5} | {'status':<26} | {'page':>4} | 命中方式 | snippet")
    print("-" * 100)

    last_page = 0
    violations = 0
    for it in items:
        if it.get("status") != "extracted":
            continue
        ct = it.get("content_text") or ""
        snip = body_snippet(ct, it.get("item_title") or "")
        page, how = find_page(snip, page_texts)

        # 來源 A 不變量：依編號順序，起始頁應單調不遞減
        flag = ""
        if page is not None:
            if page < last_page:
                flag = f"  ⚠ 頁碼倒退（前一個在 p{last_page}）"
                violations += 1
            last_page = page

        page_str = str(page) if page is not None else "—"
        print(f"{it.get('item_number'):>5} | {it.get('status'):<26} | {page_str:>4} | {how:<12} | {snip[:45]!r}{flag}")

    print("-" * 100)
    print(f"頁碼單調性違規數：{violations}（來源 A：>0 代表有 item 定位可疑）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
