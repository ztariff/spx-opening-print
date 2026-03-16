"""
SPX Opening Print Strategy — Phase 3: Edge Detection
=====================================================
Slices the baseline data by observable conditions to find WHEN
buying the opening print has the most (or least) edge.

Conditions tested (all purely data-driven, no models):
  1. Day of week
  2. Day of month (beginning/middle/end)
  3. Overnight gap direction & size (prior close → today open)
  4. Prior day's return (up/down/flat, magnitude)
  5. Prior day's range (high vol vs low vol)
  6. Streak (consecutive up/down days coming in)
  7. Distance from recent high/low (where is price relative to 20d high/low)
  8. First 1-min bar direction (open bar up vs down)
  9. Gap fill tendency (does gap direction predict reversal?)
  10. Month of year
  11. Prior day's close location within its range

Requires: baseline_stats.csv from Phase 2

Usage:
    python 03_edge_detection.py
"""

import os
import csv
from collections import defaultdict
from statistics import mean, median, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(SCRIPT_DIR, "baseline_stats.csv")
INPUT_FILE = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
REPORT_FILE = os.path.join(SCRIPT_DIR, "edge_detection_report.txt")
EDGE_CSV = os.path.join(SCRIPT_DIR, "edge_conditions.csv")

CHECKPOINTS = [5, 15, 30, 60, 120, 390]  # minutes to report on

report_lines = []


def p(line=""):
    report_lines.append(line)
    print(line)


def load_stats():
    """Load baseline_stats.csv into list of dicts."""
    rows = []
    with open(STATS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            for k in row:
                if k == "date":
                    continue
                try:
                    row[k] = float(row[k]) if row[k] != "" else None
                except ValueError:
                    pass
            rows.append(row)
    return rows


def load_daily_ohlc():
    """Build daily OHLC from 1-min bars for prior-day calculations."""
    daily = defaultdict(lambda: {"open": None, "high": -999999, "low": 999999, "close": None, "bars": []})
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = row["time"]
            if t < "09:30" or t >= "16:00":
                continue
            d = row["date"]
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            if daily[d]["open"] is None:
                daily[d]["open"] = o
            daily[d]["high"] = max(daily[d]["high"], h)
            daily[d]["low"] = min(daily[d]["low"], l)
            daily[d]["close"] = c
            daily[d]["bars"].append({"time": t, "open": o, "high": h, "low": l, "close": c})

    return dict(daily)


def safe_stats(values, label=""):
    """Compute stats for a list of values, return dict."""
    if not values:
        return {"n": 0, "mean": 0, "median": 0, "winrate": 0, "stdev": 0}
    wins = sum(1 for v in values if v > 0)
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "winrate": wins / len(values) * 100,
        "stdev": stdev(values) if len(values) > 1 else 0,
    }


def print_condition_table(groups, metric_key, condition_name):
    """Print a table comparing groups for a given metric."""
    p(f"\n{'='*70}")
    p(f"CONDITION: {condition_name}")
    p(f"{'='*70}")
    p(f"  {'Group':<25s}  {'N':>5s}  {'WinRate':>8s}  {'AvgP&L':>10s}  {'MedP&L':>10s}  {'StdDev':>8s}")
    p(f"  {'-'*25}  {'-'*5}  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*8}")

    all_rows = []
    for group_name in sorted(groups.keys()):
        vals = [r[metric_key] for r in groups[group_name] if r.get(metric_key) is not None]
        s = safe_stats(vals)
        if s["n"] < 5:
            continue
        p(f"  {str(group_name):<25s}  {s['n']:>5d}  {s['winrate']:>7.1f}%  {s['mean']:>+10.2f}  {s['median']:>+10.2f}  {s['stdev']:>8.2f}")
        all_rows.append({"condition": condition_name, "group": group_name, **s})

    return all_rows


def main():
    stats = load_stats()
    daily = load_daily_ohlc()
    dates_sorted = sorted(daily.keys())

    # Build a lookup: date → index for prev-day access
    date_idx = {d: i for i, d in enumerate(dates_sorted)}

    # Enrich each stats row with condition fields
    enriched = []
    for row in stats:
        d = row["date"]
        if d not in date_idx:
            continue

        idx = date_idx[d]
        dt = datetime.strptime(d, "%Y-%m-%d")

        entry = {**row}
        entry["day_of_week"] = dt.strftime("%A")
        entry["day_of_week_num"] = dt.weekday()
        entry["month"] = dt.strftime("%B")
        entry["month_num"] = dt.month
        entry["day_of_month"] = dt.day

        # Day-of-month bucket
        if dt.day <= 7:
            entry["month_period"] = "1st week"
        elif dt.day <= 14:
            entry["month_period"] = "2nd week"
        elif dt.day <= 21:
            entry["month_period"] = "3rd week"
        else:
            entry["month_period"] = "4th+ week"

        # Prior day data
        if idx > 0:
            prev_date = dates_sorted[idx - 1]
            prev = daily[prev_date]
            today = daily[d]

            if prev["close"] and today["open"]:
                # Overnight gap
                gap = today["open"] - prev["close"]
                gap_pct = gap / prev["close"] * 100
                entry["gap_points"] = gap
                entry["gap_pct"] = gap_pct
                entry["gap_dir"] = "up" if gap > 0 else "down" if gap < 0 else "flat"

                # Gap size bucket
                abs_gap = abs(gap_pct)
                if abs_gap < 0.1:
                    entry["gap_size"] = "tiny (<0.1%)"
                elif abs_gap < 0.3:
                    entry["gap_size"] = "small (0.1-0.3%)"
                elif abs_gap < 0.5:
                    entry["gap_size"] = "medium (0.3-0.5%)"
                elif abs_gap < 1.0:
                    entry["gap_size"] = "large (0.5-1%)"
                else:
                    entry["gap_size"] = "huge (>1%)"

                # Gap with direction
                entry["gap_dir_size"] = f"{entry['gap_dir']} {entry['gap_size']}"

            # Prior day return
            if prev["open"] and prev["close"]:
                prev_ret = (prev["close"] - prev["open"]) / prev["open"] * 100
                entry["prev_day_return_pct"] = prev_ret
                entry["prev_day_dir"] = "up" if prev_ret > 0.1 else "down" if prev_ret < -0.1 else "flat"

                abs_ret = abs(prev_ret)
                if abs_ret < 0.25:
                    entry["prev_day_magnitude"] = "small (<0.25%)"
                elif abs_ret < 0.5:
                    entry["prev_day_magnitude"] = "medium (0.25-0.5%)"
                elif abs_ret < 1.0:
                    entry["prev_day_magnitude"] = "large (0.5-1%)"
                else:
                    entry["prev_day_magnitude"] = "very large (>1%)"

                entry["prev_day_dir_mag"] = f"{entry['prev_day_dir']} {entry['prev_day_magnitude']}"

            # Prior day range (volatility proxy)
            if prev["high"] and prev["low"] and prev["close"]:
                prev_range = (prev["high"] - prev["low"]) / prev["close"] * 100
                entry["prev_range_pct"] = prev_range
                if prev_range < 0.5:
                    entry["prev_range_bucket"] = "tight (<0.5%)"
                elif prev_range < 1.0:
                    entry["prev_range_bucket"] = "normal (0.5-1%)"
                elif prev_range < 1.5:
                    entry["prev_range_bucket"] = "wide (1-1.5%)"
                else:
                    entry["prev_range_bucket"] = "very wide (>1.5%)"

            # Prior close location within range
            if prev["high"] != prev["low"]:
                close_loc = (prev["close"] - prev["low"]) / (prev["high"] - prev["low"])
                entry["prev_close_location"] = close_loc
                if close_loc >= 0.75:
                    entry["prev_close_loc_bucket"] = "near high (75-100%)"
                elif close_loc >= 0.5:
                    entry["prev_close_loc_bucket"] = "upper half (50-75%)"
                elif close_loc >= 0.25:
                    entry["prev_close_loc_bucket"] = "lower half (25-50%)"
                else:
                    entry["prev_close_loc_bucket"] = "near low (0-25%)"

        # Streak: consecutive up/down days
        streak = 0
        if idx > 0:
            look_back = idx - 1
            while look_back >= 0:
                lb_date = dates_sorted[look_back]
                lb = daily[lb_date]
                if lb["close"] and lb["open"]:
                    if lb["close"] > lb["open"]:
                        if streak <= 0:
                            streak -= 1  # count down days as negative streak? No...
                        # Let's define: positive streak = consecutive up days, negative = consecutive down
                        pass
                break  # Simplified — compute properly below

        # Proper streak computation
        streak = 0
        for j in range(idx - 1, max(idx - 11, -1), -1):
            if j < 0:
                break
            prev_d = dates_sorted[j]
            prev_data = daily[prev_d]
            if prev_data["close"] and prev_data["open"]:
                day_ret = prev_data["close"] - prev_data["open"]
                if streak == 0:
                    streak = 1 if day_ret > 0 else -1
                elif streak > 0 and day_ret > 0:
                    streak += 1
                elif streak < 0 and day_ret < 0:
                    streak -= 1
                else:
                    break
            else:
                break

        entry["streak"] = streak
        if streak >= 3:
            entry["streak_bucket"] = "3+ up days"
        elif streak >= 1:
            entry["streak_bucket"] = "1-2 up days"
        elif streak == 0:
            entry["streak_bucket"] = "no streak"
        elif streak >= -2:
            entry["streak_bucket"] = "1-2 down days"
        else:
            entry["streak_bucket"] = "3+ down days"

        # Distance from 20-day high/low
        if idx >= 20:
            lookback_dates = dates_sorted[idx-20:idx]
            recent_highs = [daily[dd]["high"] for dd in lookback_dates if daily[dd]["high"] > -999999]
            recent_lows = [daily[dd]["low"] for dd in lookback_dates if daily[dd]["low"] < 999999]
            if recent_highs and recent_lows:
                high_20d = max(recent_highs)
                low_20d = min(recent_lows)
                today_open = entry.get("entry_open")
                if today_open and high_20d != low_20d:
                    pct_from_high = (today_open - high_20d) / high_20d * 100
                    pct_in_range = (today_open - low_20d) / (high_20d - low_20d) * 100
                    entry["pct_from_20d_high"] = pct_from_high
                    entry["pct_in_20d_range"] = pct_in_range

                    if pct_in_range >= 90:
                        entry["range_position"] = "near 20d high (90-100%)"
                    elif pct_in_range >= 70:
                        entry["range_position"] = "upper range (70-90%)"
                    elif pct_in_range >= 30:
                        entry["range_position"] = "mid range (30-70%)"
                    elif pct_in_range >= 10:
                        entry["range_position"] = "lower range (10-30%)"
                    else:
                        entry["range_position"] = "near 20d low (0-10%)"

        # First 1-min bar direction
        if d in daily and daily[d]["bars"]:
            first_bar = daily[d]["bars"][0]
            if first_bar["close"] > first_bar["open"]:
                entry["first_bar_dir"] = "bullish open bar"
            elif first_bar["close"] < first_bar["open"]:
                entry["first_bar_dir"] = "bearish open bar"
            else:
                entry["first_bar_dir"] = "doji open bar"

            # First bar range relative to avg
            first_bar_range = first_bar["high"] - first_bar["low"]
            entry["first_bar_range"] = first_bar_range

        enriched.append(entry)

    # ── Run all condition analyses ──────────────────────────────────────
    p("=" * 70)
    p("SPX OPENING PRINT — EDGE DETECTION REPORT")
    p("=" * 70)
    p(f"Days analyzed: {len(enriched)}")
    p()

    # Use EOD P&L from 9:30 open as the primary metric, plus checkpoints
    all_edge_rows = []

    # Group-by helper
    def groupby(data, key):
        groups = defaultdict(list)
        for row in data:
            if key in row and row[key] is not None:
                groups[row[key]].append(row)
        return groups

    # 1. Day of week
    all_edge_rows.extend(print_condition_table(groupby(enriched, "day_of_week"), "eod_pnl_open", "Day of Week"))

    # 2. Month
    all_edge_rows.extend(print_condition_table(groupby(enriched, "month"), "eod_pnl_open", "Month of Year"))

    # 3. Month period
    all_edge_rows.extend(print_condition_table(groupby(enriched, "month_period"), "eod_pnl_open", "Week of Month"))

    # 4. Gap direction
    all_edge_rows.extend(print_condition_table(groupby(enriched, "gap_dir"), "eod_pnl_open", "Overnight Gap Direction"))

    # 5. Gap size
    all_edge_rows.extend(print_condition_table(groupby(enriched, "gap_size"), "eod_pnl_open", "Overnight Gap Size (absolute)"))

    # 6. Gap direction + size combined
    all_edge_rows.extend(print_condition_table(groupby(enriched, "gap_dir_size"), "eod_pnl_open", "Gap Direction + Size"))

    # 7. Prior day direction
    all_edge_rows.extend(print_condition_table(groupby(enriched, "prev_day_dir"), "eod_pnl_open", "Prior Day Direction"))

    # 8. Prior day direction + magnitude
    all_edge_rows.extend(print_condition_table(groupby(enriched, "prev_day_dir_mag"), "eod_pnl_open", "Prior Day Direction + Magnitude"))

    # 9. Prior day range
    all_edge_rows.extend(print_condition_table(groupby(enriched, "prev_range_bucket"), "eod_pnl_open", "Prior Day Range (Volatility)"))

    # 10. Streak
    all_edge_rows.extend(print_condition_table(groupby(enriched, "streak_bucket"), "eod_pnl_open", "Win/Loss Streak Coming In"))

    # 11. 20d range position
    all_edge_rows.extend(print_condition_table(groupby(enriched, "range_position"), "eod_pnl_open", "Position in 20-Day Range"))

    # 12. First bar direction
    all_edge_rows.extend(print_condition_table(groupby(enriched, "first_bar_dir"), "eod_pnl_open", "First 1-Min Bar Direction"))

    # 13. Prior close location
    all_edge_rows.extend(print_condition_table(groupby(enriched, "prev_close_loc_bucket"), "eod_pnl_open", "Prior Day Close Location in Range"))

    # ── Also show checkpoint P&Ls for key conditions ────────────────────
    p("\n\n" + "=" * 70)
    p("HOLD-PERIOD BREAKDOWN FOR KEY CONDITIONS")
    p("=" * 70)

    key_conditions = [
        ("gap_dir", "Overnight Gap Direction"),
        ("prev_day_dir", "Prior Day Direction"),
        ("streak_bucket", "Win/Loss Streak"),
        ("range_position", "20-Day Range Position"),
        ("first_bar_dir", "First Bar Direction"),
    ]

    for key, label in key_conditions:
        groups = groupby(enriched, key)
        p(f"\n--- {label} ---")
        p(f"  {'Group':<30s}", end="")
        for cp in CHECKPOINTS:
            cp_label = f"{cp}m" if cp < 60 else f"{cp//60}h"
            p(f"  {cp_label:>8s}", end="")
        p()
        p(f"  {'-'*30}", end="")
        for _ in CHECKPOINTS:
            p(f"  {'--------':>8s}", end="")
        p()

        for group_name in sorted(groups.keys()):
            p(f"  {str(group_name):<30s}", end="")
            for cp in CHECKPOINTS:
                col = f"pnl_open_{cp}m"
                vals = [r[col] for r in groups[group_name] if r.get(col) is not None]
                if vals:
                    p(f"  {mean(vals):>+8.2f}", end="")
                else:
                    p(f"  {'N/A':>8s}", end="")
            p()

    # ── Save report ─────────────────────────────────────────────────────
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_lines))

    # ── Save edge conditions CSV ────────────────────────────────────────
    if all_edge_rows:
        with open(EDGE_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["condition", "group", "n", "winrate", "mean", "median", "stdev"])
            writer.writeheader()
            for row in all_edge_rows:
                writer.writerow(row)

    print(f"\nFiles saved:")
    print(f"  {REPORT_FILE}")
    print(f"  {EDGE_CSV}")


def p(line="", end="\n"):
    """Custom print that captures output."""
    report_lines.append(line if end == "\n" else line)
    print(line, end=end)


if __name__ == "__main__":
    main()
