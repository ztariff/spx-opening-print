"""
SPX Opening Print Strategy — Phase 4: Optimal Exit Framework
=============================================================
For each condition identified in Phase 3, this script runs a grid search
over profit targets and stop losses to find the optimal exit parameters.

Tests every combination of:
  - Profit targets: 2, 5, 8, 10, 15, 20, 30, 50, 75, 100 SPX points
  - Stop losses:    2, 5, 8, 10, 15, 20, 30, 50 SPX points
  - Time stops:     15, 30, 60, 120, 240, 390 minutes

For each combo, computes:
  - Win rate
  - Average P&L per trade
  - Profit factor (gross wins / gross losses)
  - Max consecutive losers
  - Expectancy
  - Average hold time

Requires: spx_1min_bars.csv + baseline_stats.csv from prior phases

Usage:
    python 04_optimal_exits.py
"""

import os
import csv
from collections import defaultdict
from statistics import mean, median
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
STATS_FILE = os.path.join(SCRIPT_DIR, "baseline_stats.csv")
REPORT_FILE = os.path.join(SCRIPT_DIR, "optimal_exits_report.txt")
GRID_CSV = os.path.join(SCRIPT_DIR, "exit_grid_results.csv")
BEST_CSV = os.path.join(SCRIPT_DIR, "best_exits.csv")

# ── Exit parameter grid ────────────────────────────────────────────────
PROFIT_TARGETS = [2, 5, 8, 10, 15, 20, 30, 50, 75, 100]
STOP_LOSSES = [2, 5, 8, 10, 15, 20, 30, 50]
TIME_STOPS = [15, 30, 60, 120, 240, 390]  # minutes

report_lines = []


def p(line=""):
    report_lines.append(line)
    print(line)


def load_intraday():
    """Load 1-min bars grouped by date. Returns dict of date → list of bars."""
    days = defaultdict(list)
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
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


def load_daily_context():
    """Build daily context for condition filtering."""
    daily = {}
    intraday = load_intraday()
    dates_sorted = sorted(intraday.keys())
    date_idx = {d: i for i, d in enumerate(dates_sorted)}

    for d in dates_sorted:
        bars = intraday[d]
        if len(bars) < 10:
            continue

        entry = {"date": d, "bars": bars}
        dt = datetime.strptime(d, "%Y-%m-%d")
        entry["day_of_week"] = dt.strftime("%A")
        entry["entry_open"] = bars[0]["open"]
        entry["entry_1min"] = bars[0]["close"]

        idx = date_idx[d]

        # Prior day data
        if idx > 0:
            prev_date = dates_sorted[idx - 1]
            prev_bars = intraday.get(prev_date, [])
            if prev_bars:
                prev_open = prev_bars[0]["open"]
                prev_close = prev_bars[-1]["close"]
                prev_high = max(b["high"] for b in prev_bars)
                prev_low = min(b["low"] for b in prev_bars)

                gap = bars[0]["open"] - prev_close
                gap_pct = gap / prev_close * 100
                entry["gap_dir"] = "up" if gap > 0 else "down" if gap < 0 else "flat"
                entry["gap_pct"] = gap_pct

                prev_ret = (prev_close - prev_open) / prev_open * 100
                entry["prev_day_dir"] = "up" if prev_ret > 0.1 else "down" if prev_ret < -0.1 else "flat"

                # Range
                prev_range_pct = (prev_high - prev_low) / prev_close * 100
                if prev_range_pct < 0.5:
                    entry["prev_range"] = "tight"
                elif prev_range_pct < 1.0:
                    entry["prev_range"] = "normal"
                else:
                    entry["prev_range"] = "wide"

        # First bar direction
        if bars[0]["close"] > bars[0]["open"]:
            entry["first_bar"] = "bullish"
        elif bars[0]["close"] < bars[0]["open"]:
            entry["first_bar"] = "bearish"
        else:
            entry["first_bar"] = "doji"

        # Streak
        streak = 0
        for j in range(idx - 1, max(idx - 11, -1), -1):
            if j < 0:
                break
            prev_d = dates_sorted[j]
            prev_b = intraday.get(prev_d, [])
            if prev_b:
                day_ret = prev_b[-1]["close"] - prev_b[0]["open"]
                if streak == 0:
                    streak = 1 if day_ret > 0 else -1
                elif streak > 0 and day_ret > 0:
                    streak += 1
                elif streak < 0 and day_ret < 0:
                    streak -= 1
                else:
                    break

        if streak >= 3:
            entry["streak"] = "3+ up"
        elif streak >= 1:
            entry["streak"] = "1-2 up"
        elif streak == 0:
            entry["streak"] = "none"
        elif streak >= -2:
            entry["streak"] = "1-2 down"
        else:
            entry["streak"] = "3+ down"

        daily[d] = entry

    return daily


def simulate_trade(bars, entry_price, profit_target, stop_loss, time_stop, entry_bar_idx=0):
    """
    Simulate a long trade with PT, SL, and time stop.
    Returns: (pnl, exit_reason, hold_minutes, exit_price)
    """
    for i in range(entry_bar_idx + 1, min(entry_bar_idx + time_stop + 1, len(bars))):
        bar = bars[i]
        mins_held = i - entry_bar_idx

        # Check stop loss first (assume worst case — stop hit before target in same bar)
        if bar["low"] <= entry_price - stop_loss:
            return (-stop_loss, "stop_loss", mins_held, entry_price - stop_loss)

        # Check profit target
        if bar["high"] >= entry_price + profit_target:
            return (profit_target, "profit_target", mins_held, entry_price + profit_target)

    # Time stop: exit at close of the last bar
    last_idx = min(entry_bar_idx + time_stop, len(bars) - 1)
    exit_price = bars[last_idx]["close"]
    pnl = exit_price - entry_price
    return (pnl, "time_stop", last_idx - entry_bar_idx, exit_price)


def run_grid_search(days_list, label="ALL"):
    """Run full grid search over PT/SL/TimeStop for a set of days."""
    results = []

    for pt in PROFIT_TARGETS:
        for sl in STOP_LOSSES:
            for ts in TIME_STOPS:
                trades = []
                for day in days_list:
                    bars = day["bars"]
                    entry = day["entry_open"]
                    pnl, reason, hold_time, exit_px = simulate_trade(bars, entry, pt, sl, ts, entry_bar_idx=0)
                    trades.append({
                        "pnl": pnl,
                        "reason": reason,
                        "hold_time": hold_time,
                    })

                if not trades:
                    continue

                pnls = [t["pnl"] for t in trades]
                wins = [t["pnl"] for t in trades if t["pnl"] > 0]
                losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
                win_rate = len(wins) / len(trades) * 100

                gross_wins = sum(wins) if wins else 0
                gross_losses = abs(sum(losses)) if losses else 0.01
                profit_factor = gross_wins / gross_losses

                # Max consecutive losers
                max_consec_loss = 0
                current_streak = 0
                for t in trades:
                    if t["pnl"] <= 0:
                        current_streak += 1
                        max_consec_loss = max(max_consec_loss, current_streak)
                    else:
                        current_streak = 0

                avg_hold = mean([t["hold_time"] for t in trades])

                # Exits by reason
                by_reason = defaultdict(int)
                for t in trades:
                    by_reason[t["reason"]] += 1

                results.append({
                    "condition": label,
                    "profit_target": pt,
                    "stop_loss": sl,
                    "time_stop": ts,
                    "n_trades": len(trades),
                    "win_rate": round(win_rate, 2),
                    "avg_pnl": round(mean(pnls), 2),
                    "total_pnl": round(sum(pnls), 2),
                    "median_pnl": round(median(pnls), 2),
                    "profit_factor": round(profit_factor, 3),
                    "max_consec_loss": max_consec_loss,
                    "avg_hold_min": round(avg_hold, 1),
                    "pct_pt_exit": round(by_reason.get("profit_target", 0) / len(trades) * 100, 1),
                    "pct_sl_exit": round(by_reason.get("stop_loss", 0) / len(trades) * 100, 1),
                    "pct_ts_exit": round(by_reason.get("time_stop", 0) / len(trades) * 100, 1),
                    "expectancy": round(mean(pnls), 4),
                })

    return results


def main():
    p("Loading data...")
    daily = load_daily_context()
    all_days = list(daily.values())
    p(f"Loaded {len(all_days)} trading days\n")

    all_grid_results = []

    # ── 1. Full dataset (no filter) ─────────────────────────────────────
    p("=" * 70)
    p("GRID SEARCH: ALL DAYS (NO FILTER)")
    p("=" * 70)
    results = run_grid_search(all_days, "ALL")
    all_grid_results.extend(results)

    # Top 10 by avg P&L
    top10 = sorted(results, key=lambda x: x["avg_pnl"], reverse=True)[:10]
    p(f"\nTop 10 parameter combos by Avg P&L:")
    p(f"  {'PT':>4s}  {'SL':>4s}  {'TS':>5s}  {'WR%':>6s}  {'AvgPnL':>8s}  {'PF':>6s}  {'MaxCL':>6s}  {'AvgHld':>7s}  {'TotPnL':>10s}")
    for r in top10:
        p(f"  {r['profit_target']:>4d}  {r['stop_loss']:>4d}  {r['time_stop']:>5d}  "
          f"{r['win_rate']:>5.1f}%  {r['avg_pnl']:>+8.2f}  {r['profit_factor']:>6.2f}  "
          f"{r['max_consec_loss']:>6d}  {r['avg_hold_min']:>6.1f}m  {r['total_pnl']:>+10.1f}")

    # Top 10 by profit factor (min 40% win rate)
    pf_filtered = [r for r in results if r["win_rate"] >= 40]
    top10_pf = sorted(pf_filtered, key=lambda x: x["profit_factor"], reverse=True)[:10]
    p(f"\nTop 10 by Profit Factor (WR >= 40%):")
    p(f"  {'PT':>4s}  {'SL':>4s}  {'TS':>5s}  {'WR%':>6s}  {'AvgPnL':>8s}  {'PF':>6s}  {'MaxCL':>6s}  {'AvgHld':>7s}")
    for r in top10_pf:
        p(f"  {r['profit_target']:>4d}  {r['stop_loss']:>4d}  {r['time_stop']:>5d}  "
          f"{r['win_rate']:>5.1f}%  {r['avg_pnl']:>+8.2f}  {r['profit_factor']:>6.2f}  "
          f"{r['max_consec_loss']:>6d}  {r['avg_hold_min']:>6.1f}m")

    # ── 2. Condition-filtered searches ──────────────────────────────────
    conditions_to_test = [
        ("gap_dir", "up", "Gap Up Days"),
        ("gap_dir", "down", "Gap Down Days"),
        ("prev_day_dir", "up", "After Up Day"),
        ("prev_day_dir", "down", "After Down Day"),
        ("first_bar", "bullish", "Bullish First Bar"),
        ("first_bar", "bearish", "Bearish First Bar"),
        ("streak", "3+ down", "After 3+ Down Days"),
        ("streak", "3+ up", "After 3+ Up Days"),
        ("prev_range", "tight", "After Tight Range Day"),
        ("prev_range", "wide", "After Wide Range Day"),
    ]

    best_per_condition = []

    for key, val, label in conditions_to_test:
        filtered = [d for d in all_days if d.get(key) == val]
        if len(filtered) < 30:
            p(f"\nSkipping '{label}' — only {len(filtered)} days")
            continue

        p(f"\n{'='*70}")
        p(f"GRID SEARCH: {label.upper()} ({len(filtered)} days)")
        p(f"{'='*70}")

        results = run_grid_search(filtered, label)
        all_grid_results.extend(results)

        top5 = sorted(results, key=lambda x: x["avg_pnl"], reverse=True)[:5]
        p(f"  Top 5 by Avg P&L:")
        p(f"  {'PT':>4s}  {'SL':>4s}  {'TS':>5s}  {'WR%':>6s}  {'AvgPnL':>8s}  {'PF':>6s}  {'MaxCL':>6s}  {'AvgHld':>7s}  {'TotPnL':>10s}")
        for r in top5:
            p(f"  {r['profit_target']:>4d}  {r['stop_loss']:>4d}  {r['time_stop']:>5d}  "
              f"{r['win_rate']:>5.1f}%  {r['avg_pnl']:>+8.2f}  {r['profit_factor']:>6.2f}  "
              f"{r['max_consec_loss']:>6d}  {r['avg_hold_min']:>6.1f}m  {r['total_pnl']:>+10.1f}")

        if top5:
            best_per_condition.append({"condition": label, "n_days": len(filtered), **top5[0]})

    # ── Summary: Best setup per condition ───────────────────────────────
    p(f"\n\n{'='*70}")
    p("BEST EXIT PARAMETERS PER CONDITION")
    p(f"{'='*70}")
    p(f"  {'Condition':<30s}  {'N':>5s}  {'PT':>4s}  {'SL':>4s}  {'TS':>5s}  {'WR%':>6s}  {'AvgPnL':>8s}  {'PF':>6s}")
    for b in sorted(best_per_condition, key=lambda x: x["avg_pnl"], reverse=True):
        p(f"  {b['condition']:<30s}  {b['n_days']:>5d}  {b['profit_target']:>4d}  "
          f"{b['stop_loss']:>4d}  {b['time_stop']:>5d}  {b['win_rate']:>5.1f}%  "
          f"{b['avg_pnl']:>+8.2f}  {b['profit_factor']:>6.2f}")

    # ── Save everything ─────────────────────────────────────────────────
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_lines))

    with open(GRID_CSV, "w", newline="") as f:
        if all_grid_results:
            writer = csv.DictWriter(f, fieldnames=all_grid_results[0].keys())
            writer.writeheader()
            writer.writerows(all_grid_results)

    with open(BEST_CSV, "w", newline="") as f:
        if best_per_condition:
            writer = csv.DictWriter(f, fieldnames=best_per_condition[0].keys())
            writer.writeheader()
            writer.writerows(best_per_condition)

    p(f"\nFiles saved:")
    p(f"  {REPORT_FILE}")
    p(f"  {GRID_CSV}")
    p(f"  {BEST_CSV}")


if __name__ == "__main__":
    main()
