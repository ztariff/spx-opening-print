"""
SPX Opening Print — Script 33: All Edges Options Backtest
============================================================
Backtests all 6 edges found in script 32 through the real options pipeline
with 10-second entry/exit resolution.

EDGE A — "SPX Scalp": All bullish 1st bar, trail=0.02%, SL=15pts, TS=30
EDGE B — "SPX Filtered Scalp": fb_ret>0.05%, trail=0.05%, SL=15pts, TS=30
EDGE C — "Bearish Short": Bearish 1st bar, BUY PUT, PT=8pts, SL=5pts, TS=30
EDGE D — "Missed Days": Bullish days not in current strategy, trail=0.05%, SL=10, TS=120
EDGE E — "Ultra Scalp": All bullish, PT=1pt, SL=1pt, TS=5
EDGE F — "Current + Trail": Currently traded days, trail=0.05%, SL=15, TS=30

For each edge:
1. Find SPX option (call or put depending on edge)
2. Get 10s entry price
3. Get 10s bars for underlying + option
4. Simulate exit at 10s resolution
5. Calculate options P&L

Usage:
    python3 33_spx_edges_backtest.py
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
VIX_1MIN = os.path.join(SCRIPT_DIR, "vix_1min_bars.csv")
SPX_TRADES_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")  # existing strategy
SPX_CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "spx_edges_report.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "spx_edges_output")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

MIN_RISK = 50000
MAX_RISK = 200000
BASE_RISK = 75000


class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()


# ── Data Loading ──────────────────────────────────────────────────────

def load_intraday():
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


def load_daily(filepath):
    data = {}
    dates = []
    with open(filepath) as f:
        for row in csv.DictReader(f):
            d = row["date"]
            data[d] = {k: float(row[k]) for k in ["open", "high", "low", "close"]}
            dates.append(d)
    return data, sorted(set(dates))


def load_vix_1min():
    days = defaultdict(list)
    if not os.path.exists(VIX_1MIN):
        return {}
    with open(VIX_1MIN, "r") as f:
        for row in csv.DictReader(f):
            t = row["time"]
            if t < "09:30" or t >= "16:00":
                continue
            days[row["date"]].append({
                "time": t,
                "open": float(row["open"]),
                "close": float(row["close"]),
            })
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return dict(days)


# ── Cache / API helpers ──────────────────────────────────────────────

def ensure_cache():
    os.makedirs(SPX_CACHE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def cache_path(key):
    return os.path.join(SPX_CACHE_DIR, key + ".json")


def load_cache(key):
    p = cache_path(key)
    if os.path.exists(p):
        with open(p, "r") as f:
            return json.load(f)
    return None


def save_cache(key, data):
    with open(cache_path(key), "w") as f:
        json.dump(data, f)


def api_get(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif e.code in (403, 404):
                return None
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


def get_dst_offset(date_str):
    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    return 4 if is_dst else 5


# ── SPX Option Functions ─────────────────────────────────────────────

def find_nearest_expiry(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    daily_0dte_start = datetime(2022, 9, 19)
    if dt >= daily_0dte_start:
        return date_str, 0
    dow = dt.weekday()
    if dow in (0, 2, 4):
        return date_str, 0
    elif dow == 1:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    elif dow == 3:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    return date_str, 0


def find_option(date_str, target_strike, option_type="C"):
    """Find SPX option (call or put).
    option_type: 'C' for call, 'P' for put."""
    strike = round(target_strike / 5) * 5
    cache_key = f"contracts_v3_{date_str}_{strike}_{option_type}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None, None, None
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]

    expiry_date, dte = find_nearest_expiry(date_str)
    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    date_code = exp_dt.strftime("%y%m%d")

    for underlying in ["SPXW", "SPX"]:
        for offset in [0, 5, -5, 10, -10]:
            test_strike = strike + offset
            strike_code = f"{int(test_strike * 1000):08d}"
            ticker = f"O:{underlying}{date_code}{option_type}{strike_code}"
            url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
                   f"/range/1/minute/{date_str}/{date_str}"
                   f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
            time.sleep(REQUEST_DELAY)
            data = api_get(url)
            if data and data.get("resultsCount", 0) > 0:
                result = {"ticker": ticker, "strike": test_strike,
                          "expiry": expiry_date, "dte": dte}
                save_cache(cache_key, result)
                return ticker, test_strike, expiry_date, dte

    save_cache(cache_key, "none")
    return None, None, None, None


def get_option_entry_price(ticker, date_str):
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"entry10s_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else None

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/10/second/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=100&apiKey={API_KEY}")
    time.sleep(REQUEST_DELAY)
    data = api_get(url)
    if not data or data.get("resultsCount", 0) == 0:
        save_cache(cache_key, "none")
        return None

    offset_hours = get_dst_offset(date_str)
    target_seconds = 9 * 3600 + 30 * 60 + 10
    best_bar = None
    best_diff = float("inf")
    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        et_seconds = dt_et.hour * 3600 + dt_et.minute * 60 + dt_et.second
        diff = abs(et_seconds - target_seconds)
        if diff < best_diff:
            best_diff = diff
            best_bar = r
    if best_bar and best_diff <= 30:
        price = round(best_bar["c"], 2)
        save_cache(cache_key, price)
        return price
    save_cache(cache_key, "none")
    return None


def get_option_1min_bars(ticker, date_str):
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"bars_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else []

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/1/minute/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    time.sleep(REQUEST_DELAY)
    data = api_get(url)
    if not data or data.get("resultsCount", 0) == 0:
        save_cache(cache_key, "none")
        return []

    offset_hours = get_dst_offset(date_str)
    bars = []
    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime("%H:%M")
        if "09:30" <= t_str <= "16:00":
            bars.append({
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"],
            })
    save_cache(cache_key, bars if bars else "none")
    return bars


def fetch_10s_bars(ticker, date_str, cache_prefix):
    safe = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"{cache_prefix}_{date_str}_{safe}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else []

    offset_hours = get_dst_offset(date_str)
    all_bars = []
    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/10/second/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    time.sleep(REQUEST_DELAY)
    data = api_get(url)

    if not data or data.get("resultsCount", 0) == 0:
        save_cache(cache_key, "none")
        return []

    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime("%H:%M:%S")
        if "09:30:00" <= t_str <= "16:05:00":
            all_bars.append({
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
            })

    while data and data.get("next_url"):
        next_url = data["next_url"] + f"&apiKey={API_KEY}"
        time.sleep(REQUEST_DELAY)
        data = api_get(next_url)
        if data and data.get("results"):
            for r in data["results"]:
                dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
                dt_et = dt_utc - timedelta(hours=offset_hours)
                t_str = dt_et.strftime("%H:%M:%S")
                if "09:30:00" <= t_str <= "16:05:00":
                    all_bars.append({
                        "time": t_str, "open": r["o"], "high": r["h"],
                        "low": r["l"], "close": r["c"], "volume": r.get("v", 0),
                    })

    all_bars.sort(key=lambda b: b["time"])
    save_cache(cache_key, all_bars if all_bars else "none")
    return all_bars


# ── Simulation Functions ─────────────────────────────────────────────

def simulate_trailing_10s(spx_10s, opt_10s, entry_open, opt_entry_price,
                          sl_pts, trail_pct, ts_minutes, risk, direction=1):
    """Trailing stop using 10s bars. direction=1 long, -1 short."""
    if not spx_10s or not opt_10s or len(spx_10s) < 2:
        return None

    opt_map = {}
    for bar in opt_10s:
        opt_map[bar["time"]] = bar

    contract_cost = opt_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    peak_price = entry_open
    trough_price = entry_open

    entry_min = 9 * 60 + 31
    stop_min = entry_min + ts_minutes
    ts_limit = f"{stop_min // 60:02d}:{stop_min % 60:02d}:00"

    exit_time = None
    exit_reason = None
    started = False

    for bar in spx_10s:
        if bar["time"] <= "09:31:00":
            if bar["time"] >= "09:31:00":
                started = True
            continue
        if not started:
            started = True

        if bar["time"] >= ts_limit:
            exit_time = bar["time"]
            exit_reason = "Time Stop"
            break

        if direction == 1:  # Long
            if bar["high"] > peak_price:
                peak_price = bar["high"]
            if bar["low"] <= entry_open - sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
            trail_level = peak_price * (1 - trail_pct / 100)
            if bar["low"] <= trail_level and peak_price > entry_open:
                exit_time = bar["time"]
                exit_reason = "Trailing Stop"
                break
        else:  # Short
            if bar["low"] < trough_price:
                trough_price = bar["low"]
            if bar["high"] >= entry_open + sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
            trail_level = trough_price * (1 + trail_pct / 100)
            if bar["high"] >= trail_level and trough_price < entry_open:
                exit_time = bar["time"]
                exit_reason = "Trailing Stop"
                break

    if exit_time is None:
        if spx_10s:
            exit_time = spx_10s[-1]["time"]
            exit_reason = "Time Stop"
        else:
            return None

    opt_exit_price = None
    if exit_time in opt_map:
        opt_exit_price = opt_map[exit_time]["close"]
    else:
        for bar in opt_10s:
            if bar["time"] >= exit_time:
                opt_exit_price = bar["close"]
                break
        if opt_exit_price is None:
            for bar in reversed(opt_10s):
                if bar["time"] <= exit_time:
                    opt_exit_price = bar["close"]
                    break
        if opt_exit_price is None:
            opt_exit_price = opt_10s[-1]["close"]

    pnl_dollars = (opt_exit_price - opt_entry_price) * 100 * num_contracts

    entry_secs = 9 * 3600 + 31 * 60
    ep = exit_time.split(":")
    exit_secs = int(ep[0]) * 3600 + int(ep[1]) * 60 + int(ep[2])
    hold_mins = max(1, round((exit_secs - entry_secs) / 60))

    return {
        "opt_entry_price": opt_entry_price,
        "opt_exit_price": opt_exit_price,
        "num_contracts": num_contracts,
        "total_premium": total_premium,
        "pnl_dollars": round(pnl_dollars, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time[:5],
        "hold_mins": hold_mins,
    }


def simulate_fixed_ptsl_10s(spx_10s, opt_10s, entry_open, opt_entry_price,
                            pt_pts, sl_pts, ts_minutes, risk, direction=1):
    """Fixed PT/SL using 10s bars."""
    if not spx_10s or not opt_10s or len(spx_10s) < 2:
        return None

    opt_map = {}
    for bar in opt_10s:
        opt_map[bar["time"]] = bar

    contract_cost = opt_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    entry_min = 9 * 60 + 31
    stop_min = entry_min + ts_minutes
    ts_limit = f"{stop_min // 60:02d}:{stop_min % 60:02d}:00"

    exit_time = None
    exit_reason = None
    started = False

    for bar in spx_10s:
        if bar["time"] <= "09:31:00":
            if bar["time"] >= "09:31:00":
                started = True
            continue
        if not started:
            started = True

        if bar["time"] >= ts_limit:
            exit_time = bar["time"]
            exit_reason = "Time Stop"
            break

        if direction == 1:
            if pt_pts > 0 and bar["high"] >= entry_open + pt_pts:
                exit_time = bar["time"]
                exit_reason = "Profit Target"
                break
            if sl_pts > 0 and bar["low"] <= entry_open - sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break
        else:
            if pt_pts > 0 and bar["low"] <= entry_open - pt_pts:
                exit_time = bar["time"]
                exit_reason = "Profit Target"
                break
            if sl_pts > 0 and bar["high"] >= entry_open + sl_pts:
                exit_time = bar["time"]
                exit_reason = "Stop Loss"
                break

    if exit_time is None:
        if spx_10s:
            exit_time = spx_10s[-1]["time"]
            exit_reason = "Time Stop"
        else:
            return None

    opt_exit_price = None
    if exit_time in opt_map:
        opt_exit_price = opt_map[exit_time]["close"]
    else:
        for bar in opt_10s:
            if bar["time"] >= exit_time:
                opt_exit_price = bar["close"]
                break
        if opt_exit_price is None:
            for bar in reversed(opt_10s):
                if bar["time"] <= exit_time:
                    opt_exit_price = bar["close"]
                    break
        if opt_exit_price is None:
            opt_exit_price = opt_10s[-1]["close"]

    pnl_dollars = (opt_exit_price - opt_entry_price) * 100 * num_contracts

    entry_secs = 9 * 3600 + 31 * 60
    ep = exit_time.split(":")
    exit_secs = int(ep[0]) * 3600 + int(ep[1]) * 60 + int(ep[2])
    hold_mins = max(1, round((exit_secs - entry_secs) / 60))

    return {
        "opt_entry_price": opt_entry_price,
        "opt_exit_price": opt_exit_price,
        "num_contracts": num_contracts,
        "total_premium": total_premium,
        "pnl_dollars": round(pnl_dollars, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time[:5],
        "hold_mins": hold_mins,
    }


# ── Edge Definitions ─────────────────────────────────────────────────

EDGES = {
    "A": {
        "name": "SPX Scalp",
        "desc": "All bullish 1st bar, trail=0.02%, SL=15pts, TS=30",
        "filter": lambda f: f["fb_bullish"],
        "option_type": "C",
        "exit_mode": "trail",
        "trail_pct": 0.02, "sl_pts": 15, "ts_min": 30,
        "direction": 1,
    },
    "B": {
        "name": "SPX Filtered Scalp",
        "desc": "fb_ret>0.05%, trail=0.05%, SL=15pts, TS=30",
        "filter": lambda f: f["fb_bullish"] and f["fb_ret"] > 0.05,
        "option_type": "C",
        "exit_mode": "trail",
        "trail_pct": 0.05, "sl_pts": 15, "ts_min": 30,
        "direction": 1,
    },
    "C": {
        "name": "Bearish Short",
        "desc": "Bearish 1st bar, BUY PUT, PT=8pts, SL=5pts, TS=30",
        "filter": lambda f: not f["fb_bullish"],
        "option_type": "P",
        "exit_mode": "fixed",
        "pt_pts": 8, "sl_pts": 5, "ts_min": 30,
        "direction": -1,
    },
    "D": {
        "name": "Missed Days",
        "desc": "Bullish non-traded days, trail=0.05%, SL=10pts, TS=120",
        "filter": "missed",  # Special — computed at runtime
        "option_type": "C",
        "exit_mode": "trail",
        "trail_pct": 0.05, "sl_pts": 10, "ts_min": 120,
        "direction": 1,
    },
    "E": {
        "name": "Ultra Scalp",
        "desc": "All bullish, PT=1pt, SL=1pt, TS=5min",
        "filter": lambda f: f["fb_bullish"],
        "option_type": "C",
        "exit_mode": "fixed",
        "pt_pts": 1, "sl_pts": 1, "ts_min": 5,
        "direction": 1,
    },
    "F": {
        "name": "Current + Trail",
        "desc": "Currently traded days, trail=0.05%, SL=15pts, TS=30",
        "filter": "current",  # Special — computed at runtime
        "option_type": "C",
        "exit_mode": "trail",
        "trail_pct": 0.05, "sl_pts": 15, "ts_min": 30,
        "direction": 1,
    },
}


def build_features(d, bars, spx_daily, spx_dates, vix_daily, vix_1min):
    if len(bars) < 5:
        return None
    entry_open = bars[0]["open"]
    fb = bars[0]
    fb_ret = (fb["close"] - fb["open"]) / fb["open"] * 100
    fb_bullish = fb["close"] > fb["open"]
    dt = datetime.strptime(d, "%Y-%m-%d")
    dow = dt.strftime("%A")
    vix = None
    if d in vix_daily:
        vix = vix_daily[d]["open"]
    elif vix_1min and d in vix_1min and vix_1min[d]:
        vix = vix_1min[d][0]["open"]

    return {
        "date": d, "entry_open": entry_open, "fb_ret": fb_ret,
        "fb_bullish": fb_bullish, "dow": dow, "vix": vix, "bars": bars,
    }


def print_stats(trades, label):
    if not trades:
        print(f"  {label}: NO TRADES")
        return
    pnls = [t["opt_pnl"] for t in trades]
    total = sum(pnls)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / n * 100
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    cum = peak = dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    dates_span = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
                  datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
    tpy = n / (dates_span / 365.25) if dates_span > 0 else n
    sh = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if n > 1 and stdev(pnls) > 0 else 0
    calmar = total / dd if dd > 0 else 999
    avg_prem = mean([t["opt_premium"] for t in trades])
    avg_contr = mean([t["opt_contracts"] for t in trades])
    avg_hold = mean([t["opt_hold_mins"] for t in trades])

    print(f"\n  {label}")
    print(f"    Trades:       {n}")
    print(f"    Total P&L:    ${total:,.0f}")
    print(f"    Win Rate:     {wr:.1f}%  ({wins}W / {n - wins}L)")
    print(f"    Profit Factor:{pf:.2f}")
    print(f"    Max Drawdown: ${dd:,.0f}")
    print(f"    Sharpe:       {sh:.2f}")
    print(f"    Calmar:       {calmar:.2f}")
    print(f"    Avg Premium:  ${avg_prem:,.0f}")
    print(f"    Avg Contracts:{avg_contr:.0f}")
    print(f"    Avg Hold:     {avg_hold:.1f} min")


def main():
    ensure_cache()

    log_f = open(OUTPUT_REPORT, "w")
    tee = Tee(sys.stdout, log_f)
    old_stdout = sys.stdout
    sys.stdout = tee

    print("=" * 90)
    print("SPX Opening Print — All Edges Options Backtest (10s resolution)")
    print("=" * 90)

    # Load data
    print("\nLoading data...")
    intraday = load_intraday()
    spx_daily, spx_dates = load_daily(SPX_DAILY)
    vix_daily, _ = load_daily(VIX_DAILY)
    vix_1min = load_vix_1min()
    print(f"  SPX intraday: {len(intraday)} days")

    # Load existing trades for Edge D and F
    with open(SPX_TRADES_JSON, "r") as f:
        existing_trades = json.load(f)
    current_dates = set(t["date"] for t in existing_trades)
    print(f"  Current strategy: {len(current_dates)} dates")

    # Build features
    print("\nBuilding features...")
    days = {}
    for d in sorted(intraday.keys()):
        feat = build_features(d, intraday[d], spx_daily, spx_dates, vix_daily, vix_1min)
        if feat:
            days[d] = feat
    print(f"  {len(days)} tradeable days")

    # Process each edge
    all_edge_results = {}

    for edge_id in ["A", "B", "C", "D", "E", "F"]:
        edge = EDGES[edge_id]
        print(f"\n\n{'='*90}")
        print(f"EDGE {edge_id}: {edge['name']}")
        print(f"  {edge['desc']}")
        print(f"{'='*90}")

        # Filter days
        if edge["filter"] == "missed":
            eligible = {d: f for d, f in days.items()
                       if f["fb_bullish"] and d not in current_dates}
        elif edge["filter"] == "current":
            eligible = {d: f for d, f in days.items() if d in current_dates}
        else:
            eligible = {d: f for d, f in days.items() if edge["filter"](f)}

        print(f"  Eligible days: {len(eligible)}")

        trades = []
        skipped = 0
        api_calls = 0

        for i, (d, feat) in enumerate(sorted(eligible.items())):
            if (i + 1) % 100 == 0 or i == 0:
                print(f"  Processing {i+1}/{len(eligible)} ({d})...")

            entry_open = feat["entry_open"]
            vix = feat["vix"]

            # Risk sizing
            risk = BASE_RISK
            if vix and vix >= 25:
                risk = int(risk * 1.3)
            if abs(feat["fb_ret"]) > 0.20:
                risk = int(risk * 1.2)
            risk = max(MIN_RISK, min(MAX_RISK, risk))

            # Find option
            ticker, strike, expiry, dte = find_option(
                d, entry_open, edge["option_type"])
            if not ticker:
                skipped += 1
                continue

            # Get 10s entry price
            entry_10s = get_option_entry_price(ticker, d)

            # Get 1-min bars (for calendar charts)
            opt_1min = get_option_1min_bars(ticker, d)

            # Get 10s bars for simulation
            # I:SPX and SPX don't have 10s data on Polygon — use SPY as proxy
            spy_10s_raw = fetch_10s_bars("SPY", d, "spy10s")
            opt_10s = fetch_10s_bars(ticker, d, "opt10s")

            if not spy_10s_raw or not opt_10s:
                skipped += 1
                continue

            # Scale SPY bars to SPX-equivalent prices
            # Find SPY price near 9:31 to compute scale factor
            spy_ref = None
            for bar in spy_10s_raw:
                if bar["time"] >= "09:31:00":
                    spy_ref = bar["open"]
                    break
            if not spy_ref or spy_ref <= 0:
                skipped += 1
                continue
            scale = entry_open / spy_ref
            spx_10s = []
            for bar in spy_10s_raw:
                spx_10s.append({
                    "time": bar["time"],
                    "open": bar["open"] * scale,
                    "high": bar["high"] * scale,
                    "low": bar["low"] * scale,
                    "close": bar["close"] * scale,
                })

            opt_entry_price = entry_10s if entry_10s and entry_10s > 0.10 else None
            if not opt_entry_price:
                # Fallback to first opt_10s bar after 9:30:10
                for bar in opt_10s:
                    if bar["time"] >= "09:30:10":
                        opt_entry_price = bar["close"]
                        break
            if not opt_entry_price or opt_entry_price <= 0.10:
                skipped += 1
                continue

            # Simulate
            if edge["exit_mode"] == "trail":
                result = simulate_trailing_10s(
                    spx_10s, opt_10s, entry_open, opt_entry_price,
                    edge["sl_pts"], edge["trail_pct"], edge["ts_min"],
                    risk, edge["direction"])
            else:
                result = simulate_fixed_ptsl_10s(
                    spx_10s, opt_10s, entry_open, opt_entry_price,
                    edge["pt_pts"], edge["sl_pts"], edge["ts_min"],
                    risk, edge["direction"])

            if not result:
                skipped += 1
                continue

            trades.append({
                "date": d,
                "day_of_week": feat["dow"],
                "edge": edge_id,
                "edge_name": edge["name"],
                "entry_open": entry_open,
                "fb_ret": round(feat["fb_ret"], 4),
                "vix": vix,
                "strike": strike,
                "option_ticker": ticker,
                "option_type": edge["option_type"],
                "dte": dte,
                "risk": risk,
                "opt_entry_price": result["opt_entry_price"],
                "opt_exit_price": result["opt_exit_price"],
                "opt_contracts": result["num_contracts"],
                "opt_premium": result["total_premium"],
                "opt_pnl": result["pnl_dollars"],
                "opt_exit_reason": result["exit_reason"],
                "opt_exit_time": result["exit_time"],
                "opt_hold_mins": result["hold_mins"],
            })

        print(f"\n  Completed: {len(trades)} trades, {skipped} skipped")
        print_stats(trades, f"EDGE {edge_id}: {edge['name']}")

        # Save edge trades
        output_file = os.path.join(OUTPUT_DIR, f"edge_{edge_id}_trades.json")
        with open(output_file, "w") as f:
            json.dump(trades, f, indent=2)
        print(f"  Saved to {output_file}")

        all_edge_results[edge_id] = trades

    # ── Summary Comparison ────────────────────────────────────────────
    print(f"\n\n{'='*90}")
    print("SUMMARY COMPARISON")
    print(f"{'='*90}")
    print(f"\n  {'Edge':6s} {'Name':25s} {'N':>5s} {'P&L':>12s} {'WR':>6s} {'PF':>6s} {'MaxDD':>10s} {'Sharpe':>7s} {'AvgHold':>8s}")

    for edge_id in ["A", "B", "C", "D", "E", "F"]:
        trades = all_edge_results[edge_id]
        if not trades:
            print(f"  {edge_id:6s} {EDGES[edge_id]['name']:25s} {'N/A':>5s}")
            continue
        pnls = [t["opt_pnl"] for t in trades]
        total = sum(pnls)
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100
        gw = sum(p for p in pnls if p > 0)
        gl = sum(abs(p) for p in pnls if p <= 0)
        pf = gw / gl if gl > 0 else 999
        cum = peak = dd = 0
        for p in pnls:
            cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        ds = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
              datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
        tpy = n / (ds / 365.25) if ds > 0 else n
        sh = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if n > 1 and stdev(pnls) > 0 else 0
        avg_hold = mean([t["opt_hold_mins"] for t in trades])
        print(f"  {edge_id:6s} {EDGES[edge_id]['name']:25s} {n:>5d} ${total:>11,.0f} {wr:>5.1f}% {pf:>5.2f} ${dd:>9,.0f} {sh:>7.2f} {avg_hold:>6.1f}m")

    # Combined portfolio
    print(f"\n  COMBINED PORTFOLIO IDEAS:")
    for combo_name, combo_edges in [
        ("B+Existing (Scalp + Hold)", ["B"]),
        ("B+C (Scalp + Bear Short)", ["B", "C"]),
        ("A+C (Full Scalp + Bear)", ["A", "C"]),
        ("B+D (Filtered Scalp + Missed)", ["B", "D"]),
        ("A+C+D (Everything)", ["A", "C", "D"]),
    ]:
        combo_trades = []
        for eid in combo_edges:
            combo_trades.extend(all_edge_results[eid])
        combo_trades.sort(key=lambda t: t["date"])
        if not combo_trades:
            continue
        pnls = [t["opt_pnl"] for t in combo_trades]
        total = sum(pnls)
        n = len(pnls)
        cum = peak = dd = 0
        for p in pnls:
            cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        ds = (datetime.strptime(combo_trades[-1]["date"], "%Y-%m-%d") -
              datetime.strptime(combo_trades[0]["date"], "%Y-%m-%d")).days
        tpy = n / (ds / 365.25) if ds > 0 else n
        sh = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if n > 1 and stdev(pnls) > 0 else 0
        print(f"    {combo_name:40s} N={n:>5d}  P&L=${total:>11,.0f}  MaxDD=${dd:>9,.0f}  Sharpe={sh:>6.2f}")

    print(f"\n\n{'='*90}")
    print("COMPLETE")
    print(f"{'='*90}")

    sys.stdout = old_stdout
    log_f.close()
    print(f"\nDone! Report: {OUTPUT_REPORT}")
    print(f"Trade files in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
