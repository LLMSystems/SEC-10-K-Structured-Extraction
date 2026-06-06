# 來源 C：VLM 首尾段驗證 — 結果與分桶邏輯

> 計畫：[vlm_first_last_line_plan.md](vlm_first_last_line_plan.md)　程式：[vlm_first_last.py](vlm_first_last.py)
> 階段：✅ **k=1 跨 14 模型 survey 完成**（找邏輯、定基準、初步選型）；⏭ pass@k/pass^k harness 待跑
> 資料：[dataset/](dataset/) 5 份高信心 10-K　報告：[report/vlm_first_last/](report/vlm_first_last/)

## TL;DR（k=1 排行，依 tail 由高到低）

head 已近飽和（多數 62/62），**tail 是選型主軸**。下表 50 = prose 桶 tail 總數，62 = head 總數。

| 排名 | 模型 | tail | head | 備註 |
|---:|---|---|---|---|
| 1 | **gemini-3-flash-preview** | **50/50 (100%)** | 62/62 | 唯一完整滿分 |
| 2 | **qwen3.6-plus** | 49/50 (98%) | 62/62 | 只剩 NFLX-7 |
| 3 | gemini-3.1-flash-lite | 48/50 (96%) | 62/62 | |
| 3 | kimi-k2.6 | 48/50 (96%) | 60/62 | head 略弱 |
| 5 | qwen3.6-27b | 47/50 (94%) | 62/62 | |
| 5 | gemini-2.5-pro | 47/50 (94%) | 61/62 | 需開 reasoning（豁免）|
| 7 | qwen3.5-27b | 46/50 (92%) | 62/62 | |
| 7 | gemini-2.5-flash | 46/50 (92%) | 60/62 | |
| 9 | qwen3.5-122b-a10b | 45/50 (90%) | 62/62 | 大模型未拉開 |
| 10 | gemma-4-31b | 43/50 (86%) | 59/62 | |
| 10 | qwen3.5-35b-a3b | 43/50 (86%) | 61/62 | |
| 10 | qwen3.5-9b | 43/50 (86%) | 62/62 | |
| 13 | gemma-4-26b-a4b-it | 32/50 (64%) | 52/62 | 明顯掉隊 |

> `kimi-k2.6:free` 免費層限流、殘留 38 個錯誤，未列入排名（付費版 `kimi-k2.6` 已在表中）。
> harness 候選優先取前段：`gemini-3-flash-preview`、`qwen3.6-plus`、`gemini-3.1-flash-lite`、`kimi-k2.6`。

對每個 extracted item，把它**起始頁**與**結束頁**的 PNG 餵 VLM，請它逐字轉錄該 item 的開頭/結尾，
再與 GT `content_text` 的首/末文字塊比對。一致 → 邊界 PASS。

- **比對**：兩邊各去 HTML、正規化成**連續文字**（非逐行——`content_text` 沒有「視覺行」概念），
  GT 取頭/尾 300 字窗口，VLM 取整段轉錄，用 `fuzz.partial_ratio`。**門檻 TH=75**
  （經驗空隙：正確讀 81~100、真錯誤 ≤50，75 兩邊留裕度）。
- **head 對齊**：GT 先用 `strip_heading()` 剝掉開頭「Item N. 標題」——VLM 被要求略過標題給正文，兩邊要對齊。
- **輸出不要求 JSON**：10-K 內文滿是引號，小模型常把 JSON 跳脫寫壞；既然只需文字塊，直接吐純文字。

---

## 分桶邏輯（核心）

**為什麼要分桶**：item 結尾若是**表格或圖**，去 HTML 後是標籤湯 / 一串數字，無法逐字比對，
逐塊比會假性 fail。所以依**尾段型態**決定 tail 怎麼驗。head 三桶都驗（item 開頭幾乎都是 prose）。

### 1. 尾段型態偵測 `tail_kind(content_text)` → prose / table / figure

| 型態 | 判定規則 |
|---|---|
| **table** | `content_text` 去空白後以 HTML 標籤 `>` 收尾，**或** 末 1500 字去 HTML 後可讀字母 < 300 |
| **figure** | 末 400 字出現**連續 ≥4 個金額/日期軸標記**（`$250 $200 …` 或 `5/30/15 5/28/16 …`，績效圖特徵；prose 的數字夾在句中、不連續） |
| **prose** | 其餘 |

### 2. 桶 = 型態 × 有無後繼

```
prose                    → head + tail 全驗（乾淨主訊號）
{table|figure}_tail_covered → tail 不驗：邊界由「下一個 item 的 head」覆蓋（同一條分界線）
{table|figure}_tail_last    → tail 不驗且為最後一個 item → 誠實標記未驗證
```

**關鍵原理**：item N 的「結束邊界」與 item N+1 的「開始邊界」是文件裡**同一條線**。
所以即使 N 結束於 table/figure、尾段沒法逐字比，只要 **N+1 的 head 通過，那條邊界就被覆蓋**。
唯一真正驗不到的，是「結束於 table/figure 且是最後一個 item」。

> 5 份實際分布：figure 僅 RELL item 5（績效圖）；table 落在 item 2/3/8/14 等（財報、廠房表、Part III 表）；
> 其餘皆 prose。中段有表的 item（如 item 7 MD&A）尾段仍是 prose，歸 prose 桶、正常驗。

---

## 結果（k=1，temperature=0；已回填 page-range 與 next-item cue 修正）

每格為「該 filing 的 head 通過 / tail 通過（僅 prose 桶）」。

| filing | bucket（prose/tbl_cov/tbl_last/fig_cov） | 總計（head/tail） | gemma-4-31b | gemma-4-26b-a4b-it | qwen3.5-9b | qwen3.5-27b | qwen3.5-35b-a3b | qwen3.5-122b-a10b | qwen3.6-27b | qwen3.6-plus | gemini-2.5-flash | gemini-2.5-pro | gemini-3-flash-preview | gemini-3.1-flash-lite | kimi-k2.6 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RELL_2025 | 8 / 2 / 0 / 1 | 11 / 8 | 11/11 · 7/8 | 10/11 · 4/8 | 11/11 · 8/8 | 11/11 · 8/8 | 11/11 · 6/8 | 11/11 · 8/8 | 11/11 · 7/8 | 11/11 · 8/8 | 11/11 · 8/8 | 11/11 · 8/8 | 11/11 · 8/8 | 11/11 · 7/8 | 10/11 · 8/8 |
| GDC_2023 | 10 / 5 / 1 / 0 | 16 / 10 | 15/16 · 10/10 | 12/16 · 8/10 | 16/16 · 9/10 | 16/16 · 10/10 | 16/16 · 9/10 | 16/16 · 9/10 | 16/16 · 10/10 | 16/16 · 10/10 | 16/16 · 10/10 | 16/16 · 9/10 | 16/16 · 10/10 | 16/16 · 10/10 | 16/16 · 10/10 |
| NFLX_2025 | 11 / 0 / 0 / 0 | 11 / 11 | 11/11 · 11/11 | 11/11 · 8/11 | 11/11 · 9/11 | 11/11 · 10/11 | 11/11 · 10/11 | 11/11 · 9/11 | 11/11 · 10/11 | 11/11 · 10/11 | 10/11 · 9/11 | 10/11 · 11/11 | 11/11 · 11/11 | 11/11 · 11/11 | 11/11 · 11/11 |
| TSLA_2023 | 12 / 0 / 0 / 0 | 12 / 12 | 11/12 · 7/12 | 10/12 · 7/12 | 12/12 · 11/12 | 12/12 · 11/12 | 12/12 · 10/12 | 12/12 · 11/12 | 12/12 · 12/12 | 12/12 · 12/12 | 11/12 · 11/12 | 12/12 · 11/12 | 12/12 · 12/12 | 12/12 · 11/12 | 11/12 · 10/12 |
| WMT_2026 | 9 / 3 / 0 / 0 | 12 / 9 | 11/12 · 8/9 | 9/12 · 5/9 | 12/12 · 6/9 | 12/12 · 7/9 | 11/12 · 8/9 | 12/12 · 8/9 | 12/12 · 8/9 | 12/12 · 9/9 | 12/12 · 8/9 | 12/12 · 8/9 | 12/12 · 9/9 | 12/12 · 9/9 | 12/12 · 9/9 |
| **head 合計** | | **62** | **59/62 (95%)** | **52/62 (84%)** | **62/62 (100%)** | **62/62 (100%)** | **61/62 (98%)** | **62/62 (100%)** | **62/62 (100%)** | **62/62 (100%)** | **60/62 (97%)** | **61/62 (98%)** | **62/62 (100%)** | **62/62 (100%)** | **60/62 (97%)** |
| **tail 合計** | | **50** | **43/50 (86%)** | **32/50 (64%)** | **43/50 (86%)** | **46/50 (92%)** | **43/50 (86%)** | **45/50 (90%)** | **47/50 (94%)** | **49/50 (98%)** | **46/50 (92%)** | **47/50 (94%)** | **50/50 (100%)** | **48/50 (96%)** | **48/50 (96%)** |

- **head 幾乎飽和**：多數強模型已到 `62/62`；較明顯的 head 弱點集中在 `gemma`、`gemini-2.5-flash`、`kimi-k2.6`，以及明顯較弱的 `gemma-4-26b-a4b-it`。
- **tail 仍是主要鑑別點**：目前最佳是 `gemini-3-flash-preview` **50/50 (100%)**，其次 `qwen3.6-plus` **49/50 (98%)**，再來是 `gemini-3.1-flash-lite` / `kimi-k2.6` **48/50 (96%)**，以及 `gemini-2.5-pro` / `qwen3.6-27b` **47/50 (94%)**。
- **Gemini 3 / Qwen 3.6 Plus 明顯拉開**：`gemini-3-flash-preview` 是這組 5 份 benchmark 上第一個完整滿分；`qwen3.6-plus` 只剩 `NFLX-7` 一筆頁首短尾巴 case 沒過。
- **`gemini-2.5-pro` 能跑，但要特殊處理**：在 OpenRouter 上這個端點要求 reasoning 開啟，不能沿用全域 `reasoning.enabled=False`；修正後成績為 `61/62 · 47/50`。
- **兩個已定位的邏輯錯誤已被消掉**：`TSLA-1C`（page-range 少 1 頁）修正後，gemma / qwen3.6 由 fail 變 pass；`GDC-9A`（tail cue 誤指到 Item 10）修正後，兩個 Qwen 都由 fail 變 pass。
- **殘餘難題仍可解釋**：`NFLX-7` 仍是多個 Qwen 系模型的共同弱點；`TSLA` 的幾個長 prose tail 仍是較能拉開模型差距的區域。
- **新增的兩個 Qwen 3.5 小模型沒有超過 27b 基線**：`qwen3.5-35b-a3b` 為 `61/62 · 43/50`，`qwen3.5-9b` 為 `62/62 · 43/50`；兩者 head 不差，但 tail 明顯落後 `qwen3.5-27b` 的 `46/50`。
- **`qwen3.5-122b-a10b` 觀察**：雖然 head 62/62，但 tail 沒有超過 `qwen3.5-27b` / `qwen3.6-27b`；主要失分在 `GDC-2`、`NFLX-1A/7`、`TSLA-1`、`WMT-7`。
- **`gemma-4-26b-a4b-it` 明顯掉隊**：`52/62 · 32/50`，不只 tail 弱，head 也和其他主流模型拉開差距。
- 各 filing 互有勝負（不同模型錯不同 item）正是 pass@k/pass^k 要量化的變異。

**失敗的 item（可追溯）**

| 模型 | head 失 | tail 失（prose 桶）|
|---|---|---|
| gemma-4-31b | GDC-1；TSLA-5；WMT-5 | RELL-1C；TSLA-1,2,7,8,9A；WMT-3 |
| gemma-4-26b-a4b-it | RELL-7；GDC-1,1A,7A,9A；TSLA-2,5；WMT-1A,5,7 | RELL-1,1A,1C,7；GDC-2,5；NFLX-1A,7,7A；TSLA-1,2,7,8,9A；WMT-3,7A,15 |
| qwen3.5-9b | （無）| GDC-2；NFLX-7,7A；TSLA-1；WMT-3,7,15 |
| qwen3.5-27b | （無）| NFLX-7；TSLA-1C；WMT-3,15 |
| qwen3.5-35b-a3b | WMT-5 | RELL-1A,15；GDC-1A；NFLX-7；TSLA-1,5；WMT-7A |
| qwen3.6-27b | （無）| NFLX-7；RELL-7；WMT-7 |
| qwen3.5-122b-a10b | （無）| GDC-2；NFLX-1A,7；TSLA-1；WMT-7 |
| gemini-2.5-flash | NFLX-5；TSLA-5 | NFLX-7,9B；TSLA-8；WMT-15 |
| gemini-2.5-pro | NFLX-5 | GDC-5；TSLA-8；WMT-7A |
| gemini-3-flash-preview | （無）| （無） |
| gemini-3.1-flash-lite | （無）| RELL-1C；TSLA-2 |
| qwen3.6-plus | （無）| NFLX-7 |
| kimi-k2.6 | RELL-5；TSLA-5 | TSLA-1,1C |

tail 失多為「VLM 讀到頁面中段的段落、過短尾詞，或把頁底短 section 當成目標 item 的結尾」；在 cue 修正後，`GDC-9A` 類的**越界到後繼短 section**問題已明顯消失。

---

## 過程中找到並修掉的邏輯

| 問題 | 怎麼發現 | 修法 |
|---|---|---|
| **page-range end 抓錯**（7A off-by-one、9B overshoot 跨過 by_reference 的 10–14）| VLM tail 告警「這頁是審計報告不是 7A」| [item_page_ranges.py](item_page_ranges.py)：end 改用 item 自己尾段錨點 `min` 下一個 extracted 起點 |
| **page-range 少抓 1 頁**（`TSLA-1C` 實際尾段在下一頁上半部）| `tail` fail；對照 [TSLA_2023_pages.json](dataset/TSLA_2023/TSLA_2023_pages.json) 與 [page_031.png](dataset/TSLA_2023/pages/page_031.png) 發現 GT 尾段落在 next page top | [item_page_ranges.py](item_page_ranges.py)：新增「next page before next heading」續文檢查，若下一頁標題前文字仍像目前 item 尾段，則把 `end_page` 升到下一頁 |
| **head 假 fail**（GT 含「Item N. 標題」，VLM 略過標題 → 拖低分數）| 7/9A head 卡 76~81，pred 其實讀對 | `strip_heading()` 剝掉 GT 開頭標題對齊 VLM |
| **figure 尾段**（績效圖壓平成軸標記，當 prose 比會 fail）| RELL item 5 tail 持續 ~44 | `tail_kind` 加 figure 偵測（連續軸標記）→ figure_tail_covered |
| **tail cue 指到錯的下一節**（`GDC-9A` 用 `Item 10` 當 cue，實際頁面先出現 `9B/9C`）| qwen 兩模型都越界去抄 `9B/9C`；gemma 偶然自行停在 `9A` 正確結尾 | [vlm_first_last.py](vlm_first_last.py)、[vlm_probe.py](vlm_probe.py)：`next item cue` 改從 `content.json` 的**完整 item 順序**取，而非只看 extracted items（例：`9A→9B`、`9B→9C`） |
| **空回應**（qwen 在併發下間歇回空 response）| qwen batch=8 大量 `empty response` | `_ask` 把空回應也當暫時性錯誤退避重試（重試 force 繞快取）|
| **JSON 跳脫寫壞**（內文引號多，小模型跳脫錯誤）| gemma 回 `{"lines":[..\"..]}` 解析失敗 | prompt 改吐純文字、不要 JSON |

**還沒修、屬後續評估項的**：
- `partial_ratio` 對**過短答案**偏寬鬆：例如 `qwen3.6` 在修正後的 `GDC-9A` 只回很短尾詞也可能拿到高分，scorer 之後要再收緊。
- gemma 雖然在 cue 修正後 tail 已升到 43/50，但 head/tail 仍偶有回歸，後續仍要和更強模型比較穩定度。

**營運經驗**：
- qwen3.5-27b 的 provider 在 **batch=8 下空回應率偏高**，補跑用 **batch=2** 即穩。flaky provider 宜降併發。
- `google/gemini-2.5-pro` 在 OpenRouter 上 **必須開 reasoning**；若強制送 `reasoning.enabled=False` 會直接回 `400 BadRequest`。因此 [vlm_reader.py](vlm_reader.py) 現在對這個模型做了豁免處理。

---

## §7.3 偵測力（錯誤注入證明）

> 程式：[vlm_inject_eval.py](vlm_inject_eval.py)　計畫：[detection_eval_plan.md](detection_eval_plan.md)　報告：[report/detection/](report/detection/)

survey 只證明了「對**正確** GT 會 PASS」，沒證明「對**錯誤**邊界會 FAIL」——驗證器的漏抓率原本未知。
§7.3 對 GT 邊界**注入錯誤**，量偵測率（recall）與誤殺率（FP）。

- **做法**：只汙染 GT 側（＝ parser 的邊界宣稱），image/VLM 不動 → **重用 survey 的快取轉錄、零 API**。
- **運算子**：`truncate_head/tail`（少抓）、`overrun_head/tail`（越界進相鄰 item）。
- **嚴重度**（以 content_text 行為單位，只測實質截斷）：**50 行**（絕對）、**50%**（比例）；刻意不測小截斷。
- **判定**：`detected ⟺ clean PASS 且注入後 FAIL`（純門檻跨越；ratio Δ 僅輔助）。

每格 = `50行% / 50%` 偵測率；FP = clean 時誤判 FAIL 數。

| 模型 | FP head | FP tail | truncate_head | overrun_head | truncate_tail | overrun_tail |
|---|---|---|---|---|---|---|
| gemini-3-flash-preview | 0/62 | 0/50 | 90 / 84 | 90 / 90 | 86 / 96 | 88 / 82 |
| qwen3.6-plus | 0/62 | 1/50 | 90 / 84 | 90 / 90 | 82 / 98 | 88 / 76 |
| gemini-3.1-flash-lite | 0/62 | 2/50 | 90 / 84 | 90 / 90 | 90 / 96 | 88 / 83 |
| kimi-k2.6 | 2/62 | 2/50 | 83 / 75 | 90 / 90 | 92 / 100 | 88 / 77 |
| qwen3.6-27b | 0/62 | 2/50 | 90 / 84 | 90 / 90 | 92 / 98 | 88 / 81 |

- **結論**：對有意義的邊界錯誤，偵測率 **75–100%**，誤殺 **0–2/112**——「對的 PASS、錯的 FAIL」成立，四種錯誤 × 五模型都穩。補回了驗證器最關鍵的缺口。
- **盲區（已量化）**：12 個 item（table/figure 尾段不驗 tail，占 ~19%）的尾端錯誤**結構性抓不到**，需 Source A/B 補。
- **誠實邊界**：在 5 份乾淨 filing、對代理 GT（`content_text`）上量；小截斷刻意不測（可接受漏抓）。

---

## 下一步

- 生產候選優先：`gemini-3-flash-preview`、`qwen3.6-plus`、`gemini-3.1-flash-lite`、`kimi-k2.6`（k=1 最強且 §7.3 偵測穩）。
- **把驗證器接到真實 parser 輸出**（目前都對 GT 跑＝驗證驗證器；尚未對 parser 抓錯）。
- 補 **Source A（頁碼單調）/ B（TOC）**：覆蓋 C 的盲區（table/figure 尾段、漏抓整個 item），並啟用三角驗證。
