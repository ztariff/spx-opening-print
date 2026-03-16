"""
Script 16: Extend All Data Back to 2018 (v3)
=============================================
Polygon's I:SPX / I:VIX index data only starts 2023-02-14.
For pre-2023 data we use:

  - SPY 1-min bars from Polygon  (intraday percentage moves)
  - SPY daily bars from Polygon
  - Real SPX daily OHLC from CBOE  (for accurate daily price levels)
  - Real VIX daily OHLC from CBOE
  - TLT daily from Polygon  (already extended, will skip if present)

SPY intraday bars are calibrated to real SPX levels using a per-day ratio
derived from CBOE SPX opens vs SPY opens. This keeps percentage moves
from Polygon while anchoring the price level to real SPX values.

Usage:
    python3 16_extend_data_2018.py
"""

import os, sys, time, csv, io, math
import requests
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
BASE_URL = "https://api.polygon.io"
LIMIT = 50000
REQUEST_DELAY = 0.05

EXTEND_START = "2018-01-01"
EXTEND_END   = "2023-02-13"   # Day before existing Polygon index data

# CBOE publishes free historical index data
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
CBOE_SPX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/SPX_History.csv"


# ── Polygon helpers ─────────────────────────────────────────────────────────

def api_get(url, max_retries=5):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code} on attempt {attempt+1}: {resp.text[:200]}")
                time.sleep(5)
                continue
            return resp.json()
        except Exception as e:
            print(f"    Error: {e} on attempt {attempt+1}")
            time.sleep(5)
    return None


def get_date_chunks(start, end, chunk_days=25):
    chunks = []
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    cursor = s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def get_et_offset(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return 4 if dst_start <= dt.replace(hour=12) < dst_end else 5


def ts_to_et(ts_ms):
    dt_utc = datetime.utcfromtimestamp(ts_ms / 1000)
    date_str = dt_utc.strftime("%Y-%m-%d")
    offset = get_et_offset(date_str)
    return dt_utc - timedelta(hours=offset)


def fetch_all_bars(ticker, multiplier, timespan, start, end, chunk_days=25):
    chunks = get_date_chunks(start, end, chunk_days)
    all_bars = []
    for ci, (cfrom, cto) in enumerate(chunks):
        url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
               f"/range/{multiplier}/{timespan}/{cfrom}/{cto}"
               f"?adjusted=true&sort=asc&limit={LIMIT}&apiKey={API_KEY}")
        while url:
            time.sleep(REQUEST_DELAY)
            data = api_get(url)
            if not data:
                break
            results = data.get("results", [])
            all_bars.extend(results)
            next_url = data.get("next_url")
            url = f"{next_url}&apiKey={API_KEY}" if next_url else None
        if (ci + 1) % 20 == 0 or ci == len(chunks) - 1:
            print(f"    Chunk {ci+1}/{len(chunks)}: {len(all_bars)} bars so far", flush=True)
    return all_bars


# ── CBOE download ───────────────────────────────────────────────────────────

def download_cboe_csv(url, label):
    """Download a CBOE historical CSV. Returns list of {date, open, high, low, close}."""
    print(f"  Downloading CBOE {label} data...")
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            print(f"    CBOE download failed: HTTP {resp.status_code}")
            return None
        lines = resp.text.strip().split("\n")
        print(f"    Downloaded {len(lines)} lines")

        rows = []
        for line in lines[1:]:
            parts = line.strip().split(",")
            if len(parts) < 5:
                continue
            try:
                date_str = parts[0].strip()
                if "/" in date_str:
                    dt = datetime.strptime(date_str, "%m/%d/%Y")
                else:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                d = dt.strftime("%Y-%m-%d")
                o, h, l, c = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                if o > 0 and c > 0:
                    rows.append({"date": d, "open": o, "high": h, "low": l, "close": c})
            except (ValueError, IndexError):
                continue

        # Filter to our extension range
        rows = [r for r in rows if EXTEND_START <= r["date"] <= EXTEND_END]
        print(f"    Rows in range {EXTEND_START} to {EXTEND_END}: {len(rows)}")
        return rows

    except Exception as e:
        print(f"    CBOE download error: {e}")
        return None


# ── Existing-data loaders ───────────────────────────────────────────────────

def load_existing_csv_dates(filepath):
    """Return set of dates already in a CSV."""
    dates = set()
    if os.path.exists(filepath):
        with open(filepath) as f:
            for row in csv.DictReader(f):
                dates.add(row["date"])
    return dates


def load_existing_csv_rows(filepath):
    """Return (header, rows) from existing CSV."""
    rows = []
    header = None
    if os.path.exists(filepath):
        with open(filepath) as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            for row in reader:
                rows.append(row)
    return header, rows


# ── Step 1: CBOE SPX daily → spx_daily_bars.csv ────────────────────────────

def extend_spx_daily():
    filepath = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
    existing_dates = load_existing_csv_dates(filepath)
    _, existing_rows = load_existing_csv_rows(filepath)

    cboe_rows = download_cboe_csv(CBOE_SPX_URL, "SPX")
    if not cboe_rows:
        print("    FAILED: Could not get CBOE SPX data.")
        print("    Falling back to SPY daily with dynamic ratio...")
        return extend_spx_daily_spy_fallback(existing_rows, existing_dates, filepath)

    new_rows = []
    for r in cboe_rows:
        if r["date"] not in existing_dates:
            new_rows.append({
                "date": r["date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 0,
            })

    print(f"    New SPX daily rows from CBOE: {len(new_rows)}")
    all_rows = new_rows + existing_rows
    all_rows.sort(key=lambda r: r["date"])

    header = ["date", "open", "high", "low", "close", "volume"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"    Written: {len(all_rows)} total rows to spx_daily_bars.csv")

    # Return CBOE SPX daily as dict for ratio computation
    return {r["date"]: r for r in cboe_rows}


def extend_spx_daily_spy_fallback(existing_rows, existing_dates, filepath):
    """Fallback: use SPY daily from Polygon and calibrate from overlap."""
    print("    Fetching SPY daily from Polygon...")
    spy_bars = fetch_all_bars("SPY", 1, "day", EXTEND_START, EXTEND_END, chunk_days=365)
    print(f"    Got {len(spy_bars)} SPY daily bars")

    # Build SPY daily dict
    spy_daily = {}
    for bar in spy_bars:
        dt_et = ts_to_et(bar["t"])
        d = dt_et.strftime("%Y-%m-%d")
        spy_daily[d] = {"open": bar["o"], "high": bar["h"], "low": bar["l"], "close": bar["c"]}

    # Compute ratio from overlap with existing SPX data
    overlap_ratios = []
    for row in existing_rows[:30]:
        d = row["date"]
        if d in spy_daily:
            spx_c = float(row["close"])
            spy_c = spy_daily[d]["close"]
            if spy_c > 0:
                overlap_ratios.append(spx_c / spy_c)
    if overlap_ratios:
        ratio = sum(overlap_ratios) / len(overlap_ratios)
        print(f"    Calibrated SPX/SPY ratio from overlap: {ratio:.4f}")
    else:
        ratio = 10.0
        print(f"    WARNING: No overlap data, using ratio={ratio}")

    new_rows = []
    for d, spy in sorted(spy_daily.items()):
        if d not in existing_dates:
            new_rows.append({
                "date": d,
                "open": round(spy["open"] * ratio, 2),
                "high": round(spy["high"] * ratio, 2),
                "low": round(spy["low"] * ratio, 2),
                "close": round(spy["close"] * ratio, 2),
                "volume": 0,
            })

    print(f"    New SPX daily rows (SPY fallback): {len(new_rows)}")
    all_rows = new_rows + existing_rows
    all_rows.sort(key=lambda r: r["date"])

    header = ["date", "open", "high", "low", "close", "volume"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"    Written: {len(all_rows)} total rows (SPY fallback)")

    # Return as dict for ratio use
    return {d: {"open": spy["open"] * ratio, "close": spy["close"] * ratio}
            for d, spy in spy_daily.items()}


# ── Step 2: VIX daily from CBOE → vix_daily_bars.csv ───────────────────────

def extend_vix_daily():
    filepath = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
    existing_dates = load_existing_csv_dates(filepath)
    _, existing_rows = load_existing_csv_rows(filepath)

    cboe_rows = download_cboe_csv(CBOE_VIX_URL, "VIX")
    if not cboe_rows:
        print("    FAILED: Could not get CBOE VIX data. VIX daily not extended.")
        return

    new_rows = []
    for r in cboe_rows:
        if r["date"] not in existing_dates:
            new_rows.append({
                "date": r["date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 0,
            })

    print(f"    New VIX daily rows from CBOE: {len(new_rows)}")
    all_rows = new_rows + existing_rows
    all_rows.sort(key=lambda r: r["date"])

    header = ["date", "open", "high", "low", "close", "volume"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"    Written: {len(all_rows)} total rows to vix_daily_bars.csv")


# ── Step 3: SPY 1-min → SPX 1-min (calibrated per day) ─────────────────────

def extend_spx_1min(spx_daily_dict):
    """Fetch SPY 1-min from Polygon, calibrate to real SPX levels per day.

    For each day:
      ratio = CBOE_SPX_open / SPY_open  (from first 9:30 bar)
      SPX_bar = SPY_bar * ratio

    This preserves SPY's intraday percentage moves exactly while anchoring
    the price level to the real SPX open from CBOE.
    """
    filepath = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")

    # Read existing
    existing_keys = set()
    existing_rows = []
    if os.path.exists(filepath):
        with open(filepath) as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                existing_keys.add(f"{row['date']}_{row['time']}")

    print(f"\n  Fetching SPY 1-min bars ({EXTEND_START} to {EXTEND_END})...")
    print(f"    This will take a while (many API calls)...")
    spy_bars = fetch_all_bars("SPY", 1, "minute", EXTEND_START, EXTEND_END, chunk_days=5)
    print(f"    Got {len(spy_bars)} SPY 1-min bars")

    # Group SPY bars by date
    spy_by_date = defaultdict(list)
    for bar in spy_bars:
        dt_et = ts_to_et(bar["t"])
        d = dt_et.strftime("%Y-%m-%d")
        t = dt_et.strftime("%H:%M")
        if "09:30" <= t < "16:00":
            spy_by_date[d].append({
                "dt_et": dt_et,
                "time": t,
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0),
                "vwap": bar.get("vw", 0),
                "transactions": bar.get("n", ""),
                "timestamp_ms": bar["t"],
            })

    print(f"    Trading days with SPY data: {len(spy_by_date)}")

    # For each day, compute ratio from CBOE SPX open / SPY first bar open
    new_rows = []
    days_with_ratio = 0
    days_without_ratio = 0

    for d in sorted(spy_by_date.keys()):
        if d > EXTEND_END:
            continue
        day_bars = sorted(spy_by_date[d], key=lambda b: b["time"])
        if not day_bars:
            continue

        # Get SPY 9:30 open
        spy_open = None
        for b in day_bars:
            if b["time"] == "09:30":
                spy_open = b["open"]
                break
        if spy_open is None:
            spy_open = day_bars[0]["open"]
        if spy_open <= 0:
            continue

        # Get real SPX open from CBOE data
        if d in spx_daily_dict and spx_daily_dict[d].get("open", 0) > 0:
            spx_open = spx_daily_dict[d]["open"]
            if isinstance(spx_open, str):
                spx_open = float(spx_open)
            ratio = spx_open / spy_open
            days_with_ratio += 1
        else:
            # No CBOE data for this day — skip it
            days_without_ratio += 1
            continue

        # Apply ratio to all intraday bars
        for b in day_bars:
            key = f"{d}_{b['time']}"
            if key in existing_keys:
                continue
            new_rows.append({
                "datetime_et": b["dt_et"].strftime("%Y-%m-%d %H:%M:%S"),
                "date": d,
                "time": b["time"],
                "open": round(b["open"] * ratio, 2),
                "high": round(b["high"] * ratio, 2),
                "low": round(b["low"] * ratio, 2),
                "close": round(b["close"] * ratio, 2),
                "volume": b["volume"],
                "vwap": round(b["vwap"] * ratio, 2) if b["vwap"] else "",
                "transactions": b["transactions"],
                "timestamp_ms": b["timestamp_ms"],
            })

    print(f"    Days with CBOE ratio: {days_with_ratio}")
    print(f"    Days without CBOE ratio (skipped): {days_without_ratio}")
    print(f"    New SPX 1-min rows: {len(new_rows)}")

    all_rows = new_rows + existing_rows
    all_rows.sort(key=lambda r: (r["date"], r["time"]))

    header = ["datetime_et", "date", "time", "open", "high", "low", "close",
              "volume", "vwap", "transactions", "timestamp_ms"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"    Written: {len(all_rows)} total rows to spx_1min_bars.csv")


# ── Step 4: TLT daily ──────────────────────────────────────────────────────

def extend_tlt_daily():
    filepath = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
    existing_dates = load_existing_csv_dates(filepath)
    _, existing_rows = load_existing_csv_rows(filepath)

    if existing_dates and min(existing_dates) <= "2018-01-10":
        print(f"    TLT already has data from {min(existing_dates)}, skipping")
        return

    print(f"  Fetching TLT daily bars ({EXTEND_START} to {EXTEND_END})...")
    bars = fetch_all_bars("TLT", 1, "day", EXTEND_START, EXTEND_END, chunk_days=365)
    print(f"    Got {len(bars)} bars")

    new_rows = []
    for bar in bars:
        dt_et = ts_to_et(bar["t"])
        d = dt_et.strftime("%Y-%m-%d")
        if d in existing_dates:
            continue
        new_rows.append({
            "date": d,
            "open": bar["o"], "high": bar["h"],
            "low": bar["l"], "close": bar["c"],
            "volume": bar.get("v", 0),
            "vwap": bar.get("vw", ""),
            "transactions": bar.get("n", ""),
        })

    print(f"    New rows: {len(new_rows)}")
    all_rows = new_rows + existing_rows
    all_rows.sort(key=lambda r: r["date"])

    header = ["date", "open", "high", "low", "close", "volume", "vwap", "transactions"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"    Written: {len(all_rows)} total rows to tlt_daily_bars.csv")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("EXTENDING DATA BACK TO 2018")
    print("=" * 80)
    print(f"  Extension range: {EXTEND_START} to {EXTEND_END}")
    print(f"  SPX/VIX daily: CBOE historical CSVs (real index values)")
    print(f"  SPX intraday:  SPY 1-min from Polygon, calibrated per-day")
    print(f"                 using ratio = CBOE SPX open / SPY open")
    print(f"  TLT daily:     Polygon")
    print()

    # Step 1: Real SPX daily from CBOE
    print("[1/4] SPX Daily (CBOE)")
    spx_daily_dict = extend_spx_daily()

    if not spx_daily_dict:
        print("\nFATAL: No SPX daily data available for ratio calibration.")
        print("Cannot extend SPX 1-min data without real SPX levels.")
        return

    # Step 2: Real VIX daily from CBOE
    print("\n[2/4] VIX Daily (CBOE)")
    extend_vix_daily()

    # Step 3: SPY 1-min from Polygon → calibrated SPX 1-min
    print("\n[3/4] SPX 1-Min (SPY from Polygon, calibrated to CBOE SPX)")
    extend_spx_1min(spx_daily_dict)

    # Step 4: TLT daily from Polygon
    print("\n[4/4] TLT Daily (Polygon)")
    extend_tlt_daily()

    # ── Verify ──
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)
    for f in ["spx_daily_bars.csv", "vix_daily_bars.csv", "tlt_daily_bars.csv",
              "spx_1min_bars.csv"]:
        fp = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(fp):
            with open(fp) as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                if rows:
                    dates = sorted(set(r["date"] for r in rows))
                    # Show first/last price to sanity check
                    first_row = min(rows, key=lambda r: r["date"])
                    last_row = max(rows, key=lambda r: r["date"])
                    print(f"  {f:30s}  {dates[0]} to {dates[-1]}  "
                          f"({len(dates)} days, {len(rows)} rows)  "
                          f"first_open={first_row['open']}  last_close={last_row['close']}")
                else:
                    print(f"  {f:30s}  EMPTY")

    print("\n" + "=" * 80)
    print("DONE!")
    print("=" * 80)


if __name__ == "__main__":
    main()
