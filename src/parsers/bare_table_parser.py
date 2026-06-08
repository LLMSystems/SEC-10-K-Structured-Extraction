r"""
BareTableParser
處理「正文沒有章節標題、所有 Item 以單一 HTML 表格逐列陳列」的 10-K。

典型格式（信託/SPE 空殼申報，例如 KTH）：
  <table>
    <tr><td><div><u>Item 1.</u></div> Business</td><td>Not applicable.</td></tr>
    <tr><td><div><u>Item 1A.</u></div> Risk Factors</td><td>None.</td></tr>
    ...
  </table>

這類表格內容多、被 pipeline 判定為「資料型 table」而保留原始 HTML
（見 pipeline.py 的 item_refs > 3 分支），但 item 標題被 <div><u>...</u></div>
等標籤包住，導致 ITEM_PATTERN 的行首錨定（(?:^|\n)\s*Item）失敗，
RegexParser 完全找不到這些 Item。

策略：
  1. 偵測 normalized_html 是否含「裸表格」：表格內 distinct item 提及數量
     達到門檻、且完全沒有 hash anchor（<a href="#...">）——藉此與 TOC 表格區分
  2. 直接在 doc.text（殘留 HTML 標籤的版本）用 ITEM_IN_TABLE_PATTERN 掃描，
     因為 \b 在 "<u>Item" 之間仍然成立，可繞過行首錨定限制
  3. 依 ITEM_NUMBERS canonical 順序、貪婪比對位置遞增的出現點，
     確保結果單調遞增、不會把交叉引用誤認成標題
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.models import FilingMetadata, RawItem, RawSpan, PreprocessedDocument
from src.parsers.base import BaseParser, ParseResult
from src.patterns import ITEM_META, ITEM_NUMBERS, ITEM_IN_TABLE_PATTERN

# 表格內至少要找到這麼多種不同 item 編號，才視為「裸表格」（排除一般資料表格
# 偶然提到少數幾個 item 名稱的情況，例如財報附註索引、exhibit 列表）
_MIN_DISTINCT_ITEMS = 8

_ITEM_PREFIX_RE = re.compile(r"(?i)^ITEM\s*")

# 「Form 10-K Index」之類的目錄表格也會逐列陳列 "Item 1 / Item 1A / ... / Item 16"
# （搭配 "Item Number" 與 "Page(s)" 欄位標題，內容是指向外部文件的頁碼範圍，
# 而非本文揭露內容）。這種表格同樣會通過 distinct-item 數量門檻，但其列出的
# 位置只是索引條目本身，不是實際 item 內容的所在位置 —— 必須排除，否則會
# 把整份索引表誤判成裸表格陳列式申報（如 FMCCI）。
_INDEX_TABLE_HINT_RE = re.compile(r"\bItem\s+Number\b|\bPage\(s\)", re.IGNORECASE)


class BareTableParser(BaseParser):
    """
    適用於正文無 "Item X." 節標題、所有 Item 擠在同一個 HTML 表格內陳列的 10-K
    （多為信託/SPE 等空殼實體，內容極簡，逐項聲明 None / Not Applicable）。
    """

    @property
    def name(self) -> str:
        return "bare_table"

    def parse(self, doc: PreprocessedDocument, metadata: FilingMetadata) -> ParseResult:
        html = doc.normalized_html or ""
        text = doc.text
        warnings: list[str] = []

        if not self._has_bare_item_table(html):
            return self._make_result([], ["No bare item table found"])

        expected = [n for n in ITEM_NUMBERS if n != "1C" or metadata.has_item_1c]

        # 收集所有候選 (item_number → [(位置, 比對到的文字), ...])
        occurrences: dict[str, list[tuple[int, str]]] = {}
        for m in ITEM_IN_TABLE_PATTERN.finditer(text):
            num = self._extract_num(m.group(0))
            if num in ITEM_META:
                occurrences.setdefault(num, []).append((m.start(), m.group(0)))

        if not occurrences:
            return self._make_result([], ["Bare table detected but no item references found in text"])

        # 貪婪依序比對：沿 canonical 順序走，每個 item 取「位置大於上一個已選位置」
        # 的第一個出現點 → 保證結果單調遞增，避免把交叉引用誤判成標題
        picks: dict[str, tuple[int, str]] = {}
        last_pos = -1
        for num in expected:
            for pos, matched in occurrences.get(num, []):
                if pos > last_pos:
                    picks[num] = (pos, matched)
                    last_pos = pos
                    break

        if len(picks) < _MIN_DISTINCT_ITEMS:
            return self._make_result(
                [], [f"Only {len(picks)} items aligned sequentially; likely not a bare-table filing"]
            )

        missing = [n for n in expected if n not in picks]
        if missing:
            warnings.append(f"未找到以下 Item：{missing}")

        ordered_nums = sorted(picks, key=lambda n: picks[n][0])
        raw_items: list[RawItem] = []
        for i, num in enumerate(ordered_nums):
            start, matched = picks[num]
            end = picks[ordered_nums[i + 1]][0] if i + 1 < len(ordered_nums) else len(text)
            raw_items.append(RawItem(
                item_number=num,
                title_text=matched.strip(),
                start_char=start,
                end_char=end,
                confidence=0.72,
                # 單一 span 標記此 parser 為非線性結構，讓 validator 把排序問題
                # 降級為 info（裸表格內各 item 物理順序本來就照表格列順序而非 canonical 順序）
                spans=[RawSpan(start_char=start, end_char=end)],
            ))

        raw_items.sort(key=lambda x: ITEM_NUMBERS.index(x.item_number))
        return self._make_result(raw_items, warnings)

    # ── 內部方法 ──────────────────────────────────────────────

    def _has_bare_item_table(self, html: str) -> bool:
        """
        判斷是否存在「裸表格」結構：
          - 完全沒有 hash anchor（<a href="#...">）的表格 → 排除 TOC 導覽表格
          - 這些表格合計找得到足夠多種不同的 item 編號
        KTH 之類的申報常把 Item 1-4 / 5-9 / 10-14 拆成數個獨立表格陳列
        （而非塞進同一個表格），故跨表格累計而非單表格判斷。
        """
        soup = BeautifulSoup(html, "html.parser")
        nums = set()
        for table in soup.find_all("table"):
            if table.find("a", href=lambda h: bool(h) and h.startswith("#")):
                continue

            table_text = table.get_text(" ", strip=True)
            if _INDEX_TABLE_HINT_RE.search(table_text):
                continue
            for m in ITEM_IN_TABLE_PATTERN.finditer(table_text):
                num = self._extract_num(m.group(0))
                if num in ITEM_META:
                    nums.add(num)

        return len(nums) >= _MIN_DISTINCT_ITEMS

    def _extract_num(self, matched: str) -> str:
        """將 'Item 9.A' / 'ITEM 10' 等比對文字正規化為 ITEM_META 的 key（如 '9A' / '10'）"""
        return _ITEM_PREFIX_RE.sub("", matched).upper().replace(".", "").strip()
