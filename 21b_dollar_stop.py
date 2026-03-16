"""Test dollar-based position stop using real option bar data."""

import json, os, math
from datetime import datetime
from statistics import mean, stdev

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
trades = json.load(open(os.path.join(SCRIPT_DIR, "options_trades.json")))

def load_bars(ticker, date):
    safe = ticker.replace(":", "_").replace("/", "_")
    path = os.path.join(CACHE_DIR, f"bars_{date}_{safe}.json")
    if os.path.exists(path):
        return json.load(open(path))
    return None

base_pnls = [t["opt_pnl"] for t in trades]
days = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") - datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
years = days / 365.25
tpy = len(trades) / years
base_sharpe = (mean(base_pnls) / stdev(base_pnls)) * math.sqrt(tpy)

print(f"Baseline: Sharpe {base_sharpe:.2f} | P&L ${sum(base_pnls):,.0f}")
print()
print("Dollar-based position stop (exit when unrealized loss on position > $X):")
print("=" * 100)

for max_loss in [40000, 50000, 60000, 70000, 75000, 80000, 90000, 100000]:
    pnls = []
    triggered = 0
    saved_from = {}

    for t in trades:
        opt_bars = load_bars(t["option_ticker"], t["date"])
        if not opt_bars:
            pnls.append(t["opt_pnl"])
            continue

        entry_price = t["opt_entry_price"]
        contracts = t["opt_contracts"]

        exited_early = False
        for bar in opt_bars:
            if bar["time"] <= "09:31":
                continue
            worst_loss = contracts * (bar["low"] - entry_price) * 100
            if worst_loss < -max_loss:
                pnl = -max_loss
                pnls.append(pnl)
                triggered += 1
                reason = t["opt_exit_reason"]
                saved_from[reason] = saved_from.get(reason, 0) + 1
                exited_early = True
                break

        if not exited_early:
            pnls.append(t["opt_pnl"])

    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy)
    total = sum(pnls)
    cum = peak = max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    calmar = total / max_dd if max_dd > 0 else 999
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999

    delta_s = sharpe - base_sharpe
    delta_p = total - sum(base_pnls)
    sign_s = "+" if delta_s >= 0 else ""
    sign_p = "+" if delta_p >= 0 else ""
    print(f"  Max loss ${max_loss/1000:.0f}k: Sharpe {sharpe:.2f} ({sign_s}{delta_s:.2f}) | "
          f"P&L ${total:>10,.0f} ({sign_p}{delta_p:>10,.0f}) | "
          f"DD ${max_dd:>8,.0f} | Calmar {calmar:.2f} | PF {pf:.2f} | Triggered: {triggered}")
    replaced = ", ".join(f"{k}: {v}" for k, v in sorted(saved_from.items()) if v > 0)
    if replaced:
        print(f"    Replaced: {replaced}")
    print()
