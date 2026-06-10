# SEC 10-K Structured Extraction Tool

Parses SEC EDGAR Form 10-K annual reports into standardized JSON.
Automatically identifies the content and status of every Item — zero LLM cost, average latency < 1 second.

[English](README.md) | [中文](README_zh-CN.md)

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status Accuracy](https://img.shields.io/badge/status%20accuracy-100%25-brightgreen)](eval_datasets/results/)

---

## Features

- **Full Item coverage**: Parses all 16 Items across Part I–IV, outputting one of five statuses: `extracted` / `incorporated_by_reference` / `not_applicable` / `reserved` / `missing`
- **Zero LLM cost**: Purely rule-based pipeline — parsing itself takes ~0.03s with no API calls
- **iXBRL support**: Handles the iXBRL format mandatory for large filers since 2019
- **XBRL financial extraction**: Optionally reconstructs Item 8 primary financial statements directly from XBRL (income statement, balance sheet, cash flow statement, etc.)
- **Sync / async API**: Both `Pipeline` and `AsyncPipeline` are available

---

## Quick Start

### Installation

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Usage

#### Synchronous

```python
from src.pipeline import Pipeline
from src.models import FilingInput

pipeline = Pipeline()

# Option 1: CIK + Accession Number
result = pipeline.run(FilingInput(
    cik="0000320193",
    accession_number="0000320193-23-000106",
))

# Option 2: Direct URL
result = pipeline.run(FilingInput(
    url="https://www.sec.gov/Archives/edgar/data/.../filing.htm",
))

# Save results (JSON + Markdown)
result = pipeline.run(input, save_to="output/")
```

#### Async

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

## Output Format

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

A purely rule-based pipeline — SEC regulations mandate Item numbering and ordering, giving the parser reliable anchors regardless of visual formatting variation:

```text
Input (CIK + Accession Number or direct URL)
  ↓ fetch       Retrieve metadata and HTML from the SEC EDGAR API
  ↓ preprocess  HTML → plain text (handle iXBRL, tables, hyphenation)
  ↓ parse       RegexParser locates the start and end position of each Item
  ↓ postprocess Classify the status of each Item
Output (standardized JSON)
```

Architecture overview:

```text
src/
├── pipeline.py              Main pipeline
├── async_pipeline.py        Async version
├── models.py                Data structures
├── patterns.py              Regex pattern definitions
├── postprocessor.py         Item status classification
├── item8_xbrl_facts.py      XBRL financial extraction
├── render_item8_markdown.py XBRL result renderer
├── parsers/
│   ├── regex_parser.py      Main parser
│   └── hybrid.py            Dispatcher (supports future LLM fallback)
└── eval/
    ├── metrics.py           Evaluation script
    └── runner.py            Batch evaluation entry point
```

---

## Evaluation

Benchmarked on **35 filings, 12 companies, 2016–2026**, with manually annotated ground truth:

| Metric | Value |
| --- | --- |
| Status Accuracy | **100.0%** (788 / 788 items) |
| Critical Regressions | **0** |
| Content Length Normal Rate | 99.0% (484 / 489 extracted items) |
| Head/Tail Match Pass Rate | Head 99.8% / Tail 100.0% |
| Average Latency | **0.687s** |
| LLM Cost | **$0** |

In a separate large-scale test across **507 randomly sampled filings**, structural error rate dropped **95.5%** from the initial version.

Ground truth was manually annotated using [SEC-10-K-Annotation-Tool](https://github.com/LLMSystems/SEC-10-K-Annotation-Tool). Full report: [eval_datasets/results/](eval_datasets/results/).

### Running Evaluation

```bash
python -m src.eval.metrics \
    --ground-truth eval_datasets/ground_truth \
    --output eval_datasets/results
```

---

## XBRL Financial Extraction (Item 8)

Reconstructs Item 8 primary financial statements directly from XBRL, outputting a multi-period Markdown report.

```python
from src.item8_xbrl_facts import get_item8_xbrl_facts
from src.render_item8_markdown import write_item8_markdown

payload = get_item8_xbrl_facts("0000019617", "0001628280-26-008131")
write_item8_markdown(payload, "output_item8.md")
```

Parses four XBRL source files (Instance Document, Presentation Linkbase, Label Linkbase, Schema) and classifies results into three blocks: main statements, numeric disclosures, and text disclosures.

---

## Known Limitations

- Supports HTML-format filings from approximately 2000 onward. SGML / plain-text formats from before 1996 have no HTML structure and cannot be processed.

---

## Contributing

PRs and Issues are welcome. If you encounter a filing that fails to parse, please open an Issue with the CIK and Accession Number.

---

## License

MIT © [LLMSystems](https://github.com/LLMSystems)
