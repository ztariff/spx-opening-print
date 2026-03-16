"""
QQQ Opening Print — Script 27: Contrarian Options Backtest
============================================================
Key insight from Script 26: QQQ's edge is MEAN REVERSION, not momentum.

What works for QQQ (opposite of SPX):
- Below SMA50/200 (not above)
- Gap down days (not gap up)
- After red days (especially big red -1%+)
- Higher VIX (>= 18-20)
- Exclude Tuesday
- Tight PT/SL: 0.20% / 0.10% with 30-min hold

Phase 1: Test contrarian combined filters (linear) to find best combo
Phase 2: Run best combo through full options backtest

Usage:
    python3 27_qqq_contrarian_backtest.py
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QQQ_1MIN = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")
QQQ_DAILY = os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "qqq_contrarian_report.txt")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "qqq_contrarian_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"

# ── Contrarian strategy parameters ──────────────────────────────────
# From script 26 analysis:
PT_PCT = 0.20      # 0.20% profit target (best from grid search)
SL_PCT = 0.10      # 0.10% stop loss
TS_BARS = 30       # 30-min time stop

MIN_RISK = 25000
MAX_RISK = 200000
REQUEST_DELAY = 0.05

# Tee stdout to file
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

# ── Cache / API helpers (reused from script 24) ─────────────────────

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
                print(f"    HTTP {e.code} on attempt {attempt+1}")
                time.sleep(2)
        except Exception as e:
            print(f"    Error: {e} on attempt {attempt+1}")
            time.sleep(2)
    return None

def find_nearest_expiry_qqq(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    daily_0dte_start = datetime(2022, 11, 1)
    if dt >= daily_0dte_start:
        return date_str, 0
    dow = dt.weekday()
    if dow in (0, 2, 4):  # Mon, Wed, Fri
        return date_str, 0
    elif dow == 1:  # Tue -> Wed
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d"), 1
    elif dow == 3:  # Thu -> Fri
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
    exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
    date_code = exp_dt.strftime("%y%m%d")

    for offset in [0, 1, -1, 2, -2, 3, -3]:
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


# ══════════════════════════════════════════════════════════════════════
# PHASE 1: Test contrarian combined filters (linear)
# ══════════════════════════════════════════════════════════════════════

def run_phase1(qqq_daily, qqq_dates, qqq_intraday, vix_daily):
    print("=" * 80)
    print("PHASE 1: CONTRARIAN COMBINED FILTER SEARCH (LINEAR)")
    print("=" * 80)

    intra_dates = sorted(qqq_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}

    # Build day data (same as script 26)
    all_days = []
    for d in intra_dates:
        bars = qqq_intraday[d]
        if len(bars) < 10: continue
        dt = datetime.strptime(d, "%Y-%m-%d")
        entry = bars[0]["open"]

        day = {
            "date": d, "dow": dt.strftime("%A"), "entry": entry,
            "bullish": bars[0]["close"] > bars[0]["open"],
            "vix": vix_daily[d]["open"] if d in vix_daily else None,
            "bars": bars,
        }

        # Gap and prior day
        idx = intra_idx[d]
        if idx > 0:
            prev_d = intra_dates[idx - 1]
            prev_bars = qqq_intraday.get(prev_d, [])
            if prev_bars:
                prev_close = prev_bars[-1]["close"]
                day["gap_pct"] = (entry - prev_close) / prev_close * 100
                prev_open = prev_bars[0]["open"]
                day["prev_ret"] = (prev_close - prev_open) / prev_open * 100
                prev_high = max(b["high"] for b in prev_bars)
                prev_low = min(b["low"] for b in prev_bars)
                day["prev_range"] = (prev_high - prev_low) / prev_close * 100

        # MAs and RSI
        ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
        ma200 = compute_ma(qqq_daily, qqq_dates, d, 200)
        rsi = compute_rsi(qqq_daily, qqq_dates, d, 14)
        day["above_ma50"] = entry > ma50 if ma50 else None
        day["below_ma50"] = entry < ma50 if ma50 else None
        day["above_ma200"] = entry > ma200 if ma200 else None
        day["below_ma200"] = entry < ma200 if ma200 else None
        day["rsi"] = rsi

        all_days.append(day)

    print(f"  Total days: {len(all_days)}")

    # Simulation function
    def sim(days, pt_pct=PT_PCT, sl_pct=SL_PCT, ts=TS_BARS, bullish_only=True):
        pnls = []
        for d in days:
            if bullish_only and not d["bullish"]: continue
            bars = d["bars"]
            entry = d["entry"]
            pt = entry * pt_pct / 100
            sl = entry * sl_pct / 100
            pnl_pct = 0
            for bi in range(1, min(ts + 1, len(bars))):
                bar = bars[bi]
                if bar["low"] <= entry - sl:
                    pnl_pct = -sl_pct; break
                if bar["high"] >= entry + pt:
                    pnl_pct = pt_pct; break
            else:
                idx = min(ts, len(bars) - 1)
                pnl_pct = (bars[idx]["close"] - entry) / entry * 100
            pnls.append(pnl_pct)
        if len(pnls) < 20: return None
        total = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        gw = sum(p for p in pnls if p > 0)
        gl = sum(abs(p) for p in pnls if p <= 0)
        pf = gw / gl if gl > 0 else 999
        sd = stdev(pnls) if len(pnls) > 1 else 1
        sharpe = mean(pnls) / sd if sd > 0 else 0
        cum = peak = max_dd = 0
        for p in pnls:
            cum += p; peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return {"n": len(pnls), "wr": wr, "pf": pf, "sharpe": sharpe,
                "total": total, "max_dd": max_dd, "mean": mean(pnls)}

    # Baseline
    base = sim(all_days)
    print(f"\n  Baseline (bullish, no filters): N={base['n']} Sharpe={base['sharpe']:.3f} "
          f"WR={base['wr']:.1f}% PF={base['pf']:.2f} Total={base['total']:.2f}%")

    # ── Contrarian combos ──
    print(f"\n  --- CONTRARIAN COMBINED FILTERS ---")
    print(f"  {'Label':55s} {'N':>4s} {'Sharpe':>7s} {'WR':>5s} {'PF':>5s} {'Total':>8s} {'MaxDD':>6s}")
    print("  " + "-" * 95)

    combos = [
        # Single contrarian filters
        ("Below SMA50", lambda d: d.get("below_ma50") == True),
        ("Below SMA200", lambda d: d.get("below_ma200") == True),
        ("VIX >= 18", lambda d: d.get("vix") and d["vix"] >= 18),
        ("VIX >= 20", lambda d: d.get("vix") and d["vix"] >= 20),
        ("Prior day red", lambda d: d.get("prev_ret", 0) < 0),
        ("Prior big red (<-1%)", lambda d: d.get("prev_ret", 0) < -1),
        ("Gap down", lambda d: d.get("gap_pct", 0) < 0),
        ("Exclude Tuesday", lambda d: d["dow"] != "Tuesday"),
        ("RSI 40-60", lambda d: d.get("rsi") and 40 <= d["rsi"] <= 60),
        ("Prior range > 1.5%", lambda d: d.get("prev_range", 0) > 1.5),

        # Two-filter contrarian combos
        ("Below SMA50 + VIX >= 18", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18),
        ("Below SMA50 + VIX >= 20", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 20),
        ("Below SMA50 + prior red", lambda d: d.get("below_ma50") == True and d.get("prev_ret", 0) < 0),
        ("Below SMA50 + prior big red", lambda d: d.get("below_ma50") == True and d.get("prev_ret", 0) < -1),
        ("Below SMA50 + gap down", lambda d: d.get("below_ma50") == True and d.get("gap_pct", 0) < 0),
        ("Below SMA50 + no Tue", lambda d: d.get("below_ma50") == True and d["dow"] != "Tuesday"),
        ("Below SMA50 + RSI 40-60", lambda d: d.get("below_ma50") == True and d.get("rsi") and 40 <= d["rsi"] <= 60),
        ("Below SMA200 + VIX >= 18", lambda d: d.get("below_ma200") == True and d.get("vix") and d["vix"] >= 18),
        ("Below SMA200 + prior red", lambda d: d.get("below_ma200") == True and d.get("prev_ret", 0) < 0),
        ("Below SMA200 + gap down", lambda d: d.get("below_ma200") == True and d.get("gap_pct", 0) < 0),
        ("VIX >= 18 + prior red", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0),
        ("VIX >= 18 + prior big red", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < -1),
        ("VIX >= 18 + gap down", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("gap_pct", 0) < 0),
        ("VIX >= 20 + prior red", lambda d: d.get("vix") and d["vix"] >= 20 and d.get("prev_ret", 0) < 0),
        ("Prior red + gap down", lambda d: d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),
        ("Prior big red + gap down", lambda d: d.get("prev_ret", 0) < -1 and d.get("gap_pct", 0) < 0),
        ("Prior range > 1.5% + VIX >= 18", lambda d: d.get("prev_range", 0) > 1.5 and d.get("vix") and d["vix"] >= 18),

        # Three-filter contrarian combos
        ("Below SMA50 + VIX >= 18 + prior red", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0),
        ("Below SMA50 + VIX >= 18 + gap down", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d.get("gap_pct", 0) < 0),
        ("Below SMA50 + VIX >= 18 + no Tue", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d["dow"] != "Tuesday"),
        ("Below SMA50 + prior red + gap down", lambda d: d.get("below_ma50") == True and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),
        ("Below SMA50 + prior red + no Tue", lambda d: d.get("below_ma50") == True and d.get("prev_ret", 0) < 0 and d["dow"] != "Tuesday"),
        ("Below SMA50 + VIX >= 20 + prior red", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 20 and d.get("prev_ret", 0) < 0),
        ("Below SMA200 + VIX >= 18 + prior red", lambda d: d.get("below_ma200") == True and d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0),
        ("VIX >= 18 + prior red + gap down", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),
        ("VIX >= 18 + prior red + no Tue", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d["dow"] != "Tuesday"),
        ("VIX >= 20 + prior red + gap down", lambda d: d.get("vix") and d["vix"] >= 20 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),
        ("Prior range > 1.5% + VIX >= 18 + gap down", lambda d: d.get("prev_range", 0) > 1.5 and d.get("vix") and d["vix"] >= 18 and d.get("gap_pct", 0) < 0),

        # Four-filter combos
        ("Below SMA50 + VIX >= 18 + prior red + no Tue", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d["dow"] != "Tuesday"),
        ("Below SMA50 + VIX >= 18 + prior red + gap down", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),
        ("Below SMA50 + VIX >= 18 + gap down + no Tue", lambda d: d.get("below_ma50") == True and d.get("vix") and d["vix"] >= 18 and d.get("gap_pct", 0) < 0 and d["dow"] != "Tuesday"),
        ("Below SMA200 + VIX >= 18 + prior red + gap down", lambda d: d.get("below_ma200") == True and d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),

        # Also test: no MA filter (just vol + mean reversion signals)
        ("VIX >= 18 + prior red + gap down + no Tue", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0 and d["dow"] != "Tuesday"),
        ("VIX >= 18 + prior big red + no Tue", lambda d: d.get("vix") and d["vix"] >= 18 and d.get("prev_ret", 0) < -1 and d["dow"] != "Tuesday"),
        ("VIX 18-30 + prior red + gap down", lambda d: d.get("vix") and 18 <= d["vix"] <= 30 and d.get("prev_ret", 0) < 0 and d.get("gap_pct", 0) < 0),

        # Also test with SMA50 ABOVE (script 26's best) for comparison
        ("[MOMENTUM] Above SMA50 + VIX 16-30 + RSI 35-70", lambda d: d.get("above_ma50") == True and d.get("vix") and 16 <= d["vix"] <= 30 and d.get("rsi") and 35 <= d["rsi"] <= 70),
    ]

    results = []
    for label, filt in combos:
        filtered = [d for d in all_days if filt(d)]
        r = sim(filtered)
        if r:
            delta = r["sharpe"] - base["sharpe"]
            sign = "+" if delta >= 0 else ""
            print(f"  {label:55s} {r['n']:>4d} {r['sharpe']:>7.3f} {r['wr']:>5.1f} {r['pf']:>5.2f} {r['total']:>8.2f} {r['max_dd']:>6.3f}  ({sign}{delta:.3f})")
            results.append((r["sharpe"], r, label, filt))
        else:
            print(f"  {label:55s}   -- too few trades --")

    # Rank by Sharpe
    results.sort(key=lambda x: x[0], reverse=True)
    print(f"\n  {'='*80}")
    print(f"  TOP 15 CONTRARIAN COMBOS BY SHARPE")
    print(f"  {'='*80}")
    for i, (sh, r, label, _) in enumerate(results[:15]):
        print(f"  #{i+1:>2d}: Sharpe {sh:.3f} | {label}")
        print(f"        N={r['n']} WR={r['wr']:.1f}% PF={r['pf']:.2f} Total={r['total']:.2f}% MaxDD={r['max_dd']:.3f}%")

    # Also test different PT/SL on top combos
    print(f"\n  {'='*80}")
    print(f"  PT/SL SENSITIVITY ON TOP 3 COMBOS")
    print(f"  {'='*80}")

    pt_sl_variants = [
        (0.15, 0.10, 30), (0.20, 0.10, 30), (0.25, 0.10, 30), (0.30, 0.10, 30),
        (0.20, 0.15, 30), (0.20, 0.10, 60), (0.30, 0.15, 30), (0.30, 0.15, 60),
        (0.40, 0.10, 30), (0.20, 0.10, 15), (0.15, 0.10, 15),
    ]

    for rank_idx in range(min(3, len(results))):
        _, _, label, filt = results[rank_idx]
        filtered = [d for d in all_days if filt(d)]
        print(f"\n  Combo: {label}")
        print(f"  {'PT%':>5s} {'SL%':>5s} {'TS':>4s} | {'N':>4s} {'Sharpe':>7s} {'WR':>5s} {'PF':>5s} {'Total':>8s} {'MaxDD':>6s}")
        print("  " + "-" * 65)
        for pt, sl, ts in pt_sl_variants:
            r = sim(filtered, pt, sl, ts)
            if r:
                print(f"  {pt:>5.2f} {sl:>5.2f} {ts:>4d} | {r['n']:>4d} {r['sharpe']:>7.3f} {r['wr']:>5.1f} {r['pf']:>5.2f} {r['total']:>8.2f} {r['max_dd']:>6.3f}")

    return results, all_days


# ══════════════════════════════════════════════════════════════════════
# PHASE 2: Options backtest on best contrarian combo
# ══════════════════════════════════════════════════════════════════════

def simulate_options_trade(option_bars, qqq_bars, entry_open, pt, sl, ts,
                           risk, first_bar_bullish, entry_10s_price=None):
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

    # Exit logic using QQQ underlying bars
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
        if bars_held > ts: break

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
        ts_idx = min(len(qqq_bars) - 1, ts + 1)
        exit_time = qqq_bars[ts_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_idx

    # Get option exit price
    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        exit_option_price = None
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
    }


def run_phase2(results, all_days, qqq_daily, qqq_dates, qqq_intraday, vix_daily):
    ensure_cache()

    # Use the #1 contrarian combo from Phase 1
    # But also test the top 3 through options
    top_combos_to_test = min(5, len(results))

    print(f"\n\n{'='*80}")
    print(f"PHASE 2: OPTIONS BACKTEST ON TOP {top_combos_to_test} CONTRARIAN COMBOS")
    print(f"{'='*80}")

    intra_dates = sorted(qqq_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}

    best_options_sharpe = -999
    best_options_result = None
    best_options_trades = None
    best_label = None

    for combo_rank in range(top_combos_to_test):
        sh, r_linear, label, filt = results[combo_rank]
        print(f"\n  --- Testing #{combo_rank+1}: {label} (linear Sharpe={sh:.3f}) ---")

        # Get trade days for this combo
        filtered_days = [d for d in all_days if filt(d) and d["bullish"]]
        print(f"  Bullish filtered days: {len(filtered_days)}")

        # Risk sizing: flat for simplicity, scaled to contrarian conviction
        # Higher VIX / bigger selloff = more risk
        base_risk = 50000  # More conservative than SPX since QQQ options are cheap

        options_trades = []
        skipped = 0

        for i, day in enumerate(filtered_days):
            d = day["date"]
            entry = day["entry"]

            if (i + 1) % 50 == 0 or i == 0:
                print(f"    Processing {i+1}/{len(filtered_days)}: {d}...")

            # Contrarian risk scaling
            risk = base_risk
            vix = day.get("vix")
            prev_ret = day.get("prev_ret", 0)
            if vix and vix >= 25: risk = min(risk * 1.5, MAX_RISK)
            if prev_ret < -1: risk = min(risk * 1.3, MAX_RISK)
            risk = round(risk / 1000) * 1000

            # PT/SL from optimal analysis
            pt = entry * PT_PCT / 100
            sl = entry * SL_PCT / 100

            target_strike = round(entry)
            ticker, strike, expiry_date, dte = find_qqq_option_call(d, target_strike)
            if not ticker:
                skipped += 1; continue

            entry_10s = get_option_entry_price(ticker, d)
            opt_bars = get_option_bars(ticker, d)
            if not opt_bars:
                skipped += 1; continue

            qqq_bars = qqq_intraday.get(d, [])
            opt_result = simulate_options_trade(
                opt_bars, qqq_bars, entry, pt, sl, TS_BARS,
                risk, True, entry_10s_price=entry_10s
            )
            if not opt_result:
                skipped += 1; continue

            options_trades.append({
                "date": d,
                "day_of_week": day["dow"],
                "entry_open": entry,
                "vix": vix,
                "prev_ret": round(prev_ret, 3) if prev_ret else None,
                "gap_pct": round(day.get("gap_pct", 0), 3),
                "strike": strike,
                "option_ticker": ticker,
                "expiry_date": expiry_date,
                "dte": dte,
                "pt": round(pt, 2), "sl": round(sl, 2), "ts": TS_BARS,
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

        print(f"\n    OPTIONS RESULTS: {label}")
        print(f"    Trades: {len(options_trades)} (skipped {skipped})")
        print(f"    Total P&L: ${total_pnl:,.0f}")
        print(f"    WR: {wr:.1f}%  |  PF: {pf:.2f}")
        print(f"    Max DD: ${max_dd:,.0f}")
        print(f"    Sharpe: {sharpe:.2f}  |  Calmar: {calmar:.2f}")
        print(f"    Avg premium: ${mean([t['opt_premium'] for t in options_trades]):,.0f}")

        if sharpe > best_options_sharpe:
            best_options_sharpe = sharpe
            best_options_result = {
                "label": label, "n": len(options_trades), "total_pnl": total_pnl,
                "wr": wr, "pf": pf, "max_dd": max_dd, "sharpe": sharpe, "calmar": calmar,
            }
            best_options_trades = options_trades
            best_label = label

    # ── Final report for best combo ──
    if best_options_trades:
        print(f"\n\n{'='*80}")
        print(f"BEST CONTRARIAN COMBO: {best_label}")
        print(f"{'='*80}")
        r = best_options_result
        print(f"  Trades: {r['n']}")
        print(f"  Total P&L: ${r['total_pnl']:,.0f}")
        print(f"  Win Rate: {r['wr']:.1f}%")
        print(f"  Profit Factor: {r['pf']:.2f}")
        print(f"  Max Drawdown: ${r['max_dd']:,.0f}")
        print(f"  Sharpe Ratio: {r['sharpe']:.2f}")
        print(f"  Calmar Ratio: {r['calmar']:.2f}")

        # Compare to SPX
        print(f"\n  COMPARISON:")
        print(f"  {'Metric':20s} {'QQQ Contrarian':>16s} {'SPX Momentum':>16s}")
        print(f"  {'-'*55}")
        print(f"  {'Trades':20s} {r['n']:>16d} {'331':>16s}")
        print(f"  {'Total P&L':20s} {'${:,.0f}'.format(r['total_pnl']):>16s} {'$6,110,000':>16s}")
        print(f"  {'Win Rate':20s} {'{:.1f}%'.format(r['wr']):>16s} {'50.8%':>16s}")
        print(f"  {'Profit Factor':20s} {'{:.2f}'.format(r['pf']):>16s} {'2.16':>16s}")
        print(f"  {'Max Drawdown':20s} {'${:,.0f}'.format(r['max_dd']):>16s} {'$319,000':>16s}")
        print(f"  {'Sharpe':20s} {'{:.2f}'.format(r['sharpe']):>16s} {'1.41':>16s}")
        print(f"  {'Calmar':20s} {'{:.2f}'.format(r['calmar']):>16s} {'19.15':>16s}")

        # Yearly breakdown
        print(f"\n  YEARLY BREAKDOWN:")
        yearly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
        for t in best_options_trades:
            yr = t["date"][:4]
            yearly[yr]["pnl"] += t["opt_pnl"]
            yearly[yr]["count"] += 1
            if t["opt_pnl"] > 0: yearly[yr]["wins"] += 1
        cum = 0
        for yr in sorted(yearly.keys()):
            y = yearly[yr]
            cum += y["pnl"]
            ywr = y["wins"] / y["count"] * 100 if y["count"] else 0
            print(f"  {yr}: {y['count']:>3} trades, P&L ${y['pnl']:>10,.0f}, Cum ${cum:>12,.0f}, WR {ywr:.0f}%")

        # By exit reason
        print(f"\n  BY EXIT REASON:")
        by_reason = defaultdict(list)
        for t in best_options_trades:
            by_reason[t["opt_exit_reason"]].append(t["opt_pnl"])
        for reason in sorted(by_reason.keys()):
            rp = by_reason[reason]
            print(f"    {reason:25s}: {len(rp):>3} trades, avg ${mean(rp):>10,.0f}, WR {sum(1 for p in rp if p > 0)/len(rp)*100:.0f}%")

        # Save
        with open(OUTPUT_JSON, "w") as f:
            json.dump(best_options_trades, f, indent=2, default=str)
        print(f"\n  Trade data saved: {OUTPUT_JSON}")

    return best_options_result, best_options_trades


# ══════════════════════════════════════════════════════════════════════

def main():
    _report_file = open(OUTPUT_REPORT, "w")
    sys.stdout = Tee(sys.__stdout__, _report_file)

    print("Loading data...")
    qqq_daily, qqq_dates = load_daily_csv(QQQ_DAILY)
    qqq_intraday = load_intraday_csv(QQQ_1MIN)
    vix_daily, _ = load_daily_csv(VIX_DAILY)
    print(f"  QQQ daily: {len(qqq_daily)} | QQQ intraday: {len(qqq_intraday)} days | VIX: {len(vix_daily)}")

    # Phase 1: Find best contrarian filter combo (linear)
    results, all_days = run_phase1(qqq_daily, qqq_dates, qqq_intraday, vix_daily)

    # Phase 2: Run top combos through options backtest
    best_result, best_trades = run_phase2(results, all_days, qqq_daily, qqq_dates, qqq_intraday, vix_daily)

    print(f"\nReport saved to: {OUTPUT_REPORT}")
    _report_file.close()
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
