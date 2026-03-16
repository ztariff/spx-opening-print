"""
Script 14: Filter Optimization for Options Strategy
Tests various filters to improve P&L curve:
  - VIX floor
  - Bearish 1st bar skip threshold
  - Score minimum
  - Combined filters
  - Equity curves for comparison
"""

import json
from datetime import datetime
from collections import defaultdict

with open("options_trades.json") as f:
    trades = json.load(f)

# Sort by date
trades.sort(key=lambda t: t["date"])

def evaluate_filter(filtered, label):
    """Compute stats for a filtered trade set."""
    if not filtered:
        return None
    total_pnl = sum(t["opt_pnl"] for t in filtered)
    wins = [t for t in filtered if t["opt_pnl"] > 0]
    losses = [t for t in filtered if t["opt_pnl"] <= 0]
    wr = len(wins) / len(filtered) * 100
    avg_win = sum(t["opt_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["opt_pnl"] for t in losses) / len(losses) if losses else 0

    # Max drawdown & equity curve
    cum = 0
    peak = 0
    max_dd = 0
    worst_dd_end = None
    equity = []
    for t in filtered:
        cum += t["opt_pnl"]
        equity.append(cum)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            worst_dd_end = t["date"]

    # Profit factor
    gross_wins = sum(t["opt_pnl"] for t in wins) if wins else 0
    gross_losses = abs(sum(t["opt_pnl"] for t in losses)) if losses else 1
    pf = gross_wins / gross_losses if gross_losses > 0 else 999

    # Monthly P&L consistency
    monthly = defaultdict(float)
    for t in filtered:
        m = t["date"][:7]
        monthly[m] += t["opt_pnl"]
    pos_months = len([v for v in monthly.values() if v > 0])
    neg_months = len([v for v in monthly.values() if v <= 0])
    
    # Calmar ratio (annualized return / max DD)
    days = (datetime.strptime(filtered[-1]["date"], "%Y-%m-%d") - 
            datetime.strptime(filtered[0]["date"], "%Y-%m-%d")).days
    years = days / 365.25 if days > 0 else 1
    annual_return = total_pnl / years
    calmar = annual_return / max_dd if max_dd > 0 else 999

    return {
        "label": label,
        "trades": len(filtered),
        "wr": wr,
        "pnl": total_pnl,
        "avg_trade": total_pnl / len(filtered),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_dd": max_dd,
        "pf": pf,
        "calmar": calmar,
        "pos_months": pos_months,
        "neg_months": neg_months,
        "annual": annual_return,
        "equity": equity,
    }

print("=" * 100)
print("FILTER OPTIMIZATION — OPTIONS STRATEGY")
print("=" * 100)

results = []

# Baseline
results.append(evaluate_filter(trades, "BASELINE (no filter)"))

# === VIX FILTERS ===
for vix_min in [14, 15, 16, 17, 18]:
    f = [t for t in trades if t.get("vix") and t["vix"] >= vix_min]
    results.append(evaluate_filter(f, f"VIX >= {vix_min}"))

# === SCORE FILTERS ===
for score_min in [5, 10, 15, 20]:
    f = [t for t in trades if t["score"] >= score_min]
    results.append(evaluate_filter(f, f"Score >= {score_min}"))

# === BEARISH 1ST BAR THRESHOLD ===
# Only take bearish 1st bar trades when score >= X
for bear_thresh in [40, 50, 55, 60, 70]:
    f = [t for t in trades if t["first_bar_bullish"] or t["score"] >= bear_thresh]
    results.append(evaluate_filter(f, f"Bearish only if score >= {bear_thresh}"))

# === COMBINED FILTERS ===
# VIX + score
for vix_min in [15, 16]:
    for score_min in [10, 15]:
        f = [t for t in trades if t.get("vix") and t["vix"] >= vix_min and t["score"] >= score_min]
        results.append(evaluate_filter(f, f"VIX >= {vix_min} + Score >= {score_min}"))

# VIX + bearish threshold
for vix_min in [15, 16]:
    for bear_thresh in [50, 55]:
        f = [t for t in trades if t.get("vix") and t["vix"] >= vix_min 
             and (t["first_bar_bullish"] or t["score"] >= bear_thresh)]
        results.append(evaluate_filter(f, f"VIX >= {vix_min} + Bear if score >= {bear_thresh}"))

# Triple combo
for vix_min in [15, 16]:
    for score_min in [10, 15]:
        for bear_thresh in [50, 55]:
            f = [t for t in trades if t.get("vix") and t["vix"] >= vix_min
                 and t["score"] >= score_min
                 and (t["first_bar_bullish"] or t["score"] >= bear_thresh)]
            results.append(evaluate_filter(f, 
                f"VIX >= {vix_min} + Score >= {score_min} + Bear >= {bear_thresh}"))

# === PRINT RESULTS ===
print()
print(f"{'Filter':<45} {'Trades':>6} {'WR':>6} {'Total P&L':>12} {'Avg/Trade':>10} "
      f"{'MaxDD':>10} {'PF':>5} {'Calmar':>7} {'Ann P&L':>11} {'Mo+/Mo-':>8}")
print("-" * 135)

# Sort by Calmar ratio
results.sort(key=lambda r: r["calmar"], reverse=True)

for r in results:
    flag = " ***" if r["label"] == "BASELINE (no filter)" else ""
    print(f"{r['label']:<45} {r['trades']:>6} {r['wr']:>5.1f}% ${r['pnl']:>11,.0f} "
          f"${r['avg_trade']:>9,.0f} ${r['max_dd']:>9,.0f} {r['pf']:>5.2f} "
          f"{r['calmar']:>7.2f} ${r['annual']:>10,.0f} {r['pos_months']:>3}/{r['neg_months']:<3}{flag}")

# Top 5 analysis
print()
print("=" * 100)
print("TOP 5 FILTERS (by Calmar ratio)")
print("=" * 100)
for i, r in enumerate(results[:5]):
    print(f"\n#{i+1}: {r['label']}")
    print(f"  Trades: {r['trades']}  |  WR: {r['wr']:.1f}%  |  Profit Factor: {r['pf']:.2f}")
    print(f"  Total P&L: ${r['pnl']:,.0f}  |  Annual: ${r['annual']:,.0f}")
    print(f"  Max DD: ${r['max_dd']:,.0f}  |  Calmar: {r['calmar']:.2f}")
    print(f"  Avg Win: ${r['avg_win']:,.0f}  |  Avg Loss: ${r['avg_loss']:,.0f}")
    print(f"  Months +/-: {r['pos_months']}/{r['neg_months']}")

# === Monthly equity comparison: Baseline vs Top Filter ===
print()
print("=" * 100)
print("MONTHLY P&L COMPARISON: BASELINE vs BEST FILTER")
print("=" * 100)

baseline = evaluate_filter(trades, "baseline")

# Get the best non-baseline filter
best = [r for r in results if r["label"] != "BASELINE (no filter)"][0]
best_label = best["label"]

# Rebuild monthly for both
def monthly_pnl(trade_list):
    m = defaultdict(float)
    for t in trade_list:
        m[t["date"][:7]] += t["opt_pnl"]
    return dict(m)

base_monthly = monthly_pnl(trades)

# Re-filter for best
if "VIX >= 16 + Score >= 10 + Bear >= 50" in best_label:
    best_trades = [t for t in trades if t.get("vix") and t["vix"] >= 16
                   and t["score"] >= 10
                   and (t["first_bar_bullish"] or t["score"] >= 50)]
elif "VIX >= 16 + Score >= 15" in best_label and "Bear" not in best_label:
    best_trades = [t for t in trades if t.get("vix") and t["vix"] >= 16 and t["score"] >= 15]
elif "VIX >= 16 + Bear if score >= 50" in best_label:
    best_trades = [t for t in trades if t.get("vix") and t["vix"] >= 16
                   and (t["first_bar_bullish"] or t["score"] >= 50)]
else:
    # Generic rebuild - just use the label to identify
    best_trades = trades  # fallback

best_monthly = monthly_pnl(best_trades)

all_months = sorted(set(list(base_monthly.keys()) + list(best_monthly.keys())))
base_cum = 0
best_cum = 0

print(f"\n{'Month':<10} {'Base P&L':>12} {'Base Cum':>12} {'Best P&L':>12} {'Best Cum':>12} {'Diff':>10}")
print("-" * 70)
for m in all_months:
    bp = base_monthly.get(m, 0)
    fp = best_monthly.get(m, 0)
    base_cum += bp
    best_cum += fp
    diff = fp - bp
    print(f"{m:<10} ${bp:>11,.0f} ${base_cum:>11,.0f} ${fp:>11,.0f} ${best_cum:>11,.0f} ${diff:>9,.0f}")

print(f"\n  Best filter: {best_label}")
print(f"  P&L improvement: ${best['pnl'] - baseline['pnl']:+,.0f}")
print(f"  MaxDD improvement: ${baseline['max_dd'] - best['max_dd']:+,.0f}")
print(f"  Calmar improvement: {baseline['calmar']:.2f} -> {best['calmar']:.2f}")

