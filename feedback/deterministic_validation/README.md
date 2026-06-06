# Tier 1 規則驗證

用 34 份人工標註 Ground Truth 證明每條 Tier 1 規則的主張：**「違反 → 高信心錯」**。
設計與理路見 [validation_plan.md](validation_plan.md)。

## 執行

```bash
python -m feedback.deterministic_validation.runner
```

全程只讀 `eval_datasets/ground_truth/*/*/*.json`（不碰 `_fulltext.md`）。

把錯誤資料寫成檔案（可檢視、可重現）：

```bash
python -m feedback.deterministic_validation.runner --dump   # 輸出到 feedback/deterministic_validation/mutants/
```

每個 operator 一個 JSON（陣列），每筆 = 一份被改壞的 parse + metadata
（`source` / `operator` / `target` / `expected_flag` / `actually_flagged`）；
`content` 只存長度避免檔案爆大。索引見 `mutants/manifest.json`。

## 結構

| 檔案 | 內容 |
|---|---|
| `model.py` | GT 載入 + 資料模型（`Item` / `Parse`，含 SEC 標準編號順序） |
| `rules.py` | 4 條規則，吃一份 Parse 回傳觸發的 Flag |
| `mutations.py` | 錯誤注入 operator，在 gold 上製造 wrong parse |
| `runner.py` | 跑精確度（FP）+ 偵測率（recall）+ 關鍵診斷 |

## 兩個測試

- **精確度 / 不誤殺**：34 份正確 GT 直接跑 → 期望零觸發。**能否證**：一份正確 GT 觸發就推翻主張。
- **偵測率 / 抓得到**：在 GT 上注入對應錯誤 → 期望觸發。只能支持、不能證普世。

## 規則與 operator

| 規則 | 定義 | 注入 operator |
|---|---|---|
| 1 區間合法 | `0 ≤ start < end` | reverse / zero / neg_start |
| 2 單調非重疊 | 按編號排序後 start 遞增、區間不重疊 | swap / overlap / displace |
| 3 漏抓偵測 | **3a 空隙**：相鄰已指派 item 間空隙 ≤ 門檻；**3b 完整性**：核心 item 不得漏掉 | omit_item |
| 4 內容地板 | `Σ len(content_text)` ≥ 先驗地板 | gut / keep_one |

> 規則 2、3 把 `incorporated_by_reference` 也算「已指派」——GT 中 by_reference 帶 char_range，會佔住文字空間。
> 規則 3 的核心 item = `{1,1A,2,3,5,7,8,9A,15}`（GT 中 100% 被找到，允許 not_applicable / reserved，漏掉即錯）。

## 實測結果

**精確度：4 條規則在 34 份 GT 全部 FP = 0。**

| 規則 | FP | 偵測率 | 證明力 |
|---|---|---|---|
| 1 區間合法 | 0/34 | 100% | 低（純邏輯，注入即觸發屬套套邏輯） |
| 2 單調非重疊 | 0/34 | 100%（swap/overlap/displace） | **高**：錯誤寫實（選錯候選），精確度跑在真實資料 |
| 3 漏抓偵測 | 0/34 | 見下 | 中：FP=0；對「負責範圍」recall=100%，界外有意識地不收 |
| 4 內容地板 | 0/34 | 100%（gut/keep_one） | 中：靠先驗地板，安全邊際 30.6× |

**規則 3 的偵測率拆解（升級後）**：omit_item = 把某個 item 整個從輸出移除。

| 漏抓的 item | 偵測率 | 由誰接管 |
|---|---|---|
| 核心 item（任意位置/大小，含 Item 1 / 15 邊界） | **303/303 = 100%** | 3b 完整性 |
| 非核心 ・ 內部 ・ 大於門檻 | **97/97 = 100%** | 3a 空隙 |
| 非核心 ・ 邊界或小於門檻（**界外**） | 1/142 ≈ 1% | 不收（見下） |

整體 raw recall 74%，但**對規則 3 負責的範圍（核心 item + 內部大漏抓）已是 100%**；74% 只是被「刻意界外」的 142 個非核心小/邊界 item 拉低。

**為什麼界外那 142 個不硬收**：要收它們得把完整性擴大到「所有 item」，但非核心 item 在乾淨 GT 本就常合法缺席（not_applicable / missing 共 18 筆），硬收必然 FP > 0 → 破壞「違反就一定錯」。所以交給 Tier 2，是有意識的劃界。

**升級重點**：原本純空隙規則對「第一/最後一個 item 被漏抓」是盲區（無鄰居 → 無空隙）；加上 3b 核心完整性後，邊界核心 item（Item 1、Item 15）改由完整性接管 → 盲區補上，且 FP 仍 = 0。

**關鍵診斷**：規則 3 合法空隙最大 413 字（門檻 1000，邊際 2.4×）；規則 4 內容總和最小 153k（地板 5k，邊際 30.6×）。

## 結論

- **規則 1、4**：主張成立，但靠定義/先驗門檻，GT 主要作回歸保證。
- **規則 2**：此驗證法最能服人的範例——錯誤寫實、雙向可驗。
- **規則 3**：FP=0；升級成「空隙 + 核心完整性」後，對其負責範圍 recall=100%（含原本的邊界盲區）。剩下的非核心小/邊界漏抓有意識地劃為界外，交給 Tier 2，硬收會破壞 FP=0。
