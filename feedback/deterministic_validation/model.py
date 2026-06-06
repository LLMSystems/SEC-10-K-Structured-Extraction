"""
資料模型與 Ground Truth 載入。

把每份 GT JSON 視為一份「正確的解析輸出」（gold parse），
之後 mutation 會在此基礎上注入單一類別的錯誤。

設計：全程只讀 JSON（char_range / content_text / status），不碰 _fulltext.md。
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# SEC 規範的 Item 標準順序（規則 2 用來判斷「按編號排序」）
CANON_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A",
    "8", "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16",
]
_CANON_INDEX = {num: i for i, num in enumerate(CANON_ORDER)}


def canon_index(item_number: str) -> int:
    """回傳 item 在標準順序中的位置；未知編號排到最後。"""
    return _CANON_INDEX.get(item_number.upper(), len(CANON_ORDER))


@dataclass
class Item:
    item_number: str
    status: str
    char_range: Optional[tuple[int, int]]
    content_text: Optional[str]

    @property
    def has_range(self) -> bool:
        return self.char_range is not None

    @property
    def size(self) -> int:
        if self.char_range is None:
            return 0
        return self.char_range[1] - self.char_range[0]


@dataclass
class Parse:
    """一份解析輸出（gold 或 mutated）。"""
    filing_id: str
    items: list[Item] = field(default_factory=list)

    def ranged_items(self) -> list[Item]:
        """有 char_range 的 item（extracted + incorporated_by_reference），按 start 位置排序。"""
        items = [it for it in self.items if it.has_range]
        return sorted(items, key=lambda it: it.char_range[0])

    def ranged_by_canon(self) -> list[Item]:
        """有 char_range 的 item，按 SEC 標準編號順序排序。"""
        items = [it for it in self.items if it.has_range]
        return sorted(items, key=lambda it: canon_index(it.item_number))

    def extracted_items(self) -> list[Item]:
        return [it for it in self.items if it.status == "extracted"]


def _load_one(path: str) -> Parse:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    filing_id = "/".join(Path(path).parts[-3:-1])  # 例如 CISO/2021
    items = []
    for raw in data["items"]:
        cr = raw.get("char_range")
        items.append(
            Item(
                item_number=raw["item_number"].upper(),
                status=raw["status"],
                char_range=tuple(cr) if cr else None,
                content_text=raw.get("content_text"),
            )
        )
    return Parse(filing_id=filing_id, items=items)


def load_ground_truth(root: str = "eval_datasets/ground_truth") -> list[Parse]:
    files = sorted(glob.glob(f"{root}/*/*/*.json"))
    parses = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "items" not in data:
            continue
        parses.append(_load_one(f))
    return parses
