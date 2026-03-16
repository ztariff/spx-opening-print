"""
QQQ Opening Print — Script 25: ITM Options Test
=================================================
Test deeper ITM QQQ calls to see if higher delta / less theta improves results.

ATM QQQ calls were $0.50-$3.00 (mostly time value, high theta).
ITM calls have more intrinsic value, higher delta, move more like stock.

Test various ITM depths:
- ATM (strike = current price) — baseline
- 1 ITM ($1 below current price)
- 2 ITM ($2 below)
- 3 ITM ($3 below)
- 5 ITM ($5 below)
- 1% ITM (strike = 99% of price)
- 2% ITM (strike = 98% of price)
- 3% ITM (strike = 97% of price)
"""

import json, os, math, time, csv, urllib.request, urllib.error
from datetime import datetime, timedelta
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")
QQQ_1MIN = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

# Load existing QQQ trade days from the ATM backtest
trades = json.load(open(os.path.join(SCRIPT_DIR, "qqq_options_trades.json")))
print(f"Loaded {len(trades)} QQQ ATM trades as reference")

# Load QQQ intraday
def load_intraday():
    data = defaultdict(list)
    with open(QQQ_1MIN) as f:
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

print("Loading QQQ intraday...")
qqq_intraday = load_intraday()

# ── API / Cache helpers ──────────────────────────────────────────────

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
    daily_0dte_start = datetime(2022, 11, 1)
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

def get_option_bars(ticker, date_str):
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
                "time": t_str, "open": r["o"], "high": r["h"],
                "low": r["l"], "close": r["c"], "volume": r.get("v", 0)
            })

    if bars:
        save_cache(cache_key, bars)
    else:
        save_cache(cache_key, "none")
    return bars

def get_entry_10s(ticker, date_str):
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

def find_qqq_call(date_str, target_strike):
    """Find QQQ call at specific strike."""
    cache_key = f"qqq_itm_{date_str}_{target_strike}"
    cached = load_cache(cache_key)
    if cached is not None:
        if cached == "none":
            return None, None, None, None
        return cached["ticker"], cached["strike"], cached["expiry"], cached["dte"]

    expiry_date, dte = find_nearest_expiry_qqq(date_str)
    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    date_code = exp_dt.strftime("%y%m%d")

    # Try exact strike and neighbors
    for offset in [0, 1, -1]:
        test_strike = target_strike + offset
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


def simulate_trade(opt_bars, qqq_bars, entry_open, pt, sl, ts, risk, entry_10s_price=None):
    """Simulate options trade, return pnl dict or None."""
    if not opt_bars or len(opt_bars) < 2:
        return None

    if entry_10s_price and entry_10s_price > 0.10:
        option_entry = entry_10s_price
    else:
        entry_bar = None
        for bar in opt_bars:
            if bar["time"] == "09:31":
                entry_bar = bar; break
        if not entry_bar:
            entry_bar = opt_bars[0]
        option_entry = entry_bar["close"]
    if option_entry <= 0.10:
        return None

    contract_cost = option_entry * 100
    num_contracts = int(risk / contract_cost)
    if num_contracts < 1:
        num_contracts = 1
    total_premium = num_contracts * contract_cost

    opt_map = {bar["time"]: bar for bar in opt_bars}

    exit_time = None
    exit_reason = None
    hold_mins = 0

    started = False
    bars_held = 0
    for bar in qqq_bars:
        if bar["time"] <= "09:31":
            if bar["time"] == "09:31": started = True
            continue
        if not started: started = True
        bars_held += 1
        if bars_held > ts: break

        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]; exit_reason = "Stop Loss"; hold_mins = bars_held; break
        if bar["high"] >= entry_open + pt:
            exit_time = bar["time"]; exit_reason = "Profit Target"; hold_mins = bars_held; break

    if exit_time is None:
        ts_idx = min(len(qqq_bars) - 1, ts + 1)
        exit_time = qqq_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    exit_price = None
    if exit_time in opt_map:
        exit_price = opt_map[exit_time]["close"]
    else:
        for obar in opt_bars:
            if obar["time"] >= exit_time:
                exit_price = obar["close"]; break
        if exit_price is None:
            exit_price = opt_bars[-1]["close"]

    pnl = num_contracts * (exit_price - option_entry) * 100
    return {
        "pnl": round(pnl, 2),
        "entry": option_entry,
        "exit": exit_price,
        "contracts": num_contracts,
        "premium": total_premium,
        "reason": exit_reason,
    }


# ── Main test ────────────────────────────────────────────────────────

ensure_cache()

# ITM depths to test: (label, dollars_itm)
# For percentage-based, we'll compute per trade
itm_configs = [
    ("ATM (baseline)", 0),
    ("$1 ITM", 1),
    ("$2 ITM", 2),
    ("$3 ITM", 3),
    ("$5 ITM", 5),
    ("$7 ITM", 7),
    ("$10 ITM", 10),
    ("1% ITM", "1pct"),
    ("2% ITM", "2pct"),
    ("3% ITM", "3pct"),
]

print(f"\nTesting {len(itm_configs)} ITM depths across {len(trades)} trade days...")
print()

all_results = []

for config_label, itm_depth in itm_configs:
    pnls = []
    skipped = 0
    entry_prices = []

    for t in trades:
        d = t["date"]
        entry_open = t["entry_open"]
        qqq_bars = qqq_intraday.get(d, [])
        if not qqq_bars:
            skipped += 1
            continue

        # Compute target strike
        if isinstance(itm_depth, str) and "pct" in itm_depth:
            pct = int(itm_depth.replace("pct", ""))
            target_strike = round(entry_open * (1 - pct / 100))
        else:
            target_strike = round(entry_open) - itm_depth

        ticker, strike, expiry, dte = find_qqq_call(d, target_strike)
        if not ticker:
            skipped += 1
            continue

        entry_10s = get_entry_10s(ticker, d)
        opt_bars = get_option_bars(ticker, d)
        if not opt_bars:
            skipped += 1
            continue

        result = simulate_trade(opt_bars, qqq_bars, entry_open,
                                 t["pt"], t["sl"], t["ts"], t["risk"],
                                 entry_10s_price=entry_10s)
        if not result:
            skipped += 1
            continue

        pnls.append(result["pnl"])
        entry_prices.append(result["entry"])

    if len(pnls) < 10:
        print(f"  {config_label:15s}: Only {len(pnls)} trades (skipped {skipped}), insufficient data")
        continue

    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / len(pnls) * 100
    cum = peak = max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    calmar = total / max_dd if max_dd > 0 else 999
    gw = sum(p for p in pnls if p > 0)
    gl = sum(abs(p) for p in pnls if p <= 0)
    pf = gw / gl if gl > 0 else 999

    days_span = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
                 datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
    tpy = len(pnls) / (days_span / 365.25)
    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if stdev(pnls) > 0 else 0

    avg_entry = mean(entry_prices)
    print(f"  {config_label:15s}: Sharpe {sharpe:.2f} | P&L ${total:>10,.0f} | DD ${max_dd:>8,.0f} | "
          f"Calmar {calmar:.2f} | PF {pf:.2f} | WR {wr:.1f}% | "
          f"Trades: {len(pnls)} | Avg entry: ${avg_entry:.2f}")

    all_results.append((sharpe, total, max_dd, calmar, pf, wr, len(pnls), avg_entry, config_label))

print()
print("=" * 100)
print("RANKED BY SHARPE")
print("=" * 100)
all_results.sort(key=lambda x: x[0], reverse=True)
for i, (sharpe, total, dd, calmar, pf, wr, n, avg_e, label) in enumerate(all_results):
    print(f"  #{i+1}: {label:15s} | Sharpe {sharpe:.2f} | P&L ${total:>10,.0f} | DD ${dd:>8,.0f} | "
          f"Calmar {calmar:.2f} | PF {pf:.2f} | WR {wr:.1f}% | Avg entry ${avg_e:.2f}")
