"""
SPX Opening Print Strategy — Phase 5: Combined Condition Stacking
==================================================================
Tests combinations of the strongest individual edge conditions to find
setups where multiple factors align. Also tests "anti-setups" to confirm
when NOT to trade.

Key individual edges identified in Phase 3/4:
  POSITIVE:
    - Bullish first bar (59.5% WR, +3.71 avg, 6.65 PF with tight stop)
    - Monday (65% WR, +8.94 avg)
    - After 3+ down days (54.9% WR, +8.04 avg, 3.03 PF)
    - Gap up (54.6% WR, +1.43 avg, 4.18 PF with tight stop)
    - Lower 20d range 10-30% (61.8% WR, +17.24 avg)
    - Prior day very large down >1% (54.5% WR, +13.72 avg)
    - Prior day close near low 0-25% (56.5% WR, +5.21 avg)
    - June / November (61.7%/65% WR)
    - 2nd week of month (57.2% WR, +5.70 avg)
    - Very wide prior range >1.5% (59.2% WR, +8.69 avg)

  NEGATIVE:
    - Bearish first bar (46.9% WR, -1.64 avg, 0.23 PF)
    - Thursday (44.4% WR, -7.63 avg)
    - Near 20d low 0-10% (45.7% WR, -1.56 avg)
    - Gap down + large (35.4% WR, -5.58 avg)
    - 3rd week of month (49.4% WR, -3.41 avg)

This script:
  1. Tests every 2-condition and 3-condition combo from the positive set
  2. Runs the exit grid search on combos with N >= 20
  3. Reports the best stacked setups with full stats
  4. Reports the best "avoid" signals
  5. Builds a final ranked strategy table

Requires: spx_1min_bars.csv from Phase 1

Usage:
    python 05_combined_conditions.py
"""

import os
import csv
from collections import defaultdict
from statistics import mean, median, stdev
from datetime import datetime
from itertools import combinations

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
REPORT_FILE = os.path.join(SCRIPT_DIR, "combined_conditions_report.txt")
COMBOS_CSV = os.path.join(SCRIPT_DIR, "combined_conditions_results.csv")
STRATEGY_CSV = os.path.join(SCRIPT_DIR, "final_strategy_table.csv")

PROFIT_TARGETS = [2, 5, 8, 10, 15, 20, 30, 50]
STOP_LOSSES = [2, 5, 8, 10, 15, 20]
TIME_STOPS = [15, 30, 60, 120, 240, 390]

report_lines = []


def p(line=""):
    report_lines.append(line)
    print(line)


def load_intraday():
    """Load 1-min bars grouped by date."""
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


def build_context(intraday):
    """Build per-day context with all condition flags."""
    dates_sorted = sorted(intraday.keys())
    date_idx = {d: i for i, d in enumerate(dates_sorted)}
    daily = {}

    for d in dates_sorted:
        bars = intraday[d]
        if len(bars) < 10:
            continue

        ctx = {"date": d, "bars": bars}
        dt = datetime.strptime(d, "%Y-%m-%d")
        idx = date_idx[d]

        # ── Basic fields ────────────────────────────────────────────
        ctx["entry_open"] = bars[0]["open"]
        ctx["entry_1min"] = bars[0]["close"]
        ctx["day_of_week"] = dt.strftime("%A")
        ctx["month"] = dt.month
        ctx["day_of_month"] = dt.day
        ctx["week_of_month"] = "1st" if dt.day <= 7 else "2nd" if dt.day <= 14 else "3rd" if dt.day <= 21 else "4th+"

        # ── First bar direction ─────────────────────────────────────
        if bars[0]["close"] > bars[0]["open"]:
            ctx["first_bar"] = "bullish"
        elif bars[0]["close"] < bars[0]["open"]:
            ctx["first_bar"] = "bearish"
        else:
            ctx["first_bar"] = "doji"

        # ── Prior day data ──────────────────────────────────────────
        if idx > 0:
            prev_date = dates_sorted[idx - 1]
            prev_bars = intraday.get(prev_date, [])
            if prev_bars and len(prev_bars) >= 10:
                prev_open = prev_bars[0]["open"]
                prev_close = prev_bars[-1]["close"]
                prev_high = max(b["high"] for b in prev_bars)
                prev_low = min(b["low"] for b in prev_bars)

                # Gap
                gap = bars[0]["open"] - prev_close
                gap_pct = gap / prev_close * 100
                ctx["gap_dir"] = "up" if gap > 0 else "down"
                ctx["gap_pct"] = gap_pct
                abs_gap = abs(gap_pct)
                if abs_gap < 0.1:
                    ctx["gap_size"] = "tiny"
                elif abs_gap < 0.3:
                    ctx["gap_size"] = "small"
                elif abs_gap < 0.5:
                    ctx["gap_size"] = "medium"
                elif abs_gap < 1.0:
                    ctx["gap_size"] = "large"
                else:
                    ctx["gap_size"] = "huge"

                # Prior day return
                prev_ret = (prev_close - prev_open) / prev_open * 100
                ctx["prev_day_dir"] = "up" if prev_ret > 0.1 else "down" if prev_ret < -0.1 else "flat"
                ctx["prev_day_ret_pct"] = prev_ret
                abs_ret = abs(prev_ret)
                ctx["prev_day_big_down"] = prev_ret < -1.0
                ctx["prev_day_big_up"] = prev_ret > 1.0

                # Prior day range
                prev_range_pct = (prev_high - prev_low) / prev_close * 100
                ctx["prev_range_pct"] = prev_range_pct
                ctx["prev_range_wide"] = prev_range_pct > 1.5
                ctx["prev_range_tight"] = prev_range_pct < 0.5

                # Prior close location
                if prev_high != prev_low:
                    close_loc = (prev_close - prev_low) / (prev_high - prev_low)
                    ctx["prev_close_near_low"] = close_loc < 0.25
                    ctx["prev_close_near_high"] = close_loc >= 0.75

        # ── Streak ──────────────────────────────────────────────────
        streak = 0
        for j in range(idx - 1, max(idx - 15, -1), -1):
            if j < 0:
                break
            sd = dates_sorted[j]
            sb = intraday.get(sd, [])
            if sb and len(sb) >= 10:
                day_ret = sb[-1]["close"] - sb[0]["open"]
                if streak == 0:
                    streak = 1 if day_ret > 0 else -1
                elif streak > 0 and day_ret > 0:
                    streak += 1
                elif streak < 0 and day_ret < 0:
                    streak -= 1
                else:
                    break
        ctx["streak"] = streak
        ctx["streak_3plus_down"] = streak <= -3
        ctx["streak_3plus_up"] = streak >= 3

        # ── 20-day range position ───────────────────────────────────
        if idx >= 20:
            lookback = dates_sorted[idx-20:idx]
            highs = []
            lows = []
            for ld in lookback:
                lb = intraday.get(ld, [])
                if lb:
                    highs.append(max(b["high"] for b in lb))
                    lows.append(min(b["low"] for b in lb))
            if highs and lows:
                h20 = max(highs)
                l20 = min(lows)
                if h20 != l20:
                    pct_in = (bars[0]["open"] - l20) / (h20 - l20) * 100
                    ctx["range_20d_pct"] = pct_in
                    if pct_in >= 90:
                        ctx["range_pos"] = "near_high"
                    elif pct_in >= 70:
                        ctx["range_pos"] = "upper"
                    elif pct_in >= 30:
                        ctx["range_pos"] = "mid"
                    elif pct_in >= 10:
                        ctx["range_pos"] = "lower"
                    else:
                        ctx["range_pos"] = "near_low"
                    ctx["range_lower"] = 10 <= pct_in < 30

        # ── EOD P&L (for quick stats without grid search) ──────────
        eod_close = bars[-1]["close"]
        ctx["eod_pnl_open"] = eod_close - bars[0]["open"]
        ctx["eod_pnl_1min"] = eod_close - bars[0]["close"]

        daily[d] = ctx

    return daily


def simulate_trade(bars, entry_price, pt, sl, ts, entry_idx=0):
    """Simulate long trade. Returns (pnl, reason, hold_mins)."""
    for i in range(entry_idx + 1, min(entry_idx + ts + 1, len(bars))):
        bar = bars[i]
        mins = i - entry_idx
        if bar["low"] <= entry_price - sl:
            return (-sl, "sl", mins)
        if bar["high"] >= entry_price + pt:
            return (pt, "pt", mins)
    last = min(entry_idx + ts, len(bars) - 1)
    return (bars[last]["close"] - entry_price, "ts", last - entry_idx)


def grid_search(days_list):
    """Run grid search, return best result by avg P&L and best by PF."""
    best_pnl = None
    best_pf = None

    for pt in PROFIT_TARGETS:
        for sl in STOP_LOSSES:
            for ts in TIME_STOPS:
                pnls = []
                holds = []
                for day in days_list:
                    pnl, reason, hold = simulate_trade(
                        day["bars"], day["entry_open"], pt, sl, ts
                    )
                    pnls.append(pnl)
                    holds.append(hold)

                if not pnls:
                    continue

                wins = [x for x in pnls if x > 0]
                losses = [x for x in pnls if x <= 0]
                wr = len(wins) / len(pnls) * 100
                gw = sum(wins) if wins else 0
                gl = abs(sum(losses)) if losses else 0.01
                pf = gw / gl
                avg = mean(pnls)

                result = {
                    "pt": pt, "sl": sl, "ts": ts,
                    "n": len(pnls), "wr": round(wr, 1),
                    "avg_pnl": round(avg, 2), "total_pnl": round(sum(pnls), 1),
                    "pf": round(pf, 2), "avg_hold": round(mean(holds), 1),
                    "median_pnl": round(median(pnls), 2),
                }

                if best_pnl is None or avg > best_pnl["avg_pnl"]:
                    best_pnl = result
                if wr >= 35 and (best_pf is None or pf > best_pf["pf"]):
                    best_pf = result

    return best_pnl, best_pf


# ── Define condition filters ───────────────────────────────────────────

POSITIVE_CONDITIONS = {
    "bullish_1st_bar": lambda d: d.get("first_bar") == "bullish",
    "monday": lambda d: d.get("day_of_week") == "Monday",
    "gap_up": lambda d: d.get("gap_dir") == "up",
    "3+_down_streak": lambda d: d.get("streak_3plus_down", False),
    "lower_20d_range": lambda d: d.get("range_lower", False),
    "prev_big_down": lambda d: d.get("prev_day_big_down", False),
    "prev_close_near_low": lambda d: d.get("prev_close_near_low", False),
    "wide_prev_range": lambda d: d.get("prev_range_wide", False),
    "2nd_week": lambda d: d.get("week_of_month") == "2nd",
}

NEGATIVE_CONDITIONS = {
    "bearish_1st_bar": lambda d: d.get("first_bar") == "bearish",
    "thursday": lambda d: d.get("day_of_week") == "Thursday",
    "near_20d_low": lambda d: d.get("range_pos") == "near_low",
    "gap_down_large": lambda d: d.get("gap_dir") == "down" and d.get("gap_size") in ("large", "huge"),
    "3rd_week": lambda d: d.get("week_of_month") == "3rd",
}


def test_combo(all_days, conditions, label):
    """Test a specific combination of conditions."""
    filtered = [d for d in all_days if all(cond(d) for cond in conditions)]
    n = len(filtered)
    if n < 15:
        return None

    eod_pnls = [d["eod_pnl_open"] for d in filtered]
    wins = sum(1 for x in eod_pnls if x > 0)
    wr = wins / n * 100

    # Quick grid search for best exits
    best_pnl, best_pf = grid_search(filtered)

    return {
        "label": label,
        "n": n,
        "wr_eod": round(wr, 1),
        "avg_eod": round(mean(eod_pnls), 2),
        "median_eod": round(median(eod_pnls), 2),
        "stdev_eod": round(stdev(eod_pnls), 2) if n > 1 else 0,
        "best_pnl": best_pnl,
        "best_pf": best_pf,
    }


def main():
    p("Loading 1-min data...")
    intraday = load_intraday()
    p("Building day context...")
    daily = build_context(intraday)
    all_days = list(daily.values())
    p(f"Total trading days: {len(all_days)}\n")

    all_results = []

    # ── 1. Single positive conditions (recap) ──────────────────────────
    p("=" * 80)
    p("SINGLE POSITIVE CONDITIONS (with best exits)")
    p("=" * 80)
    p(f"  {'Condition':<30s} {'N':>5s} {'WR%':>6s} {'AvgEOD':>8s} {'BestPT':>6s} {'BestSL':>6s} {'BestTS':>6s} {'BestAvg':>8s} {'BestPF':>7s}")

    for name, cond in POSITIVE_CONDITIONS.items():
        result = test_combo(all_days, [cond], name)
        if result:
            bp = result["best_pnl"]
            p(f"  {name:<30s} {result['n']:>5d} {result['wr_eod']:>5.1f}% {result['avg_eod']:>+8.2f} "
              f"{bp['pt']:>6d} {bp['sl']:>6d} {bp['ts']:>6d} {bp['avg_pnl']:>+8.2f} {bp['pf']:>7.2f}")
            all_results.append(result)

    # ── 2. All 2-condition combos ──────────────────────────────────────
    p(f"\n{'='*80}")
    p("TWO-CONDITION COMBOS (positive + positive)")
    p(f"{'='*80}")
    p(f"  {'Combo':<45s} {'N':>5s} {'WR%':>6s} {'AvgEOD':>8s} {'BestAvg':>8s} {'BestPF':>7s} {'PT':>4s} {'SL':>4s} {'TS':>5s}")

    cond_names = list(POSITIVE_CONDITIONS.keys())
    two_combos = []
    for i, j in combinations(range(len(cond_names)), 2):
        n1, n2 = cond_names[i], cond_names[j]
        c1, c2 = POSITIVE_CONDITIONS[n1], POSITIVE_CONDITIONS[n2]
        label = f"{n1} + {n2}"
        result = test_combo(all_days, [c1, c2], label)
        if result:
            two_combos.append(result)

    # Sort by avg P&L from best exit
    two_combos.sort(key=lambda x: x["best_pnl"]["avg_pnl"] if x["best_pnl"] else 0, reverse=True)

    for r in two_combos:
        bp = r["best_pnl"]
        if bp:
            p(f"  {r['label']:<45s} {r['n']:>5d} {r['wr_eod']:>5.1f}% {r['avg_eod']:>+8.2f} "
              f"{bp['avg_pnl']:>+8.2f} {bp['pf']:>7.2f} {bp['pt']:>4d} {bp['sl']:>4d} {bp['ts']:>5d}")
        all_results.append(r)

    # ── 3. Three-condition combos (only test top 2-combos + others) ────
    p(f"\n{'='*80}")
    p("THREE-CONDITION COMBOS")
    p(f"{'='*80}")
    p(f"  {'Combo':<60s} {'N':>4s} {'WR%':>6s} {'AvgEOD':>8s} {'BestAvg':>8s} {'PF':>6s}")

    three_combos = []
    for i, j, k in combinations(range(len(cond_names)), 3):
        n1, n2, n3 = cond_names[i], cond_names[j], cond_names[k]
        c1, c2, c3 = POSITIVE_CONDITIONS[n1], POSITIVE_CONDITIONS[n2], POSITIVE_CONDITIONS[n3]
        label = f"{n1} + {n2} + {n3}"
        result = test_combo(all_days, [c1, c2, c3], label)
        if result:
            three_combos.append(result)

    three_combos.sort(key=lambda x: x["best_pnl"]["avg_pnl"] if x["best_pnl"] else 0, reverse=True)

    for r in three_combos[:30]:  # Top 30
        bp = r["best_pnl"]
        if bp:
            p(f"  {r['label']:<60s} {r['n']:>4d} {r['wr_eod']:>5.1f}% {r['avg_eod']:>+8.2f} "
              f"{bp['avg_pnl']:>+8.2f} {bp['pf']:>6.2f}")
        all_results.append(r)

    # ── 4. Negative conditions & "don't trade" combos ──────────────────
    p(f"\n{'='*80}")
    p("NEGATIVE / DON'T-TRADE CONDITIONS")
    p(f"{'='*80}")
    p(f"  {'Condition':<45s} {'N':>5s} {'WR%':>6s} {'AvgEOD':>8s}")

    for name, cond in NEGATIVE_CONDITIONS.items():
        filtered = [d for d in all_days if cond(d)]
        if len(filtered) >= 10:
            eod = [d["eod_pnl_open"] for d in filtered]
            wr = sum(1 for x in eod if x > 0) / len(eod) * 100
            p(f"  {name:<45s} {len(filtered):>5d} {wr:>5.1f}% {mean(eod):>+8.2f}")

    # Negative combos
    p(f"\n  --- Negative combo stacks ---")
    neg_names = list(NEGATIVE_CONDITIONS.keys())
    for i, j in combinations(range(len(neg_names)), 2):
        n1, n2 = neg_names[i], neg_names[j]
        c1, c2 = NEGATIVE_CONDITIONS[n1], NEGATIVE_CONDITIONS[n2]
        filtered = [d for d in all_days if c1(d) and c2(d)]
        if len(filtered) >= 10:
            eod = [d["eod_pnl_open"] for d in filtered]
            wr = sum(1 for x in eod if x > 0) / len(eod) * 100
            label = f"{n1} + {n2}"
            p(f"  {label:<45s} {len(filtered):>5d} {wr:>5.1f}% {mean(eod):>+8.2f}")

    # ── 5. Positive with negative filter removed ───────────────────────
    p(f"\n{'='*80}")
    p("POSITIVE CONDITIONS WITH NEGATIVE FILTERS REMOVED")
    p(f"{'='*80}")
    p(f"  {'Setup':<55s} {'N':>4s} {'WR%':>6s} {'AvgEOD':>8s} {'BestAvg':>8s} {'PF':>6s}")

    # Take top positive combos and add "NOT bearish first bar" and "NOT thursday"
    no_bearish = lambda d: d.get("first_bar") != "bearish"
    no_thursday = lambda d: d.get("day_of_week") != "Thursday"
    no_negatives = lambda d: no_bearish(d) and no_thursday(d)

    # Test key setups with negatives removed
    key_setups = [
        ("bullish_1st_bar", [POSITIVE_CONDITIONS["bullish_1st_bar"]]),
        ("bullish_1st_bar + gap_up", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["gap_up"]]),
        ("bullish_1st_bar + monday", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["monday"]]),
        ("bullish_1st_bar + 3+_down_streak", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["3+_down_streak"]]),
        ("bullish_1st_bar + wide_prev_range", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["wide_prev_range"]]),
        ("bullish_1st_bar + prev_big_down", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["prev_big_down"]]),
        ("bullish_1st_bar + lower_20d_range", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["lower_20d_range"]]),
        ("bullish_1st_bar + prev_close_near_low", [POSITIVE_CONDITIONS["bullish_1st_bar"], POSITIVE_CONDITIONS["prev_close_near_low"]]),
    ]

    for name, conds in key_setups:
        # With no-negatives overlay
        full_label = f"{name} (no Thu/no bear)"
        all_conds = conds + [no_negatives]
        result = test_combo(all_days, all_conds, full_label)
        if result:
            bp = result["best_pnl"]
            if bp:
                p(f"  {full_label:<55s} {result['n']:>4d} {result['wr_eod']:>5.1f}% {result['avg_eod']:>+8.2f} "
                  f"{bp['avg_pnl']:>+8.2f} {bp['pf']:>6.2f}")
            all_results.append(result)

    # ── 6. Final strategy ranking ──────────────────────────────────────
    p(f"\n\n{'='*80}")
    p("FINAL STRATEGY RANKING")
    p("(sorted by best avg P&L from grid search, minimum 15 days)")
    p(f"{'='*80}")

    ranked = [r for r in all_results if r and r.get("best_pnl")]
    ranked.sort(key=lambda x: x["best_pnl"]["avg_pnl"], reverse=True)

    p(f"\n  {'#':>3s} {'Setup':<55s} {'N':>4s} {'EOD_WR':>7s} {'BestAvg':>8s} {'PF':>6s} {'PT':>4s} {'SL':>4s} {'TS':>5s} {'AvgHld':>7s}")
    p(f"  {'---':>3s} {'-'*55} {'----':>4s} {'-------':>7s} {'--------':>8s} {'------':>6s} {'----':>4s} {'----':>4s} {'-----':>5s} {'-------':>7s}")

    strategy_rows = []
    for i, r in enumerate(ranked[:40]):
        bp = r["best_pnl"]
        p(f"  {i+1:>3d} {r['label']:<55s} {r['n']:>4d} {r['wr_eod']:>6.1f}% {bp['avg_pnl']:>+8.2f} "
          f"{bp['pf']:>6.2f} {bp['pt']:>4d} {bp['sl']:>4d} {bp['ts']:>5d} {bp['avg_hold']:>6.1f}m")
        strategy_rows.append({
            "rank": i+1,
            "setup": r["label"],
            "n_days": r["n"],
            "eod_winrate": r["wr_eod"],
            "eod_avg_pnl": r["avg_eod"],
            "best_avg_pnl": bp["avg_pnl"],
            "best_pf": bp["pf"],
            "best_pt": bp["pt"],
            "best_sl": bp["sl"],
            "best_ts": bp["ts"],
            "best_wr": bp["wr"],
            "best_total_pnl": bp["total_pnl"],
            "avg_hold_min": bp["avg_hold"],
        })

    # ── Save files ──────────────────────────────────────────────────────
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_lines))

    if strategy_rows:
        with open(STRATEGY_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=strategy_rows[0].keys())
            writer.writeheader()
            writer.writerows(strategy_rows)

    # Also save full combo results
    combo_rows = []
    for r in all_results:
        if r and r.get("best_pnl"):
            bp = r["best_pnl"]
            combo_rows.append({
                "label": r["label"],
                "n": r["n"],
                "wr_eod": r["wr_eod"],
                "avg_eod": r["avg_eod"],
                "median_eod": r["median_eod"],
                "best_avg_pnl": bp["avg_pnl"],
                "best_pf": bp["pf"],
                "best_pt": bp["pt"],
                "best_sl": bp["sl"],
                "best_ts": bp["ts"],
                "best_wr": bp["wr"],
                "total_pnl": bp["total_pnl"],
                "avg_hold": bp["avg_hold"],
            })

    if combo_rows:
        with open(COMBOS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=combo_rows[0].keys())
            writer.writeheader()
            writer.writerows(combo_rows)

    p(f"\nFiles saved:")
    p(f"  {REPORT_FILE}")
    p(f"  {COMBOS_CSV}")
    p(f"  {STRATEGY_CSV}")


if __name__ == "__main__":
    main()
