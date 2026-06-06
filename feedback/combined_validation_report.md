# SEC 10-K Parser 驗證總報告

## 主要結論

提出一套的 SEC 10-K parser 驗證架構，由兩個互補模組組成：

1. **SEC 10-K 視覺驗證器**
   先用 VLM 自動完成頁面導航，再以頁面證據檢查 parser 抽出的 item 前後邊界是否正確。
2. **SEC 10-K 確定性驗證器**
   驗證任何正確解析都必然滿足的結構條件。這些條件一旦被違反，就能直接判定 parser 結果存在錯誤。

這兩套方法合起來形成一條完整的驗證閉環：

- 視覺驗證器負責回答「即使結構上沒有明顯矛盾，頁面上的 item 起點與終點是否真的和 parser 輸出一致」。
- 確定性驗證器負責回答「這份 parser 結果是否違反必然成立的結構不變量」。

目前的核心實驗結論如下：

- **視覺驗證器**
  - `nav` 階段在 5 份 filing、62 個 item 上達到 `56/62 (90.3%)` exact、`60/62 (96.8%)` within `±1 page`。
    這表示視覺驗證器已能在大多數 item 上先自動找到正確頁面，並把少數偏差控制在很小的頁碼範圍內。
  - `e2e precision` 基線在 gated 子集上達到 `head 59/60 (98.3%)`、`tail 43/44 (97.7%)`。
    這表示在成功導航到可信頁面的前提下，視覺驗證器對正確 parser 結果具有很低的誤殺率。
  - `e2e detection` 基線在 4 類錯誤上的偵測率落在 `83.1%–95.3%`。
    這表示視覺驗證器不只會在 clean case 上通過，也能對常見的截斷與越界錯誤提供穩定的偵測能力。
  - 以本次精度最佳模型 `google/gemini-3-flash-preview` 估算，完整跑完一份 filing 的成本約為 `US$0.022`，折合約 `NT$0.7`，亦即不到 `NT$1`／份。
    這表示即使使用目前表現最好的模型，這套視覺驗證流程的單份驗證成本仍相當低。
- **確定性驗證器**
  - 在 34 份人工標註的 10-K Ground Truth 上，4 條規則的 false positive 均為 `0/34 (0.0%)`。
    這表示確定性驗證器在真實正確資料上沒有誤殺，規則本身足夠保守且與資料分布無關。
  - 在 3,760 個系統性注入錯誤樣本上，規則 1、2、4 的偵測率為 `100%`；規則 3 對核心 item 遺失與大段內文遺失的偵測率亦為 `100%`。
    這表示確定性驗證器已能穩定抓出違反必要結構條件的 parser 錯誤，特別適合處理區間非法、順序錯亂、重要 item 消失與全文異常過短等問題。

這套驗證架構主要提供：

- 對結構錯誤的**確定性證偽能力**
- 對頁面邊界錯誤的**高信心視覺複查能力**
- 對 parser 結果的**可重現、可量化**驗證流程

---

## 1. 驗證總體設計

- **結構型錯誤**
  例如頁面區間非法、item 順序倒退、區間重疊、重要 item 消失、全文內容異常過短。這類錯誤不需要理解頁面語意，只要違反必要條件，就可直接判錯。
- **邊界型錯誤**
  例如 item 開頭抓錯、尾段被截斷、尾端越界讀到下一節、頁面導航偏移。這類錯誤即使整體 JSON 看起來「格式正常」，仍可能需要回到 PDF 頁面做實際驗證。

因此，本報告將 parser 驗證拆成兩層：

1. **視覺驗證器**
   用來處理「需要頁面證據」的邊界檢查。
2. **確定性驗證器**
   用來處理「違反即證錯」的必要條件。

### 1.2 閉環流程

```mermaid
flowchart TD
    A["SEC 10-K PDF"] --> B["Parser 輸出<br/>item title / start / end / content"]
    B --> C["SEC 10-K 確定性驗證器"]
    C --> D{"是否違反結構不變量"}
    D -- "是" --> E["直接 FAIL<br/>可明確定位結構錯誤"]
    D -- "否" --> F["SEC 10-K 視覺驗證器"]
    A --> F
    F --> G["nav<br/>TOC 抽取 -> printed page -> render page"]
    G --> H["heading reconciliation / gate"]
    H --> I{"是否找到可信頁面"}
    I -- "否" --> J["中立 / 無法判定<br/>不判對也不判錯"]
    I -- "是" --> K["VLM 檢查 head / tail 頁面證據"]
    K --> L{"與 parser 結果是否一致"}
    L -- "是" --> M["PASS"]
    L -- "否" --> N["FAIL<br/>可定位邊界錯誤"]
```

---

## 2. SEC 10-K 視覺驗證器

### 2.1 目標與定位

SEC 10-K 視覺驗證器的目標，是建立一條獨立於 parser 內容的頁面驗證鏈，回答以下問題：

- 這個 item 應該落在哪一頁？
- parser 抽出的開頭，是否真的出現在該 item 的開頭頁？
- parser 抽出的尾端，是否真的停在該 item 的結尾頁，而沒有被截斷或越界？

這個驗證器真正要驗證的是：**當 parser 輸出進來後，是否能用視覺證據對 item 邊界做高信心複查。(透過多模態模型)**

### 2.2 方法拆解

視覺驗證器分成兩個階段：

1. **Navigation (`nav`)**
   用多模態模型從 TOC 抽取 `item -> printed page`，再將 printed page 對齊到 PDF render page，最後以 `Item N + title` 在正文頁做 heading reconciliation。
2. **End-to-End Validation (`e2e`)**
   在 `nav` 導出的可信頁面上，將 parser 輸出的 `item title / start / end / content` 作為待驗目標，讓多模態模型直接檢查 head / tail 是否與頁面證據一致。具體做法是：
   - `head`：讀取重定位後的起始頁，確認 parser 抽出的開頭文字是否真的出現在該 item 的起點附近。
   - `tail`：不只讀單一結尾頁，而是讀最後 `1–2` 頁；若 item 結尾可能跨頁，模型會同時查看 `end_page-1` 與 `end_page`，確認 parser 抽出的尾端是否真的停在下一個 section heading 之前。
   - 若頁面證據與 parser 內容一致，則判定通過；若頁面證據顯示有截斷、越界或起訖錯置，則判定失敗。

如果 `nav` 無法找到可信頁面，系統會輸出：

- **中立 / 無法判定**

視覺驗證器失敗代表證據不足，不代表 parser 一定錯。

### 2.3 驗證資料

視覺驗證器目前使用 5 份人工整理(新增pdf頁碼對應資訊標註)的 benchmark filing：

- `GDC_2023`
- `NFLX_2025`
- `RELL_2025`
- `TSLA_2023`
- `WMT_2026`

每份資料都包含：

- 原始 `PDF`
- 由 PDF 渲染出的頁面 `PNG`
- item 內容 Ground Truth，例如 `content.json`
- item 頁碼與邊界標註，例如 `pages.json`

這批資料的來源分成兩層：

- **原始來源**：來自本專案整理的 SEC 10-K benchmark filing PDF 與其對應的 parser Ground Truth。
- **驗證標註來源**：在原始 parser Ground Truth 之上，額外補上 PDF 頁碼對應、頁面渲染圖、以及 item 的 `start_page / end_page` 對帳結果，形成可供 `nav`、`e2e.run`、`e2e.inject` 共用的評估資料。

這組 benchmark 的設計目的，主要是讓 `nav`、`e2e precision`、`e2e detection` 都能在同一批可追查資料上完成評估。

幾個關鍵分母說明：

- `nav` 的總分母是 `62`，代表 5 份 filing 中納入導航評估的 item 數。
- `e2e precision` 先看 `gate`，因此會先從 `62` 收斂到成功找到可信頁面的子集；在基線中為 `60/62`。
- `head precision` 只在 gated clean item 上計算，因此基線分母為 `60`。
- `tail precision` 進一步只在可評估 tail 子集上計算，因此基線分母為 `44`。
- `e2e detection` 的分母來自 `gate + clean-pass` 子集，所以不同 detect model 之間可能略有不同。

因此，視覺驗證器的所有數字都應解讀為：

- 先有獨立導航
- 再有 gate
- 最後才在可判定子集上做 head / tail 驗證與錯誤注入測試

### 2.4 模型選型

本節的模型選型主要參考 [bench.md](./external_reference_validation/bench.md) 中整理的多模態模型 survey。該表彙整了多個主流 VLM 的公開 benchmark 排名，以及價格、context window、速度與首 token 延遲等資訊，作為本研究建立候選池的起點。

在此基礎上，本報告再依下列條件縮小到實際評估模型：

- 可透過目前實驗環境穩定呼叫
- 支援本研究需要的影像輸入
- 成本、速度與可重跑性適合進行 5 份 filing 的完整比較

#### Navigation model

`nav` 階段固定採用：

- `google/gemini-3-flash-preview`

原因不是單一指標最佳而已，而是 navigation task 本身較難，包含：

- TOC 頁辨識
- `item -> printed page` 抽取
- `printed page -> render page` 對位
- heading reconciliation
- TOC 與正文同名 item 的排除

在這種多步驟導航任務中，穩定性與覆蓋率比單一 head/tail 分數更重要，因此 `nav` 直接固定使用目前最穩定的模型。

#### Detection models

`detect` 階段則評估 13 個開源/閉源模型：

- `google/gemini-3-flash-preview`
- `google/gemini-3.1-flash-lite`
- `google/gemini-2.5-pro`
- `google/gemini-2.5-flash`
- `google/gemma-4-31b-it`
- `google/gemma-4-26b-a4b-it`
- `qwen/qwen3.6-plus`
- `qwen/qwen3.6-27b`
- `qwen/qwen3.5-27b`
- `qwen/qwen3.5-122b-a10b`
- `qwen/qwen3.5-35b-a3b`
- `qwen/qwen3.5-9b`
- `moonshotai/kimi-k2.6`

評估分成兩個子任務：

- `e2e.run`：量 clean precision / false positive
- `e2e.inject`：量 detection / recall

### 2.5 Stage 1：Navigation 覆蓋率

`nav` 在 5 份 filing、62 個 item 上的結果如下：

| filing | exact | within `±1 page` |
|---|---:|---:|
| `GDC_2023` | `15/16 (93.8%)` | `16/16 (100.0%)` |
| `NFLX_2025` | `9/11 (81.8%)` | `11/11 (100.0%)` |
| `RELL_2025` | `9/11 (81.8%)` | `10/11 (90.9%)` |
| `TSLA_2023` | `11/12 (91.7%)` | `11/12 (91.7%)` |
| `WMT_2026` | `12/12 (100.0%)` | `12/12 (100.0%)` |
| **合計** | **`56/62 (90.3%)`** | **`60/62 (96.8%)`** |

這代表：

- `nav` 已能為大多數 item 找到正確頁面。
- 即使未完全 exact，絕大多數誤差也落在 `±1 page` 內，足以作為 heading reconciliation 的起點。

### 2.6 Stage 2：E2E Precision 基線

基線設定：

- `nav-model = google/gemini-3-flash-preview`
- `detect-model = google/gemini-3-flash-preview`

| filing | gate | head precision | tail precision |
|---|---:|---:|---:|
| `GDC_2023` | `16/16 (100.0%)` | `16/16 (100.0%)` | `10/10 (100.0%)` |
| `NFLX_2025` | `11/11 (100.0%)` | `11/11 (100.0%)` | `10/10 (100.0%)` |
| `RELL_2025` | `10/11 (90.9%)` | `9/10 (90.0%)` | `5/6 (83.3%)` |
| `TSLA_2023` | `11/12 (91.7%)` | `11/11 (100.0%)` | `10/10 (100.0%)` |
| `WMT_2026` | `12/12 (100.0%)` | `12/12 (100.0%)` | `8/8 (100.0%)` |
| **合計** | **`60/62 (96.8%)`** | **`59/60 (98.3%)`** | **`43/44 (97.7%)`** |

解讀如下：

- `gate 60/62` 代表只有極少數 item 因證據不足而保持中立。
- `head 59/60` 顯示在可信頁面上，head 驗證已非常穩定。
- `tail 43/44` 已接近 head 水準，顯示把 tail 擴成最後 1–2 頁後，原本由跨頁短尾巴造成的假性失敗大幅減少。

### 2.7 Stage 2：13 模型 Precision Sweep

以下表格固定 `nav-model = google/gemini-3-flash-preview`，比較 13 個 `detect-model` 的 `e2e.run` 結果。分母統一以 gated clean 子集計算：

- head 分母：`60`
- tail 分母：`44`

| rank | detect model | head | tail |
|---|---|---:|---:|
| 1 | `google/gemini-3-flash-preview` | `59/60 (98.3%)` | `43/44 (97.7%)` |
| 2 | `qwen/qwen3.6-plus` | `60/60 (100.0%)` | `40/44 (90.9%)` |
| 3 | `moonshotai/kimi-k2.6` | `57/60 (95.0%)` | `39/44 (88.6%)` |
| 4 | `google/gemini-2.5-pro` | `52/53 (98.1%)` | `33/39 (84.6%)` |
| 5 | `google/gemini-3.1-flash-lite` | `60/60 (100.0%)` | `36/44 (81.8%)` |
| 5 | `qwen/qwen3.5-9b` | `60/60 (100.0%)` | `36/44 (81.8%)` |
| 7 | `qwen/qwen3.6-27b` | `60/60 (100.0%)` | `33/44 (75.0%)` |
| 8 | `qwen/qwen3.5-27b` | `60/60 (100.0%)` | `32/44 (72.7%)` |
| 8 | `qwen/qwen3.5-122b-a10b` | `60/60 (100.0%)` | `32/44 (72.7%)` |
| 10 | `qwen/qwen3.5-35b-a3b` | `58/59 (98.3%)` | `31/43 (72.1%)` |
| 11 | `google/gemma-4-31b-it` | `56/60 (93.3%)` | `29/44 (65.9%)` |
| 11 | `google/gemini-2.5-flash` | `55/57 (96.5%)` | `27/41 (65.9%)` |
| 13 | `google/gemma-4-26b-a4b-it` | `38/49 (77.6%)` | `12/34 (35.3%)` |

這張表的重點說明在相同 `nav` 前提下，不同多模態模型在 clean 邊界驗證上的穩定度差異。

- `google/gemini-3-flash-preview` 在 head 與 tail 兩端維持最均衡的結果，因此仍是主要基線。
- `qwen/qwen3.6-plus` 與 `moonshotai/kimi-k2.6` 在 2 頁 tail 版本下明顯受益，顯示多頁 tail 對跨頁結尾情境確實有效。
- `google/gemini-3.1-flash-lite` 與 `google/gemini-2.5-pro` 仍屬第一梯隊，顯示 Gemini 系列在這個任務上整體較穩。
- 多個 Qwen 模型在 head precision 幾乎滿分，但 tail precision 仍有明顯分化，表示它們在尾端邊界上的穩定度差異較大。
- `google/gemma-4-26b-a4b-it` 與部分較小模型的 tail 明顯偏低，說明在同樣的頁面導航前提下，邊界判讀能力仍有明顯落差。

### 2.8 Stage 3：E2E Detection 基線

基線模型 `google/gemini-3-flash-preview` 的 `e2e.inject` 結果如下：

| operator | `50 lines` | `50%` |
|---|---:|---:|
| `truncate_head` | `53/59 (89.8%)` | `49/59 (83.1%)` |
| `overrun_head` | `55/59 (93.2%)` | `55/59 (93.2%)` |
| `truncate_tail` | `38/43 (88.4%)` | `42/43 (97.7%)` |
| `overrun_tail` | `41/43 (95.3%)` | `38/43 (88.4%)` |

這表示：

- 對 head 類錯誤，視覺驗證器可穩定抓到大多數截斷與越界。
- 對 tail 類錯誤，偵測率仍維持在 `88.4%–97.7%`。
- 這些數字是在完整 `nav + gate + detect` 流程下取得，而不是直接吃人工指定頁碼。

### 2.9 Stage 3：13 模型 Detection Sweep

以下表格固定 `nav-model = google/gemini-3-flash-preview`，比較 13 個 `detect-model` 的 `e2e.inject` 結果。

注意：這張表的分母來自各模型自己的 `gate + clean-pass` 子集，因此模型間分母可能略有不同；這是 e2e 設計的一部分，不是統計錯誤。

| detect model | `truncate_head` | `overrun_head` | `truncate_tail` | `overrun_tail` |
|---|---|---|---|---|
| `google/gemini-3-flash-preview` | `53/59 (89.8%)` / `49/59 (83.1%)` | `55/59 (93.2%)` / `55/59 (93.2%)` | `38/43 (88.4%)` / `42/43 (97.7%)` | `41/43 (95.3%)` / `38/43 (88.4%)` |
| `google/gemini-3.1-flash-lite` | `54/60 (90.0%)` / `50/60 (83.3%)` | `56/60 (93.3%)` / `56/60 (93.3%)` | `31/36 (86.1%)` / `35/36 (97.2%)` | `34/36 (94.4%)` / `31/36 (86.1%)` |
| `google/gemini-2.5-pro` | `47/52 (90.4%)` / `44/52 (84.6%)` | `48/52 (92.3%)` / `48/52 (92.3%)` | `28/33 (84.8%)` / `32/33 (97.0%)` | `32/33 (97.0%)` / `29/33 (87.9%)` |
| `google/gemini-2.5-flash` | `49/55 (89.1%)` / `46/55 (83.6%)` | `51/55 (92.7%)` / `51/55 (92.7%)` | `24/27 (88.9%)` / `27/27 (100.0%)` | `25/27 (92.6%)` / `21/27 (77.8%)` |
| `google/gemma-4-31b-it` | `50/56 (89.3%)` / `46/56 (82.1%)` | `53/56 (94.6%)` / `53/56 (94.6%)` | `24/29 (82.8%)` / `28/29 (96.6%)` | `26/29 (89.7%)` / `24/29 (82.8%)` |
| `google/gemma-4-26b-a4b-it` | `34/38 (89.5%)` / `32/38 (84.2%)` | `37/38 (97.4%)` / `37/38 (97.4%)` | `9/12 (75.0%)` / `12/12 (100.0%)` | `12/12 (100.0%)` / `12/12 (100.0%)` |
| `qwen/qwen3.6-plus` | `54/60 (90.0%)` / `50/60 (83.3%)` | `56/60 (93.3%)` / `56/60 (93.3%)` | `30/40 (75.0%)` / `39/40 (97.5%)` | `38/40 (95.0%)` / `34/40 (85.0%)` |
| `qwen/qwen3.6-27b` | `54/60 (90.0%)` / `50/60 (83.3%)` | `56/60 (93.3%)` / `56/60 (93.3%)` | `29/33 (87.9%)` / `33/33 (100.0%)` | `31/33 (93.9%)` / `27/33 (81.8%)` |
| `qwen/qwen3.5-27b` | `53/60 (88.3%)` / `51/60 (85.0%)` | `56/60 (93.3%)` / `56/60 (93.3%)` | `27/32 (84.4%)` / `30/32 (93.8%)` | `29/32 (90.6%)` / `27/32 (84.4%)` |
| `qwen/qwen3.5-122b-a10b` | `54/60 (90.0%)` / `51/60 (85.0%)` | `56/60 (93.3%)` / `56/60 (93.3%)` | `27/32 (84.4%)` / `32/32 (100.0%)` | `30/32 (93.8%)` / `28/32 (87.5%)` |
| `qwen/qwen3.5-35b-a3b` | `52/58 (89.7%)` / `51/58 (87.9%)` | `53/58 (91.4%)` / `54/58 (93.1%)` | `27/31 (87.1%)` / `30/31 (96.8%)` | `28/31 (90.3%)` / `25/31 (80.6%)` |
| `qwen/qwen3.5-9b` | `53/60 (88.3%)` / `52/60 (86.7%)` | `55/60 (91.7%)` / `56/60 (93.3%)` | `25/36 (69.4%)` / `34/36 (94.4%)` | `34/36 (94.4%)` / `31/36 (86.1%)` |
| `moonshotai/kimi-k2.6` | `47/57 (82.5%)` / `42/57 (73.7%)` | `53/57 (93.0%)` / `53/57 (93.0%)` | `31/39 (79.5%)` / `38/39 (97.4%)` | `37/39 (94.9%)` / `33/39 (84.6%)` |

這張表的重點在於：即使同樣通過 `nav + gate`，不同 detect model 對「錯誤邊界」的敏感度仍有顯著差異。

- `google/gemini-3-flash-preview` 在四類錯誤上維持最均衡的偵測率，因此仍適合作為整體視覺驗證方案的主要基線。
- `google/gemini-3.1-flash-lite`、`google/gemini-2.5-pro` 在多數 operator 上與主基線接近，顯示 Gemini 系列不只 precision 穩，對錯誤邊界也有不錯的 recall。
- `qwen/qwen3.6-plus` 與 `moonshotai/kimi-k2.6` 在 2 頁 tail 設定下的 tail 偵測有明顯改善，說明多頁 tail 對跨頁結尾情境確實有效。
- Qwen 系列在部分 tail truncate / overrun 任務上表現突出，但整體波動仍較大，表示它們對某些錯誤型態特別敏感，卻未必在四類 operator 上都同樣穩定。
- `google/gemma-4-31b-it` 在部分 tail 任務上仍具競爭力，但 head truncate 與整體平衡性仍弱於第一梯隊。

因此，這張表更適合被解讀為「不同模型對不同錯誤型態的敏感度輪廓」，而不只是單一總分排名。

---

## 3. SEC 10-K 確定性驗證器

### 3.1 目標與定位

SEC 10-K 確定性驗證器的目標，是驗證任何正確解析都必然滿足的條件。它不依賴語言模型、不依賴語料統計分布，也不以「大多數 filing 通常長這樣」作為判準。

它回答的問題是：

- parser 結果是否違反了基本的頁面區間幾何約束？
- parser 結果是否破壞了 SEC 10-K item 的必要順序？
- parser 是否遺漏了本應存在的重要 item？
- parser 是否其實抽到錯文件、空內容、或明顯不完整的內容？

這種方法的優勢是：

- 若規則成立，錯誤可以被明確定位。
- 若規則違反，判錯依據清楚，不依賴模型主觀判讀。
- 執行成本極低，適合作為 parser 驗證第一道防線。

### 3.2 驗證資料

確定性驗證器使用兩層資料：

1. **34 份 Ground Truth**
   來自人工整理的 10-K 標註資料，用來量 false positive，也就是在正確資料上是否誤殺。
2. **3,760 個 mutants**
   從上述 Ground Truth 系統性生成，用來量 recall，也就是面對已知錯誤時能否抓到。

資料來源同樣分成兩層：

- **原始來源**：本專案整理的 SEC 10-K parser Ground Truth，包含每份 filing 的 item 結構、頁碼區間與內容文字。
- **驗證來源**：以這 34 份 Ground Truth 為母體，透過程式化 mutation 產生 `3,760` 個錯誤樣本，用來對四條規則做系統性對抗測試。

Ground Truth 的覆蓋範圍包含：

- 2016–2026 年
- 12 家公司
- 多種 filer 規模
- iXBRL 與較舊 HTML
- Part III incorporated by reference
- 超過 400 頁的大型 filing

Mutants 則對應到四條規則的主要錯誤型態，例如：

- 非法區間
- 順序倒退或頁碼重疊
- 重要 item 消失
- 內容大幅縮水或接近空檔

因此，確定性驗證器的數字要分成兩種解讀：

- `0/34` 類型的結果，代表在真實正確資料上的 false positive
- `100%` 類型的結果，代表在人工注入錯誤資料上的偵測率

### 3.3 四條核心規則

| 規則 | 檢查內容 | 核心意義 | false positive | 偵測能力 |
|---|---|---|---:|---:|
| 規則 1：區間合法性 | `0 <= start < end` | 防止零長度、反向區間、負頁碼 | `0/34` | `100%` |
| 規則 2：單調且不可重疊 | 後續 item 的 `start` 不得早於前一 item 的 `end` | 防止順序倒退與頁面重疊 | `0/34` | `100%` |
| 規則 3：重要 item 不可消失 | SEC 10-K 重要 item 應在抽取結果中存在 | 防止 parser 大段漏抓或整節消失 | `0/34` | `100%`（核心 item / 大段遺失） |
| 規則 4：全文內容底線 | 抽取總內容不得低於保守下限 | 防止錯文件、空抽取、嚴重截斷 | `0/34` | `100%` |

### 3.4 驗證資料與對抗式測試

確定性驗證器的可信度來自四個層面的證據：

1. **規則本身是必要條件，而不是語料統計**
   這些規則不依賴某家公司、某年度、某種版型，而是來自 parser 結果若要正確，必然滿足的結構性約束。
2. **在 34 份真實 Ground Truth 上沒有誤殺**
   評估集涵蓋 2016–2026、12 家公司、多種 filer 規模、iXBRL、新舊 HTML、Part III by reference，以及超過 400 頁的大型 filing。
3. **以系統性錯誤注入驗證召回率**
   從 34 份 Ground Truth 生成 `3,760` 個 mutants，分別模擬區間非法、重疊、順序錯、item 消失與內容清空。
4. **門檻設得保守**
   例如規則 3 的 gap threshold 設為 `1,000 chars`，而資料中實際最大正常 gap 僅 `413 chars`；規則 4 的全文底線設為 `5,000 chars`，而最短真實 filing 仍有 `153,052 chars`。

### 3.5 結果說明

這組結果代表：

- 確定性驗證器不是在「猜哪種 filing 看起來像異常」。
- 它是在檢查 parser 結果是否違反了不應被違反的硬性約束。
- 因此，只要命中規則違反，就具有非常高的可解釋性與可操作性。

它的邊界也很清楚：

- 若 parser 在結構上仍自洽，但頁面邊界抓得不準，確定性驗證器未必能發現。
- 這類 finer-grained 的邊界錯誤，就是視覺驗證器要補上的部分。

---

## 4. 方案配置總結

### 4.1 驗證方案組成

- **結構層**
  使用 SEC 10-K 確定性驗證器，檢查 parser 結果是否違反必要結構條件。
- **頁面層**
  使用 SEC 10-K 視覺驗證器，透過 `nav + e2e` 在 PDF 頁面上複查 item 邊界。

### 4.2 視覺驗證器實驗配置

#### Navigation

- 本報告固定使用 `google/gemini-3-flash-preview`

#### Detection

- 本報告在相同 `nav-model` 下，對 13 個 detect model 做並列比較。
- `google/gemini-3-flash-preview` 作為主要基線，是因為它在本次 benchmark 上同時取得最佳整體 precision 與穩定的 detection 結果。
- 其餘表現較強的模型包括：
  - `google/gemini-3.1-flash-lite`
  - `google/gemini-2.5-pro`
  - `qwen/qwen3.6-plus`

就這份報告的目的而言，這些結果主要用來說明：

- `nav` 覆蓋率最佳且最穩定
- `e2e precision` 在 13 模型中最佳
- `e2e detection` 維持高而均衡的表現

---

## 5. 邊界與限制

### 5.1 確定性驗證器的限制

- 它只驗證必要條件，不直接保證頁面語意邊界百分之百正確。
- 若 parser 在結構上完全自洽，但某個 item 的 head / tail 抓錯，仍需要視覺驗證器補充。

### 5.2 視覺驗證器的限制

- `nav` 尚未達到 `100%` exact coverage。
- `gate` 失敗時系統會保持中立，因此不是所有 item 都會得到 PASS/FAIL。
- `tail` 任務天生比 `head` 更難，尤其在跨頁尾巴、同頁換節、頁首短尾段等情境下。
- `e2e.inject` 分母會依 `gate + clean-pass` 子集變化，因此不同 detect model 的分母不完全一致。
- 目前 benchmark 為 5 份 filing，雖然涵蓋多種版型，但仍非完整 SEC universe。

---

## 6. 可重現性與檔案入口

### 6.1 主要程式

- 視覺驗證器
  - [toc_extract.py](./external_reference_validation/toc_nav/toc_extract.py)
  - [coverage.py](./external_reference_validation/toc_nav/coverage.py)
  - [run.py](./external_reference_validation/e2e/run.py)
  - [inject.py](./external_reference_validation/e2e/inject.py)
- 確定性驗證器
  - [model.py](./deterministic_validation/model.py)
  - [rules.py](./deterministic_validation/rules.py)
  - [mutations.py](./deterministic_validation/mutations.py)
  - [runner.py](./deterministic_validation/runner.py)

### 6.2 重跑指令

#### 視覺驗證器：navigation

```bash
python -m feedback.external_reference_validation.toc_nav.toc_extract --label GDC_2023
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
```

#### 視覺驗證器：e2e precision

```bash
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
```

#### 視覺驗證器：e2e detection

```bash
python -m feedback.external_reference_validation.e2e.inject --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
```

#### 確定性驗證器

```bash
python -m feedback.deterministic_validation.runner
python -m feedback.deterministic_validation.runner --dump
```
