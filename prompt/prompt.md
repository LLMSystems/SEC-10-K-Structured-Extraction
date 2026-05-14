# AI 協作開發說明

**使用工具 : Claude Code**

## 人與 AI 的協作模式

本次前後端開發採用「人工主導、AI 協作」 的方式進行開發。由我負責解析方案選擇，驗證優化機制，以及主要核心解析開發，AI 協助需求理解、文檔開發，解析器骨架搭建，並根據解析錯誤進行 pattern 優化。整體流程並不是一次性將任務交給 AI 完成，而是透過反覆討論、修正與確認，逐步把抽象想法轉換為具體規格與可執行成果。

主要協作流程如下
1. 根據[需求題目](../docs/面試題目.md) 先請 AI 將題目原始敘述重新拆解，明確整理出交付物，包括輸入、輸出規格，解析應該要的狀態(`extracted`、`incorporated_by_reference`、`not_applicable`、`reserved`)、轉換成可以落地的[問題定義與設計項目文檔](../docs/題目說明（重寫）.md)

## 實際協作紀錄

本專案的協作過程可分為SEC-10-K理解、解析方案決定、協作開發、優化循環與測試五個階段，並分別反映在既有文件中。
 
首先在SEC-10-K理解階段，我先把請 AI 協助整理 [SEC-10-K 財報相關資訊](../docs/SEC-10-K-財報結構整理.md)，包含存放位置、10-K 的標準結構、每個 Part 組成、以及重要資訊，例如 2023 年後新增 Item 1、2021 年前沒有 Item 6 等等

接著在解析方案決定階段，我與 AI 討論該採用哪種方式，例如純解析、llm 混合等等。由於時間有限，且我看了幾份財報後，認為 10-K 格式具備足夠的結構性，且如果 LLM 處理整份 10-K，動輒 10 萬 token 以上消耗，且題目要求為結構性輸出，不單單是解析檔案，且規則式解析出錯時原因明確，因此只要有良好的優化循環是可以做的

在協作開發階段，我會請 AI 使用 skill 依規格產出第一版後端路由與服務流程、前端頁面與元件骨架。實作上主要困難在可追蹤的 Job Queue、可輪詢的狀態 API 與 SQLite 寫入流程；前端也是一樣，請 AI 使用 skill 依規格產出第一版然後我自行測試

為了有良好的優化循環，我另外開發了一個標註 [SEC-10-K 網站](https://github.com/LLMSystems/SEC-10-K-Annotation-Tool)，可以輕鬆查看解析結果，並根據錯誤來進行修改迭代


## Prompt 紀錄

以下整理的是本專案中實際反覆使用、且對結果有明顯影響的代表性 prompt 類型。

### 1. 題目拆解與規格澄清

專案一開始，我先把原始面試題目交給 AI 協助重寫與拆解，目的是把模糊描述轉成明確交付規格，包含輸入格式、輸出 JSON 結構、各 Item status 定義與例外情境。

代表性 prompt 包含：

- 依照原始題目內容，重寫成工程可落地的問題定義文件，明確列出輸入、輸出、錯誤情況與非功能需求。
- 幫我整理 10-K parser 應該輸出的 status 類型，並釐清 `extracted`、`incorporated_by_reference`、`not_applicable`、`reserved` 的判斷差異。
- 將題目需求轉換成實作 checklist，區分哪些是必要功能、哪些是延伸功能。

這一階段的結果主要體現在 [../docs/題目說明（重寫）.md](../docs/題目說明（重寫）.md)。

### 2. SEC 10-K 背景知識整理

在進入實作前，我先請 AI 協助整理 SEC 10-K 結構、Part 與 Item 的標準編排方式，以及不同年份規則差異，讓後續 parser 設計有明確依據。

代表性 prompt 包含：

- 整理 SEC Form 10-K 的標準章節結構，列出 Part I 到 Part IV 及對應 Item。
- 說明哪些 Item 會因年份不同而增減，例如 2021 年後 Item 6 的變化、2023 年新增的 Item 1C。
- 說明 Part III 為何常出現 incorporated by reference，實務上在年報中通常會怎麼寫。

這一階段的結果主要體現在 [../docs/SEC-10-K-財報結構整理.md](../docs/SEC-10-K-財報結構整理.md)。

### 3. 解析策略比較與方案決定

在理解題目與資料特性後，我有請 AI 協助分析不同技術路線的優缺點，例如純 regex、規則式加後處理、LLM 混合解析等，但最終方案仍由我根據成本、可控性與可驗證性決定。

代表性 prompt 包含：

- 比較規則式 parser 與 LLM parser 在 10-K 結構化抽取任務中的優缺點。
- 如果要求輸出穩定、成本低、可做 regression test，應該優先選哪種架構，原因是什麼。
- 幫我設計一個可擴充的 parser 架構，先以 regex 為主，未來保留 hybrid 或 LLM fallback 的空間。

這一階段幫助我確認本題最適合採用「規則式為主、可持續優化」的方向，而不是直接將整份年報交給 LLM 處理。

### 4. 解析器骨架與流程實作

當方向確定後，我請 AI 協助產出第一版程式骨架，包含資料模型、pipeline、parser 介面、postprocessor 等，先把完整流程串起來，再由我持續調整細節。

代表性 prompt 包含：

- 根據既定 JSON schema，產出 Python 資料模型與 parser 輸出結構。
- 幫我建立 10-K parsing pipeline 骨架，拆成 fetch、preprocess、parse、postprocess 幾個步驟。
- 為 regex parser 設計基本 Item 偵測方式，先處理標準的 `Item 1` 到 `Item 16` 標題。
- 幫我把 status 判斷獨立成 postprocessor，避免 parser 與判斷邏輯耦合。

這些 prompt 主要對應到 `src/pipeline.py`、`src/parsers/regex_parser.py`、`src/postprocessor.py` 與 `src/models.py` 的初版骨架。

### 5. 依錯誤案例反覆優化 pattern

專案進入中後期後，AI 的主要角色從「生出初版」轉為「根據錯誤案例協助修補 pattern」。我會先用自行標註資料與標註工具確認錯誤型態，再把具體失敗案例交給 AI 協助分析與提出修正方向。

代表性 prompt 包含：

- 這份 filing 的 Item 16 有抓到起始點 ，但是沒有抓到停止點，導致 Item 16 字數包含到附錄，幫我新增 financial statement 錨點
- 某些公司把標題寫成 `ITEM 7用表格來呈現，請協助將前處理，table內如果有 item則應該要換成文字

這一類 prompt 通常不是一次完成，而是會搭配人工比對、局部修改與重新評測多輪進行。


## AI 在本專案中的具體貢獻

AI 在本專案中的貢獻主要集中在以下幾個面向：

- 協助我補足 SEC-10-K 相關知識與需求規格釐清
    - [需求重寫文檔](../docs/題目說明（重寫）.md)
    - [SEC-10-K-財報結構整理](../docs/SEC-10-K-財報結構整理.md)

- 解析器骨幹開發，包含
    - `./src/parsers/regex_parser.py` : 基本規則解析器
    - `./src/pipeline.py` : 完整解析流程
    - `./src/postprocessor.py` : 後處理
    - `./src/patterns.py`

## 我如何驗證 AI 產出&調整

為了避免直接採信 AI 產出的內容，我在本專案中採用了幾種驗證方式。

- 解析器 : 建立跨維度資料集，涵蓋年分、公司規模大小。
- [SEC-10-K 網站](https://github.com/LLMSystems/SEC-10-K-Annotation-Tool) : 當 AI 針對某個 pattern 修復後，我會使用標註網站快速確認是否修復範圍，且通過自行標註資料集跑 regression test
- 對代碼進行人工複查，特別是 regex pattern。
- 針對 AI 產出的規範內容進行人工修訂，避免語意模糊、過度延伸或與實作脫節或是與我想法有出入，通常會充分與 AI 溝通。