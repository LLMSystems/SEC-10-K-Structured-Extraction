# Tier 2：外部參照驗證層（報告草稿）

> 規則來源：[plan.md](../plan.md)（Tier 2 規則 7）　計畫：[validation_plan.md](validation_plan.md)　與 [deterministic validation report](../deterministic_validation/deterministic_validation_report.md) 平行
> 程式與結果：[external_reference_validation/](./)（驗證器、資料集、報告）

## 結論

Tier 2 的核心問題是：**是否能利用 VLM 作為外部參照，對 parser 的 item 起訖定位進行高信心檢查**。本報告的結論是：可以。
在 5 份高信心 GT 的驗證樣本上，VLM 首尾段比對已能作為一個實用的 parser 檢查器，最佳模型在正確資料上達到
**head 62/62、tail 50/50**，其餘前段模型也維持低誤報與高偵測率。

若以目前表現最佳的 `gemini-3-flash-preview` 作為實際部署候選，根據本次實驗結果，它可以在這組 Tier 2 驗證樣本上達成：
- **clean benchmark 零誤報**：head **62/62**、tail **50/50**。
- **邊界錯誤偵測率高**：對 50 行 / 50% 等級的注入錯誤，`truncate_head` **90% / 84%**、`overrun_head` **90% / 90%**、`truncate_tail` **86% / 96%**、`overrun_tail` **88% / 82%**。
- 使用 VLM 作為驗證器，對 parser 抽出的 item 起點與終點做一層高信心的外部複查；這裡的「高信心」具體表現在：對正確資料可做到 **0 誤殺（head 62/62、tail 50/50）**，而對刻意注入的邊界錯誤，四類主要失敗模式仍維持 **82–96%** 的偵測率，足以幫助我們發現「抓錯開頭、尾段被截掉、或越界讀到下一節」這類 parser 自身難以自證的錯誤。

以成本來看，`gemini-3-flash-preview` 跑完整 5 份樣本約花費 **US$0.11**；折算下來，每份 filing 約 US 0.022，
以 `1 USD ≈ 31.5 TWD` 粗估，約為 **新台幣 0.7 元／份**，亦即不到 **1 元／份**。

目前精度最高的前三個模型為：`gemini-3-flash-preview`、`qwen3.6-plus`、`gemini-3.1-flash-lite`

若將開源可自架能力一併納入考量，`kimi-k2.6` 亦達 **60/62、48/50**，是目前開源方案中最具競爭力的候選。

## 定位

Tier 1 透過 parser 自身的 JSON 證明了「結構與幾何自洽」，但仍無法回答一個關鍵問題：
**parser 的實際定位是否正確**。例如，「Item 7 的起點是否真的對齊」「尾段是否遭到截斷」，
都不能再靠 parser 自己的產物來驗證，否則會形成循環論證。
因此，Tier 2 必須引入**與 regex 失敗模式無關的獨立外部來源**。

人工複查時的判斷，實務上可對應為三個外部來源：頁碼（A）、TOC（B）以及 **VLM 首尾段（C，主力）**。
本報告交付的是**已建置並完成驗證的來源 C**；A、B 仍列於計畫中（[validation_plan.md](validation_plan.md) §4–5），作為後續工作。

---

## 方法：來源 C — VLM 首尾段比對

**第一性原理**：若兩種失敗模式彼此獨立的方法，對「item 從哪裡開始、到哪裡結束」給出一致判斷，
就能顯著提高我們對該邊界正確性的信心。
其中，VLM 依賴的是**版面視覺訊號**（粗體標題、字級、留白、下一節標題的視覺起點），
regex 依賴的是**字串 pattern**；兩者的失敗模式本質上不同。

**流程**（[vlm_first_last.py](vlm_first_last.py)）：
1. **準備視覺輸入**：先將 filing 的 HTML 渲染為 PDF，再切成逐頁 PNG（150 dpi），得到 VLM 可直接閱讀的版面影像。
2. **建立 item 的頁面範圍**：對每個 extracted item，先由 GT 與頁面文字對齊推得 `start_page` / `end_page`，使驗證器只需要關注「起始頁」與「結束頁」兩張圖，而不必把整份文件全部送入模型。
3. **組 head / tail prompt**：對起始頁使用 head prompt，要求模型找到 `Item N` 標題後，逐字轉錄其後的前五行；對結束頁使用 tail prompt，要求模型定位該 item 的結束邊界，並逐字轉錄結束前的最後五行。tail prompt 會帶入下一個 item 的 cue，幫助模型判斷「應該停在哪裡」。
4. **限制輸出格式**：VLM 一律只輸出純文字，不要求 JSON。這樣做是因為 10-K 內文包含大量引號，小模型常在 JSON 跳脫上失敗；對本任務而言，只需要取得可比對的文字塊即可。
5. **將 GT 與 VLM 輸出正規化到同一表示**：GT `content_text` 先去除 HTML 標籤並正規化空白，轉成連續文字；VLM 回傳文字也做相同正規化。這一步的目的，是避免把「視覺換行」誤當作內容差異。
6. **head 對齊標題**：在 head 端，GT 會先經過 `strip_heading`，剝掉開頭的 `Item N. 標題`，因為 prompt 要求 VLM 主要回正文；否則模型其實讀對正文，也可能因是否重複抄標題而被誤判。
7. **擷取可比窗口並評分**：GT 取首/末 300 字窗口，VLM 則取其整段轉錄，使用 `fuzz.partial_ratio` 比對；分數 `≥ 75` 即判為 PASS。這個設定的目標是允許合理的視覺換行與局部差異，同時仍能區分真正的邊界錯誤。
8. **逐 item 輸出驗證結果**：每個 item 都會留下 head / tail 的分數、PASS/FAIL、模型轉錄文字與對應 GT 片段，方便後續人工追查失敗案例。

**分桶**：當尾段為表格或圖像時，逐字比對本身不可靠，因此依尾段型態 `prose / table / figure` 與有無後繼 item 進行分桶。
只有 `prose` 桶的 tail 會直接計分；對於 `table/figure` 尾段，來源 C 不直接驗 tail，而改由下一個 item 的 head 覆蓋同一條邊界。

在本資料集中，分桶分布如下：

| 桶 | 數量 | 占比 |
|---|---|---|
| prose（tail 直接驗） | 50 | 80.6% |
| table_tail_covered | 10 | 16.1% |
| table_tail_last | 1 | 1.6% |
| figure_tail_covered | 1 | 1.6% |

各 filing 分布為：RELL（prose 8 / table 2 / figure 1）、GDC（prose 10 / table 5 / table_last 1）、NFLX（prose 11）、TSLA（prose 12）、WMT（prose 9 / table 3）。
因此，若只看「tail 不直接驗」的非-prose item，比例是 **12/62 = 19.4%**；但其中 **11/12** 屬於 `_covered`，
邊界其實已由「下一個 item 的 head」覆蓋。真正完全未驗的只有 **1/62 = 1.6%**（GDC item 15，最後一個 item、無後繼可覆蓋）。
換言之，在這組資料上，來源 C 對尾端邊界的覆蓋是：**80.6% 直接驗證 + 17.7% 由鄰接 head 間接覆蓋 = 98.4% 有被守到**，真正的盲區僅 **1.6%**。

---

## 資料：5 份高信心 GT

由於時間有限，本階段先準備 5 份具頁碼、人工標註信心高、公司類型多樣的 filing，
並額外標註 `pdf + 每頁 PNG + item 起訖頁 + 內文 GT`（[dataset/](dataset/)）。
這 5 份是 Tier 2 的驗證樣本。

---

## VLM 選型（[bench.md](bench.md)）

驗證器本身包含 VLM 元件，因此模型選型會直接決定整體可靠度。選型流程如下：

1. **候選來源**：[bench.md](bench.md)（根據 [llm-stats多模態排行](https://llm-stats.com/benchmarks/category/multimodal)：Rating / 價格 / context / 速度 / TTFT），
   據此選出最新開源/可負擔且多模態的候選。
2. **以實測準確度選型，固定 `temperature=0`、k=1**：在此設定下，同一輸入的重複詢問結果通常相近，
   因而 `pass@1`、`pass@k`、`pass^k` 的額外資訊量有限；因此本階段不以多次抽樣為主，
   而是直接比較 5 份樣本上的 head / tail PASS 率。
3. **共 survey 14 個模型**（Gemma / Qwen / Gemini / Kimi 各版本）。由於 head 多已接近飽和（62/62），
   **tail 成為主要選型指標**。下表依 tail 成績排序：

   | 模型 | 授權 | 價格 $/M | Context | Speed | TTFT | tail | head |
   |---|---|---|---|---|---|---|---|
   | **gemini-3-flash-preview** | 閉源 | $1.00 | 1M | 522 c/s | 2.1s | **50/50** | 62/62 |
   | qwen3.6-plus | 閉源 | $1.00 | 1M | 78 c/s | 78.6s | 49/50 | 62/62 |
   | gemini-3.1-flash-lite | 閉源 | $0.50 | 1M | N/A | N/A | 48/50 | 62/62 |
   | kimi-k2.6 | 開源 | — | 262.1k | 55 c/s | 56.2s | 48/50 | 60/62 |
   | qwen3.6-27b | 開源 | — | 262.1k | 150 c/s | 15.9s | 47/50 | 62/62 |
   | gemini-2.5-pro | 閉源 | $3.00 | 1.0M | 219 c/s | 10.8s | 47/50 | 61/62 |
   | qwen3.5-27b | 開源 | — | 262.1k | 118 c/s | 35.6s | 46/50 | 62/62 |
   | gemini-2.5-flash | 閉源 | $0.74 | 1.0M | 151 c/s | 5.1s | 46/50 | 60/62 |
   | qwen3.5-122b-a10b | 開源 | — | 262.1k | 132 c/s | 33.6s | 45/50 | 62/62 |
   | gemma-4-31b | 開源 | — | 262.1k | 186 c/s | 863ms | 43/50 | 59/62 |
   | qwen3.5-35b-a3b | 開源 | — | 262.1k | 251 c/s | 23.9s | 43/50 | 61/62 |
   | qwen3.5-9b | 開源 | — | N/A | N/A | N/A | 43/50 | 62/62 |
   | gemma-4-26b-a4b | 開源 | — | 262.1k | 114 c/s | 2.6s | 32/50 | 52/62 |

---

## 為什麼可信：兩向評分（對齊 Tier 1 精神）

| 測試 | 資料 | 證明什麼 |
|---|---|---|
| **精確度 / 不誤殺** | 5 份**正確** GT 直接跑 | 正確邊界不該被誤判 → 低誤報 |
| **偵測率 / 抓得到** | 在 GT 邊界**注入錯誤** | 規則真能抓到它宣稱的錯（截斷 / 越界） |

### 精確度 / 不誤殺

在 5 份正確 GT 上，最佳模型達到 **head 62/62、tail 50/50（`gemini-3-flash-preview`，零誤報）**。

下表列出目前 13 個可比分模型在**正確資料**上的 clean benchmark 結果，排序與後續偵測率表一致：

| rank | 模型 | head | tail | 誤殺(head/tail) |
|---|---|---|---|---|
| 1 | gemini-3-flash-preview | 62/62 | 50/50 | 0/0 |
| 2 | qwen3.6-plus | 62/62 | 49/50 | 0/1 |
| 3 | gemini-3.1-flash-lite | 62/62 | 48/50 | 0/2 |
| 4 | kimi-k2.6 | 60/62 | 48/50 | 2/2 |
| 5 | qwen3.6-27b | 62/62 | 47/50 | 0/2 |
| 6 | gemini-2.5-pro | 61/62 | 47/50 | 1/3 |
| 7 | qwen3.5-27b | 62/62 | 46/50 | 0/3 |
| 8 | gemini-2.5-flash | 60/62 | 46/50 | 2/4 |
| 9 | qwen3.5-122b-a10b | 62/62 | 45/50 | 0/5 |
| 10 | gemma-4-31b-it | 59/62 | 43/50 | 3/13 |
| 11 | qwen3.5-35b-a3b | 61/62 | 43/50 | 1/7 |
| 12 | qwen3.5-9b | 62/62 | 43/50 | 0/7 |
| 13 | gemma-4-26b-a4b-it | 52/62 | 32/50 | 10/18 |

由此可見，前段模型在 clean benchmark 上已能將誤殺壓到很低；其中最強幾個候選模型幾乎都維持在 `0–3/112` 的範圍內。
詳見 [vlm_first_last_results.md](vlm_first_last_results.md)。

### 偵測率 / 抓得到（§7.3）

我設計了幾種錯誤，對 GT 邊界**注入錯誤**，使用 `truncate_head/tail`（少抓）與 `overrun_head/tail`（越界進相鄰 item）兩組運算子，
並以 50 行 / 50% 作為嚴重度。下表列出目前**已完成 detection eval 的全部可比分模型**

| rank | 模型 | 誤殺(head/tail) | truncate_head | overrun_head | truncate_tail | overrun_tail |
|---|---|---|---|---|---|---|
| 1 | gemini-3-flash-preview | 0/0 | 90 / 84 | 90 / 90 | 86 / 96 | 88 / 82 |
| 2 | qwen3.6-plus | 0/1 | 90 / 84 | 90 / 90 | 82 / 98 | 88 / 76 |
| 3 | gemini-3.1-flash-lite | 0/2 | 90 / 84 | 90 / 90 | 90 / 96 | 88 / 83 |
| 4 | kimi-k2.6 | 2/2 | 83 / 75 | 90 / 90 | 92 / 100 | 88 / 77 |
| 5 | qwen3.6-27b | 0/2 | 90 / 84 | 90 / 90 | 92 / 98 | 88 / 81 |
| 6 | gemini-2.5-pro | 1/3 | 90 / 84 | 90 / 90 | 85 / 96 | 89 / 79 |
| 7 | qwen3.5-27b | 0/3 | 89 / 86 | 90 / 90 | 85 / 96 | 87 / 81 |
| 8 | gemini-2.5-flash | 2/4 | 88 / 83 | 90 / 90 | 91 / 98 | 89 / 78 |
| 9 | qwen3.5-122b-a10b | 0/5 | 90 / 86 | 90 / 90 | 82 / 98 | 87 / 80 |
| 10 | gemma-4-31b-it | 3/13 | 90 / 83 | 92 / 92 | 95 / 97 | 84 / 81 |
| 11 | qwen3.5-35b-a3b | 1/7 | 90 / 88 | 88 / 90 | 84 / 100 | 86 / 74 |
| 12 | qwen3.5-9b | 0/7 | 89 / 87 | 89 / 90 | 81 / 95 | 88 / 84 |
| 13 | gemma-4-26b-a4b-it | 10/18 | 88 / 83 | 92 / 92 | 97 / 97 | 78 / 72 |

整體而言，前段模型對這些具有實質意義的邊界錯誤，誤殺多維持在低檔；
其中最佳幾個候選模型的誤殺約在 0–3/112。相對地，較弱模型（特別是較小的 Gemma/Qwen 變體）則會明顯拉高誤殺率。
程式見 [vlm_inject_eval.py](vlm_inject_eval.py)，報告見 [report/detection/](report/detection/)。

---

## 備註

- **上文成績採分桶後統計**：head 會對所有 item 驗證，因此總分母為 62；tail 則只對 `prose` 桶直接驗證，因此總分母為 50。
  其餘 12 個 item 的尾段屬於 `table` 或 `figure`，去 HTML 後往往只剩標籤湯、座標軸或密集數字，逐字比對會產生假性 fail。
- **分桶原理**：尾段型態先分為 `prose / table / figure`。其中 `table` 會在 `content_text` 末端呈現 HTML 標籤收尾或極低可讀字母密度；
  `figure` 則常在尾段出現連續的金額／日期軸標記。只有 `prose` 尾段適合直接做首尾段文字比對。
- **更精確的盲區說法**：雖然非-prose 尾段共有 12 個（19.4%），但其中 11 個屬於 `_covered`，邊界已由下一個 item 的 head 守住；
  真正完全未驗的只有 1 個 `table_tail_last`，也就是 **1.6%**。因此，本方法在這組資料上的尾端驗證覆蓋率應理解為 **98.4%**，而非籠統的「約 19% 盲區」。

---

## 資料與程式來源

| 項目 | 路徑 | 說明 |
|---|---|---|
| 驗證資料集（5 份） | [dataset/](dataset/) | pdf + 每頁 PNG + 起訖頁 + 內文 GT |
| 首尾段驗證器 | [vlm_first_last.py](vlm_first_last.py) | 渲染對照、分桶、partial_ratio |
| VLM 呼叫封裝 | [vlm_reader.py](vlm_reader.py) | OpenRouter + 快取 + reasoning 控制 |
| 模型 survey 結果 | [vlm_first_last_results.md](vlm_first_last_results.md) | 14 模型 × 5 份 head/tail |
| 偵測力（錯誤注入） | [vlm_inject_eval.py](vlm_inject_eval.py) ／ [report/detection/](report/detection/) | §7.3 注入評估 |
| VLM 選型參考 | [bench.md](bench.md) | llm-stats 多模態排行 |

```bash
python -m feedback.external_reference_validation.vlm_first_last --label RELL_2025 --model google/gemini-3-flash-preview  # 跑一份
python -m feedback.external_reference_validation.vlm_inject_eval                                                          # §7.3 偵測力（零 API）
```
