# §7.3 偵測力證明：錯誤注入評估 — 預計怎麼做

> 對應 [validation_plan.md](validation_plan.md) §7.3（精確度 / 偵測率）
> 被測對象：[vlm_first_last.py](vlm_first_last.py) 的首尾段驗證器 + 已選模型
> 現況：[vlm_first_last_results.md](vlm_first_last_results.md)（14 模型 k=1 survey）

---

## 0. 一句話

對 5 份 dataset 的 GT 邊界**人工注入**截斷/越界錯誤，看驗證器的 ratio 是否如預期掉到門檻下。
量兩個數：**偵測率**（注入的錯誤抓到幾成）與**誤殺率**（正確的被誤判 FAIL 幾成）。

---

## 1. 為什麼非做不可（批判回顧）

目前 survey 只證明了「對**正確** GT，最佳模型會 PASS（如 gemini-3-flash tail 50/50）」。
但**從未證明「對錯誤的邊界會 FAIL」**——一個永遠說 PASS、又沒被測過會不會對錯資料說 FAIL 的驗證器，
它的 **false-negative（漏抓）率是未知數**。唯一反證是它意外抓到我們自己的 page-range bug（7A/9B），
屬偶然、非系統性。§7.3 就是把「偶然抓到」變成「量化的偵測率」。

---

## 2. 核心設計：注入到 GT 側，重用快取 VLM → 零 API

驗證器比的是 `ratio(GT_window, VLM_read)`：
- **GT_window** = `content_text` 的首/末 300 字塊 = **parser 對邊界的宣稱**。
- **VLM_read** = 模型看 PNG 轉錄 = **版面真相**（與 parser 失敗模式獨立）。

→ 模擬「parser 邊界抓錯」＝**只汙染 GT_window**（image/VLM 不動）。汙染後 GT 與版面真相對不上 → ratio 掉 → FAIL → 偵測到。

**關鍵：零 API。** 每頁的 VLM 轉錄已存在 `vlm_cache/<model>/`（依 image+prompt+model 雜湊）。
注入只是「換一個 GT 字串、重算一次 `partial_ratio`」——可免費掃所有運算子 × 嚴重度 × 模型。

---

## 3. 錯誤運算子（對應 §7.3 的 truncate_tail / shift_head）

對 prose 桶 item 的首/末塊注入。嚴重度以「丟/加幾行」掃描（用 `content_text` 原始換行）。

| 運算子 | 模擬的 parser 錯誤 | 怎麼做 | 影響側 |
|---|---|---|---|
| `truncate_tail(k)` | 尾巴少抓 k 行 | 砍掉 `content_text` 末 k 行 → tail 塊變成更早的段落 | tail |
| `truncate_head(k)` | 起點太晚、頭少抓 k 行 | 砍掉開頭 k 行 → head 塊變成更後面的段落 | head |
| `overrun_head(k)` | 頭越界進**前一個** item | 把前一個 item 末 k 行接到開頭 → head 塊混入外來文字 | head |
| `overrun_tail(k)` | 尾巴越界進**下一個** item | 把下一個 item 前 k 行接到結尾 → tail 塊混入外來文字 | tail |

- **嚴重度（皆為實質截斷，以 content_text 行為單位）**：刻意**只測有意義的邊界錯誤**——小截斷實務上可接受、不在測試範圍。
  - **50 行**（絕對）：丟/借 50 行；短 item(<50行) 留 1 行幾近全砍。
  - **50%**（比例）：丟/借一半行；對長短 item 都穩健。
- `drop_item`（漏抓整個 item）屬**來源 B（TOC）**，不在本評估；這裡只證 C 的邊界偵測。

---

## 4. 指標

**detection 判定（純門檻跨越）**：`detected ⟺ (clean_ratio ≥ TH) ∧ (injected_ratio < TH)`。
只看「有沒有跌破 TH」（＝生產時驗證器的實際 FAIL 行為），**不額外要求掉幾分**；ratio Δ 只當輔助診斷。
要求 clean 須為 PASS，是讓掉分明確歸因於注入、而非本來就誤殺。

| 指標 | 定義 | 期望 |
|---|---|---|
| **偵測率 / recall** | 注入後 `(clean_ratio≥TH) ∧ (inj_ratio<TH)` 的比例 | 中/大嚴重度趨近 1 |
| **誤殺率 / FP** | 不注入時 clean item 仍 FAIL 的比例（負控，沿用 survey 數字）| 低 |
| **ratio Δ**（輔助）| clean_ratio − injected_ratio（連續敏感度）| 越大越好 |
| **最小可偵測嚴重度** | 偵測率首次 ≥ 0.9 的嚴重度檔 | 越小越靈敏 |

- 對**排行前 5 名**各跑一份：`gemini-3-flash-preview`、`qwen3.6-plus`、`gemini-3.1-flash-lite`、
  `kimi-k2.6`、`qwen3.6-27b`（tail 50/49/48/48/47）。五者的 VLM 轉錄 survey 時都已快取 → **仍零 API**。
- 首尾**分開**報（head 全 item、tail 僅 prose 桶）。

---

## 5. 流程（pseudo，全部吃快取）

```
for model in 候選:
  for filing, item in prose items:
    pred_head, pred_tail = 讀 vlm_cache（clean 時已產生）
    clean_head_gt = strip_heading(body_text(ct))[:300]
    clean_tail_gt = body_text(ct)[-300:]
    for op, k in 運算子 × {1,2,3,5}:
      ct' = apply(op, ct, k, prev_ct, next_ct)
      inj_gt = 對應側重算窗口（body_text/strip_heading）
      inj_ratio = partial_ratio(inj_gt, norm(pred_受影響側))
      detected = (clean_ratio>=TH) and (inj_ratio < TH)
  聚合：每 op×k 的 detection rate、ratio Δ、最小可偵測 k
```

---

## 6. 產出

```
feedback/external_reference_validation/report/detection/
  <model>.json     # 每 item×op×k：clean_ratio / inj_ratio / detected
  summary.md       # 模型 × op × k → detection rate；FP（負控）；最小可偵測 k
```

---

## 7. 誠實邊界

1. **只注入 GT 文字側**：模擬「parser 邊界文字錯」，**不模擬頁碼/分頁錯**（那是來源 A 的事）。
2. **只測實質截斷（50 行 / 50%）**：小幅截斷實務上可接受，刻意不在測試範圍——故偵測率不涵蓋小錯，這是明確的覆蓋取捨，非 bug。
3. **table/figure_tail 桶的盲區**：這些 item 的 tail 不直接驗（由下一個 head 覆蓋），所以 `truncate_tail`/`overrun_tail` 對它們**注入了也抓不到**——評估會把這個覆蓋缺口明確標出來。
4. **只 5 份 clean filing**：偵測率是在乾淨樣本上量的；messy filing 未涵蓋。
5. GT 是 dataset 的 `content_text`（代理 GT）；偵測率是相對此代理而言。

---

## 8. 決策（已定）

- **嚴重度**：`50 行`（絕對）＋`50%`（比例），皆為實質截斷；**不測小截斷（win/1~3行）、不用 25%**。
- **模型**：跑**排行前 5 名**（見 §4）。
- **detection 判定**：純門檻跨越 `(clean PASS) ∧ (inj FAIL)`，不要求 ratio Δ（Δ 只當輔助）。
- **overrun 外來文字**：用**相鄰 item 的 `content_text`**（乾淨、簡單）。
