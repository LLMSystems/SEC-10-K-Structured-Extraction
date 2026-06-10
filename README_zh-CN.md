# SEC 10-K 結構化抽取工具

將 SEC EDGAR 上的 Form 10-K 年報解析成標準化 JSON。
自動識別所有 Item 的內容與狀態，零 LLM 費用，平均耗時 < 1 秒。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status Accuracy](https://img.shields.io/badge/status%20accuracy-100%25-brightgreen)](eval_datasets/results/)

---

## Features

- **完整 Item 覆蓋**：解析 Part I–IV 全部 16 個 Item，輸出 `extracted` / `incorporated_by_reference` / `not_applicable` / `reserved` / `missing` 五種狀態
- **零 LLM 費用**：純規則式 pipeline，解析本身只需 ~0.03 秒，無 API 呼叫
- **iXBRL 支援**：自動處理 2019 年後大型公司強制使用的 iXBRL 格式
- **XBRL 財報擷取**：額外支援從 XBRL 直接還原 Item 8 主要財務報表（損益表、資產負債表、現金流量表等）
- **同步 / 異步 API**：`Pipeline` 與 `AsyncPipeline` 兩種使用方式

---

## Quick Start

### 安裝

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 使用方式

#### 同步

```python
from src.pipeline import Pipeline
from src.models import FilingInput

pipeline = Pipeline()

# 方式一：CIK + Accession Number
result = pipeline.run(FilingInput(
    cik="0000320193",
    accession_number="0000320193-23-000106",
))

# 方式二：直接給 URL
result = pipeline.run(FilingInput(
    url="https://www.sec.gov/Archives/edgar/data/.../filing.htm",
))

# 儲存結果（JSON + Markdown）
result = pipeline.run(input, save_to="output/")
```

#### 異步

```python
from src.async_pipeline import AsyncPipeline
from src.models import FilingInput
import asyncio

async def main():
    pipeline = AsyncPipeline()
    result = await pipeline.run_async(FilingInput(
        cik="0000320193",
        accession_number="0000320193-23-000106",
    ))

asyncio.run(main())
```

---

## 輸出格式

```json
{
  "filing_info": {
    "cik": "0000320193",
    "accession_number": "0000320193-23-000106",
    "company_name": "Apple Inc.",
    "fiscal_year_end": "2023-09-30"
  },
  "items": [
    {
      "part": "Part I",
      "item_number": "1",
      "item_title": "Business",
      "content_text": "Apple Inc. designs, manufactures...",
      "char_range": [1024, 45231],
      "status": "extracted"
    },
    {
      "part": "Part III",
      "item_number": "10",
      "item_title": "Directors, Executive Officers and Corporate Governance",
      "content_text": null,
      "char_range": null,
      "status": "incorporated_by_reference"
    }
  ]
}
```

---

## How It Works

本專案建立一條純規則式的結構化抽取 Pipeline：

```text
輸入（CIK + Accession Number 或直接 URL）
  ↓ fetch       從 SEC EDGAR API 取得 metadata 與 HTML
  ↓ preprocess  HTML → 純文字（處理 iXBRL、table、斷字）
  ↓ parse       RegexParser 找到各 Item 的起終位置
  ↓ postprocess 分類每個 Item 的 status
輸出（標準化 JSON）
```

架構概覽：

```text
src/
├── pipeline.py             主流程
├── async_pipeline.py       非同步版本
├── models.py               資料結構
├── patterns.py             Regex Pattern 定義
├── postprocessor.py        Item status 分類
├── item8_xbrl_facts.py     XBRL 財報擷取
├── render_item8_markdown.py XBRL 結果渲染
├── parsers/
│   ├── regex_parser.py     主 Parser
│   └── hybrid.py           調度器（支援未來接入 LLM fallback）
└── eval/
    ├── metrics.py          評測程式
    └── runner.py           批次評測入口
```

---

## Evaluation

在 **35 筆申報、12 家公司、2016–2026 年**的手工標註資料集上：

| 指標 | 數值 |
| --- | --- |
| Status 準確率 | **100.0%**（788 / 788 items） |
| Critical Regressions | **0** |
| 內容長度正常比例 | 99.0%（484 / 489 extracted items） |
| 頭尾比對通過率 | 頭部 99.8% / 尾部 100.0% |
| 平均耗時 | **0.687 秒** |
| LLM 費用 | **$0** |

在額外隨機抽樣的 **507 份申報**大規模驗測中，結構錯誤率較初版下降 **95.5%**。

Ground truth 由人工標註，使用 [SEC-10-K-Annotation-Tool](https://github.com/LLMSystems/SEC-10-K-Annotation-Tool)。詳細報告見 [eval_datasets/results/](eval_datasets/results/)。

### 執行評測

```bash
python -m src.eval.metrics \
    --ground-truth eval_datasets/ground_truth \
    --output eval_datasets/results
```

---

## XBRL 財報擷取（Item 8）

從 XBRL 直接還原 Item 8 主要財務報表，輸出為多期間 Markdown 報告。

```python
from src.item8_xbrl_facts import get_item8_xbrl_facts
from src.render_item8_markdown import write_item8_markdown

payload = get_item8_xbrl_facts("0000019617", "0001628280-26-008131")
write_item8_markdown(payload, "output_item8.md")
```

解析來源包含 Instance Document、Presentation Linkbase、Label Linkbase、Schema 四份 XBRL 檔案，自動分類為主要財務報表、數字揭露、文字揭露三個區塊。

---

## 已知限制

- 適用年份下限約為 2000 年後的 HTML 格式申報；1996 年前的 SGML / 純文字格式無法處理

---

## Contributing

歡迎 PR 與 Issue。若遇到解析失敗的申報格式，請附上 CIK 與 Accession Number 開 Issue 回報。

---

## License

MIT © [LLMSystems](https://github.com/LLMSystems)
