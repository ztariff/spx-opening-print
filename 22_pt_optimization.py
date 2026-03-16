"""
SPX Opening Print — Script 22: Profit Target Optimization for Sharpe
=====================================================================
Test tighter profit targets using real option 1-min bar data.
Currently PT varies by signal (15, 20, 50 SPX points).
Test scaling PT down to see if capturing smaller wins more reliably improves Sharpe.
"""

import json, os, math, csv
from datetime import datetime
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
trades = json.load(open(os.path.join(SCRIPT_DIR, "options_trades.json")))

def load_bars(ticker, date):
    safe = ticker.replace(":", "_").replace("/", "_")
    path = os.path.join(CACHE_DIR, f"bars_{date}_{safe}.json")
    if os.path.exists(path):
        return json.load(open(path))
    return None

def load_spx_intraday():
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

def simulate_trade(trade, opt_bars, spx_bars, pt_mult=1.0, sl_mult=1.0, ts_override=None):
    """
    Re-simulate a trade with modified PT/SL/TS.
    pt_mult: multiply the original PT by this factor
    sl_mult: multiply the original SL by this factor
    ts_override: override time stop (in bars/minutes)
    """
    entry_price = trade["opt_entry_price"]
    entry_open = trade["entry_open"]
    pt = trade["pt"] * pt_mult
    sl = trade["sl"] * sl_mult
    ts = ts_override if ts_override else trade["ts"]
    contracts = trade["opt_contracts"]

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

    if exit_time is None:
        ts_idx = min(len(spx_bars) - 1, int(ts) + 1)
        exit_time = spx_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    # Get option exit price
    if exit_time in opt_map:
        exit_price = opt_map[exit_time]["close"]
    else:
        for obar in opt_bars:
            if obar["time"] >= exit_time:
                exit_price = obar["close"]
                break
        if exit_price is None:
            exit_price = opt_bars[-1]["close"]

    pnl = contracts * (exit_price - entry_price) * 100
    return pnl, exit_reason, hold_mins

def compute_stats(pnls):
    if len(pnls) < 2 or stdev(pnls) == 0:
        return None
    days = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") - datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
    tpy = len(pnls) / (days / 365.25)
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
    wr = wins / len(pnls) * 100
    return {"sharpe": sharpe, "total": total, "max_dd": max_dd, "calmar": calmar,
            "pf": pf, "wr": wr, "mean": mean(pnls), "stdev": stdev(pnls)}

# Baseline
base_pnls = [t["opt_pnl"] for t in trades]
base = compute_stats(base_pnls)
print(f"\nBASELINE: Sharpe {base['sharpe']:.2f} | P&L ${base['total']:,.0f} | DD ${base['max_dd']:,.0f} | "
      f"Calmar {base['calmar']:.2f} | PF {base['pf']:.2f} | WR {base['wr']:.1f}%")

# Show current PT distribution
pt_dist = defaultdict(int)
for t in trades:
    pt_dist[t["pt"]] += 1
print(f"\nCurrent PT distribution: {dict(sorted(pt_dist.items()))}")
print()

results = []

# ══════════════════════════════════════════════════════════════════════
# TEST 1: Scale PT by multiplier (keep SL and TS same)
# ══════════════════════════════════════════════════════════════════════

print("=" * 100)
print("TEST 1: Scale profit target by multiplier")
print("=" * 100)

for pt_mult in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5]:
    pnls = []
    reasons = defaultdict(int)
    for t in trades:
        opt_bars = load_bars(t["option_ticker"], t["date"])
        spx_bars = spx_intraday.get(t["date"], [])
        if not opt_bars or not spx_bars:
            pnls.append(t["opt_pnl"])
            continue
        pnl, reason, hold = simulate_trade(t, opt_bars, spx_bars, pt_mult=pt_mult)
        pnls.append(pnl)
        reasons[reason] += 1

    s = compute_stats(pnls)
    delta = s["sharpe"] - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  PT x{pt_mult:.1f}: Sharpe {s['sharpe']:.2f} ({sign}{delta:.2f}) | "
          f"P&L ${s['total']:>10,.0f} | DD ${s['max_dd']:>8,.0f} | "
          f"Calmar {s['calmar']:.2f} | PF {s['pf']:.2f} | WR {s['wr']:.1f}% | "
          f"PT:{reasons.get('Profit Target (SPX)',0)} SL:{reasons.get('Stop Loss (SPX)',0)} TS:{reasons.get('Time Stop',0)}")
    results.append((s["sharpe"], s["total"], s["max_dd"], s["calmar"], s["pf"], s["wr"],
                     f"PT x{pt_mult:.1f}"))

# ══════════════════════════════════════════════════════════════════════
# TEST 2: Fixed PT (override all signal-specific PTs)
# ══════════════════════════════════════════════════════════════════════

print()
print("=" * 100)
print("TEST 2: Fixed profit target (SPX points)")
print("=" * 100)

for fixed_pt in [5, 8, 10, 12, 15, 20, 25, 30, 35, 40]:
    pnls = []
    reasons = defaultdict(int)
    for t in trades:
        opt_bars = load_bars(t["option_ticker"], t["date"])
        spx_bars = spx_intraday.get(t["date"], [])
        if not opt_bars or not spx_bars:
            pnls.append(t["opt_pnl"])
            continue
        # Override PT by using mult that gives fixed_pt
        if t["pt"] > 0:
            mult = fixed_pt / t["pt"]
        else:
            mult = 1.0
        pnl, reason, hold = simulate_trade(t, opt_bars, spx_bars, pt_mult=mult)
        pnls.append(pnl)
        reasons[reason] += 1

    s = compute_stats(pnls)
    delta = s["sharpe"] - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  PT={fixed_pt:>2} pts: Sharpe {s['sharpe']:.2f} ({sign}{delta:.2f}) | "
          f"P&L ${s['total']:>10,.0f} | DD ${s['max_dd']:>8,.0f} | "
          f"Calmar {s['calmar']:.2f} | PF {s['pf']:.2f} | WR {s['wr']:.1f}% | "
          f"PT:{reasons.get('Profit Target (SPX)',0)} SL:{reasons.get('Stop Loss (SPX)',0)} TS:{reasons.get('Time Stop',0)}")
    results.append((s["sharpe"], s["total"], s["max_dd"], s["calmar"], s["pf"], s["wr"],
                     f"PT={fixed_pt} pts"))

# ══════════════════════════════════════════════════════════════════════
# TEST 3: Tighter PT + tighter TS (take profit faster, don't hold as long)
# ══════════════════════════════════════════════════════════════════════

print()
print("=" * 100)
print("TEST 3: Tighter PT + shorter time stop")
print("=" * 100)

for pt_mult, ts_over in [(0.5, 120), (0.5, 180), (0.6, 120), (0.6, 180), (0.6, 240),
                           (0.7, 180), (0.7, 240), (0.8, 240), (0.8, 300)]:
    pnls = []
    reasons = defaultdict(int)
    for t in trades:
        opt_bars = load_bars(t["option_ticker"], t["date"])
        spx_bars = spx_intraday.get(t["date"], [])
        if not opt_bars or not spx_bars:
            pnls.append(t["opt_pnl"])
            continue
        pnl, reason, hold = simulate_trade(t, opt_bars, spx_bars, pt_mult=pt_mult, ts_override=ts_over)
        pnls.append(pnl)
        reasons[reason] += 1

    s = compute_stats(pnls)
    delta = s["sharpe"] - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  PT x{pt_mult} + TS={ts_over}min: Sharpe {s['sharpe']:.2f} ({sign}{delta:.2f}) | "
          f"P&L ${s['total']:>10,.0f} | DD ${s['max_dd']:>8,.0f} | "
          f"Calmar {s['calmar']:.2f} | PF {s['pf']:.2f} | WR {s['wr']:.1f}% | "
          f"PT:{reasons.get('Profit Target (SPX)',0)} SL:{reasons.get('Stop Loss (SPX)',0)} TS:{reasons.get('Time Stop',0)}")
    results.append((s["sharpe"], s["total"], s["max_dd"], s["calmar"], s["pf"], s["wr"],
                     f"PT x{pt_mult} + TS={ts_over}min"))

# ══════════════════════════════════════════════════════════════════════
# TEST 4: Tighter PT + tighter SL (better risk/reward)
# ══════════════════════════════════════════════════════════════════════

print()
print("=" * 100)
print("TEST 4: Tighter PT + tighter SL")
print("=" * 100)

for pt_mult, sl_mult in [(0.5, 0.5), (0.6, 0.6), (0.6, 0.5), (0.7, 0.7), (0.7, 0.5),
                           (0.8, 0.8), (0.8, 0.6), (0.5, 0.7), (0.6, 0.8)]:
    pnls = []
    reasons = defaultdict(int)
    for t in trades:
        opt_bars = load_bars(t["option_ticker"], t["date"])
        spx_bars = spx_intraday.get(t["date"], [])
        if not opt_bars or not spx_bars:
            pnls.append(t["opt_pnl"])
            continue
        pnl, reason, hold = simulate_trade(t, opt_bars, spx_bars, pt_mult=pt_mult, sl_mult=sl_mult)
        pnls.append(pnl)
        reasons[reason] += 1

    s = compute_stats(pnls)
    delta = s["sharpe"] - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  PT x{pt_mult} + SL x{sl_mult}: Sharpe {s['sharpe']:.2f} ({sign}{delta:.2f}) | "
          f"P&L ${s['total']:>10,.0f} | DD ${s['max_dd']:>8,.0f} | "
          f"Calmar {s['calmar']:.2f} | PF {s['pf']:.2f} | WR {s['wr']:.1f}% | "
          f"PT:{reasons.get('Profit Target (SPX)',0)} SL:{reasons.get('Stop Loss (SPX)',0)} TS:{reasons.get('Time Stop',0)}")
    results.append((s["sharpe"], s["total"], s["max_dd"], s["calmar"], s["pf"], s["wr"],
                     f"PT x{pt_mult} + SL x{sl_mult}"))

# ══════════════════════════════════════════════════════════════════════
# RANKING
# ══════════════════════════════════════════════════════════════════════

print()
print("=" * 100)
print("TOP 15 BY SHARPE RATIO")
print("=" * 100)
results.sort(key=lambda x: x[0], reverse=True)
for i, (sharpe, total, dd, calmar, pf, wr, label) in enumerate(results[:15]):
    delta = sharpe - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  #{i+1}: Sharpe {sharpe:.2f} ({sign}{delta:.2f}) | P&L ${total:>10,.0f} | "
          f"DD ${dd:>8,.0f} | Calmar {calmar:.2f} | PF {pf:.2f} | WR {wr:.1f}% | {label}")

print()
print("=" * 100)
print("TOP 10 BY SHARPE (with P&L >= 50% of baseline)")
print("=" * 100)
min_pnl = base["total"] * 0.50
filtered = [(s, t, d, c, p, w, l) for s, t, d, c, p, w, l in results if t >= min_pnl]
filtered.sort(key=lambda x: x[0], reverse=True)
for i, (sharpe, total, dd, calmar, pf, wr, label) in enumerate(filtered[:10]):
    delta = sharpe - base["sharpe"]
    sign = "+" if delta >= 0 else ""
    print(f"  #{i+1}: Sharpe {sharpe:.2f} ({sign}{delta:.2f}) | P&L ${total:>10,.0f} | "
          f"DD ${dd:>8,.0f} | Calmar {calmar:.2f} | PF {pf:.2f} | WR {wr:.1f}% | {label}")

print(f"\n  Baseline: Sharpe {base['sharpe']:.2f} | P&L ${base['total']:,.0f} | DD ${base['max_dd']:,.0f} | "
      f"Calmar {base['calmar']:.2f} | PF {base['pf']:.2f} | WR {base['wr']:.1f}%")
