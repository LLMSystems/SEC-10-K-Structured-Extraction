# 來源 C：VLM 首尾行驗證 — demo 預計怎麼做

> 對應 [validation_plan.md](validation_plan.md) §六（來源 C，主力）與 §七（驗證驗證器）
> 已備設施：[vlm_reader.py](vlm_reader.py)（`VLMImageReader`，OpenRouter + 快取）、[dataset/](dataset/)（5 份高信心 filing 的 pdf + 每頁 PNG + 起訖頁 + GT 內文）
> 候選模型：[model_references/bench.md](model_references/bench.md)

---

## 0. 一句話

對 5 份高信心 filing 的每個 extracted item，**只把它起始頁與結束頁的 PNG 餵 VLM**，請它逐字轉錄該 item 的**開頭 / 結尾**（prompt 界定約 5 行），再與 GT `content_text` 的首/末**文字塊**做 `partial_ratio` 比對。一致 → 邊界 PASS；不一致 → 標記人工複查。在固定 `temperature=0` 下以 **k=1 準確度**跨模型比較，選最準的當生產驗證器（**不做** pass@k/pass^k，理由見 §5）。

---

## 1. 為什麼是「首尾各 5 行」而不是整段、也不是單行

- 邊界正是錯誤藏身處：**截斷**（尾巴少抓）或**越界**（頭跨進前一個 item）。中段抓錯的機率遠低，且驗中段要餵整份、成本高。
- **5 行而非單行**：單行太脆——VLM 把一行拆成兩行、OCR 錯一個數字、或誤抓標題，就會誤判 fail。整塊 5 行做 fuzzy：窗口大，**容忍單行層級的差異**，又能讓「尾巴被截掉 3 行」這類真錯誤明顯掉分（漏越多行、相似度越低）。
- 只回 5+5 行 → VLM 輸出仍短、可直接 fuzzy 比對，且**只需邊界頁**（起始頁 + 結束頁），成本可控。
- 與 regex 失敗模式獨立：VLM 看的是**版面視覺**（粗體標題、字級、留白、下一個 item 標題的視覺起點），regex 看字串 pattern。兩者對「item 從哪幾行起、到哪幾行止」一致 → 強證據。

---

## 2. 輸入（全部已存在於 dataset）

每份 filing 一個資料夾，逐 item 取：

| 要素 | 來源 | 用途 |
|---|---|---|
| 起始頁 PNG | `pages/page_{start_page:03d}.png`（頁碼來自 `_pages.json`）| 問該 item 開頭 |
| 結束頁 PNG | `pages/page_{end_page:03d}.png` | 問該 item 結尾 |
| item 編號 + 標題 | `_content.json` 的 `item_number` / `item_title` | 告訴 VLM 找哪個 item；head GT 也用標題 `strip_heading()` 對齊 |
| 下一個 item cue | `next_item_cues()`：`content.json` **完整順序**的下一個 item（**含** by_reference，如 9B→9C）| tail 的「止於何處」邊界線索 |
| GT 文字塊 | `body_text(content_text)`＝去 HTML＋正規化的**連續文字**；head 取 `strip_heading(bt)[:300]`、tail 取 `bt[-300:]` | 比對基準（連續文字塊，非逐行）|

> 不再用 `body_lines()` 切 5 行——`content_text` 沒有「視覺行」概念，逐行比會假性 fail（見 §3）。

---

## 3. 流程

```
for filing in dataset/*:
  for item in extracted items（有起訖頁）:
    kind = tail_kind(content_text)          # prose / table / figure（§分桶）
    bt   = body_text(content_text)          # 去 HTML + 正規化的「連續文字」
    # head（三桶都驗）：GT 先剝「Item N. 標題」對齊 VLM
    head_gt   = strip_heading(bt, num, title)[:300]
    head_pred = VLM(start_page.png, HEAD_PROMPT)            # 純文字
    head_ratio = fuzz.partial_ratio(head_gt, norm(head_pred))
    # tail（僅 prose 桶驗；table/figure 桶 tail 不驗，由下一個 item 的 head 覆蓋）
    if kind == "prose":
        tail_gt   = bt[-300:]
        tail_pred = VLM(end_page.png, TAIL_PROMPT(next_cue))
        tail_ratio = fuzz.partial_ratio(tail_gt, norm(tail_pred))
    PASS = ratio >= 75
```

**為何「連續文字塊 + partial_ratio」而非逐行比 5 行**：`content_text` 是文字流、沒有「視覺行」概念，
VLM 看到的才是版面行——兩者「行」對不上（行寬、斷行、子標題都不同），逐行 `fuzz.ratio` 會假性全 fail。
改成兩邊各正規化成連續字串、GT 取 300 字窗口，用 `fuzz.partial_ratio` 找最佳子串對齊：對斷行/長度差不敏感、對「漏抓一段」敏感。

**Prompt 設計（要點，反映實作）**
- **head**：找 *Item N* 標題，逐字抄它後面**前 5 行**；**含報告抬頭/收件人/子標題**（別自作主張略過，只忽略 running header/頁碼/頁尾）——此修正讓 item 8 財報的審計報告開頭能對齊。
- **tail**：先定位 item 結尾邊界（下一個 item 標題若出現在本頁 → 其正上方；否則頁面最底），逐字抄**最後 5 行**。下一個 item cue 取自 `content.json` 完整順序（含 by_reference）。
- **輸出純文字、不要 JSON**：10-K 內文滿是引號，小模型常把 JSON 跳脫寫壞。
- 「5 行」只是**界定輸出長度**；GT 端用 300 字窗口 + `partial_ratio` 當連續文字比，不逐行解析。

**呼叫設定（`vlm_reader`）**：`max_tokens=2048` ＋ 對推理模型關 `reasoning`（否則思考把額度用光、content 變空；少數端點如 gemini-2.5-pro 反而必須開 reasoning，做豁免）；空回應退避重試。

**門檻**：`partial_ratio ≥ 75`（經驗空隙：正確讀 81~100、真錯誤 ≤50，75 兩邊留裕度）。詳見 [vlm_first_last_results.md](vlm_first_last_results.md)。

---

## 4. 比對基準的誠實前提

`validation_plan.md` §7.1 原設想要「重新標註視覺首/末行」。本 demo 先用一個**夠好的代理 GT**：GT `content_text` 去 HTML＋正規化成連續文字（`body_text`），取首/末 300 字窗口（head 端再 `strip_heading` 剝標題）。

- **為何可代理**：`content_text` 是人工標註的 item 內容真值；其首/末文字就是邊界應涵蓋的內容。
- **已知落差**：
  1. content_text 開頭含「Item N. 標題」→ `strip_heading()` 剝掉，對齊 VLM（被要求略過 item 大標、給其後正文）。
  2. content_text 末段可能是 HTML 表格 / 圖（如 Item 8 財報、Item 5 績效圖）→ 連續文字塊的末 300 字非視覺最後幾行。**由 `tail_kind` 分桶處理**（table/figure → covered/last，tail 不直接比、由下一個 item 的 head 覆蓋；見 §分桶與 results）。
  3. item 太短（內文 < 20 字）→ 略過該端（標 skipped）。
- 若偽陽性集中在某類 item，再決定是否對這幾份做真正的視覺行人工標註（升級成 §7.1 的標準 GT）。

---

## 5. 模型選型：固定 temperature=0 的 k=1 準確度 survey（不做 pass@k / pass^k）

**原本構想**是同輸入問 k 次、用 pass@k（能力上界）與 pass^k（穩定度）選模型。
**實作後改變決定**：驗證器固定 `temperature=0`。在確定性取樣下，同一輸入重複問 k 次結果（近乎）相同，
`pass@1 ≈ pass@k ≈ pass^k` 三者塌縮成同一個數——量穩定度沒有資訊量，只是多花 k 倍成本與時間。

→ 改為：**固定 `temperature=0`、k=1**，把候選模型逐一在全部 5 份 dataset 上跑一遍，
直接比**準確度**（head / tail 的 PASS 率）。完整結果與排行見 [vlm_first_last_results.md](vlm_first_last_results.md)。

**誠實邊界**：`temperature=0` 不保證 bit-level 確定（provider 端 MoE 路由 / 批次 / 浮點，甚至偶發空回應）。
但這類是**暫時性 infra 雜訊**，用退避重試吸收（見 results 的「空回應重試」），**不是模型選型的軸**。
若日後要把驗證器跑在 `temperature>0`，或要量「同輸入跨次會不會漂」，再回來補 pass^k——目前用不到。

> 候選不再限於最初 5 個 open-source：實際 survey 了 14 個（Gemma / Qwen / Gemini / Kimi 各版本），
> 見 results 的 TL;DR 排行。**多模態前置查核已併入 survey 本身**——不能讀圖的模型 head 會直接崩，一眼可辨。

---

## 6. demo 產出

```
feedback/external_reference_validation/report/vlm_first_last/
  <model>/<label>.json        # 每個 item：item / bucket / start_page / end_page /
                              #   head{ratio,pass,pred,gt} / tail{ratio,pass,pred,gt 或 skipped}
```

- 彙整與跨模型排行寫在 [vlm_first_last_results.md](vlm_first_last_results.md)（非自動產 summary.md）。
- 規模感：5 份 ≈ 每份 ~12 個 extracted item → ~60 item × 2 端 = ~120 次/模型（**k=1**，table/figure 桶的 tail 省略）。
  有圖片 SHA 快取，prompt/頁不變即命中；換模型才整批新打。

---

## 7. 步驟拆解

1. **首尾段驗證器** `feedback/external_reference_validation/vlm_first_last.py`：載入一份 dataset，逐 item 取起訖頁 PNG、組 prompt、呼叫 `VLMImageReader`、整塊 fuzzy 比對、輸出每 item 結果。先**單份單模型 k=1** 驗 prompt 與解析。✅ 已完成
2. **跨模型 survey（temperature=0、k=1）**：候選模型逐一跑全 5 份，聚合 head / tail PASS 率。✅ 已完成（14 模型）
3. **排行與選型**：彙整各模型 head/tail 通過率成排行、挑前段當生產候選。✅ 見 [vlm_first_last_results.md](vlm_first_last_results.md)
4. **校準門檻 + 誠實邊界**：依結果定 fuzz 門檻、標出表格結尾弱項，回填 `validation_plan.md` §7。

---

## 8. 待確認

- ~~**模型 slug + 多模態**~~：已解決——14 個模型實跑過，不能讀圖者 head 直接崩。
- ~~**k 與通過門檻 / 量穩定度的 temperature**~~：已取消——固定 `temperature=0`、k=1，不做 pass@k/pass^k（見 §5）。
- **fuzz 門檻**：實作已落在 `partial_ratio ≥ 75`（連續文字塊比對）；待再校準的是「過短答案」偏寬鬆問題（見 results）。
- **表格結尾 item**：已用 `tail_kind` 分桶（table/figure → covered/last），其 tail 不直接驗、由下一個 item 的 head 覆蓋。
