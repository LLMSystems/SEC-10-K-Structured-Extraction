# 使用說明

這個資料夾整理了 SEC 10-K 解析器的兩套驗證方法與對應報告：

- 多模態視覺驗證器
- 確定性驗證器
- 綜合報告與可重現腳本

主要報告：

- [combined_validation_report.md](./combined_validation_report.md)

## 資料夾導覽

- [external_reference_validation](./external_reference_validation/)
  多模態視覺驗證器。核心工作是先找可信頁面，再比對頁面證據與解析器輸出是否一致。
- [deterministic_validation](./deterministic_validation/)
  確定性驗證器。核心工作是檢查違反即證錯的必要結構條件。
- [combined_validation_report.md](./combined_validation_report.md)
  兩套驗證器的完整整合報告。

## 最短重跑路徑

1. 確定性驗證器

```powershell
python -m feedback.deterministic_validation.runner
```

2. 多模態視覺驗證器：頁面導航覆蓋率

```powershell
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
```

3. 多模態視覺驗證器：端到端錯誤偵測率

```powershell
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
```

單一 filing 的端到端精確度：

```powershell
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --model google/gemini-3-flash-preview --batch 4
```

## Linux 重現

照下面順序執行：

```bash
pip install -r feedback/requirements.txt
python -m feedback.deterministic_validation.runner
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --model google/gemini-3-flash-preview --batch 4
```

這組命令對應到四件事：

- 確定性驗證器能否正常跑完
- 頁面導航覆蓋率能否重現
- 端到端錯誤偵測率能否重現
- 單一 filing 的端到端精確度能否重現

## 安裝需求

```powershell
pip install -r feedback/requirements.txt
```

這份 `requirements.txt` 包含：

- 結構驗證所需的文字處理套件
- 多模態 API 呼叫套件
- `.env` 載入套件
- PDF 轉頁圖片所需的 `PyMuPDF`

## API 設定

多模態視覺驗證器會從 `.env` 讀取金鑰。

如果使用 OpenAI：

```env
OPENAI_API_KEY=...
```

如果使用 OpenRouter：

```env
OPENROUTER_API_KEY=...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

## 主程式在哪裡

### 確定性驗證器

主入口：

- [runner.py](./deterministic_validation/runner.py)

資料來源：

- [eval_datasets/ground_truth](../eval_datasets/ground_truth/)

補充說明：

- `runner.py` 會透過 [model.py](./deterministic_validation/model.py) 讀取 [eval_datasets/ground_truth](../eval_datasets/ground_truth/) 下的 `*/*/*.json`
- 這批資料是 34 份人工標註 Ground Truth，屬於 repo 根目錄下的共用評測資料，不放在 `feedback/` 內

主要規則與資料：

- [rules.py](./deterministic_validation/rules.py)
- [mutations.py](./deterministic_validation/mutations.py)
- [model.py](./deterministic_validation/model.py)

常用指令：

```powershell
python -m feedback.deterministic_validation.runner
python -m feedback.deterministic_validation.runner --dump
```

用途：

- 驗證 34 份人工標註 Ground Truth 的不誤殺率
- 驗證 3,760 個系統性注入錯誤樣本的偵測率
- `--dump` 會把所有注入錯誤樣本輸出到 [mutants](./deterministic_validation/mutants/)

### 多模態視覺驗證器

主入口：

- [toc_extract.py](./external_reference_validation/toc_nav/toc_extract.py)
- [coverage.py](./external_reference_validation/toc_nav/coverage.py)
- [run.py](./external_reference_validation/e2e/run.py)
- [inject.py](./external_reference_validation/e2e/inject.py)

核心資料：

- [dataset](./external_reference_validation/dataset/)
- [report](./external_reference_validation/report/)
- [vlm_cache](./external_reference_validation/vlm_cache/)

資料來源：

- [dataset](./external_reference_validation/dataset/)

資料結構：

- [dataset/index.json](./external_reference_validation/dataset/index.json)：資料集索引
- `dataset/<LABEL>/<LABEL>.pdf`：原始 filing PDF
- `dataset/<LABEL>/<LABEL>_content.json`：item 內容標註
- `dataset/<LABEL>/<LABEL>_pages.json`：item 對應頁碼標註
- `dataset/<LABEL>/pages/page_XXX.png`：逐頁渲染圖片

常用指令：

抽出目錄：

```powershell
python -m feedback.external_reference_validation.toc_nav.toc_extract --label GDC_2023 --model google/gemini-3-flash-preview
```

頁面導航覆蓋率：

```powershell
python -m feedback.external_reference_validation.toc_nav.coverage --model google/gemini-3-flash-preview
```

單一 filing 端到端精確度：

```powershell
python -m feedback.external_reference_validation.e2e.run --label GDC_2023 --model google/gemini-3-flash-preview --batch 4
```

端到端錯誤偵測率：

```powershell
python -m feedback.external_reference_validation.e2e.inject --model google/gemini-3-flash-preview
```

用途：

- `toc_nav`：從目錄建立 `item -> 可信頁面` 的對應，再對照 PDF 頁面
- `e2e.run`：在可信頁面上檢查開頭與尾段是否和正確內容一致
- `e2e.inject`：在固定頁面證據下，測試注入錯誤後能不能被驗證器抓出

## 結果會寫到哪裡

### 確定性驗證器

- 預設直接把結果印在終端
- `--dump` 會把注入樣本寫到 [deterministic_validation/mutants](./deterministic_validation/mutants/)

### 多模態視覺驗證器

- 快取寫在 [external_reference_validation/vlm_cache](./external_reference_validation/vlm_cache/)
- 報表與摘要寫在 [external_reference_validation/report](./external_reference_validation/report/)
- 端到端結果整理在 [external_reference_validation/e2e/report.md](./external_reference_validation/e2e/report.md)

## 建議閱讀順序

1. 先看 [combined_validation_report.md](./combined_validation_report.md)
2. 再看 [deterministic_validation](./deterministic_validation/) 與 [external_reference_validation](./external_reference_validation/)
3. 最後依需求重跑對應腳本

## 備註

- 多模態視覺驗證器需要 API key，並透過 `.env` 提供給程式
- `e2e.inject` 會重用既有快取；如果快取不存在，會補做必要的多模態呼叫
- 視覺驗證會把 PDF 頁面轉成 PNG，所以需要 `PyMuPDF`
- 若要重現報告中的主結果，建議優先使用 `google/gemini-3-flash-preview`
