"""
SPX Opening Print — Script 37: Vol Spike Mean Reversion with REAL Option Prices
================================================================================
Refines the vol spike strategy from script 36 by using actual Polygon option
10-second bars instead of synthetic delta models.

Script 36 showed:
  - Raw mean reversion IS real (72% bounce rate after 0.20% dips)
  - But buying calls with delta model lost money (underestimated 0DTE gamma)
  - Credit spreads showed 94% WR but with simplified pricing

This script:
  1. Detects intraday dips from SPX 1-min rolling highs (same as script 36)
  2. At each spike, finds the ATM CALL/PUT via Polygon snapshot
  3. Fetches REAL 10-second option bars from Polygon
  4. Simulates exit using actual option prices at 10s resolution
  5. Sweeps: drop threshold, hold time (1m to 120m), trail/SL/time stop
  6. Tests both buying calls on dips AND selling puts/spreads
  7. Reports with real option P&L including $0.50 round-trip slippage

Uses same API/cache infrastructure as script 33.

Usage:
    python3 37_vol_spike_real_options.py
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev, median
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
VIX_1MIN = os.path.join(SCRIPT_DIR, "vix_1min_bars.csv")
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "vol_spike_real_options_report.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "vol_spike_output")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

BASE_RISK = 75000
SLIPPAGE_PER_CONTRACT = 0.50  # $0.50 round-trip per contract


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
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_intraday():
    """Load SPX 1-min bars by date."""
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


def load_vix_1min():
    days = defaultdict(list)
    if not os.path.exists(VIX_1MIN):
        return {}
    with open(VIX_1MIN, "r") as f:
        for row in csv.DictReader(f):
            t = row["time"]
            if t < "09:30" or t >= "16:00":
                continue
            days[row["date"]].append({
                "time": t,
                "close": float(row["close"]),
            })
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return dict(days)


# ══════════════════════════════════════════════════════════════════════════════
# CACHE / API HELPERS (same as script 33)
# ══════════════════════════════════════════════════════════════════════════════

def ensure_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


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
# OPTION FUNCTIONS (from script 33)
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


def find_option(date_str, target_strike, option_type="C"):
    """Find SPX option (call or put) — checks SPXW then SPX."""
    strike = round(target_strike / 5) * 5
    cache_key = f"contracts_v3_{date_str}_{strike}_{option_type}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None, None, None
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]

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
                return ticker, test_strike, expiry_date, dte

    save_cache(cache_key, "none")
    return None, None, None, None


def fetch_10s_bars(ticker, date_str, cache_prefix):
    """Fetch 10-second bars for any ticker, with pagination and caching."""
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"{cache_prefix}_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else []

    offset_hours = get_dst_offset(date_str)
    all_bars = []
    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/10/second/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    time.sleep(REQUEST_DELAY)
    data = api_get(url)

    if not data or data.get("resultsCount", 0) == 0:
        save_cache(cache_key, "none")
        return []

    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime("%H:%M:%S")
        if "09:30:00" <= t_str <= "16:05:00":
            all_bars.append({
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
            })

    while data and data.get("next_url"):
        next_url = data["next_url"] + f"&apiKey={API_KEY}"
        time.sleep(REQUEST_DELAY)
        data = api_get(next_url)
        if data and data.get("results"):
            for r in data["results"]:
                dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
                dt_et = dt_utc - timedelta(hours=offset_hours)
                t_str = dt_et.strftime("%H:%M:%S")
                if "09:30:00" <= t_str <= "16:05:00":
                    all_bars.append({
                        "time": t_str, "open": r["o"], "high": r["h"],
                        "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
                    })

    all_bars.sort(key=lambda b: b["time"])
    save_cache(cache_key, all_bars if all_bars else "none")
    return all_bars


def fetch_1min_bars(ticker, date_str):
    """Fetch 1-minute option bars with caching."""
    safe = ticker.replace(':', '_').replace('/', '_')
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
    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime("%H:%M")
        if "09:30" <= t_str <= "16:00":
            bars.append({
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"],
            })
    save_cache(cache_key, bars if bars else "none")
    return bars


# ══════════════════════════════════════════════════════════════════════════════
# SPIKE DETECTION (same as script 36)
# ══════════════════════════════════════════════════════════════════════════════

def time_to_seconds(t):
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def detect_spikes_1min(bars, drop_pct, lookback_mins, min_time="09:35", max_time="15:30"):
    """
    Detect intraday drops >= drop_pct% from rolling high in lookback window.
    Returns list of spike dicts with bar_idx, time, drop_pct, entry_price.
    Deduplicates: keeps deepest spike per 5-min window.
    """
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
                "bar_idx": i,
                "time": bar["time"],
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


# ══════════════════════════════════════════════════════════════════════════════
# REAL OPTIONS TRADE SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

def find_option_bar_at_time(opt_bars, target_time, direction="after"):
    """
    Find the option bar closest to target_time.
    direction='after': first bar at or after target_time
    direction='before': last bar at or before target_time
    direction='nearest': closest bar
    """
    if not opt_bars:
        return None

    target_secs = time_to_seconds(target_time if ":" in target_time and len(target_time) >= 7
                                   else target_time + ":00")

    if direction == "after":
        for bar in opt_bars:
            if time_to_seconds(bar["time"]) >= target_secs:
                return bar
        return opt_bars[-1]  # fallback to last
    elif direction == "before":
        for bar in reversed(opt_bars):
            if time_to_seconds(bar["time"]) <= target_secs:
                return bar
        return opt_bars[0]  # fallback to first
    else:  # nearest
        best = None
        best_diff = float("inf")
        for bar in opt_bars:
            diff = abs(time_to_seconds(bar["time"]) - target_secs)
            if diff < best_diff:
                best_diff = diff
                best = bar
        return best


def simulate_real_options_trade(opt_bars, entry_time, trail_pct, sl_pct, ts_mins,
                                 pt_pct=None, use_10s=True):
    """
    Simulate a long option trade using REAL option bars.

    Entry: buy at first option bar at/after entry_time
    Exit:  trail stop on option price, fixed SL on option price, time stop, or PT

    All exits based on actual option prices — no delta modeling needed.

    Returns dict with entry/exit prices, P&L, etc.
    """
    if not opt_bars or len(opt_bars) < 5:
        return None

    # Find entry bar
    entry_secs = time_to_seconds(entry_time if len(entry_time) >= 7 else entry_time + ":00")
    entry_bar = None
    entry_idx = None

    for i, bar in enumerate(opt_bars):
        if time_to_seconds(bar["time"]) >= entry_secs:
            entry_bar = bar
            entry_idx = i
            break

    if entry_bar is None:
        return None

    opt_entry = entry_bar["close"]
    if opt_entry <= 0.05:
        return None

    # Calculate exit levels
    sl_level = opt_entry * (1 - sl_pct / 100) if sl_pct else 0
    pt_level = opt_entry * (1 + pt_pct / 100) if pt_pct else None

    # Time stop
    stop_secs = entry_secs + ts_mins * 60
    stop_time = f"{stop_secs // 3600:02d}:{(stop_secs % 3600) // 60:02d}:{stop_secs % 60:02d}"

    # Track peak for trailing
    peak_price = opt_entry
    exit_bar = None
    exit_reason = None

    for i in range(entry_idx + 1, len(opt_bars)):
        bar = opt_bars[i]
        bar_secs = time_to_seconds(bar["time"])

        # Time stop check
        if bar_secs >= stop_secs:
            exit_bar = bar
            exit_reason = "Time Stop"
            break

        # Update peak
        if bar["high"] > peak_price:
            peak_price = bar["high"]

        # Profit target
        if pt_level and bar["high"] >= pt_level:
            exit_bar = bar
            exit_reason = "Profit Target"
            break

        # Stop loss (based on option price dropping)
        if sl_level and bar["low"] <= sl_level:
            exit_bar = bar
            exit_reason = "Stop Loss"
            break

        # Trailing stop (only activates after option moves up from entry)
        if trail_pct and peak_price > opt_entry * 1.01:  # need at least 1% gain before trail
            trail_level = peak_price * (1 - trail_pct / 100)
            if bar["low"] <= trail_level:
                exit_bar = bar
                exit_reason = "Trailing Stop"
                break

    if exit_bar is None:
        exit_bar = opt_bars[-1]
        exit_reason = "Time Stop (EOD)"

    opt_exit = exit_bar["close"]

    # P&L
    entry_secs_actual = time_to_seconds(entry_bar["time"])
    exit_secs_actual = time_to_seconds(exit_bar["time"])
    hold_secs = exit_secs_actual - entry_secs_actual
    hold_mins = max(0.17, hold_secs / 60)  # at least 10s

    return {
        "entry_time": entry_bar["time"],
        "exit_time": exit_bar["time"],
        "opt_entry": round(opt_entry, 2),
        "opt_exit": round(opt_exit, 2),
        "opt_peak": round(peak_price, 2),
        "pnl_per_contract": round((opt_exit - opt_entry) * 100, 2),
        "pnl_pct": round((opt_exit - opt_entry) / opt_entry * 100, 2) if opt_entry > 0 else 0,
        "exit_reason": exit_reason,
        "hold_mins": round(hold_mins, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ensure_dirs()

    report_file = open(OUTPUT_REPORT, "w")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, report_file)

    print("=" * 90)
    print("VOL SPIKE MEAN REVERSION — REAL POLYGON OPTION PRICES")
    print("=" * 90)
    print()

    # ── Load data ────────────────────────────────────────────────────────
    print("Loading data...")
    intraday = load_intraday()
    daily = load_daily()
    vix_daily = load_vix_daily()
    vix_1min = load_vix_1min()

    dates = sorted(intraday.keys())
    print(f"  SPX 1-min days: {len(dates)}")
    print(f"  Date range: {dates[0]} to {dates[-1]}")

    # Only use dates from 2022-09-19+ (daily 0DTE available)
    dates_0dte = [d for d in dates if d >= "2022-09-19"]
    print(f"  0DTE-eligible dates (>= 2022-09-19): {len(dates_0dte)}")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: DETECT ALL SPIKES AND CATALOG OPTION DATA AVAILABILITY
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("PHASE 1: DETECT SPIKES & FETCH OPTION DATA")
    print("=" * 90)
    print()

    # Use 0.20% as minimum detection threshold — sweep tighter later
    ALL_SPIKES = []
    option_found = 0
    option_missed = 0
    bars_found = 0
    bars_missed = 0

    for di, date in enumerate(dates_0dte):
        bars = intraday[date]
        if len(bars) < 30:
            continue

        vix = vix_daily.get(date, 20)

        # Detect with broad threshold — we'll filter tighter in Phase 2
        spikes = detect_spikes_1min(bars, drop_pct=0.15, lookback_mins=10,
                                     min_time="09:35", max_time="15:00")

        if not spikes:
            continue

        if (di + 1) % 50 == 0 or di == 0:
            print(f"  [{di+1}/{len(dates_0dte)}] {date}: {len(spikes)} spikes detected")

        for spike in spikes:
            entry_price = spike["entry_price"]  # SPX level at spike

            # Find ATM CALL option
            ticker, strike, expiry, dte = find_option(date, entry_price, "C")

            if not ticker:
                option_missed += 1
                continue
            option_found += 1

            # Fetch option 10s bars (this is the key data!)
            opt_10s = fetch_10s_bars(ticker, date, "opt10s")

            # Also try 1-min bars as fallback
            opt_1min = []
            if not opt_10s or len(opt_10s) < 10:
                opt_1min = fetch_1min_bars(ticker, date)

            has_bars = len(opt_10s) > 10 or len(opt_1min) > 5
            if has_bars:
                bars_found += 1
            else:
                bars_missed += 1
                continue

            ALL_SPIKES.append({
                "date": date,
                "spike_time": spike["time"],
                "spike_idx": spike["bar_idx"],
                "drop_pct": spike["drop_pct"],
                "rolling_high": spike["rolling_high"],
                "spike_low": spike["spike_low"],
                "entry_spx": entry_price,
                "vix": vix,
                "option_ticker": ticker,
                "option_strike": strike,
                "opt_10s": opt_10s if len(opt_10s) > 10 else None,
                "opt_1min": opt_1min if len(opt_1min) > 5 else None,
            })

    print()
    print(f"  Total spikes detected: {option_found + option_missed}")
    print(f"  Options found: {option_found}  |  missed: {option_missed}")
    print(f"  With bar data: {bars_found}  |  without: {bars_missed}")
    print(f"  Tradeable spikes: {len(ALL_SPIKES)}")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: PARAMETER SWEEP — BUY ATM CALL ON DIP
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("PHASE 2: PARAMETER SWEEP — BUY ATM CALL ON DIP (REAL OPTION PRICES)")
    print("=" * 90)
    print()

    # Parameter grid
    drop_thresholds = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    trail_pcts = [3, 5, 8, 10, 15, 20, 30, 50]           # % of option price
    sl_pcts = [10, 15, 20, 30, 40, 50, 75]                # % of option price
    ts_values = [5, 10, 15, 30, 60, 90, 120]              # minutes
    pt_pcts = [None, 10, 20, 30, 50, 100, 200]            # % of option price (None = no PT, trail only)

    # For the main sweep, fix some dimensions and sweep key ones
    # First: sweep drop threshold + hold time (ts) with moderate trail/SL
    print("--- Stage 1: Drop threshold × Hold time (trail=10%, SL=30%) ---")
    print()

    stage1_results = []

    for drop_thresh in drop_thresholds:
        for ts in ts_values:
            eligible = [s for s in ALL_SPIKES if s["drop_pct"] >= drop_thresh]
            if len(eligible) < 10:
                continue

            trades = []
            for spike in eligible:
                opt_bars = spike["opt_10s"] or spike["opt_1min"]
                if not opt_bars:
                    continue

                result = simulate_real_options_trade(
                    opt_bars,
                    entry_time=spike["spike_time"] + ":00" if len(spike["spike_time"]) <= 5 else spike["spike_time"],
                    trail_pct=10,
                    sl_pct=30,
                    ts_mins=ts,
                    pt_pct=None,
                )

                if not result:
                    continue

                # Size the trade
                opt_entry = result["opt_entry"]
                if opt_entry <= 0.10:
                    continue

                risk = BASE_RISK
                if spike["vix"] and spike["vix"] >= 25:
                    risk = int(risk * 1.3)

                contracts = max(1, int(risk / (opt_entry * 100)))
                pnl = result["pnl_per_contract"] * contracts
                slip = SLIPPAGE_PER_CONTRACT * 100 * contracts
                net_pnl = pnl - slip

                trades.append({
                    "date": spike["date"],
                    "entry_time": result["entry_time"],
                    "exit_time": result["exit_time"],
                    "opt_entry": result["opt_entry"],
                    "opt_exit": result["opt_exit"],
                    "opt_peak": result["opt_peak"],
                    "contracts": contracts,
                    "gross_pnl": round(pnl, 2),
                    "net_pnl": round(net_pnl, 2),
                    "pnl_pct": result["pnl_pct"],
                    "exit_reason": result["exit_reason"],
                    "hold_mins": result["hold_mins"],
                    "vix": spike["vix"],
                    "drop_pct": spike["drop_pct"],
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

            # Max drawdown
            cum = peak = dd = 0
            for p in net_pnls:
                cum += p
                peak = max(peak, cum)
                dd = max(dd, peak - cum)

            # Monthly Sharpe
            monthly = defaultdict(float)
            for t in trades:
                monthly[t["date"][:7]] += t["net_pnl"]
            mv = list(monthly.values())
            sharpe = mean(mv) / stdev(mv) * (12 ** 0.5) if len(mv) > 3 and stdev(mv) > 0 else 0

            avg_hold = mean([t["hold_mins"] for t in trades])

            stage1_results.append({
                "drop": drop_thresh, "ts": ts,
                "trades": n, "total": total, "wr": wr, "pf": pf,
                "dd": dd, "sharpe": sharpe, "avg_hold": avg_hold,
                "per_trade": total / n,
                "all_trades": trades,
            })

    # Print stage 1 results
    stage1_results.sort(key=lambda x: x["total"], reverse=True)

    print(f"{'Drop':>5} {'TS':>4} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Hold':>6}")
    print("-" * 90)
    for r in stage1_results[:30]:
        print(f"{r['drop']:>5.2f} {r['ts']:>4} | {r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} "
              f"${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} ${r['dd']:>9,.0f} {r['sharpe']:>7.2f} {r['avg_hold']:>5.1f}m")
    print()

    # ── Stage 2: Full sweep on promising combos ──────────────────────────
    print("--- Stage 2: Full parameter sweep on best drop thresholds ---")
    print()

    # Find which drop thresholds had any positive results
    positive_drops = set()
    for r in stage1_results:
        if r["total"] > 0:
            positive_drops.add(r["drop"])

    # If none were positive, use all thresholds
    if not positive_drops:
        positive_drops = set(drop_thresholds)
        print("  (No positive combos in Stage 1 — sweeping all thresholds)")
    else:
        print(f"  Positive drop thresholds: {sorted(positive_drops)}")

    stage2_results = []

    sweep_count = 0
    for drop_thresh in sorted(positive_drops):
        eligible = [s for s in ALL_SPIKES if s["drop_pct"] >= drop_thresh]
        if len(eligible) < 10:
            continue

        for trail in trail_pcts:
            for sl in sl_pcts:
                for ts in ts_values:
                    for pt in [None, 20, 50, 100, 200]:
                        sweep_count += 1
                        trades = []

                        for spike in eligible:
                            opt_bars = spike["opt_10s"] or spike["opt_1min"]
                            if not opt_bars:
                                continue

                            result = simulate_real_options_trade(
                                opt_bars,
                                entry_time=spike["spike_time"] + ":00" if len(spike["spike_time"]) <= 5 else spike["spike_time"],
                                trail_pct=trail,
                                sl_pct=sl,
                                ts_mins=ts,
                                pt_pct=pt,
                            )

                            if not result or result["opt_entry"] <= 0.10:
                                continue

                            risk = BASE_RISK
                            if spike["vix"] and spike["vix"] >= 25:
                                risk = int(risk * 1.3)

                            contracts = max(1, int(risk / (result["opt_entry"] * 100)))
                            pnl = result["pnl_per_contract"] * contracts
                            slip = SLIPPAGE_PER_CONTRACT * 100 * contracts
                            net_pnl = pnl - slip

                            trades.append({
                                "date": spike["date"],
                                "net_pnl": round(net_pnl, 2),
                                "pnl_pct": result["pnl_pct"],
                                "exit_reason": result["exit_reason"],
                                "hold_mins": result["hold_mins"],
                                "vix": spike["vix"],
                                "opt_entry": result["opt_entry"],
                                "opt_exit": result["opt_exit"],
                                "contracts": contracts,
                                "drop_pct": spike["drop_pct"],
                                "entry_time": result["entry_time"],
                                "exit_time": result["exit_time"],
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

                        stage2_results.append({
                            "drop": drop_thresh, "trail": trail, "sl": sl,
                            "ts": ts, "pt": pt,
                            "trades": n, "total": total, "wr": wr, "pf": pf,
                            "dd": dd, "sharpe": sharpe, "avg_hold": avg_hold,
                            "per_trade": total / n,
                            "all_trades": trades,
                        })

    print(f"  Swept {sweep_count} combinations, {len(stage2_results)} valid")
    print()

    # Top 30 by total P&L
    stage2_results.sort(key=lambda x: x["total"], reverse=True)
    print("TOP 30 BY TOTAL P&L:")
    print(f"{'Drop':>5} {'Trail':>5} {'SL':>4} {'TS':>4} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Hold':>6}")
    print("-" * 105)
    for r in stage2_results[:30]:
        pt_str = f"{r['pt']}%" if r['pt'] else "none"
        print(f"{r['drop']:>5.2f} {r['trail']:>4}% {r['sl']:>3}% {r['ts']:>4} {pt_str:>5} | "
              f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
              f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} {r['avg_hold']:>5.1f}m")
    print()

    # Top 30 by Sharpe (min 30 trades)
    sharpe_sorted = [r for r in stage2_results if r["trades"] >= 30]
    sharpe_sorted.sort(key=lambda x: x["sharpe"], reverse=True)
    print("TOP 30 BY SHARPE (min 30 trades):")
    print(f"{'Drop':>5} {'Trail':>5} {'SL':>4} {'TS':>4} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Hold':>6}")
    print("-" * 105)
    for r in sharpe_sorted[:30]:
        pt_str = f"{r['pt']}%" if r['pt'] else "none"
        print(f"{r['drop']:>5.2f} {r['trail']:>4}% {r['sl']:>3}% {r['ts']:>4} {pt_str:>5} | "
              f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
              f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} {r['avg_hold']:>5.1f}m")
    print()

    # Top 30 by profit factor (min 30 trades, min $10K total)
    pf_sorted = [r for r in stage2_results if r["trades"] >= 30 and r["total"] > 10000]
    pf_sorted.sort(key=lambda x: x["pf"], reverse=True)
    print("TOP 30 BY PROFIT FACTOR (min 30 trades, min $10K total):")
    print(f"{'Drop':>5} {'Trail':>5} {'SL':>4} {'TS':>4} {'PT':>5} | {'N':>5} {'Win%':>6} {'PF':>6} {'Total P&L':>12} {'$/Trade':>9} {'MaxDD':>10} {'Sharpe':>7} {'Hold':>6}")
    print("-" * 105)
    for r in pf_sorted[:30]:
        pt_str = f"{r['pt']}%" if r['pt'] else "none"
        print(f"{r['drop']:>5.2f} {r['trail']:>4}% {r['sl']:>3}% {r['ts']:>4} {pt_str:>5} | "
              f"{r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>5.2f} ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} "
              f"${r['dd']:>9,.0f} {r['sharpe']:>7.2f} {r['avg_hold']:>5.1f}m")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: DEEP DIVE ON BEST EDGES
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("PHASE 3: DEEP DIVE ON BEST EDGES")
    print("=" * 90)
    print()

    # Take top 5 unique configs by Sharpe (min 30 trades)
    seen = set()
    top_edges = []
    for r in sharpe_sorted:
        key = (r["drop"], r["trail"], r["sl"], r["ts"], r["pt"])
        if key not in seen and r["trades"] >= 30:
            seen.add(key)
            top_edges.append(r)
            if len(top_edges) >= 5:
                break

    # Also include best by total P&L if different
    for r in stage2_results[:3]:
        key = (r["drop"], r["trail"], r["sl"], r["ts"], r["pt"])
        if key not in seen and r["trades"] >= 20:
            seen.add(key)
            top_edges.append(r)

    for i, edge in enumerate(top_edges):
        pt_str = f"PT={edge['pt']}%" if edge['pt'] else "Trail only"
        print(f"EDGE {i+1}: drop>={edge['drop']:.2f}% | trail={edge['trail']}% SL={edge['sl']}% TS={edge['ts']}m {pt_str}")
        print(f"  Trades: {edge['trades']}  Win%: {edge['wr']:.1f}%  PF: {edge['pf']:.2f}  "
              f"Total: ${edge['total']:,.0f}  Sharpe: {edge['sharpe']:.2f}  MaxDD: ${edge['dd']:,.0f}")
        print(f"  Avg hold: {edge['avg_hold']:.1f}m  Per trade: ${edge['per_trade']:,.0f}")

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

        # Sample trades (5 best, 5 worst)
        sorted_trades = sorted(trades, key=lambda t: t["net_pnl"], reverse=True)
        print(f"  Best 5 trades:")
        for t in sorted_trades[:5]:
            print(f"    {t['date']} {t['entry_time']}-{t['exit_time']}  "
                  f"opt ${t['opt_entry']:.2f}→${t['opt_exit']:.2f}  "
                  f"{t['contracts']}c  ${t['net_pnl']:>+10,.0f}  {t['exit_reason']}")
        print(f"  Worst 5 trades:")
        for t in sorted_trades[-5:]:
            print(f"    {t['date']} {t['entry_time']}-{t['exit_time']}  "
                  f"opt ${t['opt_entry']:.2f}→${t['opt_exit']:.2f}  "
                  f"{t['contracts']}c  ${t['net_pnl']:>+10,.0f}  {t['exit_reason']}")

        print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: BUY PUT ON SPIKE (SHORT THESIS — IF SPX KEEPS FALLING)
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("PHASE 4: BUY PUT ON SPIKE (MOMENTUM CONTINUATION)")
    print("=" * 90)
    print()
    print("Testing: Instead of buying calls on the dip, buy puts for momentum continuation")
    print()

    put_results = []

    for drop_thresh in [0.20, 0.30, 0.40, 0.50]:
        for ts in [5, 10, 15, 30, 60]:
            for trail in [5, 10, 15, 20]:
                for sl in [15, 30, 50]:
                    eligible = [s for s in ALL_SPIKES if s["drop_pct"] >= drop_thresh]
                    if len(eligible) < 10:
                        continue

                    trades = []
                    for spike in eligible:
                        # Find ATM PUT instead of call
                        # We need to look up the put — use cached data if available
                        put_ticker, put_strike, _, _ = find_option(spike["date"], spike["entry_spx"], "P")
                        if not put_ticker:
                            continue

                        put_10s = fetch_10s_bars(put_ticker, spike["date"], "opt10s")
                        put_1min = []
                        if not put_10s or len(put_10s) < 10:
                            put_1min = fetch_1min_bars(put_ticker, spike["date"])

                        opt_bars = put_10s if len(put_10s) > 10 else put_1min
                        if not opt_bars or len(opt_bars) < 5:
                            continue

                        result = simulate_real_options_trade(
                            opt_bars,
                            entry_time=spike["spike_time"] + ":00" if len(spike["spike_time"]) <= 5 else spike["spike_time"],
                            trail_pct=trail,
                            sl_pct=sl,
                            ts_mins=ts,
                            pt_pct=None,
                        )

                        if not result or result["opt_entry"] <= 0.10:
                            continue

                        risk = BASE_RISK
                        contracts = max(1, int(risk / (result["opt_entry"] * 100)))
                        pnl = result["pnl_per_contract"] * contracts
                        slip = SLIPPAGE_PER_CONTRACT * 100 * contracts
                        net_pnl = pnl - slip

                        trades.append({"date": spike["date"], "net_pnl": round(net_pnl, 2)})

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

                    put_results.append({
                        "drop": drop_thresh, "trail": trail, "sl": sl, "ts": ts,
                        "trades": n, "total": total, "wr": wr, "sharpe": sharpe,
                        "per_trade": total / n,
                    })

    if put_results:
        put_results.sort(key=lambda x: x["total"], reverse=True)
        print("TOP 20 PUT-BUYING COMBOS BY TOTAL P&L:")
        print(f"{'Drop':>5} {'Trail':>5} {'SL':>4} {'TS':>4} | {'N':>5} {'Win%':>6} {'Total P&L':>12} {'$/Trade':>9} {'Sharpe':>7}")
        print("-" * 80)
        for r in put_results[:20]:
            print(f"{r['drop']:>5.2f} {r['trail']:>4}% {r['sl']:>3}% {r['ts']:>4} | "
                  f"{r['trades']:>5} {r['wr']:>5.1f}% ${r['total']:>11,.0f} ${r['per_trade']:>8,.0f} {r['sharpe']:>7.2f}")
    else:
        print("  No valid put-buying combos found.")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 90)
    print("FINAL SUMMARY")
    print("=" * 90)
    print()

    if top_edges:
        best = top_edges[0]
        pt_str = f"PT={best['pt']}%" if best['pt'] else "no fixed PT"
        print(f"BEST CALL-BUYING EDGE (by Sharpe):")
        print(f"  Trigger: SPX drops >= {best['drop']:.2f}% from 10-min rolling high")
        print(f"  Action:  BUY ATM CALL at spike close")
        print(f"  Exit:    Trail {best['trail']}% / SL {best['sl']}% / TS {best['ts']}m / {pt_str}")
        print(f"  Results: {best['trades']} trades | {best['wr']:.1f}% WR | PF {best['pf']:.2f} | "
              f"${best['total']:,.0f} total | Sharpe {best['sharpe']:.2f}")
        print(f"  MaxDD: ${best['dd']:,.0f} | Avg hold: {best['avg_hold']:.1f}m | Per trade: ${best['per_trade']:,.0f}")
        print()

    if stage2_results and stage2_results[0]["total"] > 0:
        best_pnl = stage2_results[0]
        pt_str = f"PT={best_pnl['pt']}%" if best_pnl['pt'] else "no fixed PT"
        print(f"BEST CALL-BUYING EDGE (by Total P&L):")
        print(f"  Trigger: SPX drops >= {best_pnl['drop']:.2f}% from 10-min rolling high")
        print(f"  Action:  BUY ATM CALL at spike close")
        print(f"  Exit:    Trail {best_pnl['trail']}% / SL {best_pnl['sl']}% / TS {best_pnl['ts']}m / {pt_str}")
        print(f"  Results: {best_pnl['trades']} trades | {best_pnl['wr']:.1f}% WR | PF {best_pnl['pf']:.2f} | "
              f"${best_pnl['total']:,.0f} total | Sharpe {best_pnl['sharpe']:.2f}")
        print()

    if put_results and put_results[0]["total"] > 0:
        best_put = put_results[0]
        print(f"BEST PUT-BUYING EDGE (momentum continuation):")
        print(f"  Trigger: SPX drops >= {best_put['drop']:.2f}% from 10-min rolling high")
        print(f"  Action:  BUY ATM PUT at spike close")
        print(f"  Exit:    Trail {best_put['trail']}% / SL {best_put['sl']}% / TS {best_put['ts']}m")
        print(f"  Results: {best_put['trades']} trades | {best_put['wr']:.1f}% WR | "
              f"${best_put['total']:,.0f} total | Sharpe {best_put['sharpe']:.2f}")
        print()

    # Save all top edges as JSON for potential dashboard integration
    edge_export = []
    for i, edge in enumerate(top_edges[:5]):
        edge_export.append({
            "edge_num": i + 1,
            "trigger": f"SPX drops >= {edge['drop']:.2f}% from 10-min rolling high",
            "action": "BUY ATM CALL",
            "drop_threshold": edge["drop"],
            "trail_pct": edge["trail"],
            "sl_pct": edge["sl"],
            "ts_mins": edge["ts"],
            "pt_pct": edge["pt"],
            "trades": edge["trades"],
            "win_rate": round(edge["wr"], 1),
            "profit_factor": round(edge["pf"], 2),
            "total_pnl": round(edge["total"], 0),
            "sharpe": round(edge["sharpe"], 2),
            "max_dd": round(edge["dd"], 0),
            "avg_hold_mins": round(edge["avg_hold"], 1),
            "per_trade_pnl": round(edge["per_trade"], 0),
        })

    export_path = os.path.join(OUTPUT_DIR, "vol_spike_best_edges.json")
    with open(export_path, "w") as f:
        json.dump(edge_export, f, indent=2)
    print(f"  Best edges saved to: {export_path}")

    print()
    print("Script complete.")
    print("=" * 90)

    sys.stdout = original_stdout
    report_file.close()
    print(f"\nReport saved to: {OUTPUT_REPORT}")
    print(f"Edge data in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
