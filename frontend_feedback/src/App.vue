<script setup lang="ts">
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  BadgeCheck,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Database,
  Eye,
  FileCheck2,
  FileText,
  Gauge,
  GitBranch,
  Layers3,
  Menu,
  Navigation,
  Pause,
  Play,
  Radar,
  RotateCcw,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  WalletCards,
  X
} from '@lucide/vue'
import type { BarSeriesOption, ScatterSeriesOption } from 'echarts/charts'
import { BarChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption,
} from 'echarts/components'
import type { ComposeOption } from 'echarts/core'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, ref } from 'vue'
import VChart from 'vue-echarts'

import type { FlowNode } from './data/report'
import {
  benchModels,
  detectionOperators,
  deterministicRules,
  flowNodes,
  heroMetrics,
  modelDetectionRows,
  modelPrecisionRows,
  navigationRows,
  validatorSummaries
} from './data/report'

type ECOption = ComposeOption<
  | BarSeriesOption
  | ScatterSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
>

use([BarChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const activeFlowIndex = ref(0)

// Flow animation state
const isPlaying = ref(false)
const completedSteps = ref<number[]>([])
const showBranchChoice = ref(false)
const branchTaken = ref<'deterministic-fail' | 'neutral' | null>(null)
let _playTimer: ReturnType<typeof setInterval> | null = null

const BRANCH_CONFIG: Record<number, {
  title: string
  description: string
  mainLabel: string
  branchLabel: string
  branchType: 'deterministic-fail' | 'neutral'
}> = {
  1: {
    title: '確定性檢查：有兩種結果',
    description: '規則全通過就繼續往下，任何規則被違反就直接輸出失敗；選一條路繼續。',
    mainLabel: '規則全通過，繼續視覺驗證',
    branchLabel: '規則違反，直接失敗',
    branchType: 'deterministic-fail',
  },
  3: {
    title: '可信頁面確認：有兩種結果',
    description: '找到可信頁面就比對邊界；找不到則保持中立，不對解析器下判斷。選一條路繼續。',
    mainLabel: '找到可信頁面，進行比對',
    branchLabel: '找不到頁面，輸出中立',
    branchType: 'neutral',
  },
}

const currentBranchConfig = computed(() => BRANCH_CONFIG[activeFlowIndex.value] ?? null)
const isFlowEnded = computed(
  () => branchTaken.value !== null || completedSteps.value.includes(flowNodes.length - 1),
)

const palette = {
  ink: '#292524',
  stone: '#78716c',
  line: '#d6d3d1',
  visual: '#0f766e',
  visualSoft: '#5eead4',
  deterministic: '#b45309',
  deterministicSoft: '#fbbf24',
  neutral: '#475569',
  rose: '#be123c',
}

const percent = (value: number, total: number) => Number(((value / total) * 100).toFixed(1))

const clippedPercentBar = (value: number, min = 60) => ({
  value: Math.max(value, min),
  actual: value,
})

const percentBarLabel = (params: unknown) => {
  const data = (params as { data?: { actual?: number } | number }).data
  if (typeof data === 'object' && data && typeof data.actual === 'number') return `${data.actual}%`

  const value = (params as { value?: number }).value
  return typeof value === 'number' ? `${value}%` : ''
}

const toneClass = (tone: string) => {
  if (tone === 'visual') return 'border-teal-200 bg-teal-50/80 text-teal-950'
  if (tone === 'deterministic') return 'border-amber-200 bg-amber-50/80 text-amber-950'
  if (tone === 'cost') return 'border-sky-200 bg-sky-50/80 text-sky-950'
  return 'border-stone-200 bg-stone-50 text-stone-950'
}

const flowPhaseClass = (phase: string, active = false) => {
  if (phase === 'deterministic') {
    return active
      ? 'border-amber-600 bg-amber-50 text-amber-950'
      : 'border-amber-200 bg-white text-stone-800'
  }

  if (phase === 'visual') {
    return active ? 'border-teal-700 bg-teal-50 text-teal-950' : 'border-teal-200 bg-white text-stone-800'
  }

  return active ? 'border-stone-900 bg-stone-100 text-stone-950' : 'border-stone-200 bg-white text-stone-800'
}

const nodeCircleClass = (phase: string, index: number) => {
  const isDone = completedSteps.value.includes(index)
  const isActive = activeFlowIndex.value === index
  if (isDone) return 'border-teal-500 bg-teal-600 text-white'
  if (isActive) return 'border-stone-900 bg-stone-950 text-white'
  if (phase === 'deterministic') return 'border-amber-300 bg-amber-50 text-amber-800'
  if (phase === 'visual') return 'border-teal-200 bg-teal-50 text-teal-800'
  return 'border-stone-200 bg-stone-100 text-stone-700'
}

function clearPlayTimer() {
  if (_playTimer !== null) {
    clearInterval(_playTimer)
    _playTimer = null
  }
}

function resetFlow() {
  clearPlayTimer()
  isPlaying.value = false
  activeFlowIndex.value = 0
  completedSteps.value = []
  showBranchChoice.value = false
  branchTaken.value = null
}

function doAdvance() {
  if (BRANCH_CONFIG[activeFlowIndex.value] !== undefined && !showBranchChoice.value) {
    clearPlayTimer()
    isPlaying.value = false
    showBranchChoice.value = true
    return
  }
  const next = activeFlowIndex.value + 1
  if (next >= flowNodes.length) {
    if (!completedSteps.value.includes(activeFlowIndex.value)) {
      completedSteps.value = [...completedSteps.value, activeFlowIndex.value]
    }
    clearPlayTimer()
    isPlaying.value = false
    return
  }
  if (!completedSteps.value.includes(activeFlowIndex.value)) {
    completedSteps.value = [...completedSteps.value, activeFlowIndex.value]
  }
  activeFlowIndex.value = next
}

function startAutoPlay() {
  clearPlayTimer()
  isPlaying.value = true
  _playTimer = setInterval(doAdvance, 1500)
}

function togglePlay() {
  if (isFlowEnded.value) {
    resetFlow()
    return
  }
  if (isPlaying.value) {
    clearPlayTimer()
    isPlaying.value = false
  } else {
    showBranchChoice.value = false
    startAutoPlay()
  }
}

function stepForward() {
  if (isFlowEnded.value || isPlaying.value) return
  showBranchChoice.value = false
  doAdvance()
}

function chooseBranch(choice: 'main' | 'branch') {
  showBranchChoice.value = false
  if (!completedSteps.value.includes(activeFlowIndex.value)) {
    completedSteps.value = [...completedSteps.value, activeFlowIndex.value]
  }
  if (choice === 'branch') {
    const config = BRANCH_CONFIG[activeFlowIndex.value]
    if (config) branchTaken.value = config.branchType
    isPlaying.value = false
  } else {
    activeFlowIndex.value = activeFlowIndex.value + 1
    startAutoPlay()
  }
}

function jumpToStep(index: number) {
  if (isPlaying.value) return
  clearPlayTimer()
  branchTaken.value = null
  showBranchChoice.value = false
  activeFlowIndex.value = index
  completedSteps.value = Array.from({ length: index }, (_, i) => i)
}

// Demo step data — Apple Inc. 2021-09-25 10-K, Item 7
interface DemoStepData {
  type: string
  filing?: string
  item?: string
  pageStart?: number
  pageEnd?: number
  chars?: string
  headPreview?: string
  checks?: { rule: string; label: string; detail: string; pass: boolean }[]
  navSteps?: { n: string; text: string }[]
  navResult?: string
  page?: number
  snippet?: string
  note?: string
  head?: { page: number; text: string }
  tail?: { pages: string; text: string }
  items?: string[]
}

const demoSteps: DemoStepData[] = [
  {
    type: 'input',
    filing: 'Apple Inc. — 2021-09-25 10-K',
    item: 'Item 7 — Management\'s Discussion and Analysis',
    pageStart: 38,
    pageEnd: 72,
    chars: '33,746',
    headPreview:
      'Item 7.    Management\'s Discussion and Analysis of Financial Condition and Results of Operations\nThe following discussion should be read in conjunction with the consolidated financial statements and accompanying notes included in Part II, Item 8 of this Form 10-K…',
  },
  {
    type: 'checks',
    checks: [
      { rule: '規則 1', label: '頁碼區間合法', detail: 'page_start 38 < page_end 72，區間合法', pass: true },
      { rule: '規則 2', label: '章節順序正確', detail: 'Item 1 → 2 → 3 → 5 → 7 → 8 依序遞增', pass: true },
      { rule: '規則 3', label: '重要章節存在', detail: 'Item 1, 7, 8 均已抽取到', pass: true },
      { rule: '規則 4', label: '內容長度合理', detail: '33,746 字元，高於最低門檻', pass: true },
    ],
  },
  {
    type: 'nav',
    navSteps: [
      { n: '1', text: '在 PDF 中找到目錄頁（第 3 頁）' },
      { n: '2', text: '從目錄讀出 Item 7 的印刷頁碼：38' },
      { n: '3', text: '換算偏移量，確認 PDF 圖像頁為第 38 頁' },
      { n: '4', text: '在頁 37–39 附近搜尋正文標題確認' },
    ],
    navResult: '候選頁確認：PDF 第 38 頁',
  },
  {
    type: 'confirm',
    page: 38,
    snippet:
      'PART II\n\nItem 7.    Management\'s Discussion and Analysis of Financial Condition and Results of Operations',
    note: '已排除目錄頁同名項目，確認為正文章節標題 ✓',
  },
  {
    type: 'compare',
    head: {
      page: 38,
      text: 'Item 7.    Management\'s Discussion and Analysis of Financial Condition and Results of Operations\nThe following discussion should be read in conjunction with the consolidated financial statements and accompanying notes…',
    },
    tail: {
      pages: '71–72',
      text: '…The Company expects to continue to fund its dividend payments and share repurchases from its operating cash flows.\n\nDeemed Repatriation Tax Payable',
    },
  },
  {
    type: 'verdict',
    items: [
      '確定性規則 4 / 4 通過，無結構錯誤',
      '頁面導航找到可信頁（第 38 頁）',
      '開頭比對：與頁面一致',
      '尾段比對：與頁面一致',
    ],
  },
]

const currentDemo = computed(
  (): DemoStepData => demoSteps[activeFlowIndex.value] ?? demoSteps[0]!,
)

const showPrompt = ref(false)
const showTocPrompt = ref(false)

const navigationOption = computed<ECOption>(() => ({
  color: [palette.visual, palette.deterministicSoft],
  tooltip: { trigger: 'axis' },
  legend: {
    bottom: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: palette.stone },
  },
  grid: { top: 20, right: 16, bottom: 48, left: 42 },
  xAxis: {
    type: 'category',
    data: navigationRows.map((row) => row.filing.replace('_', '\n')),
    axisLine: { lineStyle: { color: palette.line } },
    axisTick: { show: false },
    axisLabel: { color: palette.stone, fontSize: 11 },
  },
  yAxis: {
    type: 'value',
    max: 100,
    axisLabel: { formatter: '{value}%', color: palette.stone },
    splitLine: { lineStyle: { color: '#e7e5e4' } },
  },
  series: [
    {
      name: '完全命中',
      type: 'bar',
      barWidth: 14,
      data: navigationRows.map((row) => percent(row.exact, row.exactTotal)),
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
    {
      name: '相差一頁內',
      type: 'bar',
      barWidth: 14,
      data: navigationRows.map((row) => percent(row.within, row.withinTotal)),
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
  ],
}))

const detectionOption = computed<ECOption>(() => ({
  color: [palette.visual, palette.rose],
  tooltip: { trigger: 'axis' },
  legend: {
    bottom: 0,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { color: palette.stone },
  },
  grid: { top: 20, right: 16, bottom: 48, left: 42 },
  xAxis: {
    type: 'category',
    data: detectionOperators.map((item) => item.shortLabel),
    axisLine: { lineStyle: { color: palette.line } },
    axisTick: { show: false },
    axisLabel: { color: palette.stone },
  },
  yAxis: {
    type: 'value',
    max: 100,
    axisLabel: { formatter: '{value}%', color: palette.stone },
    splitLine: { lineStyle: { color: '#e7e5e4' } },
  },
  series: [
    {
      name: '50 行',
      type: 'bar',
      barWidth: 16,
      data: detectionOperators.map((item) => item.linesRate),
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
    {
      name: '50%',
      type: 'bar',
      barWidth: 16,
      data: detectionOperators.map((item) => item.halfRate),
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
  ],
}))

const modelRankingOption = computed<ECOption>(() => {
  const rows = [...modelPrecisionRows].reverse()
  return {
    color: [palette.visual, palette.deterministicSoft],
    tooltip: { trigger: 'axis' },
    legend: {
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: palette.stone },
    },
    grid: { top: 16, right: 32, bottom: 48, left: 168 },
    xAxis: {
      type: 'value',
      min: 60,
      max: 100,
      axisLabel: { formatter: '{value}%', color: palette.stone },
      splitLine: { lineStyle: { color: '#e7e5e4' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((row) => row.model.replace('google/', '').replace('qwen/', '')),
      axisLine: { lineStyle: { color: palette.line } },
      axisTick: { show: false },
      axisLabel: { color: palette.stone, fontSize: 11 },
    },
    series: [
      {
        name: '開頭',
        type: 'bar',
        barWidth: 10,
        data: rows.map((row) => clippedPercentBar(row.headRate)),
        label: {
          show: true,
          position: 'right',
          formatter: percentBarLabel,
          color: palette.stone,
          fontSize: 10,
        },
        itemStyle: { borderRadius: [0, 4, 4, 0] },
      },
      {
        name: '尾段',
        type: 'bar',
        barWidth: 10,
        data: rows.map((row) => clippedPercentBar(row.tailRate)),
        label: {
          show: true,
          position: 'right',
          formatter: percentBarLabel,
          color: palette.stone,
          fontSize: 10,
        },
        itemStyle: { borderRadius: [0, 4, 4, 0] },
      },
    ],
  }
})

const modelDetectionOption = computed<ECOption>(() => {
  const rows = [...modelDetectionRows].reverse()
  return {
    color: [palette.visual, '#ea580c', palette.deterministicSoft],
    tooltip: { trigger: 'axis' },
    legend: {
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: palette.stone },
    },
    grid: { top: 12, right: 24, bottom: 48, left: 164 },
    xAxis: {
      type: 'value',
      min: 80,
      max: 100,
      axisLabel: { formatter: '{value}%', color: palette.stone },
      splitLine: { lineStyle: { color: '#e7e5e4' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((row) => row.model.replace('google/', '').replace('qwen/', '')),
      axisLine: { lineStyle: { color: palette.line } },
      axisTick: { show: false },
      axisLabel: { color: palette.stone, fontSize: 11 },
    },
    series: [
      {
        name: '平均偵測率',
        type: 'bar',
        barWidth: 8,
        barGap: '35%',
        barCategoryGap: '48%',
        data: rows.map((row) => row.averageRate),
        label: {
          show: true,
          position: 'right',
          formatter: '{c}%',
          color: palette.stone,
          fontSize: 10,
        },
        itemStyle: { borderRadius: [0, 4, 4, 0] },
      },
      {
        name: '開頭錯誤',
        type: 'bar',
        barWidth: 8,
        data: rows.map((row) => row.headRate),
        label: {
          show: true,
          position: 'right',
          formatter: '{c}%',
          color: palette.stone,
          fontSize: 10,
        },
        itemStyle: { borderRadius: [0, 4, 4, 0] },
      },
      {
        name: '尾段錯誤',
        type: 'bar',
        barWidth: 8,
        data: rows.map((row) => row.tailRate),
        label: {
          show: true,
          position: 'right',
          formatter: '{c}%',
          color: palette.stone,
          fontSize: 10,
        },
        itemStyle: { borderRadius: [0, 4, 4, 0] },
      },
    ],
  }
})

const benchOption = computed<ECOption>(() => {
  const pool = benchModels.filter((model) => !model.selected)
  const selected = benchModels.filter((model) => model.selected && !model.navModel)
  const nav = benchModels.filter((model) => model.navModel)

  const sourceLabel = (sourceType: (typeof benchModels)[number]['sourceType']) =>
    sourceType === 'open' ? '開放權重' : '閉源 API'

  const toPoint = (model: (typeof benchModels)[number]) => ({
    value: [model.price, model.rating, model.contextK, model.model, model.rank, sourceLabel(model.sourceType)],
    symbol: model.sourceType === 'open' ? 'diamond' : 'circle',
  })

  return {
    color: [palette.line, palette.visual, palette.rose],
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const data = (params as { data?: { value?: unknown[] } }).data?.value ?? []
        return `${data[3]}<br/>${data[5]}<br/>評分 ${data[1]} / 價格 $${data[0]} / 排名 #${data[4]}`
      },
    },
    legend: {
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: palette.stone },
    },
    grid: { top: 18, right: 24, bottom: 52, left: 48 },
    xAxis: {
      type: 'value',
      name: '價格 $/M',
      nameTextStyle: { color: palette.stone },
      axisLabel: { color: palette.stone },
      splitLine: { lineStyle: { color: '#e7e5e4' } },
    },
    yAxis: {
      type: 'value',
      name: '綜合評分',
      nameTextStyle: { color: palette.stone },
      axisLabel: { color: palette.stone },
      splitLine: { lineStyle: { color: '#e7e5e4' } },
    },
    series: [
      {
        name: '候選池',
        type: 'scatter',
        symbolSize: 11,
        data: pool.map(toPoint),
      },
      {
        name: '納入驗證',
        type: 'scatter',
        symbolSize: 15,
        data: selected.map(toPoint),
      },
      {
        name: '頁面導航模型',
        type: 'scatter',
        symbolSize: 22,
        data: nav.map(toPoint),
      },
    ],
  }
})

const activeFlow = computed(
  () =>
    flowNodes[activeFlowIndex.value] ?? {
      title: '解析器輸出',
      badge: '輸入',
      description: '解析器產出的 item 結構，是兩個驗證器共同檢查的對象。',
      phase: 'neutral',
      icon: 'file',
      points: ['item 標題', '起始頁與結束頁', '內容文字', '文件來源'],
    } satisfies FlowNode,
)

// Mobile menu
const mobileMenuOpen = ref(false)

// Scroll active section
const activeSection = ref('top')
const NAV_SECTIONS = ['top', 'flow', 'visual', 'deterministic', 'data'] as const

if (typeof window !== 'undefined') {
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          activeSection.value = entry.target.id
        }
      }
    },
    { rootMargin: '-30% 0px -60% 0px', threshold: 0 },
  )
  // Wait for DOM
  setTimeout(() => {
    for (const id of NAV_SECTIONS) {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    }
  }, 0)
}
</script>

<template>
  <main class="min-h-screen bg-stone-50 text-stone-950">
    <div class="fixed inset-x-0 top-0 z-40 border-b border-stone-200/80 bg-stone-50/85 backdrop-blur">
      <nav class="mx-auto flex max-w-7xl items-center justify-between px-5 py-3">
        <a href="#top" class="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck class="size-4 text-teal-700" />
          SEC 10-K 驗證總覽
        </a>
        <div class="hidden items-center gap-5 text-sm text-stone-600 md:flex">
          <a
            v-for="{ id, label } in [{ id: 'flow', label: '流程' }, { id: 'visual', label: '多模態視覺驗證' }, { id: 'deterministic', label: '確定性驗證' }]"
            :key="id"
            :href="'#' + id"
            class="transition-colors"
            :class="activeSection === id ? 'font-semibold text-stone-950' : 'hover:text-stone-950'"
          >{{ label }}</a>
        </div>
        <!-- 手機漢堡按鈕 -->
        <button
          type="button"
          class="ml-auto grid size-8 place-items-center text-stone-600 hover:text-stone-950 md:hidden"
          :aria-label="mobileMenuOpen ? '關閉選單' : '開啟選單'"
          @click="mobileMenuOpen = !mobileMenuOpen"
        >
          <X v-if="mobileMenuOpen" class="size-5" />
          <Menu v-else class="size-5" />
        </button>
      </nav>
      <!-- 手機展開選單 -->
      <Transition
        enter-active-class="transition-all duration-200"
        enter-from-class="opacity-0 -translate-y-1"
        enter-to-class="opacity-100 translate-y-0"
        leave-active-class="transition-all duration-150"
        leave-from-class="opacity-100 translate-y-0"
        leave-to-class="opacity-0 -translate-y-1"
      >
        <div v-if="mobileMenuOpen" class="border-t border-stone-200 bg-stone-50/95 backdrop-blur md:hidden">
          <div class="mx-auto flex max-w-7xl flex-col px-5 py-3 text-sm text-stone-600">
            <a
              v-for="{ id, label } in [{ id: 'flow', label: '流程' }, { id: 'visual', label: '多模態視覺驗證' }, { id: 'deterministic', label: '確定性驗證' }]"
              :key="id"
              :href="'#' + id"
              class="border-b border-stone-100 py-3 last:border-0 hover:text-stone-950"
              :class="activeSection === id ? 'font-semibold text-stone-950' : ''"
              @click="mobileMenuOpen = false"
            >{{ label }}</a>
          </div>
        </div>
      </Transition>
    </div>

    <section id="top" class="relative overflow-hidden border-b border-stone-200 pt-24">
      <div class="absolute inset-0 bg-[linear-gradient(120deg,rgba(20,184,166,0.12),transparent_36%),linear-gradient(45deg,rgba(245,158,11,0.14),transparent_34%)]" />
      <div
        class="relative mx-auto grid max-w-[92rem] gap-10 px-5 pb-14 pt-8 md:px-8 lg:grid-cols-[0.82fr_1.18fr] lg:items-end"
      >
        <div>
          <h1 class="max-w-4xl text-4xl font-bold leading-tight text-stone-950 md:text-6xl">
            SEC 10-K Parser 驗證總覽
          </h1>
          <p class="mt-5 max-w-2xl text-lg leading-8 text-stone-700">
            以視覺驗證器與確定性驗證器，建立可量化的解析器驗證閉環。
            先自動找到可信頁面，再檢查 item 邊界；同時用必要結構條件擋下明確錯誤。
          </p>
          <div class="mt-5 grid max-w-3xl gap-3">
            <div class="border-l-4 border-teal-500 bg-white/75 px-4 py-3 text-sm leading-6 text-stone-800 shadow-sm">
              多模態模型有機會用來構建 SEC 10-K Parser 驗證器，讓原本難以自動確認的頁面與邊界問題變成可量化檢查。
            </div>
            <div class="border-l-4 border-amber-500 bg-white/75 px-4 py-3 text-sm leading-6 text-stone-800 shadow-sm">
              基本且簡單的確定性驗證器可以快速建立零模型成本規則；只要規則被違反，錯誤就能被明確定位。
            </div>
          </div>
          <div class="mt-8 flex flex-wrap gap-3">
            <a
              href="#visual"
              class="inline-flex items-center gap-2 bg-stone-950 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-stone-800"
            >
              查看相關數據
              <ArrowRight class="size-4" />
            </a>
            <a
              href="#flow"
              class="inline-flex items-center gap-2 border border-stone-300 bg-white/80 px-4 py-2 text-sm font-semibold text-stone-800 transition hover:border-stone-500"
            >
              看驗證流程
            </a>
            <a
              href="https://github.com/LLMSystems/SEC-10-K-Structured-Extraction/blob/main/feedback/combined_validation_report.md"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-2 border border-teal-200 bg-teal-50/90 px-4 py-2 text-sm font-semibold text-teal-900 transition hover:border-teal-400 hover:bg-teal-100"
            >
              詳細文字報告
              <FileText class="size-4" />
            </a>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="metric in heroMetrics"
            :key="metric.label"
            class="border p-5 shadow-sm"
            :class="toneClass(metric.tone)"
          >
            <p class="text-sm font-semibold">{{ metric.label }}</p>
            <p class="mt-3 text-4xl font-bold leading-none">{{ metric.value }}</p>
            <p class="mt-3 text-sm leading-6 opacity-80">{{ metric.detail }}</p>
          </article>
        </div>
      </div>
    </section>

    <section id="flow" class="border-b border-stone-200 bg-white py-16">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-teal-700">驗證閉環</p>
            <h2 class="mt-2 text-3xl font-bold">從解析器輸出到可判定結果</h2>
          </div>
          <p class="max-w-xl text-sm leading-6 text-stone-600">
            流程先用確定性規則處理結構錯誤，再讓視覺驗證器處理需要頁面證據的邊界問題。
          </p>
        </div>

        <div class="border border-stone-200 bg-stone-50 p-4 md:p-6">
          <!-- 播放控制列 -->
          <div class="mb-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              class="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold shadow-sm transition"
              :class="
                isFlowEnded
                  ? 'bg-stone-600 text-white hover:bg-stone-700'
                  : isPlaying
                    ? 'bg-stone-950 text-white hover:bg-stone-800'
                    : 'bg-teal-700 text-white hover:bg-teal-800'
              "
              @click="togglePlay"
            >
              <RotateCcw v-if="isFlowEnded" class="size-4" />
              <Pause v-else-if="isPlaying" class="size-4" />
              <Play v-else class="size-4" />
              {{ isFlowEnded ? '重新播放' : isPlaying ? '暫停' : '播放流程' }}
            </button>
            <button
              type="button"
              :disabled="isPlaying || isFlowEnded"
              class="inline-flex items-center gap-2 border border-stone-300 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-500 disabled:cursor-not-allowed disabled:opacity-40"
              @click="stepForward"
            >
              <ArrowRight class="size-4" />
              下一步
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-2 border border-stone-200 bg-white px-3 py-2 text-sm text-stone-500 transition hover:border-stone-400 hover:text-stone-700"
              @click="resetFlow"
            >
              <RotateCcw class="size-3.5" />
              重置
            </button>
            <div class="ml-auto flex items-center gap-3 text-xs text-stone-500">
              <span class="font-semibold">
                {{ branchTaken ? '分岔結束' : `步驟 ${Math.min(activeFlowIndex + 1, flowNodes.length)} / ${flowNodes.length}` }}
              </span>
              <div class="h-1.5 w-28 overflow-hidden rounded-full bg-stone-200">
                <div
                  class="h-full rounded-full transition-all duration-700"
                  :class="
                    branchTaken === 'deterministic-fail'
                      ? 'bg-rose-500'
                      : branchTaken === 'neutral'
                        ? 'bg-stone-500'
                        : 'bg-teal-600'
                  "
                  :style="{ width: `${branchTaken ? 100 : (activeFlowIndex / (flowNodes.length - 1)) * 100}%` }"
                />
              </div>
            </div>
          </div>

          <!-- 圖例 -->
          <div class="mb-5 flex flex-wrap items-center gap-2 text-xs font-semibold text-stone-500">
            <span>圖例：</span>
            <span class="border border-amber-300 bg-amber-50 px-2 py-0.5 text-amber-800">確定性驗證</span>
            <span class="border border-teal-200 bg-teal-50 px-2 py-0.5 text-teal-800">視覺驗證</span>
            <span class="border border-stone-200 bg-white px-2 py-0.5 text-stone-700">輸入 / 輸出</span>
            <span class="inline-flex items-center gap-1 text-rose-600">
              <GitBranch class="size-3" />
              有分岔節點
            </span>
          </div>

          <!-- 流程圖 -->
          <div class="relative overflow-x-auto pb-2">
            <div class="pointer-events-none absolute inset-y-0 right-0 z-10 w-10 bg-gradient-to-l from-stone-50 to-transparent md:hidden" />
            <p class="mb-1 text-right text-xs text-stone-400 md:hidden">← 可左右滾動</p>
            <div class="flex min-w-[860px] items-start pt-2">
              <template v-for="(node, index) in flowNodes" :key="node.title">
                <!-- 節點 -->
                <div
                  class="flex min-w-0 flex-1 flex-col items-center"
                  :class="!isPlaying ? 'cursor-pointer' : 'cursor-default'"
                  @click="jumpToStep(index)"
                >
                  <!-- 圓形指示器 -->
                  <div class="relative mb-3 flex justify-center">
                    <span
                      v-if="isPlaying && activeFlowIndex === index"
                      class="absolute inline-flex size-14 animate-ping opacity-20"
                      :class="
                        node.phase === 'visual'
                          ? 'bg-teal-400'
                          : node.phase === 'deterministic'
                            ? 'bg-amber-400'
                            : 'bg-stone-400'
                      "
                    />
                    <span
                      class="relative z-10 grid size-14 place-items-center border-2 text-sm font-bold shadow-sm transition-all duration-500"
                      :class="nodeCircleClass(node.phase, index)"
                    >
                      <Check v-if="completedSteps.includes(index)" class="size-5" />
                      <span v-else>{{ String(index + 1).padStart(2, '0') }}</span>
                    </span>
                    <span
                      v-if="BRANCH_CONFIG[index] !== undefined"
                      class="absolute -right-1 -top-1 z-20 grid size-4 place-items-center rounded-full bg-rose-500 text-white shadow-sm"
                    >
                      <GitBranch class="size-2.5" />
                    </span>
                  </div>
                  <!-- 節點卡片 -->
                  <div
                    class="w-full border p-3 transition-all duration-500"
                    :class="[
                      flowPhaseClass(node.phase, activeFlowIndex === index),
                      activeFlowIndex === index ? 'shadow-md' : '',
                      !isPlaying ? 'hover:-translate-y-0.5 hover:shadow-sm' : '',
                    ]"
                  >
                    <div class="mb-2 flex items-center justify-between gap-2">
                      <span class="text-xs font-bold text-stone-500">{{ node.badge }}</span>
                      <BadgeCheck v-if="index === flowNodes.length - 1" class="size-3.5 text-teal-600" />
                    </div>
                    <h3 class="text-sm font-bold leading-snug">{{ node.title }}</h3>
                    <p class="mt-1.5 line-clamp-2 text-xs leading-5 text-stone-600">{{ node.description }}</p>
                    <p v-if="!isPlaying" class="mt-2 text-right text-[10px] text-stone-400">點擊跳轉</p>
                  </div>
                </div>

                <!-- 節點間連線 -->
                <div v-if="index < flowNodes.length - 1" class="flex flex-shrink-0 items-start px-0.5 pt-7">
                  <div class="relative h-px w-6">
                    <div class="absolute inset-0 bg-stone-200" />
                    <div
                      class="absolute inset-y-0 left-0 bg-teal-500 transition-all duration-700"
                      :style="{ width: completedSteps.includes(index) ? '100%' : '0%' }"
                    />
                  </div>
                  <ArrowRight
                    class="size-4 flex-shrink-0 transition-colors duration-700"
                    :class="completedSteps.includes(index) ? 'text-teal-500' : 'text-stone-300'"
                  />
                </div>
              </template>
            </div>
          </div>

          <!-- 範例資料面板 -->
          <Transition
            enter-active-class="transition-all duration-500"
            enter-from-class="opacity-0 translate-y-1"
            enter-to-class="opacity-100 translate-y-0"
            mode="out-in"
          >
            <div
              v-if="!branchTaken"
              :key="activeFlowIndex"
              class="mt-5 border border-stone-200 bg-white"
            >
              <div class="flex items-center gap-2 border-b border-stone-100 bg-stone-50 px-4 py-2.5">
                <Sparkles class="size-3.5 text-stone-400" />
                <span class="text-xs font-semibold text-stone-500">範例資料 — Apple Inc. 2021 10-K / Item 7</span>
                <span class="ml-auto rounded bg-stone-100 px-2 py-0.5 text-xs text-stone-400">步驟 {{ activeFlowIndex + 1 }} 對應</span>
              </div>

              <div class="p-4">
                <!-- 輸入：解析器輸出 -->
                <template v-if="currentDemo.type === 'input'">
                  <div class="grid gap-4 lg:grid-cols-[auto_1fr]">
                    <div class="grid content-start gap-2 text-sm">
                      <div class="flex gap-3">
                        <span class="w-24 shrink-0 text-xs font-semibold text-stone-400">Filing</span>
                        <span class="text-stone-800">{{ currentDemo.filing }}</span>
                      </div>
                      <div class="flex gap-3">
                        <span class="w-24 shrink-0 text-xs font-semibold text-stone-400">Item</span>
                        <span class="text-stone-800">{{ currentDemo.item }}</span>
                      </div>
                      <div class="flex gap-3">
                        <span class="w-24 shrink-0 text-xs font-semibold text-stone-400">page_start</span>
                        <span class="font-mono text-stone-800">{{ currentDemo.pageStart }}</span>
                      </div>
                      <div class="flex gap-3">
                        <span class="w-24 shrink-0 text-xs font-semibold text-stone-400">page_end</span>
                        <span class="font-mono text-stone-800">{{ currentDemo.pageEnd }}</span>
                      </div>
                      <div class="flex gap-3">
                        <span class="w-24 shrink-0 text-xs font-semibold text-stone-400">字元數</span>
                        <span class="font-mono text-stone-800">{{ currentDemo.chars }}</span>
                      </div>
                    </div>
                    <div class="whitespace-pre-line border border-stone-200 bg-stone-50 p-3 font-mono text-xs leading-6 text-stone-700">{{ currentDemo.headPreview }}</div>
                  </div>
                </template>

                <!-- 確定性規則核查 -->
                <template v-else-if="currentDemo.type === 'checks'">
                  <div class="grid gap-2">
                    <div
                      v-for="check in currentDemo.checks"
                      :key="check.rule"
                      class="flex items-start gap-3 text-sm"
                    >
                      <span
                        class="mt-0.5 grid size-5 shrink-0 place-items-center text-xs font-bold"
                        :class="check.pass ? 'bg-teal-600 text-white' : 'bg-rose-500 text-white'"
                      >
                        {{ check.pass ? '✓' : '✗' }}
                      </span>
                      <div class="flex flex-wrap items-baseline gap-x-2">
                        <span class="text-xs font-bold text-stone-400">{{ check.rule }}</span>
                        <span class="font-semibold text-stone-800">{{ check.label }}</span>
                        <span class="text-xs text-stone-500">{{ check.detail }}</span>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 頁面導航步驟 -->
                <template v-else-if="currentDemo.type === 'nav'">
                  <!-- TOC Prompt -->
                  <div class="mb-4">
                    <button
                      type="button"
                      class="flex w-full items-center justify-between border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-400 hover:bg-stone-100"
                      @click="showTocPrompt = !showTocPrompt"
                    >
                      <span class="flex items-center gap-2">
                        <FileText class="size-4 text-stone-500" />
                        目錄頁讀取 Prompt
                      </span>
                      <ChevronDown
                        class="size-4 text-stone-400 transition-transform duration-200"
                        :class="showTocPrompt ? 'rotate-180' : ''"
                      />
                    </button>
                    <Transition
                      enter-active-class="transition-all duration-300 overflow-hidden"
                      enter-from-class="opacity-0 max-h-0"
                      enter-to-class="opacity-100 max-h-[400px]"
                      leave-active-class="transition-all duration-200 overflow-hidden"
                      leave-from-class="opacity-100 max-h-[400px]"
                      leave-to-class="opacity-0 max-h-0"
                    >
                      <div v-if="showTocPrompt" class="border border-t-0 border-stone-200 bg-stone-50/60">
                        <div class="flex items-center gap-2 border-b border-stone-100 px-3 py-1.5">
                          <span class="rounded bg-stone-700 px-1.5 py-0.5 text-xs font-bold text-white">TOC_PROMPT</span>
                          <span class="text-xs text-stone-500">對每一頁判斷是否為目錄頁</span>
                        </div>
                        <pre class="whitespace-pre-wrap px-3 py-2.5 font-mono text-xs leading-6 text-stone-800">You are shown one page from a SEC 10-K filing.
If this page is part of the Table of Contents (it lists Items with their page numbers), output one line per entry in exactly this form:
  &lt;item&gt; | &lt;page&gt;
where &lt;item&gt; is the Item identifier exactly as printed (e.g. 1, 1A, 7A) and &lt;page&gt; is its printed page number. Include every Item row visible on this page, in order. Output ONLY these lines.
If this page is NOT a table of contents, output exactly: NONE</pre>
                      </div>
                    </Transition>
                  </div>
                  <div class="grid gap-2">
                    <div
                      v-for="step in currentDemo.navSteps"
                      :key="step.n"
                      class="flex items-start gap-3 text-sm"
                    >
                      <span class="grid size-5 shrink-0 place-items-center bg-teal-100 text-xs font-bold text-teal-800">{{ step.n }}</span>
                      <span class="text-stone-700">{{ step.text }}</span>
                    </div>
                  </div>
                  <div class="mt-4 border-l-2 border-teal-500 pl-3 text-sm font-semibold text-teal-700">{{ currentDemo.navResult }}</div>
                </template>

                <!-- 可信頁面確認 -->
                <template v-else-if="currentDemo.type === 'confirm'">
                  <div class="mb-3 flex items-center gap-2 text-sm">
                    <span class="border border-stone-300 bg-stone-100 px-2 py-0.5 font-mono text-xs font-bold text-stone-700">PDF 第 {{ currentDemo.page }} 頁</span>
                    <Check class="size-4 text-teal-600" />
                    <span class="font-semibold text-teal-700">標題確認成功</span>
                  </div>
                  <div class="whitespace-pre-line border-l-4 border-teal-400 bg-teal-50 px-4 py-3 font-mono text-xs leading-7 text-stone-800">{{ currentDemo.snippet }}</div>
                  <p class="mt-2 text-xs text-stone-500">{{ currentDemo.note }}</p>
                </template>

                <!-- 頁面證據比對 -->
                <template v-else-if="currentDemo.type === 'compare'">
                  <!-- Prompt 展示區（預設收合） -->
                  <div class="mb-4">
                    <button
                      type="button"
                      class="flex w-full items-center justify-between border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-400 hover:bg-stone-100"
                      @click="showPrompt = !showPrompt"
                    >
                      <span class="flex items-center gap-2">
                        <FileText class="size-4 text-stone-500" />
                        多模態模型 Prompt
                      </span>
                      <ChevronDown
                        class="size-4 text-stone-400 transition-transform duration-200"
                        :class="showPrompt ? 'rotate-180' : ''"
                      />
                    </button>
                    <Transition
                      enter-active-class="transition-all duration-300 overflow-hidden"
                      enter-from-class="opacity-0 max-h-0"
                      enter-to-class="opacity-100 max-h-[600px]"
                      leave-active-class="transition-all duration-200 overflow-hidden"
                      leave-from-class="opacity-100 max-h-[600px]"
                      leave-to-class="opacity-0 max-h-0"
                    >
                      <div v-if="showPrompt" class="mt-3 grid gap-3 lg:grid-cols-2">
                        <div class="border border-teal-200 bg-teal-50/60">
                          <div class="flex items-center gap-2 border-b border-teal-100 px-3 py-1.5">
                            <span class="rounded bg-teal-700 px-1.5 py-0.5 text-xs font-bold text-white">HEAD_PROMPT</span>
                            <span class="text-xs text-teal-700">開頭擷取指令</span>
                          </div>
                          <pre class="whitespace-pre-wrap px-3 py-2.5 font-mono text-xs leading-6 text-teal-950">You are shown one page from a SEC 10-K filing rendered as an image.
This page contains the BEGINNING of "Item 7. Management's Discussion..."
Find the "Item 7" heading, then transcribe VERBATIM the first 5 lines of text that come immediately after it.
Include any report title, addressee, or sub-heading exactly as printed — do NOT skip them; only ignore page running-headers, page numbers, and footers.
Output ONLY the transcribed text, no commentary, no quotes, no formatting.</pre>
                        </div>
                        <div class="border border-stone-200 bg-stone-50/60">
                          <div class="flex items-center gap-2 border-b border-stone-100 px-3 py-1.5">
                            <span class="rounded bg-stone-700 px-1.5 py-0.5 text-xs font-bold text-white">TAIL_PROMPT</span>
                            <span class="text-xs text-stone-600">尾段擷取指令</span>
                          </div>
                          <pre class="whitespace-pre-wrap px-3 py-2.5 font-mono text-xs leading-6 text-stone-800">You are shown the last 1-2 rendered pages of the region for "Item 7. Management's Discussion...". The next section is "Item 7A. Quantitative and Qualitative Disclosures...".
Find where Item 7 ends — right before the "Item 7A" heading if it appears, otherwise the very bottom of the last page — and transcribe VERBATIM the last 5 lines of Item 7 before that point.
Ignore running headers, page numbers, footers.
Output ONLY the transcribed text, no commentary, no quotes, no formatting.</pre>
                        </div>
                      </div>
                    </Transition>
                  </div>
                  <!-- 比對結果 -->
                  <div class="grid gap-3 lg:grid-cols-2">
                    <div>
                      <div class="mb-1.5 flex items-center gap-2">
                        <span class="text-xs font-bold text-stone-400">開頭檢查</span>
                        <span class="border border-stone-200 bg-stone-50 px-1.5 py-0.5 font-mono text-xs text-stone-600">第 {{ currentDemo.head?.page }} 頁</span>
                        <span class="text-xs font-semibold text-teal-600">一致 ✓</span>
                      </div>
                      <div class="whitespace-pre-line border border-stone-200 bg-stone-50 p-3 font-mono text-xs leading-6 text-stone-700">{{ currentDemo.head?.text }}</div>
                    </div>
                    <div>
                      <div class="mb-1.5 flex items-center gap-2">
                        <span class="text-xs font-bold text-stone-400">尾段檢查</span>
                        <span class="border border-stone-200 bg-stone-50 px-1.5 py-0.5 font-mono text-xs text-stone-600">第 {{ currentDemo.tail?.pages }} 頁</span>
                        <span class="text-xs font-semibold text-teal-600">一致 ✓</span>
                      </div>
                      <div class="whitespace-pre-line border border-stone-200 bg-stone-50 p-3 font-mono text-xs leading-6 text-stone-700">{{ currentDemo.tail?.text }}</div>
                    </div>
                  </div>
                </template>

                <!-- 判定結果 -->
                <template v-else-if="currentDemo.type === 'verdict'">
                  <div class="flex items-start gap-3">
                    <CheckCircle2 class="mt-0.5 size-6 shrink-0 text-teal-600" />
                    <div>
                      <p class="font-bold text-teal-900">Item 7 通過所有驗證</p>
                      <ul class="mt-2 grid gap-1.5">
                        <li
                          v-for="item in currentDemo.items"
                          :key="item"
                          class="flex items-center gap-2 text-sm text-stone-700"
                        >
                          <Check class="size-3.5 shrink-0 text-teal-600" />
                          {{ item }}
                        </li>
                      </ul>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </Transition>

          <!-- 分岔選擇面板 -->
          <Transition
            enter-active-class="transition-all duration-300"
            enter-from-class="opacity-0 -translate-y-2"
            enter-to-class="opacity-100 translate-y-0"
            leave-active-class="transition-all duration-200"
            leave-from-class="opacity-100 translate-y-0"
            leave-to-class="opacity-0 -translate-y-1"
          >
            <div v-if="showBranchChoice && currentBranchConfig" class="mt-5 border-2 border-stone-300 bg-white p-5">
              <div class="mb-4 flex items-start gap-3">
                <GitBranch class="mt-0.5 size-5 shrink-0 text-stone-600" />
                <div>
                  <p class="font-bold text-stone-950">{{ currentBranchConfig.title }}</p>
                  <p class="mt-1 text-sm text-stone-600">{{ currentBranchConfig.description }}</p>
                </div>
              </div>
              <div class="flex flex-wrap gap-3">
                <button
                  type="button"
                  class="inline-flex items-center gap-2 border border-teal-300 bg-teal-50 px-4 py-2.5 text-sm font-semibold text-teal-900 transition hover:bg-teal-100"
                  @click="chooseBranch('main')"
                >
                  <Check class="size-4" />
                  {{ currentBranchConfig.mainLabel }}
                </button>
                <button
                  type="button"
                  class="inline-flex items-center gap-2 border border-stone-300 bg-stone-50 px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:bg-stone-100"
                  @click="chooseBranch('branch')"
                >
                  <ArrowDown class="size-4" />
                  {{ currentBranchConfig.branchLabel }}
                </button>
              </div>
            </div>
          </Transition>

          <!-- 結果：中立 -->
          <div v-if="branchTaken === 'neutral'" class="mt-5 border-2 border-dashed border-stone-400 bg-stone-50 p-5">
            <div class="flex items-start gap-3">
              <CircleDashed class="mt-0.5 size-6 shrink-0 text-stone-500" />
              <div>
                <p class="font-bold text-stone-900">中立 / 無法判定</p>
                <p class="mt-1 text-sm leading-6 text-stone-600">
                  找不到可信頁面代表證據不足。系統輸出「無法判定」，既不判解析器錯，也不判對。這是設計上的保守選擇，避免誤殺正確結果。
                </p>
              </div>
            </div>
            <button type="button" class="mt-4 text-sm text-stone-500 underline underline-offset-4 hover:text-stone-700" @click="resetFlow">
              ↺ 重新播放
            </button>
          </div>

          <!-- 結果：確定性失敗 -->
          <div v-if="branchTaken === 'deterministic-fail'" class="mt-5 border-2 border-rose-400 bg-rose-50 p-5">
            <div class="flex items-start gap-3">
              <AlertTriangle class="mt-0.5 size-6 shrink-0 text-rose-600" />
              <div>
                <p class="font-bold text-rose-950">確定性驗證失敗</p>
                <p class="mt-1 text-sm leading-6 text-rose-800">
                  規則條件被明確違反。這代表解析器結果存在可直接判斷的結構性錯誤，例如頁碼區間非法或重要章節消失，不需要多模態模型即可定位問題。
                </p>
              </div>
            </div>
            <button type="button" class="mt-4 text-sm text-rose-600 underline underline-offset-4 hover:text-rose-800" @click="resetFlow">
              ↺ 重新播放
            </button>
          </div>

          <!-- 結果：完整通過 -->
          <div v-if="isFlowEnded && !branchTaken" class="mt-5 border-2 border-teal-400 bg-teal-50 p-5">
            <div class="flex items-start gap-3">
              <CheckCircle2 class="mt-0.5 size-6 shrink-0 text-teal-700" />
              <div>
                <p class="font-bold text-teal-950">驗證完成：輸出判定結果</p>
                <p class="mt-1 text-sm leading-6 text-teal-800">
                  確定性規則通過、頁面導航找到可信頁、視覺比對一致——解析器輸出通過全部驗證，可以信任這份抽取結果。
                </p>
              </div>
            </div>
            <button type="button" class="mt-4 text-sm text-teal-700 underline underline-offset-4 hover:text-teal-900" @click="resetFlow">
              ↺ 重新播放
            </button>
          </div>

          <!-- 節點詳情面板 -->
          <div class="mt-5 grid gap-4 lg:grid-cols-[1fr_0.42fr]">
            <aside class="border border-stone-200 bg-white p-5">
              <div class="flex items-center gap-3">
                <div
                  class="grid size-11 place-items-center"
                  :class="
                    activeFlow.phase === 'deterministic'
                      ? 'bg-amber-700 text-white'
                      : activeFlow.phase === 'visual'
                        ? 'bg-teal-700 text-white'
                        : 'bg-stone-900 text-white'
                  "
                >
                  <SearchCheck v-if="activeFlow.icon === 'search'" class="size-5" />
                  <Navigation v-else-if="activeFlow.icon === 'navigation'" class="size-5" />
                  <Eye v-else-if="activeFlow.icon === 'eye'" class="size-5" />
                  <ShieldCheck v-else-if="activeFlow.icon === 'shield'" class="size-5" />
                  <BadgeCheck v-else-if="activeFlow.icon === 'result'" class="size-5" />
                  <FileText v-else class="size-5" />
                </div>
                <div>
                  <p class="text-xs font-semibold text-stone-500">目前節點</p>
                  <h3 class="text-xl font-bold">{{ activeFlow.title }}</h3>
                </div>
              </div>
              <ol class="mt-5 grid gap-0 md:grid-cols-2 md:gap-x-6">
                <li
                  v-for="(point, pointIndex) in activeFlow.points"
                  :key="point"
                  class="relative flex items-start gap-3 pb-4 text-sm leading-6 text-stone-700"
                >
                  <span
                    v-if="pointIndex < activeFlow.points.length - 1"
                    class="absolute left-3 top-7 h-[calc(100%-1.75rem)] w-px bg-stone-200 md:hidden"
                  />
                  <span
                    class="z-10 grid size-6 shrink-0 place-items-center border bg-white text-xs font-bold"
                    :class="
                      activeFlow.phase === 'deterministic'
                        ? 'border-amber-300 text-amber-800'
                        : activeFlow.phase === 'visual'
                          ? 'border-teal-300 text-teal-800'
                          : 'border-stone-300 text-stone-700'
                    "
                  >
                    {{ pointIndex + 1 }}
                  </span>
                  <span>{{ point }}</span>
                </li>
              </ol>
            </aside>

            <aside class="border border-stone-200 bg-white p-5">
              <div class="flex items-center gap-3">
                <CircleDashed class="size-6 text-stone-500" />
                <div>
                  <p class="text-xs font-semibold text-stone-500">中立分岔</p>
                  <h3 class="text-lg font-bold">沒有可信頁面時不判錯</h3>
                </div>
              </div>
              <p class="mt-4 text-sm leading-7 text-stone-600">
                可信頁面確認失敗代表證據不足，系統會輸出「中立 / 無法判定」；它不代表解析器一定錯，也不代表解析器一定對。
              </p>
              <div class="mt-4 border-l-2 border-dashed border-stone-300 pl-4 text-sm font-semibold text-stone-700">
                可信頁面確認 -> 中立 / 無法判定
              </div>
            </aside>
          </div>
        </div>
      </div>
    </section>

    <section class="border-b border-stone-200 bg-stone-100/70 py-16">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8">
          <p class="text-sm font-semibold text-amber-700">兩個驗證器</p>
          <h2 class="mt-2 text-3xl font-bold">一個看頁面，一個看必要條件</h2>
        </div>
        <div class="grid gap-5 lg:grid-cols-2">
          <article
            v-for="summary in validatorSummaries"
            :key="summary.title"
            class="border border-stone-200 bg-white p-6 shadow-sm"
          >
            <div class="mb-5 flex items-center justify-between gap-4">
              <div>
                <p class="text-sm font-semibold text-stone-500">{{ summary.eyebrow }}</p>
                <h3 class="mt-1 text-2xl font-bold">{{ summary.title }}</h3>
              </div>
              <Eye v-if="summary.eyebrow === '頁面證據'" class="size-8 text-teal-700" />
              <FileCheck2 v-else class="size-8 text-amber-700" />
            </div>
            <p class="text-sm leading-7 text-stone-600">{{ summary.description }}</p>
            <ul v-if="'proofPoints' in summary" class="mt-4 grid gap-2">
              <li
                v-for="point in summary.proofPoints"
                :key="point"
                class="flex items-start gap-3 text-sm leading-6 text-stone-700"
              >
                <span class="mt-2 size-1.5 shrink-0 bg-teal-700" />
                <span>{{ point }}</span>
              </li>
            </ul>
            <div class="mt-6 grid gap-2 sm:grid-cols-3">
              <div
                v-for="metric in summary.metrics"
                :key="metric"
                class="border border-stone-200 bg-stone-50 p-3 text-sm font-semibold text-stone-800"
              >
                {{ metric }}
              </div>
            </div>
            <div class="mt-5 flex flex-wrap gap-2">
              <span
                v-for="item in summary.catches"
                :key="item"
                class="border border-stone-300 bg-white px-2.5 py-1 text-xs font-medium text-stone-600"
              >
                {{ item }}
              </span>
            </div>
          </article>
        </div>
      </div>
    </section>

    <section id="data" class="border-y border-stone-200 bg-stone-100/70 py-16">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-sky-700">候選模型池</p>
            <h2 class="mt-2 text-3xl font-bold">多模態模型選型</h2>
          </div>
          <div class="max-w-2xl space-y-2 text-sm leading-6 text-stone-600">
            <p>
              候選池參考多模態模型評分、價格、上下文長度、速度與延遲，再篩選可穩定呼叫且支援影像輸入的模型，共挑選了
              <span class="font-semibold text-stone-950">13 個多模態模型</span>
              進行評測。評分來源參考
              <a
                href="https://llm-stats.com/benchmarks/category/multimodal"
                target="_blank"
                rel="noopener noreferrer"
                class="font-semibold text-sky-700 underline decoration-sky-300 underline-offset-4 hover:text-sky-900"
              >
                LLM Stats 多模態排行榜
              </a>
              。
            </p>
          </div>
        </div>

        <div class="grid gap-5 lg:grid-cols-[1fr_0.48fr]">
          <article class="border border-stone-200 bg-white p-5 shadow-sm">
            <div class="mb-3 flex items-center gap-2">
              <Layers3 class="size-5 text-sky-700" />
              <h3 class="text-lg font-bold">價格與綜合評分分布</h3>
            </div>
            <div class="relative h-96 overflow-hidden">
              <VChart class="absolute inset-0 size-full" :option="benchOption" autoresize />
            </div>
            <div class="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs font-semibold text-stone-600">
              <span class="text-stone-500">形狀標籤</span>
              <span class="inline-flex items-center gap-2">
                <span class="size-2.5 rotate-45 border border-stone-500 bg-stone-200" />
                開放權重
              </span>
              <span class="inline-flex items-center gap-2">
                <span class="size-2.5 rounded-full border border-stone-500 bg-stone-200" />
                閉源 API
              </span>
            </div>
          </article>

          <div class="grid gap-4">
            <article class="border border-teal-200 bg-teal-50 p-5">
              <p class="text-sm font-semibold text-teal-800">頁面導航模型</p>
              <h3 class="mt-2 text-xl font-bold">google/gemini-3-flash-preview</h3>
              <p class="mt-3 text-sm leading-6 text-teal-950/80">
                頁面導航任務包含 TOC 辨識、頁碼對位與標題確認，比單純讀首尾段更吃穩定性，因此固定使用目前最穩定的模型。
              </p>
            </article>
            <article class="border border-stone-200 bg-white p-5">
              <p class="text-sm font-semibold text-stone-500">篩選邏輯</p>
              <div class="mt-4 grid gap-3">
                <div class="flex items-center gap-3 text-sm">
                  <Database class="size-4 text-stone-500" />
                  候選模型池
                </div>
                <div class="flex items-center gap-3 text-sm">
                  <FileText class="size-4 text-stone-500" />
                  支援影像輸入
                </div>
                <div class="flex items-center gap-3 text-sm">
                  <WalletCards class="size-4 text-stone-500" />
                  成本與速度可接受
                </div>
                <div class="flex items-center gap-3 text-sm">
                  <BadgeCheck class="size-4 text-teal-700" />
                  納入正式驗證
                </div>
              </div>
            </article>
          </div>
        </div>
      </div>
    </section>

    <section id="visual" class="bg-white py-16">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-teal-700">視覺驗證器</p>
            <h2 class="mt-2 text-3xl font-bold">頁面導航、精確度與錯誤偵測</h2>
          </div>
          <p class="max-w-2xl text-sm leading-6 text-stone-600">
            這一區用三組圖回答同一個問題：視覺驗證器能不能先找到頁面、避免誤殺正確結果，並在解析器真的抓錯時把錯誤偵測出來。
          </p>
        </div>

        <div class="mb-5 border border-stone-200 bg-stone-50 p-5">
          <div class="mb-4 flex items-center gap-2">
            <FileText class="size-5 text-teal-700" />
            <h3 class="text-lg font-bold">方法與數據準備</h3>
          </div>
          <div class="grid gap-4 md:grid-cols-3">
            <article class="border border-stone-200 bg-white p-4">
              <p class="text-sm font-bold text-stone-950">資料準備</p>
              <p class="mt-2 text-sm leading-6 text-stone-600">
                使用 5 份 人工標註 10-K 文件，並準備原始 PDF、頁面圖像、章節內容標註，以及章節起訖頁面的對照資料。
              </p>
            </article>
            <article class="border border-stone-200 bg-white p-4">
              <p class="text-sm font-bold text-stone-950">驗證流程</p>
              <p class="mt-2 text-sm leading-6 text-stone-600">
                先從目錄和頁碼線索自動找到候選頁，再確認頁面上真的有對應章節標題，最後才進行多模態頁面比對。
              </p>
            </article>
            <article class="border border-stone-200 bg-white p-4">
              <p class="text-sm font-bold text-stone-950">邊界檢查</p>
              <p class="mt-2 text-sm leading-6 text-stone-600">
                開頭看起始頁，尾段看最後 1 到 2 頁；如果章節前後都和頁面證據一致，就能建立高信心判斷。
              </p>
            </article>
          </div>
        </div>

        <div class="mb-5 grid gap-3 md:grid-cols-3">
          <article class="border border-teal-200 bg-teal-50 p-4 text-teal-950">
            <div class="mb-3 flex items-center gap-2">
              <Navigation class="size-4 text-teal-700" />
              <h3 class="text-sm font-bold">頁面導航</h3>
            </div>
            <p class="text-sm leading-6">看的是自動找頁能力，分母是 5 份文件中的 62 個章節。</p>
          </article>
          <article class="border border-stone-200 bg-white p-4 text-stone-800">
            <div class="mb-3 flex items-center gap-2">
              <Gauge class="size-4 text-stone-700" />
              <h3 class="text-sm font-bold">端到端精確度</h3>
            </div>
            <p class="text-sm leading-6">看的是正確解析結果會不會被誤判，基線模型是 Gemini 3 Flash Preview。</p>
          </article>
          <article class="border border-rose-200 bg-rose-50 p-4 text-rose-950">
            <div class="mb-3 flex items-center gap-2">
              <Radar class="size-4 text-rose-700" />
              <h3 class="text-sm font-bold">錯誤偵測率</h3>
            </div>
            <p class="text-sm leading-6">看的是刻意注入截斷或越界後，驗證器能不能抓出問題。</p>
          </article>
        </div>

        <div class="grid gap-5 lg:grid-cols-2">
          <article class="border border-stone-200 bg-stone-50 p-5">
            <div class="mb-4 flex flex-col gap-3">
              <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div class="flex items-center gap-2">
                  <Navigation class="size-5 text-teal-700" />
                  <h3 class="text-lg font-bold">頁面導航覆蓋率</h3>
                </div>
                <p class="text-sm font-semibold text-stone-600">基線模型：google/gemini-3-flash-preview</p>
              </div>
              <p class="text-sm leading-6 text-stone-600">
                這張圖比較「完全找到正確頁」與「落在正確頁前後一頁內」。後者代表雖然頁碼不完全精準，但仍足以進一步做可信頁面確認。
              </p>
              <div class="flex flex-wrap gap-2 text-xs font-semibold">
                <span class="border border-stone-300 bg-white px-2 py-1">完全命中 56/62</span>
                <span class="border border-teal-200 bg-teal-50 px-2 py-1 text-teal-900">一頁內 60/62</span>
              </div>
            </div>
            <div class="relative h-72 overflow-hidden">
              <VChart class="absolute inset-0 size-full" :option="navigationOption" autoresize />
            </div>
            <div class="mt-4 border border-teal-200 bg-white p-4 text-sm leading-6 text-stone-700">
              <p class="font-bold text-stone-950">結論：頁面導航已足以支撐後續視覺檢查。</p>
              <p class="mt-1">
                5 份文件、62 個章節中，有 96.8% 能落在正確頁或前後一頁內；即使不是完全命中，也多半仍可進入可信頁面確認。
              </p>
            </div>
          </article>

          <article class="border border-stone-200 bg-stone-50 p-5">
            <div class="mb-4 flex flex-col gap-3">
              <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div class="flex items-center gap-2">
                  <Gauge class="size-5 text-teal-700" />
                  <h3 class="text-lg font-bold">端到端精確度</h3>
                </div>
                <p class="text-sm font-semibold text-stone-600">基線模型：google/gemini-3-flash-preview</p>
              </div>
              <p class="text-sm leading-6 text-stone-600">
                這張圖只看已通過可信頁面確認的正確樣本。開頭檢查使用起始頁，尾段檢查使用最後 1 到 2 頁，因此更能處理跨頁結尾。
              </p>
              <div class="flex flex-wrap gap-2 text-xs font-semibold">
                <span class="border border-stone-300 bg-white px-2 py-1">開頭 59/60</span>
                <span class="border border-teal-200 bg-teal-50 px-2 py-1 text-teal-900">尾段 43/44</span>
              </div>
            </div>
            <div class="grid grid-cols-2 gap-4 py-4">
              <div class="flex flex-col items-center justify-center border border-teal-200 bg-teal-50 py-8">
                <p class="text-xs font-semibold text-teal-700">開頭精確度</p>
                <p class="mt-2 text-5xl font-bold tabular-nums text-teal-950">98.3<span class="text-2xl">%</span></p>
                <p class="mt-1 text-xs text-teal-600">59 / 60</p>
              </div>
              <div class="flex flex-col items-center justify-center border border-stone-200 bg-stone-50 py-8">
                <p class="text-xs font-semibold text-stone-600">尾段精確度</p>
                <p class="mt-2 text-5xl font-bold tabular-nums text-stone-950">97.7<span class="text-2xl">%</span></p>
                <p class="mt-1 text-xs text-stone-500">43 / 44</p>
              </div>
            </div>
            <div class="mt-4 border border-teal-200 bg-white p-4 text-sm leading-6 text-stone-700">
              <p class="font-bold text-stone-950">結論：通過可信頁面門檻後，正確解析不容易被誤殺。</p>
              <p class="mt-1">
                開頭與尾段合計 102/104 通過，代表視覺驗證器可以作為高信心檢查器；尤其尾段改看 1 到 2 頁後，跨頁結尾更穩定。
              </p>
            </div>
          </article>

          <article class="border border-stone-200 bg-stone-50 p-5 lg:col-span-2">
            <div class="mb-4 flex flex-col gap-3">
              <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div class="flex items-center gap-2">
                  <Radar class="size-5 text-teal-700" />
                  <h3 class="text-lg font-bold">四類錯誤偵測率</h3>
                </div>
                <p class="text-sm font-semibold text-stone-600">基線模型：google/gemini-3-flash-preview</p>
              </div>
              <p class="max-w-4xl text-sm leading-6 text-stone-600">
                這張圖是在正確樣本上刻意製造錯誤，再看視覺驗證器能不能抓到。每一類錯誤各測兩種強度：截掉或越界 50 行，以及截掉或越界 50%。
              </p>
              <div class="flex flex-wrap gap-2 text-xs font-semibold">
                <span
                  v-for="operator in detectionOperators"
                  :key="operator.label"
                  class="group relative border border-stone-300 bg-white px-2 py-1 text-stone-700"
                >
                  {{ operator.label }}
                  <span
                    class="pointer-events-none absolute left-0 top-full z-30 mt-2 hidden w-64 border border-stone-200 bg-white p-3 text-left text-xs font-normal leading-5 text-stone-700 shadow-lg group-hover:block"
                  >
                    <span class="mb-1 block font-bold text-stone-950">{{ operator.label }}</span>
                    {{ operator.description }}
                  </span>
                </span>
              </div>
            </div>
            <div class="relative h-80 overflow-hidden">
              <VChart class="absolute inset-0 size-full" :option="detectionOption" autoresize />
            </div>
            <div class="mt-4 grid gap-3 md:grid-cols-3">
              <div class="border border-teal-200 bg-white p-4 text-sm leading-6 text-stone-700 md:col-span-2">
                <p class="font-bold text-stone-950">結論：視覺驗證器能有效抓出邊界錯誤，但應保留中立判定。</p>
                <p class="mt-1">
                  四類注入錯誤的偵測率落在 83.1% 到 95.3%；對明顯截斷與越界已有實用偵測力，但沒有找到可信頁面時，不應直接判定解析器錯。
                </p>
              </div>
              <div class="border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
                <p class="font-bold">讀圖重點</p>
                <p class="mt-1">50% 強度通常比 50 行更容易被抓到；尾段錯誤仍是最需要保守解讀的部分。</p>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>

    <section class="bg-white py-16">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-teal-700">模型比較</p>
            <h2 class="mt-2 text-3xl font-bold">檢查模型精確度排行榜</h2>
          </div>
          <p class="max-w-2xl text-sm leading-6 text-stone-600">
            共評比
            <span class="font-semibold text-stone-950">13 個多模態模型</span>
            ；這裡固定頁面導航模型，只替換負責讀頁面與比對邊界的檢查模型，避免把找頁能力和邊界判讀能力混在一起。
          </p>
        </div>

        <div class="mb-5 grid gap-3 md:grid-cols-3">
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">比較任務</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              所有模型都在同一批可信頁面上檢查章節開頭與尾段，任務是判斷解析器輸出是否和頁面證據一致。
            </p>
          </article>
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">固定條件</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              頁面導航模型固定為 Gemini 3 Flash Preview，排行榜主要反映檢查模型本身的邊界判讀能力。
            </p>
          </article>
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">讀圖重點</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              開頭通常較穩，尾段更容易受到跨頁、同頁換節與短尾巴影響，因此尾段分數更能拉開差異。低於 60% 的項目會貼在左側並保留真實數字。
            </p>
          </article>
        </div>

        <article class="border border-stone-200 bg-stone-50 p-5">
          <div class="relative h-[32rem] overflow-hidden">
            <VChart class="absolute inset-0 size-full" :option="modelRankingOption" autoresize />
          </div>
        </article>

        <div class="mt-5 grid gap-3 md:grid-cols-3">
          <article class="border border-teal-200 bg-teal-50 p-4 text-teal-950">
            <p class="text-sm font-bold">主要發現 1</p>
            <p class="mt-2 text-sm leading-6">
              Gemini 3 Flash Preview 在開頭與尾段都維持最高穩定度，是目前最適合作為基線的檢查模型。
            </p>
          </article>
          <article class="border border-sky-200 bg-sky-50 p-4 text-sky-950">
            <p class="text-sm font-bold">主要發現 2</p>
            <p class="mt-2 text-sm leading-6">
              Qwen 3.6 Plus 與 Kimi K2.6 在 2 頁尾段設定下表現明顯改善，表示多頁尾段檢查能降低跨頁結尾的誤判。
            </p>
          </article>
          <article class="border border-amber-200 bg-amber-50 p-4 text-amber-950">
            <p class="text-sm font-bold">主要發現 3</p>
            <p class="mt-2 text-sm leading-6">
              多數模型的開頭分數接近滿分，但尾段分數差距較大，尾段仍是視覺驗證中最有鑑別力的部分。
            </p>
          </article>
        </div>

        <div class="mt-12 mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-rose-700">模型比較</p>
            <h2 class="mt-2 text-3xl font-bold">檢查模型錯誤偵測率排行榜</h2>
          </div>
          <p class="max-w-2xl text-sm leading-6 text-stone-600">
            精確度看的是「正確結果不要被誤殺」，偵測率看的是「真的有錯時能不能抓到」。這張圖把四類錯誤與兩種錯誤強度按分母加權後，合併成平均偵測率。
          </p>
        </div>

        <div class="mb-5 grid gap-3 md:grid-cols-3">
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">比較任務</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              對正確樣本刻意注入開頭截斷、開頭越界、尾段截斷、尾段越界，再看模型能不能判出問題。
            </p>
          </article>
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">讀圖方式</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              平均偵測率越高，代表模型對錯誤邊界越敏感；開頭錯誤與尾段錯誤也分開呈現，避免只看加權總分。
            </p>
          </article>
          <article class="border border-stone-200 bg-stone-50 p-4">
            <p class="text-sm font-bold text-stone-950">重要提醒</p>
            <p class="mt-2 text-sm leading-6 text-stone-600">
              這張表的分母會隨模型的正確樣本通過情況而變動，因此要和前面的精確度排行榜一起解讀。
            </p>
          </article>
        </div>

        <article class="border border-stone-200 bg-stone-50 p-5">
          <div class="relative h-[42rem] overflow-hidden">
            <VChart class="absolute inset-0 size-full" :option="modelDetectionOption" autoresize />
          </div>
        </article>

        <div class="mt-5 grid gap-3 md:grid-cols-3">
          <article class="border border-rose-200 bg-rose-50 p-4 text-rose-950">
            <p class="text-sm font-bold">主要發現 1</p>
            <p class="mt-2 text-sm leading-6">
              Gemini 3 Flash Preview 仍維持很均衡的錯誤偵測能力，是精確度與偵測率都穩的基線選擇。
            </p>
          </article>
          <article class="border border-amber-200 bg-amber-50 p-4 text-amber-950">
            <p class="text-sm font-bold">主要發現 2</p>
            <p class="mt-2 text-sm leading-6">
              Gemma 4 26B 的平均偵測率很高，但可評估分母較小，不能單獨解讀為整體最佳。
            </p>
          </article>
          <article class="border border-sky-200 bg-sky-50 p-4 text-sky-950">
            <p class="text-sm font-bold">主要發現 3</p>
            <p class="mt-2 text-sm leading-6">
              部分模型在尾段錯誤上更敏感，但精確度較低；這代表「抓得到」和「不誤殺」需要一起看。
            </p>
          </article>
        </div>
      </div>
    </section>

    <section id="deterministic" class="border-y border-stone-200 bg-stone-950 py-16 text-white">
      <div class="mx-auto max-w-7xl px-5">
        <div class="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <p class="text-sm font-semibold text-amber-300">確定性驗證器</p>
            <h2 class="mt-2 text-3xl font-bold">違反必要條件，就能直接證錯</h2>
          </div>
          <p class="max-w-2xl text-sm leading-6 text-stone-300">
            這一層不依賴多模態模型，專門處理頁碼區間、順序、重要 item 與全文完整度這類結構問題。
          </p>
        </div>

        <div class="mb-5 border border-white/10 bg-white/10 p-5">
          <div class="mb-4 flex items-center gap-2">
            <FileCheck2 class="size-5 text-amber-300" />
            <h3 class="text-lg font-bold">方法與數據準備</h3>
          </div>
          <div class="grid gap-4 md:grid-cols-3">
            <article class="border border-white/10 bg-white/5 p-4">
              <p class="text-sm font-bold text-amber-200">正確資料</p>
              <p class="mt-2 text-sm leading-6 text-stone-300">
                使用 34 份人工整理的 10-K Ground Truth，檢查規則在正確資料上是否會誤殺。
              </p>
            </article>
            <article class="border border-white/10 bg-white/5 p-4">
              <p class="text-sm font-bold text-amber-200">錯誤樣本</p>
              <p class="mt-2 text-sm leading-6 text-stone-300">
                從 Ground Truth 系統性產生 3,760 個錯誤樣本，模擬區間非法、順序錯亂、章節遺失與內容過短。
              </p>
            </article>
            <article class="border border-white/10 bg-white/5 p-4">
              <p class="text-sm font-bold text-amber-200">判斷方式</p>
              <p class="mt-2 text-sm leading-6 text-stone-300">
                規則只檢查正確解析必然滿足的條件；一旦違反，就能直接定位錯誤，不需要模型主觀判斷。
              </p>
            </article>
          </div>
        </div>

        <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <article
            v-for="rule in deterministicRules"
            :key="rule.rule"
            class="border border-white/10 bg-white/10 p-5"
          >
            <div class="mb-5 flex items-center justify-between">
              <span class="text-sm font-semibold text-amber-300">{{ rule.rule }}</span>
              <ShieldCheck class="size-5 text-amber-300" />
            </div>
            <h3 class="text-xl font-bold">{{ rule.title }}</h3>
            <p class="mt-3 min-h-20 text-sm leading-6 text-stone-300">{{ rule.description }}</p>
            <div class="mt-5 grid grid-cols-2 gap-3">
              <div class="border border-white/10 p-3">
                <p class="text-xs text-stone-400">誤殺</p>
                <p class="mt-1 text-lg font-bold">{{ rule.falsePositive }}</p>
              </div>
              <div class="border border-white/10 p-3">
                <p class="text-xs text-stone-400">偵測率</p>
                <p class="mt-1 text-lg font-bold">{{ rule.recall }}</p>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>

    <footer class="border-t border-stone-200 bg-stone-100 py-8">
      <div class="mx-auto flex max-w-7xl flex-col gap-3 px-5 text-sm text-stone-600 md:flex-row md:items-center md:justify-between">
        <p>資料來源：<a href="https://github.com/LLMSystems/SEC-10-K-Structured-Extraction/blob/main/feedback/combined_validation_report.md" class="font-semibold text-teal-700 underline decoration-teal-300 underline-offset-4 hover:text-teal-900" target="_blank">https://github.com/LLMSystems/SEC-10-K-Structured-Extraction/blob/main/feedback/combined_validation_report.md</a></p>
      </div>
    </footer>
  </main>
</template>
