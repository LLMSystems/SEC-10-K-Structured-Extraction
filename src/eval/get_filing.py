"""Sample companies from SEC's company_tickers.json and fetch each one's most recent 10-K filing."""
import json
import random
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {'User-Agent': 'research milk333445@gmail.com', 'Accept': 'application/json'}
SAMPLE_SIZE = 1000
OUTPUT_PATH = 'company_tickers_sample500.json'
MAX_WORKERS = 8  # stays under SEC's 10 req/s limit

_print_lock = threading.Lock()


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sample_companies(seed=42):
    data = fetch_json('https://www.sec.gov/files/company_tickers.json')
    random.seed(seed)
    sample_keys = random.sample(list(data.keys()), SAMPLE_SIZE)
    sample = [data[k] for k in sample_keys]
    for item in sample:
        item['cik_str_padded'] = str(item['cik_str']).zfill(10)
    return sample


def latest_10k(cik):
    data = fetch_json(f'https://data.sec.gov/submissions/CIK{cik}.json')
    recent = data.get('filings', {}).get('recent', {})
    for form, accession, date in zip(
        recent.get('form', []),
        recent.get('accessionNumber', []),
        recent.get('filingDate', []),
    ):
        if form == '10-K':
            return accession, date  # already sorted newest first
    return None, None


def fetch_company(i, company):
    ticker = company['ticker']
    try:
        accession_number, filing_date = latest_10k(company['cik_str_padded'])
        status = accession_number or 'NO 10-K FOUND'
        with _print_lock:
            print(f'[{i:3d}/{SAMPLE_SIZE}] {ticker:<12} {status}  ({filing_date or "-"})')
    except Exception as e:
        accession_number, filing_date = None, None
        with _print_lock:
            print(f'[{i:3d}/{SAMPLE_SIZE}] {ticker:<12} ERROR: {e}')

    company['accession_number'] = accession_number
    company['filing_date'] = filing_date
    company['form_type'] = '10-K' if accession_number else None
    return i, company


def main():
    companies = sample_companies()
    print(f'Sampled {len(companies)} companies')

    results = [None] * len(companies)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_company, i, company): i
            for i, company in enumerate(companies, start=1)
        }
        for future in as_completed(futures):
            idx, company = future.result()
            results[idx - 1] = company

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    found = sum(1 for c in results if c['accession_number'])
    print(f'\nDone: {found}/{SAMPLE_SIZE} companies have a 10-K accession number. Saved to {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
