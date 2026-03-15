"""
SPX Opening Print — Script 32: Comprehensive Edge Finder
==========================================================
Same approach as script 28 (QQQ edge finder) but for SPX.
Tests everything from first principles to find new/better edge.

Current SPX strategy: 331 trades, bullish 1st bar only, signal/score system,
6 PT/SL/TS combos, $6.1M P&L.

Questions to answer:
1. Does a trailing stop work on SPX (like QQQ)?
2. Is first bar SIZE a signal (not just direction)?
3. Can we trade bearish first bars profitably?
4. What's the optimal entry timing?
5. Can we find high-frequency scalp opportunities?
6. Are there days the current strategy skips that have edge?
7. Does mean reversion work on SPX?

Usage:
    python3 32_spx_edge_finder.py
"""

import os, csv, json, math, sys
from collections import defaultdict
from statistics import mean, stdev, median
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
VIX_1MIN = os.path.join(SCRIPT_DIR, "vix_1min_bars.csv")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "spx_edge_finder_report.txt")


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


# ── Helpers ───────────────────────────────────────────────────────────

def compute_ma(daily, dates, target, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target:
            idx = i
            break
    if idx is None or idx < period:
        return None
    vals = [daily[dates[j]]["close"] for j in range(idx - period, idx) if dates[j] in daily]
    return mean(vals) if len(vals) == period else None


def sharpe(pnls, trades_per_year=None):
    if len(pnls) < 2 or stdev(pnls) == 0:
        return 0
    if trades_per_year is None:
        trades_per_year = len(pnls)
    return (mean(pnls) / stdev(pnls)) * math.sqrt(trades_per_year)


def max_dd(pnls):
    cum = peak = dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def stats_line(pnls, label="", pts=False):
    if not pnls:
        return f"  {label:40s} N=0"
    n = len(pnls)
    total = sum(pnls)
    w = sum(1 for p in pnls if p > 0)
    wr = w / n * 100
    avg = mean(pnls)
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    dd = max_dd(pnls)
    sh = sharpe(pnls)
    unit = "pts" if pts else "$"
    return f"  {label:40s} N={n:>4d}  P&L={total:>+10,.1f}{unit}  WR={wr:>5.1f}%  PF={pf:>5.2f}  Sharpe={sh:>6.3f}  MaxDD={dd:>10,.1f}{unit}"


def simulate_trade(bars, entry_idx, entry_price, pt_pts, sl_pts, ts_bars,
                   trail_pct=None, direction=1):
    """Simulate a linear SPX trade.
    direction: 1=long, -1=short
    Returns (pnl_pts, exit_reason, exit_idx, exit_bar)
    """
    peak = entry_price
    trough = entry_price

    for i in range(entry_idx + 1, min(entry_idx + ts_bars + 1, len(bars))):
        bar = bars[i]

        if direction == 1:
            # Long
            if bar["high"] > peak:
                peak = bar["high"]
            if bar["low"] > peak:
                peak = bar["high"]

            # PT
            if pt_pts > 0 and bar["high"] >= entry_price + pt_pts:
                return (pt_pts, "PT", i, bar)

            # SL
            if sl_pts > 0 and bar["low"] <= entry_price - sl_pts:
                return (-sl_pts, "SL", i, bar)

            # Trailing stop (pct of entry)
            if trail_pct is not None and peak > entry_price:
                trail_level = peak * (1 - trail_pct / 100)
                if bar["low"] <= trail_level:
                    pnl = (trail_level - entry_price)
                    return (pnl, "Trail", i, bar)
        else:
            # Short
            if bar["low"] < trough:
                trough = bar["low"]

            if pt_pts > 0 and bar["low"] <= entry_price - pt_pts:
                return (pt_pts, "PT", i, bar)

            if sl_pts > 0 and bar["high"] >= entry_price + sl_pts:
                return (-sl_pts, "SL", i, bar)

            if trail_pct is not None and trough < entry_price:
                trail_level = trough * (1 + trail_pct / 100)
                if bar["high"] >= trail_level:
                    pnl = (entry_price - trail_level)
                    return (pnl, "Trail", i, bar)

    # Time stop
    last_idx = min(entry_idx + ts_bars, len(bars) - 1)
    last_bar = bars[last_idx]
    pnl = (last_bar["close"] - entry_price) * direction
    return (pnl, "TS", last_idx, last_bar)


def build_day_features(d, bars, spx_daily, spx_dates, vix_daily, vix_dates, vix_1min):
    """Build feature dict for a trading day."""
    if len(bars) < 5:
        return None

    entry_open = bars[0]["open"]
    fb = bars[0]  # first bar 9:30
    fb_ret = (fb["close"] - fb["open"]) / fb["open"] * 100
    fb_range = (fb["high"] - fb["low"]) / fb["open"] * 100
    fb_bullish = fb["close"] > fb["open"]

    # 5-min bar
    five_min_bars = bars[:5]
    bar5_close = five_min_bars[-1]["close"]
    bar5_ret = (bar5_close - entry_open) / entry_open * 100

    # Day of week
    dt = datetime.strptime(d, "%Y-%m-%d")
    dow = dt.strftime("%A")

    # Prior day
    idx = None
    for i, dd in enumerate(spx_dates):
        if dd == d:
            idx = i
            break
    if idx is None or idx < 50:
        return None

    prev_d = spx_dates[idx - 1]
    if prev_d not in spx_daily:
        return None
    prev = spx_daily[prev_d]
    prev_ret = (prev["close"] - prev["open"]) / prev["open"] * 100
    prev_range = (prev["high"] - prev["low"]) / prev["open"] * 100

    # Gap
    gap_pts = entry_open - prev["close"]
    gap_pct = gap_pts / prev["close"] * 100

    # VIX
    vix = None
    if d in vix_daily:
        vix = vix_daily[d]["open"]
    elif vix_1min and d in vix_1min and vix_1min[d]:
        vix = vix_1min[d][0]["open"]

    # MAs
    ma20 = compute_ma(spx_daily, spx_dates, d, 20)
    ma50 = compute_ma(spx_daily, spx_dates, d, 50)

    # Prior range position
    if prev["high"] != prev["low"]:
        open_in_range = (entry_open - prev["low"]) / (prev["high"] - prev["low"])
    else:
        open_in_range = 0.5

    # 2-day and 5-day prior returns
    prev2_ret = None
    if idx >= 2 and spx_dates[idx - 2] in spx_daily:
        p2 = spx_daily[spx_dates[idx - 2]]
        prev2_ret = (prev["close"] - p2["open"]) / p2["open"] * 100

    prev5_ret = None
    if idx >= 5 and spx_dates[idx - 5] in spx_daily:
        p5 = spx_daily[spx_dates[idx - 5]]
        prev5_ret = (prev["close"] - p5["open"]) / p5["open"] * 100

    return {
        "date": d,
        "entry_open": entry_open,
        "fb_ret": fb_ret,
        "fb_range": fb_range,
        "fb_bullish": fb_bullish,
        "bar5_ret": bar5_ret,
        "dow": dow,
        "prev_ret": prev_ret,
        "prev_range": prev_range,
        "gap_pts": gap_pts,
        "gap_pct": gap_pct,
        "vix": vix,
        "ma20": ma20,
        "ma50": ma50,
        "open_in_range": open_in_range,
        "prev2_ret": prev2_ret,
        "prev5_ret": prev5_ret,
        "bars": bars,
    }


def main():
    log_f = open(OUTPUT_REPORT, "w")
    tee = Tee(sys.stdout, log_f)
    old_stdout = sys.stdout
    sys.stdout = tee

    print("=" * 90)
    print("SPX Opening Print — Comprehensive Edge Finder")
    print("=" * 90)

    print("\nLoading data...")
    intraday = load_intraday()
    spx_daily, spx_dates = load_daily(SPX_DAILY)
    vix_daily, vix_dates = load_daily(VIX_DAILY)
    vix_1min = load_vix_1min()
    print(f"  SPX intraday: {len(intraday)} days")
    print(f"  SPX daily: {len(spx_daily)} days")
    print(f"  VIX daily: {len(vix_daily)} days")

    # Build features for all days
    print("\nBuilding day features...")
    days = {}
    for d in sorted(intraday.keys()):
        feat = build_day_features(d, intraday[d], spx_daily, spx_dates, vix_daily, vix_dates, vix_1min)
        if feat:
            days[d] = feat
    print(f"  {len(days)} tradeable days")
    bull_days = {d: f for d, f in days.items() if f["fb_bullish"]}
    bear_days = {d: f for d, f in days.items() if not f["fb_bullish"]}
    print(f"  Bullish 1st bar: {len(bull_days)}  Bearish: {len(bear_days)}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 1: Direction and bar selection
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 1: DIRECTION & BAR SELECTION")
    print(f"{'='*90}")
    print("Testing: bullish buy, bearish buy, bearish sell, all buy, 5-min bar buy")
    print("Baseline: buy at 9:30 open, PT=5pts SL=3pts TS=30 bars\n")

    for label, day_set, direction in [
        ("All days LONG", days, 1),
        ("Bullish 1st bar LONG", bull_days, 1),
        ("Bearish 1st bar LONG", bear_days, 1),
        ("Bearish 1st bar SHORT", bear_days, -1),
        ("All days SHORT", days, -1),
    ]:
        pnls = []
        for d, f in sorted(day_set.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], 5, 3, 30, direction=direction)
            pnls.append(pnl)
        print(stats_line(pnls, label, pts=True))

    # With 5-min bar entry
    pnls_5m = []
    for d, f in sorted(bull_days.items()):
        if f["bar5_ret"] > 0 and len(f["bars"]) > 5:
            entry = f["bars"][4]["close"]
            pnl, _, _, _ = simulate_trade(f["bars"], 4, entry, 5, 3, 30)
            pnls_5m.append(pnl)
    print(stats_line(pnls_5m, "Bullish 5m bar LONG (enter at bar 5)", pts=True))

    # ══════════════════════════════════════════════════════════════════
    # TEST 2: PT/SL grid (SPX points, not pct)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 2: PT/SL GRID (bullish 1st bar, SPX points)")
    print(f"{'='*90}")
    print("Testing PT from 2-50pts, SL from 2-30pts, TS=30/120/240/390 bars\n")

    grid_results = []
    for pt in [2, 3, 5, 8, 10, 15, 20, 30, 50]:
        for sl in [2, 3, 5, 8, 10, 15, 20, 30]:
            for ts in [30, 120, 240, 390]:
                pnls = []
                for d, f in sorted(bull_days.items()):
                    pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], pt, sl, ts)
                    pnls.append(pnl)
                if pnls:
                    sh = sharpe(pnls)
                    grid_results.append((pt, sl, ts, sh, sum(pnls), len(pnls)))

    grid_results.sort(key=lambda x: -x[3])
    print(f"  {'PT':>4s} {'SL':>4s} {'TS':>4s}  {'Sharpe':>7s}  {'Total':>10s}  {'N':>4s}")
    for pt, sl, ts, sh, total, n in grid_results[:25]:
        print(f"  {pt:>4d} {sl:>4d} {ts:>4d}  {sh:>7.3f}  {total:>+10,.1f}  {n:>4d}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 3: Entry timing — does edge persist past bar 0?
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 3: ENTRY TIMING")
    print(f"{'='*90}")
    print("Testing entry at bar 0 (9:30), bar 1 (9:31), bar 2, bar 5, bar 10, bar 30\n")

    for entry_bar_idx in [0, 1, 2, 5, 10, 30]:
        pnls = []
        for d, f in sorted(bull_days.items()):
            if entry_bar_idx >= len(f["bars"]):
                continue
            entry = f["bars"][entry_bar_idx]["close"]
            pnl, _, _, _ = simulate_trade(f["bars"], entry_bar_idx, entry, 5, 3, 30)
            pnls.append(pnl)
        bar_time = f"09:{30 + entry_bar_idx:02d}" if entry_bar_idx < 30 else f"10:{entry_bar_idx - 30:02d}"
        print(stats_line(pnls, f"Entry bar {entry_bar_idx} (~{bar_time})", pts=True))

    # ══════════════════════════════════════════════════════════════════
    # TEST 4: TRAILING STOPS (like QQQ)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 4: TRAILING STOPS (SPX, bullish 1st bar)")
    print(f"{'='*90}")
    print("Testing trail_pct + hard SL combos, various time stops\n")

    trail_results = []
    for trail_pct in [0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50]:
        for sl_pts in [3, 5, 8, 10, 15, 20, 30]:
            for ts in [15, 30, 60, 120, 240]:
                pnls = []
                for d, f in sorted(bull_days.items()):
                    pnl, reason, _, _ = simulate_trade(
                        f["bars"], 0, f["entry_open"],
                        pt_pts=0, sl_pts=sl_pts, ts_bars=ts,
                        trail_pct=trail_pct
                    )
                    pnls.append(pnl)
                if pnls:
                    sh = sharpe(pnls)
                    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                    trail_results.append((trail_pct, sl_pts, ts, sh, sum(pnls), wr, len(pnls)))

    trail_results.sort(key=lambda x: -x[3])
    print(f"  {'Trail%':>7s} {'SL':>4s} {'TS':>4s}  {'Sharpe':>7s}  {'Total':>10s}  {'WR':>5s}  {'N':>4s}")
    for trail, sl, ts, sh, total, wr, n in trail_results[:30]:
        print(f"  {trail:>7.2f} {sl:>4d} {ts:>4d}  {sh:>7.3f}  {total:>+10,.1f}  {wr:>5.1f}  {n:>4d}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 5: FIRST BAR SIZE (like QQQ's >0.05% threshold)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 5: FIRST BAR SIZE (bullish bars only)")
    print(f"{'='*90}")
    print("Does the SIZE of the first bar predict edge?\n")

    # Using best PT/SL from grid
    best_pt, best_sl, best_ts = grid_results[0][0], grid_results[0][1], grid_results[0][2]
    print(f"  Using best grid exit: PT={best_pt} SL={best_sl} TS={best_ts}")
    # Also test with trailing stop
    best_trail = trail_results[0][0]
    best_trail_sl = trail_results[0][1]
    best_trail_ts = trail_results[0][2]
    print(f"  Also with best trail: trail={best_trail}% SL={best_trail_sl} TS={best_trail_ts}\n")

    # SPX first bar return in percent
    fb_rets = sorted([f["fb_ret"] for d, f in bull_days.items()])
    p25 = fb_rets[len(fb_rets) // 4]
    p50 = fb_rets[len(fb_rets) // 2]
    p75 = fb_rets[3 * len(fb_rets) // 4]
    print(f"  First bar return distribution (bullish): p25={p25:.3f}% p50={p50:.3f}% p75={p75:.3f}%")
    print(f"  In SPX points (at 5000): p25={5000*p25/100:.1f} p50={5000*p50/100:.1f} p75={5000*p75/100:.1f}\n")

    for threshold_pct in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50]:
        filtered = {d: f for d, f in bull_days.items() if f["fb_ret"] > threshold_pct}
        if not filtered:
            continue

        # Fixed exit
        pnls_fixed = []
        for d, f in sorted(filtered.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], best_pt, best_sl, best_ts)
            pnls_fixed.append(pnl)

        # Trailing exit
        pnls_trail = []
        for d, f in sorted(filtered.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                          pt_pts=0, sl_pts=best_trail_sl, ts_bars=best_trail_ts,
                                          trail_pct=best_trail)
            pnls_trail.append(pnl)

        print(f"  fb_ret > {threshold_pct:.2f}%:")
        print(stats_line(pnls_fixed, f"  Fixed PT/SL", pts=True))
        print(stats_line(pnls_trail, f"  Trailing stop", pts=True))

    # ══════════════════════════════════════════════════════════════════
    # TEST 6: FILTERS (VIX, gap, prior day, MA, DOW, range position)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 6: INDIVIDUAL FILTERS (bullish 1st bar)")
    print(f"{'='*90}")
    print(f"  Using best trail: trail={best_trail}% SL={best_trail_sl} TS={best_trail_ts}\n")

    def test_filter(label, day_subset):
        pnls = []
        for d, f in sorted(day_subset.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                          pt_pts=0, sl_pts=best_trail_sl, ts_bars=best_trail_ts,
                                          trail_pct=best_trail)
            pnls.append(pnl)
        print(stats_line(pnls, label, pts=True))

    test_filter("ALL bullish days", bull_days)

    # VIX
    for thresh in [15, 18, 20, 25, 30]:
        hi = {d: f for d, f in bull_days.items() if f["vix"] and f["vix"] >= thresh}
        lo = {d: f for d, f in bull_days.items() if f["vix"] and f["vix"] < thresh}
        test_filter(f"VIX >= {thresh}", hi)
        test_filter(f"VIX < {thresh}", lo)

    # Gap
    test_filter("Gap up (>0)", {d: f for d, f in bull_days.items() if f["gap_pct"] > 0})
    test_filter("Gap down (<0)", {d: f for d, f in bull_days.items() if f["gap_pct"] < 0})
    test_filter("Big gap up (>0.3%)", {d: f for d, f in bull_days.items() if f["gap_pct"] > 0.3})
    test_filter("Big gap down (<-0.3%)", {d: f for d, f in bull_days.items() if f["gap_pct"] < -0.3})

    # Prior day
    test_filter("Prior green", {d: f for d, f in bull_days.items() if f["prev_ret"] > 0})
    test_filter("Prior red", {d: f for d, f in bull_days.items() if f["prev_ret"] < 0})
    test_filter("Prior big red (<-1%)", {d: f for d, f in bull_days.items() if f["prev_ret"] < -1})
    test_filter("Prior big green (>1%)", {d: f for d, f in bull_days.items() if f["prev_ret"] > 1})

    # 2-day and 5-day momentum
    test_filter("2d ret > 0", {d: f for d, f in bull_days.items() if f["prev2_ret"] and f["prev2_ret"] > 0})
    test_filter("2d ret < 0", {d: f for d, f in bull_days.items() if f["prev2_ret"] and f["prev2_ret"] < 0})
    test_filter("5d ret > 0", {d: f for d, f in bull_days.items() if f["prev5_ret"] and f["prev5_ret"] > 0})
    test_filter("5d ret < 0", {d: f for d, f in bull_days.items() if f["prev5_ret"] and f["prev5_ret"] < 0})

    # MA position
    test_filter("Above MA50", {d: f for d, f in bull_days.items() if f["ma50"] and f["entry_open"] > f["ma50"]})
    test_filter("Below MA50", {d: f for d, f in bull_days.items() if f["ma50"] and f["entry_open"] < f["ma50"]})
    test_filter("Above MA20", {d: f for d, f in bull_days.items() if f["ma20"] and f["entry_open"] > f["ma20"]})
    test_filter("Below MA20", {d: f for d, f in bull_days.items() if f["ma20"] and f["entry_open"] < f["ma20"]})

    # DOW
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        test_filter(day, {d: f for d, f in bull_days.items() if f["dow"] == day})

    # Range position
    test_filter("Open in bottom 25% prior range", {d: f for d, f in bull_days.items() if f["open_in_range"] < 0.25})
    test_filter("Open in top 25% prior range", {d: f for d, f in bull_days.items() if f["open_in_range"] > 0.75})
    test_filter("Open in middle 50% prior range", {d: f for d, f in bull_days.items() if 0.25 <= f["open_in_range"] <= 0.75})

    # ══════════════════════════════════════════════════════════════════
    # TEST 7: BEARISH FIRST BAR — BUY THE DIP (mean reversion)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 7: BEARISH FIRST BAR STRATEGIES")
    print(f"{'='*90}")
    print("Is there a mean-reversion play after bearish first bars?\n")

    # Test buying after bearish first bar
    for pt in [2, 3, 5, 8, 10]:
        for sl in [3, 5, 8, 10, 15]:
            for ts in [15, 30, 60, 120]:
                pnls = []
                for d, f in sorted(bear_days.items()):
                    # Enter at bar 1 close (after seeing bearish bar)
                    if len(f["bars"]) < 3:
                        continue
                    entry = f["bars"][1]["close"]
                    pnl, _, _, _ = simulate_trade(f["bars"], 1, entry, pt, sl, ts)
                    pnls.append(pnl)
                if pnls and sharpe(pnls) > 0.3:
                    print(stats_line(pnls, f"Bear→Buy bar1 PT={pt} SL={sl} TS={ts}", pts=True))

    # Bearish first bar + sell
    print("\n  Selling after bearish first bar:")
    for pt in [2, 3, 5, 8]:
        for sl in [3, 5, 8, 10]:
            pnls = []
            for d, f in sorted(bear_days.items()):
                pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], pt, sl, 30, direction=-1)
                pnls.append(pnl)
            if pnls and sharpe(pnls) > 0.3:
                print(stats_line(pnls, f"Bear→Sell PT={pt} SL={sl} TS=30", pts=True))

    # ══════════════════════════════════════════════════════════════════
    # TEST 8: SCALP STRATEGIES (very tight stops, fast exits)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 8: SCALP STRATEGIES (tight stops, fast exits)")
    print(f"{'='*90}")
    print("Testing very tight PT/SL with short time stops\n")

    scalp_results = []
    for pt in [1, 2, 3, 5]:
        for sl in [1, 2, 3, 5]:
            for ts in [5, 10, 15]:
                pnls = []
                for d, f in sorted(bull_days.items()):
                    pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], pt, sl, ts)
                    pnls.append(pnl)
                if pnls:
                    sh = sharpe(pnls)
                    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                    scalp_results.append((pt, sl, ts, sh, sum(pnls), wr))

    scalp_results.sort(key=lambda x: -x[3])
    print(f"  {'PT':>4s} {'SL':>4s} {'TS':>4s}  {'Sharpe':>7s}  {'Total':>10s}  {'WR':>5s}")
    for pt, sl, ts, sh, total, wr in scalp_results[:20]:
        print(f"  {pt:>4d} {sl:>4d} {ts:>4d}  {sh:>7.3f}  {total:>+10,.1f}  {wr:>5.1f}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 9: COMBINED BEST FILTERS
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 9: COMBINED FILTERS (best from above)")
    print(f"{'='*90}")
    print("Testing combinations of top-performing filters\n")

    # Define filter combos to test
    def apply_filters(day_set, filters):
        result = dict(day_set)
        for name, fn in filters:
            result = {d: f for d, f in result.items() if fn(f)}
        return result

    filter_defs = [
        ("fb>0.05%", lambda f: f["fb_ret"] > 0.05),
        ("fb>0.10%", lambda f: f["fb_ret"] > 0.10),
        ("fb>0.20%", lambda f: f["fb_ret"] > 0.20),
        ("VIX>=18", lambda f: f["vix"] and f["vix"] >= 18),
        ("VIX>=25", lambda f: f["vix"] and f["vix"] >= 25),
        ("VIX<20", lambda f: f["vix"] and f["vix"] < 20),
        ("gap_dn", lambda f: f["gap_pct"] < 0),
        ("gap_up", lambda f: f["gap_pct"] > 0),
        ("prev_red", lambda f: f["prev_ret"] < 0),
        ("prev_green", lambda f: f["prev_ret"] > 0),
        ("below_MA50", lambda f: f["ma50"] and f["entry_open"] < f["ma50"]),
        ("above_MA50", lambda f: f["ma50"] and f["entry_open"] > f["ma50"]),
        ("5d_down", lambda f: f["prev5_ret"] and f["prev5_ret"] < 0),
        ("Mon", lambda f: f["dow"] == "Monday"),
    ]

    # Test pairs
    combo_results = []
    for i in range(len(filter_defs)):
        for j in range(i + 1, len(filter_defs)):
            filters = [filter_defs[i], filter_defs[j]]
            names = [filter_defs[i][0], filter_defs[j][0]]
            filtered = apply_filters(bull_days, [(n, fn) for n, fn in filters])
            if len(filtered) < 30:
                continue

            # Test with trailing stop
            pnls = []
            for d, f in sorted(filtered.items()):
                pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                              pt_pts=0, sl_pts=best_trail_sl, ts_bars=best_trail_ts,
                                              trail_pct=best_trail)
                pnls.append(pnl)
            if pnls:
                sh = sharpe(pnls)
                combo_results.append((" + ".join(names), sh, sum(pnls), len(pnls)))

            # Test with best fixed
            pnls2 = []
            for d, f in sorted(filtered.items()):
                pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], best_pt, best_sl, best_ts)
                pnls2.append(pnl)
            if pnls2:
                sh2 = sharpe(pnls2)
                combo_results.append((" + ".join(names) + " [fixed]", sh2, sum(pnls2), len(pnls2)))

    combo_results.sort(key=lambda x: -x[1])
    print(f"  {'Combo':55s} {'Sharpe':>7s} {'Total':>10s} {'N':>4s}")
    for name, sh, total, n in combo_results[:30]:
        print(f"  {name:55s} {sh:>7.3f} {total:>+10,.1f} {n:>4d}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 10: SPX as QQQ-style scalp (trailing stop only, no PT)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 10: SPX AS QQQ-STYLE SCALP")
    print(f"{'='*90}")
    print("Testing QQQ-identical setup: big bullish bar + tight trail\n")

    for fb_thresh in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20]:
        for trail in [0.02, 0.03, 0.05, 0.08, 0.10]:
            for sl in [3, 5, 8, 10, 15]:
                filtered = {d: f for d, f in bull_days.items() if f["fb_ret"] > fb_thresh}
                if len(filtered) < 50:
                    continue
                pnls = []
                for d, f in sorted(filtered.items()):
                    pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                                  pt_pts=0, sl_pts=sl, ts_bars=30,
                                                  trail_pct=trail)
                    pnls.append(pnl)
                if pnls:
                    sh = sharpe(pnls)
                    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                    if sh > 0.5:
                        print(f"  fb>{fb_thresh:.2f}% trail={trail:.2f}% SL={sl}pts TS=30  Sharpe={sh:.3f}  P&L={sum(pnls):>+10,.1f}  WR={wr:.1f}%  N={len(pnls)}")

    # ══════════════════════════════════════════════════════════════════
    # TEST 11: CURRENT STRATEGY REFINEMENT
    # ══════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*90}")
    print("TEST 11: CAN WE IMPROVE THE EXISTING SPX STRATEGY?")
    print(f"{'='*90}")
    print("The current strategy uses 6 PT/SL/TS combos based on score.")
    print("Testing: what if we used trailing stops + the existing signal system?\n")

    # Load actual SPX trades to see which days are traded
    spx_trades_file = os.path.join(SCRIPT_DIR, "options_trades.json")
    with open(spx_trades_file) as f:
        spx_trades = json.load(f)
    spx_trade_dates = set(t["date"] for t in spx_trades)
    spx_traded = {d: f for d, f in days.items() if d in spx_trade_dates}
    spx_not_traded = {d: f for d, f in bull_days.items() if d not in spx_trade_dates}

    print(f"  Current strategy trades: {len(spx_traded)} days")
    print(f"  Bullish days NOT traded: {len(spx_not_traded)} days\n")

    # Test trail on currently-traded days
    print("  Trailing stop on CURRENTLY TRADED days:")
    for trail in [0.05, 0.08, 0.10, 0.15, 0.20, 0.30]:
        for sl in [5, 10, 15, 20]:
            for ts in [30, 120, 240, 390]:
                pnls = []
                for d, f in sorted(spx_traded.items()):
                    pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                                  pt_pts=0, sl_pts=sl, ts_bars=ts,
                                                  trail_pct=trail)
                    pnls.append(pnl)
                if pnls and sharpe(pnls) > 0.5:
                    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                    print(f"    trail={trail:.2f}% SL={sl} TS={ts}  Sharpe={sharpe(pnls):.3f}  P&L={sum(pnls):>+10,.1f}  WR={wr:.1f}%  N={len(pnls)}")

    # Test on NOT-traded bullish days (missed opportunities?)
    print(f"\n  Edge on MISSED bullish days ({len(spx_not_traded)}):")
    for pt, sl, ts in [(5, 3, 30), (10, 5, 60), (20, 10, 120), (50, 10, 240)]:
        pnls = []
        for d, f in sorted(spx_not_traded.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"], pt, sl, ts)
            pnls.append(pnl)
        print(stats_line(pnls, f"PT={pt} SL={sl} TS={ts}", pts=True))

    for trail in [0.05, 0.10, 0.20]:
        pnls = []
        for d, f in sorted(spx_not_traded.items()):
            pnl, _, _, _ = simulate_trade(f["bars"], 0, f["entry_open"],
                                          pt_pts=0, sl_pts=10, ts_bars=120,
                                          trail_pct=trail)
            pnls.append(pnl)
        print(stats_line(pnls, f"Trail={trail}% SL=10 TS=120 (missed days)", pts=True))

    print(f"\n\n{'='*90}")
    print("SCAN COMPLETE")
    print(f"{'='*90}")

    sys.stdout = old_stdout
    log_f.close()
    print(f"\nDone! Report saved to {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
