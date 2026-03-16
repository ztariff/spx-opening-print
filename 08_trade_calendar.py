"""
SPX Opening Print Strategy — Phase 8: Trade Calendar Generator
================================================================
Evaluates every trading day against all identified setups, assigns
risk sizing ($25k–$150k) based on signal strength, simulates each
trade with the optimal exits for the signals present, and generates
an interactive HTML calendar.

Requires all data files from prior phases.

Usage:
    python3 08_trade_calendar.py

Output:
    trade_calendar.html
"""

import os
import csv
import json
from collections import defaultdict
from statistics import mean
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "trade_calendar.html")

MIN_RISK = 25000
MAX_RISK = 150000


# ── Data Loaders ───────────────────────────────────────────────────────

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


# ── Trade Simulation ───────────────────────────────────────────────────

def simulate_trade(bars, entry_price, pt, sl, ts, entry_idx=0):
    """Returns (pnl_pts, exit_reason, hold_mins, exit_price, exit_time)"""
    for i in range(entry_idx + 1, min(entry_idx + ts + 1, len(bars))):
        bar = bars[i]
        mins = i - entry_idx
        if bar["low"] <= entry_price - sl:
            return (-sl, "Stop Loss", mins, entry_price - sl, bar["time"])
        if bar["high"] >= entry_price + pt:
            return (pt, "Profit Target", mins, entry_price + pt, bar["time"])
    last = min(entry_idx + ts, len(bars) - 1)
    pnl = bars[last]["close"] - entry_price
    return (round(pnl, 2), "Time Stop", last - entry_idx, bars[last]["close"], bars[last]["time"])


# ── Signal Detection & Scoring ─────────────────────────────────────────

def evaluate_day(d, bars, intra_dates, intra_idx, spx_intraday, vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates):
    """Evaluate all signals for a given day. Returns dict with signals, score, risk, and trade params."""
    if len(bars) < 10:
        return None

    dt = datetime.strptime(d, "%Y-%m-%d")
    idx = intra_idx[d]
    entry_open = bars[0]["open"]
    entry_1min = bars[0]["close"]

    # ── First bar direction (master filter) ────────────────────────
    first_bar_bullish = bars[0]["close"] > bars[0]["open"]
    first_bar_bearish = bars[0]["close"] < bars[0]["open"]

    if not first_bar_bullish:
        return None  # No trade without bullish first bar

    signals = ["Bullish 1st bar"]
    score = 0  # Base score for bullish first bar

    # ── Day of week ────────────────────────────────────────────────
    dow = dt.strftime("%A")
    if dow == "Monday":
        signals.append("Monday")
        score += 15
    elif dow == "Thursday":
        signals.append("Thursday (negative)")
        score -= 20
    elif dow == "Friday":
        score += 5

    # ── Gap ────────────────────────────────────────────────────────
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
                signals.append(f"Gap up ({gap_pts:+.1f} pts)")
                score += 10
            elif gap_dir == "down" and abs(gap_pts) > 30:
                signals.append(f"Large gap down ({gap_pts:+.1f} pts)")
                score -= 15

            # Prior day return
            prev_open = prev_bars[0]["open"]
            prev_ret = (prev_close - prev_open) / prev_open * 100
            if prev_ret < -1.0:
                signals.append(f"Prior big down day ({prev_ret:+.1f}%)")
                score += 10

            # Prior day range
            prev_high = max(b["high"] for b in prev_bars)
            prev_low = min(b["low"] for b in prev_bars)
            prev_range_pct = (prev_high - prev_low) / prev_close * 100
            if prev_range_pct > 1.5:
                signals.append("Wide prior range")
                score += 5

    # ── Streak ─────────────────────────────────────────────────────
    streak = 0
    for j in range(idx - 1, max(idx - 15, -1), -1):
        if j < 0:
            break
        sd = intra_dates[j]
        sb = spx_intraday.get(sd, [])
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

    if streak <= -3:
        signals.append(f"3+ down day streak ({streak})")
        score += 25

    # ── VIX ────────────────────────────────────────────────────────
    vix_dates = sorted(vix_daily.keys())
    if d in vix_daily:
        vix_open = vix_daily[d]["open"]
        signals.append(f"VIX at open: {vix_open:.1f}")

        if 20 <= vix_open < 25:
            signals.append("VIX elevated (20-25)")
            score += 15
        elif 25 <= vix_open < 30:
            signals.append("VIX high (25-30)")
            score += 10
        elif vix_open >= 30:
            signals.append("VIX very high (>30)")
            score += 5

        # VIX change
        vix_idx = None
        for vi, vd in enumerate(vix_dates):
            if vd == d:
                vix_idx = vi
                break
        if vix_idx and vix_idx > 0:
            prev_vix_close = vix_daily[vix_dates[vix_idx - 1]]["close"]
            vix_chg_pct = (vix_open - prev_vix_close) / prev_vix_close * 100
            if -5 < vix_chg_pct < -1:
                signals.append(f"Vol falling ({vix_chg_pct:+.1f}%)")
                score += 20

    # ── Moving Averages ────────────────────────────────────────────
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
            if -2 < pct_from_50 < 0:
                signals.append(f"Just below 50d MA ({pct_from_50:+.1f}%)")
                score += 15
            elif pct_from_50 < -2:
                signals.append(f"Below 50d MA ({pct_from_50:+.1f}%)")
                score += 10
            elif pct_from_50 > 5:
                signals.append(f"Far above 50d MA ({pct_from_50:+.1f}%)")
                score -= 10

        if ma200:
            pct_from_200 = (entry_open - ma200) / ma200 * 100
            if pct_from_200 > 10:
                score -= 5  # Overextended

        if not above_all and not below_all:
            signals.append("Mixed MAs")
            score += 8

    # ── Week/Month/Year color ──────────────────────────────────────
    if d in spx_date_set:
        spx_date_idx_map = {sd: si for si, sd in enumerate(spx_dates)}
        sidx = spx_date_idx_map.get(d)

        # WTD
        wd = dt.weekday()
        if wd > 0:
            mon_date = (dt - timedelta(days=wd)).strftime("%Y-%m-%d")
            if mon_date in spx_daily:
                wtd_ret = (entry_open - spx_daily[mon_date]["open"]) / spx_daily[mon_date]["open"] * 100
                if wtd_ret < -1:
                    signals.append(f"Deep red week ({wtd_ret:+.1f}%)")
                    score += 15
                elif wtd_ret < 0:
                    signals.append(f"Red week ({wtd_ret:+.1f}%)")
                    score += 5

        # MTD
        month_start = dt.replace(day=1).strftime("%Y-%m-%d")
        for sd in spx_dates:
            if sd >= month_start and sd[:7] == d[:7] and sd in spx_daily:
                mtd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if mtd_ret < -1:
                    signals.append(f"Red month ({mtd_ret:+.1f}%)")
                    score += 10
                break

        # YTD
        year_start = f"{dt.year}-01-01"
        for sd in spx_dates:
            if sd >= year_start and sd[:4] == d[:4] and sd in spx_daily:
                ytd_ret = (entry_open - spx_daily[sd]["open"]) / spx_daily[sd]["open"] * 100
                if ytd_ret < -0.5:
                    signals.append(f"Red year ({ytd_ret:+.1f}%)")
                    score += 8
                break

    # ── TLT / Bonds ────────────────────────────────────────────────
    tlt_date_idx_map = {td: ti for ti, td in enumerate(tlt_dates)}
    if d in tlt_date_idx_map:
        tidx = tlt_date_idx_map[d]
        if tidx >= 5:
            tlt_5d_ago = tlt_daily.get(tlt_dates[tidx - 5])
            tlt_prev = tlt_daily.get(tlt_dates[tidx - 1])
            if tlt_5d_ago and tlt_prev:
                tlt_5d_ret = (tlt_prev["close"] - tlt_5d_ago["close"]) / tlt_5d_ago["close"] * 100
                if 0 < tlt_5d_ret < 1:
                    signals.append(f"Bonds mildly up 5d ({tlt_5d_ret:+.1f}%)")
                    score += 8

    # ── 20d range position ─────────────────────────────────────────
    if idx >= 20:
        lookback = intra_dates[idx-20:idx]
        highs = []
        lows = []
        for ld in lookback:
            lb = spx_intraday.get(ld, [])
            if lb:
                highs.append(max(b["high"] for b in lb))
                lows.append(min(b["low"] for b in lb))
        if highs and lows:
            h20 = max(highs)
            l20 = min(lows)
            if h20 != l20:
                pct_in = (entry_open - l20) / (h20 - l20) * 100
                if 10 <= pct_in < 30:
                    signals.append(f"Lower 20d range ({pct_in:.0f}%)")
                    score += 15

    # ── Determine exit parameters based on signal profile ──────────
    # Use the best exits matching the strongest signals present
    signal_set = set(s.split(" (")[0] for s in signals)

    # Default exits
    pt, sl, ts = 50, 10, 240

    if "3+ down day streak" in signal_set:
        pt, sl, ts = 50, 20, 240  # Tier 1 mean reversion
    elif "Vol falling" in signal_set:
        pt, sl, ts = 50, 10, 240  # Tier 1 vol compression
    elif "VIX elevated" in signal_set:
        pt, sl, ts = 20, 15, 30   # Quick scalp
    elif "Just below 50d MA" in signal_set:
        pt, sl, ts = 50, 2, 390   # Tight stop bounce
    elif "Red month" in signal_set or "Deep red week" in signal_set:
        pt, sl, ts = 50, 20, 240  # Mean reversion hold
    elif "Mixed MAs" in signal_set and gap_dir == "up":
        pt, sl, ts = 50, 2, 30    # Quick scalp
    elif gap_dir == "up" and "Monday" in signal_set:
        pt, sl, ts = 15, 20, 390  # Monday momentum
    elif gap_dir == "up":
        pt, sl, ts = 50, 20, 390  # Gap up hold
    elif "Monday" in signal_set:
        pt, sl, ts = 15, 20, 390

    # ── Risk sizing ────────────────────────────────────────────────
    # Score ranges roughly from -20 to 80+
    # Map to $25k–$150k
    n_positive_signals = len([s for s in signals if "negative" not in s.lower()]) - 1  # exclude base "Bullish 1st bar"

    # Base risk from score
    clamped_score = max(0, min(score, 80))
    risk = MIN_RISK + (MAX_RISK - MIN_RISK) * (clamped_score / 80)

    # Boost for multiple signals
    if n_positive_signals >= 5:
        risk = min(risk * 1.3, MAX_RISK)
    elif n_positive_signals >= 3:
        risk = min(risk * 1.15, MAX_RISK)

    risk = max(MIN_RISK, min(MAX_RISK, round(risk / 1000) * 1000))  # Round to nearest $1k

    # ── Simulate the trade ─────────────────────────────────────────
    pnl_pts, exit_reason, hold_mins, exit_price, exit_time = simulate_trade(
        bars, entry_open, pt, sl, ts, entry_idx=0
    )

    # Convert points P&L to dollar P&L based on risk
    # Risk = SL in points × dollars_per_point
    # So dollars_per_point = risk / SL
    dollars_per_point = risk / sl
    pnl_dollars = round(pnl_pts * dollars_per_point, 2)

    return {
        "date": d,
        "day_of_week": dow,
        "entry_price": round(entry_open, 2),
        "exit_price": round(exit_price, 2),
        "entry_time": "09:30",
        "exit_time": exit_time,
        "signals": signals,
        "n_signals": len(signals),
        "score": score,
        "risk": risk,
        "pt": pt,
        "sl": sl,
        "ts": ts,
        "pnl_pts": pnl_pts,
        "pnl_dollars": pnl_dollars,
        "exit_reason": exit_reason,
        "hold_mins": hold_mins,
        "dollars_per_point": round(dollars_per_point, 2),
        "vix": round(vix_daily[d]["open"], 1) if d in vix_daily else None,
    }


# ── HTML Calendar Generator ───────────────────────────────────────────

def generate_html(trades):
    """Generate interactive HTML calendar."""

    # Compute summary stats
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    total_trades = len(trades)
    winners = [t for t in trades if t["pnl_dollars"] > 0]
    losers = [t for t in trades if t["pnl_dollars"] <= 0]
    win_rate = len(winners) / total_trades * 100 if total_trades else 0
    avg_win = mean([t["pnl_dollars"] for t in winners]) if winners else 0
    avg_loss = mean([t["pnl_dollars"] for t in losers]) if losers else 0
    max_win = max([t["pnl_dollars"] for t in trades]) if trades else 0
    max_loss = min([t["pnl_dollars"] for t in trades]) if trades else 0
    total_risked = sum(t["risk"] for t in trades)
    avg_risk = mean([t["risk"] for t in trades]) if trades else 0

    # Monthly P&L
    monthly_pnl = defaultdict(float)
    monthly_trades = defaultdict(int)
    for t in trades:
        m = t["date"][:7]
        monthly_pnl[m] += t["pnl_dollars"]
        monthly_trades[m] += 1

    # Build trade lookup by date
    trade_map = {}
    for t in trades:
        trade_map[t["date"]] = t

    trades_json = json.dumps(trades, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SPX Opening Print — Trade Calendar</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; }}

.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 30px 40px;
    border-bottom: 1px solid #2a2a4a;
}}
.header h1 {{ font-size: 28px; font-weight: 600; color: #fff; margin-bottom: 8px; }}
.header .subtitle {{ color: #8888aa; font-size: 14px; }}

.stats-bar {{
    display: flex; flex-wrap: wrap; gap: 12px;
    padding: 20px 40px;
    background: #111118;
    border-bottom: 1px solid #1a1a2a;
}}
.stat-card {{
    background: #1a1a28; border-radius: 8px; padding: 14px 20px;
    min-width: 140px; flex: 1;
    border: 1px solid #2a2a3a;
}}
.stat-card .label {{ font-size: 11px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.stat-card .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
.stat-card .value.green {{ color: #00d4aa; }}
.stat-card .value.red {{ color: #ff4466; }}
.stat-card .value.neutral {{ color: #e0e0e0; }}

.main {{ display: flex; padding: 20px 40px; gap: 24px; }}
.calendar-panel {{ flex: 1; }}
.detail-panel {{
    width: 380px; min-width: 380px;
    background: #13131f; border-radius: 12px;
    border: 1px solid #2a2a3a; padding: 24px;
    position: sticky; top: 20px; max-height: calc(100vh - 40px);
    overflow-y: auto;
}}

.month-nav {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px;
}}
.month-nav button {{
    background: #2a2a3e; border: 1px solid #3a3a5a; color: #ccc;
    padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px;
}}
.month-nav button:hover {{ background: #3a3a5e; }}
.month-nav .month-title {{ font-size: 20px; font-weight: 600; }}

.cal-grid {{
    display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;
}}
.cal-header {{ text-align: center; font-size: 12px; color: #6666aa; padding: 8px 0; font-weight: 600; }}
.cal-day {{
    aspect-ratio: 1;
    border-radius: 8px; padding: 6px;
    font-size: 12px; cursor: default;
    display: flex; flex-direction: column;
    position: relative; min-height: 80px;
    border: 1px solid transparent;
    transition: all 0.15s;
}}
.cal-day.empty {{ background: transparent; }}
.cal-day.no-trade {{ background: #111118; color: #444; }}
.cal-day.win {{
    background: linear-gradient(135deg, #0a2a1a 0%, #0d3320 100%);
    border-color: #1a5a3a; cursor: pointer;
}}
.cal-day.loss {{
    background: linear-gradient(135deg, #2a0a0f 0%, #331015 100%);
    border-color: #5a1a2a; cursor: pointer;
}}
.cal-day.win:hover {{ border-color: #00d4aa; transform: scale(1.03); }}
.cal-day.loss:hover {{ border-color: #ff4466; transform: scale(1.03); }}
.cal-day.selected {{ border-color: #5577ff !important; box-shadow: 0 0 12px rgba(85,119,255,0.3); }}

.cal-day .day-num {{ font-weight: 600; font-size: 13px; }}
.cal-day .day-pnl {{
    font-size: 11px; font-weight: 700; margin-top: auto;
}}
.cal-day .day-pnl.green {{ color: #00d4aa; }}
.cal-day .day-pnl.red {{ color: #ff4466; }}
.cal-day .day-risk {{
    font-size: 9px; color: #888; margin-top: 2px;
}}
.cal-day .signal-dots {{
    display: flex; gap: 2px; margin-top: 3px; flex-wrap: wrap;
}}
.cal-day .signal-dot {{
    width: 5px; height: 5px; border-radius: 50%;
    background: #5577ff;
}}

.detail-panel h3 {{ font-size: 16px; margin-bottom: 16px; color: #aaa; }}
.detail-panel.active h3 {{ color: #fff; }}
.trade-detail {{ display: none; }}
.trade-detail.active {{ display: block; }}

.detail-row {{
    display: flex; justify-content: space-between;
    padding: 10px 0; border-bottom: 1px solid #1a1a2a;
    font-size: 13px;
}}
.detail-row .dlabel {{ color: #6666aa; }}
.detail-row .dvalue {{ font-weight: 600; text-align: right; }}

.signals-list {{ margin-top: 16px; }}
.signals-list h4 {{ font-size: 13px; color: #6666aa; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }}
.signal-tag {{
    display: inline-block; padding: 4px 10px; margin: 3px 4px 3px 0;
    background: #1a1a3a; border-radius: 12px; font-size: 11px;
    border: 1px solid #2a2a5a; color: #aabbff;
}}
.signal-tag.negative {{ background: #2a1a1a; border-color: #5a2a2a; color: #ffaa88; }}

.pnl-big {{
    font-size: 32px; font-weight: 800; margin: 12px 0;
}}
.pnl-big.green {{ color: #00d4aa; }}
.pnl-big.red {{ color: #ff4466; }}

.monthly-summary {{
    margin-top: 24px; padding: 20px 40px 40px;
}}
.monthly-summary h2 {{ font-size: 20px; margin-bottom: 16px; }}
.monthly-table {{
    width: 100%; border-collapse: collapse;
}}
.monthly-table th, .monthly-table td {{
    padding: 10px 16px; text-align: left; font-size: 13px;
    border-bottom: 1px solid #1a1a2a;
}}
.monthly-table th {{ color: #6666aa; font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: 1px; }}
</style>
</head>
<body>

<div class="header">
    <h1>SPX Opening Print Strategy</h1>
    <div class="subtitle">Trade Calendar &mdash; {trades[0]["date"]} to {trades[-1]["date"]} &mdash; {total_trades} trades</div>
</div>

<div class="stats-bar">
    <div class="stat-card">
        <div class="label">Total P&L</div>
        <div class="value {'green' if total_pnl >= 0 else 'red'}">${total_pnl:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Win Rate</div>
        <div class="value neutral">{win_rate:.1f}%</div>
    </div>
    <div class="stat-card">
        <div class="label">Total Trades</div>
        <div class="value neutral">{total_trades}</div>
    </div>
    <div class="stat-card">
        <div class="label">Avg Win</div>
        <div class="value green">${avg_win:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Avg Loss</div>
        <div class="value red">${avg_loss:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Best Trade</div>
        <div class="value green">${max_win:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Worst Trade</div>
        <div class="value red">${max_loss:,.0f}</div>
    </div>
    <div class="stat-card">
        <div class="label">Avg Risk/Trade</div>
        <div class="value neutral">${avg_risk:,.0f}</div>
    </div>
</div>

<div class="main">
    <div class="calendar-panel">
        <div class="month-nav">
            <button onclick="prevMonth()">&larr; Prev</button>
            <div class="month-title" id="monthTitle"></div>
            <button onclick="nextMonth()">Next &rarr;</button>
        </div>
        <div class="cal-grid" id="calGrid"></div>
    </div>
    <div class="detail-panel" id="detailPanel">
        <h3 id="detailTitle">Click a trade day to view details</h3>
        <div class="trade-detail" id="tradeDetail"></div>
    </div>
</div>

<div class="monthly-summary">
    <h2>Monthly Summary</h2>
    <table class="monthly-table">
        <thead>
            <tr><th>Month</th><th>Trades</th><th>P&L</th><th>Cumulative</th></tr>
        </thead>
        <tbody>
"""

    cum = 0
    for m in sorted(monthly_pnl.keys()):
        cum += monthly_pnl[m]
        color = "green" if monthly_pnl[m] >= 0 else "red"
        cum_color = "green" if cum >= 0 else "red"
        html += f"""            <tr>
                <td>{m}</td>
                <td>{monthly_trades[m]}</td>
                <td style="color: {'#00d4aa' if monthly_pnl[m]>=0 else '#ff4466'}; font-weight:700">${monthly_pnl[m]:,.0f}</td>
                <td style="color: {'#00d4aa' if cum>=0 else '#ff4466'}; font-weight:700">${cum:,.0f}</td>
            </tr>
"""

    html += f"""        </tbody>
    </table>
</div>

<script>
const trades = {trades_json};
const tradeMap = {{}};
trades.forEach(t => {{ tradeMap[t.date] = t; }});

let currentYear, currentMonth;

// Find first trade month
const firstDate = new Date(trades[0].date + 'T00:00:00');
currentYear = firstDate.getFullYear();
currentMonth = firstDate.getMonth();

function renderCalendar() {{
    const grid = document.getElementById('calGrid');
    const title = document.getElementById('monthTitle');
    const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    title.textContent = months[currentMonth] + ' ' + currentYear;

    let html = '';
    ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(d => {{
        html += '<div class="cal-header">' + d + '</div>';
    }});

    const firstDay = new Date(currentYear, currentMonth, 1);
    let startDay = firstDay.getDay() - 1;
    if (startDay < 0) startDay = 6;

    const daysInMonth = new Date(currentYear, currentMonth + 1, 0).getDate();

    for (let i = 0; i < startDay; i++) {{
        html += '<div class="cal-day empty"></div>';
    }}

    for (let d = 1; d <= daysInMonth; d++) {{
        const dateStr = currentYear + '-' + String(currentMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
        const trade = tradeMap[dateStr];

        if (trade) {{
            const cls = trade.pnl_dollars >= 0 ? 'win' : 'loss';
            const pnlCls = trade.pnl_dollars >= 0 ? 'green' : 'red';
            const pnlStr = (trade.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(trade.pnl_dollars).toLocaleString(undefined, {{maximumFractionDigits:0}});
            const nSigs = Math.min(trade.n_signals - 1, 8);
            let dots = '';
            for (let s = 0; s < nSigs; s++) dots += '<div class="signal-dot"></div>';

            html += '<div class="cal-day ' + cls + '" onclick="showTrade(\\'' + dateStr + '\\')" id="day-' + dateStr + '">';
            html += '<div class="day-num">' + d + '</div>';
            html += '<div class="signal-dots">' + dots + '</div>';
            html += '<div class="day-pnl ' + pnlCls + '">' + pnlStr + '</div>';
            html += '<div class="day-risk">$' + (trade.risk/1000).toFixed(0) + 'k risk</div>';
            html += '</div>';
        }} else {{
            const dow = new Date(currentYear, currentMonth, d).getDay();
            if (dow === 0 || dow === 6) {{
                html += '<div class="cal-day empty"></div>';
            }} else {{
                html += '<div class="cal-day no-trade"><div class="day-num" style="color:#333">' + d + '</div></div>';
            }}
        }}
    }}

    grid.innerHTML = html;
}}

function showTrade(dateStr) {{
    // Remove previous selection
    document.querySelectorAll('.cal-day.selected').forEach(el => el.classList.remove('selected'));
    const dayEl = document.getElementById('day-' + dateStr);
    if (dayEl) dayEl.classList.add('selected');

    const t = tradeMap[dateStr];
    if (!t) return;

    const panel = document.getElementById('detailPanel');
    panel.classList.add('active');
    document.getElementById('detailTitle').textContent = t.day_of_week + ', ' + dateStr;

    const pnlCls = t.pnl_dollars >= 0 ? 'green' : 'red';
    const pnlStr = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined, {{maximumFractionDigits:0}});
    const ptsPnl = (t.pnl_pts >= 0 ? '+' : '') + t.pnl_pts.toFixed(1) + ' pts';

    let signalTags = '';
    t.signals.forEach(s => {{
        const isNeg = s.toLowerCase().includes('negative') || s.toLowerCase().includes('far above');
        signalTags += '<span class="signal-tag' + (isNeg ? ' negative' : '') + '">' + s + '</span>';
    }});

    const holdStr = t.hold_mins >= 60
        ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm'
        : t.hold_mins + ' min';

    let html = '';
    html += '<div class="pnl-big ' + pnlCls + '">' + pnlStr + '</div>';
    html += '<div style="color:#888; font-size:13px; margin-bottom:16px">' + ptsPnl + ' &mdash; ' + t.exit_reason + '</div>';

    html += '<div class="detail-row"><span class="dlabel">Entry</span><span class="dvalue">' + t.entry_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + ' @ ' + t.entry_time + '</span></div>';
    html += '<div class="detail-row"><span class="dlabel">Exit</span><span class="dvalue">' + t.exit_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + ' @ ' + t.exit_time + '</span></div>';
    html += '<div class="detail-row"><span class="dlabel">Hold Time</span><span class="dvalue">' + holdStr + '</span></div>';
    html += '<div class="detail-row"><span class="dlabel">Risk</span><span class="dvalue">$' + t.risk.toLocaleString() + '</span></div>';
    html += '<div class="detail-row"><span class="dlabel">$/Point</span><span class="dvalue">$' + t.dollars_per_point.toLocaleString(undefined,{{minimumFractionDigits:0}}) + '</span></div>';
    html += '<div class="detail-row"><span class="dlabel">PT / SL / TS</span><span class="dvalue">' + t.pt + ' / ' + t.sl + ' / ' + t.ts + 'min</span></div>';
    html += '<div class="detail-row"><span class="dlabel">Score</span><span class="dvalue">' + t.score + '</span></div>';
    if (t.vix) html += '<div class="detail-row"><span class="dlabel">VIX at Open</span><span class="dvalue">' + t.vix + '</span></div>';

    html += '<div class="signals-list"><h4>Signals (' + t.n_signals + ')</h4>' + signalTags + '</div>';

    document.getElementById('tradeDetail').innerHTML = html;
    document.getElementById('tradeDetail').classList.add('active');
}}

function prevMonth() {{
    currentMonth--;
    if (currentMonth < 0) {{ currentMonth = 11; currentYear--; }}
    renderCalendar();
}}
function nextMonth() {{
    currentMonth++;
    if (currentMonth > 11) {{ currentMonth = 0; currentYear++; }}
    renderCalendar();
}}

renderCalendar();
</script>
</body>
</html>"""
    return html


# ── Main ───────────────────────────────────────────────────────────────

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
    print(f"VIX daily: {len(vix_daily)} days")
    print(f"SPX daily: {len(spx_daily)} days")
    print(f"TLT daily: {len(tlt_daily)} days")

    print("\nEvaluating trades...")
    trades = []
    for d in intra_dates:
        bars = spx_intraday[d]
        result = evaluate_day(
            d, bars, intra_dates, intra_idx, spx_intraday,
            vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates
        )
        if result:
            trades.append(result)

    print(f"\nTotal trades: {len(trades)}")
    winners = sum(1 for t in trades if t["pnl_dollars"] > 0)
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    print(f"Winners: {winners} ({winners/len(trades)*100:.1f}%)")
    print(f"Total P&L: ${total_pnl:,.0f}")

    print("\nGenerating HTML calendar...")
    html = generate_html(trades)
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    print(f"\nSaved: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
