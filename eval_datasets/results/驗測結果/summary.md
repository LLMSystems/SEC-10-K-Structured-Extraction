# 評估結果 — 2026-05-11 23-46-54

## 整體結果：⚠️ WARNING

| 指標 | 數值 |
|---|---|
| Status 準確率 | 100.0% |
| Critical Regressions | 0 |
| Warning 數 | 5 |
| 評估 Filing 數 | 35 |

## 平均耗時

| 步驟 | 平均耗時（秒） |
|---|---|
| 下載 HTML | 0.159 |
| 預處理（preprocess） | 0.494 |
| 解析（parse） | 0.030 |
| 後處理（postprocess） | 0.005 |
| **合計** | **0.687** |

## Status 混淆矩陣

| GT \ Pred | extracted | incorporated_by_reference | missing | not_applicable | reserved |
|---|---|---|---|---|---|
| **extracted** | 494 | 0 | 0 | 0 | 0 |
| **incorporated_by_reference** | 0 | 153 | 0 | 0 | 0 |
| **missing** | 0 | 0 | 18 | 0 | 0 |
| **not_applicable** | 0 | 0 | 0 | 97 | 0 |
| **reserved** | 0 | 0 | 0 | 0 | 26 |

## 內容品質統計

**Length Ratio 統計（pred 長度 / GT 長度，僅 extracted items）：**

| 指標 | 數值 |
|---|---|
| 樣本數 | 489 |
| 平均值 | 101.868 |
| 中位數 | 1.000 |
| P10 | 1.000 |
| P90 | 1.000 |

| 範圍 | 條件 | 數量 | 佔比 |
|---|---|---|---|
| 🔴 嚴重過短 | < 0.7 | 3 | 0.6% |
| 🟡 略短 | 0.7–0.85 | 0 | 0.0% |
| ✅ 正常 | 0.85–1.15 | 484 | 99.0% |
| 🟡 略長 | 1.15–1.3 | 0 | 0.0% |
| 🔴 嚴重過長 | > 1.3 | 2 | 0.4% |

**頭尾比對通過率（僅 GT & Pred 皆為 extracted）：**

| 比對位置 | 通過數 | 總數 | 通過率 |
|---|---|---|---|
| 頭部（前 150 字）| 486 | 487 | 99.8% |
| 尾部（後 150 字）| 487 | 487 | 100.0% |

## 各 Item 錯誤率排行

| Item | 錯誤數 | 總數 | 錯誤率 |
|---|---|---|---|
| 9C | 0 | 35 | ✅ 0.0% |
| 9B | 0 | 35 | ✅ 0.0% |
| 9A | 0 | 35 | ✅ 0.0% |
| 9 | 0 | 35 | ✅ 0.0% |
| 8 | 0 | 35 | ✅ 0.0% |
| 7A | 0 | 35 | ✅ 0.0% |
| 7 | 0 | 35 | ✅ 0.0% |
| 6 | 0 | 35 | ✅ 0.0% |
| 5 | 0 | 35 | ✅ 0.0% |
| 4 | 0 | 35 | ✅ 0.0% |
| 3 | 0 | 35 | ✅ 0.0% |
| 2 | 0 | 35 | ✅ 0.0% |
| 1C | 0 | 18 | ✅ 0.0% |
| 1B | 0 | 35 | ✅ 0.0% |
| 1A | 0 | 35 | ✅ 0.0% |
| 16 | 0 | 35 | ✅ 0.0% |
| 15 | 0 | 35 | ✅ 0.0% |
| 14 | 0 | 35 | ✅ 0.0% |
| 13 | 0 | 35 | ✅ 0.0% |
| 12 | 0 | 35 | ✅ 0.0% |
| 11 | 0 | 35 | ✅ 0.0% |
| 10 | 0 | 35 | ✅ 0.0% |
| 1 | 0 | 35 | ✅ 0.0% |

## 各 Filing 概覽

| Ticker | 年份 | Filer Category | Status 準確率 | Critical | Warning | 等級 |
|---|---|---|---|---|---|---|
| AAPL | 2021 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| AAPL | 2023 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| AAPL | 2025 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| CISO | 2021 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| CISO | 2023 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| CISO | 2025 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| DENN | 2019 | Accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| DENN | 2021 | Accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| DENN | 2023 | Accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| GDC | 2021 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| GDC | 2023 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| GDC | 2025 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| HURC | 2021 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| HURC | 2023 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| HURC | 2025 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| JPM | 2025 | Large accelerated filer | 100.0% | 0 | 1 | ⚠️ WARNING |
| NFLX | 2023 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| NFLX | 2024 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| NFLX | 2025 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| RELL | 2021 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 1 | ⚠️ WARNING |
| RELL | 2023 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| RELL | 2025 | Accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| TSLA | 2020 | Large accelerated filer | 100.0% | 0 | 1 | ⚠️ WARNING |
| TSLA | 2021 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| TSLA | 2023 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| TSLA | 2025 | Large accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| VRA | 2021 | Non-accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| VRA | 2023 | Non-accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| VRA | 2025 | Non-accelerated filer | 100.0% | 0 | 0 | ✅ PASS |
| WMT | 2023 | Large accelerated filer | 100.0% | 0 | 1 | ⚠️ WARNING |
| WMT | 2026 | Large accelerated filer | 100.0% | 0 | 1 | ⚠️ WARNING |
| WSTL | 2016 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| WSTL | 2018 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| WSTL | 2020 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |
| WSTL | 2021 | Non-accelerated filer<br>Smaller reporting company | 100.0% | 0 | 0 | ✅ PASS |

## 問題明細

### JPM 2025 — ⚠️ WARNING

**Status 比對：**

| Item | GT Status | Pred Status | 嚴重程度 | 說明 |
|---|---|---|---|---|
| 15 | extracted | extracted | 🟡 warning | 內容嚴重過長（ratio=49327.14，> 1.3） |

**Length Ratio 異常（±15% 以上）：**

| Item | GT 長度 | Pred 長度 | 比值 | 方向 |
|---|---|---|---|---|
| 15 | 49 | 2,417,030 | 49327.14 ⚠️ | 越界 ⬆️ |

### RELL 2021 — ⚠️ WARNING

**Status 比對：**

| Item | GT Status | Pred Status | 嚴重程度 | 說明 |
|---|---|---|---|---|
| 15 | extracted | extracted | 🟡 warning | 內容嚴重過短（ratio=0.65，< 0.7）；頭部不吻合（起始邊界可能偏移） |

**Length Ratio 異常（±15% 以上）：**

| Item | GT 長度 | Pred 長度 | 比值 | 方向 |
|---|---|---|---|---|
| 15 | 2,408 | 1,573 | 0.65 ⚠️ | 截短 ⬇️ |

### TSLA 2020 — ⚠️ WARNING

**Status 比對：**

| Item | GT Status | Pred Status | 嚴重程度 | 說明 |
|---|---|---|---|---|
| 9B | extracted | extracted | 🟡 warning | 內容嚴重過長（ratio=1.38，> 1.3） |

**Length Ratio 異常（±15% 以上）：**

| Item | GT 長度 | Pred 長度 | 比值 | 方向 |
|---|---|---|---|---|
| 9B | 34 | 47 | 1.38 ⚠️ | 越界 ⬆️ |

### WMT 2023 — ⚠️ WARNING

**Status 比對：**

| Item | GT Status | Pred Status | 嚴重程度 | 說明 |
|---|---|---|---|---|
| 1 | extracted | extracted | 🟡 warning | 內容嚴重過短（ratio=0.00，< 0.7） |

**Length Ratio 異常（±15% 以上）：**

| Item | GT 長度 | Pred 長度 | 比值 | 方向 |
|---|---|---|---|---|
| 1 | 52,640 | 0 | 0.00 ⚠️ | 截短 ⬇️ |

### WMT 2026 — ⚠️ WARNING

**Status 比對：**

| Item | GT Status | Pred Status | 嚴重程度 | 說明 |
|---|---|---|---|---|
| 1 | extracted | extracted | 🟡 warning | 內容嚴重過短（ratio=0.00，< 0.7） |

**Length Ratio 異常（±15% 以上）：**

| Item | GT 長度 | Pred 長度 | 比值 | 方向 |
|---|---|---|---|---|
| 1 | 42,285 | 0 | 0.00 ⚠️ | 截短 ⬇️ |
