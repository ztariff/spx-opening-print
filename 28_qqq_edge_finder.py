"""
QQQ Opening Print — Script 28: Comprehensive Edge Finder
==========================================================
We're missing something on QQQ. Test EVERYTHING:

1. PT/SL asymmetry: tight PT + wide SL, wide PT + tight SL, equal, etc.
2. Different entry timing: 9:31, 9:35, 9:40, 9:45
3. Buy on ANY first bar (not just bullish)
4. Trailing stop approaches
5. Time-only exits (no PT/SL at all, just hold N minutes)
6. Scale-in: buy at open, add if it dips
7. Direction: buy calls vs buy puts on bearish bars
8. First bar SIZE matters? (big bullish vs small bullish)
9. Open relative to prior range (open at high/low/mid of prior day)
10. Multi-timeframe: first 5-min bar instead of 1-min

All tested on LINEAR returns first for speed.
"""

import csv, json, os, sys, math
from datetime import datetime, timedelta
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(SCRIPT_DIR, "qqq_edge_finder_report.txt")

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

_report_file = open(REPORT_PATH, "w")
sys.stdout = Tee(sys.__stdout__, _report_file)

# ── Data loading ─────────────────────────────────────────────────────

def load_daily(filepath):
    data = {}
    dates = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row["date"]
            data[d] = {k: float(row[k]) for k in ["open", "high", "low", "close"]}
            dates.append(d)
    return data, sorted(set(dates))

def load_intraday(filepath):
    data = defaultdict(list)
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["date"]].append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    return data

def compute_ma(daily, dates, target, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target: idx = i; break
    if idx is None or idx < period: return None
    vals = [daily[dates[j]]["close"] for j in range(idx - period, idx) if dates[j] in daily]
    return mean(vals) if len(vals) == period else None

def compute_rsi(daily, dates, target, period=14):
    idx = None
    for i, d in enumerate(dates):
        if d == target: idx = i; break
    if idx is None or idx < period + 1: return None
    gains, losses = [], []
    for j in range(idx - period, idx):
        if j < 1 or dates[j] not in daily or dates[j-1] not in daily: continue
        change = daily[dates[j]]["close"] - daily[dates[j-1]]["close"]
        gains.append(max(change, 0)); losses.append(max(-change, 0))
    if not gains: return None
    ag = sum(gains)/len(gains); al = sum(losses)/len(losses)
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

# ── Load data ────────────────────────────────────────────────────────

print("Loading data...")
qqq_daily, qqq_dates = load_daily(os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv"))
qqq_intraday = load_intraday(os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv"))
vix_daily, _ = load_daily(os.path.join(SCRIPT_DIR, "vix_daily_bars.csv"))

intra_dates = sorted(qqq_intraday.keys())
intra_idx = {d: i for i, d in enumerate(intra_dates)}
print(f"  Days: {len(intra_dates)}")

# ── Build enriched day data ──────────────────────────────────────────

print("Building day data...")
all_days = []
for d in intra_dates:
    bars = qqq_intraday[d]
    if len(bars) < 30: continue
    dt = datetime.strptime(d, "%Y-%m-%d")
    entry = bars[0]["open"]

    # First bar metrics
    fb_ret = (bars[0]["close"] - bars[0]["open"]) / bars[0]["open"] * 100
    fb_range = (bars[0]["high"] - bars[0]["low"]) / bars[0]["open"] * 100
    bullish = bars[0]["close"] > bars[0]["open"]

    # First 5-min bar
    if len(bars) >= 5:
        fb5_high = max(b["high"] for b in bars[:5])
        fb5_low = min(b["low"] for b in bars[:5])
        fb5_close = bars[4]["close"]
        fb5_bullish = fb5_close > entry
        fb5_ret = (fb5_close - entry) / entry * 100
    else:
        fb5_bullish = bullish
        fb5_ret = fb_ret

    day = {
        "date": d, "dow": dt.strftime("%A"), "entry": entry, "bars": bars,
        "bullish": bullish, "fb_ret": fb_ret, "fb_range": fb_range,
        "fb5_bullish": fb5_bullish, "fb5_ret": fb5_ret,
        "vix": vix_daily[d]["open"] if d in vix_daily else None,
    }

    # Prior day info
    idx = intra_idx[d]
    if idx > 0:
        prev_d = intra_dates[idx - 1]
        prev_bars = qqq_intraday.get(prev_d, [])
        if prev_bars and len(prev_bars) > 1:
            prev_close = prev_bars[-1]["close"]
            prev_open = prev_bars[0]["open"]
            day["gap_pct"] = (entry - prev_close) / prev_close * 100
            day["prev_ret"] = (prev_close - prev_open) / prev_open * 100
            prev_high = max(b["high"] for b in prev_bars)
            prev_low = min(b["low"] for b in prev_bars)
            day["prev_range"] = (prev_high - prev_low) / prev_close * 100
            # Open relative to prior day range
            if prev_high != prev_low:
                day["open_in_range"] = (entry - prev_low) / (prev_high - prev_low)
            else:
                day["open_in_range"] = 0.5

    # MAs
    ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
    ma200 = compute_ma(qqq_daily, qqq_dates, d, 200)
    day["above_ma50"] = entry > ma50 if ma50 else None
    day["above_ma200"] = entry > ma200 if ma200 else None
    rsi = compute_rsi(qqq_daily, qqq_dates, d, 14)
    day["rsi"] = rsi

    all_days.append(day)

print(f"  Enriched days: {len(all_days)}")


# ══════════════════════════════════════════════════════════════════════
# SIMULATION ENGINE
# ══════════════════════════════════════════════════════════════════════

def sim_trade(bars, entry_price, entry_bar_idx, pt_pct, sl_pct, ts_bars, trailing_pct=None):
    """Simulate a single trade with flexible entry point."""
    pt = entry_price * pt_pct / 100
    sl = entry_price * sl_pct / 100

    peak_price = entry_price

    for bi in range(entry_bar_idx + 1, min(entry_bar_idx + ts_bars + 1, len(bars))):
        bar = bars[bi]

        # Trailing stop: update stop level as price rises
        if trailing_pct and bar["high"] > peak_price:
            peak_price = bar["high"]

        # Check stop loss (or trailing stop)
        if trailing_pct:
            trail_stop = peak_price * (1 - trailing_pct / 100)
            if bar["low"] <= trail_stop:
                return (trail_stop - entry_price) / entry_price * 100, "trailing_stop"

        if bar["low"] <= entry_price - sl:
            return -sl_pct, "stop_loss"
        if bar["high"] >= entry_price + pt:
            return pt_pct, "profit_target"

    # Time stop
    final_idx = min(entry_bar_idx + ts_bars, len(bars) - 1)
    return (bars[final_idx]["close"] - entry_price) / entry_price * 100, "time_stop"


def compute_stats(pnls, label=""):
    if len(pnls) < 20:
        return None
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    sd = stdev(pnls) if len(pnls) > 1 else 1
    sharpe = mean(pnls) / sd if sd > 0 else 0
    cum = peak = max_dd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {"n": len(pnls), "wr": wr, "pf": pf, "sharpe": sharpe,
            "total": total, "max_dd": max_dd, "mean": mean(pnls), "stdev": sd}


def print_result(label, r, baseline_sharpe=0):
    if r is None:
        print(f"  {label:60s}   -- too few trades --")
        return
    delta = r["sharpe"] - baseline_sharpe
    sign = "+" if delta >= 0 else ""
    print(f"  {label:60s} N={r['n']:>4d} Sh={r['sharpe']:>6.3f} WR={r['wr']:>5.1f}% "
          f"PF={r['pf']:>5.2f} Tot={r['total']:>7.2f}% DD={r['max_dd']:>5.3f}% ({sign}{delta:.3f})")


# ══════════════════════════════════════════════════════════════════════
# TEST 1: ENTRY DIRECTION — does first bar direction even matter?
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 1: DOES FIRST BAR DIRECTION MATTER?")
print("=" * 100)
print("  Testing: buy at open regardless vs bullish only vs bearish only")
print("  Using PT=0.20% SL=0.10% TS=30")

for label, filt in [
    ("ALL days (buy at open)", lambda d: True),
    ("Bullish first bar only", lambda d: d["bullish"]),
    ("Bearish first bar only", lambda d: not d["bullish"]),
    ("Big bullish (>0.05%)", lambda d: d["fb_ret"] > 0.05),
    ("Small bullish (<0.05%)", lambda d: d["bullish"] and d["fb_ret"] <= 0.05),
    ("Big bearish (<-0.05%)", lambda d: d["fb_ret"] < -0.05),
    ("5-min bar bullish", lambda d: d["fb5_bullish"]),
    ("5-min bar bearish", lambda d: not d["fb5_bullish"]),
]:
    pnls = []
    for day in all_days:
        if not filt(day): continue
        ret, _ = sim_trade(day["bars"], day["entry"], 0, 0.20, 0.10, 30)
        pnls.append(ret)
    r = compute_stats(pnls)
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 2: MASSIVE PT/SL GRID — including asymmetric combos
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 2: PT/SL ASYMMETRY GRID (bullish first bar)")
print("=" * 100)
print("  Testing wide range of PT/SL ratios")

grid_results = []
for pt in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00, 1.50]:
    for sl in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00]:
        for ts in [15, 30, 60, 120, 240, 390]:
            pnls = []
            for day in all_days:
                if not day["bullish"]: continue
                ret, _ = sim_trade(day["bars"], day["entry"], 0, pt, sl, ts)
                pnls.append(ret)
            r = compute_stats(pnls)
            if r:
                grid_results.append((r["sharpe"], pt, sl, ts, r))

grid_results.sort(key=lambda x: x[0], reverse=True)
print(f"\n  TOP 30 PT/SL/TS COMBOS BY SHARPE:")
print(f"  {'PT%':>5s} {'SL%':>5s} {'TS':>4s} | {'N':>4s} {'Sharpe':>7s} {'WR':>5s} {'PF':>5s} {'AvgRet':>7s} {'Total':>8s} {'MaxDD':>6s} {'PT:SL':>5s}")
print("  " + "-" * 85)
for i, (sh, pt, sl, ts, r) in enumerate(grid_results[:30]):
    ratio = pt / sl if sl > 0 else 999
    print(f"  {pt:>5.2f} {sl:>5.2f} {ts:>4d} | {r['n']:>4d} {sh:>7.3f} {r['wr']:>5.1f} {r['pf']:>5.2f} "
          f"{r['mean']:>7.4f} {r['total']:>8.2f} {r['max_dd']:>6.3f} {ratio:>5.1f}")

# Also show best by TOTAL P&L
grid_by_total = sorted(grid_results, key=lambda x: x[4]["total"], reverse=True)
print(f"\n  TOP 15 BY TOTAL RETURN:")
print(f"  {'PT%':>5s} {'SL%':>5s} {'TS':>4s} | {'N':>4s} {'Sharpe':>7s} {'WR':>5s} {'PF':>5s} {'Total':>8s} {'MaxDD':>6s}")
print("  " + "-" * 75)
for i, (sh, pt, sl, ts, r) in enumerate(grid_by_total[:15]):
    print(f"  {pt:>5.2f} {sl:>5.2f} {ts:>4d} | {r['n']:>4d} {sh:>7.3f} {r['wr']:>5.1f} {r['pf']:>5.2f} "
          f"{r['total']:>8.2f} {r['max_dd']:>6.3f}")

# Best combo for next tests
best_sh, best_pt, best_sl, best_ts, _ = grid_results[0]
# Also get a good "balanced" combo (Sharpe > 0.5 with highest total)
balanced = [(sh, pt, sl, ts, r) for sh, pt, sl, ts, r in grid_results if sh > 0.45]
balanced.sort(key=lambda x: x[4]["total"], reverse=True)
if balanced:
    bal_sh, bal_pt, bal_sl, bal_ts, _ = balanced[0]
    print(f"\n  Best Sharpe combo: PT={best_pt}% SL={best_sl}% TS={best_ts} (Sharpe={best_sh:.3f})")
    print(f"  Best balanced combo: PT={bal_pt}% SL={bal_sl}% TS={bal_ts} (Sharpe={bal_sh:.3f}, Total={balanced[0][4]['total']:.2f}%)")


# ══════════════════════════════════════════════════════════════════════
# TEST 3: DIFFERENT ENTRY TIMING
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 3: ENTRY TIMING (when do we enter?)")
print("=" * 100)
print("  Testing: buy at bar 0, 1, 2, 5, 10, 15 (i.e., 9:30, 9:31, 9:32, 9:35, 9:40, 9:45)")

for entry_offset in [0, 1, 2, 5, 10, 15]:
    for pt, sl, ts in [(0.20, 0.10, 30), (0.30, 0.20, 60), (0.50, 0.30, 120), (0.80, 0.50, 240)]:
        pnls = []
        for day in all_days:
            bars = day["bars"]
            if entry_offset >= len(bars): continue
            # Check first bar direction based on bars before entry
            if entry_offset > 0:
                direction_bullish = bars[entry_offset - 1]["close"] > bars[0]["open"]
            else:
                direction_bullish = day["bullish"]
            if not direction_bullish: continue

            entry_price = bars[entry_offset]["close"] if entry_offset > 0 else bars[0]["open"]
            ret, _ = sim_trade(bars, entry_price, entry_offset, pt, sl, ts)
            pnls.append(ret)
        r = compute_stats(pnls)
        label = f"Entry @bar {entry_offset} (9:{30+entry_offset:02d}) PT={pt}% SL={sl}% TS={ts}"
        print_result(label, r)
    print()


# ══════════════════════════════════════════════════════════════════════
# TEST 4: TRAILING STOPS
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 4: TRAILING STOPS")
print("=" * 100)

for trail_pct in [0.05, 0.10, 0.15, 0.20, 0.30]:
    for ts in [30, 60, 120, 240, 390]:
        pnls = []
        for day in all_days:
            if not day["bullish"]: continue
            ret, _ = sim_trade(day["bars"], day["entry"], 0,
                              999, 0.10, ts, trailing_pct=trail_pct)
            pnls.append(ret)
        r = compute_stats(pnls)
        label = f"Trail {trail_pct}% + SL 0.10% + TS {ts}"
        print_result(label, r)

# Trailing with initial PT target
print("\n  --- Trailing + PT cap ---")
for trail_pct in [0.10, 0.15, 0.20]:
    for pt in [0.30, 0.50, 0.80]:
        for ts in [60, 120, 240]:
            pnls = []
            for day in all_days:
                if not day["bullish"]: continue
                ret, _ = sim_trade(day["bars"], day["entry"], 0,
                                  pt, 0.10, ts, trailing_pct=trail_pct)
                pnls.append(ret)
            r = compute_stats(pnls)
            label = f"Trail {trail_pct}% + PT {pt}% + SL 0.10% + TS {ts}"
            print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 5: TIME-ONLY EXITS (no PT/SL — just hold for N minutes)
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 5: TIME-ONLY EXITS (hold for exactly N minutes)")
print("=" * 100)

for hold in [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 390]:
    pnls = []
    for day in all_days:
        if not day["bullish"]: continue
        bars = day["bars"]
        exit_idx = min(hold, len(bars) - 1)
        ret = (bars[exit_idx]["close"] - day["entry"]) / day["entry"] * 100
        pnls.append(ret)
    r = compute_stats(pnls)
    label = f"Hold {hold} min (bullish only)"
    print_result(label, r)

print()
for hold in [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 390]:
    pnls = []
    for day in all_days:
        bars = day["bars"]
        exit_idx = min(hold, len(bars) - 1)
        ret = (bars[exit_idx]["close"] - day["entry"]) / day["entry"] * 100
        pnls.append(ret)
    r = compute_stats(pnls)
    label = f"Hold {hold} min (ALL days)"
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 6: FIRST BAR SIZE MATTERS?
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 6: FIRST BAR SIZE / CHARACTERISTICS")
print("=" * 100)

# Split by first bar range
ranges = [d["fb_range"] for d in all_days if d["bullish"]]
med_range = sorted(ranges)[len(ranges)//2]
print(f"  Median first bar range: {med_range:.4f}%")

for label, filt in [
    ("Bullish + small 1st bar (< median)", lambda d: d["bullish"] and d["fb_range"] < med_range),
    ("Bullish + big 1st bar (> median)", lambda d: d["bullish"] and d["fb_range"] >= med_range),
    ("Bullish + tiny 1st bar (< 0.05%)", lambda d: d["bullish"] and d["fb_range"] < 0.05),
    ("Bullish + large 1st bar (> 0.15%)", lambda d: d["bullish"] and d["fb_range"] > 0.15),
    ("Bullish + huge 1st bar (> 0.30%)", lambda d: d["bullish"] and d["fb_range"] > 0.30),
    ("Bullish + 1st bar ret > 0.10%", lambda d: d["fb_ret"] > 0.10),
    ("Bullish + 1st bar ret > 0.20%", lambda d: d["fb_ret"] > 0.20),
    ("Bullish + 1st bar ret 0.01-0.10%", lambda d: 0.01 <= d["fb_ret"] <= 0.10),
]:
    pnls = []
    for day in all_days:
        if not filt(day): continue
        ret, _ = sim_trade(day["bars"], day["entry"], 0, 0.20, 0.10, 30)
        pnls.append(ret)
    r = compute_stats(pnls)
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 7: OPEN POSITION IN PRIOR DAY'S RANGE
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 7: WHERE DOES QQQ OPEN RELATIVE TO YESTERDAY'S RANGE?")
print("=" * 100)

for label, filt in [
    ("Open above prior high (gap above range)", lambda d: d.get("open_in_range", 0.5) > 1.0),
    ("Open in top 25% of prior range", lambda d: d.get("open_in_range", 0.5) > 0.75),
    ("Open in middle 50% of prior range", lambda d: 0.25 <= d.get("open_in_range", 0.5) <= 0.75),
    ("Open in bottom 25% of prior range", lambda d: d.get("open_in_range", 0.5) < 0.25),
    ("Open below prior low (gap below range)", lambda d: d.get("open_in_range", 0.5) < 0.0),
]:
    pnls = []
    for day in all_days:
        if not day["bullish"]: continue
        if not filt(day): continue
        ret, _ = sim_trade(day["bars"], day["entry"], 0, 0.20, 0.10, 30)
        pnls.append(ret)
    r = compute_stats(pnls)
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 8: WIDE STOP APPROACHES (let the trade breathe)
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 8: WIDE STOP APPROACHES — let the trade breathe")
print("=" * 100)
print("  Testing: tight PT with VERY wide SL, or no SL at all")

for label, pt, sl, ts in [
    ("PT=0.20% NO stop TS=30", 0.20, 99.0, 30),
    ("PT=0.20% NO stop TS=60", 0.20, 99.0, 60),
    ("PT=0.30% NO stop TS=30", 0.30, 99.0, 30),
    ("PT=0.30% NO stop TS=60", 0.30, 99.0, 60),
    ("PT=0.30% NO stop TS=120", 0.30, 99.0, 120),
    ("PT=0.50% NO stop TS=60", 0.50, 99.0, 60),
    ("PT=0.50% NO stop TS=120", 0.50, 99.0, 120),
    ("PT=0.50% NO stop TS=240", 0.50, 99.0, 240),
    ("PT=0.20% SL=0.50% TS=30", 0.20, 0.50, 30),
    ("PT=0.20% SL=1.00% TS=30", 0.20, 1.00, 30),
    ("PT=0.30% SL=0.50% TS=60", 0.30, 0.50, 60),
    ("PT=0.30% SL=1.00% TS=60", 0.30, 1.00, 60),
    ("PT=0.50% SL=1.00% TS=120", 0.50, 1.00, 120),
    ("PT=0.50% SL=1.50% TS=240", 0.50, 1.50, 240),
    ("PT=0.80% SL=1.00% TS=240", 0.80, 1.00, 240),
    ("PT=1.00% SL=1.50% TS=390", 1.00, 1.50, 390),
    ("PT=1.50% SL=1.00% TS=390", 1.50, 1.00, 390),
]:
    pnls = []
    for day in all_days:
        if not day["bullish"]: continue
        ret, _ = sim_trade(day["bars"], day["entry"], 0, pt, sl, ts)
        pnls.append(ret)
    r = compute_stats(pnls)
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 9: BEST LINEAR COMBOS + FILTERS (for highest absolute return)
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 9: BEST PT/SL + FILTER COMBOS FOR MAXIMUM TOTAL RETURN")
print("=" * 100)

# Take top 5 by total return from grid AND top 5 by sharpe
top_configs = []
seen = set()
for _, pt, sl, ts, r in grid_by_total[:5]:
    key = (pt, sl, ts)
    if key not in seen:
        top_configs.append((pt, sl, ts, "high_total"))
        seen.add(key)
for _, pt, sl, ts, r in grid_results[:5]:
    key = (pt, sl, ts)
    if key not in seen:
        top_configs.append((pt, sl, ts, "high_sharpe"))
        seen.add(key)

filters = [
    ("No filter", lambda d: True),
    ("VIX >= 16", lambda d: d.get("vix") and d["vix"] >= 16),
    ("VIX >= 18", lambda d: d.get("vix") and d["vix"] >= 18),
    ("VIX 16-30", lambda d: d.get("vix") and 16 <= d["vix"] <= 30),
    ("Above SMA50", lambda d: d.get("above_ma50") == True),
    ("RSI 35-70", lambda d: d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("RSI 40-60", lambda d: d.get("rsi") and 40 <= d["rsi"] <= 60),
    ("No Tuesday", lambda d: d["dow"] != "Tuesday"),
    ("Prior red", lambda d: d.get("prev_ret", 0) < 0),
    ("Gap down", lambda d: d.get("gap_pct", 0) < 0),
    ("VIX>=16 + Above SMA50", lambda d: d.get("vix") and d["vix"] >= 16 and d.get("above_ma50") == True),
    ("VIX>=16 + RSI 35-70", lambda d: d.get("vix") and d["vix"] >= 16 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("VIX>=18 + Prior red", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0),
    ("VIX>=18 + Gap down", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("gap_pct", 0) < 0),
    ("Above SMA50 + VIX 16-30 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 30 and d.get("rsi") and 35 <= d["rsi"] <= 70),
]

for pt, sl, ts, source in top_configs:
    print(f"\n  Config: PT={pt}% SL={sl}% TS={ts} (from {source})")
    print(f"  {'-'*95}")
    for flabel, filt in filters:
        pnls = []
        for day in all_days:
            if not day["bullish"]: continue
            if not filt(day): continue
            ret, _ = sim_trade(day["bars"], day["entry"], 0, pt, sl, ts)
            pnls.append(ret)
        r = compute_stats(pnls)
        label = f"  {flabel}"
        print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# TEST 10: WHAT IF WE BUY ON BEARISH FIRST BAR? (true contrarian)
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("TEST 10: BEARISH FIRST BAR — BUY THE DIP (true contrarian)")
print("=" * 100)
print("  What if we buy when the first bar is bearish?")

for pt, sl, ts in [(0.10, 0.10, 15), (0.15, 0.10, 30), (0.20, 0.10, 30),
                    (0.20, 0.15, 30), (0.30, 0.15, 60), (0.30, 0.20, 60),
                    (0.40, 0.20, 120), (0.50, 0.30, 120), (0.80, 0.50, 240),
                    (0.20, 0.50, 30), (0.30, 0.50, 60), (0.20, 1.00, 30)]:
    pnls = []
    for day in all_days:
        if day["bullish"]: continue  # only bearish first bars
        bars = day["bars"]
        # Enter at close of first bar (after the dip)
        entry_price = bars[0]["close"]
        ret, _ = sim_trade(bars, entry_price, 0, pt, sl, ts)
        pnls.append(ret)
    r = compute_stats(pnls)
    label = f"BUY bearish close: PT={pt}% SL={sl}% TS={ts}"
    print_result(label, r)


# ══════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 100)
print("SUMMARY: ALL TOP APPROACHES RANKED")
print("=" * 100)

# Collect everything from test 2 grid (top 10), add key findings
all_top = grid_results[:10]
print(f"\n  TOP 10 OVERALL (from full grid search, bullish first bar):")
print(f"  {'PT%':>5s} {'SL%':>5s} {'TS':>4s} | {'N':>4s} {'Sharpe':>7s} {'WR':>5s} {'PF':>5s} {'Total':>8s} {'MaxDD':>6s}")
print("  " + "-" * 75)
for i, (sh, pt, sl, ts, r) in enumerate(all_top):
    print(f"  {pt:>5.2f} {sl:>5.2f} {ts:>4d} | {r['n']:>4d} {sh:>7.3f} {r['wr']:>5.1f} {r['pf']:>5.2f} "
          f"{r['total']:>8.2f} {r['max_dd']:>6.3f}")


print(f"\nReport saved to: {REPORT_PATH}")
_report_file.close()
sys.stdout = sys.__stdout__
print(f"Done! Report saved to: {REPORT_PATH}")
