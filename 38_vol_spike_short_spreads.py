"""
SPX Opening Print — Script 38: Short Spreads on Intraday Vol Spikes
====================================================================
Script 37 showed buying ATM calls on dips works, but the real edge might be
SELLING premium on spikes — you profit from mean reversion AND theta/vol crush.

This script tests:
  1. SELL PUT SPREAD on dip (bull put spread):
     - Sell ATM put (high premium after spike), buy OTM put as hedge
     - Profit if SPX recovers or even stays flat
     - Max profit = credit received, max loss = spread width - credit

  2. SELL CALL SPREAD on rip (bear call spread) — inverse test:
     - Sell ATM call on sharp up-move, buy OTM call as hedge
     - Tests whether vol spikes UP also mean-revert

  3. IRON CONDOR on spike (sell both sides):
     - Sell put spread + sell call spread around the spike level

All using REAL Polygon option prices for both legs of every spread.

Key differences from script 37:
  - Fetches TWO option contracts per trade (short + long leg)
  - Spread P&L = credit received - cost to close
  - Max loss is capped by spread width
  - Theta works FOR us, not against us
  - Even flat price action = profit

Usage:
    python3 38_vol_spike_short_spreads.py
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev, median
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "vol_spike_short_spreads_report.txt")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

BASE_RISK = 75000
SLIPPAGE_PER_LEG = 0.30  # $0.30 per leg per contract (tighter for spreads)


class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (same as scripts 36/37)
# ══════════════════════════════════════════════════════════════════════════════

def load_intraday():
    days = defaultdict(list)
    with open(SPX_1MIN, "r") as f:
        for row in csv.DictReader(f):
            t = row["time"]
            if t < "09:30" or t >= "16:00":
                continue
            days[row["date"]].append({
                "time": t,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return dict(days)


def load_daily():
    daily = {}
    with open(SPX_DAILY, "r") as f:
        for row in csv.DictReader(f):
            daily[row["date"]] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
    return daily


def load_vix_daily():
    vix = {}
    with open(VIX_DAILY, "r") as f:
        for row in csv.DictReader(f):
            vix[row["date"]] = float(row["close"])
    return vix


# ══════════════════════════════════════════════════════════════════════════════
# CACHE / API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def cache_path(key):
    return os.path.join(CACHE_DIR, key + ".json")

def load_cache(key):
    p = cache_path(key)
    if os.path.exists(p):
        with open(p, "r") as f:
            return json.load(f)
    return None

def save_cache(key, data):
    with open(cache_path(key), "w") as f:
        json.dump(data, f)

def api_get(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif e.code in (403, 404):
                return None
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
    return None

def get_dst_offset(date_str):
    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    return 4 if is_dst else 5


# ══════════════════════════════════════════════════════════════════════════════
# OPTION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def find_nearest_expiry(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    daily_0dte_start = datetime(2022, 9, 19)
    if dt >= daily_0dte_start:
        return date_str, 0
    dow = dt.weekday()
    if dow in (0, 2, 4):
        return date_str, 0
    elif dow in (1, 3):
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    return date_str, 0


def find_option(date_str, target_strike, option_type="P"):
    """Find SPX option — checks SPXW then SPX."""
    strike = round(target_strike / 5) * 5
    cache_key = f"contracts_v3_{date_str}_{strike}_{option_type}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None
        return cached["ticker"], cached["strike"]

    expiry_date, dte = find_nearest_expiry(date_str)
    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    date_code = exp_dt.strftime("%y%m%d")

    for underlying in ["SPXW", "SPX"]:
        for offset in [0, 5, -5, 10, -10]:
            test_strike = strike + offset
            strike_code = f"{int(test_strike * 1000):08d}"
            ticker = f"O:{underlying}{date_code}{option_type}{strike_code}"
            url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
                   f"/range/1/minute/{date_str}/{date_str}"
                   f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
            time.sleep(REQUEST_DELAY)
            data = api_get(url)
            if data and data.get("resultsCount", 0) > 0:
                result = {"ticker": ticker, "strike": test_strike,
                          "expiry": expiry_date, "dte": dte}
                save_cache(cache_key, result)
                return ticker, test_strike

    save_cache(cache_key, "none")
    return None, None


def fetch_option_bars(ticker, date_str, resolution="10s"):
    """Fetch option bars at given resolution with caching."""
    safe = ticker.replace(':', '_').replace('/', '_')

    if resolution == "10s":
        cache_key = f"opt10s_{date_str}_{safe}"
        cached = load_cache(cache_key)
        if cached is not None:
            return cached if cached != "none" else []

        url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
               f"/range/10/second/{date_str}/{date_str}"
               f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    else:
        cache_key = f"bars_{date_str}_{safe}"
        cached = load_cache(cache_key)
        if cached is not None:
            return cached if cached != "none" else []

        url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
               f"/range/1/minute/{date_str}/{date_str}"
               f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")

    time.sleep(REQUEST_DELAY)
    data = api_get(url)
    if not data or data.get("resultsCount", 0) == 0:
        save_cache(cache_key, "none")
        return []

    offset_hours = get_dst_offset(date_str)
    bars = []
    time_fmt = "%H:%M:%S" if resolution == "10s" else "%H:%M"

    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime(time_fmt)
        bars.append({
            "time": t_str, "open": r["o"], "high": r["h"],
            "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
        })

    # Pagination for 10s bars
    if resolution == "10s":
        while data and data.get("next_url"):
            next_url = data["next_url"] + f"&apiKey={API_KEY}"
            time.sleep(REQUEST_DELAY)
            data = api_get(next_url)
            if data and data.get("results"):
                for r in data["results"]:
                    dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
                    dt_et = dt_utc - timedelta(hours=offset_hours)
                    t_str = dt_et.strftime(time_fmt)
                    bars.append({
                        "time": t_str, "open": r["o"], "high": r["h"],
                        "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
                    })

    bars.sort(key=lambda b: b["time"])
    save_cache(cache_key, bars if bars else "none")
    return bars


# ══════════════════════════════════════════════════════════════════════════════
# SPIKE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def time_to_seconds(t):
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def detect_spikes_1min(bars, drop_pct, lookback_mins, min_time="09:35", max_time="15:00"):
    spikes = []
    for i in range(lookback_mins, len(bars)):
        bar = bars[i]
        if bar["time"] < min_time or bar["time"] > max_time:
            continue
        window = bars[max(0, i - lookback_mins):i]
        rolling_high = max(b["high"] for b in window)
        current_drop = (rolling_high - bar["low"]) / rolling_high * 100
        if current_drop >= drop_pct:
            spikes.append({
                "bar_idx": i, "time": bar["time"],
                "drop_pct": current_drop,
                "rolling_high": rolling_high,
                "spike_low": bar["low"],
                "entry_price": bar["close"],
            })
    # Deduplicate: keep deepest per 5-min window
    filtered = []
    for spike in spikes:
        if filtered and time_to_seconds(spike["time"]) - time_to_seconds(filtered[-1]["time"]) < 300:
            if spike["drop_pct"] > filtered[-1]["drop_pct"]:
                filtered[-1] = spike
        else:
            filtered.append(spike)
    return filtered


def detect_rips_1min(bars, rip_pct, lookback_mins, min_time="09:35", max_time="15:00"):
    """Detect sharp UP moves — for testing short call spreads on rips."""
    rips = []
    for i in range(lookback_mins, len(bars)):
        bar = bars[i]
        if bar["time"] < min_time or bar["time"] > max_time:
            continue
        window = bars[max(0, i - lookback_mins):i]
        rolling_low = min(b["low"] for b in window)
        current_rip = (bar["high"] - rolling_low) / rolling_low * 100
        if current_rip >= rip_pct:
            rips.append({
                "bar_idx": i, "time": bar["time"],
                "rip_pct": current_rip,
                "rolling_low": rolling_low,
                "rip_high": bar["high"],
                "entry_price": bar["close"],
            })
    filtered = []
    for rip in rips:
        if filtered and time_to_seconds(rip["time"]) - time_to_seconds(filtered[-1]["time"]) < 300:
            if rip["rip_pct"] > filtered[-1]["rip_pct"]:
                filtered[-1] = rip
        else:
            filtered.append(rip)
    return filtered


# ══════════════════════════════════════════════════════════════════════════════
# SPREAD TRADE SIMULATION — OPTIMIZED
# ══════════════════════════════════════════════════════════════════════════════

def precompute_bar_secs(bars):
    """Pre-compute time_to_seconds for all bars once. Returns list of (secs, bar)."""
    result = []
    for bar in bars:
        t = bar["time"]
        parts = t.split(":")
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        result.append((h * 3600 + m * 60 + s, bar))
    return result


def build_spread_timeseries(short_bars, long_bars, entry_time_str):
    """
    Pre-compute the spread time series ONCE for a given spike + width combo.
    Returns a list of dicts with aligned short/long prices at each timestamp,
    plus the entry info.

    This is the key optimization: compute this once, then sweep all param
    combos against it without re-scanning bars.
    """
    # Pre-compute seconds
    short_indexed = precompute_bar_secs(short_bars)
    long_indexed = precompute_bar_secs(long_bars)

    if not short_indexed or not long_indexed:
        return None

    entry_time_full = entry_time_str if len(entry_time_str) >= 7 else entry_time_str + ":00"
    parts = entry_time_full.split(":")
    entry_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + (int(parts[2]) if len(parts) > 2 else 0)

    # Find entry bar for short leg (first bar at or after entry_secs)
    short_entry_idx = None
    for i, (secs, bar) in enumerate(short_indexed):
        if secs >= entry_secs:
            short_entry_idx = i
            break
    if short_entry_idx is None:
        return None

    # Find entry bar for long leg
    long_entry_idx = None
    for i, (secs, bar) in enumerate(long_indexed):
        if secs >= entry_secs:
            long_entry_idx = i
            break
    if long_entry_idx is None:
        return None

    short_entry_price = short_indexed[short_entry_idx][1]["close"]
    long_entry_price = long_indexed[long_entry_idx][1]["close"]
    credit = short_entry_price - long_entry_price

    if credit <= 0.05:
        return None

    actual_entry_secs = short_indexed[short_entry_idx][0]

    # Build aligned time series from entry forward
    # Use two-pointer approach to align short and long bars
    series = []
    li = long_entry_idx  # pointer into long bars

    for si in range(short_entry_idx + 1, len(short_indexed)):
        s_secs, s_bar = short_indexed[si]

        # Advance long pointer to match or exceed short time
        while li < len(long_indexed) - 1 and long_indexed[li][0] < s_secs:
            li += 1
        l_secs, l_bar = long_indexed[li]

        # Only use if reasonably aligned (within 30 seconds)
        if abs(s_secs - l_secs) > 30:
            continue

        close_debit = s_bar["close"] - l_bar["close"]
        worst_debit = s_bar["high"] - l_bar["low"]  # worst case for SL check
        best_debit = s_bar["low"] - l_bar["high"]    # best case for PT check

        series.append({
            "secs": s_secs,
            "offset_secs": s_secs - actual_entry_secs,
            "close_debit": close_debit,
            "worst_debit": worst_debit,
            "best_debit": best_debit,
            "s_time": s_bar["time"],
            "s_close": s_bar["close"],
            "l_close": l_bar["close"],
        })

    if not series:
        return None

    return {
        "short_entry": round(short_entry_price, 2),
        "long_entry": round(long_entry_price, 2),
        "credit": round(credit, 2),
        "entry_secs": actual_entry_secs,
        "entry_time": short_indexed[short_entry_idx][1]["time"],
        "series": series,
    }


def sweep_spread_params(ts_data, hold_mins, sl_mult, pt_pct, spread_width):
    """
    Given a pre-computed spread timeseries, apply one set of exit params.
    This is O(len(series)) — no string parsing, no bar lookups.
    """
    if not ts_data:
        return None

    credit = ts_data["credit"]
    hold_secs = hold_mins * 60
    exit_reason = "Time Stop (EOD)"
    exit_idx = len(ts_data["series"]) - 1  # default to last

    for i, point in enumerate(ts_data["series"]):
        # Time stop
        if point["offset_secs"] >= hold_secs:
            exit_idx = i
            exit_reason = "Time Stop"
            break

        # Stop loss
        if sl_mult is not None:
            loss_limit = credit * sl_mult
            worst_loss = credit - point["worst_debit"]
            if worst_loss <= -loss_limit:
                exit_idx = i
                exit_reason = "Stop Loss"
                break

        # Profit target
        if pt_pct is not None:
            target_profit = credit * pt_pct / 100
            best_profit = credit - point["best_debit"]
            if best_profit >= target_profit:
                exit_idx = i
                exit_reason = "Profit Target"
                break

    point = ts_data["series"][exit_idx]
    close_debit = point["close_debit"]
    pnl_per_contract = (credit - close_debit) * 100

    hold_actual = max(10, point["offset_secs"]) / 60

    return {
        "short_entry": ts_data["short_entry"],
        "long_entry": ts_data["long_entry"],
        "credit": credit,
        "short_exit": round(point["s_close"], 2),
        "long_exit": round(point["l_close"], 2),
        "close_debit": round(close_debit, 2),
        "pnl_per_contract": round(pnl_per_contract, 2),
        "pnl_pct_of_credit": round((credit - close_debit) / credit * 100, 1) if credit > 0 else 0,
        "max_loss_per": round((spread_width - credit) * 100, 2),
        "exit_reason": exit_reason,
        "entry_time": ts_data["entry_time"],
        "exit_time": point["s_time"],
        "hold_mins": round(hold_actual, 1),
        "spread_width": spread_width,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    report_file = open(OUTPUT_REPORT, "w")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, report_file)

    print("=" * 95)
    print("VOL SPIKE SHORT SPREADS — REAL POLYGON OPTION PRICES")
    print("=" * 95)
    print()

    # Load data
    print("Loading data...")
    intraday = load_intraday()
    daily = load_daily()
    vix_daily = load_vix_daily()

    dates = sorted(intraday.keys())
    dates_0dte = [d for d in dates if d >= "2022-09-19"]
    print(f"  SPX 1-min days: {len(dates)}")
    print(f"  0DTE-eligible dates: {len(dates_0dte)}")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: SELL PUT SPREADS ON DIPS (BULL PUT SPREAD)
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 95)
    print("PHASE 1: SELL PUT SPREAD ON INTRADAY DIP (BULL PUT SPREAD)")
    print("=" * 95)
    print()
    print("Strategy: When SPX drops sharply, sell ATM put + buy OTM put")
    print("Profit from: mean reversion + theta decay + vol crush")
    print()

    # Parameter grid
    drop_thresholds = [0.20, 0.30, 0.40, 0.50]
    spread_widths = [10, 15, 20, 30]        # points between strikes
    hold_times = [5, 10, 15, 30, 60, 90]    # minutes
    sl_mults = [None, 1.5, 2.0, 3.0]        # stop at N× credit lost
    pt_pcts = [None, 30, 50, 75]             # take profit at N% of credit

    all_results = []
    total_combos = len(drop_thresholds) * len(spread_widths) * len(hold_times) * len(sl_mults) * len(pt_pcts)
    print(f"Parameter grid: {total_combos} combinations")
    print()

    # First collect all spikes and their option data
    print("--- Collecting spike data and option bars ---")
    spike_data = []
    api_calls = 0

    for di, date in enumerate(dates_0dte):
        bars = intraday[date]
        if len(bars) < 30:
            continue

        vix = vix_daily.get(date, 20)

        # Detect with broad threshold
        spikes = detect_spikes_1min(bars, drop_pct=0.20, lookback_mins=10,
                                     min_time="09:35", max_time="14:30")

        if not spikes:
            continue

        if (di + 1) % 100 == 0 or di == 0:
            print(f"  [{di+1}/{len(dates_0dte)}] {date}: {len(spikes)} spikes, {api_calls} API calls so far")

        for spike in spikes:
            spx_level = spike["entry_price"]

            # For each spread width, we need ATM put and OTM put
            # Cache the options we find so we don't re-lookup
            spike_options = {
                "date": date,
                "spike": spike,
                "vix": vix,
                "puts": {},  # strike -> (ticker, bars)
            }

            # Find ATM put
            atm_strike = round(spx_level / 5) * 5
            atm_ticker, atm_actual = find_option(date, spx_level, "P")
            if not atm_ticker:
                continue
            api_calls += 1

            # Fetch ATM put bars (try 10s first, fall back to 1min)
            atm_bars = fetch_option_bars(atm_ticker, date, "10s")
            if not atm_bars or len(atm_bars) < 10:
                atm_bars = fetch_option_bars(atm_ticker, date, "1min")
            if not atm_bars or len(atm_bars) < 5:
                continue
            api_calls += 1

            spike_options["puts"][atm_actual] = {
                "ticker": atm_ticker, "bars": atm_bars
            }

            # Find OTM puts at various widths
            for width in spread_widths:
                otm_target = spx_level - width
                otm_ticker, otm_actual = find_option(date, otm_target, "P")
                if otm_ticker and otm_actual not in spike_options["puts"]:
                    otm_bars = fetch_option_bars(otm_ticker, date, "10s")
                    if not otm_bars or len(otm_bars) < 10:
                        otm_bars = fetch_option_bars(otm_ticker, date, "1min")
                    if otm_bars and len(otm_bars) >= 5:
                        spike_options["puts"][otm_actual] = {
                            "ticker": otm_ticker, "bars": otm_bars
                        }
                        api_calls += 1

            spike_options["atm_strike"] = atm_actual
            spike_data.append(spike_options)

    print(f"\n  Total spike events with option data: {len(spike_data)}")
    print(f"  Total API calls: {api_calls}")
    print()

    # ── Pre-compute spread timeseries for each spike × width ────────────
    print("--- Pre-computing spread timeseries (one-time cost) ---")

    # Key: (spike_index, width) -> ts_data
    precomputed = {}
    precompute_count = 0

    for si, sd in enumerate(spike_data):
        atm_strike = sd["atm_strike"]
        if atm_strike is None:
            continue

        short_data = sd["puts"].get(atm_strike)
        if not short_data:
            continue

        for width in spread_widths:
            # Find long leg
            best_long_strike = None
            best_width_diff = 999
            for strike in sd["puts"]:
                if strike < atm_strike:
                    actual_width = atm_strike - strike
                    diff = abs(actual_width - width)
                    if diff < best_width_diff:
                        best_width_diff = diff
                        best_long_strike = strike

            if best_long_strike is None or best_width_diff > 10:
                continue

            long_data = sd["puts"][best_long_strike]
            actual_width = atm_strike - best_long_strike

            ts_data = build_spread_timeseries(
                short_data["bars"], long_data["bars"], sd["spike"]["time"]
            )

            if ts_data:
                ts_data["actual_width"] = actual_width
                ts_data["vix"] = sd["vix"]
                ts_data["date"] = sd["date"]
                ts_data["drop_pct"] = sd["spike"]["drop_pct"]
                precomputed[(si, width)] = ts_data
                precompute_count += 1

        if (si + 1) % 1000 == 0:
            print(f"    Pre-computed {si+1}/{len(spike_data)} spikes, {precompute_count} timeseries")

    print(f"  Total pre-computed timeseries: {precompute_count}")
    print()

    # ── Run the parameter sweep (fast — just integer comparisons) ────────
    print("--- Running parameter sweep ---")

    combo_num = 0
    for drop_thresh in drop_thresholds:
        for width in spread_widths:
            for hold in hold_times:
                for sl in sl_mults:
                    for pt in pt_pcts:
                        combo_num += 1
                        if combo_num % 200 == 0:
                            print(f"  [{combo_num}/{total_combos}] drop={drop_thresh} w={width} h={hold} sl={sl} pt={pt}")

                        trades = []

                        for si, sd in enumerate(spike_data):
                            if sd["spike"]["drop_pct"] < drop_thresh:
                                continue

                            key = (si, width)
                            ts_data = precomputed.get(key)
                            if not ts_data:
                                continue

                            actual_width = ts_data["actual_width"]

                            result = sweep_spread_params(
                                ts_data, hold, sl, pt, actual_width
                            )

                            if not result:
                                continue

                            max_loss_per = actual_width * 100
                            risk = BASE_RISK
                            if ts_data["vix"] >= 25:
                                risk = int(risk * 1.3)

                            contracts = max(1, int(risk / max_loss_per))
                            gross_pnl = result["pnl_per_contract"] * contracts
                            slip = SLIPPAGE_PER_LEG * 2 * 100 * contracts
                            net_pnl = gross_pnl - slip

                            trades.append({
                                "date": ts_data["date"],
                                "entry_time": result["entry_time"],
                                "exit_time": result["exit_time"],
                                "credit": result["credit"],
                                "close_debit": result["close_debit"],
                                "contracts": contracts,
                                "gross_pnl": round(gross_pnl, 2),
                                "net_pnl": round(net_pnl, 2),
                                "pnl_pct": result["pnl_pct_of_credit"],
                                "exit_reason": result["exit_reason"],
                                "hold_mins": result["hold_mins"],
                                "vix": ts_data["vix"],
                                "drop_pct": ts_data["drop_pct"],
                                "short_entry": result["short_entry"],
                                "long_entry": result["long_entry"],
                            })

                        if len(trades) < 10:
                            continue

                        net_pnls = [t["net_pnl"] for t in trades]
                        total = sum(net_pnls)
                        n = len(trades)
                        wins = sum(1 for p in net_pnls if p > 0)
                        wr = wins / n * 100
                        gw = sum(p for p in net_pnls if p > 0)
                        gl = sum(abs(p) for p in net_pnls if p <= 0)
                        pf = gw / gl if gl > 0 else 999

                        cum = peak_cum = dd = 0
                        for p in net_pnls:
                            cum += p
                            peak_cum = max(peak_cum, cum)
                            dd = max(dd, peak_cum - cum)

                        monthly = defaultdict(float)
                        for t in trades:
                            monthly[t["date"][:7]] += t["net_pnl"]
                        mv = list(monthly.values())
                        sharpe = mean(mv) / stdev(mv) * (12 ** 0.5) if len(mv) > 3 and stdev(mv) > 0 else 0

                        avg_hold = mean([t["hold_mins"] for t in trades])
                        avg_credit = mean([t["credit"] for t in trades])

                        all_results.append({
                            "type": "PUT_SPREAD",
                            "drop": drop_thresh, "width": width, "hold": hold,
                            "sl": sl, "pt": pt,
                            "trades": n, "total": total, "wr": wr, "pf": pf,
                            "dd": dd, "sharpe": sharpe, "avg_hold": avg_hold,
                            "per_trade": total / n, "avg_credit": avg_credit,
                            "all_trades": trades,
                        })

    print()
    print(f"  Sweep complete. Valid put spread combos: {len(all_results)}")
    print()

    # ── Print put spread results ─────────────────────────────────────────
    if all_results:
        # By total P&L
        sorted_pnl = sorted(all_results, key=lambda x: x["total"], reverse=True)
        print("TOP 30 PUT SPREADS BY TOTAL P&L:")
        print(f"{'Drop':>5} {'W':>3} {'Hold':>5} {'SL':>5} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Cr':>6}")
        print("-" * 110)
        for r in sorted_pnl[:30]:
            sl_str = f"{r['sl']:.1f}x" if r['sl'] else "none"
            pt_str = f"{r['pt']}%" if r['pt'] else "none"
            print(f"{r['drop']:>5.2f} {r['width']:>3} {r['hold']:>4}m {sl_str:>5} {pt_str:>5} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
                  f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} ${r['avg_credit']:>5.2f}")
        print()

        # By Sharpe (min 30 trades)
        sorted_sharpe = [r for r in all_results if r["trades"] >= 30]
        sorted_sharpe.sort(key=lambda x: x["sharpe"], reverse=True)
        print("TOP 30 PUT SPREADS BY SHARPE (min 30 trades):")
        print(f"{'Drop':>5} {'W':>3} {'Hold':>5} {'SL':>5} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Cr':>6}")
        print("-" * 110)
        for r in sorted_sharpe[:30]:
            sl_str = f"{r['sl']:.1f}x" if r['sl'] else "none"
            pt_str = f"{r['pt']}%" if r['pt'] else "none"
            print(f"{r['drop']:>5.2f} {r['width']:>3} {r['hold']:>4}m {sl_str:>5} {pt_str:>5} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
                  f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} ${r['avg_credit']:>5.2f}")
        print()

        # By profit factor (min 30 trades, positive P&L)
        sorted_pf = [r for r in all_results if r["trades"] >= 30 and r["total"] > 5000]
        sorted_pf.sort(key=lambda x: x["pf"], reverse=True)
        print("TOP 30 PUT SPREADS BY PROFIT FACTOR (min 30 trades, min $5K):")
        print(f"{'Drop':>5} {'W':>3} {'Hold':>5} {'SL':>5} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Cr':>6}")
        print("-" * 110)
        for r in sorted_pf[:30]:
            sl_str = f"{r['sl']:.1f}x" if r['sl'] else "none"
            pt_str = f"{r['pt']}%" if r['pt'] else "none"
            print(f"{r['drop']:>5.2f} {r['width']:>3} {r['hold']:>4}m {sl_str:>5} {pt_str:>5} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
                  f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} ${r['avg_credit']:>5.2f}")
        print()

        # By win rate (min 30 trades, positive P&L)
        sorted_wr = [r for r in all_results if r["trades"] >= 30 and r["total"] > 5000]
        sorted_wr.sort(key=lambda x: x["wr"], reverse=True)
        print("TOP 20 PUT SPREADS BY WIN RATE (min 30 trades, positive P&L):")
        print(f"{'Drop':>5} {'W':>3} {'Hold':>5} {'SL':>5} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7}")
        print("-" * 100)
        for r in sorted_wr[:20]:
            sl_str = f"{r['sl']:.1f}x" if r['sl'] else "none"
            pt_str = f"{r['pt']}%" if r['pt'] else "none"
            print(f"{r['drop']:>5.2f} {r['width']:>3} {r['hold']:>4}m {sl_str:>5} {pt_str:>5} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
                  f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f}")
        print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: DEEP DIVE ON BEST PUT SPREAD EDGES
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 95)
    print("PHASE 2: DEEP DIVE ON BEST PUT SPREAD EDGES")
    print("=" * 95)
    print()

    # Take top 5 unique configs by Sharpe (min 30 trades)
    seen = set()
    top_edges = []
    for r in sorted_sharpe:
        key = (r["drop"], r["width"], r["hold"], r["sl"], r["pt"])
        if key not in seen and r["trades"] >= 30:
            seen.add(key)
            top_edges.append(r)
            if len(top_edges) >= 5:
                break

    # Also include best by total P&L
    for r in sorted_pnl[:2]:
        key = (r["drop"], r["width"], r["hold"], r["sl"], r["pt"])
        if key not in seen and r["trades"] >= 20:
            seen.add(key)
            top_edges.append(r)

    for i, edge in enumerate(top_edges):
        sl_str = f"SL={edge['sl']:.1f}x" if edge['sl'] else "no SL"
        pt_str = f"PT={edge['pt']}%" if edge['pt'] else "no PT"
        print(f"EDGE {i+1}: drop>={edge['drop']:.2f}% | {edge['width']}pt spread | hold={edge['hold']}m | {sl_str} | {pt_str}")
        print(f"  Trades: {edge['trades']}  Win%: {edge['wr']:.1f}%  PF: {edge['pf']:.2f}  "
              f"Total: ${edge['total']:,.0f}  Sharpe: {edge['sharpe']:.2f}  MaxDD: ${edge['dd']:,.0f}")
        print(f"  Avg credit: ${edge['avg_credit']:.2f}  Per trade: ${edge['per_trade']:,.0f}  Avg hold: {edge['avg_hold']:.1f}m")

        trades = edge["all_trades"]

        # Yearly breakdown
        yearly = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for t in trades:
            yr = t["date"][:4]
            yearly[yr]["pnl"] += t["net_pnl"]
            yearly[yr]["trades"] += 1
            if t["net_pnl"] > 0:
                yearly[yr]["wins"] += 1

        print(f"  Yearly:")
        for yr in sorted(yearly.keys()):
            y = yearly[yr]
            wr = y["wins"] / y["trades"] * 100 if y["trades"] > 0 else 0
            print(f"    {yr}: {y['trades']:>4} trades  {wr:>5.1f}% win  ${y['pnl']:>12,.0f}")

        # By VIX regime
        vix_low = [t for t in trades if t["vix"] < 18]
        vix_mid = [t for t in trades if 18 <= t["vix"] < 25]
        vix_high = [t for t in trades if t["vix"] >= 25]

        print(f"  By VIX:")
        for label, bucket in [("VIX<18", vix_low), ("18<=VIX<25", vix_mid), ("VIX>=25", vix_high)]:
            if len(bucket) < 3:
                continue
            pnl = sum(t["net_pnl"] for t in bucket)
            wr = sum(1 for t in bucket if t["net_pnl"] > 0) / len(bucket) * 100
            print(f"    {label}: {len(bucket)} trades  {wr:.1f}% win  ${pnl:>12,.0f}")

        # By exit reason
        by_reason = defaultdict(lambda: {"count": 0, "pnl": 0})
        for t in trades:
            by_reason[t["exit_reason"]]["count"] += 1
            by_reason[t["exit_reason"]]["pnl"] += t["net_pnl"]
        print(f"  By exit reason:")
        for reason in sorted(by_reason.keys()):
            r = by_reason[reason]
            print(f"    {reason}: {r['count']} trades  ${r['pnl']:>12,.0f}")

        # By time of day
        morning = [t for t in trades if t["entry_time"] < "10:30"]
        midday = [t for t in trades if "10:30" <= t["entry_time"] < "13:00"]
        afternoon = [t for t in trades if t["entry_time"] >= "13:00"]
        print(f"  By time of day:")
        for label, bucket in [("9:35-10:30", morning), ("10:30-13:00", midday), ("13:00+", afternoon)]:
            if len(bucket) < 3:
                continue
            pnl = sum(t["net_pnl"] for t in bucket)
            wr = sum(1 for t in bucket if t["net_pnl"] > 0) / len(bucket) * 100
            print(f"    {label}: {len(bucket)} trades  {wr:.1f}% win  ${pnl:>12,.0f}")

        # Sample trades
        sorted_trades = sorted(trades, key=lambda t: t["net_pnl"], reverse=True)
        print(f"  Best 5 trades:")
        for t in sorted_trades[:5]:
            print(f"    {t['date']} {t['entry_time']}-{t['exit_time']}  "
                  f"cr ${t['credit']:.2f}  {t['contracts']}c  ${t['net_pnl']:>+10,.0f}  {t['exit_reason']}")
        print(f"  Worst 5 trades:")
        for t in sorted_trades[-5:]:
            print(f"    {t['date']} {t['entry_time']}-{t['exit_time']}  "
                  f"cr ${t['credit']:.2f}  {t['contracts']}c  ${t['net_pnl']:>+10,.0f}  {t['exit_reason']}")
        print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: SELL CALL SPREAD ON RIPS (BEAR CALL SPREAD)
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 95)
    print("PHASE 3: SELL CALL SPREAD ON INTRADAY RIP (BEAR CALL SPREAD)")
    print("=" * 95)
    print()
    print("Strategy: When SPX rips UP sharply, sell ATM call + buy OTM call")
    print("Profit from: mean reversion down + theta decay")
    print()

    rip_results = []

    for rip_thresh in [0.20, 0.30, 0.40, 0.50]:
        # Collect rip data
        rip_data = []
        for di, date in enumerate(dates_0dte):
            bars = intraday[date]
            if len(bars) < 30:
                continue
            vix = vix_daily.get(date, 20)
            rips = detect_rips_1min(bars, rip_thresh, lookback_mins=10,
                                    min_time="09:35", max_time="14:30")
            for rip in rips:
                spx_level = rip["entry_price"]
                atm_ticker, atm_actual = find_option(date, spx_level, "C")
                if not atm_ticker:
                    continue
                atm_bars = fetch_option_bars(atm_ticker, date, "10s")
                if not atm_bars or len(atm_bars) < 10:
                    atm_bars = fetch_option_bars(atm_ticker, date, "1min")
                if not atm_bars or len(atm_bars) < 5:
                    continue

                otm_calls = {}
                otm_calls[atm_actual] = {"ticker": atm_ticker, "bars": atm_bars}

                for width in [10, 20, 30]:
                    otm_target = spx_level + width
                    otm_ticker, otm_actual = find_option(date, otm_target, "C")
                    if otm_ticker and otm_actual not in otm_calls:
                        otm_bars = fetch_option_bars(otm_ticker, date, "10s")
                        if not otm_bars or len(otm_bars) < 10:
                            otm_bars = fetch_option_bars(otm_ticker, date, "1min")
                        if otm_bars and len(otm_bars) >= 5:
                            otm_calls[otm_actual] = {"ticker": otm_ticker, "bars": otm_bars}

                rip_data.append({
                    "date": date, "rip": rip, "vix": vix,
                    "calls": otm_calls, "atm_strike": atm_actual,
                })

        print(f"  Rip threshold >= {rip_thresh:.2f}%: {len(rip_data)} events")

        # Pre-compute call spread timeseries
        rip_precomputed = {}
        for ri, rd in enumerate(rip_data):
            atm_strike = rd["atm_strike"]
            if not atm_strike:
                continue
            short_data = rd["calls"].get(atm_strike)
            if not short_data:
                continue
            for width in [10, 20, 30]:
                best_long_strike = None
                best_diff = 999
                for strike in rd["calls"]:
                    if strike > atm_strike:
                        diff = abs((strike - atm_strike) - width)
                        if diff < best_diff:
                            best_diff = diff
                            best_long_strike = strike
                if best_long_strike is None or best_diff > 10:
                    continue
                long_data = rd["calls"][best_long_strike]
                actual_width = best_long_strike - atm_strike
                ts_data = build_spread_timeseries(
                    short_data["bars"], long_data["bars"], rd["rip"]["time"]
                )
                if ts_data:
                    ts_data["actual_width"] = actual_width
                    ts_data["vix"] = rd["vix"]
                    ts_data["date"] = rd["date"]
                    rip_precomputed[(ri, width)] = ts_data

        print(f"    Pre-computed {len(rip_precomputed)} call spread timeseries")

        for width in [10, 20, 30]:
            for hold in [5, 10, 15, 30, 60]:
                for sl in [None, 2.0]:
                    for pt in [None, 50]:
                        trades = []
                        for ri, rd in enumerate(rip_data):
                            key = (ri, width)
                            ts_data = rip_precomputed.get(key)
                            if not ts_data:
                                continue

                            actual_width = ts_data["actual_width"]
                            result = sweep_spread_params(ts_data, hold, sl, pt, actual_width)
                            if not result:
                                continue

                            max_loss_per = actual_width * 100
                            contracts = max(1, int(BASE_RISK / max_loss_per))
                            gross_pnl = result["pnl_per_contract"] * contracts
                            slip = SLIPPAGE_PER_LEG * 2 * 100 * contracts
                            net_pnl = gross_pnl - slip

                            trades.append({
                                "date": ts_data["date"], "net_pnl": round(net_pnl, 2),
                                "exit_reason": result["exit_reason"],
                                "vix": ts_data["vix"],
                            })

                        if len(trades) < 10:
                            continue

                        net_pnls = [t["net_pnl"] for t in trades]
                        total = sum(net_pnls)
                        n = len(trades)
                        wins = sum(1 for p in net_pnls if p > 0)
                        wr = wins / n * 100

                        monthly = defaultdict(float)
                        for t in trades:
                            monthly[t["date"][:7]] += t["net_pnl"]
                        mv = list(monthly.values())
                        sharpe = mean(mv) / stdev(mv) * (12 ** 0.5) if len(mv) > 3 and stdev(mv) > 0 else 0

                        rip_results.append({
                            "rip": rip_thresh, "width": width, "hold": hold,
                            "sl": sl, "pt": pt,
                            "trades": n, "total": total, "wr": wr, "sharpe": sharpe,
                            "per_trade": total / n,
                        })

    if rip_results:
        rip_results.sort(key=lambda x: x["total"], reverse=True)
        print()
        print("TOP 20 CALL SPREADS ON RIPS BY TOTAL P&L:")
        print(f"{'Rip':>5} {'W':>3} {'Hold':>5} {'SL':>5} {'PT':>5} | {'N':>5} {'Win%':>6} {'Total P&L':>12} {'$/Trade':>9} {'Sharpe':>7}")
        print("-" * 85)
        for r in rip_results[:20]:
            sl_str = f"{r['sl']:.1f}x" if r['sl'] else "none"
            pt_str = f"{r['pt']}%" if r['pt'] else "none"
            print(f"{r['rip']:>5.2f} {r['width']:>3} {r['hold']:>4}m {sl_str:>5} {pt_str:>5} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} {r['sharpe']:>7.2f}")
    else:
        print("  No valid call spread combos found.")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 95)
    print("FINAL SUMMARY — SHORT SPREADS ON VOL SPIKES")
    print("=" * 95)
    print()

    if sorted_sharpe:
        best = sorted_sharpe[0]
        sl_str = f"SL={best['sl']:.1f}x credit" if best['sl'] else "no SL"
        pt_str = f"PT={best['pt']}% of credit" if best['pt'] else "no PT"
        print(f"BEST PUT SPREAD EDGE (by Sharpe):")
        print(f"  Trigger: SPX drops >= {best['drop']:.2f}% from 10-min rolling high")
        print(f"  Action:  SELL {best['width']}pt put spread (sell ATM put, buy OTM put)")
        print(f"  Exit:    Hold {best['hold']}m / {sl_str} / {pt_str}")
        print(f"  Results: {best['trades']} trades | {best['wr']:.1f}% WR | PF {best['pf']:.2f} | "
              f"${best['total']:,.0f} total | Sharpe {best['sharpe']:.2f}")
        print(f"  MaxDD: ${best['dd']:,.0f} | Avg credit: ${best['avg_credit']:.2f} | Per trade: ${best['per_trade']:,.0f}")
        print()

    if sorted_pnl:
        best = sorted_pnl[0]
        sl_str = f"SL={best['sl']:.1f}x credit" if best['sl'] else "no SL"
        pt_str = f"PT={best['pt']}% of credit" if best['pt'] else "no PT"
        print(f"BEST PUT SPREAD EDGE (by Total P&L):")
        print(f"  Trigger: SPX drops >= {best['drop']:.2f}% from 10-min rolling high")
        print(f"  Action:  SELL {best['width']}pt put spread")
        print(f"  Exit:    Hold {best['hold']}m / {sl_str} / {pt_str}")
        print(f"  Results: {best['trades']} trades | {best['wr']:.1f}% WR | PF {best['pf']:.2f} | "
              f"${best['total']:,.0f} total | Sharpe {best['sharpe']:.2f}")
        print()

    if rip_results and rip_results[0]["total"] > 0:
        best_rip = rip_results[0]
        sl_str = f"SL={best_rip['sl']:.1f}x" if best_rip['sl'] else "no SL"
        pt_str = f"PT={best_rip['pt']}%" if best_rip['pt'] else "no PT"
        print(f"BEST CALL SPREAD ON RIPS (by Total P&L):")
        print(f"  Trigger: SPX rips >= {best_rip['rip']:.2f}% from 10-min rolling low")
        print(f"  Action:  SELL {best_rip['width']}pt call spread")
        print(f"  Exit:    Hold {best_rip['hold']}m / {sl_str} / {pt_str}")
        print(f"  Results: {best_rip['trades']} trades | {best_rip['wr']:.1f}% WR | "
              f"${best_rip['total']:,.0f} total | Sharpe {best_rip['sharpe']:.2f}")
        print()

    print("Script complete.")
    print("=" * 95)

    sys.stdout = original_stdout
    report_file.close()
    print(f"\nReport saved to: {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
