"""
SPX Opening Print — Script 34: Edge Optimization with Slippage
================================================================
Re-simulates all 6 edges using cached 10s data (no new API calls).
Applies realistic bid-ask slippage ($0.50 round-trip per contract).
Optimizes weak edges (A, C, D, E) by sweeping parameters.
Then re-runs the final backtest with optimal params + slippage.

Usage:
    python3 34_spx_edges_optimize.py
"""

import os, csv, json, math, sys
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
VIX_1MIN = os.path.join(SCRIPT_DIR, "vix_1min_bars.csv")
SPX_TRADES_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")
SPX_CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "spx_edges_optimize_report.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "spx_edges_output")

SLIPPAGE = 0.50  # round-trip slippage per contract in dollars

MIN_RISK = 50000
MAX_RISK = 200000
BASE_RISK = 75000


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


# ── Data Loading ──────────────────────────────────────────────────────

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


def load_daily(filepath):
    data = {}
    dates = []
    with open(filepath) as f:
        for row in csv.DictReader(f):
            d = row["date"]
            data[d] = {k: float(row[k]) for k in ["open", "high", "low", "close"]}
            dates.append(d)
    return data, sorted(set(dates))


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
                "open": float(row["open"]),
                "close": float(row["close"]),
            })
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return dict(days)


# ── Cache helpers ──────────────────────────────────────────────────────

def cache_path(key):
    return os.path.join(SPX_CACHE_DIR, key + ".json")


def load_cache(key):
    p = cache_path(key)
    if os.path.exists(p):
        with open(p, "r") as f:
            return json.load(f)
    return None


# ── Feature building ──────────────────────────────────────────────────

def build_features(d, bars, spx_daily, spx_dates, vix_daily, vix_1min):
    if len(bars) < 5:
        return None
    entry_open = bars[0]["open"]
    fb = bars[0]
    fb_ret = (fb["close"] - fb["open"]) / fb["open"] * 100
    fb_bullish = fb["close"] > fb["open"]
    dt = datetime.strptime(d, "%Y-%m-%d")
    dow = dt.strftime("%A")
    vix = None
    if d in vix_daily:
        vix = vix_daily[d]["open"]
    elif vix_1min and d in vix_1min and vix_1min[d]:
        vix = vix_1min[d][0]["open"]
    return {
        "date": d, "entry_open": entry_open, "fb_ret": fb_ret,
        "fb_bullish": fb_bullish, "dow": dow, "vix": vix, "bars": bars,
    }


# ── Option Lookup (cache only) ────────────────────────────────────────

def find_option_cached(date_str, target_strike, option_type="C"):
    """Look up option from cache only — no API calls."""
    strike = round(target_strike / 5) * 5
    cache_key = f"contracts_v3_{date_str}_{strike}_{option_type}"
    cached = load_cache(cache_key)
    if cached is not None and cached != "none":
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]
    return None, None, None, None


def get_entry_price_cached(ticker, date_str):
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"entry10s_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None and cached != "none":
        return cached
    return None


def get_10s_bars_cached(ticker, date_str, cache_prefix):
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"{cache_prefix}_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None and cached != "none":
        return cached
    return []


# ── Simulation Functions (with slippage) ──────────────────────────────

def simulate_trailing(spx_10s, opt_10s, entry_open, opt_entry_price,
                      sl_pts, trail_pct, ts_minutes, risk, direction=1,
                      slippage=SLIPPAGE):
    if not spx_10s or not opt_10s or len(spx_10s) < 2:
        return None

    opt_map = {}
    for bar in opt_10s:
        opt_map[bar["time"]] = bar

    contract_cost = opt_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    peak_price = entry_open
    trough_price = entry_open

    entry_min = 9 * 60 + 31
    stop_min = entry_min + ts_minutes
    ts_limit = f"{stop_min // 60:02d}:{stop_min % 60:02d}:00"

    exit_time = None
    exit_reason = None
    started = False

    for bar in spx_10s:
        if bar["time"] <= "09:31:00":
            if bar["time"] >= "09:31:00":
                started = True
            continue
        if not started:
            started = True

        if bar["time"] >= ts_limit:
            exit_time = bar["time"]
            exit_reason = "Time Stop"
            break

        if direction == 1:
            if bar["high"] > peak_price:
                peak_price = bar["high"]
            if bar["low"] <= entry_open - sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
            trail_level = peak_price * (1 - trail_pct / 100)
            if bar["low"] <= trail_level and peak_price > entry_open:
                exit_time = bar["time"]
                exit_reason = "Trailing Stop"
                break
        else:
            if bar["low"] < trough_price:
                trough_price = bar["low"]
            if bar["high"] >= entry_open + sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
            trail_level = trough_price * (1 + trail_pct / 100)
            if bar["high"] >= trail_level and trough_price < entry_open:
                exit_time = bar["time"]
                exit_reason = "Trailing Stop"
                break

    if exit_time is None:
        if spx_10s:
            exit_time = spx_10s[-1]["time"]
            exit_reason = "Time Stop"
        else:
            return None

    opt_exit_price = None
    if exit_time in opt_map:
        opt_exit_price = opt_map[exit_time]["close"]
    else:
        for bar in opt_10s:
            if bar["time"] >= exit_time:
                opt_exit_price = bar["close"]
                break
        if opt_exit_price is None:
            for bar in reversed(opt_10s):
                if bar["time"] <= exit_time:
                    opt_exit_price = bar["close"]
                    break
        if opt_exit_price is None:
            opt_exit_price = opt_10s[-1]["close"]

    # Apply slippage
    slippage_cost = slippage * 100 * num_contracts
    pnl_dollars = (opt_exit_price - opt_entry_price) * 100 * num_contracts - slippage_cost

    entry_secs = 9 * 3600 + 31 * 60
    ep = exit_time.split(":")
    exit_secs = int(ep[0]) * 3600 + int(ep[1]) * 60 + int(ep[2])
    hold_mins = max(1, round((exit_secs - entry_secs) / 60))

    return {
        "opt_entry_price": opt_entry_price,
        "opt_exit_price": opt_exit_price,
        "num_contracts": num_contracts,
        "total_premium": total_premium,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_raw": round((opt_exit_price - opt_entry_price) * 100 * num_contracts, 2),
        "slippage_cost": round(slippage_cost, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time[:5],
        "hold_mins": hold_mins,
    }


def simulate_fixed(spx_10s, opt_10s, entry_open, opt_entry_price,
                   pt_pts, sl_pts, ts_minutes, risk, direction=1,
                   slippage=SLIPPAGE):
    if not spx_10s or not opt_10s or len(spx_10s) < 2:
        return None

    opt_map = {}
    for bar in opt_10s:
        opt_map[bar["time"]] = bar

    contract_cost = opt_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    entry_min = 9 * 60 + 31
    stop_min = entry_min + ts_minutes
    ts_limit = f"{stop_min // 60:02d}:{stop_min % 60:02d}:00"

    exit_time = None
    exit_reason = None
    started = False

    for bar in spx_10s:
        if bar["time"] <= "09:31:00":
            if bar["time"] >= "09:31:00":
                started = True
            continue
        if not started:
            started = True

        if bar["time"] >= ts_limit:
            exit_time = bar["time"]
            exit_reason = "Time Stop"
            break

        if direction == 1:
            if bar["high"] >= entry_open + pt_pts:
                exit_time = bar["time"]
                exit_reason = "Profit Target"
                break
            if bar["low"] <= entry_open - sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
        else:
            if bar["low"] <= entry_open - pt_pts:
                exit_time = bar["time"]
                exit_reason = "Profit Target"
                break
            if bar["high"] >= entry_open + sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break

    if exit_time is None:
        if spx_10s:
            exit_time = spx_10s[-1]["time"]
            exit_reason = "Time Stop"
        else:
            return None

    opt_exit_price = None
    if exit_time in opt_map:
        opt_exit_price = opt_map[exit_time]["close"]
    else:
        for bar in opt_10s:
            if bar["time"] >= exit_time:
                opt_exit_price = bar["close"]
                break
        if opt_exit_price is None:
            for bar in reversed(opt_10s):
                if bar["time"] <= exit_time:
                    opt_exit_price = bar["close"]
                    break
        if opt_exit_price is None:
            opt_exit_price = opt_10s[-1]["close"]

    slippage_cost = slippage * 100 * num_contracts
    pnl_dollars = (opt_exit_price - opt_entry_price) * 100 * num_contracts - slippage_cost

    entry_secs = 9 * 3600 + 31 * 60
    ep = exit_time.split(":")
    exit_secs = int(ep[0]) * 3600 + int(ep[1]) * 60 + int(ep[2])
    hold_mins = max(1, round((exit_secs - entry_secs) / 60))

    return {
        "opt_entry_price": opt_entry_price,
        "opt_exit_price": opt_exit_price,
        "num_contracts": num_contracts,
        "total_premium": total_premium,
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_raw": round((opt_exit_price - opt_entry_price) * 100 * num_contracts, 2),
        "slippage_cost": round(slippage_cost, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time[:5],
        "hold_mins": hold_mins,
    }


# ── Stats ──────────────────────────────────────────────────────────────

def compute_stats(pnls, trades=None):
    if not pnls:
        return None
    total = sum(pnls)
    n = len(pnls)
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
    if trades and len(trades) > 1:
        dates_span = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
                      datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
        tpy = n / (dates_span / 365.25) if dates_span > 0 else n
    else:
        tpy = 252
    sh = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if n > 1 and stdev(pnls) > 0 else 0
    calmar = total / dd if dd > 0 else 999
    return {
        "n": n, "total": total, "wr": wr, "pf": pf, "maxdd": dd,
        "sharpe": sh, "calmar": calmar, "wins": wins,
        "avg_pnl": mean(pnls),
    }


def print_stats(stats, label):
    if not stats:
        print(f"  {label}: NO TRADES")
        return
    print(f"\n  {label}")
    print(f"    Trades:       {stats['n']}")
    print(f"    Total P&L:    ${stats['total']:,.0f}")
    print(f"    Win Rate:     {stats['wr']:.1f}%  ({stats['wins']}W / {stats['n']-stats['wins']}L)")
    print(f"    Profit Factor:{stats['pf']:.2f}")
    print(f"    Max Drawdown: ${stats['maxdd']:,.0f}")
    print(f"    Sharpe:       {stats['sharpe']:.2f}")
    print(f"    Calmar:       {stats['calmar']:.2f}")


# ── Main: load data once, preload all cached bars ─────────────────────

def main():
    log_f = open(OUTPUT_REPORT, "w")
    tee = Tee(sys.stdout, log_f)
    old_stdout = sys.stdout
    sys.stdout = tee

    print("=" * 90)
    print("SPX Opening Print — Edge Optimization with Slippage")
    print(f"  Slippage: ${SLIPPAGE:.2f} round-trip per contract")
    print("=" * 90)

    # Load data
    print("\nLoading data...")
    intraday = load_intraday()
    spx_daily, spx_dates = load_daily(SPX_DAILY)
    vix_daily, _ = load_daily(VIX_DAILY)
    vix_1min = load_vix_1min()
    print(f"  SPX intraday: {len(intraday)} days")

    with open(SPX_TRADES_JSON, "r") as f:
        existing_trades = json.load(f)
    current_dates = set(t["date"] for t in existing_trades)
    print(f"  Current strategy: {len(current_dates)} dates")

    # Build features
    print("\nBuilding features...")
    days = {}
    for d in sorted(intraday.keys()):
        feat = build_features(d, intraday[d], spx_daily, spx_dates, vix_daily, vix_1min)
        if feat:
            days[d] = feat
    print(f"  {len(days)} tradeable days")

    # Define day filters
    bullish_days = {d: f for d, f in days.items() if f["fb_bullish"]}
    filtered_days = {d: f for d, f in days.items() if f["fb_bullish"] and f["fb_ret"] > 0.05}
    bearish_days = {d: f for d, f in days.items() if not f["fb_bullish"]}
    missed_days = {d: f for d, f in days.items() if f["fb_bullish"] and d not in current_dates}
    current_day_feats = {d: f for d, f in days.items() if d in current_dates}

    print(f"  Bullish days: {len(bullish_days)}")
    print(f"  Filtered (fb_ret>0.05%): {len(filtered_days)}")
    print(f"  Bearish days: {len(bearish_days)}")
    print(f"  Missed days: {len(missed_days)}")
    print(f"  Current strategy days: {len(current_day_feats)}")

    # ── Preload all cached data per day ────────────────────────────────
    # For each tradeable day, load option ticker + 10s bars from cache
    print("\nPreloading cached data...")

    day_data = {}  # date -> {ticker_C, ticker_P, entry_C, entry_P, spy_10s, opt_C_10s, opt_P_10s}
    loaded = 0
    skipped_no_opt = 0

    for d, feat in sorted(days.items()):
        entry_open = feat["entry_open"]
        rec = {"entry_open": entry_open, "feat": feat}

        # Look up call and put options from cache
        for opt_type in ["C", "P"]:
            ticker, strike, expiry, dte = find_option_cached(d, entry_open, opt_type)
            if not ticker:
                rec[f"ticker_{opt_type}"] = None
                continue
            rec[f"ticker_{opt_type}"] = ticker
            rec[f"strike_{opt_type}"] = strike

            # Entry price
            rec[f"entry_{opt_type}"] = get_entry_price_cached(ticker, d)

            # 10s option bars
            rec[f"opt10s_{opt_type}"] = get_10s_bars_cached(ticker, d, "opt10s")

        # SPY 10s bars (used for underlying)
        spy_10s_raw = get_10s_bars_cached("SPY", d, "spy10s")
        if spy_10s_raw:
            # Scale to SPX
            spy_ref = None
            for bar in spy_10s_raw:
                if bar["time"] >= "09:31:00":
                    spy_ref = bar["open"]
                    break
            if spy_ref and spy_ref > 0:
                scale = entry_open / spy_ref
                rec["spx_10s"] = [{
                    "time": b["time"],
                    "open": b["open"] * scale,
                    "high": b["high"] * scale,
                    "low": b["low"] * scale,
                    "close": b["close"] * scale,
                } for b in spy_10s_raw]
            else:
                rec["spx_10s"] = []
        else:
            rec["spx_10s"] = []

        day_data[d] = rec
        loaded += 1

    print(f"  Loaded {loaded} days of cached data")

    # ── Helper to run a parameter set on a day set ────────────────────

    def run_edge(eligible_dates, option_type, exit_mode, params, direction=1):
        """Run a parameter set across eligible dates. Returns list of trade dicts."""
        trades = []
        for d in sorted(eligible_dates):
            if d not in day_data:
                continue
            rec = day_data[d]
            feat = rec["feat"]

            ticker = rec.get(f"ticker_{option_type}")
            if not ticker:
                continue

            spx_10s = rec.get("spx_10s", [])
            opt_10s = rec.get(f"opt10s_{option_type}", [])
            if not spx_10s or not opt_10s:
                continue

            # Entry price
            entry_price = rec.get(f"entry_{option_type}")
            if not entry_price or entry_price <= 0.10:
                for bar in opt_10s:
                    if bar["time"] >= "09:30:10":
                        entry_price = bar["close"]
                        break
            if not entry_price or entry_price <= 0.10:
                continue

            # Risk sizing
            vix = feat["vix"]
            risk = BASE_RISK
            if vix and vix >= 25:
                risk = int(risk * 1.3)
            if abs(feat["fb_ret"]) > 0.20:
                risk = int(risk * 1.2)
            risk = max(MIN_RISK, min(MAX_RISK, risk))

            entry_open = rec["entry_open"]

            if exit_mode == "trail":
                result = simulate_trailing(
                    spx_10s, opt_10s, entry_open, entry_price,
                    params["sl_pts"], params["trail_pct"], params["ts_min"],
                    risk, direction)
            else:
                result = simulate_fixed(
                    spx_10s, opt_10s, entry_open, entry_price,
                    params["pt_pts"], params["sl_pts"], params["ts_min"],
                    risk, direction)

            if result:
                trades.append({
                    "date": d,
                    "day_of_week": feat["dow"],
                    "entry_open": entry_open,
                    "fb_ret": round(feat["fb_ret"], 4),
                    "vix": vix,
                    "strike": rec.get(f"strike_{option_type}"),
                    "option_ticker": ticker,
                    "option_type": option_type,
                    "risk": risk,
                    **result,
                })
        return trades

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: Baseline with slippage (current params)
    # ══════════════════════════════════════════════════════════════════════

    print(f"\n\n{'='*90}")
    print("PHASE 1: BASELINE WITH ${:.2f} SLIPPAGE".format(SLIPPAGE))
    print(f"{'='*90}")

    baseline_configs = {
        "A": {"dates": bullish_days, "opt": "C", "mode": "trail", "dir": 1,
              "params": {"trail_pct": 0.02, "sl_pts": 15, "ts_min": 30}},
        "B": {"dates": filtered_days, "opt": "C", "mode": "trail", "dir": 1,
              "params": {"trail_pct": 0.05, "sl_pts": 15, "ts_min": 30}},
        "C": {"dates": bearish_days, "opt": "P", "mode": "fixed", "dir": -1,
              "params": {"pt_pts": 8, "sl_pts": 5, "ts_min": 30}},
        "D": {"dates": missed_days, "opt": "C", "mode": "trail", "dir": 1,
              "params": {"trail_pct": 0.05, "sl_pts": 10, "ts_min": 120}},
        "E": {"dates": bullish_days, "opt": "C", "mode": "fixed", "dir": 1,
              "params": {"pt_pts": 1, "sl_pts": 1, "ts_min": 5}},
        "F": {"dates": current_day_feats, "opt": "C", "mode": "trail", "dir": 1,
              "params": {"trail_pct": 0.05, "sl_pts": 15, "ts_min": 30}},
    }

    baseline_results = {}
    for edge_id, cfg in baseline_configs.items():
        trades = run_edge(cfg["dates"], cfg["opt"], cfg["mode"], cfg["params"], cfg["dir"])
        pnls = [t["pnl_dollars"] for t in trades]
        stats = compute_stats(pnls, trades)
        baseline_results[edge_id] = {"trades": trades, "stats": stats}
        name = {"A": "SPX Scalp", "B": "Filtered Scalp", "C": "Bearish Short",
                "D": "Missed Days", "E": "Ultra Scalp", "F": "Current+Trail"}[edge_id]
        print_stats(stats, f"EDGE {edge_id}: {name} (baseline)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: OPTIMIZE WEAK EDGES
    # ══════════════════════════════════════════════════════════════════════

    print(f"\n\n{'='*90}")
    print("PHASE 2: PARAMETER OPTIMIZATION (weak edges)")
    print(f"{'='*90}")

    # ── Edge A: currently trail=0.02%, SL=15, TS=30
    # Problem: trail too tight → tiny option moves eaten by spread
    # Try wider trails, different SL, different TS
    print(f"\n{'─'*70}")
    print("OPTIMIZING EDGE A (SPX Scalp — all bullish days)")
    print(f"{'─'*70}")

    best_a = {"sharpe": -999}
    a_results = []
    for trail in [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
        for sl in [5, 10, 15, 20, 25]:
            for ts in [15, 30, 45, 60]:
                params = {"trail_pct": trail, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(bullish_days, "C", "trail", params, 1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    a_results.append((trail, sl, ts, stats))
                    if stats["sharpe"] > best_a["sharpe"]:
                        best_a = {**stats, "trail": trail, "sl": sl, "ts": ts}

    # Sort by Sharpe and show top 10
    a_results.sort(key=lambda x: x[3]["sharpe"], reverse=True)
    print(f"\n  Top 10 configs (by Sharpe):")
    print(f"  {'Trail%':>7} {'SL':>4} {'TS':>4} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for trail, sl, ts, s in a_results[:10]:
        print(f"  {trail:7.2f} {sl:4d} {ts:4d} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    # Also show top by total P&L
    a_by_pnl = sorted(a_results, key=lambda x: x[3]["total"], reverse=True)
    print(f"\n  Top 10 configs (by Total P&L):")
    print(f"  {'Trail%':>7} {'SL':>4} {'TS':>4} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for trail, sl, ts, s in a_by_pnl[:10]:
        print(f"  {trail:7.2f} {sl:4d} {ts:4d} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    # ── Edge C: Bearish Short — currently PT=8, SL=5, TS=30
    # Problem: put spreads wider, low WR, huge drawdown
    print(f"\n{'─'*70}")
    print("OPTIMIZING EDGE C (Bearish Short — bearish days, buy puts)")
    print(f"{'─'*70}")

    best_c = {"sharpe": -999}
    c_results = []
    # Try fixed PT/SL
    for pt in [3, 5, 8, 10, 15, 20]:
        for sl in [3, 5, 8, 10, 15]:
            for ts in [10, 15, 30, 45, 60]:
                params = {"pt_pts": pt, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(bearish_days, "P", "fixed", params, -1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    c_results.append(("fixed", pt, sl, ts, 0, stats))
                    if stats["sharpe"] > best_c["sharpe"]:
                        best_c = {**stats, "mode": "fixed", "pt": pt, "sl": sl, "ts": ts}

    # Also try trailing stop on bearish
    for trail in [0.03, 0.05, 0.08, 0.10, 0.15]:
        for sl in [3, 5, 8, 10, 15]:
            for ts in [10, 15, 30, 45, 60]:
                params = {"trail_pct": trail, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(bearish_days, "P", "trail", params, -1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    c_results.append(("trail", 0, sl, ts, trail, stats))
                    if stats["sharpe"] > best_c["sharpe"]:
                        best_c = {**stats, "mode": "trail", "trail": trail, "sl": sl, "ts": ts}

    c_results.sort(key=lambda x: x[5]["sharpe"], reverse=True)
    print(f"\n  Top 10 configs (by Sharpe):")
    print(f"  {'Mode':>6} {'PT':>4} {'SL':>4} {'TS':>4} {'Trail%':>7} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for mode, pt, sl, ts, trail, s in c_results[:10]:
        print(f"  {mode:>6} {pt:4d} {sl:4d} {ts:4d} {trail:7.2f} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    c_by_pnl = sorted(c_results, key=lambda x: x[5]["total"], reverse=True)
    print(f"\n  Top 10 configs (by Total P&L):")
    print(f"  {'Mode':>6} {'PT':>4} {'SL':>4} {'TS':>4} {'Trail%':>7} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for mode, pt, sl, ts, trail, s in c_by_pnl[:10]:
        print(f"  {mode:>6} {pt:4d} {sl:4d} {ts:4d} {trail:7.2f} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    # ── Edge D: Missed Days — currently trail=0.05%, SL=10, TS=120
    print(f"\n{'─'*70}")
    print("OPTIMIZING EDGE D (Missed Days — bullish non-traded days)")
    print(f"{'─'*70}")

    best_d = {"sharpe": -999}
    d_results = []
    for trail in [0.05, 0.08, 0.10, 0.15, 0.20, 0.30]:
        for sl in [5, 10, 15, 20, 25]:
            for ts in [30, 45, 60, 90, 120]:
                params = {"trail_pct": trail, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(missed_days, "C", "trail", params, 1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    d_results.append((trail, sl, ts, stats))
                    if stats["sharpe"] > best_d["sharpe"]:
                        best_d = {**stats, "trail": trail, "sl": sl, "ts": ts}

    # Also try fixed PT/SL for missed days
    for pt in [3, 5, 8, 10, 15]:
        for sl in [3, 5, 8, 10, 15]:
            for ts in [15, 30, 45, 60]:
                params = {"pt_pts": pt, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(missed_days, "C", "fixed", params, 1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    d_results.append((0, sl, ts, stats))

    d_results_trail = [(t, s, ts, st) for t, s, ts, st in d_results if t > 0]
    d_results_trail.sort(key=lambda x: x[3]["sharpe"], reverse=True)
    print(f"\n  Top 10 trailing configs (by Sharpe):")
    print(f"  {'Trail%':>7} {'SL':>4} {'TS':>4} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for trail, sl, ts, s in d_results_trail[:10]:
        print(f"  {trail:7.2f} {sl:4d} {ts:4d} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    # ── Edge E: Ultra Scalp — currently PT=1, SL=1, TS=5
    # Problem: tiny PT means tiny option moves, eaten by spread
    print(f"\n{'─'*70}")
    print("OPTIMIZING EDGE E (Ultra Scalp — all bullish, fixed PT/SL)")
    print(f"{'─'*70}")

    best_e = {"sharpe": -999}
    e_results = []
    for pt in [1, 2, 3, 4, 5, 8, 10]:
        for sl in [1, 2, 3, 5, 8, 10]:
            for ts in [5, 10, 15, 20, 30]:
                params = {"pt_pts": pt, "sl_pts": sl, "ts_min": ts}
                trades = run_edge(bullish_days, "C", "fixed", params, 1)
                pnls = [t["pnl_dollars"] for t in trades]
                stats = compute_stats(pnls, trades)
                if stats and stats["n"] > 50:
                    e_results.append((pt, sl, ts, stats))
                    if stats["sharpe"] > best_e["sharpe"]:
                        best_e = {**stats, "pt": pt, "sl": sl, "ts": ts}

    e_results.sort(key=lambda x: x[3]["sharpe"], reverse=True)
    print(f"\n  Top 10 configs (by Sharpe):")
    print(f"  {'PT':>4} {'SL':>4} {'TS':>4} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for pt, sl, ts, s in e_results[:10]:
        print(f"  {pt:4d} {sl:4d} {ts:4d} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    e_by_pnl = sorted(e_results, key=lambda x: x[3]["total"], reverse=True)
    print(f"\n  Top 10 configs (by Total P&L):")
    print(f"  {'PT':>4} {'SL':>4} {'TS':>4} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    for pt, sl, ts, s in e_by_pnl[:10]:
        print(f"  {pt:4d} {sl:4d} {ts:4d} {s['n']:5d} ${s['total']:>12,.0f} {s['wr']:5.1f}% {s['pf']:5.2f} ${s['maxdd']:>9,.0f} {s['sharpe']:7.2f}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: FINAL RESULTS — optimal params with slippage
    # ══════════════════════════════════════════════════════════════════════

    print(f"\n\n{'='*90}")
    print("PHASE 3: FINAL OPTIMIZED RESULTS (with ${:.2f} slippage)".format(SLIPPAGE))
    print(f"{'='*90}")

    # Pick best params for each weak edge
    # B and F already survive — keep original params
    # For A, C, D, E — use best Sharpe config from optimization

    final_configs = {}

    # Edge B — keep original (already strong)
    final_configs["B"] = baseline_configs["B"].copy()
    final_configs["B"]["name"] = "SPX Filtered Scalp"

    # Edge F — keep original (already strong)
    final_configs["F"] = baseline_configs["F"].copy()
    final_configs["F"]["name"] = "Current + Trail"

    # Edge A — use best from optimization
    if best_a.get("trail"):
        final_configs["A"] = {
            "dates": bullish_days, "opt": "C", "mode": "trail", "dir": 1,
            "params": {"trail_pct": best_a["trail"], "sl_pts": best_a["sl"], "ts_min": best_a["ts"]},
            "name": f"SPX Scalp (opt: trail={best_a['trail']}%, SL={best_a['sl']}, TS={best_a['ts']})"
        }
    else:
        final_configs["A"] = baseline_configs["A"].copy()
        final_configs["A"]["name"] = "SPX Scalp (no improvement found)"

    # Edge C — use best from optimization
    if best_c.get("mode") == "fixed":
        final_configs["C"] = {
            "dates": bearish_days, "opt": "P", "mode": "fixed", "dir": -1,
            "params": {"pt_pts": best_c.get("pt", 8), "sl_pts": best_c["sl"], "ts_min": best_c["ts"]},
            "name": f"Bearish Short (opt: PT={best_c.get('pt',8)}, SL={best_c['sl']}, TS={best_c['ts']})"
        }
    elif best_c.get("mode") == "trail":
        final_configs["C"] = {
            "dates": bearish_days, "opt": "P", "mode": "trail", "dir": -1,
            "params": {"trail_pct": best_c["trail"], "sl_pts": best_c["sl"], "ts_min": best_c["ts"]},
            "name": f"Bearish Short (opt: trail={best_c['trail']}%, SL={best_c['sl']}, TS={best_c['ts']})"
        }
    else:
        final_configs["C"] = baseline_configs["C"].copy()
        final_configs["C"]["name"] = "Bearish Short (no improvement found)"

    # Edge D — use best from optimization
    if best_d.get("trail"):
        final_configs["D"] = {
            "dates": missed_days, "opt": "C", "mode": "trail", "dir": 1,
            "params": {"trail_pct": best_d["trail"], "sl_pts": best_d["sl"], "ts_min": best_d["ts"]},
            "name": f"Missed Days (opt: trail={best_d['trail']}%, SL={best_d['sl']}, TS={best_d['ts']})"
        }
    else:
        final_configs["D"] = baseline_configs["D"].copy()
        final_configs["D"]["name"] = "Missed Days (no improvement found)"

    # Edge E — use best from optimization
    if best_e.get("pt"):
        final_configs["E"] = {
            "dates": bullish_days, "opt": "C", "mode": "fixed", "dir": 1,
            "params": {"pt_pts": best_e["pt"], "sl_pts": best_e["sl"], "ts_min": best_e["ts"]},
            "name": f"Ultra Scalp (opt: PT={best_e['pt']}, SL={best_e['sl']}, TS={best_e['ts']})"
        }
    else:
        final_configs["E"] = baseline_configs["E"].copy()
        final_configs["E"]["name"] = "Ultra Scalp (no improvement found)"

    # Run all final configs and save trades
    final_results = {}
    print(f"\n  {'Edge':<6} {'Name':<50} {'N':>5} {'P&L':>14} {'WR':>6} {'PF':>5} {'MaxDD':>10} {'Sharpe':>7}")
    print(f"  {'─'*100}")

    for edge_id in ["A", "B", "C", "D", "E", "F"]:
        cfg = final_configs[edge_id]
        trades = run_edge(cfg["dates"], cfg["opt"], cfg["mode"], cfg["params"], cfg["dir"])
        pnls = [t["pnl_dollars"] for t in trades]
        stats = compute_stats(pnls, trades)
        final_results[edge_id] = {"trades": trades, "stats": stats, "config": cfg}

        if stats:
            # Add edge info to each trade
            for t in trades:
                t["edge"] = edge_id
                t["edge_name"] = cfg.get("name", edge_id)

            # Save trades
            output_file = os.path.join(OUTPUT_DIR, f"edge_{edge_id}_trades.json")
            with open(output_file, "w") as f:
                json.dump(trades, f, indent=2)

            print(f"  {edge_id:<6} {cfg.get('name',''):<50} {stats['n']:>5} ${stats['total']:>12,.0f} {stats['wr']:5.1f}% {stats['pf']:5.2f} ${stats['maxdd']:>9,.0f} {stats['sharpe']:7.2f}")
        else:
            print(f"  {edge_id:<6} {cfg.get('name',''):<50}   NO TRADES")

    # ── Combined portfolio analysis ────────────────────────────────────
    print(f"\n\n{'='*90}")
    print("COMBINED PORTFOLIO ANALYSIS (all 6 edges)")
    print(f"{'='*90}")

    # Merge all trades by date
    all_trades = []
    for edge_id, res in final_results.items():
        all_trades.extend(res["trades"])
    all_trades.sort(key=lambda t: t["date"])

    if all_trades:
        all_pnls = [t["pnl_dollars"] for t in all_trades]
        all_stats = compute_stats(all_pnls, all_trades)
        print_stats(all_stats, "ALL 6 EDGES COMBINED")

        # Per-day analysis
        from collections import Counter
        day_pnls = defaultdict(float)
        day_counts = Counter()
        for t in all_trades:
            day_pnls[t["date"]] += t["pnl_dollars"]
            day_counts[t["date"]] += 1

        daily_pnl_list = [day_pnls[d] for d in sorted(day_pnls)]
        positive_days = sum(1 for p in daily_pnl_list if p > 0)
        total_days = len(daily_pnl_list)
        print(f"\n    Unique trading days: {total_days}")
        print(f"    Day win rate: {100*positive_days/total_days:.1f}%")
        print(f"    Avg daily P&L: ${mean(daily_pnl_list):,.0f}")
        print(f"    Avg positions/day: {sum(day_counts.values())/total_days:.1f}")

        # Daily equity curve drawdown
        cum = peak = dd = 0
        for p in daily_pnl_list:
            cum += p
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        print(f"    Daily MaxDD: ${dd:,.0f}")

        # Total slippage cost
        total_slip = sum(t.get("slippage_cost", 0) for t in all_trades)
        total_raw = sum(t.get("pnl_raw", 0) for t in all_trades)
        print(f"\n    Raw P&L (no slippage):  ${total_raw:,.0f}")
        print(f"    Total slippage cost:    ${total_slip:,.0f}")
        print(f"    Net P&L (after slip):   ${sum(all_pnls):,.0f}")
        print(f"    Slippage as % of raw:   {100*total_slip/total_raw:.1f}%" if total_raw > 0 else "")

    # ── Show what changed vs baseline ──────────────────────────────────
    print(f"\n\n{'='*90}")
    print("OPTIMIZATION CHANGES vs BASELINE")
    print(f"{'='*90}")
    for edge_id in ["A", "C", "D", "E"]:
        bl = baseline_results.get(edge_id, {}).get("stats")
        opt = final_results.get(edge_id, {}).get("stats")
        name = final_configs[edge_id].get("name", edge_id)
        print(f"\n  EDGE {edge_id}: {name}")
        if bl and opt:
            print(f"    Baseline: P&L=${bl['total']:>12,.0f}  Sharpe={bl['sharpe']:5.2f}  MaxDD=${bl['maxdd']:>9,.0f}")
            print(f"    Optimal:  P&L=${opt['total']:>12,.0f}  Sharpe={opt['sharpe']:5.2f}  MaxDD=${opt['maxdd']:>9,.0f}")
            delta_pnl = opt["total"] - bl["total"]
            delta_sh = opt["sharpe"] - bl["sharpe"]
            print(f"    Change:   P&L={'+' if delta_pnl>=0 else ''}${delta_pnl:,.0f}  Sharpe={'+' if delta_sh>=0 else ''}{delta_sh:.2f}")
        else:
            print(f"    Could not compare")

    print(f"\n\n{'='*90}")
    print("COMPLETE")
    print(f"{'='*90}")

    sys.stdout = old_stdout
    log_f.close()
    print("Done! Report saved to spx_edges_optimize_report.txt")


if __name__ == "__main__":
    main()
