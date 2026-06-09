import asyncio, json
from collections import Counter
from pathlib import Path
from src.async_pipeline import AsyncPipeline
from src.pipeline import FilingInput

with open('company_tickers_sample500.json', encoding='utf-8') as f:
    companies = json.load(f)

SEV_ORDER = {'error': 0, 'warning': 1, 'info': 2, 'ok': 3}
candidates = [c for c in companies if c.get('accession_number')]
CONCURRENCY = 8

CACHE_DIR = Path('cache')
(CACHE_DIR / 'submissions').mkdir(parents=True, exist_ok=True)
(CACHE_DIR / 'html').mkdir(parents=True, exist_ok=True)


class CachedAsyncPipeline(AsyncPipeline):
    """覆寫下載方法：先讀本地快取，沒有才打 SEC 並存檔，重跑不用重新下載。"""

    async def _get_submissions_async(self, cik_padded: str) -> dict:
        cache_file = CACHE_DIR / 'submissions' / f'{cik_padded}.json'
        if cache_file.exists():
            print(f'  [cache hit ] submissions {cik_padded}')
            return json.loads(cache_file.read_text(encoding='utf-8'))
        print(f'  [cache miss] submissions {cik_padded} -> 下載中...')
        data = await super()._get_submissions_async(cik_padded)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return data

    async def _fetch_html_async(self, url: str) -> bytes:
        name = url.rstrip('/').split('/')[-1]
        cache_file = CACHE_DIR / 'html' / name
        if cache_file.exists():
            print(f'  [cache hit ] html {name}')
            return cache_file.read_bytes()
        print(f'  [cache miss] html {name} -> 下載中...')
        content = await super()._fetch_html_async(url)
        cache_file.write_bytes(content)
        return content


print(f'Processing {len(candidates)} companies with concurrency={CONCURRENCY} (async, cached)...')

pipeline = CachedAsyncPipeline()  # 共用一個實例：HTTP 走 async I/O，CPU 步驟丟 thread executor
sem = asyncio.Semaphore(CONCURRENCY)

async def process(company):
    cik = company['cik_str_padded']
    acc = company['accession_number']
    ticker = company['ticker']
    try:
        async with sem:
            output = await pipeline.run_async(FilingInput(cik=cik, accession_number=acc))
        q = output.quality
        if q.counts.get('error', 0) > 0:
            worst = 'error'
        elif q.counts.get('warning', 0) > 0:
            worst = 'warning'
        elif q.counts.get('info', 0) > 0:
            worst = 'info'
        else:
            worst = 'ok'
        return {
            'ticker': ticker,
            'title': company['title'],
            'cik': cik,
            'accession_number': acc,
            'filing_date': company.get('filing_date'),
            'worst_severity': worst,
            'is_valid': q.is_valid,
            'score': round(q.score, 4),
            'parser': q.parser_name,
            'confidence': round(q.parser_confidence, 4),
            'found_items': q.found_item_count,
            'expected_items': q.expected_item_count,
            'missing_required': q.missing_required_items,
            'counts': q.counts,
            'flags': [
                {'code': f.code, 'severity': f.severity, 'message': f.message,
                 'item': getattr(f, 'item_number', None)}
                for f in q.flags
            ],
        }
    except Exception as e:
        return {
            'ticker': ticker,
            'title': company['title'],
            'cik': cik,
            'accession_number': acc,
            'filing_date': company.get('filing_date'),
            'worst_severity': 'pipeline_error',
            'error_message': str(e),
        }

async def main():
    results = []
    tasks = [asyncio.create_task(process(c)) for c in candidates]
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        sev = r['worst_severity']
        score = r.get('score', '-')
        err = r.get('counts', {}).get('error', 0)
        warn = r.get('counts', {}).get('warning', 0)
        print(f'[{i:2d}/{len(candidates)}] {r["ticker"]:<12} {sev:14s} score={score} e={err} w={warn}')
        results.append(r)
    return results

results = asyncio.run(main())

results.sort(key=lambda r: (SEV_ORDER.get(r['worst_severity'], 9), -r.get('score', 0)))

with open('validation_results_500.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f'\nSaved {len(results)} results to validation_results_500.json')
for sev, n in Counter(r['worst_severity'] for r in results).most_common():
    print(f'  {sev:15s}: {n}')