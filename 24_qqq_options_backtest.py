"""
QQQ Opening Print Strategy — Options Backtest
===============================================
Same strategy as SPX (script 12) but adapted for QQQ options.

Key differences from SPX:
- QQQ options ticker: O:QQQ{YYMMDD}C{STRIKE*1000:08d}
- QQQ strikes in $1 increments (vs $5 for SPX)
- QQQ 0DTE: daily since ~Nov 2022, M/W/F before that
- PT/SL in percentage terms scaled from SPX values
- Risk sizing uses same bracket approach

Usage:
    python3 24_qqq_options_backtest.py
"""

import os, csv, json, time, math, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QQQ_1MIN = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")
QQQ_DAILY = os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "qqq_options_report.txt")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "qqq_options_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"

MIN_RISK = 25000
MAX_LOSS_TARGET = 150000

def get_max_premium(score):
    if score < 25:   return 150000
    elif score < 40: return 155000
    elif score < 55: return 170000
    elif score < 70: return 175000
    elif score < 85: return 200000
    else:            return 200000

HYBRID_THRESHOLD = 25
VIX_FILTER = 16
VIX_CAP = 30
BULLISH_ONLY = True
SMA50_FILTER = True
RSI_FILTER = (35, 70)

REQUEST_DELAY = 0.05

# ── Cache helpers ────────────────────────────────────────────────────

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

# ── API helpers ──────────────────────────────────────────────────────

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
                print(f"    HTTP {e.code} on attempt {attempt+1}")
                time.sleep(2)
        except Exception as e:
            print(f"    Error: {e} on attempt {attempt+1}")
            time.sleep(2)
    return None

# ── QQQ Option helpers ───────────────────────────────────────────────

def find_nearest_expiry_qqq(date_str):
    """Find nearest QQQ option expiry.
    After Nov 2022: daily 0DTE on all trading days.
    Before that: M/W/F only."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    daily_0dte_start = datetime(2022, 11, 1)  # QQQ got daily 0DTE ~Nov 2022

    if dt >= daily_0dte_start:
        return date_str, 0

    dow = dt.weekday()
    if dow in (0, 2, 4):  # Mon, Wed, Fri
        return date_str, 0
    elif dow == 1:  # Tuesday -> Wednesday
        expiry = dt + timedelta(days=1)
        return expiry.strftime("%Y-%m-%d"), 1
    elif dow == 3:  # Thursday -> Friday
        expiry = dt + timedelta(days=1)
        return expiry.strftime("%Y-%m-%d"), 1
    else:
        return date_str, 0


def find_qqq_option_call(date_str, target_strike):
    """Find ATM QQQ call option.
    QQQ options: O:QQQ{YYMMDD}C{strike*1000:08d}
    Strikes in $1 increments."""
    cache_key = f"qqq_contracts_v1_{date_str}_{round(target_strike)}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None, None, None
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]

    expiry_date, dte = find_nearest_expiry_qqq(date_str)

    strike = round(target_strike)  # $1 increments for QQQ
    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    date_code = exp_dt.strftime("%y%m%d")

    # Try strikes near ATM
    strike_offsets = [0, 1, -1, 2, -2, 3, -3]

    for offset in strike_offsets:
        test_strike = strike + offset
        strike_code = f"{int(test_strike * 1000):08d}"
        ticker = f"O:QQQ{date_code}C{strike_code}"

        url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
               f"/range/1/minute/{date_str}/{date_str}"
               f"?adjusted=true&sort=asc&limit=3"
               f"&apiKey={API_KEY}")

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
    """Get option price at ~9:30:10 using 10-second bars."""
    cache_key = f"entry10s_{date_str}_{ticker.replace(':', '_').replace('/', '_')}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else None

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/10/second/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=100"
           f"&apiKey={API_KEY}")

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
    """Get 1-min bars for a QQQ option."""
    cache_key = f"bars_{date_str}_{ticker.replace(':', '_').replace('/', '_')}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else []

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
           f"/range/1/minute/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=5000"
           f"&apiKey={API_KEY}")

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
                "time": t_str,
                "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"],
                "volume": r.get("v", 0)
            })

    if bars:
        save_cache(cache_key, bars)
    else:
        save_cache(cache_key, "none")
    return bars


# ── Simulate options trade ───────────────────────────────────────────

def simulate_options_trade(option_bars, qqq_bars, entry_open, pt, sl, ts,
                            score, first_bar_bullish, risk, entry_10s_price=None):
    """Same logic as SPX version, adapted for QQQ."""
    if not option_bars or len(option_bars) < 2:
        return None

    if entry_10s_price and entry_10s_price > 0.10:
        option_entry_price = entry_10s_price
    else:
        entry_bar = None
        for bar in option_bars:
            if bar["time"] == "09:31":
                entry_bar = bar
                break
        if not entry_bar:
            entry_bar = option_bars[0]
        option_entry_price = entry_bar["close"]
    if option_entry_price <= 0.10:
        return None

    contract_cost = option_entry_price * 100
    num_contracts = int(risk / contract_cost)
    if num_contracts < 1:
        num_contracts = 1
    total_premium = num_contracts * contract_cost

    option_price_map = {}
    for bar in option_bars:
        option_price_map[bar["time"]] = bar

    if score >= HYBRID_THRESHOLD:
        pass
    else:
        if not first_bar_bullish:
            return None

    exit_option_price = None
    exit_time = None
    exit_reason = None
    hold_mins = 0

    spx_bar_map = {}
    for bar in qqq_bars:
        spx_bar_map[bar["time"]] = bar

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
        if bars_held > ts:
            break

        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss (QQQ)"
            hold_mins = bars_held
            break

        if bar["high"] >= entry_open + pt:
            exit_time = bar["time"]
            exit_reason = "Profit Target (QQQ)"
            hold_mins = bars_held
            break

    if exit_time is None:
        ts_target_idx = min(len(qqq_bars) - 1, ts + 1)
        exit_time = qqq_bars[ts_target_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_target_idx

    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        for bar in option_bars:
            if bar["time"] >= exit_time:
                exit_option_price = bar["close"]
                break
        if exit_option_price is None:
            exit_option_price = option_bars[-1]["close"]
            exit_time = option_bars[-1]["time"]

    pnl_per_contract = (exit_option_price - option_entry_price) * 100
    total_pnl = num_contracts * pnl_per_contract

    return {
        "option_entry_price": round(option_entry_price, 2),
        "option_exit_price": round(exit_option_price, 2),
        "num_contracts": num_contracts,
        "total_premium": round(total_premium, 2),
        "pnl_dollars": round(total_pnl, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time,
        "hold_mins": hold_mins,
        "entry_time": "09:31",
    }


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
        if d == target:
            idx = i; break
    if idx is None or idx < period:
        return None
    vals = []
    for j in range(idx - period, idx):
        if dates[j] in daily:
            vals.append(daily[dates[j]]["close"])
    return mean(vals) if len(vals) == period else None

def compute_rsi(daily, dates, target, period=14):
    idx = None
    for i, d in enumerate(dates):
        if d == target:
            idx = i; break
    if idx is None or idx < period + 1:
        return None
    gains, losses = [], []
    for j in range(idx - period, idx):
        if j < 1 or dates[j] not in daily or dates[j-1] not in daily:
            continue
        change = daily[dates[j]]["close"] - daily[dates[j-1]]["close"]
        if change > 0:
            gains.append(change); losses.append(0)
        else:
            gains.append(0); losses.append(abs(change))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ── Signal evaluation (adapted for QQQ) ─────────────────────────────

def evaluate_signals(d, bars, intra_dates, intra_idx, qqq_intraday,
                     vix_daily, qqq_daily, qqq_dates, tlt_daily, tlt_dates):
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
    if idx > 0:
        prev_d = intra_dates[idx - 1]
        prev_bars = qqq_intraday.get(prev_d, [])
        if prev_bars:
            prev_close = prev_bars[-1]["close"]
            gap_pct = (entry_open - prev_close) / prev_close * 100
            gap_dir = "up" if gap_pct > 0 else "down"
            if gap_dir == "up":
                signals.append(f"Gap up ({gap_pct:+.2f}%)"); score += 10
            elif gap_dir == "down" and abs(gap_pct) > 0.5:
                signals.append(f"Large gap down ({gap_pct:+.2f}%)"); score -= 15
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
        sb = qqq_intraday.get(sd, [])
        if sb and len(sb) >= 10:
            day_ret = sb[-1]["close"] - sb[0]["open"]
            if streak == 0: streak = 1 if day_ret > 0 else -1
            elif streak > 0 and day_ret > 0: streak += 1
            elif streak < 0 and day_ret < 0: streak -= 1
            else: break
    if streak <= -3:
        signals.append(f"3+ down day streak ({streak})"); score += 25

    if d in vix_daily:
        vix_open = vix_daily[d]["open"]
        signals.append(f"VIX at open: {vix_open:.1f}")
        if 20 <= vix_open < 25: signals.append("VIX elevated (20-25)"); score += 15
        elif 25 <= vix_open < 30: signals.append("VIX high (25-30)"); score += 10
        elif vix_open >= 30: signals.append("VIX very high (>30)"); score += 5
        vix_dates_sorted = sorted(vix_daily.keys())
        vix_idx = None
        for vi, vd in enumerate(vix_dates_sorted):
            if vd == d: vix_idx = vi; break
        if vix_idx and vix_idx > 0:
            prev_vix_close = vix_daily[vix_dates_sorted[vix_idx - 1]]["close"]
            vix_chg_pct = (vix_open - prev_vix_close) / prev_vix_close * 100
            if -5 < vix_chg_pct < -1:
                signals.append(f"Vol falling ({vix_chg_pct:+.1f}%)"); score += 20

    qqq_date_set = set(qqq_dates)
    if d in qqq_date_set:
        ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
        ma200 = compute_ma(qqq_daily, qqq_dates, d, 200)
        ma10 = compute_ma(qqq_daily, qqq_dates, d, 10)
        ma20 = compute_ma(qqq_daily, qqq_dates, d, 20)
        above_all = all(entry_open > ma for ma in [ma10, ma20, ma50, ma200] if ma)
        below_all = all(entry_open < ma for ma in [ma10, ma20, ma50, ma200] if ma)
        if ma50:
            pct_from_50 = (entry_open - ma50) / ma50 * 100
            if -2 < pct_from_50 < 0: signals.append("Just below 50d MA"); score += 15
            elif pct_from_50 < -2: signals.append("Below 50d MA"); score += 10
            elif pct_from_50 > 5: signals.append("Far above 50d MA"); score -= 10
        if not above_all and not below_all:
            signals.append("Mixed MAs"); score += 8

    if d in qqq_date_set:
        wd = dt.weekday()
        if wd > 0:
            mon_date = (dt - timedelta(days=wd)).strftime("%Y-%m-%d")
            if mon_date in qqq_daily:
                wtd_ret = (entry_open - qqq_daily[mon_date]["open"]) / qqq_daily[mon_date]["open"] * 100
                if wtd_ret < -1: signals.append("Deep red week"); score += 15
                elif wtd_ret < 0: signals.append("Red week"); score += 5
        month_start = dt.replace(day=1).strftime("%Y-%m-%d")
        for sd in qqq_dates:
            if sd >= month_start and sd[:7] == d[:7] and sd in qqq_daily:
                mtd_ret = (entry_open - qqq_daily[sd]["open"]) / qqq_daily[sd]["open"] * 100
                if mtd_ret < -1: signals.append("Red month"); score += 10
                break

    tlt_idx_map = {td: ti for ti, td in enumerate(tlt_dates)}
    if d in tlt_idx_map:
        tidx = tlt_idx_map[d]
        if tidx >= 5:
            t5 = tlt_daily.get(tlt_dates[tidx - 5])
            tp = tlt_daily.get(tlt_dates[tidx - 1])
            if t5 and tp:
                tlt_ret = (tp["close"] - t5["close"]) / t5["close"] * 100
                if 0 < tlt_ret < 1: signals.append("Bonds mildly up 5d"); score += 8

    # PT/SL in percentage terms -> QQQ points
    # SPX defaults: PT=50/5800=0.86%, SL=10/5800=0.17%
    signal_set = set(s.split(" (")[0] for s in signals)
    pt_pct, sl_pct, ts = 0.86, 0.17, 240
    if "3+ down day streak" in signal_set: pt_pct, sl_pct, ts = 0.86, 0.34, 240
    elif "Vol falling" in signal_set: pt_pct, sl_pct, ts = 0.86, 0.17, 240
    elif "VIX elevated" in signal_set: pt_pct, sl_pct, ts = 0.34, 0.26, 30
    elif "Just below 50d MA" in signal_set: pt_pct, sl_pct, ts = 0.86, 0.034, 390
    elif "Red month" in signal_set or "Deep red week" in signal_set: pt_pct, sl_pct, ts = 0.86, 0.34, 240
    elif "Mixed MAs" in signal_set and gap_dir == "up": pt_pct, sl_pct, ts = 0.86, 0.034, 30
    elif gap_dir == "up" and "Monday" in signal_set: pt_pct, sl_pct, ts = 0.26, 0.34, 390
    elif gap_dir == "up": pt_pct, sl_pct, ts = 0.86, 0.34, 390
    elif "Monday" in signal_set: pt_pct, sl_pct, ts = 0.26, 0.34, 390

    pt = entry_open * pt_pct / 100
    sl = entry_open * sl_pct / 100

    n_positive = len([s for s in signals if "negative" not in s.lower() and "bail" not in s.lower()])
    if n_positive < 1:
        return None

    max_premium = get_max_premium(score)
    clamped_score = max(0, min(score, 80))
    risk = MIN_RISK + (max_premium - MIN_RISK) * (clamped_score / 80)
    if n_positive >= 5: risk = min(risk * 1.3, max_premium)
    elif n_positive >= 3: risk = min(risk * 1.15, max_premium)
    risk = max(MIN_RISK, min(max_premium, round(risk / 1000) * 1000))

    return {
        "score": score, "signals": signals,
        "pt": round(pt, 2), "sl": round(sl, 2), "ts": ts,
        "risk": risk,
        "entry_open": entry_open,
        "first_bar_bullish": first_bar_bullish,
        "dow": dt.strftime("%A"),
        "n_positive": n_positive,
        "vix": round(vix_daily[d]["open"], 1) if d in vix_daily else None,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    ensure_cache()
    print("=" * 80)
    print("QQQ OPENING PRINT — OPTIONS BACKTEST")
    print("=" * 80)

    # Load data
    print("\nLoading data...")
    qqq_daily, qqq_dates = load_daily_csv(QQQ_DAILY)
    qqq_intraday = load_intraday_csv(QQQ_1MIN)
    vix_daily, _ = load_daily_csv(VIX_DAILY)
    tlt_daily, tlt_dates = load_daily_csv(TLT_DAILY)

    intra_dates = sorted(qqq_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}
    print(f"  QQQ daily: {len(qqq_daily)} | QQQ intraday: {len(intra_dates)} days | VIX: {len(vix_daily)}")

    # Evaluate signals
    print("\nEvaluating signals...")
    trade_days = []
    for d in intra_dates:
        bars = qqq_intraday.get(d, [])
        if not bars or len(bars) < 10:
            continue

        result = evaluate_signals(d, bars, intra_dates, intra_idx, qqq_intraday,
                                   vix_daily, qqq_daily, qqq_dates, tlt_daily, tlt_dates)
        if result:
            if BULLISH_ONLY and not result["first_bar_bullish"]:
                continue
            if VIX_FILTER and result.get("vix") and result["vix"] < VIX_FILTER:
                continue
            if VIX_CAP and result.get("vix") and result["vix"] > VIX_CAP:
                continue
            if SMA50_FILTER:
                sma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
                if sma50 and result["entry_open"] < sma50:
                    continue
            if RSI_FILTER:
                rsi = compute_rsi(qqq_daily, qqq_dates, d, 14)
                if rsi is not None and (rsi < RSI_FILTER[0] or rsi > RSI_FILTER[1]):
                    continue
            trade_days.append((d, result))

    print(f"Trade days identified: {len(trade_days)}")

    # Process each trade
    print(f"\nFetching QQQ option data and simulating trades...")
    options_trades = []
    skipped_no_0dte = 0
    skipped_no_bars = 0
    skipped_no_entry = 0
    processed = 0

    for i, (d, sig) in enumerate(trade_days):
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  Processing {i+1}/{len(trade_days)}: {d}...")

        target_strike = round(sig["entry_open"])
        ticker, strike, expiry_date, dte = find_qqq_option_call(d, target_strike)

        if not ticker:
            skipped_no_0dte += 1
            continue

        entry_10s = get_option_entry_price(ticker, d)
        opt_bars = get_option_bars(ticker, d)
        if not opt_bars:
            skipped_no_bars += 1
            continue

        qqq_bars = qqq_intraday.get(d, [])

        opt_result = simulate_options_trade(
            opt_bars, qqq_bars,
            sig["entry_open"], sig["pt"], sig["sl"], sig["ts"],
            sig["score"], sig["first_bar_bullish"], sig["risk"],
            entry_10s_price=entry_10s
        )

        if not opt_result:
            skipped_no_entry += 1
            continue

        trade_record = {
            "date": d,
            "day_of_week": sig["dow"],
            "score": sig["score"],
            "risk": sig["risk"],
            "signals": sig["signals"],
            "strike": strike,
            "option_ticker": ticker,
            "expiry_date": expiry_date,
            "dte": dte,
            "entry_open": sig["entry_open"],
            "first_bar_bullish": sig["first_bar_bullish"],
            "pt": sig["pt"], "sl": sig["sl"], "ts": sig["ts"],
            "vix": sig["vix"],
            "used_10s_entry": entry_10s is not None and entry_10s > 0.10,
            "opt_entry_price": opt_result["option_entry_price"],
            "opt_exit_price": opt_result["option_exit_price"],
            "opt_contracts": opt_result["num_contracts"],
            "opt_premium": opt_result["total_premium"],
            "opt_pnl": opt_result["pnl_dollars"],
            "opt_exit_reason": opt_result["exit_reason"],
            "opt_exit_time": opt_result["exit_time"],
            "opt_hold_mins": opt_result["hold_mins"],
        }
        options_trades.append(trade_record)
        processed += 1

    print(f"\n{'='*70}")
    print(f"Processing complete!")
    print(f"  Trades processed: {processed}")
    print(f"  Skipped (no option available): {skipped_no_0dte}")
    print(f"  Skipped (no option bars): {skipped_no_bars}")
    print(f"  Skipped (no valid entry): {skipped_no_entry}")

    # ── Generate report ──────────────────────────────────────────────
    if not options_trades:
        print("\nNo options trades to analyze!")
        return

    pnls = [t["opt_pnl"] for t in options_trades]
    total_pnl = sum(pnls)
    winners = [t for t in options_trades if t["opt_pnl"] > 0]
    losers = [t for t in options_trades if t["opt_pnl"] <= 0]
    wr = len(winners) / len(options_trades) * 100
    avg_win = mean([t["opt_pnl"] for t in winners]) if winners else 0
    avg_loss = mean([t["opt_pnl"] for t in losers]) if losers else 0
    best = max(pnls)
    worst = min(pnls)
    avg_prem = mean([t["opt_premium"] for t in options_trades])
    avg_contracts = mean([t["opt_contracts"] for t in options_trades])

    cum = peak = max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd

    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999
    calmar = total_pnl / max_dd if max_dd > 0 else 999

    days = (datetime.strptime(options_trades[-1]["date"], "%Y-%m-%d") -
            datetime.strptime(options_trades[0]["date"], "%Y-%m-%d")).days
    tpy = len(pnls) / (days / 365.25) if days > 0 else len(pnls)
    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if len(pnls) > 1 and stdev(pnls) > 0 else 0

    n_0dte = sum(1 for t in options_trades if t.get("dte", 0) == 0)
    n_1dte = sum(1 for t in options_trades if t.get("dte", 0) == 1)
    n_10s = sum(1 for t in options_trades if t.get("used_10s_entry"))

    report = []
    report.append("=" * 80)
    report.append("QQQ OPENING PRINT — OPTIONS BACKTEST")
    report.append("=" * 80)
    report.append(f"  Entry: Buy ATM nearest-expiry QQQ call at 9:30:10 (10-second bars)")
    report.append(f"  Trades with 10s entry price: {n_10s}/{len(options_trades)} ({n_10s/len(options_trades)*100:.0f}%)")
    report.append(f"  0DTE trades: {n_0dte}  |  1DTE trades: {n_1dte}")
    report.append(f"  Bullish only: {BULLISH_ONLY}  |  VIX: {VIX_FILTER}-{VIX_CAP}")
    report.append(f"  SMA50 filter: {SMA50_FILTER}  |  RSI14 filter: {RSI_FILTER}")
    report.append(f"  Date range: {options_trades[0]['date']} to {options_trades[-1]['date']}")
    report.append("")
    report.append("=" * 80)
    report.append("OPTIONS-BASED RESULTS")
    report.append("-" * 80)
    report.append(f"  Trades: {len(options_trades)}  |  WR: {wr:.1f}%")
    report.append(f"  Total P&L: ${total_pnl:,.0f}")
    report.append(f"  Avg Win: ${avg_win:,.0f}  |  Avg Loss: ${avg_loss:,.0f}")
    report.append(f"  Best: ${best:,.0f}  |  Worst: ${worst:,.0f}")
    report.append(f"  Max Drawdown: ${max_dd:,.0f}")
    report.append(f"  Profit Factor: {pf:.2f}")
    report.append(f"  Calmar Ratio: {calmar:.2f}")
    report.append(f"  Sharpe Ratio (ann.): {sharpe:.2f}")
    report.append(f"  Avg Premium per Trade: ${avg_prem:,.0f}")
    report.append(f"  Avg Contracts per Trade: {avg_contracts:.0f}")
    report.append("")

    # By exit reason
    report.append("=" * 80)
    report.append("BY EXIT REASON")
    report.append("-" * 80)
    by_reason = defaultdict(list)
    for t in options_trades:
        by_reason[t["opt_exit_reason"]].append(t["opt_pnl"])
    for reason in sorted(by_reason.keys()):
        rp = by_reason[reason]
        ravg = mean(rp)
        rwr = sum(1 for p in rp if p > 0) / len(rp) * 100
        report.append(f"  {reason:25s}: {len(rp):>3} trades, avg ${ravg:>10,.0f}, WR {rwr:.0f}%")
    report.append("")

    # Yearly
    report.append("=" * 80)
    report.append("YEARLY BREAKDOWN")
    report.append("-" * 80)
    yearly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
    for t in options_trades:
        yr = t["date"][:4]
        yearly[yr]["pnl"] += t["opt_pnl"]
        yearly[yr]["count"] += 1
        if t["opt_pnl"] > 0: yearly[yr]["wins"] += 1
    cum = 0
    for yr in sorted(yearly.keys()):
        y = yearly[yr]
        cum += y["pnl"]
        ywr = y["wins"] / y["count"] * 100 if y["count"] else 0
        report.append(f"  {yr}: {y['count']:>3} trades, P&L ${y['pnl']:>10,.0f}, "
                      f"Cum ${cum:>12,.0f}, WR {ywr:.0f}%")
    report.append("")

    # Top trades
    sorted_by_pnl = sorted(options_trades, key=lambda t: t["opt_pnl"], reverse=True)
    report.append("=" * 80)
    report.append("TOP 10 BEST TRADES")
    report.append("-" * 80)
    for t in sorted_by_pnl[:10]:
        report.append(f"  {t['date']}  |  P&L: ${t['opt_pnl']:>10,.0f}  |  "
                      f"Entry: ${t['opt_entry_price']:>7.2f}  Exit: ${t['opt_exit_price']:>7.2f}  |  "
                      f"{t['opt_contracts']} contracts  |  {t['opt_exit_reason']}")
    report.append("")
    report.append("TOP 10 WORST TRADES")
    report.append("-" * 80)
    for t in sorted_by_pnl[-10:]:
        report.append(f"  {t['date']}  |  P&L: ${t['opt_pnl']:>10,.0f}  |  "
                      f"Entry: ${t['opt_entry_price']:>7.2f}  Exit: ${t['opt_exit_price']:>7.2f}  |  "
                      f"{t['opt_contracts']} contracts  |  {t['opt_exit_reason']}")
    report.append("")

    # Monthly
    report.append("=" * 80)
    report.append("MONTHLY BREAKDOWN")
    report.append("-" * 80)
    monthly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
    for t in options_trades:
        m = t["date"][:7]
        monthly[m]["pnl"] += t["opt_pnl"]
        monthly[m]["count"] += 1
        if t["opt_pnl"] > 0: monthly[m]["wins"] += 1
    cum = 0
    for m in sorted(monthly.keys()):
        d = monthly[m]
        cum += d["pnl"]
        mwr = d["wins"] / d["count"] * 100 if d["count"] else 0
        report.append(f"  {m}  |  Trades: {d['count']:>3}  |  "
                      f"P&L: ${d['pnl']:>10,.0f}  |  Cum: ${cum:>12,.0f}  |  WR: {mwr:.0f}%")
    report.append("")
    report.append("=" * 80)

    report_text = "\n".join(report)
    print("\n" + report_text)

    with open(OUTPUT_REPORT, "w") as f:
        f.write(report_text)
    print(f"\nReport saved: {OUTPUT_REPORT}")

    with open(OUTPUT_JSON, "w") as f:
        json.dump(options_trades, f, indent=2, default=str)
    print(f"Trade data saved: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
