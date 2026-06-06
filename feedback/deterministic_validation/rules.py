"""
Tier 1 規則（4 條）。

每條規則吃一份 Parse，回傳觸發的 Flag 清單；空清單代表通過。
規則只用 char_range（整數幾何）與 content_text 長度，全程 JSON-only。

定義要點：「ranged item」= 有 char_range 的 item，含 incorporated_by_reference
（GT 中 by_reference 也帶 range，會佔住文字空間，故視為「已指派」）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Parse, canon_index

# ── 門檻 ──────────────────────────────────────────
# 規則 3：相鄰已指派 item 間的空隙上限。GT 實測合法空隙最大 413，取 1000 留安全邊際。
GAP_THRESHOLD = 1000
# 規則 3b：核心 item（GT 中 100% 被找到，漏掉即錯）。出處：validator-rules.md 的 CORE_ITEMS。
CORE_ITEMS = {"1", "1A", "2", "3", "5", "7", "8", "9A", "15"}
# 規則 4：extracted 內容字數總和地板。先驗低標，遠低於任何真實 10-K。
CONTENT_FLOOR = 5000


@dataclass
class Flag:
    rule: str
    item_number: str
    message: str


# ── 規則 1：區間合法 0 ≤ start < end ──────────────
def rule_1_range_valid(parse: Parse) -> list[Flag]:
    flags = []
    for it in parse.items:
        if it.char_range is None:
            continue
        s, e = it.char_range
        if not (0 <= s < e):
            flags.append(Flag("rule_1_range_valid", it.item_number, f"非法區間 ({s}, {e})"))
    return flags


# ── 規則 2：按編號排序後 start 單調、區間不重疊 ────
def rule_2_monotonic(parse: Parse) -> list[Flag]:
    flags = []
    items = parse.ranged_by_canon()
    for a, b in zip(items, items[1:]):
        sa, ea = a.char_range
        sb, _ = b.char_range
        if sb <= sa:
            flags.append(Flag("rule_2_monotonic", b.item_number,
                              f"start 未遞增：{a.item_number}@{sa} → {b.item_number}@{sb}"))
        elif ea > sb:
            flags.append(Flag("rule_2_monotonic", b.item_number,
                              f"區間重疊：{a.item_number} end={ea} > {b.item_number} start={sb}"))
    return flags


# ── 規則 3：漏抓偵測 = 3a 空隙 OR 3b 核心完整性 ───
def rule_3_gap(parse: Parse, threshold: int = GAP_THRESHOLD) -> list[Flag]:
    """3a：相鄰已指派 item 間不該有大段未指派文字（抓內部、夠大的漏抓）。"""
    flags = []
    items = parse.ranged_items()  # 按 start 位置排序
    for a, b in zip(items, items[1:]):
        gap = b.char_range[0] - a.char_range[1]
        if gap > threshold:
            flags.append(Flag("rule_3_gap", b.item_number,
                              f"{a.item_number} 與 {b.item_number} 間有 {gap} 字未指派"))
    return flags


def rule_3_core_missing(parse: Parse) -> list[Flag]:
    """3b：核心 item 不得漏掉（補空隙抓不到的邊界/極小漏抓）。允許 not_applicable / reserved。"""
    accounted = {it.item_number for it in parse.items if it.status != "missing"}
    return [
        Flag("rule_3_core_missing", c, f"核心 item {c} 漏掉")
        for c in CORE_ITEMS if c not in accounted
    ]


def rule_3_coverage(parse: Parse, threshold: int = GAP_THRESHOLD) -> list[Flag]:
    """規則 3 = 3a 空隙 + 3b 核心完整性。"""
    return rule_3_gap(parse, threshold) + rule_3_core_missing(parse)


# ── 規則 4：extracted 內容字數總和不得低於地板 ─────
def rule_4_content_floor(parse: Parse, floor: int = CONTENT_FLOOR) -> list[Flag]:
    s = sum(len(it.content_text or "") for it in parse.extracted_items())
    if s < floor:
        return [Flag("rule_4_content_floor", "-", f"extracted 內容總和 {s} < 地板 {floor}")]
    return []


RULES = {
    "rule_1_range_valid": rule_1_range_valid,
    "rule_2_monotonic": rule_2_monotonic,
    "rule_3_coverage": rule_3_coverage,
    "rule_4_content_floor": rule_4_content_floor,
}
