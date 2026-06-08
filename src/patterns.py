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
# 含字母後綴的編號允許數字與字母間插入句點（如 "Item 9.A." 為 "Item 9A." 的常見變體）；
# 句點變體必須排在無句點變體之前，否則 "9" 會先比對成功並把 ".A" 留在標題裡
# （見 _find_candidates 的 .replace(".", "") 正規化，會把 "9.A" 還原成 "9A"）。
_NUM_ALT = r"1\.A|1\.B|1\.C|9\.A|9\.B|9\.C|7\.A|1C|1A|1B|9C|9A|9B|7A|1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16"

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
# 範例匹配："\nItem 1." / "\nITEM 1A:" / "\nItem 7A—" / "\nItem 9A(T)." / "\nItem 1a." / "\nITEM 9a." / "\nITEM1."
# 分隔符允許 . : - — – tab 或換行；換行格式（純頁簽）以 _EXPLICIT_SEP 區分品質
# 編號後可能有過渡期條款括號註記，如 "9A(T)"（2008-2010 年間常見格式）
# 部分申報文件把字母後綴寫成小寫（如 "Item 1a."、"ITEM 9a."），故開啟 IGNORECASE；
# _find_candidates 已用 .upper() 正規化編號，不受影響。
# 少數申報文件在 "ITEM" 與編號間沒有空白（如 "ITEM1.   BUSINESS"），故將原本要求至少
# 一個空白的 \s+ 放寬為 \s*；風險低，因為 (?:^|\n)\s* 已限制 "ITEM" 必須出現在行首，
# 一般英文散文不會有 "ITEM" 緊接數字的行首字串。
ITEM_PATTERN = re.compile(
    rf"""
    (?:^|\n)                        # 行首或換行
    \s*                             # 可能有前置空白
    (?:ITEM|Item|item)              # ITEM 關鍵字
    \s*                             # 與編號間可能沒有空白（如 "ITEM1."）
    (?:\[\[ANCHOR:[^\]]*\]\]\s*)?   # pipeline 注入的錨點標記可能切在 "Item" 與編號之間
                                    # （如 "Item\n\n[[ANCHOR:item9a]]\n\n9A. Controls..."，
                                    # 因原始 HTML 把錨點目標元素只包住編號部分）
    (?P<num>{_NUM_ALT})             # Item 編號（多字元優先，大小寫不拘）
    \s*                             # 數字後可能有空白
    (?:\([A-Za-z]{{1,3}}\)\s*)?     # 可選括號註記，如 "(T)"
    [.:\-—–\t\n]                    # 分隔符
    """,
    re.VERBOSE | re.MULTILINE | re.IGNORECASE,
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
# 偵測對象 table_text_compact 用 get_text("", strip=True) 取得（無分隔字元，
# 目的是合併 inline 斷字如 "I"+"TEM"="ITEM"），但副作用是相鄰儲存格文字會直接
# 黏在一起，例如標題格 "Item 1" 與標題格 "Business" 黏成 "Item 1Business"。
# 原本的結尾 \b 會因為數字後緊接英文字母（同屬 \w）而判定無字界，導致比對失敗，
# 使本應「轉純文字」的單一標題 table 被誤判為「保留原始 HTML」，讓殘留的
# HTML 標籤滲入最終文字、破壞 ITEM_PATTERN 偵測（例如 CNET、ACONW、HIG-PG）。
# 改用 (?!\d) 取代 \b：只禁止「數字後緊接數字」（避免把 "Item 10" 誤判成
# "Item 1" + "0"，仍可靠 _NUM_ALT 的多字元 alternative 透過回溯比對到 "10"），
# 但允許「數字後緊接英文字母」視為比對成功（因為這正是黏接造成的常態）。
ITEM_IN_TABLE_PATTERN = re.compile(
    rf"\bITEM\s+(?:{_NUM_ALT})(?!\d)",
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

# ── 3a-2. Item 8 財報以「見另頁 / F-pages」方式呈現 ───────────
# 實際財報置於文件他處（如 Item 15 之後的 F-pages），Item 8 僅留一段指標文字。
# 範例："See Index to Consolidated Financial Statements"
#       "Reference is made to Pages ... of this ... Form 10-K"
#       "...appear on pages 162-314"
#       "This information appears following Item 15 ... and is included herein by reference"
#       "...are listed in the Index to the Financial Statements..."
#       "...is included as a separate section of this Annual Report..."
#       "...are filed under this Item, beginning on page..."
#       "...are appended to this report. An index ... is found in Item 15."
#       "...are attached hereto as Exhibit A"
#       "...are filed as part of this report"
#       "...required by this item are located in PART IV of this Annual Report"
#       "...is submitted in response to Part IV below. See the Index to Consolidated..."
# 樣本顯示真實內嵌財報段落極長（中位數 ≈74k 字），故下列片語只在搭配長度上限
# （見 postprocessor._classify）時才視為 by_reference 訊號，不會誤判正常內文。
FIN_STMT_BY_REF_PATTERN = re.compile(
    r"see\s+(?:the\s+)?index\s+to\b|"
    r"listed\s+in\s+(?:the\s+)?index\s+to\b|"
    r"see\s+financial\s+statements?\s+included\s+in\b|"
    r"reference\s+is\s+made\s+to\s+pages?\b|"
    r"appears?\s+(?:on|beginning\s+on)\s+pages?\b|"
    r"appears?\s+following\s+item\b|"
    r"set\s+forth\s+(?:on|beginning\s+on)\s+pages?\b|"
    r"(?:is|are)\s+set\s+forth\s+in\s+part\s+iv\b|"
    r"incorporat(?:ed|ion)\s+(?:herein\s+)?by\s+reference|"
    r"incorporated\s+into\s+this\s+item\s+\d\w*\s+by\s+reference|"
    r"(?:is|are)\s+included\s+herein\s+by\s+reference|"
    r"included\s+as\s+a\s+separate\s+section\b|"
    r"filed\s+under\s+this\s+item\b|"
    r"filed\s+as\s+part\s+of\s+this\s+report\b|"
    r"appended\s+to\s+this\s+report\b|"
    r"located\s+in\s+part\s+iv\b|"
    r"attached\s+hereto\b",
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

# ── 4h. "Item" 字詞斷字修復（混合大小寫、斷點不固定）──────────
# 部分申報文件把 "Item" 從 inline span 中間斷開且維持原始大小寫，斷點因標題而異，例如：
#   <span>It</span><span>em 1. Business.</span>        → "It\nem 1. Business."
#   <span>Ite</span><span>m 3. Legal Proceedings.</span> → "Ite\nm 3. Legal Proceedings."
# SPLIT_UPPERCASE_PATTERN 只處理全大寫斷字，無法匹配混合大小寫的 "It"/"Ite"，故另立此 pattern，
# 允許在 "Item" 四個字母間任意插入換行。限定後面緊接數字，避免誤合併正常字句。
# 範例匹配："It\nem 1A." / "Ite\nm 3." → 還原為 "Item"
SPLIT_ITEM_WORD_PATTERN = re.compile(
    r"\bI\n?t\n?e\n?m(?=\s*\d)",
)
