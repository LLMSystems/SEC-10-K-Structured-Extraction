export type Metric = {
  label: string
  value: string
  detail: string
  tone: 'visual' | 'deterministic' | 'neutral' | 'cost'
}

export type NavigationRow = {
  filing: string
  exact: number
  exactTotal: number
  within: number
  withinTotal: number
}

export type PrecisionRow = {
  filing: string
  gate: string
  head: string
  tail: string
  gateRate: number
  headRate: number
  tailRate: number
}

export type ModelPrecisionRow = {
  rank: number
  model: string
  head: string
  tail: string
  headRate: number
  tailRate: number
}

export type ModelDetectionRow = {
  rank: number
  model: string
  averageRate: number
  headRate: number
  tailRate: number
  note?: string
}

export type DetectionOperator = {
  label: string
  shortLabel: string
  description: string
  lines: string
  half: string
  linesRate: number
  halfRate: number
}

export type BenchModel = {
  rank: number
  model: string
  family: 'Google' | 'Qwen' | 'Moonshot' | 'OpenAI' | 'Anthropic' | 'Other'
  sourceType: 'open' | 'closed'
  rating: number
  price: number
  contextK: number
  speed?: number
  latencySeconds?: number
  selected: boolean
  navModel?: boolean
}

export type FlowNode = {
  title: string
  badge: string
  description: string
  phase: 'neutral' | 'deterministic' | 'visual'
  icon: 'file' | 'shield' | 'navigation' | 'search' | 'eye' | 'result'
  points: string[]
  branch?: string
}

export const heroMetrics: Metric[] = [
  {
    label: '多模態模型頁面導航一頁內命中',
    value: '96.8%',
    detail: '60/62 個 item 落在正確頁或相差一頁內',
    tone: 'visual',
  },
  {
    label: '多模態模型視覺驗證平均精確度',
    value: '98.1%',
    detail: '開頭與尾段合計 102/104 通過',
    tone: 'visual',
  },
  {
    label: '多模態模型錯誤偵測率',
    value: '83.1%–95.3%',
    detail: '端到端錯誤偵測基線在 4 類錯誤上測得',
    tone: 'visual',
  },
  {
    label: '確定性驗證誤殺率',
    value: '0/34',
    detail: '34 份正確 Ground Truth 無誤殺',
    tone: 'deterministic',
  },
  {
    label: '確定性規則核心偵測',
    value: '100%',
    detail: '3,760 個注入樣本中，規則 1、2、4 與規則 3 核心錯誤皆可抓出',
    tone: 'deterministic',
  },
  {
    label: '最佳模型單份成本',
    value: 'NT$0.7',
    detail: 'Gemini 3 Flash Preview，約 US$0.022 / filing',
    tone: 'cost',
  },
]

export const flowNodes: FlowNode[] = [
  {
    title: '解析器輸出',
    badge: '輸入',
    description: '解析器產出的章節結構，是兩個驗證器共同檢查的對象。',
    phase: 'neutral',
    icon: 'file',
    points: ['章節標題', '起始頁與結束頁', '抽取出的內容文字', '對應的 10-K 文件'],
  },
  {
    title: '確定性檢查',
    badge: '結構檢查',
    description: '先檢查任何正確解析都必須滿足的必要條件。',
    phase: 'deterministic',
    icon: 'shield',
    points: ['頁碼區間是否合法', '章節順序是否倒退', '重要章節是否消失', '全文是否異常過短'],
  },
  {
    title: '頁面導航',
    badge: '找到頁面',
    description: '不依賴解析器內容，從文件目錄與頁碼線索推回章節應該出現的 PDF 圖像頁。',
    phase: 'visual',
    icon: 'navigation',
    points: [
      '先找出文件中的目錄頁',
      '從目錄讀出各章節對應的印刷頁碼',
      '把印刷頁碼換算成 PDF 實際圖像頁',
      '在候選頁附近尋找正文中的章節標題',
      '產生後續視覺檢查要看的可信候選頁',
    ],
  },
  {
    title: '可信頁面確認',
    badge: '確認',
    description: '確認候選頁是否真的出現對應章節標題；若找不到可信頁面，就保持中立。',
    phase: 'visual',
    icon: 'search',
    points: ['在候選頁前後小範圍重新找標題', '排除目錄頁上的同名章節', '找不到可信頁面時輸出無法判定'],
    branch: '無可信頁面 -> 中立 / 無法判定',
  },
  {
    title: '頁面證據比對',
    badge: '多模態檢查',
    description: '讓多模態模型讀頁面，檢查解析器抽出的開頭與尾段是否與頁面一致。',
    phase: 'visual',
    icon: 'eye',
    points: ['檢查章節開頭頁', '檢查最後 1 到 2 頁', '確認尾段停在下一個章節前', '比對解析器內容與頁面證據'],
  },
  {
    title: '判定結果',
    badge: '輸出',
    description: '根據結構規則與頁面證據，輸出通過、失敗或無法判定。',
    phase: 'neutral',
    icon: 'result',
    points: ['通過', '失敗', '中立 / 無法判定'],
  },
]

export const validatorSummaries = [
  {
    title: 'SEC 10-K 視覺驗證器',
    eyebrow: '頁面證據',
    description: '用頁面證據檢查解析器抽出的章節邊界是否可信。',
    proofPoints: [
      '先自動找到章節所在的可信頁面',
      '再檢查解析器抽出的章節開頭與尾段',
      '如果前後邊界都和頁面一致，代表這段抽取基本上不太可能錯到別的章節',
    ],
    metrics: ['頁面導航 ±1 頁內 96.8%', '開頭精確度 98.3%', '尾段精確度 97.7%'],
    catches: ['開頭抓錯', '尾段截斷', '尾端越界', '頁面導航偏移'],
  },
  {
    title: 'SEC 10-K 確定性驗證器',
    eyebrow: '必要條件',
    description: '用必要條件檢查解析器結果是否存在結構性錯誤。',
    proofPoints: [
      '不依賴語言模型，也不需要重新讀頁面',
      '直接檢查正確解析必然滿足的結構條件',
      '一旦違反頁碼、順序、重要章節或全文長度條件，就能直接定位錯誤',
    ],
    metrics: ['34 份正確資料誤殺 0', '3,760 個錯誤樣本', '核心錯誤偵測 100%'],
    catches: ['非法區間', '順序倒退', '重要 item 消失', '內容異常過短'],
  },
]

export const navigationRows: NavigationRow[] = [
  { filing: 'GDC_2023', exact: 15, exactTotal: 16, within: 16, withinTotal: 16 },
  { filing: 'NFLX_2025', exact: 9, exactTotal: 11, within: 11, withinTotal: 11 },
  { filing: 'RELL_2025', exact: 9, exactTotal: 11, within: 10, withinTotal: 11 },
  { filing: 'TSLA_2023', exact: 11, exactTotal: 12, within: 11, withinTotal: 12 },
  { filing: 'WMT_2026', exact: 12, exactTotal: 12, within: 12, withinTotal: 12 },
]

export const precisionRows: PrecisionRow[] = [
  {
    filing: 'GDC_2023',
    gate: '16/16',
    head: '16/16',
    tail: '10/10',
    gateRate: 100,
    headRate: 100,
    tailRate: 100,
  },
  {
    filing: 'NFLX_2025',
    gate: '11/11',
    head: '11/11',
    tail: '10/10',
    gateRate: 100,
    headRate: 100,
    tailRate: 100,
  },
  {
    filing: 'RELL_2025',
    gate: '10/11',
    head: '9/10',
    tail: '5/6',
    gateRate: 90.9,
    headRate: 90,
    tailRate: 83.3,
  },
  {
    filing: 'TSLA_2023',
    gate: '11/12',
    head: '11/11',
    tail: '10/10',
    gateRate: 91.7,
    headRate: 100,
    tailRate: 100,
  },
  {
    filing: 'WMT_2026',
    gate: '12/12',
    head: '12/12',
    tail: '8/8',
    gateRate: 100,
    headRate: 100,
    tailRate: 100,
  },
]

export const detectionOperators: DetectionOperator[] = [
  {
    label: '開頭截斷',
    shortLabel: '開頭截斷',
    description: '解析器少抓了章節最前面的內容，導致章節開頭不完整。',
    lines: '53/59',
    half: '49/59',
    linesRate: 89.8,
    halfRate: 83.1,
  },
  {
    label: '開頭越界',
    shortLabel: '開頭越界',
    description: '解析器把章節開始位置往前抓，混入了上一個章節或前置內容。',
    lines: '55/59',
    half: '55/59',
    linesRate: 93.2,
    halfRate: 93.2,
  },
  {
    label: '尾段截斷',
    shortLabel: '尾段截斷',
    description: '解析器提早停止，少抓了章節結尾處仍屬於本章節的內容。',
    lines: '38/43',
    half: '42/43',
    linesRate: 88.4,
    halfRate: 97.7,
  },
  {
    label: '尾段越界',
    shortLabel: '尾段越界',
    description: '解析器把章節結尾往後抓，越過下一個章節標題並混入後續內容。',
    lines: '41/43',
    half: '38/43',
    linesRate: 95.3,
    halfRate: 88.4,
  },
]

export const modelPrecisionRows: ModelPrecisionRow[] = [
  {
    rank: 1,
    model: 'google/gemini-3-flash-preview',
    head: '59/60',
    tail: '43/44',
    headRate: 98.3,
    tailRate: 97.7,
  },
  {
    rank: 2,
    model: 'qwen/qwen3.6-plus',
    head: '60/60',
    tail: '40/44',
    headRate: 100,
    tailRate: 90.9,
  },
  {
    rank: 3,
    model: 'moonshotai/kimi-k2.6',
    head: '57/60',
    tail: '39/44',
    headRate: 95,
    tailRate: 88.6,
  },
  {
    rank: 4,
    model: 'google/gemini-2.5-pro',
    head: '52/53',
    tail: '33/39',
    headRate: 98.1,
    tailRate: 84.6,
  },
  {
    rank: 5,
    model: 'google/gemini-3.1-flash-lite',
    head: '60/60',
    tail: '36/44',
    headRate: 100,
    tailRate: 81.8,
  },
  {
    rank: 5,
    model: 'qwen/qwen3.5-9b',
    head: '60/60',
    tail: '36/44',
    headRate: 100,
    tailRate: 81.8,
  },
  {
    rank: 7,
    model: 'qwen/qwen3.6-27b',
    head: '60/60',
    tail: '33/44',
    headRate: 100,
    tailRate: 75,
  },
  {
    rank: 8,
    model: 'qwen/qwen3.5-27b',
    head: '60/60',
    tail: '32/44',
    headRate: 100,
    tailRate: 72.7,
  },
  {
    rank: 8,
    model: 'qwen/qwen3.5-122b-a10b',
    head: '60/60',
    tail: '32/44',
    headRate: 100,
    tailRate: 72.7,
  },
  {
    rank: 10,
    model: 'qwen/qwen3.5-35b-a3b',
    head: '58/59',
    tail: '31/43',
    headRate: 98.3,
    tailRate: 72.1,
  },
  {
    rank: 11,
    model: 'google/gemma-4-31b-it',
    head: '56/60',
    tail: '29/44',
    headRate: 93.3,
    tailRate: 65.9,
  },
  {
    rank: 11,
    model: 'google/gemini-2.5-flash',
    head: '55/57',
    tail: '27/41',
    headRate: 96.5,
    tailRate: 65.9,
  },
  {
    rank: 13,
    model: 'google/gemma-4-26b-a4b-it',
    head: '38/49',
    tail: '12/34',
    headRate: 77.6,
    tailRate: 35.3,
  },
]

export const modelDetectionRows: ModelDetectionRow[] = [
  {
    rank: 1,
    model: 'google/gemma-4-26b-a4b-it',
    averageRate: 92.5,
    headRate: 92.1,
    tailRate: 93.8,
    note: '可評估分母較小，需搭配精確度一起看',
  },
  {
    rank: 2,
    model: 'google/gemini-3-flash-preview',
    averageRate: 90.9,
    headRate: 89.8,
    tailRate: 92.4,
  },
  {
    rank: 3,
    model: 'qwen/qwen3.5-122b-a10b',
    averageRate: 90.8,
    headRate: 90.4,
    tailRate: 91.4,
  },
  {
    rank: 4,
    model: 'google/gemini-2.5-pro',
    averageRate: 90.6,
    headRate: 89.9,
    tailRate: 91.7,
  },
  {
    rank: 5,
    model: 'google/gemini-3.1-flash-lite',
    averageRate: 90.4,
    headRate: 90,
    tailRate: 91,
  },
  {
    rank: 6,
    model: 'qwen/qwen3.6-27b',
    averageRate: 90.3,
    headRate: 90,
    tailRate: 90.9,
  },
  {
    rank: 7,
    model: 'qwen/qwen3.5-35b-a3b',
    averageRate: 89.9,
    headRate: 90.5,
    tailRate: 88.7,
  },
  {
    rank: 8,
    model: 'google/gemini-2.5-flash',
    averageRate: 89.6,
    headRate: 89.5,
    tailRate: 89.8,
  },
  {
    rank: 9,
    model: 'google/gemma-4-31b-it',
    averageRate: 89.4,
    headRate: 90.2,
    tailRate: 87.9,
  },
  {
    rank: 9,
    model: 'qwen/qwen3.5-27b',
    averageRate: 89.4,
    headRate: 90,
    tailRate: 88.3,
  },
  {
    rank: 11,
    model: 'qwen/qwen3.6-plus',
    averageRate: 89.3,
    headRate: 90,
    tailRate: 88.1,
  },
  {
    rank: 12,
    model: 'qwen/qwen3.5-9b',
    averageRate: 88.5,
    headRate: 90,
    tailRate: 86.1,
  },
  {
    rank: 13,
    model: 'moonshotai/kimi-k2.6',
    averageRate: 87,
    headRate: 85.5,
    tailRate: 89.1,
  },
]

export const benchModels: BenchModel[] = [
  {
    rank: 4,
    model: 'Gemini 3.5 Flash',
    family: 'Google',
    sourceType: 'closed',
    rating: 46.5,
    price: 3,
    contextK: 1000,
    speed: 81,
    latencySeconds: 7.4,
    selected: false,
  },
  {
    rank: 7,
    model: 'Kimi K2.6',
    family: 'Moonshot',
    sourceType: 'closed',
    rating: 43.4,
    price: 1.56,
    contextK: 262,
    speed: 55,
    latencySeconds: 56.2,
    selected: true,
  },
  {
    rank: 10,
    model: 'Qwen3.6 Plus',
    family: 'Qwen',
    sourceType: 'closed',
    rating: 41.2,
    price: 1,
    contextK: 1000,
    speed: 78,
    latencySeconds: 78.6,
    selected: true,
  },
  {
    rank: 17,
    model: 'Gemini 3 Flash',
    family: 'Google',
    sourceType: 'closed',
    rating: 38.3,
    price: 1,
    contextK: 1000,
    speed: 522,
    latencySeconds: 2.1,
    selected: true,
    navModel: true,
  },
  {
    rank: 19,
    model: 'Qwen3.5-122B-A10B',
    family: 'Qwen',
    sourceType: 'open',
    rating: 37.1,
    price: 0.96,
    contextK: 262,
    speed: 132,
    latencySeconds: 33.6,
    selected: true,
  },
  {
    rank: 21,
    model: 'Qwen3.6-27B',
    family: 'Qwen',
    sourceType: 'open',
    rating: 35.9,
    price: 1.2,
    contextK: 262,
    speed: 150,
    latencySeconds: 15.9,
    selected: true,
  },
  {
    rank: 23,
    model: 'Qwen3.5-27B',
    family: 'Qwen',
    sourceType: 'open',
    rating: 35.5,
    price: 0.72,
    contextK: 262,
    speed: 118,
    latencySeconds: 35.6,
    selected: true,
  },
  {
    rank: 29,
    model: 'Gemini 3.1 Flash-Lite',
    family: 'Google',
    sourceType: 'closed',
    rating: 33.5,
    price: 0.5,
    contextK: 1000,
    selected: true,
  },
  {
    rank: 31,
    model: 'Gemini 2.5 Pro',
    family: 'Google',
    sourceType: 'closed',
    rating: 32.9,
    price: 3,
    contextK: 1000,
    speed: 219,
    latencySeconds: 10.8,
    selected: true,
  },
  {
    rank: 32,
    model: 'Gemma 4 31B',
    family: 'Google',
    sourceType: 'open',
    rating: 32,
    price: 0.19,
    contextK: 262,
    speed: 186,
    latencySeconds: 0.86,
    selected: true,
  },
  {
    rank: 34,
    model: 'Qwen3.5-35B-A3B',
    family: 'Qwen',
    sourceType: 'open',
    rating: 31.8,
    price: 0.6,
    contextK: 262,
    speed: 251,
    latencySeconds: 23.9,
    selected: true,
  },
  {
    rank: 42,
    model: 'Gemini 2.5 Flash',
    family: 'Google',
    sourceType: 'closed',
    rating: 29.1,
    price: 0.74,
    contextK: 1000,
    speed: 151,
    latencySeconds: 5.1,
    selected: true,
  },
  {
    rank: 48,
    model: 'Gemma 4 26B-A4B',
    family: 'Google',
    sourceType: 'open',
    rating: 26.2,
    price: 0.18,
    contextK: 262,
    speed: 114,
    latencySeconds: 2.6,
    selected: true,
  },
  {
    rank: 58,
    model: 'Qwen3.5-9B',
    family: 'Qwen',
    sourceType: 'open',
    rating: 24.1,
    price: 0.35,
    contextK: 262,
    selected: true,
  },
]

export const deterministicRules = [
  {
    rule: '規則 1',
    title: '區間合法性',
    description: '檢查頁碼區間是否反向、零長度或落在非法範圍。',
    falsePositive: '0/34',
    recall: '100%',
  },
  {
    rule: '規則 2',
    title: '單調且不可重疊',
    description: '檢查後續 item 是否倒退或與前一段重疊。',
    falsePositive: '0/34',
    recall: '100%',
  },
  {
    rule: '規則 3',
    title: '重要 item 不可消失',
    description: '檢查 SEC 10-K 的核心 item 是否被整段漏抓。',
    falsePositive: '0/34',
    recall: '100%',
  },
  {
    rule: '規則 4',
    title: '全文內容底線',
    description: '檢查是否抽到空文件、錯文件或嚴重截斷內容。',
    falsePositive: '0/34',
    recall: '100%',
  },
]

export const datasetCards = [
  {
    label: '視覺驗證資料',
    value: '5 份 filing',
    detail: 'PDF、頁面 PNG、內容 Ground Truth、頁碼對帳標註',
  },
  {
    label: '導航評估 item',
    value: '62',
    detail: '用來衡量頁面導航 exact 與 ±1 頁命中率',
  },
  {
    label: '確定性正確資料',
    value: '34 份',
    detail: '涵蓋 2016-2026、12 家公司與多種 filing 型態',
  },
  {
    label: '錯誤注入樣本',
    value: '3,760',
    detail: '由 Ground Truth 系統性產生，用來測偵測率',
  },
]
