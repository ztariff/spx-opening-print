"""
SPX Opening Print — Script 21: Option Premium Stop Test
========================================================
Test adding an option-level stop loss that exits when the option price
drops below X% of entry price. This catches premium bleed that the
SPX-level stop doesn't catch (theta decay on losing days).

Uses actual option 1-min bar data from cache to simulate real exits.
"""

import json, os, math, csv
from datetime import datetime
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
trades = json.load(open(os.path.join(SCRIPT_DIR, "options_trades.json")))

HYBRID_THRESHOLD = 25

def load_option_bars(ticker, date):
    """Load cached option 1-min bars."""
    safe = ticker.replace(":", "_").replace("/", "_")
    path = os.path.join(CACHE_DIR, f"bars_{date}_{safe}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def load_spx_intraday():
    """Load SPX 1-min bars."""
    spx = defaultdict(list)
    with open(os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")) as f:
        reader = csv.DictReader(f)
        for row in reader:
            spx[row["date"]].append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    return spx

print("Loading SPX intraday data...")
spx_intraday = load_spx_intraday()

def simulate_with_option_stop(trade, opt_bars, spx_bars, opt_stop_pct, opt_stop_after_mins=0):
    """
    Re-simulate a trade with an additional option premium stop.
    opt_stop_pct: exit if option price drops to this fraction of entry (e.g., 0.50 = 50%)
    opt_stop_after_mins: only apply option stop after this many minutes
    """
    entry_price = trade["opt_entry_price"]
    entry_open = trade["entry_open"]
    pt = trade["pt"]
    sl = trade["sl"]
    ts = trade["ts"]
    score = trade["score"]
    num_contracts = trade["opt_contracts"]
    total_premium = trade["opt_premium"]

    stop_price = entry_price * opt_stop_pct

    # Build maps
    opt_map = {}
    for bar in opt_bars:
        opt_map[bar["time"]] = bar

    exit_price = None
    exit_time = None
    exit_reason = None
    hold_mins = 0

    started = False
    bars_held = 0
    for bar in spx_bars:
        if bar["time"] <= "09:31":
            if bar["time"] == "09:31":
                started = True
            continue
        if not started:
            started = True

        bars_held += 1
        if bars_held > ts:
            break

        # Check SPX-based exits first
        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss (SPX)"
            hold_mins = bars_held
            break

        if bar["high"] >= entry_open + pt:
            exit_time = bar["time"]
            exit_reason = "Profit Target (SPX)"
            hold_mins = bars_held
            break

        # Check option premium stop
        if bars_held >= opt_stop_after_mins and bar["time"] in opt_map:
            opt_bar = opt_map[bar["time"]]
            if opt_bar["low"] <= stop_price:
                exit_time = bar["time"]
                exit_reason = "Option Premium Stop"
                hold_mins = bars_held
                break

    if exit_time is None:
        ts_idx = min(len(spx_bars) - 1, ts + 1)
        exit_time = spx_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    # Get option exit price
    if exit_reason == "Option Premium Stop":
        # Use the stop price as exit (conservative)
        exit_price = stop_price
    elif exit_time in opt_map:
        exit_price = opt_map[exit_time]["close"]
    else:
        for obar in opt_bars:
            if obar["time"] >= exit_time:
                exit_price = obar["close"]
                break
        if exit_price is None:
            exit_price = opt_bars[-1]["close"]

    pnl = num_contracts * (exit_price - entry_price) * 100
    return pnl, exit_reason, hold_mins

def compute_stats(pnls, first_date, last_date):
    if len(pnls) < 2 or stdev(pnls) == 0:
        return {}
    days = (datetime.strptime(last_date, "%Y-%m-%d") - datetime.strptime(first_date, "%Y-%m-%d")).days
    years = days / 365.25
    tpy = len(pnls) / years
    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy)
    total = sum(pnls)
    cum = peak = max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    calmar = total / max_dd if max_dd > 0 else 999
    wins = sum(1 for p in pnls if p > 0)
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    return {
        "sharpe": sharpe, "total": total, "max_dd": max_dd,
        "calmar": calmar, "pf": pf, "wr": wins/len(pnls)*100,
        "mean": mean(pnls), "stdev": stdev(pnls),
    }

# ── Test configurations ──────────────────────────────────────────────

configs = []
# Option stop at various % of entry, kicking in at various times
for stop_pct in [0.30, 0.40, 0.50, 0.60]:
    for after_mins in [0, 15, 30, 60]:
        configs.append((stop_pct, after_mins))

print(f"Testing {len(configs)} configurations across {len(trades)} trades...")
print()

results = []

# Baseline
base_pnls = [t["opt_pnl"] for t in trades]
base_stats = compute_stats(base_pnls, trades[0]["date"], trades[-1]["date"])
print(f"BASELINE: Sharpe {base_stats['sharpe']:.2f} | P&L ${base_stats['total']:,.0f} | "
      f"DD ${base_stats['max_dd']:,.0f} | Calmar {base_stats['calmar']:.2f} | PF {base_stats['pf']:.2f}")
print()

for stop_pct, after_mins in configs:
    pnls = []
    opt_stop_count = 0
    original_reasons = defaultdict(int)

    for t in trades:
        opt_bars = load_option_bars(t["option_ticker"], t["date"])
        spx_bars = spx_intraday.get(t["date"], [])

        if not opt_bars or not spx_bars:
            pnls.append(t["opt_pnl"])
            continue

        pnl, reason, hold = simulate_with_option_stop(t, opt_bars, spx_bars, stop_pct, after_mins)
        pnls.append(pnl)
        if reason == "Option Premium Stop":
            opt_stop_count += 1
            original_reasons[t["opt_exit_reason"]] += 1

    stats = compute_stats(pnls, trades[0]["date"], trades[-1]["date"])
    delta_sharpe = stats["sharpe"] - base_stats["sharpe"]
    delta_pnl = stats["total"] - base_stats["total"]

    label = f"Opt stop {stop_pct*100:.0f}% after {after_mins}min"
    print(f"  {label:30s} | Sharpe {stats['sharpe']:.2f} ({'+' if delta_sharpe>=0 else ''}{delta_sharpe:.2f}) | "
          f"P&L ${stats['total']:>10,.0f} ({'+' if delta_pnl>=0 else ''}{delta_pnl:>10,.0f}) | "
          f"DD ${stats['max_dd']:>8,.0f} | Calmar {stats['calmar']:.2f} | PF {stats['pf']:.2f} | "
          f"Triggered: {opt_stop_count}")

    if original_reasons:
        orig_str = ", ".join(f"{k}: {v}" for k, v in sorted(original_reasons.items()))
        print(f"    {'':30s}   Replaced: {orig_str}")

    results.append((stats["sharpe"], stats["total"], stats["max_dd"], stats["calmar"],
                     label, opt_stop_count, stats["pf"]))

print()
print("=" * 90)
print("TOP 10 BY SHARPE")
print("=" * 90)
results.sort(key=lambda x: x[0], reverse=True)
for i, (sharpe, total, dd, calmar, label, triggered, pf) in enumerate(results[:10]):
    delta = sharpe - base_stats["sharpe"]
    print(f"  #{i+1}: {label:30s} | Sharpe {sharpe:.2f} ({'+' if delta>=0 else ''}{delta:.2f}) | "
          f"P&L ${total:>10,.0f} | DD ${dd:>8,.0f} | Calmar {calmar:.2f} | PF {pf:.2f} | Triggered: {triggered}")

print(f"\n  Baseline: Sharpe {base_stats['sharpe']:.2f} | P&L ${base_stats['total']:,.0f} | DD ${base_stats['max_dd']:,.0f}")
