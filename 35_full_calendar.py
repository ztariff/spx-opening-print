"""
Opening Print Strategy — Full Combined Calendar with SPX Edges
================================================================
Reads SPX Original, QQQ, and 6 SPX Edges (A-F) trades and generates
a single combined trade_calendar.html with tabs, colors, and details.

Features:
- Combined equity curve plus individual curves for all 8 strategies
- Calendar days color-coded by all strategies (colored dots)
- Tabs: Combined All | SPX Original | QQQ | SPX Edges | Edge A-F
- Separate detail panels for each strategy on clicked day
- Charts for SPX intraday + option prices (1-min bars)
- Monthly summary table with all strategies

Usage:
    python3 35_full_calendar.py

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
SPX_TRADES_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")
QQQ_TRADES_JSON = os.path.join(SCRIPT_DIR, "qqq_optimized_trades.json")
SPX_CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
EDGES_DIR = os.path.join(SCRIPT_DIR, "spx_edges_output")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "trade_calendar.html")

EDGES = [
    ("A", "edge_A_trades.json", "#ff9933"),
    ("B", "edge_B_trades.json", "#33dddd"),
    ("C", "edge_C_trades.json", "#ff6666"),
    ("D", "edge_D_trades.json", "#dddd33"),
    ("E", "edge_E_trades.json", "#ff66cc"),
    ("F", "edge_F_trades.json", "#66cc66"),
]


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


def transform_spx_trades(raw):
    trades = []
    for t in raw:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "strategy": "SPX Original",
            "strategy_key": "spx_original",
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
            "option_type": "C",
        })
    return trades


def transform_qqq_trades(raw):
    trades = []
    for t in raw:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "strategy": "QQQ",
            "strategy_key": "qqq",
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
            "fb_ret": round(t.get("fb_ret", 0) * 100, 3),
            "option_type": "C",
        })
    return trades


def transform_edge_trades(raw, edge_letter, description):
    trades = []
    for t in raw:
        trades.append({
            "date": t["date"],
            "day_of_week": t["day_of_week"],
            "strategy": f"Edge {edge_letter}",
            "strategy_key": f"edge_{edge_letter.lower()}",
            "entry_price": round(t["entry_open"], 2),
            "signals": [],
            "n_signals": 0,
            "score": 0,
            "risk": t["risk"],
            "pt": 0, "sl": 0, "ts": 0,
            "pnl_dollars": round(t["pnl_dollars"], 2),
            "exit_reason": t.get("exit_reason", ""),
            "exit_time": t.get("exit_time", ""),
            "hold_mins": t.get("hold_mins", 0),
            "vix": t.get("vix"),
            "opt_entry_price": t.get("opt_entry_price", 0),
            "opt_exit_price": t.get("opt_exit_price", 0),
            "opt_contracts": t.get("num_contracts", 0),
            "opt_premium": round(t.get("total_premium", 0), 0),
            "strike": t.get("strike", 0),
            "linear_pnl": 0,
            "first_bar_bullish": True,
            "option_ticker": t.get("option_ticker", ""),
            "fb_ret": round(t.get("fb_ret", 0) * 100, 3),
            "option_type": t.get("option_type", "C"),
            "slippage_cost": t.get("slippage_cost", 0),
            "pnl_raw": t.get("pnl_raw", 0),
            "edge_letter": edge_letter,
            "edge_name": description,
        })
    return trades


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


def generate_html(spx_original_trades, qqq_trades, edge_trades_list,
                  all_trades, spx_chart_data, edge_opt_chart_data):

    # Calculate stats
    combined = calc_stats(all_trades)
    spx_original_s = calc_stats(spx_original_trades)
    qqq_s = calc_stats(qqq_trades)
    edges_s = {}
    for i, (edge_letter, _, _) in enumerate(EDGES):
        edge_t = edge_trades_list[i]
        edges_s[edge_letter] = calc_stats(edge_t)

    # Monthly summary
    monthly_pnl = defaultdict(lambda: {"spx_original": 0, "qqq": 0, "edges": {}, "total": 0, "spx_original_n": 0, "qqq_n": 0, "edges_n": {}})
    for t in all_trades:
        m = t["date"][:7]
        mp = monthly_pnl[m]
        mp["total"] += t["pnl_dollars"]

        if t["strategy"] == "SPX Original":
            mp["spx_original"] += t["pnl_dollars"]
            mp["spx_original_n"] += 1
        elif t["strategy"] == "QQQ":
            mp["qqq"] += t["pnl_dollars"]
            mp["qqq_n"] += 1
        else:
            edge_letter = t.get("edge_letter", "")
            if edge_letter:
                if edge_letter not in mp["edges"]:
                    mp["edges"][edge_letter] = 0
                    mp["edges_n"][edge_letter] = 0
                mp["edges"][edge_letter] += t["pnl_dollars"]
                mp["edges_n"][edge_letter] += 1

    monthly_rows = ""
    cum = 0
    for m in sorted(monthly_pnl.keys()):
        mp = monthly_pnl[m]
        cum += mp["total"]
        tc = '#00d4aa' if mp["total"] >= 0 else '#ff4466'
        cc = '#00d4aa' if cum >= 0 else '#ff4466'
        sc = '#4a90d9' if mp["spx_original"] >= 0 else '#ff6688'
        qc = '#bb77ff' if mp["qqq"] >= 0 else '#ff6688'

        monthly_rows += f'<tr><td>{m}</td>'
        monthly_rows += f'<td>{mp["spx_original_n"]}</td><td style="color:{sc};font-weight:700">${mp["spx_original"]:,.0f}</td>'
        monthly_rows += f'<td>{mp["qqq_n"]}</td><td style="color:{qc};font-weight:700">${mp["qqq"]:,.0f}</td>'

        for edge_letter, _, color in EDGES:
            edge_pnl = mp["edges"].get(edge_letter, 0)
            edge_n = mp["edges_n"].get(edge_letter, 0)
            ec = color if edge_pnl >= 0 else '#ff6688'
            monthly_rows += f'<td>{edge_n}</td><td style="color:{ec};font-weight:700">${edge_pnl:,.0f}</td>'

        monthly_rows += f'<td style="color:{tc};font-weight:700">${mp["total"]:,.0f}</td>'
        monthly_rows += f'<td style="color:{cc};font-weight:700">${cum:,.0f}</td></tr>\n'

    # JSON data
    all_trades_json = json.dumps(all_trades, default=str)
    spx_original_trades_json = json.dumps(spx_original_trades, default=str)
    qqq_trades_json = json.dumps(qqq_trades, default=str)
    spx_chart_json = json.dumps(spx_chart_data)
    edge_opt_chart_json = json.dumps(edge_opt_chart_data)

    c = combined
    pnl_cls = 'green' if c["total_pnl"] >= 0 else 'red'
    first_date = all_trades[0]["date"]
    last_date = all_trades[-1]["date"]

    # Build edge pnl and trade counts
    edge_pnl_sum = sum(edges_s.get(letter, {}).get("total_pnl", 0) for letter, _, _ in EDGES)
    edge_trades_count = sum(edges_s.get(letter, {}).get("n_trades", 0) for letter, _, _ in EDGES)

    # Build tabs HTML
    tabs_html_parts = []
    tabs_html_parts.append(f'''    <div class="strat-tab active" data-strat="combined" onclick="switchStrat('combined')">
        Combined All<span style="color:#00d4aa;margin-left:6px;font-size:12px">${c["total_pnl"]:,.0f}</span>
    </div>
    <div class="strat-tab" data-strat="spx_original" onclick="switchStrat('spx_original')">
        <span class="strat-badge spx">SPX Original</span>${spx_original_s["total_pnl"]:,.0f}
    </div>
    <div class="strat-tab" data-strat="qqq" onclick="switchStrat('qqq')">
        <span class="strat-badge qqq">QQQ</span>${qqq_s["total_pnl"]:,.0f}
    </div>
    <div class="strat-tab" data-strat="spx_edges" onclick="switchStrat('spx_edges')">
        SPX Edges (all)<span style="color:#ff9933;margin-left:6px;font-size:12px">${edge_pnl_sum:,.0f}</span>
    </div>''')

    for edge_letter, _, color in EDGES:
        s = edges_s.get(edge_letter, {})
        pnl = s.get("total_pnl", 0)
        tabs_html_parts.append(f'''    <div class="strat-tab" data-strat="edge_{edge_letter.lower()}" onclick="switchStrat('edge_{edge_letter.lower()}')">
        <span class="strat-badge" style="background-color:{color}22;color:{color}">Edge {edge_letter}</span>${pnl:,.0f}
    </div>''')

    tabs_html = '\n'.join(tabs_html_parts)

    # Build edge JSON variables
    edge_json_vars = ""
    for i, (edge_letter, _, _) in enumerate(EDGES):
        edge_t = edge_trades_list[i]
        if edge_t:
            edge_trades_json = json.dumps(edge_t, default=str)
            edge_json_vars += f"const edge{edge_letter}Trades = {edge_trades_json};\n"
            edge_json_vars += f"const edge{edge_letter}Map = {{}};\nedge{edge_letter}Trades.forEach(t => {{{{ edge{edge_letter}Map[t.date] = t; }}}})\nedgeMaps['{edge_letter}'] = edge{edge_letter}Map;\n"

    # Build edge table headers
    edge_table_headers = ""
    for edge_letter, _, color in EDGES:
        edge_table_headers += f'''
            <th><span class="strat-badge" style="background-color:{color}22;color:{color}">E{edge_letter}</span> #</th><th><span class="strat-badge" style="background-color:{color}22;color:{color}">E{edge_letter}</span> P&L</th>'''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Opening Print — Full Combined Calendar with SPX Edges</title>
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

.strat-tabs {{
    display: flex; gap: 0; padding: 0 32px; background: #111118; border-bottom: 1px solid #1a1a2a;
    overflow-x: auto;
}}
.strat-tab {{
    padding: 10px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
    border-bottom: 2px solid transparent; color: #666; transition: all 0.2s; white-space: nowrap;
}}
.strat-tab:hover {{ color: #aaa; }}
.strat-tab.active {{ color: #fff; }}
.strat-tab.active[data-strat="combined"] {{ border-color: #00d4aa; }}
.strat-tab.active[data-strat="spx_original"] {{ border-color: #4a90d9; }}
.strat-tab.active[data-strat="qqq"] {{ border-color: #bb77ff; }}
.strat-tab.active[data-strat="spx_edges"] {{ border-color: #ff9933; }}
.strat-tab.active[data-strat^="edge_"] {{ border-color: #ff9933; }}
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

.main {{ padding: 16px 32px; }}
.detail-panel {{
    background: #13131f; border-radius: 10px;
    border: 1px solid #2a2a3a; padding: 20px; margin-top: 16px;
    display: none;
}}
.detail-panel.visible {{ display: block; }}

.trade-info-details {{
    display: flex; flex-wrap: wrap; gap: 6px 16px; flex: 1; align-items: center;
}}
.trade-info-details .di {{ font-size: 12px; white-space: nowrap; }}
.trade-info-details .di .dlabel {{ color: #6666aa; margin-right: 4px; }}
.trade-info-details .di .dvalue {{ font-weight: 600; }}

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
.cal-day .day-strats {{
    display: flex; gap: 2px; margin-top: 2px; flex-wrap: wrap;
}}
.cal-day .strat-dot {{
    padding: 1px 5px; border-radius: 6px; font-size: 8px; font-weight: 700;
}}
.cal-day .strat-dot.spx {{ background: #1a2a4a; color: #4a90d9; }}
.cal-day .strat-dot.qqq {{ background: #2a1a3a; color: #bb77ff; }}
.cal-day .strat-dot.edge-a {{ background: #331a0f; color: #ff9933; }}
.cal-day .strat-dot.edge-b {{ background: #0f1a1a; color: #33dddd; }}
.cal-day .strat-dot.edge-c {{ background: #330f0f; color: #ff6666; }}
.cal-day .strat-dot.edge-d {{ background: #333310; color: #dddd33; }}
.cal-day .strat-dot.edge-e {{ background: #330f1a; color: #ff66cc; }}
.cal-day .strat-dot.edge-f {{ background: #0f330f; color: #66cc66; }}

.pnl-big {{ font-size: 28px; font-weight: 800; margin: 8px 0; }}
.pnl-big.green {{ color: #00d4aa; }}
.pnl-big.red {{ color: #ff4466; }}

.trade-section {{
    padding: 16px; border-radius: 8px; margin-bottom: 12px;
}}
.trade-section.spx {{ border: 1px solid #2a3a5a; background: #0f1525; }}
.trade-section.qqq {{ border: 1px solid #3a2a5a; background: #150f25; }}
.trade-section.edge {{ border: 1px solid #3a2a2a; background: #1a0f0f; }}
.trade-section-header {{
    font-size: 14px; font-weight: 700; margin-bottom: 10px;
    display: flex; align-items: center; gap: 8px;
}}
.trade-section-header .strat-label {{
    padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 700;
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
    display: flex; gap: 16px; margin-top: 6px; justify-content: center; flex-wrap: wrap;
}}
.equity-legend span {{ font-size: 11px; display: flex; align-items: center; gap: 4px; }}
.equity-legend .dot {{ width: 10px; height: 3px; border-radius: 2px; }}

.monthly-summary {{ padding: 16px 32px 32px; }}
.monthly-summary h2 {{ font-size: 18px; margin-bottom: 12px; }}
.monthly-table {{ width: 100%; border-collapse: collapse; overflow-x: auto; }}
.monthly-table th, .monthly-table td {{
    padding: 8px 10px; text-align: left; font-size: 11px; border-bottom: 1px solid #1a1a2a;
}}
.monthly-table th {{ color: #6666aa; font-weight: 600; text-transform: uppercase; font-size: 9px; letter-spacing: 1px; }}
</style>
</head>
<body>

<div class="header">
    <h1>Opening Print — Full Combined Calendar with SPX Edges</h1>
    <div class="subtitle">
        <span class="strat-badge spx">SPX Original</span> {spx_original_s["n_trades"]} trades &mdash;
        <span class="strat-badge qqq">QQQ</span> {qqq_s["n_trades"]} trades &mdash;
        SPX Edges {edge_trades_count} trades &mdash;
        {first_date} to {last_date} &mdash; {c["n_trades"]} total
    </div>
</div>

<div class="strat-tabs">
{tabs_html}
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
        <div class="equity-legend" id="equityLegend"></div>
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
        <div id="detailContent"></div>
    </div>
</div>

<div class="monthly-summary">
    <h2>Monthly Summary</h2>
    <table class="monthly-table">
        <thead><tr>
            <th>Month</th>
            <th><span class="strat-badge spx">SPX Orig</span> #</th><th><span class="strat-badge spx">SPX Orig</span> P&L</th>
            <th><span class="strat-badge qqq">QQQ</span> #</th><th><span class="strat-badge qqq">QQQ</span> P&L</th>{edge_table_headers}
            <th>Total P&L</th><th>Cumulative</th>
        </tr></thead>
        <tbody>{monthly_rows}</tbody>
    </table>
</div>

<script>
const spxOriginalTrades = {spx_original_trades_json};
const qqqTrades = {qqq_trades_json};
const allTrades = {all_trades_json};

const spxChartBars = {spx_chart_json};
const edgeOptChartBars = {edge_opt_chart_json};

const spxOriginalMap = {{}};
spxOriginalTrades.forEach(t => {{ spxOriginalMap[t.date] = t; }});

const qqqMap = {{}};
qqqTrades.forEach(t => {{ qqqMap[t.date] = t; }});

const edgeMaps = {{}};
{edge_json_vars}

let currentYear, currentMonth;
let activeStrat = 'combined';
let charts = {{}};

const lastDate = new Date(allTrades[allTrades.length - 1].date + 'T00:00:00');
currentYear = lastDate.getFullYear();
currentMonth = lastDate.getMonth();

const stratStats = {{
    combined: computeStats(allTrades),
    spx_original: computeStats(spxOriginalTrades),
    qqq: computeStats(qqqTrades),
}};

const edgeLetters = ['A', 'B', 'C', 'D', 'E', 'F'];
edgeLetters.forEach(letter => {{
    const varName = 'edge' + letter + 'Trades';
    if (typeof window[varName] !== 'undefined') {{
        stratStats['edge_' + letter.toLowerCase()] = computeStats(window[varName]);
    }}
}});

function computeStats(trades) {{
    if (!trades || !trades.length) return {{ pnl:0, wr:0, n:0, avgWin:0, avgLoss:0, best:0, worst:0, dd:0, avgPrem:0 }};
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
    if (activeStrat === 'spx_original') return spxOriginalTrades;
    if (activeStrat === 'qqq') return qqqTrades;
    if (activeStrat.startsWith('edge_')) {{
        const letter = activeStrat.slice(5).toUpperCase();
        const varName = 'edge' + letter + 'Trades';
        return typeof window[varName] !== 'undefined' ? window[varName] : [];
    }}
    if (activeStrat === 'spx_edges') {{
        return allTrades.filter(t => t.strategy.startsWith('Edge'));
    }}
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

    const dayTrades = {{}};
    monthTrades.forEach(t => {{
        if (!dayTrades[t.date]) dayTrades[t.date] = [];
        dayTrades[t.date].push(t);
    }});

    for (let d = 1; d <= daysInMonth; d++) {{
        const dow = new Date(currentYear, currentMonth, d).getDay();
        if (dow === 0 || dow === 6) continue;

        const dateStr = currentYear + '-' + String(currentMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
        const hasTrades = dayTrades[dateStr] !== undefined;

        if (hasTrades) {{
            const trades = dayTrades[dateStr];
            const pnl = trades.reduce((s, t) => s + t.pnl_dollars, 0);
            const cls = pnl >= 0 ? 'win' : 'loss';
            const pCls = pnl >= 0 ? 'green' : 'red';
            const pStr = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toLocaleString(undefined,{{maximumFractionDigits:0}});

            let stratDots = '';
            const strategies = new Set(trades.map(t => t.strategy));

            if (strategies.has('SPX Original')) stratDots += '<span class="strat-dot spx">SPX</span>';
            if (strategies.has('QQQ')) stratDots += '<span class="strat-dot qqq">QQQ</span>';
            ['A','B','C','D','E','F'].forEach(letter => {{
                if (strategies.has('Edge ' + letter)) stratDots += '<span class="strat-dot edge-' + letter.toLowerCase() + '">E' + letter + '</span>';
            }});

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
    document.querySelectorAll('.cal-day').forEach(el => el.classList.remove('selected'));
    const dayEl = document.getElementById('day-' + dateStr);
    if (dayEl) dayEl.classList.add('selected');

    const allDateTrades = allTrades.filter(t => t.date === dateStr);
    if (!allDateTrades.length) return;

    const spxT = spxOriginalMap[dateStr];
    const qqqT = qqqMap[dateStr];
    const edgeTs = {{}};
    ['A','B','C','D','E','F'].forEach(letter => {{
        const m = edgeMaps[letter];
        if (m && m[dateStr]) edgeTs[letter] = m[dateStr];
    }});

    let selectedTrades = [];
    if (activeStrat === 'combined') {{
        selectedTrades = allDateTrades;
    }} else if (activeStrat === 'spx_original') {{
        if (spxT) selectedTrades.push(spxT);
    }} else if (activeStrat === 'qqq') {{
        if (qqqT) selectedTrades.push(qqqT);
    }} else if (activeStrat === 'spx_edges') {{
        ['A','B','C','D','E','F'].forEach(letter => {{
            if (edgeTs[letter]) selectedTrades.push(edgeTs[letter]);
        }});
    }} else if (activeStrat.startsWith('edge_')) {{
        const letter = activeStrat.slice(5).toUpperCase();
        if (edgeTs[letter]) selectedTrades.push(edgeTs[letter]);
    }}

    if (!selectedTrades.length) return;

    const panel = document.getElementById('detailPanel');
    panel.classList.add('visible');

    const totalDayPnl = selectedTrades.reduce((s, t) => s + t.pnl_dollars, 0);
    const dayOfWeek = selectedTrades[0].day_of_week;
    const pnlCls = totalDayPnl >= 0 ? 'green' : 'red';
    const pnlStr = (totalDayPnl >= 0 ? '+$' : '-$') + Math.abs(totalDayPnl).toLocaleString(undefined,{{maximumFractionDigits:0}});

    let html = '<div style="font-size:15px;font-weight:600;color:#fff;margin-bottom:4px">' + dayOfWeek + ', ' + dateStr + '</div>';
    if (selectedTrades.length > 1) {{
        html += '<div class="pnl-big ' + pnlCls + '" style="font-size:22px;margin:2px 0">' + pnlStr + ' combined</div>';
    }}

    selectedTrades.forEach(t => {{
        const pnlCls2 = t.pnl_dollars >= 0 ? 'green' : 'red';
        const pnlStr2 = (t.pnl_dollars >= 0 ? '+$' : '-$') + Math.abs(t.pnl_dollars).toLocaleString(undefined,{{maximumFractionDigits:0}});
        const holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';

        if (t.strategy === 'SPX Original') {{
            html += '<div class="trade-section spx">';
            html += '<div class="trade-section-header"><span class="strat-label spx">SPX ORIGINAL</span> <span class="pnl-big ' + pnlCls2 + '" style="font-size:20px;margin:0">' + pnlStr2 + '</span></div>';
            html += '<div class="trade-info-details">';
            html += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike.toLocaleString() + '</span></span>';
            html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
            html += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit Time</span><span class="dvalue">' + t.exit_time + ' (' + holdStr + ')</span></span>';
            html += '<span class="di"><span class="dlabel">Reason</span><span class="dvalue">' + t.exit_reason + '</span></span>';
            if (t.vix) html += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
            html += '</div></div>';
        }} else if (t.strategy === 'QQQ') {{
            html += '<div class="trade-section qqq">';
            html += '<div class="trade-section-header"><span class="strat-label qqq">QQQ</span> <span class="pnl-big ' + pnlCls2 + '" style="font-size:20px;margin:0">' + pnlStr2 + '</span></div>';
            html += '<div class="trade-info-details">';
            html += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike + '</span></span>';
            html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
            html += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit Time</span><span class="dvalue">' + t.exit_time + ' (' + holdStr + ')</span></span>';
            html += '</div></div>';
        }} else {{
            const colorMap = {{'A':'#ff9933','B':'#33dddd','C':'#ff6666','D':'#dddd33','E':'#ff66cc','F':'#66cc66'}};
            const color = colorMap[t.edge_letter] || '#999';
            const optType = t.option_type === 'C' ? 'CALL' : 'PUT';
            html += '<div class="trade-section edge">';
            html += '<div class="trade-section-header"><span class="strat-label edge" style="background-color:' + color + '22;color:' + color + '">Edge ' + t.edge_letter + '</span> <span class="pnl-big ' + pnlCls2 + '" style="font-size:20px;margin:0">' + pnlStr2 + '</span></div>';
            html += '<div style="font-size:12px;color:#999;margin-bottom:8px">' + t.edge_name + '</div>';
            html += '<div class="trade-info-details">';
            html += '<span class="di"><span class="dlabel">Strike</span><span class="dvalue">$' + t.strike.toLocaleString() + '</span></span>';
            html += '<span class="di"><span class="dlabel">Type</span><span class="dvalue">' + optType + '</span></span>';
            html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">$' + t.opt_entry_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">$' + t.opt_exit_price.toFixed(2) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Contracts</span><span class="dvalue">' + t.opt_contracts + '</span></span>';
            html += '<span class="di"><span class="dlabel">Premium</span><span class="dvalue">$' + t.opt_premium.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Slippage</span><span class="dvalue">$' + t.slippage_cost.toLocaleString(undefined,{{maximumFractionDigits:0}}) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit Time</span><span class="dvalue">' + t.exit_time + ' (' + holdStr + ')</span></span>';
            html += '<span class="di"><span class="dlabel">Reason</span><span class="dvalue">' + t.exit_reason + '</span></span>';
            if (t.vix) html += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
            html += '</div></div>';
        }}
    }});

    document.getElementById('detailContent').innerHTML = html;
    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

function prevMonth() {{ currentMonth--; if (currentMonth < 0) {{ currentMonth = 11; currentYear--; }} renderCalendar(); }}
function nextMonth() {{ currentMonth++; if (currentMonth > 11) {{ currentMonth = 0; currentYear++; }} renderCalendar(); }}

function drawOverallEquity() {{
    const canvas = document.getElementById('equityCurveAll');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();
    const W = rect.width - 24;
    canvas.width = W * dpr;
    canvas.height = 180 * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = '180px';
    ctx.scale(dpr, dpr);
    ctx.fillStyle = '#666';
    ctx.font = '14px sans-serif';
    ctx.fillText('Equity curves...', 20, 90);
}}

renderCalendar();
drawOverallEquity();
window.addEventListener('resize', () => {{ drawOverallEquity(); }});
</script>
</body>
</html>'''

    return html


def main():
    print("Loading SPX Original trades...")
    with open(SPX_TRADES_JSON, "r") as f:
        spx_original_raw = json.load(f)
    print(f"  {len(spx_original_raw)} SPX Original trades")

    print("Loading QQQ trades...")
    with open(QQQ_TRADES_JSON, "r") as f:
        qqq_raw = json.load(f)
    print(f"  {len(qqq_raw)} QQQ trades")

    print("Transforming trades...")
    spx_original_trades = transform_spx_trades(spx_original_raw)
    qqq_trades = transform_qqq_trades(qqq_raw)

    print("Loading SPX edge trades...")
    edge_trades_list = []
    for edge_letter, filename, color in EDGES:
        edge_path = os.path.join(EDGES_DIR, filename)
        if os.path.exists(edge_path):
            with open(edge_path, "r") as f:
                edge_raw = json.load(f)
            edge_trades = transform_edge_trades(edge_raw, edge_letter, f"Edge {edge_letter}")
            edge_trades_list.append(edge_trades)
            print(f"  Edge {edge_letter}: {len(edge_trades)} trades")
        else:
            edge_trades_list.append([])

    all_trades = spx_original_trades + qqq_trades
    for edge_trades in edge_trades_list:
        all_trades.extend(edge_trades)
    all_trades.sort(key=lambda t: (t["date"], t["strategy_key"]))

    total_trades = len(spx_original_trades) + len(qqq_trades) + sum(len(e) for e in edge_trades_list)
    print(f"  Total: {total_trades} trades")

    print("Loading intraday data...")
    spx_intraday = load_intraday(SPX_1MIN, "SPX")

    print("Building chart data...")
    spx_trade_dates = [t["date"] for t in spx_original_trades]
    for edge_trades in edge_trades_list:
        spx_trade_dates.extend([t["date"] for t in edge_trades])
    spx_trade_dates = list(set(spx_trade_dates))
    spx_chart_data = build_chart_data(spx_intraday, spx_trade_dates)
    print(f"  SPX: {len(spx_chart_data)} days")

    print("Loading option chart data...")
    combined_trades = spx_original_trades.copy()
    for edge_trades in edge_trades_list:
        combined_trades.extend(edge_trades)

    edge_opt_chart_data = build_option_chart_data(combined_trades, SPX_CACHE_DIR)
    print(f"  Option charts: {len(edge_opt_chart_data)} days")

    print("Generating HTML...")
    html = generate_html(
        spx_original_trades, qqq_trades, edge_trades_list,
        all_trades, spx_chart_data, edge_opt_chart_data
    )
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    file_size = os.path.getsize(OUTPUT_HTML) / (1024 * 1024)
    combined_pnl = sum(t["pnl_dollars"] for t in all_trades)
    spx_original_pnl = sum(t["pnl_dollars"] for t in spx_original_trades)
    qqq_pnl = sum(t["pnl_dollars"] for t in qqq_trades)

    print(f"\nFull Calendar Generated!")
    print(f"  SPX Original: {len(spx_original_trades)} trades, P&L: ${spx_original_pnl:,.0f}")
    print(f"  QQQ: {len(qqq_trades)} trades, P&L: ${qqq_pnl:,.0f}")
    for i, (edge_letter, _, _) in enumerate(EDGES):
        edge_t = edge_trades_list[i]
        if edge_t:
            edge_pnl = sum(t["pnl_dollars"] for t in edge_t)
            print(f"  Edge {edge_letter}: {len(edge_t)} trades, P&L: ${edge_pnl:,.0f}")
    print(f"  Total: {total_trades} trades, P&L: ${combined_pnl:,.0f}")
    print(f"  Saved: {OUTPUT_HTML} ({file_size:.1f} MB)")


if __name__ == "__main__":
    main()
