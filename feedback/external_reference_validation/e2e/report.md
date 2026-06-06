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
| GDC_2023 | 16/16 | 16/16 | 9/10 |
| NFLX_2025 | 11/11 | 11/11 | 9/10 |
| RELL_2025 | 10/11 | 9/10 | 5/6 |
| TSLA_2023 | 11/12 | 11/11 | 8/10 |
| WMT_2026 | 12/12 | 12/12 | 7/8 |
| **合計** | **60/62 (97%)** | **59/60 (98%)** | **38/44 (86%)** |

→ 用**完全獨立的頁**跑 Source C，精確度 head 98% / tail 86%，與用 dataset(內容比對)頁的版本
（head 62/62、tail ~50/50）**基本一致**。唯一 head 失敗 RELL-8 是財報開頭（Source C 內容本身的事，非導航）。

### 補充：13 個 non-free detect model 的 e2e.run 精確度 sweep

為了補齊 `inject.py` 的 clean cache，並檢查端到端流程在不同 Source C 模型下的穩定度，
固定 `nav-model = google/gemini-3-flash-preview`，對 13 個 non-free `detect-model` 全跑一輪 `e2e.run --batch 4`。

下表為 **全部 item** 口徑（含 gate 棄驗造成的分母變化）：

| detect model | head | tail |
|---|---|---|
| `google/gemini-3-flash-preview` | `59/62` | `40/46` |
| `google/gemini-3.1-flash-lite` | `60/62` | `38/46` |
| `google/gemini-2.5-pro` | `58/62` | `38/46` |
| `google/gemini-2.5-flash` | `58/62` | `36/46` |
| `google/gemma-4-31b-it` | `56/62` | `32/46` |
| `google/gemma-4-26b-a4b-it` | `49/62` | `21/46` |
| `qwen/qwen3.6-plus` | `60/62` | `36/46` |
| `qwen/qwen3.6-27b` | `60/62` | `35/46` |
| `qwen/qwen3.5-27b` | `60/62` | `35/46` |
| `qwen/qwen3.5-122b-a10b` | `60/62` | `31/46` |
| `qwen/qwen3.5-35b-a3b` | `60/62` | `33/46` |
| `qwen/qwen3.5-9b` | `60/62` | `33/46` |
| `moonshotai/kimi-k2.6` | `57/62` | `36/46` |

從 e2e 精確度看，`gemini-3-flash-preview` 仍是最穩的 tail baseline；Qwen 族群在 head 很穩，
但 tail 仍明顯低於原本直接吃 dataset page 的 Source C 基準。

## 結果 2：§7.3 偵測率（用 TOC 獨立導航頁，model = gemini-3-flash-preview）

對 gate 放行 item 注入截斷/越界（50 行 / 50%），重用已快取的 VLM 讀、零 API：

| 運算子 | e2e（獨立頁）50行 / 50% | 對照原 §7.3（dataset 頁） |
|---|---|---|
| truncate_head | `53/59` / `49/59` | 90% / 84% |
| overrun_head | `55/59` / `55/59` | 90% / 90% |
| truncate_tail | `33/38` / `36/38` | 86% / 96% |
| overrun_tail | `36/38` / `33/38` | 88% / 82% |

→ **與 GT 頁版本相當（83–95%）**。尤其 `truncate_tail` 87–95% 直接證明：**TOC 導航讓截斷重新可偵測**
——§7.3 的樂觀 caveat 正式關閉，偵測力**不依賴 GT/parser 頁**。

### 補充：13 個 non-free detect model 的 e2e.inject sweep

在上述 `e2e.run` 補齊 clean cache 後，再固定 `nav-model = google/gemini-3-flash-preview`，
對同一批 13 個 non-free `detect-model` 跑 `e2e.inject --batch 4`。下表的分母是：

- `gate` 放行
- clean baseline `>= TH`

因此不同模型的分母仍可能不同；這時分母差異已不再是 cache 缺漏，而是「該模型在 e2e clean 條件下實際可評估的樣本數」。

| detect model | truncate_head | overrun_head | truncate_tail | overrun_tail |
|---|---|---|---|---|
| `google/gemini-3-flash-preview` | `53/59` / `49/59` | `55/59` / `55/59` | `33/38` / `36/38` | `36/38` / `33/38` |
| `google/gemini-3.1-flash-lite` | `54/60` / `50/60` | `56/60` / `56/60` | `31/36` / `35/36` | `34/36` / `32/36` |
| `google/gemini-2.5-pro` | `52/58` / `48/58` | `54/58` / `54/58` | `29/36` / `34/36` | `35/36` / `30/36` |
| `google/gemini-2.5-flash` | `51/58` / `48/58` | `54/58` / `54/58` | `29/34` / `33/34` | `32/34` / `27/34` |
| `google/gemma-4-31b-it` | `50/56` / `46/56` | `53/56` / `53/56` | `28/31` / `30/31` | `29/31` / `27/31` |
| `google/gemma-4-26b-a4b-it` | `43/49` / `40/49` | `47/49` / `47/49` | `20/20` / `20/20` | `17/20` / `15/20` |
| `qwen/qwen3.6-plus` | `54/60` / `50/60` | `56/60` / `56/60` | `26/34` / `33/34` | `32/34` / `27/34` |
| `qwen/qwen3.6-27b` | `54/60` / `50/60` | `56/60` / `56/60` | `27/33` / `32/33` | `30/33` / `27/33` |
| `qwen/qwen3.5-27b` | `53/60` / `51/60` | `56/60` / `56/60` | `30/33` / `33/33` | `31/33` / `27/33` |
| `qwen/qwen3.5-122b-a10b` | `54/60` / `51/60` | `56/60` / `56/60` | `25/30` / `29/30` | `28/30` / `25/30` |
| `qwen/qwen3.5-35b-a3b` | `53/59` / `52/59` | `54/59` / `55/59` | `27/32` / `31/32` | `29/32` / `25/32` |
| `qwen/qwen3.5-9b` | `53/60` / `52/60` | `55/60` / `56/60` | `26/32` / `30/32` | `30/32` / `28/32` |
| `moonshotai/kimi-k2.6` | `47/57` / `42/57` | `53/57` / `53/57` | `30/35` / `34/35` | `33/35` / `28/35` |

整體看下來，e2e 條件下最穩的組合仍是 Gemini 系列，特別是 `gemini-3-flash-preview`：
它同時兼顧較高的 clean precision 與最完整的 inject 分母，適合作為目前的導航+驗證主基線。

---

## 意義

閉環三段（Stage1 → gate → Stage2）**串通、實測、且每段對照過**，不再是「各自驗證、推測一致」：
- 獨立定位可達（覆蓋率 90%/97%，見 toc_nav）；
- 定位正確時 VLM 驗得準（head 98%）；
- 錯誤注入下抓得到（83–95%），且用的是獨立頁。

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
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
python -m feedback.external_reference_validation.e2e.inject --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
```
