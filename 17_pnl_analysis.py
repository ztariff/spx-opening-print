"""
Script 17: Deep P&L Analysis for Filter Optimization
=====================================================
Analyzes the extended backtest results (2018-2026) to find patterns
in losing periods and test filter combinations to improve the P&L curve.

Reads options_trades.json and produces analysis + tests.

Usage:
    python3 17_pnl_analysis.py
"""

import os, json, math
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = os.path.join(SCRIPT_DIR, "options_trades.json")


def load_trades():
    with open(TRADES_FILE) as f:
        return json.load(f)


def compute_stats(trades):
    if not trades:
        return None
    pnls = [t["opt_pnl"] for t in trades]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Calmar
    years = len(set(t["date"][:4] for t in trades))
    if years == 0:
        years = 1
    date_range = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
                  datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days / 365.25
    if date_range < 0.5:
        date_range = 0.5
    ann_return = total / date_range
    calmar = ann_return / max_dd if max_dd > 0 else 999

    return {
        "trades": len(trades),
        "total_pnl": total,
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "max_dd": max_dd,
        "calmar": calmar,
        "ann_return": ann_return,
        "best": max(pnls),
        "worst": min(pnls),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else 999,
    }


def print_stats(label, stats):
    if not stats:
        print(f"  {label}: No trades")
        return
    print(f"  {label:45s}  Trades:{stats['trades']:5d}  P&L:${stats['total_pnl']:>11,.0f}  "
          f"WR:{stats['wr']:5.1f}%  MaxDD:${stats['max_dd']:>10,.0f}  "
          f"Calmar:{stats['calmar']:6.2f}  PF:{stats['profit_factor']:.2f}")


def main():
    trades = load_trades()
    print(f"Loaded {len(trades)} trades from {trades[0]['date']} to {trades[-1]['date']}")

    # ── Section 1: Breakdown by year ──
    print("\n" + "=" * 120)
    print("YEARLY BREAKDOWN")
    print("=" * 120)
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["date"][:4]].append(t)
    for year in sorted(by_year.keys()):
        stats = compute_stats(by_year[year])
        print_stats(f"  {year}", stats)

    # ── Section 2: Breakdown by exit reason ──
    print("\n" + "=" * 120)
    print("BY EXIT REASON")
    print("=" * 120)
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.get("exit_reason", "unknown")].append(t)
    for reason in sorted(by_reason.keys()):
        stats = compute_stats(by_reason[reason])
        print_stats(f"  {reason}", stats)

    # ── Section 3: By DTE (0DTE vs 1DTE) ──
    print("\n" + "=" * 120)
    print("BY DTE")
    print("=" * 120)
    by_dte = defaultdict(list)
    for t in trades:
        by_dte[t.get("dte", 0)].append(t)
    for dte in sorted(by_dte.keys()):
        stats = compute_stats(by_dte[dte])
        print_stats(f"  {dte}DTE", stats)

    # ── Section 4: By VIX level ──
    print("\n" + "=" * 120)
    print("BY VIX LEVEL")
    print("=" * 120)
    vix_buckets = [(16, 18), (18, 20), (20, 25), (25, 30), (30, 40), (40, 100)]
    for lo, hi in vix_buckets:
        bucket_trades = [t for t in trades if t.get("vix") and lo <= t["vix"] < hi]
        stats = compute_stats(bucket_trades)
        print_stats(f"  VIX {lo}-{hi}", stats)

    # ── Section 5: By score bracket ──
    print("\n" + "=" * 120)
    print("BY SCORE BRACKET")
    print("=" * 120)
    score_buckets = [(-100, 0), (0, 15), (15, 30), (30, 45), (45, 60), (60, 80), (80, 200)]
    for lo, hi in score_buckets:
        bucket = [t for t in trades if lo <= t.get("score", 0) < hi]
        stats = compute_stats(bucket)
        print_stats(f"  Score {lo} to {hi}", stats)

    # ── Section 6: By day of week ──
    print("\n" + "=" * 120)
    print("BY DAY OF WEEK")
    print("=" * 120)
    by_dow = defaultdict(list)
    for t in trades:
        dow = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%A")
        by_dow[dow].append(t)
    for dow in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        if dow in by_dow:
            stats = compute_stats(by_dow[dow])
            print_stats(f"  {dow}", stats)

    # ── Section 7: By first bar direction ──
    print("\n" + "=" * 120)
    print("BY FIRST BAR DIRECTION (bail vs no-bail)")
    print("=" * 120)
    bullish = [t for t in trades if t.get("first_bar_bullish")]
    bearish = [t for t in trades if not t.get("first_bar_bullish")]
    print_stats("  Bullish 1st bar", compute_stats(bullish))
    print_stats("  Bearish 1st bar (bail trades)", compute_stats(bearish))

    # ── Section 8: By hold time ──
    print("\n" + "=" * 120)
    print("BY HOLD TIME")
    print("=" * 120)
    hold_buckets = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 120), (120, 240), (240, 500)]
    for lo, hi in hold_buckets:
        bucket = [t for t in trades if lo <= t.get("hold_mins", 0) < hi]
        stats = compute_stats(bucket)
        print_stats(f"  {lo}-{hi} min", stats)

    # ── Section 9: Pre-2023 vs Post-2023 ──
    print("\n" + "=" * 120)
    print("PRE-2023 vs POST-2023 (data source boundary)")
    print("=" * 120)
    pre = [t for t in trades if t["date"] < "2023-02-14"]
    post = [t for t in trades if t["date"] >= "2023-02-14"]
    print_stats("  Pre-2023 (SPY-calibrated)", compute_stats(pre))
    print_stats("  Post-2023 (native I:SPX)", compute_stats(post))

    # ── Section 10: Losing streaks ──
    print("\n" + "=" * 120)
    print("WORST LOSING STREAKS (5+ consecutive losses)")
    print("=" * 120)
    streak = 0
    streak_start = None
    streak_pnl = 0
    streaks = []
    for t in trades:
        if t["opt_pnl"] <= 0:
            if streak == 0:
                streak_start = t["date"]
            streak += 1
            streak_pnl += t["opt_pnl"]
        else:
            if streak >= 5:
                streaks.append((streak_start, t["date"], streak, streak_pnl))
            streak = 0
            streak_pnl = 0
    if streak >= 5:
        streaks.append((streak_start, trades[-1]["date"], streak, streak_pnl))

    streaks.sort(key=lambda x: x[3])
    for start, end, length, pnl in streaks[:10]:
        print(f"  {start} to {end}: {length} losses, P&L: ${pnl:,.0f}")

    # ══════════════════════════════════════════════════════════════════════
    # FILTER TESTS
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 120)
    print("FILTER OPTIMIZATION TESTS")
    print("=" * 120)

    all_stats = compute_stats(trades)
    print_stats("  BASELINE (current)", all_stats)
    print()

    results = []

    # Test: Higher VIX floors
    for vix_floor in [17, 18, 19, 20, 22, 25]:
        filtered = [t for t in trades if t.get("vix") and t["vix"] >= vix_floor]
        stats = compute_stats(filtered)
        if stats:
            results.append((f"VIX >= {vix_floor}", stats, filtered))
            print_stats(f"  VIX >= {vix_floor}", stats)

    # Test: Minimum score thresholds
    for min_score in [5, 10, 15, 20, 25, 30]:
        filtered = [t for t in trades if t.get("score", 0) >= min_score]
        stats = compute_stats(filtered)
        if stats:
            results.append((f"Score >= {min_score}", stats, filtered))
            print_stats(f"  Score >= {min_score}", stats)

    # Test: Skip specific DOW
    for skip_dow in ["Thursday", "Tuesday", "Wednesday"]:
        filtered = [t for t in trades
                    if datetime.strptime(t["date"], "%Y-%m-%d").strftime("%A") != skip_dow]
        stats = compute_stats(filtered)
        if stats:
            results.append((f"Skip {skip_dow}", stats, filtered))
            print_stats(f"  Skip {skip_dow}", stats)

    # Test: Only bullish first bar (no bail trades)
    filtered = [t for t in trades if t.get("first_bar_bullish")]
    stats = compute_stats(filtered)
    if stats:
        results.append(("Bullish only (no bail)", stats, filtered))
        print_stats("  Bullish only (no bail)", stats)

    # Test: VIX cap (skip very high VIX)
    for vix_cap in [30, 35, 40, 50]:
        filtered = [t for t in trades if t.get("vix") and t["vix"] <= vix_cap]
        stats = compute_stats(filtered)
        if stats:
            results.append((f"VIX <= {vix_cap}", stats, filtered))
            print_stats(f"  VIX <= {vix_cap}", stats)

    # Test: VIX band (floor + cap)
    for vix_lo, vix_hi in [(16, 30), (16, 35), (18, 30), (18, 35), (20, 35), (16, 40)]:
        filtered = [t for t in trades if t.get("vix") and vix_lo <= t["vix"] <= vix_hi]
        stats = compute_stats(filtered)
        if stats:
            results.append((f"VIX {vix_lo}-{vix_hi}", stats, filtered))
            print_stats(f"  VIX {vix_lo}-{vix_hi}", stats)

    # Test: Skip 1DTE trades
    filtered = [t for t in trades if t.get("dte", 0) == 0]
    stats = compute_stats(filtered)
    if stats:
        results.append(("0DTE only", stats, filtered))
        print_stats("  0DTE only", stats)

    # Combined filters
    print("\n  --- COMBINED FILTERS ---")
    combos = [
        ("VIX 18+ & Score 10+", lambda t: t.get("vix", 0) >= 18 and t.get("score", 0) >= 10),
        ("VIX 18+ & Score 15+", lambda t: t.get("vix", 0) >= 18 and t.get("score", 0) >= 15),
        ("VIX 20+ & Score 10+", lambda t: t.get("vix", 0) >= 20 and t.get("score", 0) >= 10),
        ("VIX 16-30 & Score 10+", lambda t: 16 <= t.get("vix", 0) <= 30 and t.get("score", 0) >= 10),
        ("VIX 16-35 & Score 10+", lambda t: 16 <= t.get("vix", 0) <= 35 and t.get("score", 0) >= 10),
        ("VIX 16-35 & Score 15+", lambda t: 16 <= t.get("vix", 0) <= 35 and t.get("score", 0) >= 15),
        ("VIX 18-35 & Bullish only", lambda t: 18 <= t.get("vix", 0) <= 35 and t.get("first_bar_bullish")),
        ("VIX 16+ & Skip Thursday", lambda t: t.get("vix", 0) >= 16 and datetime.strptime(t["date"], "%Y-%m-%d").strftime("%A") != "Thursday"),
        ("VIX 18+ & Skip Thursday", lambda t: t.get("vix", 0) >= 18 and datetime.strptime(t["date"], "%Y-%m-%d").strftime("%A") != "Thursday"),
        ("VIX 16-35 & Skip Thu & Score 10+", lambda t: 16 <= t.get("vix", 0) <= 35 and t.get("score", 0) >= 10 and datetime.strptime(t["date"], "%Y-%m-%d").strftime("%A") != "Thursday"),
        ("VIX 16+ & 0DTE only", lambda t: t.get("vix", 0) >= 16 and t.get("dte", 0) == 0),
        ("VIX 18+ & 0DTE only", lambda t: t.get("vix", 0) >= 18 and t.get("dte", 0) == 0),
        ("VIX 16-30 & 0DTE & Score 10+", lambda t: 16 <= t.get("vix", 0) <= 30 and t.get("dte", 0) == 0 and t.get("score", 0) >= 10),
    ]

    for label, fn in combos:
        filtered = [t for t in trades if fn(t)]
        stats = compute_stats(filtered)
        if stats and stats["trades"] >= 50:
            results.append((label, stats, filtered))
            print_stats(f"  {label}", stats)

    # ── Rank by Calmar ──
    print("\n" + "=" * 120)
    print("TOP 10 BY CALMAR RATIO (min 100 trades)")
    print("=" * 120)
    ranked = [(label, s) for label, s, _ in results if s["trades"] >= 100]
    ranked.sort(key=lambda x: x[1]["calmar"], reverse=True)
    for i, (label, stats) in enumerate(ranked[:10]):
        print_stats(f"  #{i+1} {label}", stats)

    print("\n" + "=" * 120)
    print("TOP 10 BY TOTAL P&L (min 100 trades)")
    print("=" * 120)
    ranked_pnl = [(label, s) for label, s, _ in results if s["trades"] >= 100]
    ranked_pnl.sort(key=lambda x: x[1]["total_pnl"], reverse=True)
    for i, (label, stats) in enumerate(ranked_pnl[:10]):
        print_stats(f"  #{i+1} {label}", stats)


if __name__ == "__main__":
    main()
