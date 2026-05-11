# SEC 10-K 財報結構說明文件

> 本文件透過 SEC EDGAR 官方 API 調研整理，供 10-K 結構化抽取 Pipeline 開發參考。

---

## 一、什麼是 10-K？

**Form 10-K** 是美國上市公司每年須向 SEC（美國證券交易委員會）提交的**年度報告**，是最完整的公司披露文件，涵蓋業務說明、財務狀況、風險因素、管理層討論等。

- **誰要提交**：在美國上市的公司（包含在美上市的外國公司，使用 20-F）
- **提交頻率**：每年一次，於會計年度結束後 60~90 天內提交
- **存放位置**：全部存放在 SEC 的 EDGAR 系統，完全公開免費

---

## 二、10-K 的標準結構（Part & Item）

10-K 分為四個 Part，共 16 個 Item。以下是完整對照表：

### Part I：公司基本資訊

| Item | 標題 | 說明 |
|---|---|---|
| Item 1 | Business | 公司主要業務、產品、市場、競爭者 |
| Item 1A | Risk Factors | 可能影響公司的風險因素（通常篇幅極長） |
| Item 1B | Unresolved Staff Comments | SEC 審查中的未解決評論（多數為空） |
| Item 1C | Cybersecurity | **2023 年後新增**，網路安全風險管理策略 |
| Item 2 | Properties | 公司持有或租用的重要房地產 |
| Item 3 | Legal Proceedings | 重大法律訴訟 |
| Item 4 | Mine Safety Disclosures | 採礦業專用（非採礦公司通常標記 Not Applicable） |

### Part II：財務資訊

| Item | 標題 | 說明 |
|---|---|---|
| Item 5 | Market for Registrant's Common Equity | 股票市場資訊、股利政策、股票回購 |
| Item 6 | Reserved | **注意**：2021 年前為「Selected Financial Data」，現已改為 Reserved |
| Item 7 | MD&A | 管理層討論與分析，解釋財務數字背後的原因（通常最長） |
| Item 7A | Quantitative and Qualitative Disclosures About Market Risk | 市場風險的量化與質化說明 |
| Item 8 | Financial Statements and Supplementary Data | 完整財務報表（資產負債表、損益表、現金流量表） |
| Item 9 | Changes in and Disagreements With Accountants | 會計師更換與分歧 |
| Item 9A | Controls and Procedures | 內部控制評估（Sarbanes-Oxley 法規要求） |
| Item 9B | Other Information | 其他需要即時揭露的資訊 |
| Item 9C | Disclosure Regarding Foreign Jurisdictions | 外國司法管轄區揭露（近年新增） |

### Part III：公司治理

| Item | 標題 | 說明 |
|---|---|---|
| Item 10 | Directors, Executive Officers and Corporate Governance | 董事、高管資訊、治理 |
| Item 11 | Executive Compensation | 高管薪酬 |
| Item 12 | Security Ownership of Certain Beneficial Owners | 主要股東持股比例 |
| Item 13 | Certain Relationships and Related Transactions | 關係人交易 |
| Item 14 | Principal Accountant Fees and Services | 會計師費用 |

> ⚠️ **重要**：Part III 是 10-K 中最常見「incorporated by reference」的部分。許多公司在年報中不填寫這部分的實際內容，而是引用另一份 **Proxy Statement（DEF 14A）** 中的對應章節。這在解析時需要特別處理。

### Part IV：附件

| Item | 標題 | 說明 |
|---|---|---|
| Item 15 | Exhibits, Financial Statement Schedules | 所有附件清單（合約、章程等）及財務附表 |
| Item 16 | Form 10-K Summary | 選填的摘要頁（多數公司省略） |

---

## 三、EDGAR 系統與 API

### 3.1 核心概念

| 概念 | 說明 | 範例 |
|---|---|---|
| **CIK**（Central Index Key） | 每家公司在 EDGAR 的唯一識別碼，10 位數字，不足補零 | Apple = `0000320193` |
| **Accession Number** | 每份申報的唯一編號，格式為 `XXXXXXXXXX-YY-NNNNNN` | `0000320193-23-000106` |
| **Filing Index** | 每份申報的文件清單頁面 | 列出所有附屬文件及主文件 |

**Accession Number 解讀**：
```
0000320193  -  23  -  000106
     │          │        │
  提交者 CIK   年份   當年第 N 份申報
```

### 3.2 官方 API Endpoints

#### 取得公司申報歷史
```
GET https://data.sec.gov/submissions/CIK{10位補零}.json
```
回傳公司基本資料與近期申報清單。

**回傳結構（簡化）**：
```json
{
  "cik": "320193",
  "name": "Apple Inc.",
  "sic": "3674",
  "sicDescription": "Semiconductors and Related Devices",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-23-000106", "0000320193-22-000108", ...],
      "filingDate":       ["2023-11-03", "2022-10-28", ...],
      "form":             ["10-K", "10-K", ...],
      "primaryDocument":  ["aapl20230930.htm", "aapl20220924.htm", ...],
      "isXBRL":           [1, 1, ...]
    }
  }
}
```

#### 取得申報文件清單（Filing Index）
```
GET https://www.sec.gov/Archives/edgar/data/{CIK}/{accession去破折號}/
```
或以 JSON 格式：
```
GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={CIK}&type=10-K&dateb=&owner=include&count=10&search_text=&output=atom
```

Filing Index 頁面列出該份申報的所有文件，例如：
```
aapl20230930.htm         <- 主文件（10-K 本體）
R1.htm, R2.htm...        <- XBRL 互動資料檔
exhibit31-1.htm          <- 附件（CEO 認證書）
exhibit32-1.htm          <- 附件（SOX 認證書）
```

#### 下載主文件
```
GET https://www.sec.gov/Archives/edgar/data/{CIK}/{accession去破折號}/{filename}
```
範例：
```
https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl20230930.htm
```

#### XBRL 財報數字（交叉驗證用）
```
GET https://data.sec.gov/api/xbrl/companyfacts/CIK{10位補零}.json
```
回傳公司所有 XBRL 標記的財務數字，可用來驗證 Item 8 抽取結果是否合理。

### 3.3 API 使用規範

```http
User-Agent: MyApp contact@example.com
```

- **必須**帶 `User-Agent` header，否則會被封鎖
- 速率限制：**10 req/sec**
- 無需 API Key，完全免費

---

## 四、10-K 的實際檔案格式

### 4.1 現代格式（約 2010 年後）：HTML / iXBRL

現代 10-K 主要為 **HTML 格式**，部分較新的申報使用 **Inline XBRL（iXBRL）**，即在 HTML 中嵌入 XBRL 標記。

```html
<!-- 典型的 Item 標題寫法 -->
<p><b>Item 1. Business</b></p>
<p><B>ITEM 1A. RISK FACTORS</B></p>
<p style="font-weight:bold;">Item&#160;7.&#160;Management&#8217;s Discussion</p>
```

**常見的標題變體（都是同一個 Item）**：
```
Item 1.   Business
ITEM 1.   BUSINESS
Item 1:   Business
Item 1 -  Business
Item&#160;1.&#160;&#160;Business   ← 非斷行空格（&nbsp;）
```

### 4.2 舊格式（約 2000 年以前）：純文字 / SGML

早期申報為純文字格式，以特定標記分隔文件：
```
<DOCUMENT>
<TYPE>10-K
<SEQUENCE>1
<FILENAME>0000950134-96-004654.txt
<TEXT>

ITEM 1. BUSINESS

The Company was incorporated...

ITEM 1A. RISK FACTORS

...
</TEXT>
</DOCUMENT>
```

這類格式沒有 HTML 結構，必須用純文字正則表達式解析。

### 4.3 文件大小

| 公司類型 | 典型大小 |
|---|---|
| 大型科技公司（Apple、Microsoft） | 5～20 MB |
| 中型公司 | 500 KB～3 MB |
| 小型公司 | 100～500 KB |
| 舊格式純文字申報 | 數十 KB |

---

## 五、解析的主要挑戰（Edge Cases）

### 5.1 Incorporated by Reference（引用）

**最常見於 Part III**。公司在 10-K 中寫：
```
The information required by this item is incorporated by reference
from our definitive proxy statement filed with the SEC.
```

此時該 Item 無實際文字內容，必須：
1. 正確識別為 `incorporated_by_reference` 狀態
2. 若需要完整內容，需另外抓取對應的 **DEF 14A（Proxy Statement）** 文件

### 5.2 Not Applicable / Reserved

```
Item 4.   Mine Safety Disclosures
Not applicable.
```

```
Item 6.   Reserved
```

需正確識別並標記 status，而非當作空白或解析失敗。

### 5.3 Item 邊界偵測困難

- 某些公司用表格（`<table>`）排版，標題藏在 `<td>` 中
- 頁首頁尾（header/footer）中常出現 Item 字樣，容易誤判為 Item 開始
- Item 7A 很短（有時只有一行），Item 7 和 Item 8 之間的邊界難以判斷
- 目錄（Table of Contents）中有所有 Item 名稱，必須排除

### 5.4 Item 編號變化

SEC 規則修訂導致 Item 結構隨年份變化：

| 變化 | 時間 |
|---|---|
| Item 1C（Cybersecurity）新增 | 2023 年報起 |
| Item 6 從「Selected Financial Data」改為「Reserved」 | 2021 年報起 |
| Item 9C（Foreign Jurisdictions）新增 | 2021 年報起 |

### 5.5 子標題混淆

Item 8（Financial Statements）內部包含大量小標題，例如：
```
CONSOLIDATED BALANCE SHEETS
CONSOLIDATED STATEMENTS OF OPERATIONS
Notes to Consolidated Financial Statements
Note 1 - Summary of Significant Accounting Policies
```

這些不是新的 Item，但格式上很像 Item 標題，容易誤觸解析規則。

---

## 六、建議的解析策略

### 規則式（Rule-based）
- 速度快、成本低
- 適合結構較標準的現代 HTML 申報
- 核心：正則表達式偵測 Item 標題邊界

**常用正則表達式**：
```python
# 偵測 Item 標題（含常見變體）
ITEM_PATTERN = re.compile(
    r'(?:^|\n|>)\s*'
    r'(item\s+(\d+[abc]?)[\.\-\:\s]+([^\n<]{5,80}))',
    re.IGNORECASE
)

# 偵測 incorporated by reference
IBR_PATTERN = re.compile(
    r'incorporated\s+by\s+reference',
    re.IGNORECASE
)

# 偵測 not applicable
NA_PATTERN = re.compile(
    r'not\s+applicable|none\s*\.',
    re.IGNORECASE
)
```

### LLM 輔助
- 適合處理邊界模糊、格式特殊的案例
- 成本較高，需控制使用範圍
- 建議只在規則式失敗時 fallback 到 LLM

### 混合策略（建議）

```
1. 先嘗試規則式解析
2. 計算信心分數（Item 數量是否合理、是否有明顯遺漏）
3. 信心不足時，針對問題片段呼叫 LLM 輔助判斷
4. 記錄每份申報使用的策略，用於後續分析
```

---

## 七、實際申報範例

以下是幾個具有代表性的 10-K 申報，適合作為開發與測試基準：

| 公司 | 年份 | 特點 | 參考連結 |
|---|---|---|---|
| Apple Inc. | 2023 | 現代 iXBRL，含 Item 1C | [EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K) |
| Netflix | 2015 | 純 HTML，結構清晰 | [直連](https://www.sec.gov/Archives/edgar/data/1065280/000106528016000047/nflx201510k.htm) |
| General Electric | 2000 年前後 | 舊格式，SGML 純文字 | EDGAR 搜尋 CIK 40987 |
| 小型礦業公司 | 任意 | 含 Item 4 Not Applicable | EDGAR 搜尋 |
| 任意大型公司 | 近年 | Part III 全為 incorporated by reference | 多數財星 500 強 |

---

## 八、XBRL 交叉驗證

`https://data.sec.gov/api/xbrl/companyfacts/CIK{10位補零}.json` 回傳所有 XBRL 標記的財務事實，例如：

```json
{
  "facts": {
    "us-gaap": {
      "Revenues": {
        "label": "Revenues",
        "description": "...",
        "units": {
          "USD": [
            {
              "end": "2023-09-30",
              "val": 383285000000,
              "accn": "0000320193-23-000106",
              "form": "10-K"
            }
          ]
        }
      }
    }
  }
}
```

可以用來：
- 驗證 Item 8 抽取的營收、淨利等數字是否合理
- 確認申報對應的會計年度
- 作為 eval set 中財務數字的 ground truth

---

*文件最後更新：基於 SEC EDGAR 官方 API 及公開資料整理*
