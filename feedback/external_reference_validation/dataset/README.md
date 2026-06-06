# Tier 2 頁碼資料集（5 份高信心 10-K）

每個資料夾一份 filing，含三個檔：

- `<label>.pdf` — 渲染後分頁 PDF（VLM / 頁碼對照來源）
- `<label>_pages.json` — 每個 item 的 `start_page` ~ `end_page`（PDF 頁序）
- `<label>_content.json` — 內文：每個 item 的 `content_text` / `char_range` / `status`（人工標註 GT）
- `pages/page_NNN.png` — 每頁 PNG @150dpi（長邊 1650 ≈ Claude vision 上限；整頁輸入最佳）

## 內容

| filing | 公司 | items | PDF 頁數 |
|---|---|---|---|
| GDC_2023 | GD Culture Group Ltd | 16 | 121 |
| NFLX_2025 | NETFLIX INC | 11 | 83 |
| RELL_2025 | RICHARDSON ELECTRONICS, LTD. | 11 | 75 |
| TSLA_2023 | Tesla, Inc. | 12 | 114 |
| WMT_2026 | Walmart Inc. | 12 | 87 |