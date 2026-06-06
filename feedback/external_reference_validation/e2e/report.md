# Tier 2 端到端：TOC 獨立導航 → gate → Source C（報告草稿）

> 程式：[run.py](run.py)（精確度）、[inject.py](inject.py)（§7.3 偵測率）
> 上游：[../toc_nav/report.md](../toc_nav/report.md)（Stage 1）、[../vlm_first_last_results.md](../vlm_first_last_results.md)（Stage 2）

## 定位：為什麼要端到端

`toc_nav`（獨立導航）與 Source C（VLM 邊界檢查）原本是**各自驗證**；而 §7.3 偵測率是用 **dataset(正確)頁**
跑的——等於「假設拿得到正確頁」，對截斷偏樂觀（真實流程的頁若由 parser 內容推得，會跟著截斷一起跑）。

端到端把兩段**真正串起來實測**，且**頁碼由 TOC 提供**（獨立於被檢查/被注入的內容）：
parser 截斷時導航不跟著錯，仍導到真實頁 → 截斷因此可偵測。這關閉了 §7.3 的樂觀 asterisk。

---

## 方法（三段 pipeline）

```
Stage 1  TOC 獨立導航：item → 渲染頁（VLM 讀 TOC + 頁尾序列擬合 + 內插）   見 toc_nav/report
   │
  gate   標題對帳 + 重定位（[run.py](run.py) find_heading_page）
   │     · 在導航頁 ±1 內找「Item N + 標題」的標題頁 → 命中即重定位到該頁（順帶修 ±1 導航誤差）
   │     · 排除 TOC 頁（低編號 item 的落點近 TOC，會撞目錄上的同名項，如 item 1）
   │     · 用 title 首詞純字母前綴（避撇號型態不符）；搆不到 → 誠實棄驗
   ▼
Stage 2  Source C 邊界檢查：head 用重定位起始頁；tail end = 下一個 item（同樣重定位）起點回推
```

gate 的三種行為都在 GDC 上驗證過：重定位修 ±1（item 12: 71→70）、整頁找標題救共頁中段（2/3/9）、
排除 TOC 救 item 1、搆不到則棄驗。

---

## 結果 1：精確度（用 TOC 獨立導航頁，gate 放行才計）

| filing | gate 良率 | head | tail |
|---|---|---|---|
| GDC_2023 | 16/16 | 16/16 | 10/10 |
| NFLX_2025 | 11/11 | 11/11 | 10/10 |
| RELL_2025 | 10/11 | 9/10 | 5/6 |
| TSLA_2023 | 11/12 | 11/11 | 10/10 |
| WMT_2026 | 12/12 | 12/12 | 8/8 |
| **合計** | **60/62 (97%)** | **59/60 (98%)** | **43/44 (98%)** |

→ 用**完全獨立的頁**跑 Source C，精確度 head 98% / tail 98%，而且 2 頁 tail 版明顯補強了跨頁尾巴情境。唯一 head 失敗仍是 RELL-8，屬財報開頭內容問題，非導航誤差。

### 補充：13 個 non-free detect model 的 e2e.run 精確度 sweep

為了補齊 `inject.py` 的 clean cache，並檢查端到端流程在不同 Source C 模型下的穩定度，
固定 `nav-model = google/gemini-3-flash-preview`，對 13 個 non-free `detect-model` 全跑一輪 `e2e.run --batch 4`。

下表為 **全部 item** 口徑（含 gate 棄驗造成的分母變化）：

| detect model | head | tail |
|---|---|---|
| `google/gemini-3-flash-preview` | `59/60` | `43/44` |
| `google/gemini-3.1-flash-lite` | `60/60` | `36/44` |
| `google/gemini-2.5-pro` | `52/53` | `33/39` |
| `google/gemini-2.5-flash` | `55/57` | `27/41` |
| `google/gemma-4-31b-it` | `56/60` | `29/44` |
| `google/gemma-4-26b-a4b-it` | `38/49` | `12/34` |
| `qwen/qwen3.6-plus` | `60/60` | `40/44` |
| `qwen/qwen3.6-27b` | `60/60` | `33/44` |
| `qwen/qwen3.5-27b` | `60/60` | `32/44` |
| `qwen/qwen3.5-122b-a10b` | `60/60` | `32/44` |
| `qwen/qwen3.5-35b-a3b` | `58/59` | `31/43` |
| `qwen/qwen3.5-9b` | `60/60` | `36/44` |
| `moonshotai/kimi-k2.6` | `57/60` | `39/44` |

從 2 頁版 e2e 精確度看，`gemini-3-flash-preview` 仍是最穩的整體 baseline，而 `qwen3.6-plus`、`kimi-k2.6` 在 tail 也明顯受益。這說明把 tail 擴成最後 1–2 頁後，跨頁尾巴原本造成的假性失敗確實被減少。

## 結果 2：§7.3 偵測率（用 TOC 獨立導航頁，model = gemini-3-flash-preview）

對 gate 放行 item 注入截斷/越界（50 行 / 50%），重用已快取的 VLM 讀、零 API：

| 運算子 | e2e（獨立頁）50行 / 50% | 對照原 §7.3（dataset 頁） |
|---|---|---|
| truncate_head | `53/59` / `49/59` | 90% / 84% |
| overrun_head | `55/59` / `55/59` | 90% / 90% |
| truncate_tail | `38/43` / `42/43` | 86% / 96% |
| overrun_tail | `41/43` / `38/43` | 88% / 82% |

→ **與 GT 頁版本相當，且 tail 分母擴大後仍維持高偵測率**。尤其 `truncate_tail` 88–98% 直接證明：**TOC 導航讓截斷重新可偵測**
——§7.3 的樂觀 caveat 正式關閉，偵測力**不依賴 GT/parser 頁**。

### 補充：13 個 non-free detect model 的 e2e.inject sweep

在上述 `e2e.run` 補齊 clean cache 後，再固定 `nav-model = google/gemini-3-flash-preview`，
對同一批 13 個 non-free `detect-model` 跑 `e2e.inject --batch 4`。下表的分母是：

- `gate` 放行
- clean baseline `>= TH`

因此不同模型的分母仍可能不同；這時分母差異已不再是 cache 缺漏，而是「該模型在 e2e clean 條件下實際可評估的樣本數」。

| detect model | truncate_head | overrun_head | truncate_tail | overrun_tail |
|---|---|---|---|---|
| `google/gemini-3-flash-preview` | `53/59` / `49/59` | `55/59` / `55/59` | `38/43` / `42/43` | `41/43` / `38/43` |
| `google/gemini-3.1-flash-lite` | `54/60` / `50/60` | `56/60` / `56/60` | `31/36` / `35/36` | `34/36` / `32/36` |
| `google/gemini-2.5-pro` | `47/52` / `44/52` | `48/52` / `48/52` | `28/33` / `32/33` | `32/33` / `29/33` |
| `google/gemini-2.5-flash` | `49/55` / `46/55` | `51/55` / `51/55` | `24/27` / `27/27` | `25/27` / `21/27` |
| `google/gemma-4-31b-it` | `50/56` / `46/56` | `53/56` / `53/56` | `24/29` / `28/29` | `26/29` / `24/29` |
| `google/gemma-4-26b-a4b-it` | `34/38` / `32/38` | `37/38` / `37/38` | `9/12` / `12/12` | `12/12` / `12/12` |
| `qwen/qwen3.6-plus` | `54/60` / `50/60` | `56/60` / `56/60` | `30/40` / `39/40` | `38/40` / `34/40` |
| `qwen/qwen3.6-27b` | `54/60` / `50/60` | `56/60` / `56/60` | `29/33` / `33/33` | `31/33` / `27/33` |
| `qwen/qwen3.5-27b` | `53/60` / `51/60` | `56/60` / `56/60` | `27/32` / `30/32` | `29/32` / `27/32` |
| `qwen/qwen3.5-122b-a10b` | `54/60` / `51/60` | `56/60` / `56/60` | `27/32` / `32/32` | `30/32` / `28/32` |
| `qwen/qwen3.5-35b-a3b` | `52/58` / `51/58` | `53/58` / `54/58` | `27/31` / `30/31` | `28/31` / `25/31` |
| `qwen/qwen3.5-9b` | `53/60` / `52/60` | `55/60` / `56/60` | `25/36` / `34/36` | `34/36` / `31/36` |
| `moonshotai/kimi-k2.6` | `47/57` / `42/57` | `53/57` / `53/57` | `31/39` / `38/39` | `37/39` / `33/39` |

整體看下來，2 頁 tail 版本下最穩的組合仍是 Gemini 系列，特別是 `gemini-3-flash-preview`；
同時 `qwen3.6-plus` 與 `kimi-k2.6` 在 tail 偵測上的改善也相當明顯，說明多頁 tail 對跨頁結尾情境確實有效。

---

## 意義

閉環三段（Stage1 → gate → Stage2）**串通、實測、且每段對照過**，不再是「各自驗證、推測一致」：
- 獨立定位可達（覆蓋率 90%/97%，見 toc_nav）；
- 定位正確時 VLM 驗得準（head 98%、tail 98%）；
- 錯誤注入下抓得到（head 83–93%、tail 88–98%），且用的是獨立頁。

同時這套機制一魚三吃：**頁尾頁碼 = 來源 A**、**TOC = 來源 B**、**邊界檢查 = 來源 C** → 三角驗證料齊。

---

## 誠實邊界

1. **item 1 在 RELL/TSLA 棄驗**：印刷頁「1」緊鄰前置/TOC、rendered↔printed offset 最不穩，導航偏 2 頁，
   ±1 重定位窗口搆不到真正正文頁 → gate **誠實棄驗**（而非給錯頁）。良率因此 60/62，非 62/62。
2. **RELL item 8 head 失敗**：財報以審計報告開頭，屬 Source C 內容問題、與導航無關。
3. **filing-dependent**：需「TOC 有頁碼欄」+「渲染頁有可抽取的印刷頁碼」。5 份在強化抽取後皆可；純 flow 無頁碼者不適用。
4. **數字為 gated 子集**：偵測率/精確度只算 gate 放行的 item；棄驗者不納入（覆蓋率另計）。
5. **驗證基準是 dataset 頁（內容比對推得）**：故「命中」= 獨立導航與內容比對一致，非對照人工視覺頁標。
6. 樣本為 5 份。

---

## 程式與怎麼跑

| 項目 | 路徑 |
|---|---|
| 端到端精確度 | [run.py](run.py) |
| 端到端 §7.3 偵測率 | [inject.py](inject.py) |

```bash
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --model google/gemini-3-flash-preview --batch 4
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
```
