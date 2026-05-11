"""
patterns.py
全專案所有 Regex Pattern 的集中定義。

分成四個區塊：
  1. Item 結構資料（ITEM_NUMBERS、ITEM_META）
  2. Parser 用：Item 標題偵測、終止邊界
  3. Postprocessor 用：status 分類偵測
  4. Preprocessing 用：頁碼/頁眉清除、HTML 清洗

修改規則：
  - 只在這個檔案新增或調整 pattern，其他模組只做 import
  - 每個 pattern 必須附上說明和範例
"""

from __future__ import annotations
import re

# ══════════════════════════════════════════════════════════════
# 1. Item 結構資料
# ══════════════════════════════════════════════════════════════

# 所有合法的 Item 編號（按照 10-K 順序）
ITEM_NUMBERS: list[str] = [
    "1", "1A", "1B", "1C",
    "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14",
    "15", "16",
]

# Item 編號 → (Part, 標準標題)
ITEM_META: dict[str, tuple[str, str]] = {
    "1":   ("Part I",   "Business"),
    "1A":  ("Part I",   "Risk Factors"),
    "1B":  ("Part I",   "Unresolved Staff Comments"),
    "1C":  ("Part I",   "Cybersecurity"),
    "2":   ("Part I",   "Properties"),
    "3":   ("Part I",   "Legal Proceedings"),
    "4":   ("Part I",   "Mine Safety Disclosures"),
    "5":   ("Part II",  "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities"),
    "6":   ("Part II",  "Reserved"),
    "7":   ("Part II",  "Management's Discussion and Analysis of Financial Condition and Results of Operations"),
    "7A":  ("Part II",  "Quantitative and Qualitative Disclosures About Market Risk"),
    "8":   ("Part II",  "Financial Statements and Supplementary Data"),
    "9":   ("Part II",  "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure"),
    "9A":  ("Part II",  "Controls and Procedures"),
    "9B":  ("Part II",  "Other Information"),
    "9C":  ("Part II",  "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections"),
    "10":  ("Part III", "Directors, Executive Officers and Corporate Governance"),
    "11":  ("Part III", "Executive Compensation"),
    "12":  ("Part III", "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters"),
    "13":  ("Part III", "Certain Relationships and Related Transactions, and Director Independence"),
    "14":  ("Part III", "Principal Accountant Fees and Services"),
    "15":  ("Part IV",  "Exhibits, Financial Statement Schedules"),
    "16":  ("Part IV",  "Form 10-K Summary"),
}

# Item 編號的 alternation 字串，供各 pattern 共用
_NUM_ALT = r"1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16"

# ══════════════════════════════════════════════════════════════
# 2. Parser 用 Pattern
# ══════════════════════════════════════════════════════════════

# ── 2a. 文件終止邊界 ──────────────────────────────────────────
# 用來截斷最後一個 Item 的範圍，避免把 SIGNATURES / PART 標頭吃進內容。
# 範例匹配："\nSIGNATURES\n"、"\nPART III\n"、"\nTABLE OF CONTENTS\n"
TERMINAL_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:"
    r"SIGNATURES?"  
    r"|EXHIBIT INDEX?"   # 有些公司把附件列表寫在最後，當成終止邊界
    r"|INDEX TO CONSOLIDATED FINANCIAL STATEMENTS"  # FCX 的獨特終止語
    r"|INDEX TO FINANCIAL STATEMENTS"
    r"|CONSOLIDATED"
    r"|CONSOLIDATED FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA"  # 有些公司（如 MSFT）把財報放在最後，當成終止邊界
    r"|EXHIBIT INDEX"
    r"|EXHIBIT"
    r"|GLOSSARY(?:\s+OF\s+TERMS?)?"     # 詞彙表
    r")\s*\n",
    re.IGNORECASE | re.MULTILINE,
)

# ── 2b. 標準 Item 標題 ────────────────────────────────────────
# 範例匹配："\nItem 1." / "\nITEM 1A:" / "\nItem 7A—"
# 分隔符允許 . : - — – tab 或換行；換行格式（純頁簽）以 _EXPLICIT_SEP 區分品質
ITEM_PATTERN = re.compile(
    rf"""
    (?:^|\n)                        # 行首或換行
    \s*                             # 可能有前置空白
    (?:ITEM|Item|item)              # ITEM 關鍵字
    \s+                             # 必須有空白
    (?P<num>{_NUM_ALT})             # Item 編號（多字元優先）
    \s*                             # 數字後可能有空白
    [.:\-—–\t\n]                    # 分隔符
    """,
    re.VERBOSE | re.MULTILINE,
)

# ── 2c. 合併 Item 標題 ────────────────────────────────────────
# 範例匹配："Items 1. and 2. Business and Properties"
# 偵測後為兩個 Item 各建一筆 RawItem，共用同一個 start_pos
COMBINED_ITEM_PATTERN = re.compile(
    rf"""
    (?:^|\n)
    \s*
    (?:ITEMS|Items|items)               # 複數 Items
    \s+
    (?P<num1>{_NUM_ALT})                # 第一個 Item 編號
    \s*\.?\s*
    and
    \s+
    (?:(?:ITEM|Item|item)\s+)?          # 第二個可選擇帶 "Item"
    (?P<num2>{_NUM_ALT})                # 第二個 Item 編號
    \s*[.\-:—–\t\n]
    """,
    re.VERBOSE | re.MULTILINE | re.IGNORECASE,
)

# ── 2d. PART + Item 同行格式 ──────────────────────────────────
# 範例匹配："Page PART I Item 1. Business"
# 也容忍 OCR 黏字："PARTIITEM 1."
# 必須有 PART [羅馬數字] 作為錨點，避免誤抓正文引用
PART_ITEM_PATTERN = re.compile(
    rf"""
    (?:^|\n)
    [^\n]*?                             # 行首可有任意前綴（Page、公司名…）
    PART\s*[IVX]+\s*                    # PART I / II / III / IV；容忍 PARTIITEM 黏字
    (?:ITEM|Item|item)\s+
    (?P<num>{_NUM_ALT})
    \s*[.:\-—–\t\n]
    """,
    re.VERBOSE | re.MULTILINE | re.IGNORECASE,
)

# ── 2e. Candidate 品質判斷 ────────────────────────────────────
# 有明確分隔符（. : - —）→ 正文標題（高品質）
# 只有換行/tab → 頁簽或目錄行（低品質）
# 範例匹配："\nITEM 1A." → 高品質；"\nItem 1A\n" → 低品質
EXPLICIT_SEP_PATTERN = re.compile(r"\d[A-C]?\s*[.:\-—–]")
# if re.search(r"\b(see|refer to|as described in|discussed in|above)\b", context)
REFERENCE_PATTERN = re.compile(
    r"\b(?:see|under|refer\s+to|as\s+described\s+in|discussed\s+in|above|below|following|herein|aforementioned|attached\s+hereto|included\s+herewith|pursuant\s+to|in\s+accordance\s+with)\b",
    re.IGNORECASE,
)

# ── 2f. Table 內是否含 Item 標題 ─────────────────────────────
# 用於 preprocessing：若 <table> 內有 Item 標題，轉純文字讓 parser 能抓到
ITEM_IN_TABLE_PATTERN = re.compile(
    rf"\bITEM\s+(?:{_NUM_ALT})\b",
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════════
# 3. Postprocessor 用 Pattern
# ══════════════════════════════════════════════════════════════

# ── 3a. Incorporated by Reference ────────────────────────────
# 只在 Part III（Item 10–14）內使用
# 範例匹配："incorporated herein by reference" / "incorporated by reference from"
BY_REF_PATTERN = re.compile(
    r"incorporat(?:ed|ion)\s+(?:herein\s+)?by\s+reference|"
    r"hereby\s+incorporat(?:ed|ion)\s+by\s+reference|"
    r"incorporat(?:ed|ion)\s+by\s+reference\s+(?:from|to|herein)",
    re.IGNORECASE,
)

# ── 3b. Not Applicable ───────────────────────────────────────
# 範例匹配："Not applicable." / "N/A" / "None."
NOT_APPLICABLE_PATTERN = re.compile(
    r"\bnot\s+applicable\b|\bn\.?a\.?\b|\bnone\b",
    re.IGNORECASE,
)

# ── 3c. Reserved ─────────────────────────────────────────────
# 範例匹配："[Reserved]" / "Reserved."
RESERVED_PATTERN = re.compile(
    r"\breserved\b",
    re.IGNORECASE,
)

# ── 3d. Mine Safety Not Applicable ───────────────────────────
# Item 4 專用；非採礦業通常寫這些短語
MINE_SAFETY_NA_PATTERN = re.compile(
    r"not\s+applicable|none|"
    r"no\s+(?:mine|mining)|"
    r"company\s+(?:does\s+not|has\s+no)\s+(?:own|operate|have)",
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════════
# 4. Preprocessing 用 Pattern
# ══════════════════════════════════════════════════════════════

# ── 4a. HTML Tag 清除 ─────────────────────────────────────────
# 用於 postprocessor：剝除 HTML 後再做 pattern 偵測
HTML_TAG_PATTERN = re.compile(r"<[^>]+>", re.DOTALL)

# ── 4b. 頁碼清除（獨立成行）────────────────────────────────────
# 範例匹配："\n  56  \n" / "\n- 56 -\n"
PAGE_NUMBER_PATTERN = re.compile(
    r"\n[^\S\r\n]*[-‒–—]*\d+[-‒–—]*[^\S\r\n]*\n",
    re.MULTILINE,
)

# ── 4c. 純數字行（補漏）──────────────────────────────────────
PAGE_NUMBER_BARE_PATTERN = re.compile(
    r"\n[^\S\r\n]*\d+[^\S\r\n]*\n",
    re.MULTILINE,
)

# ── 4d. 財務報表頁碼 F-1, F-2 ────────────────────────────────
# 範例匹配："\nF-1" / " F-12"
FINANCIAL_PAGE_PATTERN = re.compile(
    r"[\n\s]F[-‒–—]*\d+",
    re.MULTILINE,
)

# ── 4e. "Page N" 格式 ────────────────────────────────────────
# 範例匹配："\nPage 56\n"
PAGE_WORD_PATTERN = re.compile(
    r"\n[^\S\r\n]*Page\s[\d*]+[^\S\r\n]*\n",
    re.MULTILINE,
)

# ── 4f. 頁眉格式（公司名 | 年份 Form 10-K | 頁碼）──────────────
# 範例匹配："Apple Inc. | 2024 Form 10-K | 56"
PAGE_HEADER_PATTERN = re.compile(
    r"\n[^\S\r\n]*.{3,120}\|\s*\d+\s*\n",
    re.MULTILINE,
)

# ── 4g. HTML inline 斷字修復 ──────────────────────────────────
# HTML <span> 等 inline 元素有時把單字切成多行，例如：
#   "I\nTEM 10." → "ITEM 10."  /  "PA\nRT I" → "PART I"
# 規則：若某行只有 1–5 個大寫字母，則與下一行合併（無空格）。
# 範例匹配：獨立成行的 "I" 後接 "TEM 10." → "ITEM 10."
SPLIT_UPPERCASE_PATTERN = re.compile(
    r"(?m)^([A-Z]{1,5})\n([A-Z])",
)
