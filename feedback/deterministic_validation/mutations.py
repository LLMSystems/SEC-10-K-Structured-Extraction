"""
Mutation operators：在 gold parse 上注入「單一類別」的錯誤，產生 wrong parse。

每個 operator 回傳 list[Mutant]，一個 Mutant = 一份被改壞的 Parse + 對應 metadata，
供 runner 計算偵測率（recall），並可依被改 item 的大小分桶分析。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .model import Item, Parse, canon_index


@dataclass
class Mutant:
    parse: Parse
    operator: str
    target: str          # 被改的 item_number
    target_size: int     # 被改 item 的原始 char 長度（規則 3 分桶用）


def _clone(parse: Parse) -> Parse:
    return copy.deepcopy(parse)


# ── 規則 1：幾何不可能的區間 ──────────────────────
def reverse(parse: Parse) -> list[Mutant]:
    out = []
    for i, it in enumerate(parse.items):
        if it.char_range is None:
            continue
        m = _clone(parse)
        s, e = m.items[i].char_range
        m.items[i].char_range = (e, s)  # 反向
        out.append(Mutant(m, "reverse", it.item_number, it.size))
    return out


def zero(parse: Parse) -> list[Mutant]:
    out = []
    for i, it in enumerate(parse.items):
        if it.char_range is None:
            continue
        m = _clone(parse)
        s, _ = m.items[i].char_range
        m.items[i].char_range = (s, s)  # 零長度
        out.append(Mutant(m, "zero", it.item_number, it.size))
    return out


def neg_start(parse: Parse) -> list[Mutant]:
    out = []
    for i, it in enumerate(parse.items):
        if it.char_range is None:
            continue
        m = _clone(parse)
        _, e = m.items[i].char_range
        m.items[i].char_range = (-1, e)  # 負起點
        out.append(Mutant(m, "neg_start", it.item_number, it.size))
    return out


# ── 規則 2：順序錯位 / 重疊 ───────────────────────
def _ranged_canon_idx(parse: Parse) -> list[int]:
    """回傳 parse.items 中有 range 的索引，按 canon 順序排列。"""
    idx = [i for i, it in enumerate(parse.items) if it.char_range is not None]
    return sorted(idx, key=lambda i: canon_index(parse.items[i].item_number))


def swap(parse: Parse) -> list[Mutant]:
    out = []
    order = _ranged_canon_idx(parse)
    for k in range(len(order) - 1):
        i, j = order[k], order[k + 1]
        m = _clone(parse)
        m.items[i].char_range, m.items[j].char_range = (
            m.items[j].char_range, m.items[i].char_range)
        out.append(Mutant(m, "swap", parse.items[j].item_number, parse.items[j].size))
    return out


def overlap(parse: Parse, delta: int = 1000) -> list[Mutant]:
    out = []
    order = _ranged_canon_idx(parse)
    for k in range(len(order) - 1):
        i, j = order[k], order[k + 1]
        m = _clone(parse)
        si, _ = m.items[i].char_range
        sj, _ = m.items[j].char_range
        m.items[i].char_range = (si, sj + delta)  # 吃進下一個 item
        out.append(Mutant(m, "overlap", parse.items[j].item_number, parse.items[j].size))
    return out


def displace(parse: Parse) -> list[Mutant]:
    """把某個 item 的區間搬到最前面，製造非單調。"""
    out = []
    order = _ranged_canon_idx(parse)
    for k in range(1, len(order)):  # 跳過第一個
        i = order[k]
        m = _clone(parse)
        m.items[i].char_range = (0, 50)  # 搬到開頭
        out.append(Mutant(m, "displace", parse.items[i].item_number, parse.items[i].size))
    return out


# ── 規則 3：漏抓一個 item（parser 根本沒產出它）────
def omit_item(parse: Parse) -> list[Mutant]:
    """把一個 ranged item 整個從輸出移除（模擬 parser 沒找到 → item 不在輸出）。
    鄰居 range 不動 → 該 item 原本佔的文字變成無人認領的空隙。"""
    out = []
    for i, it in enumerate(parse.items):
        if it.char_range is None:
            continue
        m = _clone(parse)
        del m.items[i]
        out.append(Mutant(m, "omit_item", it.item_number, it.size))
    return out


# ── 規則 4：內容被清空 / 抓錯檔 ───────────────────
def gut(parse: Parse) -> list[Mutant]:
    """清空所有 extracted item 的內容。"""
    m = _clone(parse)
    for it in m.items:
        if it.status == "extracted":
            it.content_text = ""
    return [Mutant(m, "gut", "-", 0)]


def keep_one(parse: Parse) -> list[Mutant]:
    """只留一個短 item，模擬只解析到封面。"""
    m = _clone(parse)
    kept = False
    for it in m.items:
        if it.status == "extracted":
            if not kept:
                it.content_text = (it.content_text or "")[:100]
                kept = True
            else:
                it.content_text = ""
    return [Mutant(m, "keep_one", "-", 0)]


# 每條規則對應的 operators
OPERATORS = {
    "rule_1_range_valid": [reverse, zero, neg_start],
    "rule_2_monotonic": [swap, overlap, displace],
    "rule_3_coverage": [omit_item],
    "rule_4_content_floor": [gut, keep_one],
}
