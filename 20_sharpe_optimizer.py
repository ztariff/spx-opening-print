"""
SPX Opening Print — Script 20: Sharpe Ratio Optimizer
======================================================
Analyze what drives variance in returns and test approaches to improve Sharpe.

Sharpe = mean(returns) / stdev(returns) * sqrt(trades_per_year)

Approaches:
1. Max loss cap (tighter stop = smaller tail losses)
2. Premium cap (reduce position size on all trades)
3. Score-based filtering (only trade highest-quality setups)
4. Exit time filter (trades that hold too long tend to lose)
5. Day-of-week filter
6. VIX band tightening
7. Hybrid: combine best filters
8. Trailing stop on options (take profit earlier to reduce variance)
9. Risk-per-trade cap as % of equity
"""

import json, os, math
from datetime import datetime
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
trades = json.load(open(os.path.join(SCRIPT_DIR, "options_trades.json")))

# ── Helpers ──────────────────────────────────────────────────────────

def compute_sharpe(pnls, first_date, last_date):
    if len(pnls) < 2 or stdev(pnls) == 0:
        return 0, 0, 0, 0
    days = (datetime.strptime(last_date, "%Y-%m-%d") - datetime.strptime(first_date, "%Y-%m-%d")).days
    years = days / 365.25
    tpy = len(pnls) / years if years > 0 else len(pnls)
    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy)
    total = sum(pnls)
    # Max DD
    cum = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    calmar = total / max_dd if max_dd > 0 else 999
    return sharpe, total, max_dd, calmar

def print_result(label, pnls, first_date, last_date, baseline_sharpe=None):
    sharpe, total, max_dd, calmar = compute_sharpe(pnls, first_date, last_date)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100 if pnls else 0
    pf = sum(p for p in pnls if p > 0) / sum(abs(p) for p in pnls if p <= 0) if sum(abs(p) for p in pnls if p <= 0) > 0 else 999
    delta = f"  ({'+' if sharpe >= baseline_sharpe else ''}{sharpe - baseline_sharpe:.2f})" if baseline_sharpe else ""
    print(f"  {label}")
    print(f"    Sharpe: {sharpe:.2f}{delta}  |  Trades: {len(pnls)}  |  WR: {wr:.1f}%  |  PF: {pf:.2f}")
    print(f"    P&L: ${total:,.0f}  |  DD: ${max_dd:,.0f}  |  Calmar: {calmar:.2f}")
    print(f"    Mean: ${mean(pnls):,.0f}  |  StDev: ${stdev(pnls):,.0f}  |  Avg W: ${mean([p for p in pnls if p > 0]):,.0f}  |  Avg L: ${mean([abs(p) for p in pnls if p <= 0]):,.0f}")
    return sharpe, total, max_dd, calmar

# ── Baseline ─────────────────────────────────────────────────────────

print("=" * 80)
print("SHARPE RATIO OPTIMIZATION ANALYSIS")
print("=" * 80)

base_pnls = [t["opt_pnl"] for t in trades]
base_sharpe, _, _, _ = print_result("BASELINE (flat sizing, all current filters)",
                                      base_pnls, trades[0]["date"], trades[-1]["date"])
print()

results = []

# ══════════════════════════════════════════════════════════════════════
# ANALYSIS: What's driving variance?
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("VARIANCE DECOMPOSITION")
print("-" * 80)

# Distribution of returns
pnls_sorted = sorted(base_pnls)
print(f"  Return distribution:")
print(f"    < -$100k:  {sum(1 for p in base_pnls if p < -100000)} trades, total ${sum(p for p in base_pnls if p < -100000):,.0f}")
print(f"    -$100k to -$50k: {sum(1 for p in base_pnls if -100000 <= p < -50000)} trades, total ${sum(p for p in base_pnls if -100000 <= p < -50000):,.0f}")
print(f"    -$50k to $0:  {sum(1 for p in base_pnls if -50000 <= p < 0)} trades, total ${sum(p for p in base_pnls if -50000 <= p < 0):,.0f}")
print(f"    $0 to $50k:   {sum(1 for p in base_pnls if 0 <= p < 50000)} trades, total ${sum(p for p in base_pnls if 0 <= p < 50000):,.0f}")
print(f"    $50k to $100k: {sum(1 for p in base_pnls if 50000 <= p < 100000)} trades, total ${sum(p for p in base_pnls if 50000 <= p < 100000):,.0f}")
print(f"    $100k to $200k: {sum(1 for p in base_pnls if 100000 <= p < 200000)} trades, total ${sum(p for p in base_pnls if 100000 <= p < 200000):,.0f}")
print(f"    > $200k:  {sum(1 for p in base_pnls if p >= 200000)} trades, total ${sum(p for p in base_pnls if p >= 200000):,.0f}")

# Variance contribution by exit reason
print(f"\n  By exit reason:")
by_reason = defaultdict(list)
for t in trades:
    by_reason[t["opt_exit_reason"]].append(t["opt_pnl"])
for reason in sorted(by_reason.keys()):
    pnls = by_reason[reason]
    avg = mean(pnls)
    sd = stdev(pnls) if len(pnls) > 1 else 0
    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
    print(f"    {reason:25s}: {len(pnls):>3} trades, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

# By hold time
print(f"\n  By hold time:")
for lo, hi, label in [(0, 15, "0-15 min"), (15, 60, "15-60 min"), (60, 120, "1-2 hr"),
                        (120, 240, "2-4 hr"), (240, 999, "4hr+")]:
    subset = [t["opt_pnl"] for t in trades if lo <= t["opt_hold_mins"] < hi]
    if subset:
        avg = mean(subset)
        sd = stdev(subset) if len(subset) > 1 else 0
        wr = sum(1 for p in subset if p > 0) / len(subset) * 100
        print(f"    {label:12s}: {len(subset):>3} trades, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

# By score
print(f"\n  By score bracket:")
for lo, hi in [(0, 30), (30, 45), (45, 60), (60, 75), (75, 100)]:
    subset = [t["opt_pnl"] for t in trades if lo <= t["score"] < hi]
    if subset:
        avg = mean(subset)
        sd = stdev(subset) if len(subset) > 1 else 0
        wr = sum(1 for p in subset if p > 0) / len(subset) * 100
        print(f"    Score {lo}-{hi}: {len(subset):>3} trades, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

# By premium size
print(f"\n  By premium size:")
for lo, hi in [(0, 50000), (50000, 100000), (100000, 150000), (150000, 200000), (200000, 999999)]:
    subset = [t["opt_pnl"] for t in trades if lo <= t["opt_premium"] < hi]
    if subset:
        avg = mean(subset)
        sd = stdev(subset) if len(subset) > 1 else 0
        wr = sum(1 for p in subset if p > 0) / len(subset) * 100
        print(f"    ${lo/1000:.0f}k-${hi/1000:.0f}k prem: {len(subset):>3} trades, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

# By VIX level
print(f"\n  By VIX level:")
for lo, hi in [(16, 18), (18, 20), (20, 23), (23, 26), (26, 30)]:
    subset = [t["opt_pnl"] for t in trades if t.get("vix") and lo <= t["vix"] < hi]
    if subset:
        avg = mean(subset)
        sd = stdev(subset) if len(subset) > 1 else 0
        wr = sum(1 for p in subset if p > 0) / len(subset) * 100
        print(f"    VIX {lo}-{hi}: {len(subset):>3} trades, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 1: Cap max loss per trade
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 1: Cap max loss per trade (simulate tighter stop)")
print("-" * 80)
for cap in [50000, 75000, 100000, 120000]:
    capped = [max(p, -cap) for p in base_pnls]
    s, _, _, _ = print_result(f"Max loss capped at ${cap/1000:.0f}k", capped, trades[0]["date"], trades[-1]["date"], base_sharpe)
    results.append((s, f"Max loss cap ${cap/1000:.0f}k"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 2: Premium cap (reduce all position sizes)
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 2: Premium cap (scale down all trades)")
print("-" * 80)
for cap in [75000, 100000, 125000, 150000]:
    scaled = []
    for t in trades:
        if t["opt_premium"] > cap:
            scale = cap / t["opt_premium"]
            scaled.append(t["opt_pnl"] * scale)
        else:
            scaled.append(t["opt_pnl"])
    s, _, _, _ = print_result(f"Premium capped at ${cap/1000:.0f}k", scaled, trades[0]["date"], trades[-1]["date"], base_sharpe)
    results.append((s, f"Premium cap ${cap/1000:.0f}k"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 3: Score minimum filter
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 3: Minimum score filter")
print("-" * 80)
for min_score in [30, 35, 40, 45, 50]:
    filtered = [t for t in trades if t["score"] >= min_score]
    if len(filtered) > 10:
        pnls = [t["opt_pnl"] for t in filtered]
        s, _, _, _ = print_result(f"Score >= {min_score}", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
        results.append((s, f"Score >= {min_score}"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 4: VIX band tightening
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 4: VIX band tightening")
print("-" * 80)
for vlo, vhi in [(16, 25), (16, 28), (17, 28), (18, 28), (17, 25), (18, 25), (18, 30)]:
    filtered = [t for t in trades if t.get("vix") and vlo <= t["vix"] <= vhi]
    if len(filtered) > 20:
        pnls = [t["opt_pnl"] for t in filtered]
        s, _, _, _ = print_result(f"VIX {vlo}-{vhi}", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
        results.append((s, f"VIX {vlo}-{vhi}"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 5: Day of week filter
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 5: Day of week analysis")
print("-" * 80)
for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
    subset = [t for t in trades if t["day_of_week"] == day]
    if len(subset) > 5:
        pnls = [t["opt_pnl"] for t in subset]
        s = compute_sharpe(pnls, subset[0]["date"], subset[-1]["date"])[0]
        avg = mean(pnls)
        sd = stdev(pnls) if len(pnls) > 1 else 0
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        print(f"    {day:12s}: {len(subset):>3} trades, Sharpe {s:.2f}, avg ${avg:>10,.0f}, stdev ${sd:>10,.0f}, WR {wr:.0f}%")

# Test excluding worst days
for exclude in [["Monday"], ["Friday"], ["Monday", "Friday"]]:
    filtered = [t for t in trades if t["day_of_week"] not in exclude]
    if len(filtered) > 20:
        pnls = [t["opt_pnl"] for t in filtered]
        s, _, _, _ = print_result(f"Exclude {', '.join(exclude)}", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
        results.append((s, f"Exclude {', '.join(exclude)}"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 6: Exit time filter (skip trades that hold full duration)
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 6: Hold time analysis — cap losing trade hold time")
print("-" * 80)
# What if we simulate exiting losers earlier?
for max_hold in [30, 60, 120, 180]:
    # For trades that held longer and lost, cap their loss at what a shorter hold would imply
    # Rough approximation: if trade held 240 min and lost $100k, at 120 min it might have lost ~$50k
    # This is a rough proxy - real implementation would need intraday option data
    capped = []
    for t in trades:
        if t["opt_pnl"] < 0 and t["opt_hold_mins"] > max_hold:
            # Approximate: scale loss proportionally to hold time ratio
            ratio = max_hold / t["opt_hold_mins"]
            capped.append(t["opt_pnl"] * ratio)
        else:
            capped.append(t["opt_pnl"])
    s, _, _, _ = print_result(f"Losers capped at {max_hold}min hold (approx)", capped, trades[0]["date"], trades[-1]["date"], base_sharpe)
    results.append((s, f"Losers capped {max_hold}min"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 7: Normalize position size (equal premium per trade)
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 7: Equal premium per trade (normalize variance)")
print("-" * 80)
for target_prem in [75000, 100000, 125000]:
    normalized = []
    for t in trades:
        if t["opt_premium"] > 0:
            scale = target_prem / t["opt_premium"]
            normalized.append(t["opt_pnl"] * scale)
        else:
            normalized.append(t["opt_pnl"])
    s, _, _, _ = print_result(f"Equal ${target_prem/1000:.0f}k premium", normalized, trades[0]["date"], trades[-1]["date"], base_sharpe)
    results.append((s, f"Equal ${target_prem/1000:.0f}k prem"))
print()

# ══════════════════════════════════════════════════════════════════════
# APPROACH 8: Combined best ideas
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("APPROACH 8: Combined approaches")
print("-" * 80)

# Score >= 35 + VIX 17-28
filtered = [t for t in trades if t["score"] >= 35 and t.get("vix") and 17 <= t["vix"] <= 28]
if len(filtered) > 20:
    pnls = [t["opt_pnl"] for t in filtered]
    s, _, _, _ = print_result("Score>=35 + VIX 17-28", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
    results.append((s, "Score>=35 + VIX 17-28"))

# Score >= 40 + VIX 17-28
filtered = [t for t in trades if t["score"] >= 40 and t.get("vix") and 17 <= t["vix"] <= 28]
if len(filtered) > 20:
    pnls = [t["opt_pnl"] for t in filtered]
    s, _, _, _ = print_result("Score>=40 + VIX 17-28", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
    results.append((s, "Score>=40 + VIX 17-28"))

# Score >= 35 + premium cap 125k
filtered_trades = [t for t in trades if t["score"] >= 35]
if len(filtered_trades) > 20:
    pnls = []
    for t in filtered_trades:
        if t["opt_premium"] > 125000:
            pnls.append(t["opt_pnl"] * (125000 / t["opt_premium"]))
        else:
            pnls.append(t["opt_pnl"])
    s, _, _, _ = print_result("Score>=35 + prem cap $125k", pnls, filtered_trades[0]["date"], filtered_trades[-1]["date"], base_sharpe)
    results.append((s, "Score>=35 + prem cap $125k"))

# Equal $100k premium + VIX 17-28
filtered = [t for t in trades if t.get("vix") and 17 <= t["vix"] <= 28]
if len(filtered) > 20:
    pnls = []
    for t in filtered:
        if t["opt_premium"] > 0:
            pnls.append(t["opt_pnl"] * (100000 / t["opt_premium"]))
        else:
            pnls.append(t["opt_pnl"])
    s, _, _, _ = print_result("Equal $100k prem + VIX 17-28", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
    results.append((s, "Equal $100k prem + VIX 17-28"))

# Score >= 35 + equal $100k premium
filtered = [t for t in trades if t["score"] >= 35]
if len(filtered) > 20:
    pnls = []
    for t in filtered:
        if t["opt_premium"] > 0:
            pnls.append(t["opt_pnl"] * (100000 / t["opt_premium"]))
        else:
            pnls.append(t["opt_pnl"])
    s, _, _, _ = print_result("Score>=35 + equal $100k prem", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
    results.append((s, "Score>=35 + equal $100k prem"))

# Score >= 40 + equal $100k premium
filtered = [t for t in trades if t["score"] >= 40]
if len(filtered) > 20:
    pnls = []
    for t in filtered:
        if t["opt_premium"] > 0:
            pnls.append(t["opt_pnl"] * (100000 / t["opt_premium"]))
        else:
            pnls.append(t["opt_pnl"])
    s, _, _, _ = print_result("Score>=40 + equal $100k prem", pnls, filtered[0]["date"], filtered[-1]["date"], base_sharpe)
    results.append((s, "Score>=40 + equal $100k prem"))

print()

# ══════════════════════════════════════════════════════════════════════
# RANKING
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("TOP 15 BY SHARPE RATIO")
print("=" * 80)
results.sort(key=lambda x: x[0], reverse=True)
for i, (sharpe, label) in enumerate(results[:15]):
    delta = sharpe - base_sharpe
    print(f"  #{i+1}: Sharpe {sharpe:.2f} ({'+' if delta>=0 else ''}{delta:.2f})  —  {label}")

print(f"\n  Baseline Sharpe: {base_sharpe:.2f}")
