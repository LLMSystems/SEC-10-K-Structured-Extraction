"""
Eval Runner
批量對多家公司的最近 N 份 10-K 執行 pipeline，並把結果存到指定目錄。

使用方式（CLI）：
    python -m src.eval.runner --config eval_config.json --output eval_results/

使用方式（Python）：
    from src.eval.runner import EvalRunner
    runner = EvalRunner(output_dir="eval_results")
    runner.run_all(config)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field

import requests

from src.models import FilingInput
from src.pipeline import Pipeline

logger = logging.getLogger(__name__)

USER_AGENT = "10K-Parser contact@example.com"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


# ──────────────────────────────────────────────────────────
# 資料結構
# ──────────────────────────────────────────────────────────

@dataclass
class CompanyConfig:
    ticker: str
    cik: str
    counts: int


@dataclass
class FilingTarget:
    ticker: str
    cik: str
    accession_number: str
    report_date: str          # fiscal year end，例如 "2024-09-28"
    form_type: str            # "10-K" / "10-K/A"


@dataclass
class RunResult:
    target: FilingTarget
    success: bool
    output_dir: Path | None = None
    error: str | None = None


@dataclass
class EvalSummary:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[RunResult] = field(default_factory=list)


# ──────────────────────────────────────────────────────────
# 主類別
# ──────────────────────────────────────────────────────────

class EvalRunner:
    """
    批量評估執行器。

    Args:
        output_dir: 結果根目錄，每家公司會建立子目錄。
        pipeline:   自訂 Pipeline，預設 None 使用預設設定。
        include_amended: 是否包含 10-K/A（修正版），預設 False。
        delay:      每次 API 請求間的延遲秒數（避免觸發 SEC rate limit）。
    """

    def __init__(
        self,
        output_dir: str | Path = "eval_results",
        pipeline: Pipeline | None = None,
        include_amended: bool = False,
        delay: float = 0.2,
    ):
        self.output_dir = Path(output_dir)
        self.pipeline = pipeline or Pipeline()
        self.include_amended = include_amended
        self.delay = delay

    # ── 公開 API ──────────────────────────────────────────

    def run_all(self, config: dict) -> EvalSummary:
        """
        執行所有公司的評估。

        Args:
            config: 格式如下
                {
                    "AAPL": {"cik": "0000320193", "counts": 10},
                    ...
                }
        """
        companies = self._parse_config(config)
        summary = EvalSummary()

        for company in companies:
            logger.info(f"═══ {company.ticker} (CIK={company.cik}) ═══")
            targets = self._fetch_targets(company)
            logger.info(f"  找到 {len(targets)} 份 10-K，準備執行 {len(targets)} 份")

            for target in targets:
                result = self._run_one(target)
                summary.results.append(result)
                summary.total += 1
                if result.success:
                    summary.succeeded += 1
                else:
                    summary.failed += 1

        self._print_summary(summary)
        return summary

    def run_from_file(self, config_path: str | Path) -> EvalSummary:
        """從 JSON 檔讀取設定並執行。"""
        config_path = Path(config_path)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return self.run_all(config)

    # ── 內部方法 ──────────────────────────────────────────

    def _parse_config(self, config: dict) -> list[CompanyConfig]:
        result = []
        for ticker, info in config.items():
            result.append(CompanyConfig(
                ticker=ticker.upper(),
                cik=info["cik"].strip().lstrip("0").zfill(10),
                counts=int(info.get("counts", 1)),
            ))
        return result

    def _fetch_targets(self, company: CompanyConfig) -> list[FilingTarget]:
        """
        從 EDGAR Submissions API 取得最近 N 份 10-K 的 accession number。
        """
        url = SUBMISSIONS_URL.format(cik=company.cik)
        time.sleep(self.delay)
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
        )
        resp.raise_for_status()
        sub = resp.json()

        recent = sub.get("filings", {}).get("recent", {})
        form_types    = recent.get("form", [])
        accessions    = recent.get("accessionNumber", [])
        report_dates  = recent.get("reportDate", [])

        allowed_forms = {"10-K", "10-K/A"} if self.include_amended else {"10-K"}

        targets: list[FilingTarget] = []
        for form, acc, date in zip(form_types, accessions, report_dates):
            if form in allowed_forms:
                targets.append(FilingTarget(
                    ticker=company.ticker,
                    cik=company.cik,
                    accession_number=acc,
                    report_date=date,
                    form_type=form,
                ))
            if len(targets) >= company.counts:
                break

        return targets

    def _run_one(self, target: FilingTarget) -> RunResult:
        """對單一 filing 執行 pipeline 並儲存結果。"""
        label = f"{target.ticker} {target.report_date} ({target.accession_number})"
        logger.info(f"  → 執行：{label}")

        # 每家公司一個子目錄，再依年份分層
        year = target.report_date[:4]  # "2024-09-28" → "2024"
        company_dir = self.output_dir / target.ticker / year
        company_dir.mkdir(parents=True, exist_ok=True)

        try:
            filing_input = FilingInput(
                cik=target.cik,
                accession_number=target.accession_number,
            )
            self.pipeline.run(filing_input, save_to=company_dir)
            logger.info(f"    ✅ 完成：{label}")
            return RunResult(target=target, success=True, output_dir=company_dir)

        except Exception as e:
            logger.error(f"    ❌ 失敗：{label} — {e}")
            self._save_error(target, company_dir, str(e))
            return RunResult(target=target, success=False, error=str(e))

    def _save_error(self, target: FilingTarget, output_dir: Path, error: str) -> None:
        """把失敗記錄存成 JSON，方便事後查看。"""
        acc_clean = target.accession_number.replace("-", "")
        error_path = output_dir / f"{target.cik}_{target.report_date}_{acc_clean}_ERROR.json"
        error_path.write_text(
            json.dumps({
                "ticker": target.ticker,
                "cik": target.cik,
                "accession_number": target.accession_number,
                "report_date": target.report_date,
                "error": error,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _print_summary(self, summary: EvalSummary) -> None:
        logger.info("═" * 50)
        logger.info(f"評估完成：共 {summary.total} 份，成功 {summary.succeeded}，失敗 {summary.failed}")
        if summary.failed:
            logger.info("失敗清單：")
            for r in summary.results:
                if not r.success:
                    logger.info(f"  - {r.target.ticker} {r.target.report_date} {r.target.accession_number}: {r.error}")
        logger.info("═" * 50)


# ──────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="批量執行 10-K 結構化抽取評估")
    parser.add_argument("--config",  required=True, help="eval config JSON 路徑")
    parser.add_argument("--output",  default="eval_results", help="結果輸出目錄（預設 eval_results/）")
    parser.add_argument("--amended", action="store_true", help="是否包含 10-K/A 修正版")
    args = parser.parse_args()

    runner = EvalRunner(
        output_dir=args.output,
        include_amended=args.amended,
    )
    runner.run_from_file(args.config)


if __name__ == "__main__":
    main()
