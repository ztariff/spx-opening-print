"""
SPX Opening Print Strategy — Phase 2: Baseline Analysis
========================================================
Reads the 1-min SPX data and computes what happens after buying the opening print.

For each trading day, we track:
  - Entry at 9:30 open price (first bar open)
  - Entry at 9:31 (first 1-min bar close)
  - Minute-by-minute unrealized P&L (in SPX points and %)
  - Max favorable excursion (MFE) — best unrealized gain before any exit
  - Max adverse excursion (MAE) — worst unrealized drawdown before any exit
  - Where price is at various hold periods (5m, 15m, 30m, 1h, 2h, EOD)

Output:
  - baseline_stats.csv          (per-day summary)
  - baseline_pnl_curve.csv      (minute-by-minute avg P&L)
  - baseline_report.txt         (printed summary)

Usage:
    python 02_baseline_analysis.py
    (expects spx_1min_bars.csv in the same directory)
"""

import os
import csv
from collections import defaultdict
from statistics import mean, median, stdev

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
STATS_FILE = os.path.join(SCRIPT_DIR, "baseline_stats.csv")
PNL_CURVE_FILE = os.path.join(SCRIPT_DIR, "baseline_pnl_curve.csv")
REPORT_FILE = os.path.join(SCRIPT_DIR, "baseline_report.txt")

# RTH times (9:30 to 16:00 ET)
RTH_START = "09:30"
RTH_END = "16:00"

# Hold period checkpoints (minutes after open)
CHECKPOINTS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 390]


def load_data():
    """Load CSV into dict keyed by date, value = list of (time, o, h, l, c) sorted by time."""
    days = defaultdict(list)
    with open(INPUT_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = row["time"]
            # Only keep regular trading hours
            if t < RTH_START or t >= RTH_END:
                continue
            days[row["date"]].append({
                "time": t,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })

    # Sort each day's bars by time
    for d in days:
        days[d].sort(key=lambda x: x["time"])

    return days


def analyze_day(bars):
    """For a single day, compute entry prices and minute-by-minute tracking."""
    if len(bars) < 10:
        return None  # Skip partial days

    # Entry definitions
    entry_open = bars[0]["open"]      # 9:30 exact open
    entry_1min = bars[0]["close"]     # first 1-min bar close (9:31 effective)

    # Minute-by-minute P&L relative to each entry
    minutes_from_open = []
    pnl_from_open = []
    pnl_from_1min = []
    pct_from_open = []
    pct_from_1min = []

    # Track highs and lows for MFE/MAE
    running_high_from_open = entry_open
    running_low_from_open = entry_open
    running_high_from_1min = entry_1min
    running_low_from_1min = entry_1min

    mfe_open_points = 0
    mae_open_points = 0
    mfe_1min_points = 0
    mae_1min_points = 0

    for i, bar in enumerate(bars):
        mins = i  # minutes from 9:30

        # Use bar high/low to capture intra-bar extremes
        running_high_from_open = max(running_high_from_open, bar["high"])
        running_low_from_open = min(running_low_from_open, bar["low"])
        running_high_from_1min = max(running_high_from_1min, bar["high"])
        running_low_from_1min = min(running_low_from_1min, bar["low"])

        mfe_open_points = max(mfe_open_points, bar["high"] - entry_open)
        mae_open_points = min(mae_open_points, bar["low"] - entry_open)
        mfe_1min_points = max(mfe_1min_points, bar["high"] - entry_1min)
        mae_1min_points = min(mae_1min_points, bar["low"] - entry_1min)

        # P&L at bar close
        minutes_from_open.append(mins)
        pnl_from_open.append(bar["close"] - entry_open)
        pnl_from_1min.append(bar["close"] - entry_1min)
        pct_from_open.append((bar["close"] - entry_open) / entry_open * 100)
        pct_from_1min.append((bar["close"] - entry_1min) / entry_1min * 100)

    # Checkpoint P&Ls
    checkpoint_pnl_open = {}
    checkpoint_pnl_1min = {}
    checkpoint_pct_open = {}
    checkpoint_pct_1min = {}
    for cp in CHECKPOINTS:
        if cp < len(pnl_from_open):
            checkpoint_pnl_open[cp] = pnl_from_open[cp]
            checkpoint_pnl_1min[cp] = pnl_from_1min[cp]
            checkpoint_pct_open[cp] = pct_from_open[cp]
            checkpoint_pct_1min[cp] = pct_from_1min[cp]
        else:
            checkpoint_pnl_open[cp] = None
            checkpoint_pnl_1min[cp] = None
            checkpoint_pct_open[cp] = None
            checkpoint_pct_1min[cp] = None

    # EOD P&L
    eod_pnl_open = pnl_from_open[-1] if pnl_from_open else 0
    eod_pnl_1min = pnl_from_1min[-1] if pnl_from_1min else 0
    eod_pct_open = pct_from_open[-1] if pct_from_open else 0
    eod_pct_1min = pct_from_1min[-1] if pct_from_1min else 0

    return {
        "entry_open": entry_open,
        "entry_1min": entry_1min,
        "n_bars": len(bars),
        "eod_pnl_open": eod_pnl_open,
        "eod_pnl_1min": eod_pnl_1min,
        "eod_pct_open": eod_pct_open,
        "eod_pct_1min": eod_pct_1min,
        "mfe_open": mfe_open_points,
        "mae_open": mae_open_points,
        "mfe_1min": mfe_1min_points,
        "mae_1min": mae_1min_points,
        "checkpoint_pnl_open": checkpoint_pnl_open,
        "checkpoint_pnl_1min": checkpoint_pnl_1min,
        "checkpoint_pct_open": checkpoint_pct_open,
        "checkpoint_pct_1min": checkpoint_pct_1min,
        "pnl_curve_open": pnl_from_open,
        "pnl_curve_1min": pnl_from_1min,
        "pct_curve_open": pct_from_open,
        "pct_curve_1min": pct_from_1min,
    }


def compute_winrate_at_checkpoint(day_results, cp, entry_type="open"):
    """What % of days are positive at a given checkpoint?"""
    key = f"checkpoint_pnl_{entry_type}"
    vals = [d[key][cp] for d in day_results if d[key].get(cp) is not None]
    if not vals:
        return 0, 0, 0
    wins = sum(1 for v in vals if v > 0)
    return wins / len(vals) * 100, len(vals), mean(vals)


def main():
    print("Loading data...")
    days = load_data()
    print(f"Found {len(days)} trading days\n")

    print("Analyzing each day...")
    results = {}
    for date in sorted(days.keys()):
        r = analyze_day(days[date])
        if r:
            results[date] = r

    print(f"Analyzed {len(results)} valid trading days\n")

    # ── Per-day stats CSV ───────────────────────────────────────────────
    with open(STATS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["date", "entry_open", "entry_1min", "eod_pnl_open", "eod_pnl_1min",
                  "eod_pct_open", "eod_pct_1min", "mfe_open", "mae_open", "mfe_1min", "mae_1min"]
        for cp in CHECKPOINTS:
            header.extend([f"pnl_open_{cp}m", f"pnl_1min_{cp}m", f"pct_open_{cp}m", f"pct_1min_{cp}m"])
        writer.writerow(header)

        for date in sorted(results.keys()):
            r = results[date]
            row = [date, r["entry_open"], r["entry_1min"],
                   round(r["eod_pnl_open"], 2), round(r["eod_pnl_1min"], 2),
                   round(r["eod_pct_open"], 4), round(r["eod_pct_1min"], 4),
                   round(r["mfe_open"], 2), round(r["mae_open"], 2),
                   round(r["mfe_1min"], 2), round(r["mae_1min"], 2)]
            for cp in CHECKPOINTS:
                v1 = r["checkpoint_pnl_open"].get(cp)
                v2 = r["checkpoint_pnl_1min"].get(cp)
                v3 = r["checkpoint_pct_open"].get(cp)
                v4 = r["checkpoint_pct_1min"].get(cp)
                row.extend([
                    round(v1, 2) if v1 is not None else "",
                    round(v2, 2) if v2 is not None else "",
                    round(v3, 4) if v3 is not None else "",
                    round(v4, 4) if v4 is not None else "",
                ])
            writer.writerow(row)

    # ── Average P&L curve (minute by minute) ────────────────────────────
    max_bars = max(len(r["pnl_curve_open"]) for r in results.values())
    with open(PNL_CURVE_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["minutes_from_open", "avg_pnl_open", "avg_pnl_1min",
                         "avg_pct_open", "avg_pct_1min",
                         "median_pnl_open", "median_pnl_1min",
                         "pct_positive_open", "pct_positive_1min",
                         "n_days"])

        for m in range(max_bars):
            vals_open = [r["pnl_curve_open"][m] for r in results.values() if m < len(r["pnl_curve_open"])]
            vals_1min = [r["pnl_curve_1min"][m] for r in results.values() if m < len(r["pnl_curve_1min"])]
            pct_open = [r["pct_curve_open"][m] for r in results.values() if m < len(r["pct_curve_open"])]
            pct_1min = [r["pct_curve_1min"][m] for r in results.values() if m < len(r["pct_curve_1min"])]

            if not vals_open:
                continue

            pct_pos_open = sum(1 for v in vals_open if v > 0) / len(vals_open) * 100
            pct_pos_1min = sum(1 for v in vals_1min if v > 0) / len(vals_1min) * 100 if vals_1min else 0

            writer.writerow([
                m,
                round(mean(vals_open), 4),
                round(mean(vals_1min), 4) if vals_1min else "",
                round(mean(pct_open), 6),
                round(mean(pct_1min), 6) if pct_1min else "",
                round(median(vals_open), 4),
                round(median(vals_1min), 4) if vals_1min else "",
                round(pct_pos_open, 2),
                round(pct_pos_1min, 2) if vals_1min else "",
                len(vals_open),
            ])

    # ── Text report ─────────────────────────────────────────────────────
    all_eod_open = [r["eod_pnl_open"] for r in results.values()]
    all_eod_1min = [r["eod_pnl_1min"] for r in results.values()]
    all_eod_pct_open = [r["eod_pct_open"] for r in results.values()]
    all_eod_pct_1min = [r["eod_pct_1min"] for r in results.values()]
    all_mfe_open = [r["mfe_open"] for r in results.values()]
    all_mae_open = [r["mae_open"] for r in results.values()]
    all_mfe_1min = [r["mfe_1min"] for r in results.values()]
    all_mae_1min = [r["mae_1min"] for r in results.values()]

    report_lines = []
    def p(line=""):
        report_lines.append(line)

    p("=" * 70)
    p("SPX OPENING PRINT — BASELINE ANALYSIS")
    p("=" * 70)
    p(f"Trading days analyzed: {len(results)}")
    dates = sorted(results.keys())
    p(f"Date range: {dates[0]} to {dates[-1]}")
    p()

    # ── Entry at 9:30 Open ──────────────────────────────────────────────
    p("-" * 70)
    p("ENTRY: 9:30 OPEN PRICE")
    p("-" * 70)
    win_rate_eod = sum(1 for v in all_eod_open if v > 0) / len(all_eod_open) * 100
    p(f"EOD Win Rate:        {win_rate_eod:.1f}%")
    p(f"Avg EOD P&L:         {mean(all_eod_open):+.2f} pts  ({mean(all_eod_pct_open):+.4f}%)")
    p(f"Median EOD P&L:      {median(all_eod_open):+.2f} pts  ({median(all_eod_pct_open):+.4f}%)")
    p(f"StdDev EOD P&L:      {stdev(all_eod_open):.2f} pts")
    p(f"Avg MFE (best gain): {mean(all_mfe_open):+.2f} pts")
    p(f"Avg MAE (worst dd):  {mean(all_mae_open):+.2f} pts")
    p(f"Median MFE:          {median(all_mfe_open):+.2f} pts")
    p(f"Median MAE:          {median(all_mae_open):+.2f} pts")
    p()
    p("Win Rate & Avg P&L at Checkpoints (entry = 9:30 open):")
    p(f"  {'Hold':>8s}  {'WinRate':>8s}  {'Avg P&L':>10s}  {'N':>6s}")
    for cp in CHECKPOINTS:
        wr, n, avg = compute_winrate_at_checkpoint(list(results.values()), cp, "open")
        label = f"{cp}min" if cp < 60 else f"{cp//60}h{cp%60:02d}m" if cp % 60 else f"{cp//60}h"
        p(f"  {label:>8s}  {wr:>7.1f}%  {avg:>+10.2f}  {n:>6d}")
    p()

    # ── Entry at 1-min close ────────────────────────────────────────────
    p("-" * 70)
    p("ENTRY: FIRST 1-MIN BAR CLOSE (9:31)")
    p("-" * 70)
    win_rate_eod_1m = sum(1 for v in all_eod_1min if v > 0) / len(all_eod_1min) * 100
    p(f"EOD Win Rate:        {win_rate_eod_1m:.1f}%")
    p(f"Avg EOD P&L:         {mean(all_eod_1min):+.2f} pts  ({mean(all_eod_pct_1min):+.4f}%)")
    p(f"Median EOD P&L:      {median(all_eod_1min):+.2f} pts  ({median(all_eod_pct_1min):+.4f}%)")
    p(f"StdDev EOD P&L:      {stdev(all_eod_1min):.2f} pts")
    p(f"Avg MFE (best gain): {mean(all_mfe_1min):+.2f} pts")
    p(f"Avg MAE (worst dd):  {mean(all_mae_1min):+.2f} pts")
    p(f"Median MFE:          {median(all_mfe_1min):+.2f} pts")
    p(f"Median MAE:          {median(all_mae_1min):+.2f} pts")
    p()
    p("Win Rate & Avg P&L at Checkpoints (entry = 9:31 close):")
    p(f"  {'Hold':>8s}  {'WinRate':>8s}  {'Avg P&L':>10s}  {'N':>6s}")
    for cp in CHECKPOINTS:
        wr, n, avg = compute_winrate_at_checkpoint(list(results.values()), cp, "1min")
        label = f"{cp}min" if cp < 60 else f"{cp//60}h{cp%60:02d}m" if cp % 60 else f"{cp//60}h"
        p(f"  {label:>8s}  {wr:>7.1f}%  {avg:>+10.2f}  {n:>6d}")
    p()

    # ── MFE / MAE distribution ──────────────────────────────────────────
    p("-" * 70)
    p("MFE / MAE DISTRIBUTION (9:30 Open Entry)")
    p("-" * 70)

    mfe_buckets = [5, 10, 15, 20, 30, 50, 75, 100]
    mae_buckets = [-5, -10, -15, -20, -30, -50, -75, -100]

    p("% of days where MFE reached at least X points:")
    for b in mfe_buckets:
        pct = sum(1 for v in all_mfe_open if v >= b) / len(all_mfe_open) * 100
        p(f"  MFE >= {b:>3d} pts:  {pct:>6.1f}%")
    p()

    p("% of days where MAE reached at least X points:")
    for b in mae_buckets:
        pct = sum(1 for v in all_mae_open if v <= b) / len(all_mae_open) * 100
        p(f"  MAE <= {b:>4d} pts:  {pct:>6.1f}%")
    p()

    # ── Print & save ────────────────────────────────────────────────────
    report_text = "\n".join(report_lines)
    print(report_text)

    with open(REPORT_FILE, "w") as f:
        f.write(report_text)

    print(f"\nFiles saved:")
    print(f"  {STATS_FILE}")
    print(f"  {PNL_CURVE_FILE}")
    print(f"  {REPORT_FILE}")


if __name__ == "__main__":
    main()
