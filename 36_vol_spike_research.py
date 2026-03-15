"""
SPX Opening Print — Script 36: Intraday Vol Spike Mean Reversion Research
==========================================================================
Hypothesis: On non-crash days, sharp intraday SPX drops create vol spikes
where puts become expensive. Buying calls (or the dip) on these spikes
should yield mean-reversion profits.

Approach:
1. Load SPY 10s bars (scaled to SPX) for full intraday resolution
2. Detect "vol spikes" — sharp drops from rolling highs within a time window
3. Test mean reversion: buy ATM CALL at the spike, exit on bounce/trail/time
4. Sweep parameters: drop threshold, lookback window, hold time, exits
5. Filter by regime: VIX level, day type (trend vs chop), time of day

Uses cached SPY 10s bars + SPX 1min bars. No new API calls needed for
the underlying research. Option pricing simulated from underlying moves.

Usage:
    python3 36_vol_spike_research.py
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
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "vol_spike_research_report.txt")

# ── Tee output ──

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


# ── Data Loading ──

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
    """Load SPX daily bars."""
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
    """Load VIX 1-min bars by date for intraday VIX levels."""
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


def load_spy_10s(date):
    """Load cached SPY 10s bars for a date."""
    path = os.path.join(CACHE_DIR, f"spy10s_{date}_SPY.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    if data == "none" or not isinstance(data, list) or len(data) < 50:
        return None
    return data


def time_to_seconds(t):
    """Convert HH:MM:SS or HH:MM to seconds since midnight."""
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


# ── Vol Spike Detection ──

def detect_spikes_1min(bars, drop_pct, lookback_mins, min_time="09:35", max_time="15:30"):
    """
    Detect intraday drops from rolling high.

    A spike is when price drops >= drop_pct% from the highest close
    in the last lookback_mins minutes.

    Returns list of (bar_index, drop_pct_actual, rolling_high, spike_low) tuples.
    """
    spikes = []
    for i in range(lookback_mins, len(bars)):
        bar = bars[i]
        if bar["time"] < min_time or bar["time"] > max_time:
            continue

        # Rolling high over lookback window
        window = bars[max(0, i - lookback_mins):i]
        rolling_high = max(b["high"] for b in window)

        # Current drop from rolling high
        current_drop = (rolling_high - bar["low"]) / rolling_high * 100

        if current_drop >= drop_pct:
            spikes.append({
                "bar_idx": i,
                "time": bar["time"],
                "drop_pct": current_drop,
                "rolling_high": rolling_high,
                "spike_low": bar["low"],
                "entry_price": bar["close"],  # enter at close of spike bar
            })

    # Deduplicate: only keep the deepest spike within a 5-min window
    filtered = []
    for spike in spikes:
        if filtered and time_to_seconds(spike["time"]) - time_to_seconds(filtered[-1]["time"]) < 300:
            # Within 5 mins of last spike — keep the deeper one
            if spike["drop_pct"] > filtered[-1]["drop_pct"]:
                filtered[-1] = spike
        else:
            filtered.append(spike)

    return filtered


# ── Trade Simulation ──

def simulate_trade(bars, entry_idx, entry_price, direction, pt_pct, sl_pct, trail_pct, ts_mins):
    """
    Simulate a trade from entry_idx using 1-min bars.
    direction: 1 = long (buy call / long underlying), -1 = short

    Returns dict with exit info.
    """
    peak = entry_price
    trough = entry_price

    for i in range(entry_idx + 1, min(entry_idx + ts_mins + 1, len(bars))):
        bar = bars[i]
        price = bar["close"]
        high = bar["high"]
        low = bar["low"]

        if direction == 1:
            if high > peak:
                peak = high

            # Profit target
            if pt_pct and high >= entry_price * (1 + pt_pct / 100):
                return {
                    "exit_idx": i, "exit_time": bar["time"],
                    "exit_price": entry_price * (1 + pt_pct / 100),
                    "reason": "Profit Target",
                    "pnl_pct": pt_pct,
                }

            # Stop loss
            if sl_pct and low <= entry_price * (1 - sl_pct / 100):
                return {
                    "exit_idx": i, "exit_time": bar["time"],
                    "exit_price": entry_price * (1 - sl_pct / 100),
                    "reason": "Stop Loss",
                    "pnl_pct": -sl_pct,
                }

            # Trailing stop
            if trail_pct and peak > entry_price:
                trail_level = peak * (1 - trail_pct / 100)
                if low <= trail_level:
                    pnl = (trail_level - entry_price) / entry_price * 100
                    return {
                        "exit_idx": i, "exit_time": bar["time"],
                        "exit_price": trail_level,
                        "reason": "Trailing Stop",
                        "pnl_pct": pnl,
                    }

        else:  # direction == -1
            if low < trough:
                trough = low

            if pt_pct and low <= entry_price * (1 - pt_pct / 100):
                return {
                    "exit_idx": i, "exit_time": bar["time"],
                    "exit_price": entry_price * (1 - pt_pct / 100),
                    "reason": "Profit Target",
                    "pnl_pct": pt_pct,
                }

            if sl_pct and high >= entry_price * (1 + sl_pct / 100):
                return {
                    "exit_idx": i, "exit_time": bar["time"],
                    "exit_price": entry_price * (1 + sl_pct / 100),
                    "reason": "Stop Loss",
                    "pnl_pct": -sl_pct,
                }

            if trail_pct and trough < entry_price:
                trail_level = trough * (1 + trail_pct / 100)
                if high >= trail_level:
                    pnl = (entry_price - trail_level) / entry_price * 100
                    return {
                        "exit_idx": i, "exit_time": bar["time"],
                        "exit_price": trail_level,
                        "reason": "Trailing Stop",
                        "pnl_pct": pnl,
                    }

    # Time stop
    last_idx = min(entry_idx + ts_mins, len(bars) - 1)
    exit_price = bars[last_idx]["close"]
    if direction == 1:
        pnl = (exit_price - entry_price) / entry_price * 100
    else:
        pnl = (entry_price - exit_price) / entry_price * 100

    return {
        "exit_idx": last_idx, "exit_time": bars[last_idx]["time"],
        "exit_price": exit_price,
        "reason": "Time Stop",
        "pnl_pct": pnl,
    }


def estimate_option_pnl(underlying_pnl_pct, entry_opt_price, contracts, direction, slippage=0.50):
    """
    Estimate option P&L from underlying move.
    Uses delta ~ 0.50 for ATM options, gamma boost for larger moves.
    """
    # ATM delta ~0.50, with gamma giving ~0.03 per 1% move
    delta = 0.50
    gamma_boost = abs(underlying_pnl_pct) * 0.03  # gamma effect
    effective_delta = delta + gamma_boost if underlying_pnl_pct > 0 else delta - gamma_boost * 0.5
    effective_delta = max(0.10, min(0.95, effective_delta))

    # Option price change
    opt_change_pct = underlying_pnl_pct * effective_delta / 0.50  # normalize
    opt_exit = entry_opt_price * (1 + opt_change_pct / 100)
    opt_exit = max(0.01, opt_exit)

    raw_pnl = (opt_exit - entry_opt_price) * 100 * contracts
    slip_cost = slippage * 100 * contracts

    return raw_pnl - slip_cost


# ── Main Research ──

def main():
    report_file = open(OUTPUT_REPORT, "w")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, report_file)

    print("=" * 80)
    print("INTRADAY VOL SPIKE MEAN REVERSION RESEARCH")
    print("=" * 80)
    print()

    # Load data
    print("Loading data...")
    intraday = load_intraday()
    daily = load_daily()
    vix_daily = load_vix_daily()
    vix_1min = load_vix_1min()

    dates = sorted(intraday.keys())
    print(f"  SPX 1-min days: {len(dates)}")
    print(f"  Date range: {dates[0]} to {dates[-1]}")
    print(f"  VIX daily: {len(vix_daily)} days")
    print()

    # ══════════════════════════════════════════════════════════════════
    # PHASE 1: Characterize intraday drops
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("PHASE 1: CHARACTERIZE INTRADAY DROPS")
    print("=" * 80)
    print()

    # For each day, find the max intraday drop from rolling high
    all_max_drops = []
    drop_by_vix = {"low": [], "mid": [], "high": []}

    for date in dates:
        bars = intraday[date]
        if len(bars) < 30:
            continue

        vix = vix_daily.get(date, 20)
        vix_bucket = "low" if vix < 18 else "mid" if vix < 25 else "high"

        # Track max drop from rolling 10-min high
        running_high = bars[0]["high"]
        max_drop = 0

        for i in range(1, len(bars)):
            if bars[i]["time"] < "09:35":
                continue
            # Rolling 10-min high
            window_start = max(0, i - 10)
            rolling_high = max(b["high"] for b in bars[window_start:i])
            running_high = max(running_high, bars[i]["high"])

            drop = (rolling_high - bars[i]["low"]) / rolling_high * 100
            if drop > max_drop:
                max_drop = drop

        all_max_drops.append({"date": date, "max_drop": max_drop, "vix": vix, "vix_bucket": vix_bucket})
        drop_by_vix[vix_bucket].append(max_drop)

    # Distribution of max intraday drops
    drops = [d["max_drop"] for d in all_max_drops]
    print("Max intraday drop from 10-min rolling high (% of SPX):")
    print(f"  Mean: {mean(drops):.3f}%")
    print(f"  Median: {median(drops):.3f}%")
    print(f"  Stdev: {stdev(drops):.3f}%")
    print(f"  P25: {sorted(drops)[len(drops)//4]:.3f}%")
    print(f"  P75: {sorted(drops)[3*len(drops)//4]:.3f}%")
    print(f"  P90: {sorted(drops)[int(len(drops)*0.9)]:.3f}%")
    print(f"  P95: {sorted(drops)[int(len(drops)*0.95)]:.3f}%")
    print()

    # By VIX regime
    for bucket in ["low", "mid", "high"]:
        d = drop_by_vix[bucket]
        if len(d) < 10:
            continue
        print(f"  VIX {bucket} ({len(d)} days): mean={mean(d):.3f}%  median={median(d):.3f}%  P90={sorted(d)[int(len(d)*0.9)]:.3f}%")
    print()

    # Frequency of drops by threshold
    for thresh in [0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75, 1.0]:
        count = sum(1 for d in drops if d >= thresh)
        print(f"  Days with >= {thresh:.2f}% drop: {count} ({count/len(drops)*100:.1f}%)")
    print()

    # ══════════════════════════════════════════════════════════════════
    # PHASE 2: Mean reversion after drops — raw stats
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("PHASE 2: MEAN REVERSION AFTER DROPS")
    print("=" * 80)
    print()

    # For each spike, measure the bounce over the next N minutes
    for drop_thresh in [0.20, 0.30, 0.40, 0.50]:
        print(f"--- Drop threshold: >= {drop_thresh:.2f}% from 10-min rolling high ---")

        bounces_5m = []
        bounces_10m = []
        bounces_15m = []
        bounces_30m = []
        spike_count = 0

        for date in dates:
            bars = intraday[date]
            if len(bars) < 30:
                continue

            spikes = detect_spikes_1min(bars, drop_thresh, lookback_mins=10)

            for spike in spikes:
                spike_count += 1
                idx = spike["bar_idx"]
                entry = spike["entry_price"]

                for hold, arr in [(5, bounces_5m), (10, bounces_10m), (15, bounces_15m), (30, bounces_30m)]:
                    exit_idx = min(idx + hold, len(bars) - 1)
                    exit_price = bars[exit_idx]["close"]
                    bounce_pct = (exit_price - entry) / entry * 100
                    arr.append(bounce_pct)

        print(f"  Spikes found: {spike_count}")
        if spike_count > 0:
            for label, arr in [("5m", bounces_5m), ("10m", bounces_10m), ("15m", bounces_15m), ("30m", bounces_30m)]:
                wins = sum(1 for b in arr if b > 0)
                avg = mean(arr) if arr else 0
                print(f"  {label} bounce: mean={avg:+.4f}%  win_rate={wins/len(arr)*100:.1f}%  median={median(arr):+.4f}%")
        print()

    # ══════════════════════════════════════════════════════════════════
    # PHASE 3: Full trade simulation — buy CALL on dip
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("PHASE 3: FULL TRADE SIMULATION — BUY CALL ON INTRADAY DIP")
    print("=" * 80)
    print()

    # Parameter sweep
    results = []

    drop_thresholds = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    trail_pcts = [0.05, 0.10, 0.15, 0.20]
    sl_pcts = [0.10, 0.15, 0.20, 0.30]
    ts_values = [15, 30, 60]
    lookbacks = [5, 10, 15]

    total_combos = len(drop_thresholds) * len(trail_pcts) * len(sl_pcts) * len(ts_values) * len(lookbacks)
    print(f"Sweeping {total_combos} parameter combinations...")
    print()

    combo_num = 0

    for drop_thresh in drop_thresholds:
        for lookback in lookbacks:
            for trail in trail_pcts:
                for sl in sl_pcts:
                    for ts in ts_values:
                        combo_num += 1

                        all_trades = []

                        for date in dates:
                            bars = intraday[date]
                            if len(bars) < 30:
                                continue

                            vix = vix_daily.get(date, 20)
                            day_data = daily.get(date)
                            if not day_data:
                                continue

                            spikes = detect_spikes_1min(bars, drop_thresh, lookback, min_time="09:35", max_time="15:00")

                            for spike in spikes:
                                # Buy call on dip — long direction
                                trade = simulate_trade(
                                    bars, spike["bar_idx"], spike["entry_price"],
                                    direction=1,
                                    pt_pct=None,  # no fixed PT, use trail
                                    sl_pct=sl,
                                    trail_pct=trail,
                                    ts_mins=ts,
                                )

                                # Estimate option P&L (ATM call ~$15-30 for SPX)
                                est_opt_price = max(5, vix * 0.8)  # rough ATM 0DTE call price
                                contracts = max(1, int(75000 / (est_opt_price * 100)))
                                opt_pnl = estimate_option_pnl(
                                    trade["pnl_pct"], est_opt_price, contracts, 1, slippage=0.50
                                )

                                all_trades.append({
                                    "date": date,
                                    "entry_time": spike["time"],
                                    "exit_time": trade["exit_time"],
                                    "reason": trade["reason"],
                                    "underlying_pnl_pct": trade["pnl_pct"],
                                    "opt_pnl": opt_pnl,
                                    "vix": vix,
                                    "drop_pct": spike["drop_pct"],
                                })

                        if len(all_trades) < 20:
                            continue

                        total_pnl = sum(t["opt_pnl"] for t in all_trades)
                        wins = sum(1 for t in all_trades if t["opt_pnl"] > 0)
                        avg_win = mean([t["opt_pnl"] for t in all_trades if t["opt_pnl"] > 0]) if wins > 0 else 0
                        losses = len(all_trades) - wins
                        avg_loss = mean([t["opt_pnl"] for t in all_trades if t["opt_pnl"] <= 0]) if losses > 0 else 0

                        # Monthly P&L for Sharpe
                        monthly = defaultdict(float)
                        for t in all_trades:
                            monthly[t["date"][:7]] += t["opt_pnl"]
                        monthly_vals = list(monthly.values())
                        sharpe = 0
                        if len(monthly_vals) > 3 and stdev(monthly_vals) > 0:
                            sharpe = mean(monthly_vals) / stdev(monthly_vals) * (12 ** 0.5)

                        results.append({
                            "drop": drop_thresh,
                            "lookback": lookback,
                            "trail": trail,
                            "sl": sl,
                            "ts": ts,
                            "trades": len(all_trades),
                            "total_pnl": total_pnl,
                            "win_rate": wins / len(all_trades) * 100,
                            "avg_win": avg_win,
                            "avg_loss": avg_loss,
                            "sharpe": sharpe,
                            "per_trade": total_pnl / len(all_trades),
                            "all_trades": all_trades,
                        })

    print(f"Completed sweep. {len(results)} valid combinations.")
    print()

    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)

    print("TOP 20 BY SHARPE:")
    print(f"{'Drop':>5} {'LB':>3} {'Trail':>6} {'SL':>6} {'TS':>3} | {'Trades':>6} {'Win%':>6} {'Total P&L':>12} {'$/Trade':>9} {'Sharpe':>7}")
    print("-" * 85)
    for r in results[:20]:
        print(f"{r['drop']:>5.2f} {r['lookback']:>3} {r['trail']:>6.2f} {r['sl']:>6.2f} {r['ts']:>3} | "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl']:>12,.0f} {r['per_trade']:>9,.0f} {r['sharpe']:>7.2f}")
    print()

    # Sort by total P&L
    results.sort(key=lambda x: x["total_pnl"], reverse=True)

    print("TOP 20 BY TOTAL P&L:")
    print(f"{'Drop':>5} {'LB':>3} {'Trail':>6} {'SL':>6} {'TS':>3} | {'Trades':>6} {'Win%':>6} {'Total P&L':>12} {'$/Trade':>9} {'Sharpe':>7}")
    print("-" * 85)
    for r in results[:20]:
        print(f"{r['drop']:>5.2f} {r['lookback']:>3} {r['trail']:>6.2f} {r['sl']:>6.2f} {r['ts']:>3} | "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl']:>12,.0f} {r['per_trade']:>9,.0f} {r['sharpe']:>7.2f}")
    print()

    # ══════════════════════════════════════════════════════════════════
    # PHASE 4: Deep dive on best edges
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("PHASE 4: DEEP DIVE ON BEST EDGES")
    print("=" * 80)
    print()

    # Take top 3 by Sharpe (min 100 trades) for deep analysis
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    top_edges = [r for r in results if r["trades"] >= 50][:5]

    for i, edge in enumerate(top_edges):
        print(f"Edge {i+1}: drop>={edge['drop']:.2f}% LB={edge['lookback']}m trail={edge['trail']:.2f}% SL={edge['sl']:.2f}% TS={edge['ts']}m")
        print(f"  Trades: {edge['trades']}  Win rate: {edge['win_rate']:.1f}%  Total P&L: ${edge['total_pnl']:,.0f}  Sharpe: {edge['sharpe']:.2f}")
        print(f"  Avg win: ${edge['avg_win']:,.0f}  Avg loss: ${edge['avg_loss']:,.0f}  Per trade: ${edge['per_trade']:,.0f}")

        # Yearly breakdown
        yearly = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
        for t in edge["all_trades"]:
            yr = t["date"][:4]
            yearly[yr]["pnl"] += t["opt_pnl"]
            yearly[yr]["trades"] += 1
            if t["opt_pnl"] > 0:
                yearly[yr]["wins"] += 1

        print(f"  Yearly breakdown:")
        for yr in sorted(yearly.keys()):
            y = yearly[yr]
            wr = y["wins"] / y["trades"] * 100 if y["trades"] > 0 else 0
            print(f"    {yr}: {y['trades']:>4} trades  {wr:>5.1f}% win  ${y['pnl']:>12,.0f}")

        # By VIX regime
        vix_low = [t for t in edge["all_trades"] if t["vix"] < 18]
        vix_mid = [t for t in edge["all_trades"] if 18 <= t["vix"] < 25]
        vix_high = [t for t in edge["all_trades"] if t["vix"] >= 25]

        print(f"  By VIX regime:")
        for label, trades in [("VIX<18", vix_low), ("18≤VIX<25", vix_mid), ("VIX≥25", vix_high)]:
            if len(trades) < 5:
                continue
            pnl = sum(t["opt_pnl"] for t in trades)
            wr = sum(1 for t in trades if t["opt_pnl"] > 0) / len(trades) * 100
            avg = pnl / len(trades)
            print(f"    {label}: {len(trades)} trades  {wr:.1f}% win  ${pnl:>12,.0f} total  ${avg:,.0f}/trade")

        # By exit reason
        by_reason = defaultdict(lambda: {"count": 0, "pnl": 0})
        for t in edge["all_trades"]:
            by_reason[t["reason"]]["count"] += 1
            by_reason[t["reason"]]["pnl"] += t["opt_pnl"]

        print(f"  By exit reason:")
        for reason in sorted(by_reason.keys()):
            r = by_reason[reason]
            print(f"    {reason}: {r['count']} trades  ${r['pnl']:>12,.0f}")

        # By time of day
        morning = [t for t in edge["all_trades"] if t["entry_time"] < "10:30"]
        midday = [t for t in edge["all_trades"] if "10:30" <= t["entry_time"] < "13:00"]
        afternoon = [t for t in edge["all_trades"] if t["entry_time"] >= "13:00"]

        print(f"  By time of day:")
        for label, trades in [("9:35-10:30", morning), ("10:30-13:00", midday), ("13:00-15:00", afternoon)]:
            if len(trades) < 5:
                continue
            pnl = sum(t["opt_pnl"] for t in trades)
            wr = sum(1 for t in trades if t["opt_pnl"] > 0) / len(trades) * 100
            print(f"    {label}: {len(trades)} trades  {wr:.1f}% win  ${pnl:>12,.0f}")

        # Max drawdown
        cumulative = 0
        peak_cum = 0
        max_dd = 0
        for t in sorted(edge["all_trades"], key=lambda x: x["date"] + x["entry_time"]):
            cumulative += t["opt_pnl"]
            if cumulative > peak_cum:
                peak_cum = cumulative
            dd = peak_cum - cumulative
            if dd > max_dd:
                max_dd = dd

        print(f"  Max drawdown: ${max_dd:,.0f}")
        print()

    # ══════════════════════════════════════════════════════════════════
    # PHASE 5: Alternative — sell put on spike (short vol)
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("PHASE 5: ALTERNATIVE — SHORT VOL ON SPIKE (sell put at spike)")
    print("=" * 80)
    print()
    print("Testing: When SPX drops sharply, sell OTM put (short vol) expecting mean reversion")
    print("Risk: defined by buying further OTM put as hedge (spread)")
    print()

    # Use the best drop threshold from Phase 3
    best_drop = top_edges[0]["drop"] if top_edges else 0.30
    best_lookback = top_edges[0]["lookback"] if top_edges else 10

    # For selling puts: profit if SPX stays flat or bounces
    # Model as: sell ATM put, buy 20-pt OTM put (credit spread)
    # Max profit = credit received, max loss = spread width - credit

    spread_results = []
    for drop_thresh in [0.20, 0.30, 0.40, 0.50]:
        trades = []
        for date in dates:
            bars = intraday[date]
            if len(bars) < 30:
                continue
            vix = vix_daily.get(date, 20)

            spikes = detect_spikes_1min(bars, drop_thresh, best_lookback, min_time="09:35", max_time="14:30")

            for spike in spikes:
                idx = spike["bar_idx"]
                entry = spike["entry_price"]

                # Credit spread: sell ATM put, buy 20pt OTM put
                # Credit ~ VIX * 0.4 (rough estimate for ATM 0DTE put at spike)
                credit = vix * 0.4
                spread_width = 20  # 20 points
                max_loss_per_contract = (spread_width - credit) * 100

                # Hold for 30 minutes, then close
                exit_idx = min(idx + 30, len(bars) - 1)
                exit_price = bars[exit_idx]["close"]

                # If SPX recovered or stayed: keep most of credit
                # If SPX dropped more: lose on spread
                move = exit_price - entry

                # Simplified P&L: if SPX above entry, keep ~80% of credit (theta decay)
                # If SPX below entry, lose proportionally
                if move >= 0:
                    pnl_per = credit * 0.6 * 100  # kept ~60% of credit in 30 min
                elif move > -spread_width:
                    pnl_per = (credit - abs(move) * 0.5) * 100
                else:
                    pnl_per = -(spread_width - credit) * 100  # max loss

                contracts = max(1, int(75000 / max_loss_per_contract)) if max_loss_per_contract > 0 else 5
                total_pnl = pnl_per * contracts - 0.50 * 100 * contracts  # slippage

                trades.append({
                    "date": date, "pnl": total_pnl, "move": move,
                    "vix": vix, "entry_time": spike["time"],
                })

        if len(trades) < 20:
            continue

        total = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        monthly = defaultdict(float)
        for t in trades:
            monthly[t["date"][:7]] += t["pnl"]
        mv = list(monthly.values())
        sharpe = mean(mv) / stdev(mv) * (12 ** 0.5) if len(mv) > 3 and stdev(mv) > 0 else 0

        spread_results.append({
            "drop": drop_thresh, "trades": len(trades), "total": total,
            "win_rate": wins/len(trades)*100, "sharpe": sharpe,
            "per_trade": total / len(trades),
        })

        print(f"  Drop>={drop_thresh:.2f}%: {len(trades)} trades  Win={wins/len(trades)*100:.1f}%  "
              f"Total=${total:,.0f}  $/trade=${total/len(trades):,.0f}  Sharpe={sharpe:.2f}")

    print()

    # ══════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()

    if top_edges:
        best = top_edges[0]
        print(f"Best long-call edge (by Sharpe):")
        print(f"  Trigger: SPX drops >= {best['drop']:.2f}% from {best['lookback']}-min rolling high")
        print(f"  Action:  BUY ATM CALL")
        print(f"  Exit:    Trail {best['trail']:.2f}% / SL {best['sl']:.2f}% / TS {best['ts']}m")
        print(f"  Results: {best['trades']} trades | {best['win_rate']:.1f}% win | ${best['total_pnl']:,.0f} total | Sharpe {best['sharpe']:.2f}")
        print()

    if spread_results:
        best_spread = max(spread_results, key=lambda x: x["sharpe"])
        print(f"Best credit spread edge (by Sharpe):")
        print(f"  Trigger: SPX drops >= {best_spread['drop']:.2f}% from rolling high")
        print(f"  Action:  SELL ATM PUT / BUY 20pt OTM PUT (credit spread)")
        print(f"  Results: {best_spread['trades']} trades | {best_spread['win_rate']:.1f}% win | ${best_spread['total']:.0f} total | Sharpe {best_spread['sharpe']:.2f}")

    print()
    print("Script complete.")

    sys.stdout = original_stdout
    report_file.close()
    print(f"\nReport saved to: {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
