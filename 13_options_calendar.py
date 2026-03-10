"""
SPX Opening Print Strategy — Phase 13: Options-Based Trade Calendar
====================================================================
Reads options_trades.json from Phase 12 and generates trade_calendar.html
showing options P&L (premium-based) instead of linear P&L.

Shows: option entry/exit prices, # contracts, premium, strike price.
Max loss per trade = premium paid. No bail logic needed.

Usage:
    python3 13_options_calendar.py

Output:
    trade_calendar.html  (overwrites previous version)
"""

import os
import csv
import json
from collections import defaultdict
from statistics import mean
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
OPTIONS_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "trade_calendar.html")


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


def build_chart_data(spx_intraday, trade_dates):
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


def build_option_chart_data(options_trades):
    """Load cached option 1-min bars and build chart data.
    Returns dict: date → list of [time_unix, o, h, l, c]"""
    import calendar as cal_mod
    opt_chart_data = {}
    for t in options_trades:
        d = t["date"]
        ticker = t["option_ticker"]
        # Build cache key matching what 12_options_backtest.py used
        cache_key = f"bars_{d}_{ticker.replace(':', '_').replace('/', '_')}"
        cache_file = os.path.join(CACHE_DIR, cache_key + ".json")
        if not os.path.exists(cache_file):
            continue
        with open(cache_file, "r") as f:
            bars = json.load(f)
        if not bars or bars == "none":
            continue
        dt_base = datetime.strptime(d, "%Y-%m-%d")
        day_bars = []
        for bar in bars:
            h, m = bar["time"].split(":")
            bar_dt = dt_base.replace(hour=int(h), minute=int(m), second=0)
            ts = int(cal_mod.timegm(bar_dt.timetuple()))
            day_bars.append([
                ts,
                round(bar["open"], 2),
                round(bar["high"], 2),
                round(bar["low"], 2),
                round(bar["close"], 2),
            ])
        if day_bars:
            opt_chart_data[d] = day_bars
    return opt_chart_data


# ── Transform options trades → calendar format ───────────────────────

def transform_trades(options_trades):
    """Convert options_trades.json records to the format the calendar HTML expects."""
    trades = []
    for t in options_trades:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "entry_price": round(t["entry_open"], 2),
            "exit_price": round(t.get("opt_exit_price", 0), 2),
            "entry_time": "09:30:10",
            "exit_time": t.get("opt_exit_time", ""),
            "signals": t["signals"],
            "n_signals": len(t["signals"]),
            "score": t["score"],
            "risk": t["risk"],
            "pt": t["pt"], "sl": t["sl"], "ts": t["ts"],
            "pnl_pts": t.get("linear_pnl_pts", 0),
            "pnl_dollars": round(t["opt_pnl"], 2),
            "exit_reason": t.get("opt_exit_reason", ""),
            "hold_mins": t.get("opt_hold_mins", 0),
            "dollars_per_point": 0,  # not applicable for options
            "vix": t.get("vix"),
            # Options-specific fields
            "opt_entry_price": t.get("opt_entry_price", 0),
            "opt_exit_price": t.get("opt_exit_price", 0),
            "opt_contracts": t.get("opt_contracts", 0),
            "opt_premium": round(t.get("opt_premium", 0), 0),
            "strike": t.get("strike", 0),
            "linear_pnl": round(t.get("linear_pnl", 0), 2),
            "first_bar_bullish": t.get("first_bar_bullish", True),
        })
    return trades


# ── HTML Generation ───────────────────────────────────────────────────

def generate_html(trades, chart_data, opt_chart_data):
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    total_trades = len(trades)
    winners = [t for t in trades if t["pnl_dollars"] > 0]
    losers = [t for t in trades if t["pnl_dollars"] <= 0]
    win_rate = len(winners) / total_trades * 100 if total_trades else 0
    avg_win = mean([t["pnl_dollars"] for t in winners]) if winners else 0
    avg_loss = mean([t["pnl_dollars"] for t in losers]) if losers else 0
    max_win = max(t["pnl_dollars"] for t in trades) if trades else 0
    max_loss = min(t["pnl_dollars"] for t in trades) if trades else 0
    avg_premium = mean([t["opt_premium"] for t in trades]) if trades else 0
    avg_contracts = mean([t["opt_contracts"] for t in trades]) if trades else 0

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cum += t["pnl_dollars"]
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd

    monthly_pnl = defaultdict(float)
    monthly_trades = defaultdict(int)
    for t in trades:
        m = t["date"][:7]
        monthly_pnl[m] += t["pnl_dollars"]
        monthly_trades[m] += 1

    trades_json = json.dumps(trades, default=str)
    chart_json_str = json.dumps(chart_data)
    opt_chart_json_str = json.dumps(opt_chart_data)

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
<title>SPX Opening Print — 0DTE Options Calendar</title>
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
    background: #1a1a28; border-radius: 6px; padding: 10px 16px; min-width: 110px; flex: 1;
    border: 1px solid #2a2a3a;
}}
.stat-card .label {{ font-size: 10px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.stat-card .value {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}
.stat-card .value.green {{ color: #00d4aa; }}
.stat-card .value.red {{ color: #ff4466; }}
.stat-card .value.neutral {{ color: #e0e0e0; }}

.main {{ padding: 16px 32px; }}
.detail-panel {{
    background: #13131f; border-radius: 10px;
    border: 1px solid #2a2a3a; padding: 20px; margin-top: 16px;
    display: none;
}}
.detail-panel.visible {{ display: block; }}

.trade-info-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; margin-bottom: 14px;
}}
.trade-info-left {{ flex: 0 0 auto; margin-right: 16px; }}
.trade-info-details {{
    display: flex; flex-wrap: wrap; gap: 6px 16px; flex: 1; align-items: center;
}}
.trade-info-details .di {{ font-size: 12px; white-space: nowrap; }}
.trade-info-details .di .dlabel {{ color: #6666aa; margin-right: 4px; }}
.trade-info-details .di .dvalue {{ font-weight: 600; }}
.trade-info-signals {{ width: 100%; margin-top: 4px; }}

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

.month-stats {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
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

.signal-tag {{
    display: inline-block; padding: 3px 8px; margin: 2px 3px 2px 0;
    background: #1a1a3a; border-radius: 10px; font-size: 10px; border: 1px solid #2a2a5a; color: #aabbff;
}}
.signal-tag.negative {{ background: #2a1a1a; border-color: #5a2a2a; color: #ffaa88; }}
.pnl-big {{ font-size: 28px; font-weight: 800; margin: 8px 0; }}
.pnl-big.green {{ color: #00d4aa; }}
.pnl-big.red {{ color: #ff4466; }}

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
#chartContainer {{ width: 100%; height: 400px; position: relative; }}
#ohlcLegend {{
    position: absolute; top: 8px; left: 12px; z-index: 10;
    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
    font-size: 11px; color: #aaa; pointer-events: none;
    display: flex; gap: 10px;
}}
#ohlcLegend span {{ white-space: nowrap; }}
#ohlcLegend .ol {{ color: #666; }}
#ohlcLegend .ov {{ font-weight: 600; }}

.chart-wrap {{
    margin-top: 10px; border-radius: 8px; overflow: hidden;
    border: 1px solid #2a2e3d; background: #131722;
}}
.chart-wrap .ch-header {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; background: #1e2130; border-bottom: 1px solid #2a2e3d;
    font-size: 12px;
}}
.chart-wrap .ch-header .ch-title {{ font-weight: 600; font-size: 13px; }}
.chart-wrap .ch-header .ch-sub {{ color: #888; margin-left: 10px; }}
#optChartContainer {{ width: 100%; height: 350px; position: relative; }}
#optOhlcLegend {{
    position: absolute; top: 8px; left: 12px; z-index: 10;
    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
    font-size: 11px; color: #aaa; pointer-events: none;
    display: flex; gap: 10px;
}}
#optOhlcLegend span {{ white-space: nowrap; }}
#optOhlcLegend .ol {{ color: #666; }}
#optOhlcLegend .ov {{ font-weight: 600; }}

.equity-section {{ padding: 0 32px; margin-top: 12px; }}
.equity-section h3 {{ font-size: 14px; font-weight: 600; color: #aaa; margin-bottom: 8px; }}
.equity-canvas-wrap {{
    background: #131722; border-radius: 8px; border: 1px solid #2a2e3d;
    padding: 12px 12px 8px 12px; position: relative;
}}
.equity-canvas-wrap canvas {{ width: 100%; display: block; }}

.monthly-summary {{ padding: 16px 32px 32px; }}
.monthly-summary h2 {{ font-size: 18px; margin-bottom: 12px; }}
.monthly-table {{ width: 100%; border-collapse: collapse; }}
.monthly-table th, .monthly-table td {{
    padding: 8px 14px; text-align: left; font-size: 12px; border-bottom: 1px solid #1a1a2a;
}}
.monthly-table th {{ color: #6666aa; font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 1px; }}

/* Options comparison badge */
.opt-badge {{
    display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 9px;
    font-weight: 600; margin-left: 8px;
}}
.opt-badge.better {{ background: #0a2a1a; color: #00d4aa; border: 1px solid #1a5a3a; }}
.opt-badge.worse {{ background: #2a0a0f; color: #ff4466; border: 1px solid #5a1a2a; }}
</style>
</head>
<body>

<div class="header">
    <h1>SPX Opening Print — 0DTE Options Strategy</h1>
    <div class="subtitle">Buy ATM 0DTE SPX Call at 9:30:10 &mdash; {trades[0]["date"]} to {trades[-1]["date"]} &mdash; {total_trades} trades &mdash; Max loss = premium</div>
</div>

<div class="stats-bar">
    <div class="stat-card"><div class="label">Total P&L</div><div class="value {pnl_cls}">${total_pnl:,.0f}</div></div>
    <div class="stat-card"><div class="label">Win Rate</div><div class="value neutral">{win_rate:.1f}%</div></div>
    <div class="stat-card"><div class="label">Trades</div><div class="value neutral">{total_trades}</div></div>
    <div class="stat-card"><div class="label">Avg Win</div><div class="value green">${avg_win:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Loss</div><div class="value red">${avg_loss:,.0f}</div></div>
    <div class="stat-card"><div class="label">Best</div><div class="value green">${max_win:,.0f}</div></div>
    <div class="stat-card"><div class="label">Worst</div><div class="value red">${max_loss:,.0f}</div></div>
    <div class="stat-card"><div class="label">Max DD</div><div class="value red">${max_dd:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Premium</div><div class="value neutral">${avg_premium:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Contracts</div><div class="value neutral">{avg_contracts:.0f}</div></div>
</div>

<div class="equity-section">
    <h3>Strategy Equity Curve (Options)</h3>
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
const optChartBars = {opt_chart_json_str};
const tradeMap = {{}};
trades.forEach(t => {{ tradeMap[t.date] = t; }});

let currentYear, currentMonth;
let tvChart = null, tvCandleSeries = null;
let tvOptChart = null, tvOptCandleSeries = null;

const lastDate = new Date(trades[trades.length - 1].date + 'T00:00:00');
currentYear = lastDate.getFullYear();
currentMonth = lastDate.getMonth();

function renderCalendar() {{
    const grid = document.getElementById('calGrid');
    const title = document.getElementById('monthTitle');
    const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    title.textContent = months[currentMonth] + ' ' + currentYear;

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
    const mAvgPremium = mCount > 0 ? (monthTrades.reduce((s, t) => s + t.opt_premium, 0) / mCount) : 0;

    const pnlCls = mPnl >= 0 ? 'green' : 'red';
    let statsHtml = '';
    statsHtml += '<div class="ms"><div class="msl">Month P&L</div><div class="msv ' + pnlCls + '">' + (mPnl >= 0 ? '+$' : '-$') + Math.abs(mPnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Trades</div><div class="msv neutral">' + mCount + '</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Win Rate</div><div class="msv neutral">' + mWR + '%</div></div>';
    statsHtml += '<div class="ms"><div class="msl">W / L</div><div class="msv neutral">' + mWins + ' / ' + (mCount - mWins) + '</div></div>';
    if (mWins > 0) statsHtml += '<div class="ms"><div class="msl">Avg Win</div><div class="msv green">+$' + Math.abs(mAvgWin).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    if (mLosers.length > 0) statsHtml += '<div class="ms"><div class="msl">Avg Loss</div><div class="msv red">-$' + Math.abs(mAvgLoss).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    if (mCount > 0) {{
        statsHtml += '<div class="ms"><div class="msl">Best</div><div class="msv green">+$' + Math.abs(mBest).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
        statsHtml += '<div class="ms"><div class="msl">Worst</div><div class="msv red">' + (mWorst >= 0 ? '+$' : '-$') + Math.abs(mWorst).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
        statsHtml += '<div class="ms"><div class="msl">Avg Premium</div><div class="msv neutral">$' + Math.abs(mAvgPremium).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    }}
    document.getElementById('monthStats').innerHTML = mCount > 0 ? statsHtml : '<div class="ms" style="flex:unset;width:100%;text-align:center"><div class="msv neutral" style="font-size:13px">No trades this month</div></div>';

    let html = '';
    ['Mon','Tue','Wed','Thu','Fri'].forEach(d => {{ html += '<div class="cal-header">' + d + '</div>'; }});

    const daysInMonth = new Date(currentYear, currentMonth + 1, 0).getDate();
    const dowToCol = [null, 0, 1, 2, 3, 4, null];

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
        if (dow === 0 || dow === 6) continue;

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
            html += '<div class="day-risk">' + trade.opt_contracts + ' contracts</div></div>';
        }} else {{
            html += '<div class="cal-day no-trade"><div class="day-num" style="color:#333">' + d + '</div></div>';
        }}
    }}
    grid.innerHTML = html;
}}

function showTrade(dateStr) {{
    document.querySelectorAll('.cal-day.selected').forEach(el => el.classList.remove('selected'));
    const dayEl = document.getElementById('day-' + dateStr);
    if (dayEl) dayEl.classList.add('selected');

    const t = tradeMap[dateStr];
    if (!t) return;

    const panel = document.getElementById('detailPanel');
    panel.classList.add('visible');

    const pnlCls = t.pnl_dollars >= 0 ? 'green' : 'red';
    const pnlStr = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
    const holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';

    // Compare to linear
    const linDiff = t.pnl_dollars - t.linear_pnl;
    const linBadge = linDiff >= 0
        ? '<span class="opt-badge better">Options +$' + Math.abs(linDiff).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' vs linear</span>'
        : '<span class="opt-badge worse">Options -$' + Math.abs(linDiff).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' vs linear</span>';

    const bullishBadge = t.first_bar_bullish
        ? '<span style="color:#26a69a;font-size:10px;font-weight:600">&bull; Bullish 1st bar</span>'
        : '<span style="color:#ef5350;font-size:10px;font-weight:600">&bull; Bearish 1st bar (held via options)</span>';

    let signalTags = '';
    t.signals.forEach(s => {{
        const isNeg = s.toLowerCase().includes('negative') || s.toLowerCase().includes('far above');
        signalTags += '<span class="signal-tag' + (isNeg ? ' negative' : '') + '">' + s + '</span>';
    }});

    let html = '<div class="trade-info-bar">';
    html += '<div class="trade-info-left">';
    html += '<div style="font-size:15px;font-weight:600;color:#fff">' + t.day_of_week + ', ' + dateStr + ' ' + bullishBadge + '</div>';
    html += '<div class="pnl-big ' + pnlCls + '" style="font-size:24px;margin:2px 0">' + pnlStr + linBadge + '</div>';
    html += '<div style="color:#888;font-size:11px">' + t.exit_reason + ' &mdash; Linear: ' + (t.linear_pnl >= 0 ? '+$' : '-$') + Math.abs(t.linear_pnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div>';
    html += '</div>';
    html += '<div class="trade-info-details">';
    html += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike.toLocaleString() + '</span></span>';
    html += '<span class="di"><span class="dlabel">Call Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
    html += '<span class="di"><span class="dlabel">Call Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
    html += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
    html += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
    html += '<span class="di"><span class="dlabel">SPX Open</span><span class="dvalue">' + t.entry_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + '</span></span>';
    html += '<span class="di"><span class="dlabel">Exit Time</span><span class="dvalue">' + t.exit_time + '</span></span>';
    html += '<span class="di"><span class="dlabel">Hold</span><span class="dvalue">' + holdStr + '</span></span>';
    html += '<span class="di"><span class="dlabel">PT/SL/TS</span><span class="dvalue">' + t.pt + '/' + t.sl + '/' + t.ts + 'm</span></span>';
    html += '<span class="di"><span class="dlabel">Score</span><span class="dvalue">' + t.score + '</span></span>';
    if (t.vix) html += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
    html += '<div class="trade-info-signals">' + signalTags + '</div>';
    html += '</div></div>';

    html += '<div id="chartWrap">';
    html += '<div class="ch-header"><span class="ch-title">SPX 1m &mdash; ' + dateStr + '</span>';
    html += '<span class="ch-sub">';
    html += '<span style="color:#2962ff">&#9646; SPX Open ' + t.entry_price.toFixed(2) + '</span> &middot; ';
    html += '<span style="color:#00d4aa">&#9646; PT +' + t.pt + '</span> &middot; ';
    html += '<span style="color:#ff4466">&#9646; SL -' + t.sl + '</span>';
    html += '</span></div>';
    html += '<div id="chartContainer"><div id="ohlcLegend"></div></div>';
    html += '</div>';

    // Option price chart
    const hasOptBars = optChartBars[dateStr] && optChartBars[dateStr].length > 0;
    if (hasOptBars) {{
        html += '<div class="chart-wrap">';
        html += '<div class="ch-header"><span class="ch-title">0DTE Call $' + t.strike + 'C &mdash; ' + dateStr + '</span>';
        html += '<span class="ch-sub">';
        html += '<span style="color:#2962ff">&#9646; Entry $' + t.opt_entry_price.toFixed(2) + '</span> &middot; ';
        html += '<span style="color:' + (t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466') + '">&#9646; Exit $' + t.opt_exit_price.toFixed(2) + '</span>';
        html += '</span></div>';
        html += '<div id="optChartContainer"><div id="optOhlcLegend"></div></div>';
        html += '</div>';
    }}

    document.getElementById('detailContent').innerHTML = html;
    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
    renderChart(dateStr, t);
    if (hasOptBars) renderOptionChart(dateStr, t);
}}

function renderChart(dateStr, t) {{
    const bars = chartBars[dateStr];
    if (!bars || !bars.length) return;
    if (tvChart) {{ tvChart.remove(); tvChart = null; tvCandleSeries = null; }}
    if (tvOptChart) {{ tvOptChart.remove(); tvOptChart = null; tvOptCandleSeries = null; }}
    const container = document.getElementById('chartContainer');

    tvChart = LightweightCharts.createChart(container, {{
        width: container.clientWidth, height: 400,
        layout: {{ background: {{ color: '#131722' }}, textColor: '#DDD' }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        timeScale: {{ borderColor: '#2a2e3d', timeVisible: true, secondsVisible: false }},
        rightPriceScale: {{ borderColor: '#2a2e3d' }},
        grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.04)' }}, horzLines: {{ color: 'rgba(255,255,255,0.04)' }} }},
    }});

    tvCandleSeries = tvChart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }});

    const candleData = bars.map(b => ({{ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }}));
    tvCandleSeries.setData(candleData);

    const entryTime = candleData[0].time;
    const entryPrice = t.entry_price;

    let exitTime = candleData[candleData.length - 1].time;
    for (const bar of candleData) {{
        const barDate = new Date(bar.time * 1000);
        const barTimeStr = String(barDate.getUTCHours()).padStart(2,'0') + ':' + String(barDate.getUTCMinutes()).padStart(2,'0');
        if (barTimeStr === t.exit_time) {{ exitTime = bar.time; break; }}
    }}

    tvCandleSeries.createPriceLine({{ price: entryPrice, color: '#2962ff', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
    tvCandleSeries.createPriceLine({{ price: entryPrice + t.pt, color: '#00d4aa', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
    tvCandleSeries.createPriceLine({{ price: entryPrice - t.sl, color: '#ff4466', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});

    const exitColor = t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466';
    const exitLabel = t.exit_reason;

    const markers = [
        {{ time: entryTime, position: 'belowBar', color: '#2962ff', shape: 'arrowUp', text: 'BUY CALL $' + t.opt_entry_price.toFixed(2) + ' x' + t.opt_contracts }},
        {{ time: exitTime, position: 'aboveBar', color: exitColor, shape: 'arrowDown', text: exitLabel + ' $' + t.opt_exit_price.toFixed(2) }},
    ];
    markers.sort((a, b) => a.time - b.time);
    tvCandleSeries.setMarkers(markers);
    tvChart.timeScale().fitContent();

    const legend = document.getElementById('ohlcLegend');
    tvChart.subscribeCrosshairMove(param => {{
        if (!param || !param.time || !param.seriesData) {{ legend.innerHTML = ''; return; }}
        const data = param.seriesData.get(tvCandleSeries);
        if (!data) {{ legend.innerHTML = ''; return; }}
        const o = data.open, h = data.high, l = data.low, c = data.close;
        const chg = c - o;
        const color = chg >= 0 ? '#26a69a' : '#ef5350';
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

    const resizeObs = new ResizeObserver(() => {{ if (tvChart) tvChart.applyOptions({{ width: container.clientWidth }}); }});
    resizeObs.observe(container);
}}

function renderOptionChart(dateStr, t) {{
    const bars = optChartBars[dateStr];
    if (!bars || !bars.length) return;
    if (tvOptChart) {{ tvOptChart.remove(); tvOptChart = null; tvOptCandleSeries = null; }}
    const container = document.getElementById('optChartContainer');
    if (!container) return;

    tvOptChart = LightweightCharts.createChart(container, {{
        width: container.clientWidth, height: 350,
        layout: {{ background: {{ color: '#131722' }}, textColor: '#DDD' }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        timeScale: {{ borderColor: '#2a2e3d', timeVisible: true, secondsVisible: false }},
        rightPriceScale: {{ borderColor: '#2a2e3d' }},
        grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.04)' }}, horzLines: {{ color: 'rgba(255,255,255,0.04)' }} }},
    }});

    tvOptCandleSeries = tvOptChart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }});

    const candleData = bars.map(b => ({{ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }}));
    tvOptCandleSeries.setData(candleData);

    const exitColor = t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466';

    // Entry price line
    tvOptCandleSeries.createPriceLine({{ price: t.opt_entry_price, color: '#2962ff', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
    // Exit price line
    tvOptCandleSeries.createPriceLine({{ price: t.opt_exit_price, color: exitColor, lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});

    // Find entry and exit times
    let entryTime = candleData[0].time;
    let exitTime = candleData[candleData.length - 1].time;
    // Entry is ~9:30:10, approximate to 09:31 bar
    for (const bar of candleData) {{
        const barDate = new Date(bar.time * 1000);
        const barTimeStr = String(barDate.getUTCHours()).padStart(2,'0') + ':' + String(barDate.getUTCMinutes()).padStart(2,'0');
        if (barTimeStr === '09:31') {{ entryTime = bar.time; break; }}
    }}
    for (const bar of candleData) {{
        const barDate = new Date(bar.time * 1000);
        const barTimeStr = String(barDate.getUTCHours()).padStart(2,'0') + ':' + String(barDate.getUTCMinutes()).padStart(2,'0');
        if (barTimeStr === t.exit_time) {{ exitTime = bar.time; break; }}
    }}

    const markers = [
        {{ time: entryTime, position: 'belowBar', color: '#2962ff', shape: 'arrowUp', text: 'BUY $' + t.opt_entry_price.toFixed(2) + ' x' + t.opt_contracts }},
        {{ time: exitTime, position: 'aboveBar', color: exitColor, shape: 'arrowDown', text: 'SELL $' + t.opt_exit_price.toFixed(2) }},
    ];
    markers.sort((a, b) => a.time - b.time);
    tvOptCandleSeries.setMarkers(markers);
    tvOptChart.timeScale().fitContent();

    // OHLC legend for option chart
    const legend = document.getElementById('optOhlcLegend');
    tvOptChart.subscribeCrosshairMove(param => {{
        if (!param || !param.time || !param.seriesData) {{ legend.innerHTML = ''; return; }}
        const data = param.seriesData.get(tvOptCandleSeries);
        if (!data) {{ legend.innerHTML = ''; return; }}
        const o = data.open, h = data.high, l = data.low, c = data.close;
        const chg = c - o;
        const color = chg >= 0 ? '#26a69a' : '#ef5350';
        const dt = new Date(param.time * 1000);
        const hh = String(dt.getUTCHours()).padStart(2,'0');
        const mm = String(dt.getUTCMinutes()).padStart(2,'0');
        legend.innerHTML = '<span><span class="ol">T</span> <span class="ov">' + hh + ':' + mm + '</span></span>' +
            '<span><span class="ol">O</span> <span class="ov">$' + o.toFixed(2) + '</span></span>' +
            '<span><span class="ol">H</span> <span class="ov">$' + h.toFixed(2) + '</span></span>' +
            '<span><span class="ol">L</span> <span class="ov">$' + l.toFixed(2) + '</span></span>' +
            '<span><span class="ol">C</span> <span class="ov" style="color:' + color + '">$' + c.toFixed(2) + '</span></span>' +
            '<span style="color:' + color + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '</span>';
    }});

    const resizeObs = new ResizeObserver(() => {{ if (tvOptChart) tvOptChart.applyOptions({{ width: container.clientWidth }}); }});
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
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
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

    ctx.strokeStyle = 'rgba(255,255,255,0.1)'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(padLeft, y(0)); ctx.lineTo(W - padRight, y(0)); ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath(); ctx.moveTo(x(0), y(0));
    for (let i = 0; i < points.length; i++) ctx.lineTo(x(i), y(vals[i]));
    ctx.lineTo(x(points.length - 1), y(0)); ctx.closePath();
    const grad = ctx.createLinearGradient(0, padTop, 0, padTop + cH);
    grad.addColorStop(0, color.replace(')', ',' + fillAlpha + ')').replace('rgb', 'rgba'));
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad; ctx.fill();

    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = 'round';
    for (let i = 0; i < points.length; i++) {{ if (i === 0) ctx.moveTo(x(i), y(vals[i])); else ctx.lineTo(x(i), y(vals[i])); }}
    ctx.stroke();

    const lastIdx = points.length - 1;
    ctx.beginPath(); ctx.arc(x(lastIdx), y(vals[lastIdx]), 4, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();

    ctx.fillStyle = '#888'; ctx.font = '10px -apple-system, sans-serif'; ctx.textAlign = 'right';
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {{
        const v = minV + (range * i / ySteps);
        const label = (v >= 0 ? '+$' : '-$') + Math.abs(v/1000).toFixed(0) + 'k';
        ctx.fillText(label, padLeft - 6, y(v) + 3);
    }}
    const endVal = vals[lastIdx];
    ctx.fillStyle = color; ctx.font = 'bold 11px -apple-system, sans-serif'; ctx.textAlign = 'left';
    const endLabel = (endVal >= 0 ? '+$' : '-$') + Math.abs(endVal/1000).toFixed(0) + 'k';
    ctx.fillText(endLabel, x(lastIdx) + 8, y(endVal) + 4);

    ctx.fillStyle = '#666'; ctx.font = '9px -apple-system, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(points[0].d, x(0), H - 4);
    ctx.fillText(points[lastIdx].d, x(lastIdx), H - 4);
    if (points.length > 10) {{ const midIdx = Math.floor(points.length / 2); ctx.fillText(points[midIdx].d, x(midIdx), H - 4); }}
}}

const equityAll = [];
let cumPnl = 0;
trades.forEach(t => {{ cumPnl += t.pnl_dollars; equityAll.push({{ d: t.date.slice(5), v: cumPnl }}); }});

function drawOverallEquity() {{
    drawEquityCurve('equityCurveAll', equityAll, cumPnl >= 0 ? 'rgb(0,212,170)' : 'rgb(255,68,102)', 0.15);
}}

function drawMonthEquity() {{
    const monthKey = currentYear + '-' + String(currentMonth+1).padStart(2,'0');
    const monthTrades = trades.filter(t => t.date.startsWith(monthKey));
    const wrap = document.getElementById('monthCurveWrap');
    if (monthTrades.length < 2) {{ wrap.style.display = 'none'; return; }}
    wrap.style.display = 'block';
    const pts = []; let mCum = 0;
    monthTrades.forEach(t => {{ mCum += t.pnl_dollars; pts.push({{ d: t.date.slice(8), v: mCum }}); }});
    drawEquityCurve('equityCurveMonth', pts, mCum >= 0 ? 'rgb(0,212,170)' : 'rgb(255,68,102)', 0.2);
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


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print("Loading options trades...")
    with open(OPTIONS_JSON, "r") as f:
        options_trades = json.load(f)
    print(f"  {len(options_trades)} trades loaded")

    print("Transforming to calendar format...")
    trades = transform_trades(options_trades)

    print("Loading SPX intraday for charts...")
    spx_intraday = load_spx_intraday()

    print("Building chart data...")
    trade_dates = [t["date"] for t in trades]
    chart_data = build_chart_data(spx_intraday, trade_dates)
    print(f"  SPX chart data for {len(chart_data)} days")

    print("Loading option chart data from cache...")
    opt_chart_data = build_option_chart_data(options_trades)
    print(f"  Option chart data for {len(opt_chart_data)} days")

    print("Generating HTML...")
    html = generate_html(trades, chart_data, opt_chart_data)
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    file_size = os.path.getsize(OUTPUT_HTML) / (1024 * 1024)
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    winners = sum(1 for t in trades if t["pnl_dollars"] > 0)
    print(f"\nOptions Calendar Generated!")
    print(f"  Trades: {len(trades)}  |  WR: {winners/len(trades)*100:.1f}%  |  P&L: ${total_pnl:,.0f}")
    print(f"  Saved: {OUTPUT_HTML} ({file_size:.1f} MB)")


if __name__ == "__main__":
    main()
