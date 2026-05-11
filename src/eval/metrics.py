"""
src/eval/metrics.py

評估腳本：將 pipeline 輸出與 ground truth 比對，計算各項指標並產生報告。

執行方式（CLI）：
    python -m src.eval.metrics \\
        --ground-truth eval_datasets/ground_truth \\
        --output eval_datasets/results

執行方式（Python）：
    from src.eval.metrics import MetricsRunner
    runner = MetricsRunner("eval_datasets/ground_truth", "eval_datasets/results")
    summary = runner.run()

指標說明（詳見 docs/eval_design.md）：
  Metric A  Status 準確率        — pred.status == gt.status
  Metric B  內容長度比            — pred_len / gt_len（gt 為 extracted 時）
  Metric C  頭尾比對              — GT 頭尾 150 字在 pred 中能否找到
  Metric D  Critical Regression  — GT extracted → pred missing/not_applicable
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models import FilingInput, FilingOutput, ItemResult
from src.pipeline import Pipeline

# Optional: rapidfuzz for fuzzy matching
try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── 調整參數 ───────────────────────────────────────────────
_HEAD_TAIL_CHARS     = 150    # 頭尾比對取樣字元數
_MATCH_MODE          = "fuzzy"  # "exact" 或 "fuzzy"（需要 rapidfuzz）
_FUZZY_THRESHOLD     = 90       # 模糊匹配閾值（0-100），預設 90
_LEN_RATIO_OK_LO     = 0.85   # 長度比正常下限
_LEN_RATIO_OK_HI     = 1.15   # 長度比正常上限
_LEN_RATIO_WARN_LO   = 0.70   # 長度比警告下限
_LEN_RATIO_WARN_HI   = 1.30   # 長度比警告上限
_ACC_FAIL_THRESHOLD  = 0.90   # Status 準確率低於此值 → FAIL
_ACC_WARN_THRESHOLD  = 0.95   # Status 準確率低於此值 → WARNING

# ── 嚴重程度 icon ──────────────────────────────────────────
_SEV_ICON   = {"critical": "🔴", "warning": "🟡", "info": "🟢", "pass": "✅"}
_LEVEL_ICON = {"PASS": "✅", "WARNING": "⚠️", "FAIL": "❌", "ERROR": "💥"}


# ══════════════════════════════════════════════════════════════
# 資料結構
# ══════════════════════════════════════════════════════════════

@dataclass
class ItemEvalResult:
    item_number:  str
    gt_status:    str
    pred_status:  str
    status_match: bool
    gt_len:       int
    pred_len:     int
    length_ratio: Optional[float]  # gt 為 extracted 時才計算
    head_match:   Optional[bool]   # gt 為 extracted 時才計算
    tail_match:   Optional[bool]   # gt 為 extracted 且 pred 也是 extracted 時才計算
    severity:     str              # "pass" / "info" / "warning" / "critical"
    note:         str              # 人類可讀說明


@dataclass
class FilingEvalResult:
    ticker:               str
    year:                 str
    gt_file:              str
    filing_info:          dict
    status_accuracy:      float
    critical_regressions: int           # -1 表示 pipeline 執行失敗
    items:                list[ItemEvalResult]
    level:                str           # "PASS" / "WARNING" / "FAIL" / "ERROR"
    error:                Optional[str] = None
    timing:               Optional[dict] = None  # fetch/preprocess/parse/postprocess 秒數


@dataclass
class EvalSummary:
    run_at:                    str
    gt_dir:                    str
    filings:                   list[FilingEvalResult]
    overall_status_accuracy:   float
    total_critical_regressions: int
    total_warnings:            int
    level:                     str
    avg_timing:                Optional[dict] = None  # 各步驟平均耗時（秒）


# ══════════════════════════════════════════════════════════════
# 輔助函式
# ══════════════════════════════════════════════════════════════

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub(" ", text)


def _normalize(text: str) -> str:
    """壓縮連續空白為單一空格，供字串比對使用。"""
    return re.sub(r"\s+", " ", text).strip()


def _check_head_tail(
    gt_content: str,
    pred_content: str,
    n: int = _HEAD_TAIL_CHARS,
    mode: str = _MATCH_MODE,
    threshold: int = _FUZZY_THRESHOLD,
) -> tuple[bool, bool]:
    """
    去除 HTML tag 並壓縮空白後，比對 GT 頭尾是否出現在 pred 中。
    
    Args:
        gt_content: Ground truth 內容
        pred_content: 預測內容
        n: 取樣字元數（預設 150）
        mode: 比對模式 "exact" 或 "fuzzy"（預設 "fuzzy"）
        threshold: 模糊匹配閾值 0-100（預設 90）
    
    Returns:
        (head_match, tail_match) 兩個布林值
    """
    gt_plain   = _normalize(_strip_html(gt_content))
    pred_plain = _normalize(_strip_html(pred_content))

    # gt 太短時（小於 n），直接比整段
    head = gt_plain[:n]
    tail = gt_plain[-n:] if len(gt_plain) >= n else gt_plain

    # 選擇比對模式
    if mode == "fuzzy" and RAPIDFUZZ_AVAILABLE:
        # 使用 rapidfuzz 的 partial_ratio 進行模糊匹配
        head_score = fuzz.partial_ratio(head, pred_plain)
        tail_score = fuzz.partial_ratio(tail, pred_plain)
        return (head_score >= threshold), (tail_score >= threshold)
    else:
        # 降級到完全匹配（預設行為）
        if mode == "fuzzy" and not RAPIDFUZZ_AVAILABLE:
            logger.warning(
                "rapidfuzz 未安裝，降級使用完全匹配模式。"
                "執行 'pip install rapidfuzz' 以啟用模糊匹配。"
            )
        return (head in pred_plain), (tail in pred_plain)


def _severity_and_note(
    gt_status:    str,
    pred_status:  str,
    status_match: bool,
    length_ratio: Optional[float],
    head_match:   Optional[bool],
    tail_match:   Optional[bool],
) -> tuple[str, str]:
    """
    根據 status 比對與內容比對，決定嚴重程度和說明。

    Returns:
        (severity, note)  severity ∈ {"pass", "info", "warning", "critical"}
    """
    # ── Status 不一致 ─────────────────────────────────────────
    if not status_match:
        # Critical：GT extracted → pred 遺失
        if gt_status == "extracted" and pred_status in ("missing", "not_applicable"):
            return "critical", f"GT={gt_status} → Pred={pred_status}（Item 遺失）"

        # Warning：GT missing → pred extracted（可能抓錯邊界，需人工確認）
        if gt_status == "missing" and pred_status == "extracted":
            return "warning", (
                f"GT={gt_status} → Pred={pred_status}"
                "（GT 確認無此 Item，但 pred 找到了內容，請人工確認）"
            )

        # Warning：GT extracted → pred 其他錯誤 status
        if gt_status == "extracted":
            return "warning", f"GT={gt_status} → Pred={pred_status}"

        # Warning：GT incorporated_by_reference → pred 其他
        if gt_status == "incorporated_by_reference":
            return "warning", f"GT={gt_status} → Pred={pred_status}"

        # Info：語義相近的差異
        semantic_close = {
            frozenset({"reserved", "not_applicable"}),
            frozenset({"missing", "not_applicable"}),
            frozenset({"missing", "reserved"}),
        }
        if frozenset({gt_status, pred_status}) in semantic_close:
            return "info", f"GT={gt_status} → Pred={pred_status}（語義相近，影響較小）"

        # 其他 status 不一致
        return "warning", f"GT={gt_status} → Pred={pred_status}"

    # ── Status 一致，只有 extracted 需要進一步檢查內容 ─────────
    if gt_status != "extracted":
        return "pass", ""

    notes: list[str] = []

    # Length ratio
    if length_ratio is not None:
        if length_ratio < _LEN_RATIO_WARN_LO:
            notes.append(f"內容嚴重過短（ratio={length_ratio:.2f}，< {_LEN_RATIO_WARN_LO}）")
        elif length_ratio > _LEN_RATIO_WARN_HI:
            notes.append(f"內容嚴重過長（ratio={length_ratio:.2f}，> {_LEN_RATIO_WARN_HI}）")
        elif length_ratio < _LEN_RATIO_OK_LO:
            notes.append(f"內容略短（ratio={length_ratio:.2f}）")
        elif length_ratio > _LEN_RATIO_OK_HI:
            notes.append(f"內容略長（ratio={length_ratio:.2f}）")

    # Head / tail
    if head_match is False:
        notes.append("頭部不吻合（起始邊界可能偏移）")
    if tail_match is False:
        notes.append("尾部不吻合（結束邊界可能截斷或越界）")

    if not notes:
        return "pass", ""

    return "warning", "；".join(notes)


def _classify_item(gt: ItemResult, pred: ItemResult) -> ItemEvalResult:
    """比較單一 Item 的 GT 與預測，回傳 ItemEvalResult。"""
    gt_len   = len(gt.content_text)   if gt.content_text   else 0
    pred_len = len(pred.content_text) if pred.content_text else 0

    status_match = (gt.status == pred.status)

    # length_ratio 與頭尾比對只在 gt 為 extracted 時計算
    length_ratio: Optional[float] = None
    head_match:   Optional[bool]  = None
    tail_match:   Optional[bool]  = None

    if gt.status == "extracted" and gt.content_text:
        length_ratio = round(pred_len / gt_len, 4) if gt_len > 0 else None
        if pred.status == "extracted" and pred.content_text:
            head_match, tail_match = _check_head_tail(gt.content_text, pred.content_text)

    severity, note = _severity_and_note(
        gt.status, pred.status, status_match,
        length_ratio, head_match, tail_match,
    )

    return ItemEvalResult(
        item_number  = gt.item_number,
        gt_status    = gt.status,
        pred_status  = pred.status,
        status_match = status_match,
        gt_len       = gt_len,
        pred_len     = pred_len,
        length_ratio = length_ratio,
        head_match   = head_match,
        tail_match   = tail_match,
        severity     = severity,
        note         = note,
    )


# ══════════════════════════════════════════════════════════════
# 主類別
# ══════════════════════════════════════════════════════════════

class MetricsRunner:
    """
    讀取 ground truth → 重跑 pipeline → 計算指標 → 產生報告。

    Args:
        gt_dir:     ground truth 根目錄（結構：{TICKER}/{YEAR}/*.json）
        output_dir: 報告輸出根目錄，每次執行建立時間戳子目錄
        pipeline:   自訂 Pipeline 實例；None 則使用預設設定
    """

    def __init__(
        self,
        gt_dir:     str | Path,
        output_dir: str | Path,
        pipeline:   Pipeline | None = None,
    ) -> None:
        self.gt_dir     = Path(gt_dir)
        self.output_dir = Path(output_dir)
        self.pipeline   = pipeline or Pipeline()

    def run(self) -> EvalSummary:
        """執行完整評估，回傳 EvalSummary 並將報告寫到磁碟。"""
        run_at     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        result_dir = self.output_dir / run_at
        (result_dir / "per_filing").mkdir(parents=True, exist_ok=True)

        if not self.gt_dir.exists():
            raise FileNotFoundError(
                f"Ground truth 目錄不存在：{self.gt_dir}\n"
                f"請確認路徑正確，或用 --ground-truth 指定正確目錄。"
            )

        gt_files = sorted(self.gt_dir.rglob("*.json"))
        if not gt_files:
            raise FileNotFoundError(
                f"在 {self.gt_dir} 找不到任何 ground truth JSON。\n"
                f"預期結構：{{TICKER}}/{{YEAR}}/{{CIK}}_{{FY_END}}_{{ACC}}.json"
            )

        logger.info(f"找到 {len(gt_files)} 份 ground truth，開始評估…")

        filing_results: list[FilingEvalResult] = []
        for gt_path in gt_files:
            result = self._eval_one(gt_path)
            filing_results.append(result)

            # 儲存 per_filing JSON
            out_name = f"{result.ticker}_{result.year}.json"
            (result_dir / "per_filing" / out_name).write_text(
                json.dumps(asdict(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.info(
                f"  [{result.level}] {result.ticker} {result.year} "
                f"status_acc={result.status_accuracy:.1%}  "
                f"critical={result.critical_regressions}"
            )

        summary = self._aggregate(filing_results, run_at)

        (result_dir / "summary.json").write_text(
            json.dumps(asdict(summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md = self._render_markdown(summary)
        (result_dir / "summary.md").write_text(md, encoding="utf-8")

        logger.info(
            f"\n整體結果：{summary.level}  "
            f"status_acc={summary.overall_status_accuracy:.1%}  "
            f"critical={summary.total_critical_regressions}  "
            f"warnings={summary.total_warnings}"
        )
        logger.info(f"報告已存至：{result_dir}")
        return summary

    # ──────────────────────────────────────────────────────────
    # 單筆評估
    # ──────────────────────────────────────────────────────────

    def _eval_one(self, gt_path: Path) -> FilingEvalResult:
        """對單一 ground truth JSON 執行評估。"""
        # 從路徑取 ticker / year（ground_truth/TICKER/YEAR/file.json）
        parts  = gt_path.parts
        ticker = parts[-3]
        year   = parts[-2]
        label  = f"{ticker} {year}"

        logger.info(f"評估：{label}  ({gt_path.name})")

        # 讀取 GT
        gt_data   = json.loads(gt_path.read_text(encoding="utf-8"))
        gt_output = FilingOutput.model_validate(gt_data)
        fi        = gt_data["filing_info"]

        # 重跑 pipeline
        try:
            pred_output = self.pipeline.run(
                FilingInput(
                    cik=fi["cik"],
                    accession_number=fi["accession_number"],
                )
            )
        except Exception as exc:
            logger.error(f"  Pipeline 失敗 ({label})：{exc}")
            return FilingEvalResult(
                ticker=ticker,
                year=year,
                gt_file=str(gt_path),
                filing_info=fi,
                status_accuracy=0.0,
                critical_regressions=-1,
                items=[],
                level="ERROR",
                error=str(exc),
            )

        # 建立 pred item_number → ItemResult 的 map
        pred_map: dict[str, ItemResult] = {
            item.item_number: item for item in pred_output.items
        }

        # 逐 item 比對（以 GT 為基準）
        item_results: list[ItemEvalResult] = []
        for gt_item in gt_output.items:
            pred_item = pred_map.get(gt_item.item_number)
            if pred_item is None:
                # pred 輸出中完全沒有這個 item_number（理論上不應發生）
                gt_len = len(gt_item.content_text) if gt_item.content_text else 0
                item_results.append(ItemEvalResult(
                    item_number  = gt_item.item_number,
                    gt_status    = gt_item.status,
                    pred_status  = "<absent>",
                    status_match = False,
                    gt_len       = gt_len,
                    pred_len     = 0,
                    length_ratio = None,
                    head_match   = None,
                    tail_match   = None,
                    severity     = "critical",
                    note         = "Pred 輸出中完全缺少此 item_number",
                ))
            else:
                item_results.append(_classify_item(gt_item, pred_item))

        # 計算 filing 層級指標
        total    = len(item_results)
        correct  = sum(1 for r in item_results if r.status_match)
        criticals = sum(1 for r in item_results if r.severity == "critical")
        accuracy = correct / total if total > 0 else 0.0
        level    = self._filing_level(accuracy, criticals, item_results)

        timing_dict: Optional[dict] = None
        if pred_output.timing is not None:
            t = pred_output.timing
            timing_dict = {
                "fetch_html_sec":  t.fetch_html_sec,
                "preprocess_sec":  t.preprocess_sec,
                "parse_sec":       t.parse_sec,
                "postprocess_sec": t.postprocess_sec,
                "total_sec":       t.total_sec,
            }

        return FilingEvalResult(
            ticker               = ticker,
            year                 = year,
            gt_file              = str(gt_path),
            filing_info          = fi,
            status_accuracy      = round(accuracy, 4),
            critical_regressions = criticals,
            items                = item_results,
            level                = level,
            timing               = timing_dict,
        )

    @staticmethod
    def _filing_level(
        accuracy:  float,
        criticals: int,
        items:     list[ItemEvalResult],
    ) -> str:
        if criticals > 0 or accuracy < _ACC_FAIL_THRESHOLD:
            return "FAIL"
        has_warning = (
            accuracy < _ACC_WARN_THRESHOLD
            or any(i.severity == "warning" for i in items)
            or any(
                i.length_ratio is not None
                and not (_LEN_RATIO_WARN_LO <= i.length_ratio <= _LEN_RATIO_WARN_HI)
                for i in items
            )
        )
        return "WARNING" if has_warning else "PASS"

    # ──────────────────────────────────────────────────────────
    # 整體彙整
    # ──────────────────────────────────────────────────────────

    def _aggregate(
        self,
        results: list[FilingEvalResult],
        run_at:  str,
    ) -> EvalSummary:
        valid = [r for r in results if r.level != "ERROR"]

        if valid:
            total_items   = sum(len(r.items) for r in valid)
            total_correct = sum(
                sum(1 for i in r.items if i.status_match) for r in valid
            )
            overall_acc = total_correct / total_items if total_items else 0.0
        else:
            overall_acc = 0.0

        total_critical = sum(
            r.critical_regressions for r in results if r.critical_regressions > 0
        )
        total_warnings = sum(
            sum(1 for i in r.items if i.severity == "warning") for r in valid
        )

        if any(r.level in ("FAIL", "ERROR") for r in results):
            level = "FAIL"
        elif overall_acc < _ACC_WARN_THRESHOLD or total_warnings > 0:
            level = "WARNING"
        else:
            level = "PASS"

        # 平均耗時（只取有 timing 資料的 filing）
        timed = [r.timing for r in results if r.timing is not None]
        avg_timing: Optional[dict] = None
        if timed:
            keys = ("fetch_html_sec", "preprocess_sec", "parse_sec", "postprocess_sec", "total_sec")
            avg_timing = {
                k: round(statistics.mean(t[k] for t in timed), 3)
                for k in keys
            }

        return EvalSummary(
            run_at                     = run_at,
            gt_dir                     = str(self.gt_dir),
            filings                    = results,
            overall_status_accuracy    = round(overall_acc, 4),
            total_critical_regressions = total_critical,
            total_warnings             = total_warnings,
            level                      = level,
            avg_timing                 = avg_timing,
        )

    # ──────────────────────────────────────────────────────────
    # Markdown 報告
    # ──────────────────────────────────────────────────────────

    def _render_markdown(self, summary: EvalSummary) -> str:
        lines: list[str] = []
        level_icon = _LEVEL_ICON
        sev_icon   = _SEV_ICON

        # ── 標題 ──────────────────────────────────────────────
        ts = summary.run_at.replace("_", " ")
        lines += [
            f"# 評估結果 — {ts}",
            "",
            f"## 整體結果：{level_icon.get(summary.level, '')} {summary.level}",
            "",
            "| 指標 | 數值 |",
            "|---|---|",
            f"| Status 準確率 | {summary.overall_status_accuracy:.1%} |",
            f"| Critical Regressions | {summary.total_critical_regressions} |",
            f"| Warning 數 | {summary.total_warnings} |",
            f"| 評估 Filing 數 | {len(summary.filings)} |",
            "",
        ]

        # ── 平均耗時 ──────────────────────────────────────────────
        if summary.avg_timing:
            at = summary.avg_timing
            lines += [
                "## 平均耗時",
                "",
                "| 步驟 | 平均耗時（秒） |",
                "|---|---|",
                f"| 下載 HTML | {at['fetch_html_sec']:.3f} |",
                f"| 預處理（preprocess） | {at['preprocess_sec']:.3f} |",
                f"| 解析（parse） | {at['parse_sec']:.3f} |",
                f"| 後處理（postprocess） | {at['postprocess_sec']:.3f} |",
                f"| **合計** | **{at['total_sec']:.3f}** |",
                "",
            ]

        # ── 收集所有有效 filing 的 items ──────────────────────────
        valid_items = [
            item
            for r in summary.filings if r.level != "ERROR"
            for item in r.items
        ]

        # ── Status 混淆矩陣 ────────────────────────────────────
        confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for item in valid_items:
            confusion[item.gt_status][item.pred_status] += 1

        all_statuses = sorted(
            {s for item in valid_items for s in (item.gt_status, item.pred_status)}
        )
        if all_statuses:
            header_row = "| GT \\ Pred | " + " | ".join(all_statuses) + " |"
            sep_row    = "|" + "---|" * (len(all_statuses) + 1)
            lines += ["## Status 混淆矩陣", "", header_row, sep_row]
            for gt_s in all_statuses:
                cells = " | ".join(
                    str(confusion[gt_s].get(pred_s, 0)) for pred_s in all_statuses
                )
                lines.append(f"| **{gt_s}** | {cells} |")
            lines.append("")

        # ── 內容品質統計（長度比 & 頭尾比對）──────────────────────
        ratios = [i.length_ratio for i in valid_items if i.length_ratio is not None]
        heads  = [i.head_match  for i in valid_items if i.head_match  is not None]
        tails  = [i.tail_match  for i in valid_items if i.tail_match  is not None]

        if ratios or heads:
            lines += ["## 內容品質統計", ""]

        if ratios:
            ratios_sorted = sorted(ratios)
            n = len(ratios_sorted)
            p10 = ratios_sorted[max(0, int(n * 0.10) - 1)]
            p90 = ratios_sorted[min(n - 1, int(n * 0.90))]

            cnt_sev_short  = sum(1 for r in ratios if r < _LEN_RATIO_WARN_LO)
            cnt_short      = sum(1 for r in ratios if _LEN_RATIO_WARN_LO <= r < _LEN_RATIO_OK_LO)
            cnt_ok         = sum(1 for r in ratios if _LEN_RATIO_OK_LO <= r <= _LEN_RATIO_OK_HI)
            cnt_long       = sum(1 for r in ratios if _LEN_RATIO_OK_HI < r <= _LEN_RATIO_WARN_HI)
            cnt_sev_long   = sum(1 for r in ratios if r > _LEN_RATIO_WARN_HI)

            lines += [
                "**Length Ratio 統計（pred 長度 / GT 長度，僅 extracted items）：**",
                "",
                "| 指標 | 數值 |",
                "|---|---|",
                f"| 樣本數 | {n} |",
                f"| 平均值 | {statistics.mean(ratios):.3f} |",
                f"| 中位數 | {statistics.median(ratios):.3f} |",
                f"| P10 | {p10:.3f} |",
                f"| P90 | {p90:.3f} |",
                "",
                "| 範圍 | 條件 | 數量 | 佔比 |",
                "|---|---|---|---|",
                f"| 🔴 嚴重過短 | < {_LEN_RATIO_WARN_LO} | {cnt_sev_short} | {cnt_sev_short/n:.1%} |",
                f"| 🟡 略短 | {_LEN_RATIO_WARN_LO}–{_LEN_RATIO_OK_LO} | {cnt_short} | {cnt_short/n:.1%} |",
                f"| ✅ 正常 | {_LEN_RATIO_OK_LO}–{_LEN_RATIO_OK_HI} | {cnt_ok} | {cnt_ok/n:.1%} |",
                f"| 🟡 略長 | {_LEN_RATIO_OK_HI}–{_LEN_RATIO_WARN_HI} | {cnt_long} | {cnt_long/n:.1%} |",
                f"| 🔴 嚴重過長 | > {_LEN_RATIO_WARN_HI} | {cnt_sev_long} | {cnt_sev_long/n:.1%} |",
                "",
            ]

        if heads:
            head_rate = sum(heads) / len(heads)
            tail_rate = sum(tails) / len(tails) if tails else None
            lines += [
                "**頭尾比對通過率（僅 GT & Pred 皆為 extracted）：**",
                "",
                "| 比對位置 | 通過數 | 總數 | 通過率 |",
                "|---|---|---|---|",
                f"| 頭部（前 {_HEAD_TAIL_CHARS} 字）"
                f"| {sum(heads)} | {len(heads)} | {head_rate:.1%} |",
            ]
            if tail_rate is not None:
                lines.append(
                    f"| 尾部（後 {_HEAD_TAIL_CHARS} 字）"
                    f"| {sum(tails)} | {len(tails)} | {tail_rate:.1%} |"
                )
            lines.append("")

        # ── 各 Item 錯誤率排行 ─────────────────────────────────
        item_stat: dict[str, list[bool]] = defaultdict(list)
        for item in valid_items:
            item_stat[item.item_number].append(item.status_match)

        if item_stat:
            ranked = sorted(
                item_stat.items(),
                key=lambda kv: (sum(1 for v in kv[1] if not v) / len(kv[1]), kv[0]),
                reverse=True,
            )
            lines += [
                "## 各 Item 錯誤率排行",
                "",
                "| Item | 錯誤數 | 總數 | 錯誤率 |",
                "|---|---|---|---|",
            ]
            for item_num, matches in ranked:
                errors = sum(1 for v in matches if not v)
                rate   = errors / len(matches)
                bar    = "🔴" if rate >= 0.5 else ("🟡" if rate > 0 else "✅")
                lines.append(
                    f"| {item_num} | {errors} | {len(matches)} | {bar} {rate:.1%} |"
                )
            lines.append("")

        # ── 各 Filing 概覽 ─────────────────────────────────────
        lines += [
            "## 各 Filing 概覽",
            "",
            "| Ticker | 年份 | Filer Category | Status 準確率 | Critical | Warning | 等級 |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in summary.filings:
            warnings = sum(1 for i in r.items if i.severity == "warning")
            fcat     = r.filing_info.get("filer_category") or "—"
            icon     = level_icon.get(r.level, r.level)
            acc_str  = f"{r.status_accuracy:.1%}" if r.level != "ERROR" else "—"
            crit_str = str(r.critical_regressions) if r.critical_regressions >= 0 else "—"
            lines.append(
                f"| {r.ticker} | {r.year} | {fcat} "
                f"| {acc_str} | {crit_str} | {warnings} | {icon} {r.level} |"
            )

        lines.append("")

        # ── 問題明細（只列出非 PASS 的 filing）───────────────────
        problem_filings = [r for r in summary.filings if r.level != "PASS"]
        if problem_filings:
            lines += ["## 問題明細", ""]

            for r in problem_filings:
                lines += [
                    f"### {r.ticker} {r.year} — {level_icon.get(r.level, '')} {r.level}",
                    "",
                ]

                # Pipeline 失敗
                if r.level == "ERROR":
                    lines += [f"Pipeline 執行失敗：`{r.error}`", ""]
                    continue

                # Status 問題
                problem_items = [i for i in r.items if i.severity != "pass"]
                if problem_items:
                    lines += [
                        "**Status 比對：**",
                        "",
                        "| Item | GT Status | Pred Status | 嚴重程度 | 說明 |",
                        "|---|---|---|---|---|",
                    ]
                    for item in problem_items:
                        icon = sev_icon.get(item.severity, "")
                        note = item.note or "—"
                        lines.append(
                            f"| {item.item_number} | {item.gt_status} | {item.pred_status} "
                            f"| {icon} {item.severity} | {note} |"
                        )
                    lines.append("")

                # Length Ratio 異常彙整（status 正確但長度有問題）
                ratio_issues = [
                    i for i in r.items
                    if i.length_ratio is not None
                    and not (_LEN_RATIO_OK_LO <= i.length_ratio <= _LEN_RATIO_OK_HI)
                ]
                if ratio_issues:
                    lines += [
                        "**Length Ratio 異常（±15% 以上）：**",
                        "",
                        "| Item | GT 長度 | Pred 長度 | 比值 | 方向 |",
                        "|---|---|---|---|---|",
                    ]
                    for i in ratio_issues:
                        direction = "截短 ⬇️" if i.length_ratio < 1.0 else "越界 ⬆️"
                        warn_mark = "⚠️" if not (_LEN_RATIO_WARN_LO <= i.length_ratio <= _LEN_RATIO_WARN_HI) else ""
                        lines.append(
                            f"| {i.item_number} | {i.gt_len:,} | {i.pred_len:,} "
                            f"| {i.length_ratio:.2f} {warn_mark} | {direction} |"
                        )
                    lines.append("")

                # 頭尾比對失敗（status 正確且長度正常，但邊界偏移）
                boundary_issues = [
                    i for i in r.items
                    if i.status_match
                    and (i.head_match is False or i.tail_match is False)
                    and i not in ratio_issues  # 已在 ratio 中顯示的不重複
                ]
                if boundary_issues:
                    lines += [
                        "**頭尾比對失敗：**",
                        "",
                        "| Item | 頭部 | 尾部 |",
                        "|---|---|---|",
                    ]
                    for i in boundary_issues:
                        head_str = "✅" if i.head_match else "❌"
                        tail_str = "✅" if i.tail_match else "❌"
                        lines.append(f"| {i.item_number} | {head_str} | {tail_str} |")
                    lines.append("")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════

def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="10-K Parser 評估：將 pipeline 輸出與 ground truth 比對"
    )
    parser.add_argument(
        "--ground-truth", "-g",
        default="eval_datasets/ground_truth",
        help="Ground truth 根目錄（預設 eval_datasets/ground_truth）",
    )
    parser.add_argument(
        "--output", "-o",
        default="eval_datasets/results",
        help="報告輸出根目錄（預設 eval_datasets/results）",
    )
    args = parser.parse_args()

    runner = MetricsRunner(
        gt_dir     = args.ground_truth,
        output_dir = args.output,
    )
    summary = runner.run()

    # Exit code：FAIL / ERROR → 1，其餘 → 0（方便 CI 使用）
    import sys
    sys.exit(1 if summary.level in ("FAIL", "ERROR") else 0)


if __name__ == "__main__":
    main()
