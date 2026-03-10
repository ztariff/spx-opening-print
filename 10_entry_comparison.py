"""
SPX Opening Print Strategy — Phase 10: Entry Method Comparison
===============================================================
Compares two approaches:
  A) WAIT & CONFIRM: Check signals at open, wait for 1st bar close.
     If bullish → enter at bars[0]["close"] (9:31 price). If bearish → no trade.
  B) ENTER & BAIL: Enter at 9:30 open on any signal day.
     If 1st bar closes bearish → immediately exit at bars[0]["close"].
     If bullish → hold with normal PT/SL/TS.

Uses the same signal detection logic as scripts 08/09.

Usage:
    python3 10_entry_comparison.py

Output:
    entry_comparison_report.txt
"""

import os
import csv
from collections import defaultdict
from statistics import mean
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
REPORT = os.path.join(SCRIPT_DIR, "entry_comparison_report.txt")

MIN_RISK = 25000
MAX_RISK = 150000

output_lines = []
def p(msg=""):
    print(msg)
    output_lines.append(str(msg))


# ── Data Loaders ──────────────────────────────────────────────────────

def load_spx_intraday():
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


def load_vix_daily():
    data = {}
    if not os.path.exists(VIX_DAILY):
        return data
    with open(VIX_DAILY, "r") as f:
        for row in csv.DictReader(f):
            try:
                data[row["date"]] = {"open": float(row["open"]), "close": float(row["close"])}
            except (ValueError, KeyError):
                continue
    return data


def load_daily_csv(filepath):
    data = {}
    if not os.path.exists(filepath):
        return data
    with open(filepath, "r") as f:
        for row in csv.DictReader(f):
            try:
                data[row["date"]] = {
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                }
            except (ValueError, KeyError):
                continue
    return data


def compute_ma(daily_data, dates, target_date, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period:
        return None
    closes = [daily_data[dates[j]]["close"] for j in range(idx - period, idx) if dates[j] in daily_data]
    if len(closes) < period * 0.8:
        return None
    return mean(closes)


def simulate_trade(bars, entry_price, pt, sl, ts, entry_idx=0):
    for i in range(entry_idx + 1, min(entry_idx + ts + 1, len(bars))):
        bar = bars[i]
        if bar["low"] <= entry_price - sl:
            return (-sl, "Stop Loss", i - entry_idx, entry_price - sl, bar["time"])
        if bar["high"] >= entry_price + pt:
            return (pt, "Profit Target", i - entry_idx, entry_price + pt, bar["time"])
    last = min(entry_idx + ts, len(bars) - 1)
    pnl = bars[last]["close"] - entry_price
    return (round(pnl, 2), "Time Stop", last - entry_idx, bars[last]["close"], bars[last]["time"])


# ── Signal Detection (returns signals + score + exit params, without first-bar gate) ──

def get_signals(d, bars, intra_dates, intra_idx, spx_intraday, vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates):
    """Returns (signals, score, pt, sl, ts, gap_dir) or None if day is invalid."""
    if len(bars) < 10:
        return None

    dt = datetime.strptime(d, "%Y-%m-%d")
    idx = intra_idx[d]
    entry_open = bars[0]["open"]

    signals = []
    score = 0

    dow = dt.strftime("%A")
    if dow == "Monday":
        signals.append("Monday"); score += 15
    elif dow == "Thursday":
        signals.append("Thursday (negative)"); score -= 20
    elif dow == "Friday":
        score += 5

    gap_dir = None
    gap_pts = 0
    if idx > 0:
        prev_d = intra_dates[idx - 1]
        prev_bars = spx_intraday.get(prev_d, [])
        if prev_bars:
            prev_close = prev_bars[-1]["close"]
            gap_pts = entry_open - prev_close
            gap_dir = "up" if gap_pts > 0 else "down"
            if gap_dir == "up":
                signals.append(f"Gap up ({gap_pts:+.1f} pts)"); score += 10
            elif gap_dir == "down" and abs(gap_pts) > 30:
                signals.append(f"Large gap down ({gap_pts:+.1f} pts)"); score -= 15
            prev_open = prev_bars[0]["open"]
            prev_ret = (prev_close - prev_open) / prev_open * 100
            if prev_ret < -1.0:
                signals.append(f"Prior big down day ({prev_ret:+.1f}%)"); score += 10
            prev_high = max(b["high"] for b in prev_bars)
            prev_low = min(b["low"] for b in prev_bars)
            prev_range_pct = (prev_high - prev_low) / prev_close * 100
            if prev_range_pct > 1.5:
                signals.append("Wide prior range"); score += 5

    streak = 0
    for j in range(idx - 1, max(idx - 15, -1), -1):
        if j < 0: break
        sd = intra_dates[j]
        sb = spx_intraday.get(sd, [])
        if sb and len(sb) >= 10:
            day_ret = sb[-1]["close"] - sb[0]["open"]
            if streak == 0: streak = 1 if day_ret > 0 else -1
            elif streak > 0 and day_ret > 0: streak += 1
            elif streak < 0 and day_ret < 0: streak -= 1
            else: break
    if streak <= -3:
        signals.append(f"3+ down day streak ({streak})"); score += 25

    vix_dates_sorted = sorted(vix_daily.keys())
    if d in vix_daily:
        vix_open = vix_daily[d]["open"]
        if 20 <= vix_open < 25: signals.append("VIX elevated (20-25)"); score += 15
        elif 25 <= vix_open < 30: signals.append("VIX high (25-30)"); score += 10
        elif vix_open >= 30: signals.append("VIX very high (>30)"); score += 5
        vix_idx = None
        for vi, vd in enumerate(vix_dates_sorted):
            if vd == d: vix_idx = vi; break
        if vix_idx and vix_idx > 0:
            prev_vix_close = vix_daily[vix_dates_sorted[vix_idx - 1]]["close"]
            vix_chg_pct = (vix_open - prev_vix_close) / prev_vix_close * 100
            if -5 < vix_chg_pct < -1:
                signals.append(f"Vol falling ({vix_chg_pct:+.1f}%)"); score += 20

    spx_date_set = set(spx_dates)
    if d in spx_date_set:
        ma50 = compute_ma(spx_daily, spx_dates, d, 50)
        ma200 = compute_ma(spx_daily, spx_dates, d, 200)
        ma10 = compute_ma(spx_daily, spx_dates, d, 10)
        ma20 = compute_ma(spx_daily, spx_dates, d, 20)
        above_all = all(entry_open > ma for ma in [ma10, ma20, ma50, ma200] if ma)
        below_all = all(entry_open < ma for ma in [ma10, ma20, ma50, ma200] if ma)
        if ma50:
            pct_from_50 = (entry_open - ma50) / ma50 * 100
            if -2 < pct_from_50 < 0: signals.append(f"Just below 50d MA ({pct_from_50:+.1f}%)"); score += 15
            elif pct_from_50 < -2: signals.append(f"Below 50d MA ({pct_from_50:+.1f}%)"); score += 10
            elif pct_from_50 > 5: signals.append(f"Far above 50d MA ({pct_from_50:+.1f}%)"); score -= 10
        if ma200:
            pct_from_200 = (entry_open - ma200) / ma200 * 100
            if pct_from_200 > 10: score -= 5
        if not above_all and not below_all:
            signals.append("Mixed MAs"); score += 8

    if d in spx_date_set:
        wd = dt.weekday()
        if wd > 0:
            mon_date = (dt - timedelta(days=wd)).strftime("%Y-%m-%d")
            if mon_date in spx_daily:
                wtd_ret = (entry_open - spx_daily[mon_date]["open"]) / spx_daily[mon_date]["open"] * 100
                if wtd_ret < -1: signals.append(f"Deep red week ({wtd_ret:+.1f}%)"); score += 15
                elif wtd_ret < 0: signals.append(f"Red week ({wtd_ret:+.1f}%)"); score += 5
        month_start = dt.replace(day=1).strftime("%Y-%m-%d")
        for sd in spx_dates:
            if sd >= month_start and sd[:7] == d[:7] and sd in spx_daily:
                mtd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if mtd_ret < -1: signals.append(f"Red month ({mtd_ret:+.1f}%)"); score += 10
                break
        year_start = f"{dt.year}-01-01"
        for sd in spx_dates:
            if sd >= year_start and sd[:4] == d[:4] and sd in spx_daily:
                ytd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if ytd_ret < -0.5: signals.append(f"Red year ({ytd_ret:+.1f}%)"); score += 8
                break

    tlt_date_idx_map = {td: ti for ti, td in enumerate(tlt_dates)}
    if d in tlt_date_idx_map:
        tidx = tlt_date_idx_map[d]
        if tidx >= 5:
            tlt_5d_ago = tlt_daily.get(tlt_dates[tidx - 5])
            tlt_prev = tlt_daily.get(tlt_dates[tidx - 1])
            if tlt_5d_ago and tlt_prev:
                tlt_5d_ret = (tlt_prev["close"] - tlt_5d_ago["close"]) / tlt_5d_ago["close"] * 100
                if 0 < tlt_5d_ret < 1: signals.append(f"Bonds mildly up 5d ({tlt_5d_ret:+.1f}%)"); score += 8

    if idx >= 20:
        lookback = intra_dates[idx-20:idx]
        highs, lows = [], []
        for ld in lookback:
            lb = spx_intraday.get(ld, [])
            if lb: highs.append(max(b["high"] for b in lb)); lows.append(min(b["low"] for b in lb))
        if highs and lows:
            h20, l20 = max(highs), min(lows)
            if h20 != l20:
                pct_in = (entry_open - l20) / (h20 - l20) * 100
                if 10 <= pct_in < 30: signals.append(f"Lower 20d range ({pct_in:.0f}%)"); score += 15

    # Exit params based on dominant signal
    signal_set = set(s.split(" (")[0] for s in signals)
    pt, sl, ts = 50, 10, 240
    if "3+ down day streak" in signal_set: pt, sl, ts = 50, 20, 240
    elif "Vol falling" in signal_set: pt, sl, ts = 50, 10, 240
    elif "VIX elevated" in signal_set: pt, sl, ts = 20, 15, 30
    elif "Just below 50d MA" in signal_set: pt, sl, ts = 50, 2, 390
    elif "Red month" in signal_set or "Deep red week" in signal_set: pt, sl, ts = 50, 20, 240
    elif "Mixed MAs" in signal_set and gap_dir == "up": pt, sl, ts = 50, 2, 30
    elif gap_dir == "up" and "Monday" in signal_set: pt, sl, ts = 15, 20, 390
    elif gap_dir == "up": pt, sl, ts = 50, 20, 390
    elif "Monday" in signal_set: pt, sl, ts = 15, 20, 390

    return signals, score, pt, sl, ts, gap_dir


def compute_risk(signals, score):
    n_positive = len([s for s in signals if "negative" not in s.lower()])
    clamped_score = max(0, min(score, 80))
    risk = MIN_RISK + (MAX_RISK - MIN_RISK) * (clamped_score / 80)
    if n_positive >= 5: risk = min(risk * 1.3, MAX_RISK)
    elif n_positive >= 3: risk = min(risk * 1.15, MAX_RISK)
    risk = max(MIN_RISK, min(MAX_RISK, round(risk / 1000) * 1000))
    return risk


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    spx_intraday = load_spx_intraday()
    vix_daily = load_vix_daily()
    spx_daily = load_daily_csv(SPX_DAILY)
    tlt_daily = load_daily_csv(TLT_DAILY)

    spx_dates = sorted(spx_daily.keys())
    tlt_dates = sorted(tlt_daily.keys())
    intra_dates = sorted(spx_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}

    print(f"SPX intraday: {len(intra_dates)} days")

    # ── Approach A: WAIT & CONFIRM ──
    # Check signals at open. Wait for bar[0] close.
    # If bullish → enter at bars[0]["close"], simulate from bar 1 onward.
    # If bearish → skip day entirely.
    a_trades = []

    # ── Approach B: ENTER & BAIL ──
    # Enter at bars[0]["open"] on any day with signals.
    # If bar[0] closes bearish → exit at bars[0]["close"] (bail).
    # If bar[0] closes bullish → hold with normal PT/SL/TS from bar 1 onward.
    b_trades = []

    for d in intra_dates:
        bars = spx_intraday[d]
        result = get_signals(
            d, bars, intra_dates, intra_idx, spx_intraday,
            vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates
        )
        if result is None:
            continue

        signals, score, pt, sl, ts, gap_dir = result

        # Need at least some positive signals to trade
        n_positive = len([s for s in signals if "negative" not in s.lower()])
        if n_positive < 1:
            continue

        risk = compute_risk(signals, score)
        first_bar_bullish = bars[0]["close"] > bars[0]["open"]

        # ── Approach A: Wait & Confirm ──
        if first_bar_bullish:
            entry_a = bars[0]["close"]  # enter at 9:31
            pnl_pts, exit_reason, hold_mins, exit_price, exit_time = simulate_trade(
                bars, entry_a, pt, sl, ts, entry_idx=1
            )
            dpp = risk / sl
            a_trades.append({
                "date": d, "entry": entry_a, "pnl_pts": pnl_pts,
                "pnl_dollars": round(pnl_pts * dpp, 2),
                "exit_reason": exit_reason, "risk": risk,
                "signals": signals, "n_signals": len(signals),
            })
        # else: skip — no trade for approach A

        # ── Approach B: Enter & Bail ──
        entry_b = bars[0]["open"]  # enter at 9:30
        if first_bar_bullish:
            # Bar closed green — hold with normal exits, simulate from bar 1
            pnl_pts, exit_reason, hold_mins, exit_price, exit_time = simulate_trade(
                bars, entry_b, pt, sl, ts, entry_idx=1
            )
            dpp = risk / sl
            b_trades.append({
                "date": d, "entry": entry_b, "pnl_pts": pnl_pts,
                "pnl_dollars": round(pnl_pts * dpp, 2),
                "exit_reason": exit_reason, "risk": risk,
                "first_bar": "bullish",
                "signals": signals, "n_signals": len(signals),
            })
        else:
            # Bar closed red — bail immediately at bar[0] close
            bail_price = bars[0]["close"]
            bail_pnl = round(bail_price - entry_b, 2)
            dpp = risk / sl
            b_trades.append({
                "date": d, "entry": entry_b, "pnl_pts": bail_pnl,
                "pnl_dollars": round(bail_pnl * dpp, 2),
                "exit_reason": "Bail (bearish 1st bar)", "risk": risk,
                "first_bar": "bearish",
                "signals": signals, "n_signals": len(signals),
            })

    # ── Report ──
    p("=" * 80)
    p("SPX OPENING PRINT — ENTRY METHOD COMPARISON")
    p("=" * 80)
    p()

    for label, trades in [("A) WAIT & CONFIRM (enter at 9:31 close if bullish)", a_trades),
                          ("B) ENTER & BAIL (enter at 9:30 open, bail if bearish)", b_trades)]:
        p("-" * 80)
        p(f"  {label}")
        p("-" * 80)
        total = len(trades)
        winners = [t for t in trades if t["pnl_dollars"] > 0]
        losers = [t for t in trades if t["pnl_dollars"] <= 0]
        total_pnl = sum(t["pnl_dollars"] for t in trades)
        avg_pnl = total_pnl / total if total else 0
        wr = len(winners) / total * 100 if total else 0
        avg_win = mean([t["pnl_dollars"] for t in winners]) if winners else 0
        avg_loss = mean([t["pnl_dollars"] for t in losers]) if losers else 0
        gross_win = sum(t["pnl_dollars"] for t in winners)
        gross_loss = abs(sum(t["pnl_dollars"] for t in losers))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        max_win = max(t["pnl_dollars"] for t in trades) if trades else 0
        max_loss = min(t["pnl_dollars"] for t in trades) if trades else 0
        avg_pts = mean([t["pnl_pts"] for t in trades]) if trades else 0

        p(f"  Total Trades:    {total}")
        p(f"  Winners:         {len(winners)} ({wr:.1f}%)")
        p(f"  Losers:          {len(losers)}")
        p(f"  Total P&L:       ${total_pnl:,.0f}")
        p(f"  Avg P&L/Trade:   ${avg_pnl:,.0f}")
        p(f"  Avg Pts/Trade:   {avg_pts:+.2f}")
        p(f"  Avg Win:         ${avg_win:,.0f}")
        p(f"  Avg Loss:        ${avg_loss:,.0f}")
        p(f"  Profit Factor:   {pf:.2f}")
        p(f"  Best Trade:      ${max_win:,.0f}")
        p(f"  Worst Trade:     ${max_loss:,.0f}")

        # Equity curve stats
        cum = 0
        peak = 0
        max_dd = 0
        for t in trades:
            cum += t["pnl_dollars"]
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        p(f"  Max Drawdown:    ${max_dd:,.0f}")
        p()

    # ── Approach B breakdown: bullish vs bearish first bars ──
    p("-" * 80)
    p("  B) BREAKDOWN: Bullish first bar vs Bearish bail-outs")
    p("-" * 80)
    b_bull = [t for t in b_trades if t.get("first_bar") == "bullish"]
    b_bear = [t for t in b_trades if t.get("first_bar") == "bearish"]

    p(f"  Bullish 1st bar days: {len(b_bull)}")
    if b_bull:
        bull_pnl = sum(t["pnl_dollars"] for t in b_bull)
        bull_wr = sum(1 for t in b_bull if t["pnl_dollars"] > 0) / len(b_bull) * 100
        p(f"    P&L:     ${bull_pnl:,.0f}")
        p(f"    Win Rate: {bull_wr:.1f}%")
        p(f"    Avg P&L:  ${bull_pnl/len(b_bull):,.0f}")
    p()

    p(f"  Bearish 1st bar days (bailed): {len(b_bear)}")
    if b_bear:
        bear_pnl = sum(t["pnl_dollars"] for t in b_bear)
        bear_wr = sum(1 for t in b_bear if t["pnl_dollars"] > 0) / len(b_bear) * 100
        bear_avg_pts = mean([t["pnl_pts"] for t in b_bear])
        p(f"    P&L:      ${bear_pnl:,.0f}")
        p(f"    Win Rate:  {bear_wr:.1f}%")
        p(f"    Avg Pts:   {bear_avg_pts:+.2f}")
        p(f"    Avg P&L:   ${bear_pnl/len(b_bear):,.0f}")

        # Distribution of bail P&L
        bail_pnls = sorted([t["pnl_pts"] for t in b_bear])
        p(f"    Bail pts range: {bail_pnls[0]:+.2f} to {bail_pnls[-1]:+.2f}")
        p(f"    Median bail:    {bail_pnls[len(bail_pnls)//2]:+.2f} pts")
    p()

    # ── Head-to-head: same-day comparison ──
    p("-" * 80)
    p("  HEAD-TO-HEAD: Days where both approaches traded")
    p("-" * 80)
    a_dates = {t["date"]: t for t in a_trades}
    both_days = [d for d in a_dates if any(t["date"] == d for t in b_trades)]
    b_map = {t["date"]: t for t in b_trades}

    a_better = 0
    b_better = 0
    a_total_h2h = 0
    b_total_h2h = 0
    for d in both_days:
        a_pnl = a_dates[d]["pnl_dollars"]
        b_pnl = b_map[d]["pnl_dollars"]
        a_total_h2h += a_pnl
        b_total_h2h += b_pnl
        if a_pnl > b_pnl: a_better += 1
        elif b_pnl > a_pnl: b_better += 1

    p(f"  Shared days: {len(both_days)}")
    p(f"  A better on {a_better} days, B better on {b_better} days")
    p(f"  A total on shared days: ${a_total_h2h:,.0f}")
    p(f"  B total on shared days: ${b_total_h2h:,.0f}")
    p(f"  Difference (B - A):     ${b_total_h2h - a_total_h2h:,.0f}")
    p()

    # ── Extra days B gets that A doesn't (bearish first bar days) ──
    b_only = [t for t in b_trades if t["date"] not in a_dates]
    p(f"  Extra days B trades (bearish 1st bar bail-outs): {len(b_only)}")
    if b_only:
        extra_pnl = sum(t["pnl_dollars"] for t in b_only)
        extra_wr = sum(1 for t in b_only if t["pnl_dollars"] > 0) / len(b_only) * 100
        p(f"    P&L from extra days: ${extra_pnl:,.0f}")
        p(f"    Win Rate: {extra_wr:.1f}%")
    p()

    # ── Verdict ──
    p("=" * 80)
    a_total_pnl = sum(t["pnl_dollars"] for t in a_trades)
    b_total_pnl = sum(t["pnl_dollars"] for t in b_trades)
    p(f"  APPROACH A TOTAL P&L: ${a_total_pnl:,.0f}  ({len(a_trades)} trades)")
    p(f"  APPROACH B TOTAL P&L: ${b_total_pnl:,.0f}  ({len(b_trades)} trades)")
    p()
    if b_total_pnl > a_total_pnl:
        p(f"  >>> APPROACH B WINS by ${b_total_pnl - a_total_pnl:,.0f}")
    elif a_total_pnl > b_total_pnl:
        p(f"  >>> APPROACH A WINS by ${a_total_pnl - b_total_pnl:,.0f}")
    else:
        p("  >>> TIE")
    p("=" * 80)

    with open(REPORT, "w") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved: {REPORT}")


if __name__ == "__main__":
    main()
