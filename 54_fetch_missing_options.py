#!/usr/bin/env python3
"""
54_fetch_missing_options.py
Fetches 1-min option bars from Polygon for all signal dates that are missing
option data in the cache. Run this from the terminal with internet access.

Usage:
    python3 54_fetch_missing_options.py

This will:
1. Read missing_option_contracts.json (420 contracts across 209 dates)
2. Fetch 1-min bars from Polygon for each contract
3. Save to options_cache/ in the same format used by the backtest
4. Print progress and summary

Estimated time: ~5-10 minutes (420 API calls with rate limiting)
API calls: 420 (well within top-tier plan limits)
"""

import json
import os
import time
import sys
import urllib.request
import urllib.error

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "options_cache")
CONTRACTS_FILE = os.path.join(BASE, "missing_option_contracts.json")

# Rate limiting: Polygon top-tier allows high throughput,
# but we add a small delay to be respectful
DELAY_BETWEEN_CALLS = 0.15  # seconds


def fetch_option_bars(ticker, date):
    """
    Fetch 1-min bars for an option contract from Polygon.
    Returns list of bar dicts or None on failure.

    Polygon endpoint:
    GET /v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}
    """
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={API_KEY}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SPX-0DTE-Backtest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        if data.get("resultsCount", 0) == 0 or "results" not in data:
            return None

        return data["results"]

    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"  Rate limited, waiting 5s...")
            time.sleep(5)
            return fetch_option_bars(ticker, date)  # retry once
        print(f"  HTTP {e.code} for {ticker} on {date}")
        return None
    except Exception as e:
        print(f"  Error fetching {ticker} on {date}: {e}")
        return None


def save_to_cache(ticker, date, bars):
    """Save bars to cache in same format as existing files."""
    # O:SPXW180629C02725000 -> O_SPXW180629C02725000_2018-06-29.json
    clean_ticker = ticker.replace(":", "_")
    fname = f"{clean_ticker}_{date}.json"
    fpath = os.path.join(CACHE, fname)

    with open(fpath, "w") as f:
        json.dump(bars, f)

    return fpath


def main():
    # Load contracts to fetch
    if not os.path.exists(CONTRACTS_FILE):
        print(f"ERROR: {CONTRACTS_FILE} not found.")
        print("Run the analysis script first to generate the missing contracts list.")
        sys.exit(1)

    with open(CONTRACTS_FILE) as f:
        contracts = json.load(f)

    print(f"=" * 70)
    print(f"  SPX 0DTE — Fetch Missing Option Bars")
    print(f"=" * 70)
    print(f"  Contracts to fetch: {len(contracts)}")
    print(f"  Unique dates: {len(set(c['date'] for c in contracts))}")
    print(f"  Cache directory: {CACHE}")
    print(f"  API delay: {DELAY_BETWEEN_CALLS}s between calls")
    print(f"  Estimated time: {len(contracts) * DELAY_BETWEEN_CALLS / 60:.1f} minutes")
    print(f"=" * 70)
    print()

    # Check which are already cached (in case of re-run)
    already_cached = 0
    to_fetch = []
    for c in contracts:
        clean_ticker = c["ticker"].replace(":", "_")
        fname = f"{clean_ticker}_{c['date']}.json"
        fpath = os.path.join(CACHE, fname)
        if os.path.exists(fpath):
            already_cached += 1
        else:
            to_fetch.append(c)

    if already_cached > 0:
        print(f"Skipping {already_cached} already-cached contracts")

    if not to_fetch:
        print("All contracts already cached! Nothing to fetch.")
        return

    print(f"Fetching {len(to_fetch)} contracts...\n")

    fetched = 0
    empty = 0
    errors = 0
    start_time = time.time()

    for i, c in enumerate(to_fetch):
        ticker = c["ticker"]
        date = c["date"]
        strike = c["strike"]

        bars = fetch_option_bars(ticker, date)

        if bars is None:
            empty += 1
            status = "NO DATA"
        elif len(bars) == 0:
            empty += 1
            status = "EMPTY"
            # Save empty array so we don't re-fetch
            save_to_cache(ticker, date, [])
        else:
            fetched += 1
            save_to_cache(ticker, date, bars)
            status = f"{len(bars)} bars"

        # Progress
        elapsed = time.time() - start_time
        pct = (i + 1) / len(to_fetch) * 100
        eta = (elapsed / (i + 1)) * (len(to_fetch) - i - 1) if i > 0 else 0
        print(f"  [{i+1:>4}/{len(to_fetch)}] {pct:5.1f}%  {date}  {ticker}  ${strike}  -> {status}  (ETA {eta:.0f}s)")

        time.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - start_time

    print(f"\n{'=' * 70}")
    print(f"  DONE")
    print(f"{'=' * 70}")
    print(f"  Fetched:    {fetched} contracts with data")
    print(f"  Empty:      {empty} contracts (no bars available)")
    print(f"  Errors:     {errors}")
    print(f"  Time:       {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Cache size: {len(os.listdir(CACHE)):,} files")
    print(f"{'=' * 70}")
    print()
    print("Next steps:")
    print("  1. Re-run 53_verified_trades_export.py to re-price all signal dates")
    print("  2. Re-run build_calendar3.py to rebuild the calendar with all trades")


if __name__ == "__main__":
    main()
