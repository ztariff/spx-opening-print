"""
SPX Opening Print Strategy — Phase 9: Trade Calendar with TradingView Charts
==============================================================================
Approach C (Hybrid): Score >= 25 uses Approach B (enter at open, bail if bearish).
Score < 25 uses Approach A (wait for bullish 1st bar confirmation, enter at 9:31).
Interactive calendar with embedded TradingView Lightweight Charts, equity curves,
monthly stats, and OHLC hover legend.

Requires: spx_1min_bars.csv + all data from prior phases.

Usage:
    python3 09_trade_calendar_charts.py

Output:
    trade_calendar.html  (overwrites previous version)
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


# ── Data Loaders (same as 08) ─────────────────────────────────────────

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
        mins = i - entry_idx
        if bar["low"] <= entry_price - sl:
            return (-sl, "Stop Loss", mins, entry_price - sl, bar["time"])
        if bar["high"] >= entry_price + pt:
            return (pt, "Profit Target", mins, entry_price + pt, bar["time"])
    last = min(entry_idx + ts, len(bars) - 1)
    pnl = bars[last]["close"] - entry_price
    return (round(pnl, 2), "Time Stop", last - entry_idx, bars[last]["close"], bars[last]["time"])


# ── Signal Detection & Scoring (same as 08) ───────────────────────────

HYBRID_THRESHOLD = 25

def evaluate_day(d, bars, intra_dates, intra_idx, spx_intraday, vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates):
    """Approach C (Hybrid): Score >= 25 → Approach B (enter at open, bail if bearish).
    Score < 25 → Approach A (wait for bullish confirmation, enter at 9:31 close)."""
    if len(bars) < 10:
        return None

    dt = datetime.strptime(d, "%Y-%m-%d")
    idx = intra_idx[d]
    entry_open = bars[0]["open"]

    first_bar_bullish = bars[0]["close"] > bars[0]["open"]

    signals = []
    if first_bar_bullish:
        signals.append("Bullish 1st bar")
    else:
        signals.append("Bearish 1st bar (bail)")
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

    vix_dates = sorted(vix_daily.keys())
    if d in vix_daily:
        vix_open = vix_daily[d]["open"]
        signals.append(f"VIX at open: {vix_open:.1f}")
        if 20 <= vix_open < 25: signals.append("VIX elevated (20-25)"); score += 15
        elif 25 <= vix_open < 30: signals.append("VIX high (25-30)"); score += 10
        elif vix_open >= 30: signals.append("VIX very high (>30)"); score += 5
        vix_idx = None
        for vi, vd in enumerate(vix_dates):
            if vd == d: vix_idx = vi; break
        if vix_idx and vix_idx > 0:
            prev_vix_close = vix_daily[vix_dates[vix_idx - 1]]["close"]
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
        spx_date_idx_map = {sd: si for si, sd in enumerate(spx_dates)}
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

    # Need at least one positive signal to trade
    n_positive = len([s for s in signals if "negative" not in s.lower() and "bail" not in s.lower()])
    if n_positive < 1:
        return None

    clamped_score = max(0, min(score, 80))
    risk = MIN_RISK + (MAX_RISK - MIN_RISK) * (clamped_score / 80)
    if n_positive >= 5: risk = min(risk * 1.3, MAX_RISK)
    elif n_positive >= 3: risk = min(risk * 1.15, MAX_RISK)
    risk = max(MIN_RISK, min(MAX_RISK, round(risk / 1000) * 1000))

    dollars_per_point = risk / sl

    if score >= HYBRID_THRESHOLD:
        # Approach B: enter at 9:30 open, bail if bearish
        entry_price_used = entry_open
        entry_time_used = "09:30"
        if first_bar_bullish:
            pnl_pts, exit_reason, hold_mins, exit_price, exit_time = simulate_trade(
                bars, entry_price_used, pt, sl, ts, entry_idx=1
            )
        else:
            bail_price = bars[0]["close"]
            pnl_pts = round(bail_price - entry_price_used, 2)
            exit_reason = "Bail (bearish 1st bar)"
            hold_mins = 1
            exit_price = bail_price
            exit_time = bars[0]["time"]
    else:
        # Approach A: wait for bullish confirmation, skip if bearish
        if not first_bar_bullish:
            return None
        entry_price_used = bars[0]["close"]
        entry_time_used = "09:31"
        pnl_pts, exit_reason, hold_mins, exit_price, exit_time = simulate_trade(
            bars, entry_price_used, pt, sl, ts, entry_idx=1
        )

    pnl_dollars = round(pnl_pts * dollars_per_point, 2)

    return {
        "date": d,
        "day_of_week": dow,
        "entry_price": round(entry_price_used, 2),
        "exit_price": round(exit_price, 2),
        "entry_time": entry_time_used,
        "exit_time": exit_time,
        "signals": signals,
        "n_signals": len(signals),
        "score": score,
        "risk": risk,
        "pt": pt, "sl": sl, "ts": ts,
        "pnl_pts": pnl_pts,
        "pnl_dollars": pnl_dollars,
        "exit_reason": exit_reason,
        "hold_mins": hold_mins,
        "dollars_per_point": round(dollars_per_point, 2),
        "vix": round(vix_daily[d]["open"], 1) if d in vix_daily else None,
    }


# ── Build chart bar data for trade days ────────────────────────────────

def build_chart_data(spx_intraday, trade_dates):
    """Build compact 1-min bar arrays for each trade day.
    Returns dict: date → list of [time_unix, o, h, l, c]
    Timestamps are UTC seconds that represent ET wall-clock times
    so lightweight-charts displays correct ET times."""
    import calendar
    chart_data = {}
    for d in trade_dates:
        bars = spx_intraday.get(d, [])
        if not bars:
            continue
        dt_base = datetime.strptime(d, "%Y-%m-%d")
        day_bars = []
        for bar in bars:
            h, m = bar["time"].split(":")
            # Build a struct_time in UTC that has the ET hours/minutes
            # This way lightweight-charts (which interprets as UTC) shows ET times
            bar_dt = dt_base.replace(hour=int(h), minute=int(m), second=0)
            ts = int(calendar.timegm(bar_dt.timetuple()))
            day_bars.append([
                ts,
                round(bar["open"], 2),
                round(bar["high"], 2),
                round(bar["low"], 2),
                round(bar["close"], 2),
            ])
        chart_data[d] = day_bars
    return chart_data


# ── HTML Generation ────────────────────────────────────────────────────

def generate_html(trades, chart_data):
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    total_trades = len(trades)
    winners = [t for t in trades if t["pnl_dollars"] > 0]
    losers = [t for t in trades if t["pnl_dollars"] <= 0]
    win_rate = len(winners) / total_trades * 100 if total_trades else 0
    avg_win = mean([t["pnl_dollars"] for t in winners]) if winners else 0
    avg_loss = mean([t["pnl_dollars"] for t in losers]) if losers else 0
    max_win = max(t["pnl_dollars"] for t in trades) if trades else 0
    max_loss = min(t["pnl_dollars"] for t in trades) if trades else 0
    avg_risk = mean([t["risk"] for t in trades]) if trades else 0

    monthly_pnl = defaultdict(float)
    monthly_trades = defaultdict(int)
    for t in trades:
        m = t["date"][:7]
        monthly_pnl[m] += t["pnl_dollars"]
        monthly_trades[m] += 1

    trades_json = json.dumps(trades, default=str)
    chart_json_str = json.dumps(chart_data)
    # Build monthly summary rows
    monthly_rows = ""
    cum = 0
    for m in sorted(monthly_pnl.keys()):
        cum += monthly_pnl[m]
        mc = '#00d4aa' if monthly_pnl[m] >= 0 else '#ff4466'
        cc = '#00d4aa' if cum >= 0 else '#ff4466'
        monthly_rows += f'<tr><td>{m}</td><td>{monthly_trades[m]}</td>'
        monthly_rows += f'<td style="color:{mc};font-weight:700">${monthly_pnl[m]:,.0f}</td>'
        monthly_rows += f'<td style="color:{cc};font-weight:700">${cum:,.0f}</td></tr>\n'

    pnl_cls = 'green' if total_pnl >= 0 else 'red'

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SPX Opening Print — Trade Calendar</title>
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; }}

.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 24px 32px; border-bottom: 1px solid #2a2a4a;
}}
.header h1 {{ font-size: 24px; font-weight: 600; color: #fff; margin-bottom: 4px; }}
.header .subtitle {{ color: #8888aa; font-size: 13px; }}

.stats-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 32px;
    background: #111118; border-bottom: 1px solid #1a1a2a;
}}
.stat-card {{
    background: #1a1a28; border-radius: 6px; padding: 10px 16px; min-width: 120px; flex: 1;
    border: 1px solid #2a2a3a;
}}
.stat-card .label {{ font-size: 10px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.stat-card .value {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}
.stat-card .value.green {{ color: #00d4aa; }}
.stat-card .value.red {{ color: #ff4466; }}
.stat-card .value.neutral {{ color: #e0e0e0; }}

.main {{ padding: 16px 32px; }}
.calendar-panel {{ }}
.detail-panel {{
    background: #13131f; border-radius: 10px;
    border: 1px solid #2a2a3a; padding: 20px; margin-top: 16px;
    display: none;
}}
.detail-panel.visible {{ display: block; }}

/* Trade info bar — horizontal layout above chart */
.trade-info-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; margin-bottom: 14px;
}}
.trade-info-left {{
    flex: 0 0 auto; margin-right: 16px;
}}
.trade-info-details {{
    display: flex; flex-wrap: wrap; gap: 6px 16px; flex: 1; align-items: center;
}}
.trade-info-details .di {{
    font-size: 12px; white-space: nowrap;
}}
.trade-info-details .di .dlabel {{ color: #6666aa; margin-right: 4px; }}
.trade-info-details .di .dvalue {{ font-weight: 600; }}
.trade-info-signals {{
    width: 100%; margin-top: 4px;
}}

.month-nav {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }}
.month-nav button {{
    background: #2a2a3e; border: 1px solid #3a3a5a; color: #ccc;
    padding: 6px 14px; border-radius: 5px; cursor: pointer; font-size: 13px;
}}
.month-nav button:hover {{ background: #3a3a5e; }}
.month-nav .month-title {{ font-size: 18px; font-weight: 600; }}

.cal-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 3px; }}
.cal-header {{ text-align: center; font-size: 11px; color: #6666aa; padding: 6px 0; font-weight: 600; }}
.cal-day {{
    border-radius: 6px; padding: 5px; font-size: 11px;
    cursor: default; display: flex; flex-direction: column;
    min-height: 80px; border: 1px solid transparent; transition: all 0.15s;
}}

/* ── Month stats bar ── */
.month-stats {{
    display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px;
}}
.month-stats .ms {{
    background: #1a1a28; border-radius: 6px; padding: 8px 14px; flex: 1; min-width: 100px;
    border: 1px solid #2a2a3a;
}}
.month-stats .ms .msl {{ font-size: 9px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.month-stats .ms .msv {{ font-size: 16px; font-weight: 700; margin-top: 2px; }}
.month-stats .ms .msv.green {{ color: #00d4aa; }}
.month-stats .ms .msv.red {{ color: #ff4466; }}
.month-stats .ms .msv.neutral {{ color: #e0e0e0; }}
.cal-day.empty {{ background: transparent; }}
.cal-day.no-trade {{ background: #111118; color: #333; }}
.cal-day.win {{ background: linear-gradient(135deg, #0a2a1a 0%, #0d3320 100%); border-color: #1a5a3a; cursor: pointer; }}
.cal-day.loss {{ background: linear-gradient(135deg, #2a0a0f 0%, #331015 100%); border-color: #5a1a2a; cursor: pointer; }}
.cal-day.win:hover {{ border-color: #00d4aa; transform: scale(1.02); }}
.cal-day.loss:hover {{ border-color: #ff4466; transform: scale(1.02); }}
.cal-day.selected {{ border-color: #5577ff !important; box-shadow: 0 0 10px rgba(85,119,255,0.3); }}
.cal-day .day-num {{ font-weight: 600; font-size: 12px; }}
.cal-day .day-pnl {{ font-size: 10px; font-weight: 700; margin-top: auto; }}
.cal-day .day-pnl.green {{ color: #00d4aa; }}
.cal-day .day-pnl.red {{ color: #ff4466; }}
.cal-day .day-risk {{ font-size: 8px; color: #888; }}
.cal-day .signal-dots {{ display: flex; gap: 2px; margin-top: 2px; flex-wrap: wrap; }}
.cal-day .signal-dot {{ width: 4px; height: 4px; border-radius: 50%; background: #5577ff; }}

.detail-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1a1a2a; font-size: 12px; }}
.detail-row .dlabel {{ color: #6666aa; }}
.detail-row .dvalue {{ font-weight: 600; text-align: right; }}
.signals-list {{ margin-top: 10px; }}
.signals-list h4 {{ font-size: 11px; color: #6666aa; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 1px; }}
.signal-tag {{
    display: inline-block; padding: 3px 8px; margin: 2px 3px 2px 0;
    background: #1a1a3a; border-radius: 10px; font-size: 10px; border: 1px solid #2a2a5a; color: #aabbff;
}}
.signal-tag.negative {{ background: #2a1a1a; border-color: #5a2a2a; color: #ffaa88; }}
.pnl-big {{ font-size: 28px; font-weight: 800; margin: 8px 0; }}
.pnl-big.green {{ color: #00d4aa; }}
.pnl-big.red {{ color: #ff4466; }}

/* ── Inline chart ── */
#chartWrap {{
    margin-top: 14px; border-radius: 8px; overflow: hidden;
    border: 1px solid #2a2e3d; background: #131722;
}}
#chartWrap .ch-header {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; background: #1e2130; border-bottom: 1px solid #2a2e3d;
    font-size: 12px;
}}
#chartWrap .ch-header .ch-title {{ font-weight: 600; font-size: 13px; }}
#chartWrap .ch-header .ch-sub {{ color: #888; margin-left: 10px; }}
#chartContainer {{ width: 100%; height: 500px; position: relative; }}
#ohlcLegend {{
    position: absolute; top: 8px; left: 12px; z-index: 10;
    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
    font-size: 11px; color: #aaa; pointer-events: none;
    display: flex; gap: 10px;
}}
#ohlcLegend span {{ white-space: nowrap; }}
#ohlcLegend .ol {{ color: #666; }}
#ohlcLegend .ov {{ font-weight: 600; }}

/* ── Legend labels overlaid on chart ── */
.chart-legend {{
    position: absolute; top: 8px; left: 12px; z-index: 10;
    display: flex; gap: 14px; font-size: 11px; font-weight: 600; pointer-events: none;
}}
.chart-legend .leg-entry {{ color: #2962ff; }}
.chart-legend .leg-pt {{ color: #00d4aa; }}
.chart-legend .leg-sl {{ color: #ff4466; }}

/* ── Equity curves ── */
.equity-section {{
    padding: 0 32px; margin-top: 12px;
}}
.equity-section h3 {{
    font-size: 14px; font-weight: 600; color: #aaa; margin-bottom: 8px;
}}
.equity-canvas-wrap {{
    background: #131722; border-radius: 8px; border: 1px solid #2a2e3d;
    padding: 12px 12px 8px 12px; position: relative;
}}
.equity-canvas-wrap canvas {{
    width: 100%; display: block;
}}
.eq-label {{
    position: absolute; font-size: 10px; font-weight: 600; pointer-events: none;
}}

.monthly-summary {{ padding: 16px 32px 32px; }}
.monthly-summary h2 {{ font-size: 18px; margin-bottom: 12px; }}
.monthly-table {{ width: 100%; border-collapse: collapse; }}
.monthly-table th, .monthly-table td {{
    padding: 8px 14px; text-align: left; font-size: 12px; border-bottom: 1px solid #1a1a2a;
}}
.monthly-table th {{ color: #6666aa; font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 1px; }}

.placeholder-msg {{
    color: #555; text-align: center; padding: 60px 20px; font-size: 14px;
}}
</style>
</head>
<body>

<div class="header">
    <h1>SPX Opening Print Strategy</h1>
    <div class="subtitle">Trade Calendar &mdash; {trades[0]["date"]} to {trades[-1]["date"]} &mdash; {total_trades} trades &mdash; click any trade day</div>
</div>

<div class="stats-bar">
    <div class="stat-card"><div class="label">Total P&L</div><div class="value {pnl_cls}">${total_pnl:,.0f}</div></div>
    <div class="stat-card"><div class="label">Win Rate</div><div class="value neutral">{win_rate:.1f}%</div></div>
    <div class="stat-card"><div class="label">Trades</div><div class="value neutral">{total_trades}</div></div>
    <div class="stat-card"><div class="label">Avg Win</div><div class="value green">${avg_win:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Loss</div><div class="value red">${avg_loss:,.0f}</div></div>
    <div class="stat-card"><div class="label">Best</div><div class="value green">${max_win:,.0f}</div></div>
    <div class="stat-card"><div class="label">Worst</div><div class="value red">${max_loss:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Risk</div><div class="value neutral">${avg_risk:,.0f}</div></div>
</div>

<div class="equity-section">
    <h3>Strategy Equity Curve</h3>
    <div class="equity-canvas-wrap">
        <canvas id="equityCurveAll" height="150"></canvas>
    </div>
</div>

<div class="main">
    <div class="calendar-panel">
        <div class="month-nav">
            <button onclick="prevMonth()">&larr; Prev</button>
            <div class="month-title" id="monthTitle"></div>
            <button onclick="nextMonth()">Next &rarr;</button>
        </div>
        <div class="month-stats" id="monthStats"></div>
        <div class="equity-canvas-wrap" id="monthCurveWrap" style="margin-bottom:12px;display:none">
            <canvas id="equityCurveMonth" height="120"></canvas>
        </div>
        <div class="cal-grid" id="calGrid"></div>
    </div>
    <div class="detail-panel" id="detailPanel">
        <div id="detailContent"></div>
    </div>
</div>

<div class="monthly-summary">
    <h2>Monthly Summary</h2>
    <table class="monthly-table">
        <thead><tr><th>Month</th><th>Trades</th><th>P&L</th><th>Cumulative</th></tr></thead>
        <tbody>{monthly_rows}</tbody>
    </table>
</div>

<script>
const trades = {trades_json};
const chartBars = {chart_json_str};
const tradeMap = {{}};
trades.forEach(t => {{ tradeMap[t.date] = t; }});

let currentYear, currentMonth;
let tvChart = null, tvCandleSeries = null;

const lastDate = new Date(trades[trades.length - 1].date + 'T00:00:00');
currentYear = lastDate.getFullYear();
currentMonth = lastDate.getMonth();

function renderCalendar() {{
    const grid = document.getElementById('calGrid');
    const title = document.getElementById('monthTitle');
    const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    title.textContent = months[currentMonth] + ' ' + currentYear;

    // ── Compute month stats ──
    const monthKey = currentYear + '-' + String(currentMonth+1).padStart(2,'0');
    const monthTrades = trades.filter(t => t.date.startsWith(monthKey));
    const mCount = monthTrades.length;
    const mWins = monthTrades.filter(t => t.pnl_dollars > 0).length;
    const mPnl = monthTrades.reduce((s, t) => s + t.pnl_dollars, 0);
    const mWR = mCount > 0 ? (mWins / mCount * 100).toFixed(1) : '0.0';
    const mAvgWin = mWins > 0 ? (monthTrades.filter(t => t.pnl_dollars > 0).reduce((s, t) => s + t.pnl_dollars, 0) / mWins) : 0;
    const mLosers = monthTrades.filter(t => t.pnl_dollars <= 0);
    const mAvgLoss = mLosers.length > 0 ? (mLosers.reduce((s, t) => s + t.pnl_dollars, 0) / mLosers.length) : 0;
    const mBest = mCount > 0 ? Math.max(...monthTrades.map(t => t.pnl_dollars)) : 0;
    const mWorst = mCount > 0 ? Math.min(...monthTrades.map(t => t.pnl_dollars)) : 0;
    const mAvgRisk = mCount > 0 ? (monthTrades.reduce((s, t) => s + t.risk, 0) / mCount) : 0;

    const pnlCls = mPnl >= 0 ? 'green' : 'red';
    let statsHtml = '';
    statsHtml += '<div class="ms"><div class="msl">Month P&L</div><div class="msv ' + pnlCls + '">' + (mPnl >= 0 ? '+$' : '-$') + Math.abs(mPnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Trades</div><div class="msv neutral">' + mCount + '</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Win Rate</div><div class="msv neutral">' + mWR + '%</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Wins / Losses</div><div class="msv neutral">' + mWins + ' / ' + (mCount - mWins) + '</div></div>';
    if (mWins > 0) statsHtml += '<div class="ms"><div class="msl">Avg Win</div><div class="msv green">+$' + Math.abs(mAvgWin).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    if (mLosers.length > 0) statsHtml += '<div class="ms"><div class="msl">Avg Loss</div><div class="msv red">-$' + Math.abs(mAvgLoss).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    if (mCount > 0) {{
        statsHtml += '<div class="ms"><div class="msl">Best</div><div class="msv green">+$' + Math.abs(mBest).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
        statsHtml += '<div class="ms"><div class="msl">Worst</div><div class="msv red">' + (mWorst >= 0 ? '+$' : '-$') + Math.abs(mWorst).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    }}
    document.getElementById('monthStats').innerHTML = mCount > 0 ? statsHtml : '<div class="ms" style="flex:unset;width:100%;text-align:center"><div class="msv neutral" style="font-size:13px">No trades this month</div></div>';

    // ── Calendar grid (Mon-Fri only) ──
    let html = '';
    ['Mon','Tue','Wed','Thu','Fri'].forEach(d => {{ html += '<div class="cal-header">' + d + '</div>'; }});

    const daysInMonth = new Date(currentYear, currentMonth + 1, 0).getDate();

    // Find first weekday — figure out which column day 1 falls on (Mon=0..Fri=4)
    const firstDow = new Date(currentYear, currentMonth, 1).getDay(); // 0=Sun..6=Sat
    // Map: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4. Sun/Sat get skipped.
    const dowToCol = [null, 0, 1, 2, 3, 4, null]; // Sun=null, Mon=0, ..., Sat=null

    // Find the first weekday of the month
    let firstWeekdayDate = 1;
    while (firstWeekdayDate <= daysInMonth) {{
        const dw = new Date(currentYear, currentMonth, firstWeekdayDate).getDay();
        if (dw >= 1 && dw <= 5) break;
        firstWeekdayDate++;
    }}
    if (firstWeekdayDate > daysInMonth) {{ grid.innerHTML = html; return; }}

    const firstCol = dowToCol[new Date(currentYear, currentMonth, firstWeekdayDate).getDay()];
    for (let i = 0; i < firstCol; i++) html += '<div class="cal-day empty"></div>';

    for (let d = 1; d <= daysInMonth; d++) {{
        const dow = new Date(currentYear, currentMonth, d).getDay();
        if (dow === 0 || dow === 6) continue; // skip weekends

        const dateStr = currentYear + '-' + String(currentMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
        const trade = tradeMap[dateStr];
        if (trade) {{
            const cls = trade.pnl_dollars >= 0 ? 'win' : 'loss';
            const pCls = trade.pnl_dollars >= 0 ? 'green' : 'red';
            const pStr = (trade.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(trade.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
            const nSigs = Math.min(trade.n_signals - 1, 8);
            let dots = ''; for (let s = 0; s < nSigs; s++) dots += '<div class="signal-dot"></div>';
            html += '<div class="cal-day ' + cls + '" onclick="showTrade(\\'' + dateStr + '\\')" id="day-' + dateStr + '">';
            html += '<div class="day-num">' + d + '</div><div class="signal-dots">' + dots + '</div>';
            html += '<div class="day-pnl ' + pCls + '">' + pStr + '</div>';
            html += '<div class="day-risk">$' + (trade.risk/1000).toFixed(0) + 'k</div></div>';
        }} else {{
            html += '<div class="cal-day no-trade"><div class="day-num" style="color:#333">' + d + '</div></div>';
        }}
    }}
    grid.innerHTML = html;
}}

function showTrade(dateStr) {{
    // Highlight selected day
    document.querySelectorAll('.cal-day.selected').forEach(el => el.classList.remove('selected'));
    const dayEl = document.getElementById('day-' + dateStr);
    if (dayEl) dayEl.classList.add('selected');

    const t = tradeMap[dateStr];
    if (!t) return;

    const panel = document.getElementById('detailPanel');
    panel.classList.add('visible');

    const pnlCls = t.pnl_dollars >= 0 ? 'green' : 'red';
    const pnlStr = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
    const ptsPnl = (t.pnl_pts >= 0 ? '+' : '') + t.pnl_pts.toFixed(1) + ' pts';
    const holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';
    const pnlColor = t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466';

    let signalTags = '';
    t.signals.forEach(s => {{
        const isNeg = s.toLowerCase().includes('negative') || s.toLowerCase().includes('far above');
        signalTags += '<span class="signal-tag' + (isNeg ? ' negative' : '') + '">' + s + '</span>';
    }});

    // Compact horizontal trade info
    let html = '<div class="trade-info-bar">';
    html += '<div class="trade-info-left">';
    html += '<div style="font-size:15px;font-weight:600;color:#fff">' + t.day_of_week + ', ' + dateStr + '</div>';
    html += '<div class="pnl-big ' + pnlCls + '" style="font-size:24px;margin:2px 0">' + pnlStr + '</div>';
    html += '<div style="color:#888;font-size:11px">' + ptsPnl + ' &mdash; ' + t.exit_reason + '</div>';
    html += '</div>';
    html += '<div class="trade-info-details">';
    html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">' + t.entry_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + ' @ ' + t.entry_time + '</span></span>';
    html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">' + t.exit_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + ' @ ' + t.exit_time + '</span></span>';
    html += '<span class="di"><span class="dlabel">Hold</span><span class="dvalue">' + holdStr + '</span></span>';
    html += '<span class="di"><span class="dlabel">Risk</span><span class="dvalue">$' + t.risk.toLocaleString() + '</span></span>';
    html += '<span class="di"><span class="dlabel">$/Pt</span><span class="dvalue">$' + t.dollars_per_point.toLocaleString() + '</span></span>';
    html += '<span class="di"><span class="dlabel">PT/SL/TS</span><span class="dvalue">' + t.pt + '/' + t.sl + '/' + t.ts + 'm</span></span>';
    html += '<span class="di"><span class="dlabel">Score</span><span class="dvalue">' + t.score + '</span></span>';
    if (t.vix) html += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
    html += '<div class="trade-info-signals">' + signalTags + '</div>';
    html += '</div></div>';

    // Chart section — full width
    html += '<div id="chartWrap">';
    html += '<div class="ch-header"><span class="ch-title">SPX 1m &mdash; ' + dateStr + '</span>';
    html += '<span class="ch-sub">';
    html += '<span style="color:#2962ff">&#9646; Entry ' + t.entry_price.toFixed(2) + '</span> &middot; ';
    html += '<span style="color:#00d4aa">&#9646; PT +' + t.pt + '</span> &middot; ';
    html += '<span style="color:#ff4466">&#9646; SL -' + t.sl + '</span>';
    html += '</span></div>';
    html += '<div id="chartContainer"><div id="ohlcLegend"></div></div>';
    html += '</div>';

    document.getElementById('detailContent').innerHTML = html;

    // Scroll to chart
    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});

    // Render chart
    renderChart(dateStr, t);
}}

function renderChart(dateStr, t) {{
    const bars = chartBars[dateStr];
    if (!bars || !bars.length) return;

    // Destroy previous chart
    if (tvChart) {{ tvChart.remove(); tvChart = null; tvCandleSeries = null; }}

    const container = document.getElementById('chartContainer');

    tvChart = LightweightCharts.createChart(container, {{
        width: container.clientWidth,
        height: 500,
        layout: {{ background: {{ color: '#131722' }}, textColor: '#DDD' }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        timeScale: {{ borderColor: '#2a2e3d', timeVisible: true, secondsVisible: false }},
        rightPriceScale: {{ borderColor: '#2a2e3d' }},
        grid: {{
            vertLines: {{ color: 'rgba(255,255,255,0.04)' }},
            horzLines: {{ color: 'rgba(255,255,255,0.04)' }},
        }},
    }});

    tvCandleSeries = tvChart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }});

    const candleData = bars.map(b => ({{ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }}));
    tvCandleSeries.setData(candleData);

    // Entry & exit times
    const entryTime = candleData[0].time;
    const entryPrice = t.entry_price;

    let exitTime = candleData[candleData.length - 1].time;
    for (const bar of candleData) {{
        const barDate = new Date(bar.time * 1000);
        const barTimeStr = String(barDate.getUTCHours()).padStart(2,'0') + ':' + String(barDate.getUTCMinutes()).padStart(2,'0');
        if (barTimeStr === t.exit_time) {{
            exitTime = bar.time;
            break;
        }}
    }}

    // ── Price level labels on y-axis only (no horizontal lines) ──
    tvCandleSeries.createPriceLine({{
        price: entryPrice,
        color: '#2962ff',
        lineWidth: 1,
        lineStyle: 2,
        lineVisible: false,
        axisLabelVisible: true,
        title: '',
    }});
    tvCandleSeries.createPriceLine({{
        price: entryPrice + t.pt,
        color: '#00d4aa',
        lineWidth: 1,
        lineStyle: 2,
        lineVisible: false,
        axisLabelVisible: true,
        title: '',
    }});
    tvCandleSeries.createPriceLine({{
        price: entryPrice - t.sl,
        color: '#ff4466',
        lineWidth: 1,
        lineStyle: 2,
        lineVisible: false,
        axisLabelVisible: true,
        title: '',
    }});

    // ── Markers: entry arrow, PT/SL labels, exit arrow ──
    const exitColor = t.pnl_pts >= 0 ? '#00d4aa' : '#ff4466';
    const exitLabel = t.exit_reason + ' ' + (t.pnl_pts >= 0 ? '+' : '') + t.pnl_pts.toFixed(1) + 'pts';

    // Find a bar near the midpoint for PT/SL labels
    const midIdx = Math.min(Math.floor(candleData.length * 0.4), candleData.length - 1);
    const ptLabelTime = candleData[Math.min(midIdx + 2, candleData.length - 1)].time;
    const slLabelTime = candleData[Math.min(midIdx + 4, candleData.length - 1)].time;

    const markers = [
        {{
            time: entryTime,
            position: 'belowBar',
            color: '#2962ff',
            shape: 'arrowUp',
            text: 'BUY @ ' + entryPrice.toFixed(2),
        }},
        {{
            time: exitTime,
            position: 'aboveBar',
            color: exitColor,
            shape: 'arrowDown',
            text: exitLabel,
        }},
    ];

    // Sort markers by time (required by lightweight-charts)
    markers.sort((a, b) => a.time - b.time);
    tvCandleSeries.setMarkers(markers);

    tvChart.timeScale().fitContent();

    // ── OHLC legend on crosshair move ──
    const legend = document.getElementById('ohlcLegend');
    tvChart.subscribeCrosshairMove(param => {{
        if (!param || !param.time || !param.seriesData) {{
            legend.innerHTML = '';
            return;
        }}
        const data = param.seriesData.get(tvCandleSeries);
        if (!data) {{ legend.innerHTML = ''; return; }}
        const o = data.open, h = data.high, l = data.low, c = data.close;
        const chg = c - o;
        const color = chg >= 0 ? '#26a69a' : '#ef5350';
        // Format time from unix timestamp
        const dt = new Date(param.time * 1000);
        const hh = String(dt.getUTCHours()).padStart(2,'0');
        const mm = String(dt.getUTCMinutes()).padStart(2,'0');
        legend.innerHTML = '<span><span class="ol">T</span> <span class="ov">' + hh + ':' + mm + '</span></span>' +
            '<span><span class="ol">O</span> <span class="ov">' + o.toFixed(2) + '</span></span>' +
            '<span><span class="ol">H</span> <span class="ov">' + h.toFixed(2) + '</span></span>' +
            '<span><span class="ol">L</span> <span class="ov">' + l.toFixed(2) + '</span></span>' +
            '<span><span class="ol">C</span> <span class="ov" style="color:' + color + '">' + c.toFixed(2) + '</span></span>' +
            '<span style="color:' + color + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '</span>';
    }});

    // Resize handler
    const resizeObs = new ResizeObserver(() => {{
        if (tvChart) tvChart.applyOptions({{ width: container.clientWidth }});
    }});
    resizeObs.observe(container);
}}

const CURVE_HEIGHTS = {{ equityCurveAll: 150, equityCurveMonth: 120 }};

function drawEquityCurve(canvasId, points, color, fillAlpha) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas || points.length < 2) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();
    const W = rect.width - 24;
    const H = CURVE_HEIGHTS[canvasId] || 120;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const vals = points.map(p => p.v);
    const minV = Math.min(0, ...vals);
    const maxV = Math.max(0, ...vals);
    const range = maxV - minV || 1;
    const padTop = 25, padBot = 20, padLeft = 60, padRight = 16;
    const cW = W - padLeft - padRight;
    const cH = H - padTop - padBot;

    function x(i) {{ return padLeft + (i / (points.length - 1)) * cW; }}
    function y(v) {{ return padTop + (1 - (v - minV) / range) * cH; }}

    // Zero line
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padLeft, y(0));
    ctx.lineTo(W - padRight, y(0));
    ctx.stroke();
    ctx.setLineDash([]);

    // Fill under curve
    ctx.beginPath();
    ctx.moveTo(x(0), y(0));
    for (let i = 0; i < points.length; i++) ctx.lineTo(x(i), y(vals[i]));
    ctx.lineTo(x(points.length - 1), y(0));
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, padTop, 0, padTop + cH);
    grad.addColorStop(0, color.replace(')', ',' + fillAlpha + ')').replace('rgb', 'rgba'));
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    for (let i = 0; i < points.length; i++) {{
        if (i === 0) ctx.moveTo(x(i), y(vals[i]));
        else ctx.lineTo(x(i), y(vals[i]));
    }}
    ctx.stroke();

    // End dot
    const lastIdx = points.length - 1;
    ctx.beginPath();
    ctx.arc(x(lastIdx), y(vals[lastIdx]), 4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // Y-axis labels
    ctx.fillStyle = '#888';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'right';
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {{
        const v = minV + (range * i / ySteps);
        const label = (v >= 0 ? '+$' : '-$') + Math.abs(v/1000).toFixed(0) + 'k';
        ctx.fillText(label, padLeft - 6, y(v) + 3);
    }}

    // End value label
    const endVal = vals[lastIdx];
    ctx.fillStyle = color;
    ctx.font = 'bold 11px -apple-system, sans-serif';
    ctx.textAlign = 'left';
    const endLabel = (endVal >= 0 ? '+$' : '-$') + Math.abs(endVal/1000).toFixed(0) + 'k';
    ctx.fillText(endLabel, x(lastIdx) + 8, y(endVal) + 4);

    // X-axis: first and last date labels
    ctx.fillStyle = '#666';
    ctx.font = '9px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(points[0].d, x(0), H - 4);
    ctx.fillText(points[lastIdx].d, x(lastIdx), H - 4);
    if (points.length > 10) {{
        const midIdx = Math.floor(points.length / 2);
        ctx.fillText(points[midIdx].d, x(midIdx), H - 4);
    }}
}}

// Build overall equity curve data
const equityAll = [];
let cumPnl = 0;
trades.forEach(t => {{
    cumPnl += t.pnl_dollars;
    equityAll.push({{ d: t.date.slice(5), v: cumPnl }});
}});

function drawOverallEquity() {{
    drawEquityCurve('equityCurveAll', equityAll,
        cumPnl >= 0 ? 'rgb(0,212,170)' : 'rgb(255,68,102)', 0.15);
}}

function drawMonthEquity() {{
    const monthKey = currentYear + '-' + String(currentMonth+1).padStart(2,'0');
    const monthTrades = trades.filter(t => t.date.startsWith(monthKey));
    const wrap = document.getElementById('monthCurveWrap');
    if (monthTrades.length < 2) {{ wrap.style.display = 'none'; return; }}
    wrap.style.display = 'block';
    const pts = [];
    let mCum = 0;
    monthTrades.forEach(t => {{
        mCum += t.pnl_dollars;
        pts.push({{ d: t.date.slice(8), v: mCum }});
    }});
    const color = mCum >= 0 ? 'rgb(0,212,170)' : 'rgb(255,68,102)';
    drawEquityCurve('equityCurveMonth', pts, color, 0.2);
}}

function prevMonth() {{ currentMonth--; if (currentMonth < 0) {{ currentMonth = 11; currentYear--; }} renderCalendar(); drawMonthEquity(); }}
function nextMonth() {{ currentMonth++; if (currentMonth > 11) {{ currentMonth = 0; currentYear++; }} renderCalendar(); drawMonthEquity(); }}

drawOverallEquity();
renderCalendar();
drawMonthEquity();
window.addEventListener('resize', () => {{ drawOverallEquity(); drawMonthEquity(); }});
</script>
</body>
</html>'''

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

    print("Evaluating trades...")
    trades = []
    for d in intra_dates:
        bars = spx_intraday[d]
        result = evaluate_day(
            d, bars, intra_dates, intra_idx, spx_intraday,
            vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates
        )
        if result:
            trades.append(result)

    print(f"Total trades: {len(trades)}")
    winners = sum(1 for t in trades if t["pnl_dollars"] > 0)
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    print(f"Winners: {winners} ({winners/len(trades)*100:.1f}%)")
    print(f"Total P&L: ${total_pnl:,.0f}")

    print("Building chart data for trade days...")
    trade_dates = [t["date"] for t in trades]
    chart_data = build_chart_data(spx_intraday, trade_dates)
    print(f"Chart data for {len(chart_data)} days")

    print("Generating HTML...")
    html = generate_html(trades, chart_data)
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    file_size = os.path.getsize(OUTPUT_HTML) / (1024 * 1024)
    print(f"\nSaved: {OUTPUT_HTML} ({file_size:.1f} MB)")


if __name__ == "__main__":
    main()
