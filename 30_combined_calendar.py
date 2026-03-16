"""
Opening Print Strategy — Combined SPX + QQQ Options Calendar
=============================================================
Reads both options_trades.json (SPX) and qqq_optimized_trades.json (QQQ)
and generates a single combined trade_calendar.html.

Features:
- Combined equity curve (SPX + QQQ) plus individual curves
- Calendar days color-coded by strategy (blue=SPX, purple=QQQ, split=both)
- Separate detail panels for SPX vs QQQ trades
- Charts for both SPX and QQQ intraday + option prices

Usage:
    python3 30_combined_calendar.py

Output:
    trade_calendar.html
"""

import os
import csv
import json
from collections import defaultdict
from statistics import mean
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
QQQ_1MIN = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")
SPX_TRADES_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")
QQQ_TRADES_JSON = os.path.join(SCRIPT_DIR, "qqq_optimized_trades.json")
SPX_CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
QQQ_CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "trade_calendar.html")


# ── Data Loaders ──────────────────────────────────────────────────────

def load_intraday(csv_path, ticker_label):
    days = defaultdict(list)
    with open(csv_path, "r") as f:
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
    print(f"  {ticker_label} intraday: {len(days)} days")
    return dict(days)


def build_chart_data(intraday, trade_dates):
    import calendar
    chart_data = {}
    for d in trade_dates:
        bars = intraday.get(d, [])
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


def build_option_chart_data(trades_list, cache_dir):
    import calendar as cal_mod
    opt_chart_data = {}
    for t in trades_list:
        d = t["date"]
        ticker = t["option_ticker"]
        cache_key = f"bars_{d}_{ticker.replace(':', '_').replace('/', '_')}"
        cache_file = os.path.join(cache_dir, cache_key + ".json")
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


# ── Transform trades ─────────────────────────────────────────────────

def transform_spx_trades(raw):
    trades = []
    for t in raw:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "strategy": "SPX",
            "entry_price": round(t["entry_open"], 2),
            "signals": t["signals"],
            "n_signals": len(t["signals"]),
            "score": t["score"],
            "risk": t["risk"],
            "pt": t["pt"], "sl": t["sl"], "ts": t["ts"],
            "pnl_dollars": round(t["opt_pnl"], 2),
            "exit_reason": t.get("opt_exit_reason", ""),
            "exit_time": t.get("opt_exit_time", ""),
            "hold_mins": t.get("opt_hold_mins", 0),
            "vix": t.get("vix"),
            "opt_entry_price": t.get("opt_entry_price", 0),
            "opt_exit_price": t.get("opt_exit_price", 0),
            "opt_contracts": t.get("opt_contracts", 0),
            "opt_premium": round(t.get("opt_premium", 0), 0),
            "strike": t.get("strike", 0),
            "linear_pnl": round(t.get("linear_pnl", 0), 2),
            "first_bar_bullish": t.get("first_bar_bullish", True),
            "option_ticker": t.get("option_ticker", ""),
            "fb_ret": 0,
        })
    return trades


def transform_qqq_trades(raw):
    trades = []
    for t in raw:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "strategy": "QQQ",
            "entry_price": round(t["entry_open"], 2),
            "signals": [],
            "n_signals": 0,
            "score": 0,
            "risk": t["risk"],
            "pt": 0, "sl": 0, "ts": 0,
            "pnl_dollars": round(t["opt_pnl"], 2),
            "exit_reason": t.get("opt_exit_reason", ""),
            "exit_time": t.get("opt_exit_time", ""),
            "hold_mins": t.get("opt_hold_mins", 0),
            "vix": t.get("vix"),
            "opt_entry_price": t.get("opt_entry_price", 0),
            "opt_exit_price": t.get("opt_exit_price", 0),
            "opt_contracts": t.get("opt_contracts", 0),
            "opt_premium": round(t.get("opt_premium", 0), 0),
            "strike": t.get("strike", 0),
            "linear_pnl": 0,
            "first_bar_bullish": True,
            "option_ticker": t.get("option_ticker", ""),
            "fb_ret": round(t.get("fb_ret", 0) * 100, 3),  # as pct
        })
    return trades


# ── Stats helper ─────────────────────────────────────────────────────

def calc_stats(trades):
    if not trades:
        return {}
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    winners = [t for t in trades if t["pnl_dollars"] > 0]
    losers = [t for t in trades if t["pnl_dollars"] <= 0]
    win_rate = len(winners) / len(trades) * 100
    avg_win = mean([t["pnl_dollars"] for t in winners]) if winners else 0
    avg_loss = mean([t["pnl_dollars"] for t in losers]) if losers else 0
    max_win = max(t["pnl_dollars"] for t in trades)
    max_loss = min(t["pnl_dollars"] for t in trades)
    avg_premium = mean([t["opt_premium"] for t in trades])
    avg_contracts = mean([t["opt_contracts"] for t in trades])

    cum = peak = max_dd = 0
    for t in trades:
        cum += t["pnl_dollars"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "total_pnl": total_pnl,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_win": max_win,
        "max_loss": max_loss,
        "max_dd": max_dd,
        "avg_premium": avg_premium,
        "avg_contracts": avg_contracts,
    }


# ── HTML Generation ──────────────────────────────────────────────────

def generate_html(spx_trades, qqq_trades, all_trades,
                  spx_chart_data, qqq_chart_data,
                  spx_opt_chart_data, qqq_opt_chart_data):

    combined = calc_stats(all_trades)
    spx_s = calc_stats(spx_trades)
    qqq_s = calc_stats(qqq_trades)

    # Monthly summary
    monthly_pnl = defaultdict(lambda: {"spx": 0, "qqq": 0, "total": 0, "spx_n": 0, "qqq_n": 0})
    for t in all_trades:
        m = t["date"][:7]
        monthly_pnl[m]["total"] += t["pnl_dollars"]
        if t["strategy"] == "SPX":
            monthly_pnl[m]["spx"] += t["pnl_dollars"]
            monthly_pnl[m]["spx_n"] += 1
        else:
            monthly_pnl[m]["qqq"] += t["pnl_dollars"]
            monthly_pnl[m]["qqq_n"] += 1

    monthly_rows = ""
    cum = 0
    for m in sorted(monthly_pnl.keys()):
        mp = monthly_pnl[m]
        cum += mp["total"]
        tc = '#00d4aa' if mp["total"] >= 0 else '#ff4466'
        cc = '#00d4aa' if cum >= 0 else '#ff4466'
        sc = '#4a90d9' if mp["spx"] >= 0 else '#ff6688'
        qc = '#bb77ff' if mp["qqq"] >= 0 else '#ff6688'
        monthly_rows += f'<tr><td>{m}</td>'
        monthly_rows += f'<td>{mp["spx_n"]}</td><td style="color:{sc};font-weight:700">${mp["spx"]:,.0f}</td>'
        monthly_rows += f'<td>{mp["qqq_n"]}</td><td style="color:{qc};font-weight:700">${mp["qqq"]:,.0f}</td>'
        monthly_rows += f'<td style="color:{tc};font-weight:700">${mp["total"]:,.0f}</td>'
        monthly_rows += f'<td style="color:{cc};font-weight:700">${cum:,.0f}</td></tr>\n'

    # JSON data
    # Build trade maps: for each date, up to 2 trades (one SPX, one QQQ)
    spx_trade_map = {}
    for t in spx_trades:
        spx_trade_map[t["date"]] = t
    qqq_trade_map = {}
    for t in qqq_trades:
        qqq_trade_map[t["date"]] = t

    all_trades_json = json.dumps(all_trades, default=str)
    spx_trades_json = json.dumps(spx_trades, default=str)
    qqq_trades_json = json.dumps(qqq_trades, default=str)
    spx_chart_json = json.dumps(spx_chart_data)
    qqq_chart_json = json.dumps(qqq_chart_data)
    spx_opt_chart_json = json.dumps(spx_opt_chart_data)
    qqq_opt_chart_json = json.dumps(qqq_opt_chart_data)

    c = combined
    pnl_cls = 'green' if c["total_pnl"] >= 0 else 'red'

    first_date = all_trades[0]["date"]
    last_date = all_trades[-1]["date"]

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Opening Print — Combined SPX + QQQ Options Calendar</title>
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

/* Strategy tabs */
.strat-tabs {{
    display: flex; gap: 0; padding: 0 32px; background: #111118; border-bottom: 1px solid #1a1a2a;
}}
.strat-tab {{
    padding: 10px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
    border-bottom: 2px solid transparent; color: #666; transition: all 0.2s;
}}
.strat-tab:hover {{ color: #aaa; }}
.strat-tab.active {{ color: #fff; }}
.strat-tab.active[data-strat="combined"] {{ border-color: #00d4aa; }}
.strat-tab.active[data-strat="spx"] {{ border-color: #4a90d9; }}
.strat-tab.active[data-strat="qqq"] {{ border-color: #bb77ff; }}
.strat-badge {{
    display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 10px;
    margin-left: 6px; font-weight: 700;
}}
.strat-badge.spx {{ background: #1a2a4a; color: #4a90d9; }}
.strat-badge.qqq {{ background: #2a1a3a; color: #bb77ff; }}

.stats-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 32px;
    background: #111118; border-bottom: 1px solid #1a1a2a;
}}
.stat-card {{
    background: #1a1a28; border-radius: 6px; padding: 10px 16px; min-width: 100px; flex: 1;
    border: 1px solid #2a2a3a;
}}
.stat-card .label {{ font-size: 10px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.stat-card .value {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}
.stat-card .value.green {{ color: #00d4aa; }}
.stat-card .value.red {{ color: #ff4466; }}
.stat-card .value.neutral {{ color: #e0e0e0; }}
.stat-card .value.spx {{ color: #4a90d9; }}
.stat-card .value.qqq {{ color: #bb77ff; }}

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
    min-height: 90px; border: 1px solid transparent; transition: all 0.15s;
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
.cal-day .day-strats {{
    display: flex; gap: 2px; margin-top: 2px;
}}
.cal-day .strat-dot {{
    padding: 1px 5px; border-radius: 6px; font-size: 8px; font-weight: 700;
}}
.cal-day .strat-dot.spx {{ background: #1a2a4a; color: #4a90d9; }}
.cal-day .strat-dot.qqq {{ background: #2a1a3a; color: #bb77ff; }}

.signal-tag {{
    display: inline-block; padding: 3px 8px; margin: 2px 3px 2px 0;
    background: #1a1a3a; border-radius: 10px; font-size: 10px; border: 1px solid #2a2a5a; color: #aabbff;
}}
.signal-tag.negative {{ background: #2a1a1a; border-color: #5a2a2a; color: #ffaa88; }}
.pnl-big {{ font-size: 28px; font-weight: 800; margin: 8px 0; }}
.pnl-big.green {{ color: #00d4aa; }}
.pnl-big.red {{ color: #ff4466; }}

/* Trade sections in detail panel */
.trade-section {{
    padding: 16px; border-radius: 8px; margin-bottom: 12px;
}}
.trade-section.spx {{ border: 1px solid #2a3a5a; background: #0f1525; }}
.trade-section.qqq {{ border: 1px solid #3a2a5a; background: #150f25; }}
.trade-section-header {{
    font-size: 14px; font-weight: 700; margin-bottom: 10px;
    display: flex; align-items: center; gap: 8px;
}}
.trade-section-header .strat-label {{
    padding: 2px 8px; border-radius: 6px; font-size: 11px;
}}
.trade-section-header .strat-label.spx {{ background: #1a2a4a; color: #4a90d9; }}
.trade-section-header .strat-label.qqq {{ background: #2a1a3a; color: #bb77ff; }}

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
.ohlc-legend {{
    position: absolute; top: 8px; left: 12px; z-index: 10;
    font-family: 'SF Mono', 'Menlo', 'Monaco', monospace;
    font-size: 11px; color: #aaa; pointer-events: none;
    display: flex; gap: 10px;
}}
.ohlc-legend span {{ white-space: nowrap; }}
.ohlc-legend .ol {{ color: #666; }}
.ohlc-legend .ov {{ font-weight: 600; }}

.equity-section {{ padding: 0 32px; margin-top: 12px; }}
.equity-section h3 {{ font-size: 14px; font-weight: 600; color: #aaa; margin-bottom: 8px; }}
.equity-canvas-wrap {{
    background: #131722; border-radius: 8px; border: 1px solid #2a2e3d;
    padding: 12px 12px 8px 12px; position: relative;
}}
.equity-canvas-wrap canvas {{ width: 100%; display: block; }}

.equity-legend {{
    display: flex; gap: 16px; margin-top: 6px; justify-content: center;
}}
.equity-legend span {{ font-size: 11px; display: flex; align-items: center; gap: 4px; }}
.equity-legend .dot {{ width: 10px; height: 3px; border-radius: 2px; }}

.monthly-summary {{ padding: 16px 32px 32px; }}
.monthly-summary h2 {{ font-size: 18px; margin-bottom: 12px; }}
.monthly-table {{ width: 100%; border-collapse: collapse; }}
.monthly-table th, .monthly-table td {{
    padding: 8px 14px; text-align: left; font-size: 12px; border-bottom: 1px solid #1a1a2a;
}}
.monthly-table th {{ color: #6666aa; font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 1px; }}

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
    <h1>Opening Print — Combined SPX + QQQ 0DTE Options</h1>
    <div class="subtitle">
        <span class="strat-badge spx">SPX</span> {spx_s["n_trades"]} trades &mdash;
        <span class="strat-badge qqq">QQQ</span> {qqq_s["n_trades"]} trades &mdash;
        {first_date} to {last_date} &mdash; {c["n_trades"]} total
    </div>
</div>

<div class="strat-tabs">
    <div class="strat-tab active" data-strat="combined" onclick="switchStrat('combined')">
        Combined<span style="color:#00d4aa;margin-left:6px;font-size:12px">${c["total_pnl"]:,.0f}</span>
    </div>
    <div class="strat-tab" data-strat="spx" onclick="switchStrat('spx')">
        <span class="strat-badge spx">SPX</span>${spx_s["total_pnl"]:,.0f}
    </div>
    <div class="strat-tab" data-strat="qqq" onclick="switchStrat('qqq')">
        <span class="strat-badge qqq">QQQ</span>${qqq_s["total_pnl"]:,.0f}
    </div>
</div>

<div class="stats-bar" id="statsBar">
    <div class="stat-card"><div class="label">Total P&L</div><div class="value {pnl_cls}" id="statPnl">${c["total_pnl"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Win Rate</div><div class="value neutral" id="statWR">{c["win_rate"]:.1f}%</div></div>
    <div class="stat-card"><div class="label">Trades</div><div class="value neutral" id="statTrades">{c["n_trades"]}</div></div>
    <div class="stat-card"><div class="label">Avg Win</div><div class="value green" id="statAvgWin">${c["avg_win"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Loss</div><div class="value red" id="statAvgLoss">${c["avg_loss"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Best</div><div class="value green" id="statBest">${c["max_win"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Worst</div><div class="value red" id="statWorst">${c["max_loss"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Max DD</div><div class="value red" id="statDD">${c["max_dd"]:,.0f}</div></div>
    <div class="stat-card"><div class="label">Avg Premium</div><div class="value neutral" id="statPrem">${c["avg_premium"]:,.0f}</div></div>
</div>

<div class="equity-section">
    <h3>Strategy Equity Curves</h3>
    <div class="equity-canvas-wrap">
        <canvas id="equityCurveAll" height="180"></canvas>
        <div class="equity-legend">
            <span><span class="dot" style="background:#00d4aa"></span> Combined</span>
            <span><span class="dot" style="background:#4a90d9"></span> SPX</span>
            <span><span class="dot" style="background:#bb77ff"></span> QQQ</span>
        </div>
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
        <thead><tr>
            <th>Month</th>
            <th><span class="strat-badge spx">SPX</span> #</th><th><span class="strat-badge spx">SPX</span> P&L</th>
            <th><span class="strat-badge qqq">QQQ</span> #</th><th><span class="strat-badge qqq">QQQ</span> P&L</th>
            <th>Total P&L</th><th>Cumulative</th>
        </tr></thead>
        <tbody>{monthly_rows}</tbody>
    </table>
</div>

<script>
const spxTrades = {spx_trades_json};
const qqqTrades = {qqq_trades_json};
const allTrades = {all_trades_json};

const spxChartBars = {spx_chart_json};
const qqqChartBars = {qqq_chart_json};
const spxOptChartBars = {spx_opt_chart_json};
const qqqOptChartBars = {qqq_opt_chart_json};

// Build trade maps: date → trade
const spxMap = {{}};
spxTrades.forEach(t => {{ spxMap[t.date] = t; }});
const qqqMap = {{}};
qqqTrades.forEach(t => {{ qqqMap[t.date] = t; }});

let currentYear, currentMonth;
let activeStrat = 'combined';  // 'combined', 'spx', 'qqq'

// Chart instances (we can have up to 4 charts visible: SPX intraday, SPX option, QQQ intraday, QQQ option)
let charts = {{}};

const lastDate = new Date(allTrades[allTrades.length - 1].date + 'T00:00:00');
currentYear = lastDate.getFullYear();
currentMonth = lastDate.getMonth();

// Pre-compute stats for each strategy
const stratStats = {{
    combined: computeStats(allTrades),
    spx: computeStats(spxTrades),
    qqq: computeStats(qqqTrades),
}};

function computeStats(trades) {{
    if (!trades.length) return {{ pnl:0, wr:0, n:0, avgWin:0, avgLoss:0, best:0, worst:0, dd:0, avgPrem:0 }};
    const pnl = trades.reduce((s,t) => s + t.pnl_dollars, 0);
    const wins = trades.filter(t => t.pnl_dollars > 0);
    const losses = trades.filter(t => t.pnl_dollars <= 0);
    const wr = wins.length / trades.length * 100;
    const avgWin = wins.length ? wins.reduce((s,t) => s + t.pnl_dollars, 0) / wins.length : 0;
    const avgLoss = losses.length ? losses.reduce((s,t) => s + t.pnl_dollars, 0) / losses.length : 0;
    const best = Math.max(...trades.map(t => t.pnl_dollars));
    const worst = Math.min(...trades.map(t => t.pnl_dollars));
    const avgPrem = trades.reduce((s,t) => s + t.opt_premium, 0) / trades.length;
    let cum=0, peak=0, dd=0;
    trades.forEach(t => {{ cum += t.pnl_dollars; if(cum>peak) peak=cum; const d=peak-cum; if(d>dd) dd=d; }});
    return {{ pnl, wr, n: trades.length, avgWin, avgLoss, best, worst, dd, avgPrem }};
}}

function switchStrat(s) {{
    activeStrat = s;
    document.querySelectorAll('.strat-tab').forEach(el => {{
        el.classList.toggle('active', el.dataset.strat === s);
    }});
    updateStatsBar();
    renderCalendar();
    drawMonthEquity();
    drawOverallEquity();
}}

function updateStatsBar() {{
    const st = stratStats[activeStrat];
    document.getElementById('statPnl').textContent = '$' + st.pnl.toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statPnl').className = 'value ' + (st.pnl >= 0 ? 'green' : 'red');
    document.getElementById('statWR').textContent = st.wr.toFixed(1) + '%';
    document.getElementById('statTrades').textContent = st.n;
    document.getElementById('statAvgWin').textContent = '$' + Math.abs(st.avgWin).toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statAvgLoss').textContent = '$' + Math.abs(st.avgLoss).toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statBest').textContent = '$' + st.best.toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statWorst').textContent = '$' + st.worst.toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statDD').textContent = '$' + st.dd.toLocaleString(undefined,{{maximumFractionDigits:0}});
    document.getElementById('statPrem').textContent = '$' + st.avgPrem.toLocaleString(undefined,{{maximumFractionDigits:0}});
}}

function getFilteredTrades() {{
    if (activeStrat === 'spx') return spxTrades;
    if (activeStrat === 'qqq') return qqqTrades;
    return allTrades;
}}

function renderCalendar() {{
    const grid = document.getElementById('calGrid');
    const title = document.getElementById('monthTitle');
    const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    title.textContent = months[currentMonth] + ' ' + currentYear;

    const monthKey = currentYear + '-' + String(currentMonth+1).padStart(2,'0');
    const filtered = getFilteredTrades();
    const monthTrades = filtered.filter(t => t.date.startsWith(monthKey));

    // Month stats
    const mCount = monthTrades.length;
    const mWins = monthTrades.filter(t => t.pnl_dollars > 0).length;
    const mPnl = monthTrades.reduce((s, t) => s + t.pnl_dollars, 0);
    const pnlCls = mPnl >= 0 ? 'green' : 'red';

    let statsHtml = '';
    statsHtml += '<div class="ms"><div class="msl">Month P&L</div><div class="msv ' + pnlCls + '">' + (mPnl >= 0 ? '+$' : '-$') + Math.abs(mPnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</div></div>';
    statsHtml += '<div class="ms"><div class="msl">Trades</div><div class="msv neutral">' + mCount + '</div></div>';
    if (mCount > 0) {{
        const mWR = (mWins / mCount * 100).toFixed(1);
        statsHtml += '<div class="ms"><div class="msl">Win Rate</div><div class="msv neutral">' + mWR + '%</div></div>';
        statsHtml += '<div class="ms"><div class="msl">W / L</div><div class="msv neutral">' + mWins + ' / ' + (mCount - mWins) + '</div></div>';
        if (activeStrat === 'combined') {{
            const mSpx = monthTrades.filter(t => t.strategy === 'SPX');
            const mQqq = monthTrades.filter(t => t.strategy === 'QQQ');
            const spxPnl = mSpx.reduce((s,t) => s + t.pnl_dollars, 0);
            const qqqPnl = mQqq.reduce((s,t) => s + t.pnl_dollars, 0);
            if (mSpx.length) statsHtml += '<div class="ms"><div class="msl"><span class="strat-badge spx" style="margin:0">SPX</span></div><div class="msv" style="color:#4a90d9">' + (spxPnl >= 0 ? '+$' : '-$') + Math.abs(spxPnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' (' + mSpx.length + ')</div></div>';
            if (mQqq.length) statsHtml += '<div class="ms"><div class="msl"><span class="strat-badge qqq" style="margin:0">QQQ</span></div><div class="msv" style="color:#bb77ff">' + (qqqPnl >= 0 ? '+$' : '-$') + Math.abs(qqqPnl).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' (' + mQqq.length + ')</div></div>';
        }}
    }}
    document.getElementById('monthStats').innerHTML = mCount > 0 ? statsHtml : '<div class="ms" style="flex:unset;width:100%;text-align:center"><div class="msv neutral" style="font-size:13px">No trades this month</div></div>';

    // Calendar grid
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

    // Build day PnL map from filtered trades
    const dayPnl = {{}};
    const dayStrats = {{}};
    monthTrades.forEach(t => {{
        if (!dayPnl[t.date]) {{ dayPnl[t.date] = 0; dayStrats[t.date] = []; }}
        dayPnl[t.date] += t.pnl_dollars;
        dayStrats[t.date].push(t.strategy);
    }});

    for (let d = 1; d <= daysInMonth; d++) {{
        const dow = new Date(currentYear, currentMonth, d).getDay();
        if (dow === 0 || dow === 6) continue;

        const dateStr = currentYear + '-' + String(currentMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
        const hasTrade = dayPnl[dateStr] !== undefined;

        if (hasTrade) {{
            const pnl = dayPnl[dateStr];
            const strats = dayStrats[dateStr];
            const cls = pnl >= 0 ? 'win' : 'loss';
            const pCls = pnl >= 0 ? 'green' : 'red';
            const pStr = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toLocaleString(undefined,{{maximumFractionDigits:0}});

            let stratDots = '';
            if (strats.includes('SPX')) stratDots += '<span class="strat-dot spx">SPX</span>';
            if (strats.includes('QQQ')) stratDots += '<span class="strat-dot qqq">QQQ</span>';

            html += '<div class="cal-day ' + cls + '" onclick="showTrade(\\'' + dateStr + '\\')" id="day-' + dateStr + '">';
            html += '<div class="day-num">' + d + '</div>';
            html += '<div class="day-strats">' + stratDots + '</div>';
            html += '<div class="day-pnl ' + pCls + '">' + pStr + '</div>';
            html += '</div>';
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

    const spxT = spxMap[dateStr];
    const qqqT = qqqMap[dateStr];
    if (!spxT && !qqqT) return;

    // Filter by active strategy
    const showSpx = spxT && (activeStrat === 'combined' || activeStrat === 'spx');
    const showQqq = qqqT && (activeStrat === 'combined' || activeStrat === 'qqq');
    if (!showSpx && !showQqq) return;

    const panel = document.getElementById('detailPanel');
    panel.classList.add('visible');

    let totalDayPnl = 0;
    if (showSpx) totalDayPnl += spxT.pnl_dollars;
    if (showQqq) totalDayPnl += qqqT.pnl_dollars;

    const dayOfWeek = (spxT || qqqT).day_of_week;
    const pnlCls = totalDayPnl >= 0 ? 'green' : 'red';
    const pnlStr = (totalDayPnl >= 0 ? '+$' : '-$') + Math.abs(totalDayPnl).toLocaleString(undefined,{{maximumFractionDigits:0}});

    let html = '<div style="font-size:15px;font-weight:600;color:#fff;margin-bottom:4px">' + dayOfWeek + ', ' + dateStr + '</div>';
    if (showSpx && showQqq) {{
        html += '<div class="pnl-big ' + pnlCls + '" style="font-size:22px;margin:2px 0">' + pnlStr + ' combined</div>';
    }}

    // SPX section
    if (showSpx) {{
        html += buildSpxSection(spxT, dateStr);
    }}

    // QQQ section
    if (showQqq) {{
        html += buildQqqSection(qqqT, dateStr);
    }}

    document.getElementById('detailContent').innerHTML = html;
    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});

    // Render charts
    destroyAllCharts();
    if (showSpx) {{
        renderUnderlying('spxChartContainer', 'spxOhlcLegend', spxChartBars[dateStr], spxT, 'SPX', '#2962ff');
        if (spxOptChartBars[dateStr]) renderOption('spxOptChartContainer', 'spxOptOhlcLegend', spxOptChartBars[dateStr], spxT, 'SPX');
    }}
    if (showQqq) {{
        renderUnderlying('qqqChartContainer', 'qqqOhlcLegend', qqqChartBars[dateStr], qqqT, 'QQQ', '#bb77ff');
        if (qqqOptChartBars[dateStr]) renderOption('qqqOptChartContainer', 'qqqOptOhlcLegend', qqqOptChartBars[dateStr], qqqT, 'QQQ');
    }}
}}

function buildSpxSection(t, dateStr) {{
    const pnlCls = t.pnl_dollars >= 0 ? 'green' : 'red';
    const pnlStr = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
    const holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';

    const linDiff = t.pnl_dollars - t.linear_pnl;
    const linBadge = linDiff >= 0
        ? '<span class="opt-badge better">Options +$' + Math.abs(linDiff).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' vs linear</span>'
        : '<span class="opt-badge worse">Options -$' + Math.abs(linDiff).toLocaleString(undefined,{{maximumFractionDigits:0}}) + ' vs linear</span>';

    let signalTags = '';
    t.signals.forEach(s => {{
        const isNeg = s.toLowerCase().includes('negative') || s.toLowerCase().includes('far above');
        signalTags += '<span class="signal-tag' + (isNeg ? ' negative' : '') + '">' + s + '</span>';
    }});

    let h = '<div class="trade-section spx">';
    h += '<div class="trade-section-header"><span class="strat-label spx">SPX</span> <span class="pnl-big ' + pnlCls + '" style="font-size:20px;margin:0">' + pnlStr + '</span>' + linBadge + '</div>';
    h += '<div class="trade-info-details">';
    h += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike.toLocaleString() + '</span></span>';
    h += '<span class="di"><span class="dlabel">Call Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
    h += '<span class="di"><span class="dlabel">Call Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
    h += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
    h += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
    h += '<span class="di"><span class="dlabel">SPX Open</span><span class="dvalue">' + t.entry_price.toLocaleString(undefined,{{minimumFractionDigits:2}}) + '</span></span>';
    h += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">' + t.exit_time + ' (' + holdStr + ')</span></span>';
    h += '<span class="di"><span class="dlabel">Reason</span><span class="dvalue">' + t.exit_reason + '</span></span>';
    h += '<span class="di"><span class="dlabel">PT/SL/TS</span><span class="dvalue">' + t.pt + '/' + t.sl + '/' + t.ts + 'm</span></span>';
    h += '<span class="di"><span class="dlabel">Score</span><span class="dvalue">' + t.score + '</span></span>';
    if (t.vix) h += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
    h += '</div>';
    if (signalTags) h += '<div style="margin-top:6px">' + signalTags + '</div>';

    // SPX charts
    h += '<div class="chart-wrap"><div class="ch-header"><span class="ch-title">SPX 1m &mdash; ' + dateStr + '</span>';
    h += '<span class="ch-sub"><span style="color:#2962ff">&#9646; Open ' + t.entry_price.toFixed(2) + '</span></span></div>';
    h += '<div id="spxChartContainer" style="width:100%;height:350px;position:relative"><div id="spxOhlcLegend" class="ohlc-legend"></div></div></div>';

    if (spxOptChartBars[dateStr]) {{
        h += '<div class="chart-wrap"><div class="ch-header"><span class="ch-title">SPX 0DTE Call $' + t.strike + 'C</span>';
        h += '<span class="ch-sub"><span style="color:#2962ff">&#9646; $' + t.opt_entry_price.toFixed(2) + '</span> &rarr; <span style="color:' + (t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466') + '">$' + t.opt_exit_price.toFixed(2) + '</span></span></div>';
        h += '<div id="spxOptChartContainer" style="width:100%;height:300px;position:relative"><div id="spxOptOhlcLegend" class="ohlc-legend"></div></div></div>';
    }}

    h += '</div>';
    return h;
}}

function buildQqqSection(t, dateStr) {{
    const pnlCls = t.pnl_dollars >= 0 ? 'green' : 'red';
    const pnlStr = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
    const holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';

    let h = '<div class="trade-section qqq">';
    h += '<div class="trade-section-header"><span class="strat-label qqq">QQQ</span> <span class="pnl-big ' + pnlCls + '" style="font-size:20px;margin:0">' + pnlStr + '</span></div>';
    h += '<div class="trade-info-details">';
    h += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike + '</span></span>';
    h += '<span class="di"><span class="dlabel">Call Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
    h += '<span class="di"><span class="dlabel">Call Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
    h += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
    h += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
    h += '<span class="di"><span class="dlabel">QQQ Open</span><span class="dvalue">$' + t.entry_price.toFixed(2) + '</span></span>';
    h += '<span class="di"><span class="dlabel">1st Bar</span><span class="dvalue" style="color:#00d4aa">+' + t.fb_ret.toFixed(3) + '%</span></span>';
    h += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">' + t.exit_time + ' (' + holdStr + ')</span></span>';
    h += '<span class="di"><span class="dlabel">Reason</span><span class="dvalue">' + t.exit_reason + '</span></span>';
    h += '<span class="di"><span class="dlabel">Risk</span><span class="dvalue">$' + t.risk.toLocaleString() + '</span></span>';
    if (t.vix) h += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
    h += '</div>';

    // QQQ charts
    h += '<div class="chart-wrap"><div class="ch-header"><span class="ch-title">QQQ 1m &mdash; ' + dateStr + '</span>';
    h += '<span class="ch-sub"><span style="color:#bb77ff">&#9646; Open $' + t.entry_price.toFixed(2) + '</span></span></div>';
    h += '<div id="qqqChartContainer" style="width:100%;height:350px;position:relative"><div id="qqqOhlcLegend" class="ohlc-legend"></div></div></div>';

    if (qqqOptChartBars[dateStr]) {{
        h += '<div class="chart-wrap"><div class="ch-header"><span class="ch-title">QQQ 0DTE Call $' + t.strike + 'C</span>';
        h += '<span class="ch-sub"><span style="color:#bb77ff">&#9646; $' + t.opt_entry_price.toFixed(2) + '</span> &rarr; <span style="color:' + (t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466') + '">$' + t.opt_exit_price.toFixed(2) + '</span></span></div>';
        h += '<div id="qqqOptChartContainer" style="width:100%;height:300px;position:relative"><div id="qqqOptOhlcLegend" class="ohlc-legend"></div></div></div>';
    }}

    h += '</div>';
    return h;
}}

function destroyAllCharts() {{
    Object.values(charts).forEach(c => {{ if (c) c.remove(); }});
    charts = {{}};
}}

function renderUnderlying(containerId, legendId, bars, t, label, color) {{
    if (!bars || !bars.length) return;
    const container = document.getElementById(containerId);
    if (!container) return;

    const chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth, height: 350,
        layout: {{ background: {{ color: '#131722' }}, textColor: '#DDD' }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        timeScale: {{ borderColor: '#2a2e3d', timeVisible: true, secondsVisible: false }},
        rightPriceScale: {{ borderColor: '#2a2e3d' }},
        grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.04)' }}, horzLines: {{ color: 'rgba(255,255,255,0.04)' }} }},
    }});
    charts[containerId] = chart;

    const series = chart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }});

    const candleData = bars.map(b => ({{ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }}));
    series.setData(candleData);

    const entryTime = candleData[0].time;
    const entryPrice = t.entry_price;

    // Entry price line
    series.createPriceLine({{ price: entryPrice, color: color, lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});

    if (label === 'SPX' && t.pt > 0) {{
        series.createPriceLine({{ price: entryPrice + t.pt, color: '#00d4aa', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
        series.createPriceLine({{ price: entryPrice - t.sl, color: '#ff4466', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
    }}

    let exitTime = candleData[candleData.length - 1].time;
    if (t.exit_time) {{
        const [eHH, eMM] = t.exit_time.split(':').map(Number);
        const eMins = eHH * 60 + eMM;
        for (const bar of candleData) {{
            const bd = new Date(bar.time * 1000);
            const bm = bd.getUTCHours() * 60 + bd.getUTCMinutes();
            if (bm >= eMins) {{ exitTime = bar.time; break; }}
        }}
    }}

    const exitColor = t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466';
    const markers = [
        {{ time: entryTime, position: 'belowBar', color: color, shape: 'arrowUp', text: 'BUY CALL $' + t.opt_entry_price.toFixed(2) + ' x' + t.opt_contracts }},
        {{ time: exitTime, position: 'aboveBar', color: exitColor, shape: 'arrowDown', text: t.exit_reason + ' $' + t.opt_exit_price.toFixed(2) }},
    ];
    markers.sort((a, b) => a.time - b.time);
    series.setMarkers(markers);
    chart.timeScale().fitContent();

    // OHLC legend
    const legend = document.getElementById(legendId);
    chart.subscribeCrosshairMove(param => {{
        if (!param || !param.time || !param.seriesData) {{ legend.innerHTML = ''; return; }}
        const data = param.seriesData.get(series);
        if (!data) {{ legend.innerHTML = ''; return; }}
        const o = data.open, h = data.high, l = data.low, c = data.close;
        const chg = c - o;
        const clr = chg >= 0 ? '#26a69a' : '#ef5350';
        const dt = new Date(param.time * 1000);
        const hh = String(dt.getUTCHours()).padStart(2,'0');
        const mm = String(dt.getUTCMinutes()).padStart(2,'0');
        legend.innerHTML = '<span><span class="ol">T</span> <span class="ov">' + hh + ':' + mm + '</span></span>' +
            '<span><span class="ol">O</span> <span class="ov">' + o.toFixed(2) + '</span></span>' +
            '<span><span class="ol">H</span> <span class="ov">' + h.toFixed(2) + '</span></span>' +
            '<span><span class="ol">L</span> <span class="ov">' + l.toFixed(2) + '</span></span>' +
            '<span><span class="ol">C</span> <span class="ov" style="color:' + clr + '">' + c.toFixed(2) + '</span></span>';
    }});

    new ResizeObserver(() => {{ chart.applyOptions({{ width: container.clientWidth }}); }}).observe(container);
}}

function renderOption(containerId, legendId, bars, t, label) {{
    if (!bars || !bars.length) return;
    const container = document.getElementById(containerId);
    if (!container) return;

    const chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth, height: 300,
        layout: {{ background: {{ color: '#131722' }}, textColor: '#DDD' }},
        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
        timeScale: {{ borderColor: '#2a2e3d', timeVisible: true, secondsVisible: false }},
        rightPriceScale: {{ borderColor: '#2a2e3d' }},
        grid: {{ vertLines: {{ color: 'rgba(255,255,255,0.04)' }}, horzLines: {{ color: 'rgba(255,255,255,0.04)' }} }},
    }});
    charts[containerId] = chart;

    const series = chart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }});

    const candleData = bars.map(b => ({{ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }}));
    series.setData(candleData);

    series.createPriceLine({{ price: t.opt_entry_price, color: '#2962ff', lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});
    const exitColor = t.pnl_dollars >= 0 ? '#00d4aa' : '#ff4466';
    series.createPriceLine({{ price: t.opt_exit_price, color: exitColor, lineWidth: 1, lineStyle: 2, lineVisible: false, axisLabelVisible: true, title: '' }});

    let entryTime = candleData[0].time;
    let exitTime = candleData[candleData.length - 1].time;
    for (const bar of candleData) {{
        const bd = new Date(bar.time * 1000);
        const bt = String(bd.getUTCHours()).padStart(2,'0') + ':' + String(bd.getUTCMinutes()).padStart(2,'0');
        if (bt === '09:31') {{ entryTime = bar.time; break; }}
    }}
    if (t.exit_time) {{
        const [eH, eM] = t.exit_time.split(':').map(Number);
        const em = eH * 60 + eM;
        for (const bar of candleData) {{
            const bd = new Date(bar.time * 1000);
            const bm = bd.getUTCHours() * 60 + bd.getUTCMinutes();
            if (bm >= em) {{ exitTime = bar.time; break; }}
        }}
    }}

    const markers = [
        {{ time: entryTime, position: 'belowBar', color: '#2962ff', shape: 'arrowUp', text: 'BUY $' + t.opt_entry_price.toFixed(2) + ' x' + t.opt_contracts }},
        {{ time: exitTime, position: 'aboveBar', color: exitColor, shape: 'arrowDown', text: 'SELL $' + t.opt_exit_price.toFixed(2) }},
    ];
    markers.sort((a, b) => a.time - b.time);
    series.setMarkers(markers);
    chart.timeScale().fitContent();

    const legend = document.getElementById(legendId);
    chart.subscribeCrosshairMove(param => {{
        if (!param || !param.time || !param.seriesData) {{ legend.innerHTML = ''; return; }}
        const data = param.seriesData.get(series);
        if (!data) {{ legend.innerHTML = ''; return; }}
        const o = data.open, h = data.high, l = data.low, c = data.close;
        const chg = c - o;
        const clr = chg >= 0 ? '#26a69a' : '#ef5350';
        const dt = new Date(param.time * 1000);
        const hh = String(dt.getUTCHours()).padStart(2,'0');
        const mm = String(dt.getUTCMinutes()).padStart(2,'0');
        legend.innerHTML = '<span><span class="ol">T</span> <span class="ov">' + hh + ':' + mm + '</span></span>' +
            '<span><span class="ol">O</span> <span class="ov">$' + o.toFixed(2) + '</span></span>' +
            '<span><span class="ol">H</span> <span class="ov">$' + h.toFixed(2) + '</span></span>' +
            '<span><span class="ol">L</span> <span class="ov">$' + l.toFixed(2) + '</span></span>' +
            '<span><span class="ol">C</span> <span class="ov" style="color:' + clr + '">$' + c.toFixed(2) + '</span></span>';
    }});

    new ResizeObserver(() => {{ chart.applyOptions({{ width: container.clientWidth }}); }}).observe(container);
}}

// ── Equity Curves ───────────────────────────────────────────────────
const CURVE_HEIGHTS = {{ equityCurveAll: 180, equityCurveMonth: 120 }};

function drawMultiEquityCurve(canvasId, datasets, height) {{
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();
    const W = rect.width - 24;
    const H = height || CURVE_HEIGHTS[canvasId] || 120;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    let allVals = [];
    datasets.forEach(ds => {{ ds.points.forEach(p => allVals.push(p.v)); }});
    if (!allVals.length) return;

    const minV = Math.min(0, ...allVals);
    const maxV = Math.max(0, ...allVals);
    const range = maxV - minV || 1;
    const padTop = 25, padBot = 20, padLeft = 65, padRight = 16;
    const cW = W - padLeft - padRight;
    const cH = H - padTop - padBot;

    // Use the longest dataset for x-axis
    const maxLen = Math.max(...datasets.map(ds => ds.points.length));
    function x(i, len) {{ return padLeft + (i / (len - 1 || 1)) * cW; }}
    function y(v) {{ return padTop + (1 - (v - minV) / range) * cH; }}

    // Zero line
    ctx.strokeStyle = 'rgba(255,255,255,0.1)'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(padLeft, y(0)); ctx.lineTo(W - padRight, y(0)); ctx.stroke();
    ctx.setLineDash([]);

    // Draw each dataset
    datasets.forEach(ds => {{
        const pts = ds.points;
        if (pts.length < 2) return;
        const len = pts.length;

        // Fill
        ctx.beginPath(); ctx.moveTo(x(0, len), y(0));
        for (let i = 0; i < len; i++) ctx.lineTo(x(i, len), y(pts[i].v));
        ctx.lineTo(x(len - 1, len), y(0)); ctx.closePath();
        const grad = ctx.createLinearGradient(0, padTop, 0, padTop + cH);
        grad.addColorStop(0, ds.color.replace(')', ',0.08)').replace('rgb', 'rgba'));
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = grad; ctx.fill();

        // Line
        ctx.beginPath(); ctx.strokeStyle = ds.color; ctx.lineWidth = ds.primary ? 2.5 : 1.5; ctx.lineJoin = 'round';
        if (!ds.primary) ctx.setLineDash([4, 3]);
        for (let i = 0; i < len; i++) {{ if (i === 0) ctx.moveTo(x(i, len), y(pts[i].v)); else ctx.lineTo(x(i, len), y(pts[i].v)); }}
        ctx.stroke();
        ctx.setLineDash([]);

        // End dot + label
        const lastIdx = len - 1;
        ctx.beginPath(); ctx.arc(x(lastIdx, len), y(pts[lastIdx].v), 3, 0, Math.PI * 2);
        ctx.fillStyle = ds.color; ctx.fill();

        const endVal = pts[lastIdx].v;
        ctx.fillStyle = ds.color; ctx.font = 'bold 10px -apple-system, sans-serif'; ctx.textAlign = 'left';
        const endLabel = ds.label + ' ' + (endVal >= 0 ? '+$' : '-$') + (Math.abs(endVal) >= 1000000 ? (Math.abs(endVal)/1000000).toFixed(1) + 'M' : Math.abs(endVal/1000).toFixed(0) + 'k');
        ctx.fillText(endLabel, x(lastIdx, len) + 6, y(endVal) + (ds.yOffset || 0));
    }});

    // Y axis labels
    ctx.fillStyle = '#888'; ctx.font = '10px -apple-system, sans-serif'; ctx.textAlign = 'right';
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {{
        const v = minV + (range * i / ySteps);
        const label = v >= 1000000 || v <= -1000000
            ? (v >= 0 ? '+$' : '-$') + (Math.abs(v)/1000000).toFixed(1) + 'M'
            : (v >= 0 ? '+$' : '-$') + Math.abs(v/1000).toFixed(0) + 'k';
        ctx.fillText(label, padLeft - 6, y(v) + 3);
    }}

    // X axis dates
    const mainDs = datasets.find(d => d.primary) || datasets[0];
    const mpts = mainDs.points;
    ctx.fillStyle = '#666'; ctx.font = '9px -apple-system, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(mpts[0].d, x(0, mpts.length), H - 4);
    ctx.fillText(mpts[mpts.length-1].d, x(mpts.length-1, mpts.length), H - 4);
    if (mpts.length > 10) {{ const mid = Math.floor(mpts.length / 2); ctx.fillText(mpts[mid].d, x(mid, mpts.length), H - 4); }}
}}

// Build equity arrays
const eqCombined = []; let cumC = 0;
allTrades.forEach(t => {{ cumC += t.pnl_dollars; eqCombined.push({{ d: t.date.slice(5), v: cumC }}); }});

const eqSpx = []; let cumS = 0;
spxTrades.forEach(t => {{ cumS += t.pnl_dollars; eqSpx.push({{ d: t.date.slice(5), v: cumS }}); }});

const eqQqq = []; let cumQ = 0;
qqqTrades.forEach(t => {{ cumQ += t.pnl_dollars; eqQqq.push({{ d: t.date.slice(5), v: cumQ }}); }});

function drawOverallEquity() {{
    if (activeStrat === 'combined') {{
        drawMultiEquityCurve('equityCurveAll', [
            {{ points: eqCombined, color: 'rgb(0,212,170)', primary: true, label: 'Combined', yOffset: -4 }},
            {{ points: eqSpx, color: 'rgb(74,144,217)', primary: false, label: 'SPX', yOffset: 4 }},
            {{ points: eqQqq, color: 'rgb(187,119,255)', primary: false, label: 'QQQ', yOffset: 12 }},
        ], 180);
    }} else if (activeStrat === 'spx') {{
        drawMultiEquityCurve('equityCurveAll', [
            {{ points: eqSpx, color: 'rgb(74,144,217)', primary: true, label: 'SPX', yOffset: 0 }},
        ], 180);
    }} else {{
        drawMultiEquityCurve('equityCurveAll', [
            {{ points: eqQqq, color: 'rgb(187,119,255)', primary: true, label: 'QQQ', yOffset: 0 }},
        ], 180);
    }}
}}

function drawMonthEquity() {{
    const monthKey = currentYear + '-' + String(currentMonth+1).padStart(2,'0');
    const wrap = document.getElementById('monthCurveWrap');
    const filtered = getFilteredTrades();
    const monthTrades = filtered.filter(t => t.date.startsWith(monthKey));
    if (monthTrades.length < 2) {{ wrap.style.display = 'none'; return; }}
    wrap.style.display = 'block';

    const pts = []; let mCum = 0;
    monthTrades.forEach(t => {{ mCum += t.pnl_dollars; pts.push({{ d: t.date.slice(8), v: mCum }}); }});
    const color = activeStrat === 'spx' ? 'rgb(74,144,217)' : activeStrat === 'qqq' ? 'rgb(187,119,255)' : (mCum >= 0 ? 'rgb(0,212,170)' : 'rgb(255,68,102)');
    drawMultiEquityCurve('equityCurveMonth', [
        {{ points: pts, color: color, primary: true, label: '', yOffset: 0 }},
    ], 120);
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
    print("Loading SPX trades...")
    with open(SPX_TRADES_JSON, "r") as f:
        spx_raw = json.load(f)
    print(f"  {len(spx_raw)} SPX trades")

    print("Loading QQQ trades...")
    with open(QQQ_TRADES_JSON, "r") as f:
        qqq_raw = json.load(f)
    print(f"  {len(qqq_raw)} QQQ trades")

    print("Transforming trades...")
    spx_trades = transform_spx_trades(spx_raw)
    qqq_trades = transform_qqq_trades(qqq_raw)

    # Merge all trades sorted by date
    all_trades = sorted(spx_trades + qqq_trades, key=lambda t: (t["date"], t["strategy"]))
    print(f"  {len(all_trades)} total trades ({len(spx_trades)} SPX + {len(qqq_trades)} QQQ)")

    # Check date overlap
    spx_dates = set(t["date"] for t in spx_trades)
    qqq_dates = set(t["date"] for t in qqq_trades)
    overlap = spx_dates & qqq_dates
    print(f"  {len(overlap)} days with both SPX and QQQ trades")

    print("Loading intraday data...")
    spx_intraday = load_intraday(SPX_1MIN, "SPX")
    qqq_intraday = load_intraday(QQQ_1MIN, "QQQ")

    print("Building chart data...")
    spx_trade_dates = [t["date"] for t in spx_trades]
    qqq_trade_dates = [t["date"] for t in qqq_trades]
    spx_chart_data = build_chart_data(spx_intraday, spx_trade_dates)
    qqq_chart_data = build_chart_data(qqq_intraday, qqq_trade_dates)
    print(f"  SPX: {len(spx_chart_data)} days, QQQ: {len(qqq_chart_data)} days")

    print("Loading option chart data...")
    spx_opt_chart_data = build_option_chart_data(spx_raw, SPX_CACHE_DIR)
    qqq_opt_chart_data = build_option_chart_data(qqq_raw, QQQ_CACHE_DIR)
    print(f"  SPX opts: {len(spx_opt_chart_data)} days, QQQ opts: {len(qqq_opt_chart_data)} days")

    print("Generating HTML...")
    html = generate_html(
        spx_trades, qqq_trades, all_trades,
        spx_chart_data, qqq_chart_data,
        spx_opt_chart_data, qqq_opt_chart_data,
    )
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    file_size = os.path.getsize(OUTPUT_HTML) / (1024 * 1024)
    combined_pnl = sum(t["pnl_dollars"] for t in all_trades)
    spx_pnl = sum(t["pnl_dollars"] for t in spx_trades)
    qqq_pnl = sum(t["pnl_dollars"] for t in qqq_trades)
    print(f"\nCombined Calendar Generated!")
    print(f"  SPX: {len(spx_trades)} trades, P&L: ${spx_pnl:,.0f}")
    print(f"  QQQ: {len(qqq_trades)} trades, P&L: ${qqq_pnl:,.0f}")
    print(f"  Total: {len(all_trades)} trades, P&L: ${combined_pnl:,.0f}")
    print(f"  Saved: {OUTPUT_HTML} ({file_size:.1f} MB)")


if __name__ == "__main__":
    main()
