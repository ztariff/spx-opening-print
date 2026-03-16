"""
SPX Opening Print Strategy — Phase 6: Additional Data Collection
=================================================================
Pulls supplementary data from Polygon needed for enriched analysis:

  1. VIX (I:VIX) — 1-min bars to capture 9:30 AM ET opening level
  2. TLT (iShares 20+ Year Treasury ETF) — daily bars for bond direction
  3. SPX daily bars (I:SPX) — for computing moving averages cleanly
  4. US 10-Year Yield (I:US10Y) — daily bars for yield level/direction

All data is saved as CSVs in the same directory.

Usage:
    python 06_collect_additional_data.py YOUR_POLYGON_API_KEY

Output:
    vix_1min_bars.csv
    tlt_daily_bars.csv
    spx_daily_bars.csv
    us10y_daily_bars.csv
"""

import sys
import os
import time
import requests
import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
YEARS_BACK = 6  # Extra year for MA warmup
LIMIT = 50000
RATE_LIMIT_PAUSE = 0.1


def get_date_ranges(years_back, chunk_days=30):
    end = datetime.today()
    start = end - timedelta(days=365 * years_back)
    ranges = []
    cursor = start
    while cursor < end:
        chunk_end = cursor + timedelta(days=chunk_days)
        if chunk_end > end:
            chunk_end = end
        ranges.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def fetch_bars(api_key, ticker, multiplier, timespan, date_from, date_to):
    all_results = []
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
        f"/range/{multiplier}/{timespan}/{date_from}/{date_to}"
        f"?adjusted=true&sort=asc&limit={LIMIT}&apiKey={api_key}"
    )
    while url:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            print("    Rate limited — waiting 60s...")
            time.sleep(60)
            continue
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        results = data.get("results", [])
        all_results.extend(results)
        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            time.sleep(RATE_LIMIT_PAUSE)
        else:
            url = None
    return all_results


def ts_to_et(ts_ms):
    utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt


def collect_intraday(api_key, ticker, output_file, label):
    """Collect 1-min intraday bars."""
    date_ranges = get_date_ranges(YEARS_BACK, chunk_days=30)
    print(f"\n{'='*50}")
    print(f"Collecting {label}: {ticker} (1-min bars)")
    print(f"Chunks: {len(date_ranges)}")
    print(f"Output: {output_file}")
    print(f"{'='*50}")

    total = 0
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime_et", "date", "time", "open", "high", "low", "close", "volume", "timestamp_ms"])

        for i, (d_from, d_to) in enumerate(date_ranges):
            print(f"  [{i+1}/{len(date_ranges)}] {d_from} → {d_to} ...", end=" ", flush=True)
            bars = fetch_bars(api_key, ticker, 1, "minute", d_from, d_to)
            for bar in bars:
                et = ts_to_et(bar["t"])
                writer.writerow([
                    et.strftime("%Y-%m-%d %H:%M:%S"),
                    et.strftime("%Y-%m-%d"),
                    et.strftime("%H:%M"),
                    bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"),
                    bar.get("v", 0), bar.get("t"),
                ])
            total += len(bars)
            print(f"{len(bars)} bars (total: {total:,})")
            if i < len(date_ranges) - 1:
                time.sleep(RATE_LIMIT_PAUSE)

    print(f"Done: {total:,} bars → {output_file}")
    return total


def collect_daily(api_key, ticker, output_file, label):
    """Collect daily bars."""
    date_ranges = get_date_ranges(YEARS_BACK, chunk_days=365)
    print(f"\n{'='*50}")
    print(f"Collecting {label}: {ticker} (daily bars)")
    print(f"Output: {output_file}")
    print(f"{'='*50}")

    total = 0
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "vwap", "timestamp_ms"])

        for i, (d_from, d_to) in enumerate(date_ranges):
            print(f"  [{i+1}/{len(date_ranges)}] {d_from} → {d_to} ...", end=" ", flush=True)
            bars = fetch_bars(api_key, ticker, 1, "day", d_from, d_to)
            for bar in bars:
                et = ts_to_et(bar["t"])
                writer.writerow([
                    et.strftime("%Y-%m-%d"),
                    bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"),
                    bar.get("v", 0), bar.get("vw", ""), bar.get("t"),
                ])
            total += len(bars)
            print(f"{len(bars)} bars (total: {total:,})")
            if i < len(date_ranges) - 1:
                time.sleep(RATE_LIMIT_PAUSE)

    print(f"Done: {total:,} bars → {output_file}")
    return total


def collect_treasury_yields(api_key, output_file):
    """Collect US 10-Year Treasury yield from Polygon's dedicated treasury endpoint.
    Falls back to the TNX index or IEF ETF if the treasury endpoint fails."""
    print(f"\n{'='*50}")
    print(f"Collecting US 10-Year Treasury Yield")
    print(f"Output: {output_file}")
    print(f"{'='*50}")

    end = datetime.today()
    start = end - timedelta(days=365 * YEARS_BACK)

    # Try the dedicated treasury yields endpoint first
    url = (
        f"https://api.polygon.io/v3/reference/treasury/yields"
        f"?date.gte={start.strftime('%Y-%m-%d')}"
        f"&date.lte={end.strftime('%Y-%m-%d')}"
        f"&order=asc&limit=1000&apiKey={api_key}"
    )

    all_rows = []
    attempts = 0
    while url and attempts < 100:
        attempts += 1
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            print("    Rate limited — waiting 60s...")
            time.sleep(60)
            continue
        if resp.status_code != 200:
            print(f"    Treasury endpoint HTTP {resp.status_code}: {resp.text[:200]}")
            print("    Falling back to TNX index ticker...")
            break

        data = resp.json()
        results = data.get("results", [])
        for r in results:
            # Treasury endpoint returns fields like: date, year_10, year_2, etc.
            y10 = r.get("year_10")
            if y10 is not None:
                all_rows.append({
                    "date": r.get("date", ""),
                    "close": y10,
                })
        print(f"    Got {len(results)} records (total: {len(all_rows)})")

        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            time.sleep(RATE_LIMIT_PAUSE)
        else:
            url = None

    # If treasury endpoint worked
    if all_rows:
        with open(output_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "open", "high", "low", "close", "volume", "vwap", "timestamp_ms"])
            for r in all_rows:
                # We only have close from treasury endpoint — fill open/high/low with close
                writer.writerow([r["date"], r["close"], r["close"], r["close"], r["close"], 0, "", ""])
        print(f"Done: {len(all_rows)} yield records → {output_file}")
        return len(all_rows)

    # Fallback: try TNX (CBOE 10-Year Treasury Note Yield Index)
    print("    Trying I:TNX ticker...")
    date_ranges = get_date_ranges(YEARS_BACK, chunk_days=365)
    total = 0
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "vwap", "timestamp_ms"])
        for i, (d_from, d_to) in enumerate(date_ranges):
            print(f"    [{i+1}/{len(date_ranges)}] {d_from} → {d_to} ...", end=" ", flush=True)
            bars = fetch_bars(api_key, "I:TNX", 1, "day", d_from, d_to)
            if not bars:
                # Also try without I: prefix
                bars = fetch_bars(api_key, "TNX", 1, "day", d_from, d_to)
            for bar in bars:
                et = ts_to_et(bar["t"])
                writer.writerow([
                    et.strftime("%Y-%m-%d"),
                    bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"),
                    bar.get("v", 0), bar.get("vw", ""), bar.get("t"),
                ])
            total += len(bars)
            print(f"{len(bars)} bars (total: {total:,})")
            if i < len(date_ranges) - 1:
                time.sleep(RATE_LIMIT_PAUSE)

    print(f"Done: {total:,} bars → {output_file}")
    return total


def main():
    if len(sys.argv) < 2:
        print("Usage: python 06_collect_additional_data.py YOUR_POLYGON_API_KEY")
        sys.exit(1)

    api_key = sys.argv[1]

    print("=" * 60)
    print("ADDITIONAL DATA COLLECTION FOR SPX OPENING PRINT STRATEGY")
    print("=" * 60)

    # 1. VIX daily bars (prior close = known at 9:30, open = today's VIX open)
    #    Daily is more reliable than 1-min and gives us exactly what we need
    collect_daily(
        api_key, "I:VIX",
        os.path.join(SCRIPT_DIR, "vix_daily_bars.csv"),
        "VIX Daily"
    )

    # 2. SPX daily bars (for MAs — extra year of warmup)
    collect_daily(
        api_key, "I:SPX",
        os.path.join(SCRIPT_DIR, "spx_daily_bars.csv"),
        "SPX Daily"
    )

    # 3. TLT daily bars (bond direction proxy)
    collect_daily(
        api_key, "TLT",
        os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv"),
        "TLT (20+ Year Treasury ETF)"
    )

    # 4. US 10-Year Yield — Polygon dedicated treasury endpoint
    collect_treasury_yields(
        api_key,
        os.path.join(SCRIPT_DIR, "us10y_daily_bars.csv"),
    )

    print(f"\n{'='*60}")
    print("ALL ADDITIONAL DATA COLLECTED")
    print(f"{'='*60}")
    print("Files created:")
    print(f"  vix_daily_bars.csv     — VIX daily (open + prior close)")
    print(f"  spx_daily_bars.csv     — SPX daily (for MAs)")
    print(f"  tlt_daily_bars.csv     — TLT daily (bond direction)")
    print(f"  us10y_daily_bars.csv   — 10Y yield daily")
    print(f"\nNext: run 07_enriched_analysis.py")


if __name__ == "__main__":
    main()
