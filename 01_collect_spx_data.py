"""
SPX Opening Print Strategy — Phase 1: Data Collection
======================================================
Pulls 5 years of 1-minute intraday bars for SPX from Polygon.io
Saves to a single CSV for downstream analysis.

Usage:
    python 01_collect_spx_data.py YOUR_POLYGON_API_KEY

Output:
    spx_1min_bars.csv  (in the same directory as this script)
"""

import sys
import os
import time
import requests
import csv
from datetime import datetime, timedelta

# ── Config ──────────────────────────────────────────────────────────────────
TICKER = "I:SPX"
TIMESPAN = "minute"
MULTIPLIER = 1
YEARS_BACK = 5
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spx_1min_bars.csv")
LIMIT = 50000  # Polygon max results per request
RATE_LIMIT_PAUSE = 0.1  # seconds between requests

# ── Helpers ─────────────────────────────────────────────────────────────────

def get_date_ranges(years_back):
    """Break the full date range into monthly chunks to stay within Polygon's 50k row limit."""
    end = datetime.today()
    start = end - timedelta(days=365 * years_back)

    ranges = []
    cursor = start
    while cursor < end:
        month_end = cursor + timedelta(days=30)
        if month_end > end:
            month_end = end
        ranges.append((cursor.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")))
        cursor = month_end + timedelta(days=1)
    return ranges


def fetch_bars(api_key, date_from, date_to):
    """Fetch 1-min bars for one date chunk. Handles pagination."""
    all_results = []
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{TICKER}"
        f"/range/{MULTIPLIER}/{TIMESPAN}/{date_from}/{date_to}"
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

        # Pagination: Polygon returns next_url if there are more results
        next_url = data.get("next_url")
        if next_url:
            url = f"{next_url}&apiKey={api_key}"
            time.sleep(RATE_LIMIT_PAUSE)
        else:
            url = None

    return all_results


def timestamp_to_et(ts_ms):
    """Convert Polygon's Unix ms timestamp to ET datetime string.
    Polygon returns timestamps in UTC for indices."""
    from zoneinfo import ZoneInfo
    utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt.strftime("%Y-%m-%d %H:%M:%S")


def timestamp_to_date(ts_ms):
    from zoneinfo import ZoneInfo
    utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt.strftime("%Y-%m-%d")


def timestamp_to_time(ts_ms):
    from zoneinfo import ZoneInfo
    utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt.strftime("%H:%M")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python 01_collect_spx_data.py YOUR_POLYGON_API_KEY")
        sys.exit(1)

    api_key = sys.argv[1]
    date_ranges = get_date_ranges(YEARS_BACK)

    print(f"SPX 1-Min Data Collection")
    print(f"{'='*50}")
    print(f"Ticker:      {TICKER}")
    print(f"Lookback:    {YEARS_BACK} years")
    print(f"Chunks:      {len(date_ranges)} monthly periods")
    print(f"Output:      {OUTPUT_FILE}")
    print(f"{'='*50}\n")

    total_bars = 0

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime_et", "date", "time", "open", "high", "low", "close", "volume", "vwap", "transactions", "timestamp_ms"])

        for i, (d_from, d_to) in enumerate(date_ranges):
            print(f"[{i+1}/{len(date_ranges)}] Fetching {d_from} → {d_to} ...", end=" ", flush=True)

            bars = fetch_bars(api_key, d_from, d_to)

            for bar in bars:
                ts = bar.get("t")
                writer.writerow([
                    timestamp_to_et(ts),
                    timestamp_to_date(ts),
                    timestamp_to_time(ts),
                    bar.get("o"),
                    bar.get("h"),
                    bar.get("l"),
                    bar.get("c"),
                    bar.get("v", 0),
                    bar.get("vw", ""),
                    bar.get("n", ""),
                    ts
                ])

            total_bars += len(bars)
            print(f"{len(bars)} bars  (total: {total_bars:,})")

            if i < len(date_ranges) - 1:
                time.sleep(RATE_LIMIT_PAUSE)

    print(f"\nDone. {total_bars:,} bars saved to {OUTPUT_FILE}")
    print(f"File size: {os.path.getsize(OUTPUT_FILE) / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
