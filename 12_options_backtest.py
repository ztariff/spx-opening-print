"""
SPX Opening Print Strategy — Phase 12: 0DTE Options Backtest
=============================================================
Instead of buying SPX directly, buy $RISK worth of ATM 0DTE SPX calls
at ~9:30:10 (use 9:31 bar close as proxy). Max loss = premium paid.

For each trade day from the existing strategy:
  1. Find 0DTE ATM call option contract (strike closest to SPX open)
  2. Pull 1-min bars for that option
  3. Entry at 9:31 bar close (proxy for 9:30:10 fill)
  4. Apply exit logic using option prices
  5. Max loss = premium paid (capped by definition)

Note: SPX daily 0DTE only available since ~May 2022 (CBOE added
Tue/Thu expiries). Before that, only M/W/F. We skip days with no
0DTE expiration available.

Usage:
    python3 12_options_backtest.py

Output:
    options_backtest_report.txt
    options_trades.json  (for calendar integration later)
"""

import os
import csv
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict
from statistics import mean
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "options_backtest_report.txt")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "options_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")

API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
BASE_URL = "https://api.polygon.io"

MIN_RISK = 25000
MAX_LOSS_TARGET = 150000  # Target max loss in dollars

# Dynamic max premium by score bracket, based on historical worst-case loss %
# Score < 25: worst loss = 100% of premium -> max premium $150k
# Score 25-40: worst loss = 99% -> max premium $155k
# Score 40-55: worst loss = 89% -> max premium $170k
# Score 55-70: worst loss = 87% -> max premium $175k
# Score 70-85: worst loss = 77% -> max premium $200k
# Score 85+:   worst loss = 75% -> max premium $200k
def get_max_premium(score):
    if score < 25:   return 150000
    elif score < 40: return 155000
    elif score < 55: return 170000
    elif score < 70: return 175000
    elif score < 85: return 200000
    else:            return 200000
HYBRID_THRESHOLD = 25

# Rate limit: Polygon free tier = 5 req/min. Paid tiers are higher.
# We'll do 4 req/sec with backoff on 429s.
REQUEST_DELAY = 0.3  # seconds between requests


# ── Cache helpers ─────────────────────────────────────────────────────

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


# ── API helpers ───────────────────────────────────────────────────────

def api_get(url, max_retries=3):
    """GET with retry and rate limit handling."""
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


def find_0dte_call(date_str, target_strike):
    """Find the ATM 0DTE SPX call option ticker for a given date.
    Constructs the ticker directly and verifies bars exist.
    Tries SPXW first (weekly/0DTE), then SPX.
    Returns (ticker, strike) or (None, None) if not available."""
    cache_key = f"contracts_v2_{date_str}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None
        return cached["ticker"], cached["strike"]

    # Round strike to nearest $5
    strike = round(target_strike / 5) * 5
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_code = dt.strftime("%y%m%d")  # YYMMDD

    # Try multiple strikes near ATM (in case exact ATM has no liquidity)
    strike_offsets = [0, 5, -5, 10, -10]

    for underlying in ["SPXW", "SPX"]:
        for offset in strike_offsets:
            test_strike = strike + offset
            # Polygon options ticker format: O:{underlying}{YYMMDD}C{strike*1000:08d}
            strike_code = f"{int(test_strike * 1000):08d}"
            ticker = f"O:{underlying}{date_code}C{strike_code}"

            # Try to fetch just 1 bar to verify this ticker exists
            url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}"
                   f"/range/1/minute/{date_str}/{date_str}"
                   f"?adjusted=true&sort=asc&limit=3"
                   f"&apiKey={API_KEY}")

            time.sleep(REQUEST_DELAY)
            data = api_get(url)
            if data and data.get("resultsCount", 0) > 0:
                save_cache(cache_key, {"ticker": ticker, "strike": test_strike})
                return ticker, test_strike

    save_cache(cache_key, "none")
    return None, None


def get_option_entry_price(ticker, date_str):
    """Get the option price at ~9:30:10 using 10-second bars.
    Returns the close of the 9:30:10 bar, or None if unavailable."""
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

    # Determine DST offset for this date
    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    offset_hours = 4 if is_dst else 5

    # Find the bar closest to 9:30:10 ET
    target_seconds = 9 * 3600 + 30 * 60 + 10  # 9:30:10 in seconds from midnight
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

    if best_bar and best_diff <= 30:  # Within 30 seconds of target
        price = round(best_bar["c"], 2)
        save_cache(cache_key, price)
        return price

    save_cache(cache_key, "none")
    return None


def get_option_bars(ticker, date_str):
    """Get 1-min bars for an option on a specific date.
    Returns list of {time, open, high, low, close, volume} dicts."""
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
    for r in data.get("results", []):
        # Convert timestamp to ET time string
        ts_ms = r["t"]
        # Polygon timestamps are in UTC milliseconds
        dt_utc = datetime.utcfromtimestamp(ts_ms / 1000)
        # Convert UTC to ET (approximate: -4 for EDT, -5 for EST)
        # Determine if date is in DST
        dt_date = datetime.strptime(date_str, "%Y-%m-%d")
        # Simple DST check: second Sunday in March to first Sunday in November
        year = dt_date.year
        # March: second Sunday
        mar1 = datetime(year, 3, 1)
        dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
        # November: first Sunday
        nov1 = datetime(year, 11, 1)
        dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
        offset_hours = 4 if is_dst else 5

        dt_et = dt_utc - timedelta(hours=offset_hours)
        time_str = dt_et.strftime("%H:%M")

        # Only keep RTH bars (9:30 - 16:00)
        if time_str < "09:30" or time_str >= "16:00":
            continue

        bars.append({
            "time": time_str,
            "open": round(r["o"], 2),
            "high": round(r["h"], 2),
            "low": round(r["l"], 2),
            "close": round(r["c"], 2),
            "volume": r.get("v", 0),
        })

    bars.sort(key=lambda x: x["time"])
    save_cache(cache_key, bars if bars else "none")
    return bars


# ── Data Loaders (same as 09) ────────────────────────────────────────

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


# ── Signal Detection (same as 09, returns score + signals + params) ──

def evaluate_signals(d, bars, intra_dates, intra_idx, spx_intraday,
                     vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates):
    """Returns (score, signals, pt, sl, ts, risk, entry_open, first_bar_bullish)
    or None if no trade."""
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

    vix_dates_sorted = sorted(vix_daily.keys())
    if d in vix_daily:
        vix_open = vix_daily[d]["open"]
        signals.append(f"VIX at open: {vix_open:.1f}")
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
        "score": score,
        "signals": signals,
        "pt": pt, "sl": sl, "ts": ts,
        "risk": risk,
        "entry_open": entry_open,
        "first_bar_bullish": first_bar_bullish,
        "dow": dt.strftime("%A"),
        "n_positive": n_positive,
        "vix": round(vix_daily[d]["open"], 1) if d in vix_daily else None,
    }


# ── Options trade simulation ─────────────────────────────────────────

def simulate_options_trade(option_bars, spx_bars, entry_open, pt, sl, ts,
                           score, first_bar_bullish, risk, entry_10s_price=None):
    """Simulate a trade using actual option prices.

    Entry: buy calls at 9:30:10 (from 10-second bars). Falls back to
    first 1-min bar close if 10s data unavailable.
    Exit logic based on SPX movement mapped to option prices:
      - If SPX hits entry + PT → sell calls at that option bar
      - If SPX hits entry - SL → sell calls (but loss capped at premium)
      - Time stop → sell at that bar's option price
      - For Approach C: if score < threshold and bearish → no trade
      - For Approach C: if score >= threshold and bearish → hold (no bail needed,
        loss is capped at premium anyway!)

    Returns dict with trade details or None."""

    if not option_bars or len(option_bars) < 2:
        return None

    # Use 9:30:10 price from 10-second bars if available
    if entry_10s_price and entry_10s_price > 0.10:
        option_entry_price = entry_10s_price
    else:
        # Fallback: first bar's close (9:30 bar close = ~9:31 price)
        entry_bar = None
        for bar in option_bars:
            if bar["time"] >= "09:30":
                entry_bar = bar
                break
        if entry_bar is None:
            entry_bar = option_bars[0]
        option_entry_price = entry_bar["close"]
    if option_entry_price <= 0.10:  # option essentially worthless, skip
        return None

    # Number of contracts we can buy
    # Each contract = 100 shares, cost = price * 100
    contract_cost = option_entry_price * 100
    num_contracts = int(risk / contract_cost)
    if num_contracts < 1:
        num_contracts = 1
    total_premium = num_contracts * contract_cost

    # Now simulate using SPX bars for exit signals, but use option prices for P&L
    # Build a time-aligned map of option prices
    option_price_map = {}
    for bar in option_bars:
        option_price_map[bar["time"]] = bar

    # Approach C logic for exit timing
    if score >= HYBRID_THRESHOLD:
        # Approach B: we're in regardless. No bail needed since max loss = premium.
        # But we still apply PT/SL/TS based on SPX levels.
        # With options, we don't "bail" — we just hold since downside is capped.
        # However, we can still use SPX-based exit signals to take profits.
        pass
    else:
        # Approach A: only trade if first bar is bullish
        if not first_bar_bullish:
            return None

    # Find exit using SPX bars for signal, option bars for price
    exit_option_price = None
    exit_time = None
    exit_reason = None
    hold_mins = 0

    # Map SPX bars by time
    spx_bar_map = {}
    for bar in spx_bars:
        spx_bar_map[bar["time"]] = bar

    # Start from bar after entry (09:31 or later)
    started = False
    bars_held = 0
    for bar in spx_bars:
        if bar["time"] <= "09:31":
            if bar["time"] == "09:31":
                started = True
            continue
        if not started:
            started = True

        bars_held += 1
        if bars_held > ts:
            break

        # Check SPX-based exit conditions
        if bar["low"] <= entry_open - sl:
            # Stop loss hit in SPX — sell option at this bar's price
            exit_time = bar["time"]
            exit_reason = "Stop Loss (SPX)"
            hold_mins = bars_held
            break

        if bar["high"] >= entry_open + pt:
            # Profit target hit in SPX — sell option
            exit_time = bar["time"]
            exit_reason = "Profit Target (SPX)"
            hold_mins = bars_held
            break

    if exit_time is None:
        # Time stop — find the last valid bar
        ts_target_idx = min(len(spx_bars) - 1, ts + 1)  # +1 since we skip first bar
        exit_time = spx_bars[ts_target_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_target_idx

    # Get option price at exit time
    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        # Find closest option bar at or after exit time
        for bar in option_bars:
            if bar["time"] >= exit_time:
                exit_option_price = bar["close"]
                break
        if exit_option_price is None:
            # Use last available option bar
            exit_option_price = option_bars[-1]["close"]
            exit_time = option_bars[-1]["time"]

    # Calculate P&L
    pnl_per_contract = (exit_option_price - option_entry_price) * 100
    total_pnl = num_contracts * pnl_per_contract

    # Max loss is capped at premium paid (options can't go below 0)
    # This is automatic — if option goes to 0, loss = -total_premium

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


# ── Main ──────────────────────────────────────────────────────────────

def main():
    ensure_cache()

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

    # Phase 1: Identify all trade days using same signal logic
    print("\nPhase 1: Identifying trade days...")
    trade_days = []
    for d in intra_dates:
        bars = spx_intraday[d]
        result = evaluate_signals(
            d, bars, intra_dates, intra_idx, spx_intraday,
            vix_daily, spx_daily, spx_dates, tlt_daily, tlt_dates
        )
        if result:
            # Apply Approach C filter
            if result["score"] < HYBRID_THRESHOLD and not result["first_bar_bullish"]:
                continue  # Approach A would skip this day
            trade_days.append((d, result))

    print(f"Trade days identified: {len(trade_days)}")

    # Phase 2: For each trade day, find 0DTE option and get prices
    print("\nPhase 2: Fetching 0DTE option data from Polygon...")
    print("(This will take a while due to API rate limits)\n")

    options_trades = []
    linear_trades = []  # For comparison
    skipped_no_0dte = 0
    skipped_no_bars = 0
    skipped_no_entry = 0
    processed = 0

    for i, (d, sig) in enumerate(trade_days):
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  Processing {i+1}/{len(trade_days)}: {d}...")

        # Find ATM 0DTE call
        target_strike = round(sig["entry_open"] / 5) * 5  # Round to nearest $5 strike
        ticker, strike = find_0dte_call(d, target_strike)

        if not ticker:
            skipped_no_0dte += 1
            continue

        # Get 9:30:10 entry price from 10-second bars
        entry_10s = get_option_entry_price(ticker, d)

        # Get option 1-min bars
        opt_bars = get_option_bars(ticker, d)
        if not opt_bars:
            skipped_no_bars += 1
            continue

        # Get SPX bars for this day
        spx_bars = spx_intraday.get(d, [])

        # Simulate options trade
        opt_result = simulate_options_trade(
            opt_bars, spx_bars,
            sig["entry_open"], sig["pt"], sig["sl"], sig["ts"],
            sig["score"], sig["first_bar_bullish"], sig["risk"],
            entry_10s_price=entry_10s
        )

        if not opt_result:
            skipped_no_entry += 1
            continue

        # Also compute the linear (non-options) P&L for comparison
        dollars_per_point = sig["risk"] / sig["sl"]
        if sig["score"] >= HYBRID_THRESHOLD:
            if sig["first_bar_bullish"]:
                # Simulate from bar 1
                spx_entry = sig["entry_open"]
                pnl_pts = 0
                for bi in range(1, min(sig["ts"] + 1, len(spx_bars))):
                    bar = spx_bars[bi]
                    if bar["low"] <= spx_entry - sig["sl"]:
                        pnl_pts = -sig["sl"]; break
                    if bar["high"] >= spx_entry + sig["pt"]:
                        pnl_pts = sig["pt"]; break
                else:
                    last_idx = min(sig["ts"], len(spx_bars) - 1)
                    pnl_pts = round(spx_bars[last_idx]["close"] - spx_entry, 2)
            else:
                # Bail
                bail_price = spx_bars[0]["close"]
                pnl_pts = round(bail_price - sig["entry_open"], 2)
        else:
            # Approach A
            spx_entry = spx_bars[0]["close"]
            pnl_pts = 0
            for bi in range(1, min(sig["ts"] + 1, len(spx_bars))):
                bar = spx_bars[bi]
                if bar["low"] <= spx_entry - sig["sl"]:
                    pnl_pts = -sig["sl"]; break
                if bar["high"] >= spx_entry + sig["pt"]:
                    pnl_pts = sig["pt"]; break
            else:
                last_idx = min(sig["ts"], len(spx_bars) - 1)
                pnl_pts = round(spx_bars[last_idx]["close"] - spx_entry, 2)

        linear_pnl = round(pnl_pts * dollars_per_point, 2)

        trade_record = {
            "date": d,
            "day_of_week": sig["dow"],
            "score": sig["score"],
            "risk": sig["risk"],
            "signals": sig["signals"],
            "strike": strike,
            "option_ticker": ticker,
            "entry_open": sig["entry_open"],
            "first_bar_bullish": sig["first_bar_bullish"],
            "pt": sig["pt"], "sl": sig["sl"], "ts": sig["ts"],
            "vix": sig["vix"],
            "used_10s_entry": entry_10s is not None and entry_10s > 0.10,
            # Options results
            "opt_entry_price": opt_result["option_entry_price"],
            "opt_exit_price": opt_result["option_exit_price"],
            "opt_contracts": opt_result["num_contracts"],
            "opt_premium": opt_result["total_premium"],
            "opt_pnl": opt_result["pnl_dollars"],
            "opt_exit_reason": opt_result["exit_reason"],
            "opt_exit_time": opt_result["exit_time"],
            "opt_hold_mins": opt_result["hold_mins"],
            # Linear comparison
            "linear_pnl": linear_pnl,
            "linear_pnl_pts": pnl_pts,
        }
        options_trades.append(trade_record)
        processed += 1

    print(f"\n{'='*70}")
    print(f"Processing complete!")
    print(f"  Trades processed: {processed}")
    print(f"  Skipped (no 0DTE available): {skipped_no_0dte}")
    print(f"  Skipped (no option bars): {skipped_no_bars}")
    print(f"  Skipped (no valid entry): {skipped_no_entry}")

    # ── Generate report ───────────────────────────────────────────────
    if not options_trades:
        print("\nNo options trades to analyze!")
        return

    # Options stats
    opt_total_pnl = sum(t["opt_pnl"] for t in options_trades)
    opt_winners = [t for t in options_trades if t["opt_pnl"] > 0]
    opt_losers = [t for t in options_trades if t["opt_pnl"] <= 0]
    opt_wr = len(opt_winners) / len(options_trades) * 100
    opt_avg_win = mean([t["opt_pnl"] for t in opt_winners]) if opt_winners else 0
    opt_avg_loss = mean([t["opt_pnl"] for t in opt_losers]) if opt_losers else 0
    opt_best = max(t["opt_pnl"] for t in options_trades)
    opt_worst = min(t["opt_pnl"] for t in options_trades)
    opt_avg_premium = mean([t["opt_premium"] for t in options_trades])
    opt_avg_contracts = mean([t["opt_contracts"] for t in options_trades])

    # Max drawdown
    opt_cum = 0
    opt_peak = 0
    opt_max_dd = 0
    for t in options_trades:
        opt_cum += t["opt_pnl"]
        if opt_cum > opt_peak: opt_peak = opt_cum
        dd = opt_peak - opt_cum
        if dd > opt_max_dd: opt_max_dd = dd

    # Linear comparison stats (same days only)
    lin_total_pnl = sum(t["linear_pnl"] for t in options_trades)
    lin_winners = [t for t in options_trades if t["linear_pnl"] > 0]
    lin_losers = [t for t in options_trades if t["linear_pnl"] <= 0]
    lin_wr = len(lin_winners) / len(options_trades) * 100
    lin_best = max(t["linear_pnl"] for t in options_trades)
    lin_worst = min(t["linear_pnl"] for t in options_trades)

    lin_cum = 0
    lin_peak = 0
    lin_max_dd = 0
    for t in options_trades:
        lin_cum += t["linear_pnl"]
        if lin_cum > lin_peak: lin_peak = lin_cum
        dd = lin_peak - lin_cum
        if dd > lin_max_dd: lin_max_dd = dd

    # Bail comparison
    bail_trades = [t for t in options_trades if not t["first_bar_bullish"] and t["score"] >= HYBRID_THRESHOLD]
    bail_opt_pnl = sum(t["opt_pnl"] for t in bail_trades) if bail_trades else 0
    bail_lin_pnl = sum(t["linear_pnl"] for t in bail_trades) if bail_trades else 0

    report = []
    report.append("=" * 80)
    report.append("SPX OPENING PRINT — 0DTE OPTIONS BACKTEST")
    report.append("=" * 80)
    n_10s = sum(1 for t in options_trades if t.get("used_10s_entry"))
    report.append(f"  Entry: Buy ATM 0DTE SPX call at 9:30:10 (10-second bars)")
    report.append(f"  Trades with 10s entry price: {n_10s}/{len(options_trades)} ({n_10s/len(options_trades)*100:.0f}%)")
    report.append(f"  Fallback: 1st 1-min bar close when 10s data unavailable")
    report.append(f"  Max loss per trade = premium paid (no bail needed)")
    report.append(f"  Approach C hybrid (threshold={HYBRID_THRESHOLD})")
    report.append(f"  Date range: {options_trades[0]['date']} to {options_trades[-1]['date']}")
    report.append("")

    report.append("=" * 80)
    report.append("OPTIONS-BASED RESULTS")
    report.append("-" * 80)
    report.append(f"  Trades: {len(options_trades)}  |  WR: {opt_wr:.1f}%")
    report.append(f"  Total P&L: ${opt_total_pnl:,.0f}")
    report.append(f"  Avg Win: ${opt_avg_win:,.0f}  |  Avg Loss: ${opt_avg_loss:,.0f}")
    report.append(f"  Best: ${opt_best:,.0f}  |  Worst: ${opt_worst:,.0f}")
    report.append(f"  Max Drawdown: ${opt_max_dd:,.0f}")
    report.append(f"  Avg Premium per Trade: ${opt_avg_premium:,.0f}")
    report.append(f"  Avg Contracts per Trade: {opt_avg_contracts:.0f}")
    report.append("")

    report.append("=" * 80)
    report.append("LINEAR COMPARISON (same days, Approach C)")
    report.append("-" * 80)
    report.append(f"  Trades: {len(options_trades)}  |  WR: {lin_wr:.1f}%")
    report.append(f"  Total P&L: ${lin_total_pnl:,.0f}")
    report.append(f"  Best: ${lin_best:,.0f}  |  Worst: ${lin_worst:,.0f}")
    report.append(f"  Max Drawdown: ${lin_max_dd:,.0f}")
    report.append("")

    report.append("=" * 80)
    report.append("BAIL TRADE COMPARISON (bearish 1st bar, score >= 25)")
    report.append("-" * 80)
    report.append(f"  Bail trades: {len(bail_trades)}")
    report.append(f"  Options P&L on bail days: ${bail_opt_pnl:,.0f}")
    report.append(f"  Linear P&L on bail days:  ${bail_lin_pnl:,.0f}")
    report.append(f"  Options advantage: ${bail_opt_pnl - bail_lin_pnl:,.0f}")
    report.append("")

    # Top 10 best and worst options trades
    sorted_by_pnl = sorted(options_trades, key=lambda t: t["opt_pnl"], reverse=True)
    report.append("=" * 80)
    report.append("TOP 10 BEST OPTIONS TRADES")
    report.append("-" * 80)
    for t in sorted_by_pnl[:10]:
        report.append(f"  {t['date']}  |  P&L: ${t['opt_pnl']:>10,.0f}  |  "
                      f"Entry: ${t['opt_entry_price']:>7.2f}  Exit: ${t['opt_exit_price']:>7.2f}  |  "
                      f"{t['opt_contracts']} contracts  |  {t['opt_exit_reason']}")

    report.append("")
    report.append("TOP 10 WORST OPTIONS TRADES")
    report.append("-" * 80)
    for t in sorted_by_pnl[-10:]:
        report.append(f"  {t['date']}  |  P&L: ${t['opt_pnl']:>10,.0f}  |  "
                      f"Entry: ${t['opt_entry_price']:>7.2f}  Exit: ${t['opt_exit_price']:>7.2f}  |  "
                      f"{t['opt_contracts']} contracts  |  {t['opt_exit_reason']}")

    # Monthly breakdown
    report.append("")
    report.append("=" * 80)
    report.append("MONTHLY BREAKDOWN (OPTIONS)")
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
        wr = d["wins"] / d["count"] * 100 if d["count"] else 0
        report.append(f"  {m}  |  Trades: {d['count']:>3}  |  "
                      f"P&L: ${d['pnl']:>10,.0f}  |  Cum: ${cum:>12,.0f}  |  WR: {wr:.0f}%")

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
