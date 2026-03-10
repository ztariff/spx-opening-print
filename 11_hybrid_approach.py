"""
SPX Opening Print Strategy — Phase 11: Hybrid Approach C Testing
=================================================================
Tests a hybrid entry method:
  - HIGH conviction (score >= threshold): Enter at 9:30 open (Approach B).
    Bail if 1st bar bearish, hold if bullish.
  - LOW conviction (score < threshold): Wait for confirmation (Approach A).
    Only enter at 9:31 close if 1st bar bullish. Skip bearish days.

Tests multiple score thresholds to find the optimal cutoff.
Also tests: B with minimum score filter, and A/B baselines for comparison.

Usage:
    python3 11_hybrid_approach.py

Output:
    hybrid_approach_report.txt
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
REPORT = os.path.join(SCRIPT_DIR, "hybrid_approach_report.txt")

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


# ── Signal Detection ──────────────────────────────────────────────────

def get_signals(d, bars, intra_dates, intra_idx, spx_intraday, vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates):
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
                signals.append(f"Gap up"); score += 10
            elif gap_dir == "down" and abs(gap_pts) > 30:
                signals.append(f"Large gap down"); score -= 15
            prev_open = prev_bars[0]["open"]
            prev_ret = (prev_close - prev_open) / prev_open * 100
            if prev_ret < -1.0:
                signals.append("Prior big down day"); score += 10
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
        signals.append("3+ down day streak"); score += 25

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
                signals.append("Vol falling"); score += 20

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
            if -2 < pct_from_50 < 0: signals.append("Just below 50d MA"); score += 15
            elif pct_from_50 < -2: signals.append("Below 50d MA"); score += 10
            elif pct_from_50 > 5: signals.append("Far above 50d MA"); score -= 10
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
                if wtd_ret < -1: signals.append("Deep red week"); score += 15
                elif wtd_ret < 0: signals.append("Red week"); score += 5
        month_start = dt.replace(day=1).strftime("%Y-%m-%d")
        for sd in spx_dates:
            if sd >= month_start and sd[:7] == d[:7] and sd in spx_daily:
                mtd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if mtd_ret < -1: signals.append("Red month"); score += 10
                break
        year_start = f"{dt.year}-01-01"
        for sd in spx_dates:
            if sd >= year_start and sd[:4] == d[:4] and sd in spx_daily:
                ytd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if ytd_ret < -0.5: signals.append("Red year"); score += 8
                break

    tlt_date_idx_map = {td: ti for ti, td in enumerate(tlt_dates)}
    if d in tlt_date_idx_map:
        tidx = tlt_date_idx_map[d]
        if tidx >= 5:
            tlt_5d_ago = tlt_daily.get(tlt_dates[tidx - 5])
            tlt_prev = tlt_daily.get(tlt_dates[tidx - 1])
            if tlt_5d_ago and tlt_prev:
                tlt_5d_ret = (tlt_prev["close"] - tlt_5d_ago["close"]) / tlt_5d_ago["close"] * 100
                if 0 < tlt_5d_ret < 1: signals.append("Bonds mildly up 5d"); score += 8

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
                if 10 <= pct_in < 30: signals.append("Lower 20d range"); score += 15

    # Exit params
    signal_set = set(signals)
    pt, sl, ts = 50, 10, 240
    if "3+ down day streak" in signal_set: pt, sl, ts = 50, 20, 240
    elif "Vol falling" in signal_set: pt, sl, ts = 50, 10, 240
    elif "VIX elevated (20-25)" in signal_set: pt, sl, ts = 20, 15, 30
    elif "Just below 50d MA" in signal_set: pt, sl, ts = 50, 2, 390
    elif "Red month" in signal_set or "Deep red week" in signal_set: pt, sl, ts = 50, 20, 240
    elif "Mixed MAs" in signal_set and gap_dir == "up": pt, sl, ts = 50, 2, 30
    elif gap_dir == "up" and "Monday" in signal_set: pt, sl, ts = 15, 20, 390
    elif gap_dir == "up": pt, sl, ts = 50, 20, 390
    elif "Monday" in signal_set: pt, sl, ts = 15, 20, 390

    n_positive = len([s for s in signals if "negative" not in s.lower()])
    return signals, score, pt, sl, ts, gap_dir, n_positive


def compute_risk(n_positive, score):
    clamped_score = max(0, min(score, 80))
    risk = MIN_RISK + (MAX_RISK - MIN_RISK) * (clamped_score / 80)
    if n_positive >= 5: risk = min(risk * 1.3, MAX_RISK)
    elif n_positive >= 3: risk = min(risk * 1.15, MAX_RISK)
    risk = max(MIN_RISK, min(MAX_RISK, round(risk / 1000) * 1000))
    return risk


def report_stats(label, trades):
    p(f"  {label}")
    p("-" * 80)
    total = len(trades)
    if total == 0:
        p("  No trades")
        p()
        return
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    wr = len(winners) / total * 100
    avg_pnl = total_pnl / total
    avg_win = mean([t["pnl"] for t in winners]) if winners else 0
    avg_loss = mean([t["pnl"] for t in losers]) if losers else 0
    gross_win = sum(t["pnl"] for t in winners)
    gross_loss = abs(sum(t["pnl"] for t in losers))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    max_win = max(t["pnl"] for t in trades)
    max_loss = min(t["pnl"] for t in trades)
    cum = 0; peak = 0; max_dd = 0
    for t in trades:
        cum += t["pnl"]
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd

    p(f"  Trades: {total}  |  WR: {wr:.1f}%  |  PF: {pf:.2f}")
    p(f"  Total P&L: ${total_pnl:,.0f}  |  Avg: ${avg_pnl:,.0f}/trade")
    p(f"  Avg Win: ${avg_win:,.0f}  |  Avg Loss: ${avg_loss:,.0f}")
    p(f"  Best: ${max_win:,.0f}  |  Worst: ${max_loss:,.0f}")
    p(f"  Max Drawdown: ${max_dd:,.0f}")

    # Count bails
    bails = [t for t in trades if t.get("bail")]
    if bails:
        bail_pnl = sum(t["pnl"] for t in bails)
        p(f"  Bails: {len(bails)} ({len(bails)/total*100:.1f}%)  |  Bail P&L: ${bail_pnl:,.0f}")
    p()


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
    print("Computing signals for all days...")

    # Pre-compute signals for every day
    day_info = {}
    for d in intra_dates:
        bars = spx_intraday[d]
        result = get_signals(d, bars, intra_dates, intra_idx, spx_intraday,
                            vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates)
        if result is None:
            continue
        signals, score, pt, sl, ts, gap_dir, n_positive = result
        if n_positive < 1:
            continue
        first_bar_bullish = bars[0]["close"] > bars[0]["open"]
        risk = compute_risk(n_positive, score)
        dpp = risk / sl
        day_info[d] = {
            "bars": bars, "signals": signals, "score": score,
            "pt": pt, "sl": sl, "ts": ts, "risk": risk, "dpp": dpp,
            "first_bar_bullish": first_bar_bullish,
            "entry_open": bars[0]["open"],
            "entry_close": bars[0]["close"],
            "n_positive": n_positive,
        }

    print(f"Signal days: {len(day_info)}")

    # ── Simulate all approaches ──

    def sim_approach_a(days_dict):
        """A: Wait & confirm. Enter at bar[0] close if bullish."""
        trades = []
        for d, info in sorted(days_dict.items()):
            if not info["first_bar_bullish"]:
                continue
            entry = info["entry_close"]
            pnl_pts, reason, mins, ep, et = simulate_trade(
                info["bars"], entry, info["pt"], info["sl"], info["ts"], entry_idx=1)
            trades.append({"date": d, "pnl": round(pnl_pts * info["dpp"], 2),
                          "pnl_pts": pnl_pts, "bail": False, "score": info["score"]})
        return trades

    def sim_approach_b(days_dict):
        """B: Enter at open, bail if bearish."""
        trades = []
        for d, info in sorted(days_dict.items()):
            entry = info["entry_open"]
            if info["first_bar_bullish"]:
                pnl_pts, reason, mins, ep, et = simulate_trade(
                    info["bars"], entry, info["pt"], info["sl"], info["ts"], entry_idx=1)
                trades.append({"date": d, "pnl": round(pnl_pts * info["dpp"], 2),
                              "pnl_pts": pnl_pts, "bail": False, "score": info["score"]})
            else:
                bail_pnl = info["entry_close"] - entry
                trades.append({"date": d, "pnl": round(bail_pnl * info["dpp"], 2),
                              "pnl_pts": round(bail_pnl, 2), "bail": True, "score": info["score"]})
        return trades

    def sim_hybrid(days_dict, threshold):
        """C: Hybrid. Score >= threshold → Approach B. Score < threshold → Approach A."""
        trades = []
        for d, info in sorted(days_dict.items()):
            if info["score"] >= threshold:
                # High conviction → B (enter at open)
                entry = info["entry_open"]
                if info["first_bar_bullish"]:
                    pnl_pts, reason, mins, ep, et = simulate_trade(
                        info["bars"], entry, info["pt"], info["sl"], info["ts"], entry_idx=1)
                    trades.append({"date": d, "pnl": round(pnl_pts * info["dpp"], 2),
                                  "pnl_pts": pnl_pts, "bail": False, "score": info["score"]})
                else:
                    bail_pnl = info["entry_close"] - entry
                    trades.append({"date": d, "pnl": round(bail_pnl * info["dpp"], 2),
                                  "pnl_pts": round(bail_pnl, 2), "bail": True, "score": info["score"]})
            else:
                # Low conviction → A (wait & confirm)
                if not info["first_bar_bullish"]:
                    continue
                entry = info["entry_close"]
                pnl_pts, reason, mins, ep, et = simulate_trade(
                    info["bars"], entry, info["pt"], info["sl"], info["ts"], entry_idx=1)
                trades.append({"date": d, "pnl": round(pnl_pts * info["dpp"], 2),
                              "pnl_pts": pnl_pts, "bail": False, "score": info["score"]})
        return trades

    def sim_b_filtered(days_dict, min_score):
        """B but only trade when score >= min_score."""
        filtered = {d: info for d, info in days_dict.items() if info["score"] >= min_score}
        return sim_approach_b(filtered)

    # ── Run everything ──

    p("=" * 80)
    p("SPX OPENING PRINT — HYBRID APPROACH C TESTING")
    p("=" * 80)
    p()

    # Baselines
    p("=" * 80)
    p("BASELINES")
    p("=" * 80)
    report_stats("A) WAIT & CONFIRM (enter 9:31 if bullish)", sim_approach_a(day_info))
    report_stats("B) ENTER & BAIL (enter 9:30, bail if bearish)", sim_approach_b(day_info))

    # B with minimum score filters
    p("=" * 80)
    p("APPROACH B WITH MINIMUM SCORE FILTER")
    p("=" * 80)
    for min_s in [10, 15, 20, 25, 30, 35, 40]:
        count = sum(1 for info in day_info.values() if info["score"] >= min_s)
        report_stats(f"B (score >= {min_s})  [{count} eligible days]", sim_b_filtered(day_info, min_s))

    # Hybrid C with different thresholds
    p("=" * 80)
    p("APPROACH C: HYBRID (B above threshold, A below)")
    p("=" * 80)
    for thresh in [5, 10, 15, 20, 25, 30, 35, 40, 50]:
        high_conv = sum(1 for info in day_info.values() if info["score"] >= thresh)
        low_conv = sum(1 for info in day_info.values() if info["score"] < thresh)
        trades = sim_hybrid(day_info, thresh)
        report_stats(f"C (threshold={thresh})  [B:{high_conv} days, A:{low_conv} days]", trades)

    # Score distribution
    p("=" * 80)
    p("SCORE DISTRIBUTION")
    p("=" * 80)
    scores = [info["score"] for info in day_info.values()]
    bullish_scores = [info["score"] for info in day_info.values() if info["first_bar_bullish"]]
    bearish_scores = [info["score"] for info in day_info.values() if not info["first_bar_bullish"]]
    p(f"  All signal days: {len(scores)}")
    p(f"  Score range: {min(scores)} to {max(scores)}")
    p(f"  Mean score: {mean(scores):.1f}")
    p(f"  Bullish 1st bar days: {len(bullish_scores)} (mean score: {mean(bullish_scores):.1f})")
    p(f"  Bearish 1st bar days: {len(bearish_scores)} (mean score: {mean(bearish_scores):.1f})")
    p()

    # Bail analysis by score bucket
    p("  BAIL DAMAGE BY SCORE BUCKET:")
    for lo, hi in [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 100)]:
        bucket = [info for info in day_info.values()
                  if info["score"] >= lo and info["score"] < hi and not info["first_bar_bullish"]]
        if bucket:
            avg_bail = mean([info["entry_close"] - info["entry_open"] for info in bucket])
            bail_cost = sum((info["entry_close"] - info["entry_open"]) * info["dpp"] for info in bucket)
            p(f"    Score {lo}-{hi}: {len(bucket)} bails, avg {avg_bail:+.1f} pts, total ${bail_cost:,.0f}")
    p()

    # Find the best overall
    p("=" * 80)
    p("RANKING BY TOTAL P&L")
    p("=" * 80)
    results = []
    results.append(("A) Wait & Confirm", sim_approach_a(day_info)))
    results.append(("B) Enter & Bail", sim_approach_b(day_info)))
    for min_s in [10, 15, 20, 25, 30, 35, 40]:
        results.append((f"B (score>={min_s})", sim_b_filtered(day_info, min_s)))
    for thresh in [5, 10, 15, 20, 25, 30, 35, 40, 50]:
        results.append((f"C (thresh={thresh})", sim_hybrid(day_info, thresh)))

    ranked = sorted(results, key=lambda x: sum(t["pnl"] for t in x[1]), reverse=True)
    for i, (name, trades) in enumerate(ranked):
        total_pnl = sum(t["pnl"] for t in trades)
        n = len(trades)
        w = sum(1 for t in trades if t["pnl"] > 0)
        wr = w / n * 100 if n else 0
        gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        cum = 0; peak = 0; max_dd = 0
        for t in trades:
            cum += t["pnl"]
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        bails = sum(1 for t in trades if t.get("bail"))
        marker = " <<<" if i == 0 else ""
        p(f"  {i+1:2d}. {name:25s}  P&L: ${total_pnl:>12,.0f}  Trades: {n:4d}  WR: {wr:5.1f}%  PF: {pf:5.2f}  DD: ${max_dd:>10,.0f}  Bails: {bails:3d}{marker}")

    p()
    p("=" * 80)

    with open(REPORT, "w") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved: {REPORT}")


if __name__ == "__main__":
    main()
