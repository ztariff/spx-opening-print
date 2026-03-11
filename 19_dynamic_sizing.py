"""
SPX Opening Print — Script 19: Dynamic Position Sizing Analysis
================================================================
Test various equity-curve-based sizing approaches:

1. Rolling P&L window (trailing N trades)
2. Rolling P&L window (trailing N calendar days)
3. Equity curve MA (above/below moving avg of equity)
4. Drawdown-based (reduce when in DD, full at new highs)
5. Win-rate adaptive (rolling win rate adjusts size)
6. Streak-based (consecutive W/L adjusts size)
7. Combined best-of approaches

Each approach scales the base premium between SIZE_DOWN and SIZE_UP
multipliers relative to the default get_max_premium(score).
"""

import json, os
from datetime import datetime, timedelta
from statistics import mean

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
trades = json.load(open(os.path.join(SCRIPT_DIR, "options_trades.json")))

# ── Helpers ──────────────────────────────────────────────────────────

def get_base_premium(score):
    if score < 25:   return 150000
    elif score < 40: return 155000
    elif score < 55: return 170000
    elif score < 70: return 175000
    elif score < 85: return 200000
    else:            return 200000

def simulate(trades, size_func, label):
    """
    Replay trades with dynamic sizing.
    size_func(trade_index, trades_so_far) -> multiplier (0.0 to 2.0+)
    Returns stats dict.
    """
    equity = 0
    peak = 0
    max_dd = 0
    pnls = []
    yearly = {}

    for i, t in enumerate(trades):
        mult = size_func(i, trades[:i], equity, peak)
        mult = max(0.0, mult)  # floor at 0 (skip)

        base_prem = get_base_premium(t["score"])
        new_prem = base_prem * mult

        # Scale P&L proportionally
        if t["opt_premium"] > 0:
            pnl_pct = t["opt_pnl"] / t["opt_premium"]
        else:
            pnl_pct = 0
        scaled_pnl = new_prem * pnl_pct

        equity += scaled_pnl
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        pnls.append(scaled_pnl)

        yr = t["date"][:4]
        yearly[yr] = yearly.get(yr, 0) + scaled_pnl

    wins = sum(1 for p in pnls if p > 0)
    total = len(pnls)
    avg_win = mean([p for p in pnls if p > 0]) if wins > 0 else 0
    avg_loss = mean([abs(p) for p in pnls if p <= 0]) if (total - wins) > 0 else 1

    return {
        "label": label,
        "total_pnl": equity,
        "max_dd": max_dd,
        "calmar": equity / max_dd if max_dd > 0 else 999,
        "trades": total,
        "win_rate": wins / total * 100 if total > 0 else 0,
        "profit_factor": (sum(p for p in pnls if p > 0) / sum(abs(p) for p in pnls if p <= 0)) if sum(abs(p) for p in pnls if p <= 0) > 0 else 999,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "yearly": yearly,
        "equity_curve": pnls,
    }

def print_result(r, baseline=None):
    print(f"\n{'='*70}")
    print(f"  {r['label']}")
    print(f"{'='*70}")
    print(f"  P&L:     ${r['total_pnl']:>12,.0f}   {'':>4}", end="")
    if baseline:
        delta = r['total_pnl'] - baseline['total_pnl']
        print(f"({'+' if delta>=0 else ''}{delta:,.0f} vs base)")
    else:
        print()
    print(f"  Max DD:  ${r['max_dd']:>12,.0f}   {'':>4}", end="")
    if baseline:
        delta = r['max_dd'] - baseline['max_dd']
        print(f"({'+' if delta>=0 else ''}{delta:,.0f} vs base)")
    else:
        print()
    print(f"  Calmar:  {r['calmar']:>13.2f}   {'':>4}", end="")
    if baseline:
        delta = r['calmar'] - baseline['calmar']
        print(f"({'+' if delta>=0 else ''}{delta:.2f} vs base)")
    else:
        print()
    print(f"  WR:      {r['win_rate']:>12.1f}%")
    print(f"  PF:      {r['profit_factor']:>13.2f}")
    print(f"  Avg W:   ${r['avg_win']:>12,.0f}")
    print(f"  Avg L:   ${r['avg_loss']:>12,.0f}")
    print(f"  Yearly:")
    for yr in sorted(r['yearly'].keys()):
        print(f"    {yr}: ${r['yearly'][yr]:>12,.0f}")

# ── Baseline (flat sizing) ──────────────────────────────────────────

def flat_size(i, hist, eq, peak):
    return 1.0

baseline = simulate(trades, flat_size, "BASELINE — Flat 1.0x sizing")
print_result(baseline)

results = [baseline]

# ══════════════════════════════════════════════════════════════════════
# APPROACH 1: Rolling P&L window (trailing N trades)
# ══════════════════════════════════════════════════════════════════════

for window, up, down in [(5, 1.3, 0.6), (10, 1.3, 0.6), (10, 1.5, 0.5),
                          (15, 1.3, 0.6), (20, 1.3, 0.6), (10, 1.3, 0.3),
                          (7, 1.3, 0.5), (10, 1.0, 0.5)]:
    def make_func(w, u, d):
        def f(i, hist, eq, peak):
            if i < w:
                return 1.0
            recent = [t["opt_pnl"] for t in hist[-w:]]
            return u if sum(recent) > 0 else d
        return f

    label = f"Rolling {window}-trade P&L: up={up}x, down={down}x"
    r = simulate(trades, make_func(window, up, down), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 2: Rolling calendar-day window
# ══════════════════════════════════════════════════════════════════════

for days, up, down in [(10, 1.3, 0.6), (14, 1.3, 0.6), (14, 1.5, 0.5),
                        (21, 1.3, 0.6), (14, 1.0, 0.5)]:
    def make_func(dd, u, d):
        def f(i, hist, eq, peak):
            if i < 3:
                return 1.0
            cutoff = datetime.strptime(trades[i]["date"], "%Y-%m-%d") - timedelta(days=dd)
            recent_pnl = sum(t["opt_pnl"] for t in hist
                           if datetime.strptime(t["date"], "%Y-%m-%d") >= cutoff)
            return u if recent_pnl > 0 else d
        return f

    label = f"Rolling {days}-day P&L: up={up}x, down={down}x"
    r = simulate(trades, make_func(days, up, down), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 3: Equity curve moving average
# ══════════════════════════════════════════════════════════════════════

for window, up, down in [(10, 1.3, 0.6), (15, 1.3, 0.6), (20, 1.3, 0.6),
                          (10, 1.5, 0.5), (20, 1.5, 0.5), (10, 1.0, 0.5),
                          (15, 1.0, 0.5)]:
    def make_func(w, u, d):
        eq_history = []
        running = [0]
        def f(i, hist, eq, peak):
            if i > 0:
                running[0] += hist[-1]["opt_pnl"]
            eq_history.append(running[0])
            if len(eq_history) < w:
                return 1.0
            ma = mean(eq_history[-w:])
            return u if running[0] >= ma else d
        return f

    label = f"Equity MA({window}): up={up}x, down={down}x"
    r = simulate(trades, make_func(window, up, down), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 4: Drawdown-based sizing
# ══════════════════════════════════════════════════════════════════════

for dd_thresh, up, down in [(50000, 1.3, 0.6), (100000, 1.3, 0.6),
                             (150000, 1.3, 0.5), (75000, 1.3, 0.5),
                             (100000, 1.5, 0.5), (50000, 1.0, 0.5),
                             (75000, 1.0, 0.5), (100000, 1.0, 0.3)]:
    def make_func(thresh, u, d):
        def f(i, hist, eq, peak):
            dd = peak - eq
            if dd > thresh:
                return d
            elif dd == 0 and eq > 0:  # at new high
                return u
            return 1.0
        return f

    label = f"DD-based (>{dd_thresh/1000:.0f}k→{down}x, new high→{up}x)"
    r = simulate(trades, make_func(dd_thresh, up, down), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 5: Rolling win-rate adaptive
# ══════════════════════════════════════════════════════════════════════

for window in [10, 15, 20]:
    def make_func(w):
        def f(i, hist, eq, peak):
            if i < w:
                return 1.0
            recent_wins = sum(1 for t in hist[-w:] if t["opt_pnl"] > 0)
            wr = recent_wins / w
            # Scale: 40% WR -> 0.5x, 50% -> 1.0x, 60% -> 1.5x
            return max(0.3, min(2.0, 0.5 + (wr - 0.4) * 5.0))
        return f

    label = f"Win-rate adaptive ({window}-trade window)"
    r = simulate(trades, make_func(window), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 6: Streak-based
# ══════════════════════════════════════════════════════════════════════

for win_streak_up, loss_streak_down in [(3, 3), (2, 2), (3, 2), (2, 3)]:
    for up, down in [(1.3, 0.6), (1.5, 0.5)]:
        def make_func(ws, ls, u, d):
            def f(i, hist, eq, peak):
                if i < max(ws, ls):
                    return 1.0
                # Count current streak
                streak = 0
                for t in reversed(hist):
                    if t["opt_pnl"] > 0:
                        if streak >= 0:
                            streak += 1
                        else:
                            break
                    else:
                        if streak <= 0:
                            streak -= 1
                        else:
                            break

                if streak >= ws:
                    return u
                elif streak <= -ls:
                    return d
                return 1.0
            return f

        label = f"Streak: {win_streak_up}W→{up}x, {loss_streak_down}L→{down}x"
        r = simulate(trades, make_func(win_streak_up, loss_streak_down, up, down), label)
        results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 7: Gradual scaling (not binary)
# ══════════════════════════════════════════════════════════════════════

for window in [10, 15, 20]:
    def make_func(w):
        def f(i, hist, eq, peak):
            if i < w:
                return 1.0
            recent_pnl = sum(t["opt_pnl"] for t in hist[-w:])
            recent_avg_prem = mean(t["opt_premium"] for t in hist[-w:])
            if recent_avg_prem == 0:
                return 1.0
            # Normalize: +100% of avg premium = 1.5x, -100% = 0.5x
            ratio = recent_pnl / (recent_avg_prem * w)
            mult = 1.0 + ratio * 2.0  # scale factor
            return max(0.3, min(2.0, mult))
        return f

    label = f"Gradual scale ({window}-trade rolling P&L)"
    r = simulate(trades, make_func(window), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 8: Combined — DD reduction + hot streak boost
# ══════════════════════════════════════════════════════════════════════

for dd_k, window, up, down in [(75, 10, 1.3, 0.5), (100, 10, 1.3, 0.5),
                                 (75, 7, 1.3, 0.5), (50, 10, 1.5, 0.5),
                                 (75, 10, 1.5, 0.3), (100, 15, 1.3, 0.5)]:
    def make_func(dk, w, u, d):
        def f(i, hist, eq, peak):
            dd = peak - eq
            # In drawdown? Size down regardless
            if dd > dk * 1000:
                return d
            # Not in DD — check recent momentum
            if i < w:
                return 1.0
            recent = sum(t["opt_pnl"] for t in hist[-w:])
            return u if recent > 0 else 1.0
        return f

    label = f"Combined: DD>{dd_k}k→{down}x + {window}-trade hot→{up}x"
    r = simulate(trades, make_func(dd_k, window, up, down), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 9: "Anti-tilt" — after big loss, reduce next N trades
# ══════════════════════════════════════════════════════════════════════

for big_loss, cooldown, down_mult in [(-50000, 3, 0.5), (-75000, 3, 0.5),
                                       (-50000, 5, 0.5), (-30000, 3, 0.5),
                                       (-50000, 3, 0.3), (-75000, 5, 0.3)]:
    def make_func(bl, cd, dm):
        def f(i, hist, eq, peak):
            if i < 1:
                return 1.0
            # Check last N trades for big loss
            lookback = hist[-cd:] if len(hist) >= cd else hist
            for t in lookback:
                if t["opt_pnl"] < bl:
                    return dm
            return 1.0
        return f

    label = f"Anti-tilt: loss<${big_loss/1000:.0f}k → {down_mult}x for {cooldown} trades"
    r = simulate(trades, make_func(big_loss, cooldown, down_mult), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# APPROACH 10: Tiered — multiple levels based on rolling P&L
# ══════════════════════════════════════════════════════════════════════

for window in [10, 15]:
    def make_func(w):
        def f(i, hist, eq, peak):
            if i < w:
                return 1.0
            recent = sum(t["opt_pnl"] for t in hist[-w:])
            recent_prem = mean(t["opt_premium"] for t in hist[-w:])
            if recent_prem == 0:
                return 1.0
            ratio = recent / (recent_prem * w)

            # Tiered:
            if ratio > 0.10:    return 1.5   # very hot
            elif ratio > 0.0:   return 1.2   # warm
            elif ratio > -0.10: return 0.8   # cool
            else:               return 0.5   # cold
        return f

    label = f"Tiered 4-level ({window}-trade window)"
    r = simulate(trades, make_func(window), label)
    results.append(r)

# ══════════════════════════════════════════════════════════════════════
# RANKING
# ══════════════════════════════════════════════════════════════════════

print("\n" + "="*80)
print("  TOP 15 BY CALMAR RATIO")
print("="*80)
ranked = sorted(results, key=lambda x: x["calmar"], reverse=True)
for i, r in enumerate(ranked[:15]):
    delta_pnl = r["total_pnl"] - baseline["total_pnl"]
    delta_dd = r["max_dd"] - baseline["max_dd"]
    print(f"\n  #{i+1}: {r['label']}")
    print(f"       P&L: ${r['total_pnl']:>10,.0f} ({'+' if delta_pnl>=0 else ''}{delta_pnl:,.0f})")
    print(f"       DD:  ${r['max_dd']:>10,.0f} ({'+' if delta_dd>=0 else ''}{delta_dd:,.0f})")
    print(f"       Calmar: {r['calmar']:.2f}  |  WR: {r['win_rate']:.1f}%  |  PF: {r['profit_factor']:.2f}")

print("\n" + "="*80)
print("  TOP 10 BY TOTAL P&L (with better Calmar than baseline)")
print("="*80)
ranked_pnl = sorted([r for r in results if r["calmar"] >= baseline["calmar"]],
                     key=lambda x: x["total_pnl"], reverse=True)
for i, r in enumerate(ranked_pnl[:10]):
    delta_pnl = r["total_pnl"] - baseline["total_pnl"]
    delta_dd = r["max_dd"] - baseline["max_dd"]
    print(f"\n  #{i+1}: {r['label']}")
    print(f"       P&L: ${r['total_pnl']:>10,.0f} ({'+' if delta_pnl>=0 else ''}{delta_pnl:,.0f})")
    print(f"       DD:  ${r['max_dd']:>10,.0f} ({'+' if delta_dd>=0 else ''}{delta_dd:,.0f})")
    print(f"       Calmar: {r['calmar']:.2f}  |  WR: {r['win_rate']:.1f}%  |  PF: {r['profit_factor']:.2f}")

print("\n" + "="*80)
print("  BEST OVERALL (highest Calmar with P&L >= 80% of baseline)")
print("="*80)
min_pnl = baseline["total_pnl"] * 0.80
best_overall = sorted([r for r in results if r["total_pnl"] >= min_pnl],
                       key=lambda x: x["calmar"], reverse=True)
for i, r in enumerate(best_overall[:10]):
    delta_pnl = r["total_pnl"] - baseline["total_pnl"]
    delta_dd = r["max_dd"] - baseline["max_dd"]
    print(f"\n  #{i+1}: {r['label']}")
    print(f"       P&L: ${r['total_pnl']:>10,.0f} ({'+' if delta_pnl>=0 else ''}{delta_pnl:,.0f})")
    print(f"       DD:  ${r['max_dd']:>10,.0f} ({'+' if delta_dd>=0 else ''}{delta_dd:,.0f})")
    print(f"       Calmar: {r['calmar']:.2f}  |  WR: {r['win_rate']:.1f}%  |  PF: {r['profit_factor']:.2f}")
    # Show yearly
    for yr in sorted(r['yearly'].keys()):
        print(f"         {yr}: ${r['yearly'][yr]:>10,.0f}")

print(f"\n\nBaseline reference: P&L ${baseline['total_pnl']:,.0f}, DD ${baseline['max_dd']:,.0f}, Calmar {baseline['calmar']:.2f}")
