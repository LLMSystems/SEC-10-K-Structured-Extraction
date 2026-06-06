# Tier 2 — 渲染 demo

對應 [validation_plan.md](validation_plan.md)「三、共用設施」與「來源 A 頁碼 / 來源 C VLM」。
第一步：把 SEC 10-K 的 HTML 忠實渲染成「人看到的版面」，產出**真實分頁的 PDF** 與**每頁 PNG**，作為後續餵 VLM / 頁碼對照的輸入。

## 安裝

```bash
pip install playwright pymupdf
playwright install chromium
```

## 用法

```bash
# 方式一：CIK + Accession Number（重用 pipeline 的 EDGAR 解析）
python -m feedback.external_reference_validation.render_demo --cik 0000320193 --accession 0000320193-23-000106

# 方式二：直接給主文件 HTML URL
python -m feedback.external_reference_validation.render_demo --url https://www.sec.gov/Archives/edgar/data/.../aapl.htm

# 選項
#   --out feedback/external_reference_validation/out   輸出根目錄
#   --dpi 150         PNG 解析度
#   --max-pages 5     只轉前 N 頁（試跑用）
```

輸出（`feedback/external_reference_validation/out/{stem}/`，已列入 `.gitignore`）：

```
feedback/external_reference_validation/out/0000320193_2023-09-30_000032019323000106/
├── 0000320193_2023-09-30_000032019323000106.pdf   # 真實分頁 PDF
└── pages/
    ├── page_001.png   # 每頁一張，1-based 命名
    └── ...
```

## 兩個設計決定（踩過的坑）

1. **不讓 Chromium 直接 goto(SEC URL)**：瀏覽器會對 HTML + 所有子資源併發請求，
   觸發 SEC fair-access 速率限制 → 回「Undeclared Automated Tool」封鎖頁。
   改為先用 `requests`（單一請求，已證實可用）抓 HTML，再用 Playwright `route`
   攔截把這份 bytes 餵入渲染，其餘子資源 `abort`。代價：圖片 / logo 不顯示，
   但文字版面忠實，對 VLM 抓首尾行 / 頁碼對照已足夠。

2. **`prefer_css_page_size=True`**：尊重文件自己的 `@page` 尺寸，而非強制 Letter。
   SEC iXBRL 文件多半自帶頁面尺寸，強制套 Letter+margin 會把一個版面頁拆成多頁、
   產生半空白頁。用文件自身尺寸後，PDF 分頁貼近原本的印製頁
   （AAPL 2023：81 頁 → 61 頁）。

## 已知待處理（給來源 A 頁碼用）

- **PDF 頁序 ≠ 文件印製頁碼**：封面 / TOC 常不計頁或用羅馬數字，兩者差一個固定 offset。
  例：AAPL 2023 的 PDF 第 20 頁 = 頁尾印製的「17」。
  後續要建立 `char_offset → 頁碼` 對照時，需決定以「PDF 頁序」或「印製頁碼」為準。
- **圖片未渲染**：目前 abort 所有子資源。若某些 filing 把重要表格存成圖片，需改成
  允許圖片並做速率控制（每秒 < 10 req）。
