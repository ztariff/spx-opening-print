"""
QQQ Opening Print — Script 26: What Actually Works for QQQ?
============================================================
Instead of copying SPX logic, analyze QQQ from first principles.

Questions to answer:
1. Does the bullish first bar edge exist on QQQ? How strong?
2. What's the optimal PT/SL for QQQ specifically?
3. Which signals matter for QQQ vs SPX? (tech is different)
4. Does VIX matter the same way?
5. Does day of week matter differently?
6. What about QQQ-specific: NASDAQ breadth, mag7 momentum?
7. What hold time works best for QQQ 0DTE?
"""

import csv, json, os, math, sys, io
from datetime import datetime, timedelta
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(SCRIPT_DIR, "qqq_analysis_report.txt")

# Tee stdout to both console and file
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

print("Loading data...")
qqq_daily, qqq_dates = load_daily(os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv"))
qqq_intraday = load_intraday(os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv"))
vix_daily, _ = load_daily(os.path.join(SCRIPT_DIR, "vix_daily_bars.csv"))
tlt_daily, tlt_dates = load_daily(os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv"))
# Also load SPX for comparison
spx_daily, spx_dates = load_daily(os.path.join(SCRIPT_DIR, "spx_daily_bars.csv"))

intra_dates = sorted(qqq_intraday.keys())
intra_idx = {d: i for i, d in enumerate(intra_dates)}
print(f"QQQ intraday days: {len(intra_dates)}")

# ══════════════════════════════════════════════════════════════════════
# STEP 1: Basic first-bar edge on QQQ
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("STEP 1: DOES THE FIRST BAR EDGE EXIST ON QQQ?")
print("=" * 80)

# For each day, compute: first bar direction, then rest-of-day return
bullish_rets = []
bearish_rets = []
all_days_data = []

for d in intra_dates:
    bars = qqq_intraday[d]
    if len(bars) < 10: continue

    dt = datetime.strptime(d, "%Y-%m-%d")
    entry = bars[0]["open"]
    first_bar_bullish = bars[0]["close"] > bars[0]["open"]

    # Rest-of-day returns at various hold times
    rets = {}
    for target_min in [15, 30, 60, 120, 240, 390]:
        idx = min(target_min, len(bars) - 1)
        if idx > 0:
            ret_pct = (bars[idx]["close"] - entry) / entry * 100
            rets[target_min] = ret_pct

    # Max favorable excursion (how far did it go up from open?)
    max_up = max((b["high"] - entry) / entry * 100 for b in bars[1:]) if len(bars) > 1 else 0
    # Max adverse excursion
    max_down = min((b["low"] - entry) / entry * 100 for b in bars[1:]) if len(bars) > 1 else 0

    vix_open = vix_daily[d]["open"] if d in vix_daily else None

    day_data = {
        "date": d, "dow": dt.strftime("%A"), "entry": entry,
        "bullish": first_bar_bullish, "rets": rets,
        "max_up": max_up, "max_down": max_down,
        "vix": vix_open,
    }

    # Compute gap
    idx_d = intra_idx[d]
    if idx_d > 0:
        prev_d = intra_dates[idx_d - 1]
        prev_bars = qqq_intraday.get(prev_d, [])
        if prev_bars:
            prev_close = prev_bars[-1]["close"]
            day_data["gap_pct"] = (entry - prev_close) / prev_close * 100
            prev_open = prev_bars[0]["open"]
            day_data["prev_ret"] = (prev_close - prev_open) / prev_open * 100
            prev_high = max(b["high"] for b in prev_bars)
            prev_low = min(b["low"] for b in prev_bars)
            day_data["prev_range"] = (prev_high - prev_low) / prev_close * 100

    # MAs and RSI
    ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
    ma200 = compute_ma(qqq_daily, qqq_dates, d, 200)
    rsi = compute_rsi(qqq_daily, qqq_dates, d, 14)
    day_data["above_ma50"] = entry > ma50 if ma50 else None
    day_data["above_ma200"] = entry > ma200 if ma200 else None
    day_data["rsi"] = rsi

    all_days_data.append(day_data)

    if first_bar_bullish:
        bullish_rets.append(rets.get(390, 0))
    else:
        bearish_rets.append(rets.get(390, 0))

print(f"\n  Total trading days: {len(all_days_data)}")
print(f"  Bullish first bars: {len(bullish_rets)} ({len(bullish_rets)/len(all_days_data)*100:.1f}%)")
print(f"  Bearish first bars: {len(bearish_rets)} ({len(bearish_rets)/len(all_days_data)*100:.1f}%)")
print(f"\n  Bullish 1st bar -> EOD return: avg {mean(bullish_rets):.3f}%, stdev {stdev(bullish_rets):.3f}%")
print(f"  Bearish 1st bar -> EOD return: avg {mean(bearish_rets):.3f}%, stdev {stdev(bearish_rets):.3f}%")
print(f"  All days -> EOD return: avg {mean(bullish_rets + bearish_rets):.3f}%")

# Hold time analysis for bullish days
print(f"\n  Bullish first bar — return by hold time:")
for mins in [15, 30, 60, 120, 240, 390]:
    vals = [d["rets"].get(mins, 0) for d in all_days_data if d["bullish"]]
    wr = sum(1 for v in vals if v > 0) / len(vals) * 100
    print(f"    {mins:>3}min: avg {mean(vals):.3f}%, WR {wr:.1f}%, stdev {stdev(vals):.3f}%")

# ══════════════════════════════════════════════════════════════════════
# STEP 2: Optimal PT/SL for QQQ (percentage-based)
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("STEP 2: OPTIMAL PT/SL FOR QQQ (on bullish first bar days)")
print("=" * 80)

def sim_ptsl(days_data, pt_pct, sl_pct, ts_min, bullish_only=True):
    pnls = []
    for d in days_data:
        if bullish_only and not d["bullish"]: continue
        bars = qqq_intraday[d["date"]]
        entry = d["entry"]
        pt = entry * pt_pct / 100
        sl = entry * sl_pct / 100

        pnl_pct = 0
        for bi in range(1, min(ts_min + 1, len(bars))):
            bar = bars[bi]
            if bar["low"] <= entry - sl:
                pnl_pct = -sl_pct; break
            if bar["high"] >= entry + pt:
                pnl_pct = pt_pct; break
        else:
            idx = min(ts_min, len(bars) - 1)
            pnl_pct = (bars[idx]["close"] - entry) / entry * 100
        pnls.append(pnl_pct)

    if len(pnls) < 20: return None
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    sd = stdev(pnls) if len(pnls) > 1 else 1
    sharpe_raw = mean(pnls) / sd if sd > 0 else 0
    cum = peak = max_dd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {"pnls": pnls, "total": total, "wr": wr, "pf": pf,
            "sharpe_raw": sharpe_raw, "mean": mean(pnls), "stdev": sd,
            "max_dd_pct": max_dd, "n": len(pnls)}

print("\n  Testing PT/SL combinations (percentage of QQQ price):")
print(f"  {'PT%':>5s} {'SL%':>5s} {'TS':>4s} | {'N':>4s} {'WR':>5s} {'PF':>5s} {'Mean%':>7s} {'StDev%':>7s} {'Sharpe':>7s} {'Total%':>8s} {'MaxDD%':>7s}")
print("  " + "-" * 85)

best_sharpe = 0
best_config = None
configs = []

for pt_pct in [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5]:
    for sl_pct in [0.1, 0.15, 0.2, 0.3, 0.4, 0.5]:
        for ts in [30, 60, 120, 240, 390]:
            r = sim_ptsl(all_days_data, pt_pct, sl_pct, ts)
            if r:
                configs.append((r["sharpe_raw"], pt_pct, sl_pct, ts, r))

# Sort by Sharpe and show top 20
configs.sort(key=lambda x: x[0], reverse=True)
for i, (sh, pt, sl, ts, r) in enumerate(configs[:25]):
    marker = " <-- BEST" if i == 0 else ""
    print(f"  {pt:>5.2f} {sl:>5.2f} {ts:>4d} | {r['n']:>4d} {r['wr']:>5.1f} {r['pf']:>5.2f} "
          f"{r['mean']:>7.4f} {r['stdev']:>7.3f} {sh:>7.3f} {r['total']:>8.2f} {r['max_dd_pct']:>7.3f}{marker}")

best_sh, best_pt, best_sl, best_ts, best_r = configs[0]

# ══════════════════════════════════════════════════════════════════════
# STEP 3: Which filters matter for QQQ?
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("STEP 3: WHICH FILTERS MATTER FOR QQQ?")
print(f"  (Using best PT/SL from above: PT={best_pt}%, SL={best_sl}%, TS={best_ts})")
print("=" * 80)

def test_filter(label, filter_func, pt=best_pt, sl=best_sl, ts=best_ts):
    filtered = [d for d in all_days_data if filter_func(d)]
    r = sim_ptsl(filtered, pt, sl, ts, bullish_only=True)
    if not r: return None
    base = sim_ptsl(all_days_data, pt, sl, ts, bullish_only=True)
    delta = r["sharpe_raw"] - base["sharpe_raw"]
    sign = "+" if delta >= 0 else ""
    print(f"  {label:40s}: N={r['n']:>4d} WR={r['wr']:>5.1f}% PF={r['pf']:>5.2f} "
          f"Sharpe={r['sharpe_raw']:>6.3f} ({sign}{delta:.3f}) Total={r['total']:>7.2f}%")
    return r["sharpe_raw"]

print("\n  --- VIX Filters ---")
test_filter("No VIX filter (baseline)", lambda d: True)
test_filter("VIX >= 14", lambda d: d.get("vix") and d["vix"] >= 14)
test_filter("VIX >= 16", lambda d: d.get("vix") and d["vix"] >= 16)
test_filter("VIX >= 18", lambda d: d.get("vix") and d["vix"] >= 18)
test_filter("VIX >= 20", lambda d: d.get("vix") and d["vix"] >= 20)
test_filter("VIX <= 25", lambda d: d.get("vix") and d["vix"] <= 25)
test_filter("VIX <= 28", lambda d: d.get("vix") and d["vix"] <= 28)
test_filter("VIX <= 30", lambda d: d.get("vix") and d["vix"] <= 30)
test_filter("VIX 16-25", lambda d: d.get("vix") and 16 <= d["vix"] <= 25)
test_filter("VIX 16-28", lambda d: d.get("vix") and 16 <= d["vix"] <= 28)
test_filter("VIX 16-30", lambda d: d.get("vix") and 16 <= d["vix"] <= 30)
test_filter("VIX 18-25", lambda d: d.get("vix") and 18 <= d["vix"] <= 25)
test_filter("VIX 18-28", lambda d: d.get("vix") and 18 <= d["vix"] <= 28)
test_filter("VIX 14-25", lambda d: d.get("vix") and 14 <= d["vix"] <= 25)
test_filter("VIX 14-28", lambda d: d.get("vix") and 14 <= d["vix"] <= 28)

print("\n  --- MA Filters ---")
test_filter("Above SMA50", lambda d: d.get("above_ma50") == True)
test_filter("Below SMA50", lambda d: d.get("above_ma50") == False)
test_filter("Above SMA200", lambda d: d.get("above_ma200") == True)
test_filter("Below SMA200", lambda d: d.get("above_ma200") == False)

print("\n  --- RSI Filters ---")
test_filter("RSI 30-70", lambda d: d.get("rsi") and 30 <= d["rsi"] <= 70)
test_filter("RSI 35-65", lambda d: d.get("rsi") and 35 <= d["rsi"] <= 65)
test_filter("RSI 35-70", lambda d: d.get("rsi") and 35 <= d["rsi"] <= 70)
test_filter("RSI 40-60", lambda d: d.get("rsi") and 40 <= d["rsi"] <= 60)
test_filter("RSI 40-70", lambda d: d.get("rsi") and 40 <= d["rsi"] <= 70)
test_filter("RSI > 50", lambda d: d.get("rsi") and d["rsi"] > 50)
test_filter("RSI 50-70", lambda d: d.get("rsi") and 50 <= d["rsi"] <= 70)

print("\n  --- Gap Filters ---")
test_filter("Gap up", lambda d: d.get("gap_pct", 0) > 0)
test_filter("Gap down", lambda d: d.get("gap_pct", 0) < 0)
test_filter("Gap up > 0.1%", lambda d: d.get("gap_pct", 0) > 0.1)
test_filter("Gap up > 0.3%", lambda d: d.get("gap_pct", 0) > 0.3)
test_filter("Small gap (< 0.3%)", lambda d: abs(d.get("gap_pct", 0)) < 0.3)
test_filter("No large gap down (> -0.5%)", lambda d: d.get("gap_pct", 0) > -0.5)

print("\n  --- Day of Week ---")
for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
    test_filter(f"{day}", lambda d, day=day: d["dow"] == day)
test_filter("Exclude Tuesday", lambda d: d["dow"] != "Tuesday")
test_filter("Exclude Thursday", lambda d: d["dow"] != "Thursday")
test_filter("Exclude Tue+Thu", lambda d: d["dow"] not in ("Tuesday", "Thursday"))
test_filter("Mon+Wed+Fri only", lambda d: d["dow"] in ("Monday", "Wednesday", "Friday"))

print("\n  --- Prior Day ---")
test_filter("Prior day green", lambda d: d.get("prev_ret", 0) > 0)
test_filter("Prior day red", lambda d: d.get("prev_ret", 0) < 0)
test_filter("Prior day big red (<-1%)", lambda d: d.get("prev_ret", 0) < -1)
test_filter("Prior range > 1.5%", lambda d: d.get("prev_range", 0) > 1.5)
test_filter("Prior range > 2%", lambda d: d.get("prev_range", 0) > 2)

print("\n  --- Max Excursion (how far did QQQ go up/down from open?) ---")
test_filter("Max up potential > 0.5%", lambda d: d.get("max_up", 0) > 0.5)
test_filter("Max down risk < -0.5%", lambda d: d.get("max_down", 0) > -0.5)

# ══════════════════════════════════════════════════════════════════════
# STEP 4: Combined best filters
# ══════════════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("STEP 4: COMBINED FILTERS")
print("=" * 80)

combos = [
    ("Above SMA50 + VIX 16-30", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 30),
    ("Above SMA50 + VIX 16-28", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 28),
    ("Above SMA50 + VIX 14-28", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 28),
    ("Above SMA50 + VIX 14-25", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 25),
    ("Above SMA50 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("Above SMA50 + RSI 40-70", lambda d: d.get("above_ma50") == True and d.get("rsi") and 40 <= d["rsi"] <= 70),
    ("Above SMA50 + RSI 50-70", lambda d: d.get("above_ma50") == True and d.get("rsi") and 50 <= d["rsi"] <= 70),
    ("SMA50 + VIX 16-28 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 28 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("SMA50 + VIX 14-28 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 28 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("SMA50 + VIX 16-30 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 30 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("SMA50 + VIX 14-25 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 25 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("SMA50 + VIX 16-28 + RSI 40-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 28 and d.get("rsi") and 40 <= d["rsi"] <= 70),
    ("SMA50 + VIX 14-28 + no Thu", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 28 and d["dow"] != "Thursday"),
    ("SMA50 + VIX 14-28 + no Tue/Thu", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 28 and d["dow"] not in ("Tuesday", "Thursday")),
    ("SMA50 + RSI 35-70 + gap up", lambda d: d.get("above_ma50") == True and d.get("rsi") and 35 <= d["rsi"] <= 70 and d.get("gap_pct", 0) > 0),
    ("SMA50 + RSI 35-70 + no large gap dn", lambda d: d.get("above_ma50") == True and d.get("rsi") and 35 <= d["rsi"] <= 70 and d.get("gap_pct", 0) > -0.5),
    ("Above SMA200 + VIX 14-28", lambda d: d.get("above_ma200") == True and d.get("vix") and 14 <= d["vix"] <= 28),
    ("Above SMA200 + RSI 35-70", lambda d: d.get("above_ma200") == True and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ("SMA50 + VIX 14-28 + RSI 35-70 + no Thu", lambda d: d.get("above_ma50") == True and d.get("vix") and 14 <= d["vix"] <= 28 and d.get("rsi") and 35 <= d["rsi"] <= 70 and d["dow"] != "Thursday"),
]

combo_results = []
for label, filt in combos:
    filtered = [d for d in all_days_data if filt(d)]
    r = sim_ptsl(filtered, best_pt, best_sl, best_ts, bullish_only=True)
    if r:
        base = sim_ptsl(all_days_data, best_pt, best_sl, best_ts, bullish_only=True)
        delta = r["sharpe_raw"] - base["sharpe_raw"]
        sign = "+" if delta >= 0 else ""
        print(f"  {label:45s}: N={r['n']:>4d} WR={r['wr']:>5.1f}% PF={r['pf']:>5.2f} "
              f"Sharpe={r['sharpe_raw']:>6.3f} ({sign}{delta:.3f}) Total={r['total']:>7.2f}%")
        combo_results.append((r["sharpe_raw"], r["total"], r["n"], r["wr"], r["pf"], r["max_dd_pct"], label))

print("\n" + "=" * 80)
print("TOP 10 COMBINED FILTERS BY SHARPE")
print("=" * 80)
combo_results.sort(key=lambda x: x[0], reverse=True)
for i, (sh, total, n, wr, pf, dd, label) in enumerate(combo_results[:10]):
    print(f"  #{i+1}: Sharpe {sh:.3f} | {label}")
    print(f"       N={n} WR={wr:.1f}% PF={pf:.2f} Total={total:.2f}% MaxDD={dd:.3f}%")

print(f"\nReport saved to: {REPORT_PATH}")
_report_file.close()
sys.stdout = sys.__stdout__
