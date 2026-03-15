"""
SPX Opening Print — Script 39: Drop Velocity Analysis
======================================================
Tests whether the SPEED of intraday drops matters for mean reversion edge.

Key question: Does a 0.5% drop in 2 minutes have different edge than a 0.5% drop
in 10 minutes? Faster drops = more panic/stop-hunting → stronger snapback?

Approach:
  1. Enhanced spike detection that measures velocity
  2. Reuse ALL cached option data from script 37 (loads from disk on demand)
  3. Sweep parameter grid SPLIT by velocity bucket
  4. Compare edge across velocity buckets

Usage:
    python3 39_velocity_analysis.py
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
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "velocity_analysis_report.txt")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

BASE_RISK = 75000
SLIPPAGE_PER_CONTRACT = 0.50


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


def load_vix_daily():
    vix = {}
    with open(VIX_DAILY, "r") as f:
        for row in csv.DictReader(f):
            vix[row["date"]] = float(row["close"])
    return vix


# ══════════════════════════════════════════════════════════════════════════════
# CACHE HELPERS (read-only — all data cached by scripts 37/38)
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
    os.makedirs(CACHE_DIR, exist_ok=True)
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
                time.sleep(2 ** attempt)
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


def find_option_cached(date_str, target_strike, option_type="C"):
    """Look up option from cache only. Returns ticker or None."""
    strike = round(target_strike / 5) * 5
    cache_key = f"contracts_v3_{date_str}_{strike}_{option_type}"
    cached = load_cache(cache_key)
    if cached is not None and cached != "none":
        return cached["ticker"], cached["strike"]
    # Try API if not cached
    expiry_date = date_str  # simplification for 0DTE
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
                          "expiry": expiry_date, "dte": 0}
                save_cache(cache_key, result)
                return ticker, test_strike
    save_cache(cache_key, "none")
    return None, None


def load_opt_bars(ticker, date_str):
    """Load option bars from cache. Try 10s first, then 1min."""
    safe = ticker.replace(':', '_').replace('/', '_')

    # Try 10s bars
    cache_key = f"opt10s_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached and cached != "none" and len(cached) > 10:
        return cached

    # Try 1min bars
    cache_key = f"bars_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached and cached != "none" and len(cached) > 5:
        return cached

    # Fetch 10s from API if not cached
    offset_hours = get_dst_offset(date_str)
    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/10/second/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    time.sleep(REQUEST_DELAY)
    data = api_get(url)
    if not data or data.get("resultsCount", 0) == 0:
        save_cache(f"opt10s_{date_str}_{safe}", "none")
        return []
    all_bars = []
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
    save_cache(f"opt10s_{date_str}_{safe}", all_bars if all_bars else "none")
    return all_bars


# ══════════════════════════════════════════════════════════════════════════════
# VELOCITY-AWARE SPIKE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def time_to_seconds(t):
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def detect_spikes_with_velocity(bars, drop_pct, lookback_mins, min_time="09:35", max_time="15:00"):
    """
    Enhanced spike detection that measures DROP VELOCITY.
    velocity = drop_pct / minutes_elapsed (%/min)
    """
    spikes = []
    for i in range(lookback_mins, len(bars)):
        bar = bars[i]
        if bar["time"] < min_time or bar["time"] > max_time:
            continue

        window = bars[max(0, i - lookback_mins):i]
        rolling_high = 0
        peak_bar_idx = 0
        for j, wb in enumerate(window):
            if wb["high"] > rolling_high:
                rolling_high = wb["high"]
                peak_bar_idx = max(0, i - lookback_mins) + j

        current_drop = (rolling_high - bar["low"]) / rolling_high * 100

        if current_drop >= drop_pct:
            peak_bar = bars[peak_bar_idx]
            peak_secs = time_to_seconds(peak_bar["time"])
            trough_secs = time_to_seconds(bar["time"])
            elapsed_mins = max(1, (trough_secs - peak_secs) / 60)
            velocity = current_drop / elapsed_mins

            spikes.append({
                "bar_idx": i,
                "time": bar["time"],
                "drop_pct": current_drop,
                "rolling_high": rolling_high,
                "spike_low": bar["low"],
                "entry_price": bar["close"],
                "peak_time": peak_bar["time"],
                "elapsed_mins": round(elapsed_mins, 1),
                "velocity": round(velocity, 4),
                "bars_spanned": i - peak_bar_idx,
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
# TRADE SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

def simulate_trade(opt_bars, entry_time, trail_pct, sl_pct, ts_mins, pt_pct=None):
    if not opt_bars or len(opt_bars) < 5:
        return None

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

    sl_level = opt_entry * (1 - sl_pct / 100) if sl_pct else 0
    pt_level = opt_entry * (1 + pt_pct / 100) if pt_pct else None
    stop_secs = entry_secs + ts_mins * 60

    peak_price = opt_entry
    exit_bar = None
    exit_reason = None

    for i in range(entry_idx + 1, len(opt_bars)):
        bar = opt_bars[i]
        bar_secs = time_to_seconds(bar["time"])

        if bar_secs >= stop_secs:
            exit_bar = bar
            exit_reason = "TS"
            break
        if bar["high"] > peak_price:
            peak_price = bar["high"]
        if pt_level and bar["high"] >= pt_level:
            exit_bar = bar
            exit_reason = "PT"
            break
        if sl_level and bar["low"] <= sl_level:
            exit_bar = bar
            exit_reason = "SL"
            break
        if trail_pct and peak_price > opt_entry * 1.01:
            trail_level = peak_price * (1 - trail_pct / 100)
            if bar["low"] <= trail_level:
                exit_bar = bar
                exit_reason = "Trail"
                break

    if exit_bar is None:
        exit_bar = opt_bars[-1]
        exit_reason = "EOD"

    opt_exit = exit_bar["close"]
    hold_secs = time_to_seconds(exit_bar["time"]) - time_to_seconds(entry_bar["time"])
    hold_mins = max(0.17, hold_secs / 60)

    return {
        "opt_entry": round(opt_entry, 2),
        "pnl_per_contract": round((opt_exit - opt_entry) * 100, 2),
        "hold_mins": round(hold_mins, 1),
        "exit_reason": exit_reason,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STATS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def compute_stats(trades):
    if not trades or len(trades) < 5:
        return None
    pnls = [t["net_pnl"] for t in trades]
    total = sum(pnls)
    n = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / n * 100
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999

    cum = peak = dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)

    monthly = defaultdict(float)
    for t in trades:
        monthly[t["date"][:7]] += t["net_pnl"]
    mv = list(monthly.values())
    sharpe = mean(mv) / stdev(mv) * (12 ** 0.5) if len(mv) > 3 and stdev(mv) > 0 else 0

    avg_hold = mean([t["hold_mins"] for t in trades])

    return {
        "n": n, "total": total, "wr": wr, "pf": pf,
        "dd": dd, "sharpe": sharpe, "avg_hold": avg_hold,
        "per_trade": total / n,
    }


def print_row(label, stats):
    if not stats:
        print(f"  {label:<28} — insufficient data")
        return
    s = stats
    print(f"  {label:<28} N={s['n']:>5}  WR={s['wr']:>5.1f}%  PF={s['pf']:>5.2f}  "
          f"Total=${s['total']:>10,.0f}  $/tr=${s['per_trade']:>7,.0f}  "
          f"DD=${s['dd']:>8,.0f}  Sh={s['sharpe']:>5.2f}  Hold={s['avg_hold']:>4.1f}m")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    report_file = open(OUTPUT_REPORT, "w")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, report_file)

    print("=" * 100)
    print("DROP VELOCITY ANALYSIS — DOES SPEED OF MOVE AFFECT MEAN REVERSION EDGE?")
    print("=" * 100)
    print()

    # ── Load data ────────────────────────────────────────────────────────
    print("Loading data...")
    intraday = load_intraday()
    vix_daily = load_vix_daily()

    dates = sorted(intraday.keys())
    dates_0dte = [d for d in dates if d >= "2022-09-19"]
    print(f"  SPX 1-min days: {len(dates)}  |  0DTE-eligible: {len(dates_0dte)}")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: DETECT ALL SPIKES WITH VELOCITY, STORE LIGHTWEIGHT METADATA
    # (Option bars loaded on-demand from cache, NOT held in memory)
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("PHASE 1: DETECT SPIKES WITH VELOCITY METRICS")
    print("=" * 100)
    print()

    ALL_SPIKES = []  # lightweight — no option bars stored
    found = 0
    missed = 0

    for di, date in enumerate(dates_0dte):
        bars = intraday[date]
        if len(bars) < 30:
            continue

        vix = vix_daily.get(date, 20)
        spikes = detect_spikes_with_velocity(bars, drop_pct=0.20, lookback_mins=10,
                                              min_time="09:35", max_time="15:00")

        if not spikes:
            continue

        if (di + 1) % 100 == 0 or di == 0:
            vel_strs = ', '.join(f'{s["velocity"]:.3f}' for s in spikes[:3])
            print(f"  [{di+1}/{len(dates_0dte)}] {date}: {len(spikes)} spikes  (vel: {vel_strs})")

        for spike in spikes:
            entry_price = spike["entry_price"]
            ticker, strike = find_option_cached(date, entry_price, "C")

            if not ticker:
                missed += 1
                continue
            found += 1

            ALL_SPIKES.append({
                "date": date,
                "spike_time": spike["time"],
                "drop_pct": spike["drop_pct"],
                "entry_spx": entry_price,
                "vix": vix,
                "ticker": ticker,
                "strike": strike,
                # Velocity fields
                "velocity": spike["velocity"],
                "elapsed_mins": spike["elapsed_mins"],
                "bars_spanned": spike["bars_spanned"],
            })

    print()
    print(f"  Total tradeable spikes: {len(ALL_SPIKES)}")
    print(f"  Options found: {found}  |  missed: {missed}")
    print()

    # Free intraday data to save memory
    del intraday

    # ── Velocity distribution ─────────────────────────────────────────────
    velocities = [s["velocity"] for s in ALL_SPIKES]
    elapsed_all = [s["elapsed_mins"] for s in ALL_SPIKES]

    print("── Velocity Distribution ──")
    print(f"  Mean velocity:   {mean(velocities):.4f} %/min")
    print(f"  Median velocity: {median(velocities):.4f} %/min")
    print(f"  P25: {sorted(velocities)[len(velocities)//4]:.4f}  P75: {sorted(velocities)[3*len(velocities)//4]:.4f}")
    print(f"  Min:  {min(velocities):.4f}  Max: {max(velocities):.4f}")
    print(f"  Mean elapsed:    {mean(elapsed_all):.1f} mins  Median: {median(elapsed_all):.1f} mins")
    print()

    vel_bins = [0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50, 99]
    print("  Velocity histogram:")
    for i in range(len(vel_bins) - 1):
        lo, hi = vel_bins[i], vel_bins[i + 1]
        count = sum(1 for v in velocities if lo <= v < hi)
        label = f"{lo:.2f}-{hi:.2f}" if hi < 99 else f"{lo:.2f}+    "
        bar = "#" * min(80, count // 10)
        print(f"    {label} %/min: {count:>5}  {bar}")
    print()

    time_bins = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    print("  Elapsed time histogram (minutes from peak to trough):")
    for t in time_bins:
        count = sum(1 for e in elapsed_all if e == t)
        bar = "#" * min(80, count // 10)
        print(f"    {t:>2} min: {count:>5}  {bar}")
    print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: SIMULATE TRADES — VELOCITY BUCKET COMPARISON
    # Load option bars on-demand, process one spike at a time
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("PHASE 2: VELOCITY BUCKET ANALYSIS — BUY ATM CALL ON DIP")
    print("=" * 100)
    print()

    velocity_buckets = [
        ("ULTRA-FAST (>0.25)",  0.25, 99),
        ("FAST (0.10-0.25)",    0.10, 0.25),
        ("MEDIUM (0.05-0.10)",  0.05, 0.10),
        ("SLOW (<0.05)",        0.00, 0.05),
    ]

    # Define param combos to test
    param_combos = [
        # (trail, sl, ts, pt, label)
        (10, 30, 10, None,  "tr10 sl30 ts10"),
        (10, 30, 30, None,  "tr10 sl30 ts30"),
        (10, 30, 60, None,  "tr10 sl30 ts60"),
        (10, 30, 5, None,   "tr10 sl30 ts5"),
        (10, 30, 15, None,  "tr10 sl30 ts15"),
        (10, 30, 30, 50,    "tr10 sl30 ts30 pt50"),
        (10, 30, 30, 100,   "tr10 sl30 ts30 pt100"),
        (15, 40, 30, None,  "tr15 sl40 ts30"),
        (5, 20, 10, None,   "tr5 sl20 ts10"),
        (20, 50, 60, None,  "tr20 sl50 ts60"),
        (10, 30, 90, None,  "tr10 sl30 ts90"),
        (5, 20, 5, None,    "tr5 sl20 ts5"),
        (10, 30, 10, 50,    "tr10 sl30 ts10 pt50"),
        (10, 30, 60, 100,   "tr10 sl30 ts60 pt100"),
    ]

    drop_thresholds = [0.20, 0.30, 0.40, 0.50]

    # For each spike, simulate all param combos and store results with velocity tag
    # Process spikes in batches by date to minimize cache reads
    print("Simulating trades across all param combos...")
    print()

    # Group spikes by (date, ticker) for efficient bar loading
    spike_groups = defaultdict(list)
    for si, spike in enumerate(ALL_SPIKES):
        key = (spike["date"], spike["ticker"])
        spike_groups[key].append((si, spike))

    # Results: for each (drop_thresh, param_combo_idx), store list of trade dicts
    # Each trade dict has: date, net_pnl, hold_mins, velocity, elapsed_mins, vix
    all_trade_results = []  # list of dicts, one per spike×param_combo

    group_count = 0
    total_groups = len(spike_groups)

    for (date, ticker), group_spikes in spike_groups.items():
        group_count += 1
        if group_count % 200 == 0:
            print(f"  Processing group {group_count}/{total_groups} ({date})  "
                  f"trades so far: {len(all_trade_results)}")

        # Load option bars ONCE for this date+ticker
        opt_bars = load_opt_bars(ticker, date)
        if not opt_bars or len(opt_bars) < 5:
            continue

        for si, spike in group_spikes:
            entry_t = spike["spike_time"] + ":00" if len(spike["spike_time"]) <= 5 else spike["spike_time"]

            for pi, (trail, sl, ts, pt, plabel) in enumerate(param_combos):
                result = simulate_trade(opt_bars, entry_t, trail, sl, ts, pt)
                if not result or result["opt_entry"] <= 0.10:
                    continue

                risk = BASE_RISK * (1.3 if spike["vix"] and spike["vix"] >= 25 else 1.0)
                contracts = max(1, int(risk / (result["opt_entry"] * 100)))
                pnl = result["pnl_per_contract"] * contracts
                slip = SLIPPAGE_PER_CONTRACT * 100 * contracts

                all_trade_results.append({
                    "date": spike["date"],
                    "net_pnl": round(pnl - slip, 2),
                    "hold_mins": result["hold_mins"],
                    "velocity": spike["velocity"],
                    "elapsed_mins": spike["elapsed_mins"],
                    "vix": spike["vix"],
                    "drop_pct": spike["drop_pct"],
                    "param_idx": pi,
                    "exit_reason": result["exit_reason"],
                })

    print(f"  Total trade results: {len(all_trade_results)}")
    print()

    # ── 2A: For each drop threshold × param combo, compare velocity buckets ──
    print("─── 2A: Edge by Velocity Bucket ───")
    print()

    for drop_thresh in drop_thresholds:
        print(f"  ══ Drop >= {drop_thresh:.2f}% ══")

        for pi, (trail, sl, ts, pt, plabel) in enumerate(param_combos):
            trades = [t for t in all_trade_results
                      if t["drop_pct"] >= drop_thresh and t["param_idx"] == pi]

            if len(trades) < 20:
                continue

            stats_all = compute_stats(trades)
            if not stats_all or stats_all["pf"] < 0.5:
                continue

            print(f"    ── {plabel} ──")
            print_row("ALL VELOCITIES", stats_all)

            for bname, vel_lo, vel_hi in velocity_buckets:
                bucket = [t for t in trades if vel_lo <= t["velocity"] < vel_hi]
                stats = compute_stats(bucket)
                print_row(bname, stats)
            print()

    # ── 2B: Summary — which velocity bucket has best edge? ──
    print("─── 2B: SUMMARY — Best Edge by Velocity Bucket ───")
    print()
    print("For each velocity bucket, find the param combo with highest Sharpe:")
    print()

    for bname, vel_lo, vel_hi in [("ALL", 0, 99)] + velocity_buckets:
        best_sharpe = -999
        best_combo = None
        best_stats = None

        for drop_thresh in drop_thresholds:
            for pi, (trail, sl, ts, pt, plabel) in enumerate(param_combos):
                trades = [t for t in all_trade_results
                          if t["drop_pct"] >= drop_thresh and t["param_idx"] == pi
                          and vel_lo <= t["velocity"] < vel_hi]
                stats = compute_stats(trades)
                if stats and stats["sharpe"] > best_sharpe and stats["n"] >= 20:
                    best_sharpe = stats["sharpe"]
                    best_combo = f"d>={drop_thresh} {plabel}"
                    best_stats = stats

        if best_stats:
            print(f"  {bname}")
            print(f"    Best: {best_combo}")
            print_row("", best_stats)
            print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: ELAPSED TIME ANALYSIS
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("PHASE 3: ELAPSED TIME BUCKETS — RAW MINUTES FROM PEAK TO TROUGH")
    print("=" * 100)
    print()

    elapsed_buckets = [
        ("1-2 min (flash)",   1, 3),
        ("3-4 min (fast)",    3, 5),
        ("5-6 min (moderate)", 5, 7),
        ("7-8 min (gradual)", 7, 9),
        ("9-10 min (slow)",   9, 11),
    ]

    for drop_thresh in [0.20, 0.30, 0.50]:
        print(f"  ── Drop >= {drop_thresh:.2f}% ──")
        # Use the "tr10 sl30 ts30" combo (index 1) as representative
        trades = [t for t in all_trade_results
                  if t["drop_pct"] >= drop_thresh and t["param_idx"] == 1]

        if len(trades) < 20:
            print("     Too few trades")
            print()
            continue

        stats_all = compute_stats(trades)
        print_row("ALL ELAPSED", stats_all)

        for ename, lo, hi in elapsed_buckets:
            bucket = [t for t in trades if lo <= t["elapsed_mins"] < hi]
            stats = compute_stats(bucket)
            print_row(ename, stats)
        print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: VELOCITY × VIX INTERACTION
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("PHASE 4: VELOCITY × VIX INTERACTION")
    print("=" * 100)
    print()
    print("Does velocity matter MORE in high-VIX vs low-VIX?")
    print("Fixed: drop>=0.25%, tr10 sl30 ts30")
    print()

    trades_base = [t for t in all_trade_results
                   if t["drop_pct"] >= 0.25 and t["param_idx"] == 1]

    vix_regimes = [
        ("Low VIX (<18)",      0, 18),
        ("Normal VIX (18-25)", 18, 25),
        ("High VIX (25+)",     25, 999),
    ]

    for vname, vlo, vhi in vix_regimes:
        print(f"  ── {vname} ──")
        vix_trades = [t for t in trades_base if vlo <= t["vix"] < vhi]
        stats_all = compute_stats(vix_trades)
        print_row("ALL VELOCITIES", stats_all)

        for bname, vel_lo, vel_hi in velocity_buckets:
            bucket = [t for t in vix_trades if vel_lo <= t["velocity"] < vel_hi]
            stats = compute_stats(bucket)
            print_row(bname, stats)
        print()

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5: RAW SPX MEAN REVERSION BY VELOCITY (no options needed)
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("PHASE 5: RAW SPX MEAN REVERSION BY VELOCITY (underlying, no options)")
    print("=" * 100)
    print()
    print("Simpler test: after a drop, how much does SPX recover in N minutes?")
    print("Uses the spike metadata already collected.")
    print()

    # We already have ALL_SPIKES with velocity. Need intraday bars again.
    # Reload just what we need — future bars after each spike
    print("Reloading intraday for forward-looking analysis...")
    intraday = load_intraday()

    for drop_thresh in [0.20, 0.30, 0.50]:
        print(f"  ── Drop >= {drop_thresh:.2f}% ──")
        eligible = [s for s in ALL_SPIKES if s["drop_pct"] >= drop_thresh]
        if len(eligible) < 20:
            print("     Too few")
            continue

        # For each spike, measure SPX change over next N minutes
        recovery_data = []
        for spike in eligible:
            date = spike["date"]
            bars = intraday.get(date, [])
            if not bars:
                continue

            # Find spike bar
            spike_time = spike["spike_time"]
            spike_secs = time_to_seconds(spike_time)
            entry_px = spike["entry_spx"]

            for horizon in [5, 10, 30, 60]:
                target_secs = spike_secs + horizon * 60
                # Find bar closest to target
                best_bar = None
                best_diff = float("inf")
                for bar in bars:
                    diff = abs(time_to_seconds(bar["time"]) - target_secs)
                    if diff < best_diff:
                        best_diff = diff
                        best_bar = bar
                if best_bar and best_diff < 120:  # within 2 min
                    chg = (best_bar["close"] - entry_px) / entry_px * 100
                    recovery_data.append({
                        "velocity": spike["velocity"],
                        "elapsed_mins": spike["elapsed_mins"],
                        "horizon": horizon,
                        "chg_pct": chg,
                    })

        for horizon in [5, 10, 30]:
            hz = [r for r in recovery_data if r["horizon"] == horizon]
            if len(hz) < 10:
                continue

            chgs = [r["chg_pct"] for r in hz]
            bounce_pct = sum(1 for c in chgs if c > 0) / len(chgs) * 100
            avg_chg = mean(chgs)
            print(f"    {horizon}m fwd — ALL: N={len(chgs):>5}  Bounce%={bounce_pct:>5.1f}  AvgChg={avg_chg:>+.4f}%")

            for bname, vel_lo, vel_hi in velocity_buckets:
                bucket = [r for r in hz if vel_lo <= r["velocity"] < vel_hi]
                if len(bucket) < 5:
                    print(f"      {bname:<28} N={len(bucket):>5}  (too few)")
                    continue
                chgs_b = [r["chg_pct"] for r in bucket]
                bpct = sum(1 for c in chgs_b if c > 0) / len(chgs_b) * 100
                avg_b = mean(chgs_b)
                print(f"      {bname:<28} N={len(bucket):>5}  Bounce%={bpct:>5.1f}  AvgChg={avg_b:>+.4f}%")
            print()

    # ══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print()
    print("Compare the metrics above across velocity buckets to determine:")
    print("  1. Do FAST drops (high velocity) produce better mean reversion edge?")
    print("  2. Is the effect consistent across VIX regimes?")
    print("  3. Does raw SPX bounce rate differ by velocity?")
    print("  4. Should velocity be a FILTER in the live trading strategy?")
    print()

    sys.stdout = original_stdout
    report_file.close()
    print(f"\nReport saved to: {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
