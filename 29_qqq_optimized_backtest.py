"""
QQQ Opening Print — Script 29: Optimized Options Backtest
============================================================
Key findings from Script 28:
1. Big bullish first bar (>0.10%) is the strongest signal (Sharpe 1.177, 389 trades)
2. Trailing stop 0.05% + SL 0.10% is best exit (Sharpe 0.960, 91.8% WR)
3. Entry MUST be at the open (bar 0) — edge vanishes by bar 1
4. VIX >= 18 and gap down boost Sharpe further

Strategy:
- Entry: Buy ATM QQQ 0DTE call at 9:30:10 (10s bars)
- Signal: Bullish first bar with return > threshold
- Exit: Trailing stop on QQQ underlying, or SL, or time stop
- Filters: Various combos tested

Tests multiple configurations through the options pipeline.
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QQQ_1MIN = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")
QQQ_DAILY = os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "qqq_optimized_report.txt")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "qqq_optimized_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

MIN_RISK = 50000
MAX_RISK = 200000

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

# ── Data loading ─────────────────────────────────────────────────────

def load_daily_csv(filepath):
    data = {}
    dates = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row["date"]
            data[d] = {k: float(row[k]) for k in ["open", "high", "low", "close"]}
            dates.append(d)
    return data, sorted(set(dates))

def load_intraday_csv(filepath):
    data = defaultdict(list)
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["date"]].append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    return data

def compute_ma(daily, dates, target, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target: idx = i; break
    if idx is None or idx < period: return None
    vals = [daily[dates[j]]["close"] for j in range(idx - period, idx) if dates[j] in daily]
    return mean(vals) if len(vals) == period else None

def compute_rsi(daily, dates, target, period=14):
    idx = None
    for i, d in enumerate(dates):
        if d == target: idx = i; break
    if idx is None or idx < period + 1: return None
    gains, losses = [], []
    for j in range(idx - period, idx):
        if j < 1 or dates[j] not in daily or dates[j-1] not in daily: continue
        change = daily[dates[j]]["close"] - daily[dates[j-1]]["close"]
        gains.append(max(change, 0)); losses.append(max(-change, 0))
    if not gains: return None
    ag = sum(gains)/len(gains); al = sum(losses)/len(losses)
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

# ── Cache / API helpers ──────────────────────────────────────────────

def ensure_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)

def cache_path(key):
    return os.path.join(CACHE_DIR, key + ".json")

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
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif e.code == 404:
                return None
            else:
                time.sleep(2)
        except Exception as e:
            time.sleep(2)
    return None

def find_nearest_expiry_qqq(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt >= datetime(2022, 11, 1):
        return date_str, 0
    dow = dt.weekday()
    if dow in (0, 2, 4):
        return date_str, 0
    elif dow == 1:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    elif dow == 3:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    return date_str, 0

def find_qqq_option_call(date_str, target_strike):
    cache_key = f"qqq_contracts_v1_{date_str}_{round(target_strike)}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None, None, None
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]

    expiry_date, dte = find_nearest_expiry_qqq(date_str)
    strike = round(target_strike)
    date_code = datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%y%m%d")

    for offset in [0, 1, -1, 2, -2, 3, -3]:
        test_strike = strike + offset
        strike_code = f"{int(test_strike * 1000):08d}"
        ticker = f"O:QQQ{date_code}C{strike_code}"
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
    cache_key = f"entry10s_{date_str}_{ticker.replace(':', '_').replace('/', '_')}"
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

    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    offset_hours = 4 if is_dst else 5

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

def get_option_bars(ticker, date_str):
    cache_key = f"bars_{date_str}_{ticker.replace(':', '_').replace('/', '_')}"
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

    bars = []
    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    offset_hours = 4 if is_dst else 5

    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        t_str = dt_et.strftime("%H:%M")
        if "09:30" <= t_str <= "16:00":
            bars.append({
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"], "volume": r.get("v", 0)
            })
    if bars:
        save_cache(cache_key, bars)
    else:
        save_cache(cache_key, "none")
    return bars


# ── Options trade simulation with trailing stop ──────────────────────

def simulate_options_trade_trailing(option_bars, qqq_bars, entry_open, sl_pct, trail_pct,
                                     ts_bars, risk, entry_10s_price=None):
    """Simulate options trade using trailing stop on QQQ underlying."""
    if not option_bars or len(option_bars) < 2:
        return None

    if entry_10s_price and entry_10s_price > 0.10:
        option_entry_price = entry_10s_price
    else:
        entry_bar = None
        for bar in option_bars:
            if bar["time"] == "09:31":
                entry_bar = bar; break
        if not entry_bar:
            entry_bar = option_bars[0]
        option_entry_price = entry_bar["close"]
    if option_entry_price <= 0.10:
        return None

    contract_cost = option_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    option_price_map = {bar["time"]: bar for bar in option_bars}

    # Exit logic: trailing stop on QQQ underlying
    sl = entry_open * sl_pct / 100
    peak_price = entry_open

    exit_time = exit_reason = None
    hold_mins = 0
    started = False
    bars_held = 0

    for bar in qqq_bars:
        if bar["time"] <= "09:31":
            if bar["time"] == "09:31":
                started = True
            continue
        if not started:
            started = True

        bars_held += 1
        if bars_held > ts_bars:
            break

        # Update trailing peak
        if bar["high"] > peak_price:
            peak_price = bar["high"]

        # Check hard stop loss from entry
        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss"
            hold_mins = bars_held
            break

        # Check trailing stop
        trail_level = peak_price * (1 - trail_pct / 100)
        if bar["low"] <= trail_level and peak_price > entry_open:
            exit_time = bar["time"]
            exit_reason = "Trailing Stop"
            hold_mins = bars_held
            break

    if exit_time is None:
        ts_idx = min(len(qqq_bars) - 1, ts_bars + 1)
        exit_time = qqq_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    # Get option exit price
    exit_option_price = None
    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        for bar in option_bars:
            if bar["time"] >= exit_time:
                exit_option_price = bar["close"]; break
        if exit_option_price is None:
            exit_option_price = option_bars[-1]["close"]
            exit_time = option_bars[-1]["time"]

    pnl = num_contracts * (exit_option_price - option_entry_price) * 100

    return {
        "option_entry_price": round(option_entry_price, 2),
        "option_exit_price": round(exit_option_price, 2),
        "num_contracts": num_contracts,
        "total_premium": round(total_premium, 2),
        "pnl_dollars": round(pnl, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time,
        "hold_mins": hold_mins,
        "peak_price": round(peak_price, 2),
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    _report_file = open(OUTPUT_REPORT, "w")
    sys.stdout = Tee(sys.__stdout__, _report_file)

    ensure_cache()
    print("Loading data...")
    qqq_daily, qqq_dates = load_daily_csv(QQQ_DAILY)
    qqq_intraday = load_intraday_csv(QQQ_1MIN)
    vix_daily, _ = load_daily_csv(VIX_DAILY)

    intra_dates = sorted(qqq_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}
    print(f"  QQQ intraday days: {len(intra_dates)}")

    # ── Build enriched day data ──
    print("Building day data...")
    all_days = []
    for d in intra_dates:
        bars = qqq_intraday[d]
        if len(bars) < 30: continue
        dt = datetime.strptime(d, "%Y-%m-%d")
        entry = bars[0]["open"]
        fb_ret = (bars[0]["close"] - bars[0]["open"]) / bars[0]["open"] * 100
        bullish = bars[0]["close"] > bars[0]["open"]

        day = {
            "date": d, "dow": dt.strftime("%A"), "entry": entry, "bars": bars,
            "bullish": bullish, "fb_ret": fb_ret,
            "vix": vix_daily[d]["open"] if d in vix_daily else None,
        }

        idx = intra_idx[d]
        if idx > 0:
            prev_d = intra_dates[idx - 1]
            prev_bars = qqq_intraday.get(prev_d, [])
            if prev_bars and len(prev_bars) > 1:
                prev_close = prev_bars[-1]["close"]
                day["gap_pct"] = (entry - prev_close) / prev_close * 100
                prev_open = prev_bars[0]["open"]
                day["prev_ret"] = (prev_close - prev_open) / prev_open * 100

        ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
        day["above_ma50"] = entry > ma50 if ma50 else None
        rsi = compute_rsi(qqq_daily, qqq_dates, d, 14)
        day["rsi"] = rsi

        all_days.append(day)

    print(f"  Enriched days: {len(all_days)}")

    # ── Define configurations to test ──
    configs = [
        {
            "name": "A: Big bar >0.10% + Trail 0.05% + SL 0.10% TS 30",
            "fb_min": 0.10, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {},
        },
        {
            "name": "B: Big bar >0.10% + Trail 0.05% + SL 0.10% TS 60",
            "fb_min": 0.10, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 60,
            "filters": {},
        },
        {
            "name": "C: Big bar >0.10% + Trail 0.10% + SL 0.10% TS 30",
            "fb_min": 0.10, "trail_pct": 0.10, "sl_pct": 0.10, "ts": 30,
            "filters": {},
        },
        {
            "name": "D: Big bar >0.10% + Trail 0.10% + SL 0.10% TS 60",
            "fb_min": 0.10, "trail_pct": 0.10, "sl_pct": 0.10, "ts": 60,
            "filters": {},
        },
        {
            "name": "E: Big bar >0.05% + Trail 0.05% + SL 0.10% TS 30",
            "fb_min": 0.05, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {},
        },
        {
            "name": "F: Big bar >0.10% + Trail 0.05% + SL 0.10% TS 30 + VIX>=16",
            "fb_min": 0.10, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {"vix_min": 16},
        },
        {
            "name": "G: Big bar >0.10% + Trail 0.05% + SL 0.10% TS 30 + VIX>=18",
            "fb_min": 0.10, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {"vix_min": 18},
        },
        {
            "name": "H: All bullish + Trail 0.05% + SL 0.10% TS 30 (no fb filter)",
            "fb_min": 0.0, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {},
        },
        {
            "name": "I: Big bar >0.10% + PT 0.15% + SL 0.05% TS 15 (best grid)",
            "fb_min": 0.10, "trail_pct": None, "pt_pct": 0.15, "sl_pct": 0.05, "ts": 15,
            "filters": {},
        },
        {
            "name": "J: Big bar >0.10% + PT 0.20% + SL 0.10% TS 30 (original best)",
            "fb_min": 0.10, "trail_pct": None, "pt_pct": 0.20, "sl_pct": 0.10, "ts": 30,
            "filters": {},
        },
        {
            "name": "K: Big bar >0.05% + Trail 0.05% + SL 0.10% TS 30 + VIX>=16",
            "fb_min": 0.05, "trail_pct": 0.05, "sl_pct": 0.10, "ts": 30,
            "filters": {"vix_min": 16},
        },
        {
            "name": "L: Big bar >0.10% + Trail 0.05% + SL 0.15% TS 30",
            "fb_min": 0.10, "trail_pct": 0.05, "sl_pct": 0.15, "ts": 30,
            "filters": {},
        },
    ]

    # ── Run each config through options backtest ──
    print(f"\n{'='*90}")
    print(f"TESTING {len(configs)} CONFIGURATIONS THROUGH OPTIONS BACKTEST")
    print(f"{'='*90}")

    all_config_results = []

    for cfg_idx, cfg in enumerate(configs):
        name = cfg["name"]
        fb_min = cfg["fb_min"]
        trail_pct = cfg.get("trail_pct")
        pt_pct = cfg.get("pt_pct")
        sl_pct = cfg["sl_pct"]
        ts = cfg["ts"]
        filters = cfg.get("filters", {})

        print(f"\n  --- Config {cfg_idx+1}/{len(configs)}: {name} ---")

        # Filter days
        filtered = []
        for day in all_days:
            if not day["bullish"]: continue
            if day["fb_ret"] < fb_min: continue
            vix = day.get("vix")
            if "vix_min" in filters and (not vix or vix < filters["vix_min"]): continue
            if "vix_max" in filters and (not vix or vix > filters["vix_max"]): continue
            if "no_tuesday" in filters and day["dow"] == "Tuesday": continue
            filtered.append(day)

        print(f"  Filtered days: {len(filtered)}")

        # Run options backtest
        options_trades = []
        skipped = 0
        for i, day in enumerate(filtered):
            d = day["date"]
            entry = day["entry"]

            if (i + 1) % 50 == 0 or i == 0:
                print(f"    Processing {i+1}/{len(filtered)}: {d}...")

            # Risk sizing
            risk = 75000  # base
            vix = day.get("vix")
            if vix and vix >= 25: risk = min(risk * 1.3, MAX_RISK)
            if day["fb_ret"] > 0.20: risk = min(risk * 1.2, MAX_RISK)
            risk = max(MIN_RISK, round(risk / 1000) * 1000)

            target_strike = round(entry)
            ticker, strike, expiry_date, dte = find_qqq_option_call(d, target_strike)
            if not ticker:
                skipped += 1; continue

            entry_10s = get_option_entry_price(ticker, d)
            opt_bars = get_option_bars(ticker, d)
            if not opt_bars:
                skipped += 1; continue

            qqq_bars = day["bars"]

            if trail_pct is not None:
                # Trailing stop mode
                opt_result = simulate_options_trade_trailing(
                    opt_bars, qqq_bars, entry, sl_pct, trail_pct, ts,
                    risk, entry_10s_price=entry_10s
                )
            else:
                # Fixed PT/SL mode
                opt_result = simulate_options_trade_fixed(
                    opt_bars, qqq_bars, entry, pt_pct, sl_pct, ts,
                    risk, entry_10s_price=entry_10s
                )

            if not opt_result:
                skipped += 1; continue

            options_trades.append({
                "date": d,
                "day_of_week": day["dow"],
                "entry_open": entry,
                "fb_ret": round(day["fb_ret"], 4),
                "vix": vix,
                "strike": strike,
                "option_ticker": ticker,
                "dte": dte,
                "risk": risk,
                "opt_entry_price": opt_result["option_entry_price"],
                "opt_exit_price": opt_result["option_exit_price"],
                "opt_contracts": opt_result["num_contracts"],
                "opt_premium": opt_result["total_premium"],
                "opt_pnl": opt_result["pnl_dollars"],
                "opt_exit_reason": opt_result["exit_reason"],
                "opt_exit_time": opt_result["exit_time"],
                "opt_hold_mins": opt_result["hold_mins"],
                "used_10s_entry": entry_10s is not None and entry_10s > 0.10,
            })

        if len(options_trades) < 10:
            print(f"    Only {len(options_trades)} trades — skipping")
            all_config_results.append((name, None, None))
            continue

        # Compute stats
        pnls = [t["opt_pnl"] for t in options_trades]
        total_pnl = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        gw = sum(p for p in pnls if p > 0)
        gl = sum(abs(p) for p in pnls if p <= 0)
        pf = gw / gl if gl > 0 else 999

        cum = peak = max_dd = 0
        for p in pnls:
            cum += p; peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        days_span = (datetime.strptime(options_trades[-1]["date"], "%Y-%m-%d") -
                     datetime.strptime(options_trades[0]["date"], "%Y-%m-%d")).days
        tpy = len(pnls) / (days_span / 365.25) if days_span > 0 else len(pnls)
        sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if len(pnls) > 1 and stdev(pnls) > 0 else 0
        calmar = total_pnl / max_dd if max_dd > 0 else 999

        result = {
            "name": name, "n": len(options_trades), "total_pnl": total_pnl,
            "wr": wr, "pf": pf, "max_dd": max_dd, "sharpe": sharpe,
            "calmar": calmar, "skipped": skipped,
            "avg_prem": mean([t["opt_premium"] for t in options_trades]),
        }

        print(f"\n    RESULTS: {name}")
        print(f"    Trades: {len(options_trades)} (skipped {skipped})")
        print(f"    Total P&L: ${total_pnl:,.0f}  |  Avg: ${mean(pnls):,.0f}")
        print(f"    WR: {wr:.1f}%  |  PF: {pf:.2f}")
        print(f"    Max DD: ${max_dd:,.0f}")
        print(f"    Sharpe: {sharpe:.2f}  |  Calmar: {calmar:.2f}")

        all_config_results.append((name, result, options_trades))

    # ── Summary comparison ──
    print(f"\n\n{'='*90}")
    print(f"CONFIGURATION COMPARISON")
    print(f"{'='*90}")
    print(f"  {'Config':65s} {'N':>4s} {'P&L':>12s} {'WR':>5s} {'PF':>5s} {'MaxDD':>9s} {'Sharpe':>7s} {'Calmar':>7s}")
    print("  " + "-" * 120)

    valid_results = [(name, r, trades) for name, r, trades in all_config_results if r]
    valid_results.sort(key=lambda x: x[1]["sharpe"], reverse=True)

    for name, r, _ in valid_results:
        print(f"  {r['name']:65s} {r['n']:>4d} ${r['total_pnl']:>10,.0f} {r['wr']:>5.1f} {r['pf']:>5.2f} "
              f"${r['max_dd']:>8,.0f} {r['sharpe']:>7.2f} {r['calmar']:>7.2f}")

    # SPX comparison
    print(f"\n  {'SPX Momentum (baseline)':65s} {'331':>4s} {'$6,110,000':>12s} {'50.8':>5s} {'2.16':>5s} "
          f"{'$319,000':>9s} {'1.41':>7s} {'19.15':>7s}")

    # ── Save best config ──
    if valid_results:
        best_name, best_r, best_trades = valid_results[0]
        print(f"\n\n{'='*90}")
        print(f"BEST CONFIG: {best_name}")
        print(f"{'='*90}")
        print(f"  Trades: {best_r['n']}")
        print(f"  Total P&L: ${best_r['total_pnl']:,.0f}")
        print(f"  Win Rate: {best_r['wr']:.1f}%")
        print(f"  Profit Factor: {best_r['pf']:.2f}")
        print(f"  Max Drawdown: ${best_r['max_dd']:,.0f}")
        print(f"  Sharpe: {best_r['sharpe']:.2f}")
        print(f"  Calmar: {best_r['calmar']:.2f}")
        print(f"  Avg Premium: ${best_r['avg_prem']:,.0f}")

        # Yearly
        print(f"\n  YEARLY BREAKDOWN:")
        yearly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
        for t in best_trades:
            yr = t["date"][:4]
            yearly[yr]["pnl"] += t["opt_pnl"]
            yearly[yr]["count"] += 1
            if t["opt_pnl"] > 0: yearly[yr]["wins"] += 1
        cum = 0
        for yr in sorted(yearly.keys()):
            y = yearly[yr]
            cum += y["pnl"]
            ywr = y["wins"] / y["count"] * 100 if y["count"] else 0
            print(f"    {yr}: {y['count']:>3} trades, P&L ${y['pnl']:>10,.0f}, Cum ${cum:>12,.0f}, WR {ywr:.0f}%")

        # By exit reason
        print(f"\n  BY EXIT REASON:")
        by_reason = defaultdict(list)
        for t in best_trades:
            by_reason[t["opt_exit_reason"]].append(t["opt_pnl"])
        for reason in sorted(by_reason.keys()):
            rp = by_reason[reason]
            print(f"    {reason:25s}: {len(rp):>3} trades, avg ${mean(rp):>10,.0f}, "
                  f"WR {sum(1 for p in rp if p > 0)/len(rp)*100:.0f}%")

        # Save trades
        with open(OUTPUT_JSON, "w") as f:
            json.dump(best_trades, f, indent=2, default=str)
        print(f"\n  Trade data saved: {OUTPUT_JSON}")

    print(f"\nReport saved to: {OUTPUT_REPORT}")
    _report_file.close()
    sys.stdout = sys.__stdout__


# ── Fixed PT/SL simulation (for comparison configs) ──────────────────

def simulate_options_trade_fixed(option_bars, qqq_bars, entry_open, pt_pct, sl_pct,
                                  ts_bars, risk, entry_10s_price=None):
    """Same as trailing but with fixed PT/SL on QQQ."""
    if not option_bars or len(option_bars) < 2:
        return None

    if entry_10s_price and entry_10s_price > 0.10:
        option_entry_price = entry_10s_price
    else:
        entry_bar = None
        for bar in option_bars:
            if bar["time"] == "09:31":
                entry_bar = bar; break
        if not entry_bar:
            entry_bar = option_bars[0]
        option_entry_price = entry_bar["close"]
    if option_entry_price <= 0.10:
        return None

    contract_cost = option_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    option_price_map = {bar["time"]: bar for bar in option_bars}

    pt = entry_open * pt_pct / 100
    sl = entry_open * sl_pct / 100

    exit_time = exit_reason = None
    hold_mins = 0
    started = False
    bars_held = 0

    for bar in qqq_bars:
        if bar["time"] <= "09:31":
            if bar["time"] == "09:31": started = True
            continue
        if not started: started = True

        bars_held += 1
        if bars_held > ts_bars: break

        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss"
            hold_mins = bars_held
            break
        if bar["high"] >= entry_open + pt:
            exit_time = bar["time"]
            exit_reason = "Profit Target"
            hold_mins = bars_held
            break

    if exit_time is None:
        ts_idx = min(len(qqq_bars) - 1, ts_bars + 1)
        exit_time = qqq_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    exit_option_price = None
    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        for bar in option_bars:
            if bar["time"] >= exit_time:
                exit_option_price = bar["close"]; break
        if exit_option_price is None:
            exit_option_price = option_bars[-1]["close"]
            exit_time = option_bars[-1]["time"]

    pnl = num_contracts * (exit_option_price - option_entry_price) * 100

    return {
        "option_entry_price": round(option_entry_price, 2),
        "option_exit_price": round(exit_option_price, 2),
        "num_contracts": num_contracts,
        "total_premium": round(total_premium, 2),
        "pnl_dollars": round(pnl, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time,
        "hold_mins": hold_mins,
        "peak_price": entry_open,
    }


if __name__ == "__main__":
    main()
