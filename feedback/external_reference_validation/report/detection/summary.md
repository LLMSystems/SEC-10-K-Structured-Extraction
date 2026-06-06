# Tier 2 §7.3 偵測力評估（錯誤注入，零 API）

TH=75；detected ⟺ clean PASS 且注入後 FAIL。嚴重度 win≈300字 / 25% / 50%。

## 偵測率（每模型）

| 模型 | FP head | FP tail | truncate_head/50lines | truncate_head/50% | overrun_head/50lines | overrun_head/50% | truncate_tail/50lines | truncate_tail/50% | overrun_tail/50lines | overrun_tail/50% |
|---|---|---|---|---|---|---|---|---|---|---|
| google/gemini-3-flash-preview | 0/62 | 0/50 | 90% | 84% | 90% | 90% | 86% | 96% | 88% | 82% |

> tail 盲區（table/figure 桶不驗 tail）：各模型 12 個 item，其 truncate_tail/overrun_tail 注入了也抓不到（覆蓋缺口）。