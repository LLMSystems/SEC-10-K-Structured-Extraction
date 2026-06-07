# Feedback

這個資料夾放的是 SEC 10-K 解析器驗證相關的：

- 實驗程式
- 驗證資料
- 可重跑結果
- 最終報告

如果你是第一次到這裡，建議先看：

- [combined_validation_report.md](./combined_validation_report.md)

這份是目前最完整的總報告；其餘子資料夾則是支撐這份報告的程式與資料。

## 這裡有什麼

- [external_reference_validation](./external_reference_validation/)
  多模態視覺驗證器。負責頁面導航、可信頁面確認、首尾段邊界核對、錯誤注入測試。
- [deterministic_validation](./deterministic_validation/)
  確定性驗證器。負責檢查任何正確解析都必然滿足的結構條件。
- [combined_validation_report.md](./combined_validation_report.md)
  兩個驗證器整合後的總報告。

## 最短開始方式

如果你只想先確認程式能跑通，建議從這兩個入口開始：

1. 確定性驗證器

```powershell
python -m feedback.deterministic_validation.runner
```

2. 多模態視覺驗證器

```powershell
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
```

上面三個指令對應到：

- 頁面導航覆蓋率
- 端到端精確度
- 端到端錯誤偵測

## 執行前準備


### 安裝依賴

```powershell
pip install -r feedback/requirements.txt
```

這份 `requirements.txt` 已包含：

- 基本文字處理與比對套件
- 多模態 API 呼叫套件
- `.env` 載入套件
- PDF 頁面處理用的 `PyMuPDF`

### 設定 API Key

多模態視覺驗證器會透過 [vlm_reader.py](./external_reference_validation/vlm_reader.py) 讀取 `.env`。

如果使用 OpenAI：

```env
OPENAI_API_KEY=...
```

如果使用 OpenRouter：

```env
OPENROUTER_API_KEY=...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

OpenRouter 常用的額外設定可以再補：

## 主程式入口

### 確定性驗證器

主程式：

- [runner.py](./deterministic_validation/runner.py)

相關模組：

- [rules.py](./deterministic_validation/rules.py)
- [mutations.py](./deterministic_validation/mutations.py)
- [model.py](./deterministic_validation/model.py)

執行：

```powershell
python -m feedback.deterministic_validation.runner
python -m feedback.deterministic_validation.runner --dump
```

用途：

- 在 34 份正確標註資料上量誤殺率
- 在 3,760 個注入錯誤樣本上量偵測率
- `--dump` 會把每個規則的錯誤樣本輸出到 [mutants](./deterministic_validation/mutants/)

### 多模態視覺驗證器

主要程式：

- [toc_extract.py](./external_reference_validation/toc_nav/toc_extract.py)
- [coverage.py](./external_reference_validation/toc_nav/coverage.py)
- [run.py](./external_reference_validation/e2e/run.py)
- [inject.py](./external_reference_validation/e2e/inject.py)

相關資料：

- [dataset](./external_reference_validation/dataset/)
- [report](./external_reference_validation/report/)
- [vlm_cache](./external_reference_validation/vlm_cache/)

常用指令：

頁面導航抽取目錄：

```powershell
python -m feedback.external_reference_validation.toc_nav.toc_extract --label GDC_2023 --model google/gemini-3-flash-preview
```

頁面導航覆蓋率：

```powershell
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
```

端到端精確度：

```powershell
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --nav-model google/gemini-3-flash-preview --detect-model google/gemini-3-flash-preview --batch 4
```

端到端錯誤偵測：

```powershell
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
```

用途：

- `toc_nav`：從目錄頁抽出 `item -> 印刷頁碼`，再對齊到 PDF 頁面
- `e2e.run`：在可信頁面上檢查開頭與尾段是否和正確內容一致
- `e2e.inject`：在正確樣本上注入截斷或越界，檢查模型能不能抓到

## 結果會寫到哪裡

### 確定性驗證器

- 終端輸出：每條規則的誤殺率與偵測率
- 檔案輸出：`--dump` 時寫到 [deterministic_validation/mutants](./deterministic_validation/mutants/)

### 多模態視覺驗證器

- 快取：寫到 [external_reference_validation/vlm_cache](./external_reference_validation/vlm_cache/)
- 中間結果與報表：寫到 [external_reference_validation/report](./external_reference_validation/report/)
- 端到端報告：可參考 [external_reference_validation/e2e/report.md](./external_reference_validation/e2e/report.md)

## 建議閱讀順序

如果你想快速理解整體設計：

1. 先看 [combined_validation_report.md](./combined_validation_report.md)
2. 再看 [deterministic_validation](./deterministic_validation/) 與 [external_reference_validation](./external_reference_validation/)
3. 最後視需要重跑上面的主程式

## 常見注意事項

- 多模態視覺驗證器需要 API key；若沒設 `.env`，會在 `vlm_reader.py` 直接報錯。
- `e2e.inject` 會大量使用快取；若前面沒跑過對應模型與頁面，第一次仍可能打 API。
- 多模態流程會讀 PDF 與 PNG，若本機缺 `PyMuPDF`（`fitz`），頁面相關腳本會失敗。
- 命令中的模型名稱可以替換，但如果要重現報告中的基線，請優先使用 `google/gemini-3-flash-preview`。
