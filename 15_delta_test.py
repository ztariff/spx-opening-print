"""
Script 15: Delta / Strike Offset Comparison
Tests different option deltas (strike offsets from ATM) to find optimal entry.

Offsets tested (points from ATM, rounded to $5):
  ITM:  -20, -10
  ATM:  0 (current baseline)
  OTM:  +10, +20, +30, +40, +50

For each offset, we:
  1. Construct the option ticker at that strike
  2. Fetch 10-second bars for entry price + 1-min bars for simulation
  3. Run the same trade simulation logic
  4. Compare P&L, WR, drawdown, Calmar across all deltas
"""

import os, sys, json, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "options_cache")
API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

HYBRID_THRESHOLD = 25
VIX_FILTER = 16

# Strike offsets to test (points from ATM)
# Fewer offsets + skip 10s entry to reduce API calls
OFFSETS = [-20, -10, 0, 10, 20, 30, 50]


# ── Cache & API helpers (same as script 12) ──────────────────────────

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
        except Exception:
            time.sleep(2)
    return None


# ── DST helper ───────────────────────────────────────────────────────

def get_et_offset(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return 4 if dst_start <= dt.replace(hour=12) < dst_end else 5


# ── Option data fetchers ─────────────────────────────────────────────

def construct_option_ticker(date_str, target_strike):
    """Construct option ticker directly without API verification.
    We know from ATM backtest that SPXW works for virtually all dates.
    Returns (ticker, strike)."""
    strike = round(target_strike / 5) * 5
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_code = dt.strftime("%y%m%d")
    strike_code = f"{int(strike * 1000):08d}"
    ticker = f"O:SPXW{date_code}C{strike_code}"
    return ticker, strike


def get_entry_price_10s(ticker, date_str):
    """Get option price at ~9:30:10 from 10-second bars."""
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

    offset_hours = get_et_offset(date_str)
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
    """Get 1-min bars for option."""
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

    offset_hours = get_et_offset(date_str)
    bars = []
    for r in data.get("results", []):
        dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
        dt_et = dt_utc - timedelta(hours=offset_hours)
        time_str = dt_et.strftime("%H:%M")
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


# ── Premium sizing (same as script 12) ───────────────────────────────

MIN_RISK = 25000

def get_max_premium(score):
    if score < 25:   return 150000
    elif score < 40: return 155000
    elif score < 55: return 170000
    elif score < 70: return 175000
    elif score < 85: return 200000
    else:            return 200000


# ── Trade simulation ─────────────────────────────────────────────────

def simulate_trade(option_bars, spx_bars, entry_open, pt, sl, ts,
                   score, first_bar_bullish, risk, entry_10s_price=None):
    """Simulate an options trade. Returns dict or None."""
    if not option_bars or len(option_bars) < 2:
        return None

    # Entry price
    if entry_10s_price and entry_10s_price > 0.10:
        option_entry_price = entry_10s_price
    else:
        entry_bar = None
        for bar in option_bars:
            if bar["time"] >= "09:30":
                entry_bar = bar
                break
        if entry_bar is None:
            entry_bar = option_bars[0]
        option_entry_price = entry_bar["close"]
    if option_entry_price <= 0.10:
        return None

    # Position sizing
    contract_cost = option_entry_price * 100
    num_contracts = int(risk / contract_cost)
    if num_contracts < 1:
        num_contracts = 1
    total_premium = num_contracts * contract_cost

    # Option price map by time
    option_price_map = {bar["time"]: bar for bar in option_bars}

    # Approach C logic
    if score >= HYBRID_THRESHOLD:
        pass  # Hold regardless of 1st bar
    else:
        if not first_bar_bullish:
            return None

    # Find exit using SPX bars
    exit_time = None
    exit_reason = None
    hold_mins = 0

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

        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss (SPX)"
            hold_mins = bars_held
            break
        if bar["high"] >= entry_open + pt:
            exit_time = bar["time"]
            exit_reason = "Profit Target (SPX)"
            hold_mins = bars_held
            break

    if exit_time is None:
        ts_target_idx = min(len(spx_bars) - 1, ts + 1)
        exit_time = spx_bars[ts_target_idx]["time"]
        exit_reason = "Time Stop"
        hold_mins = ts_target_idx

    # Get option price at exit
    exit_option_price = None
    if exit_time in option_price_map:
        exit_option_price = option_price_map[exit_time]["close"]
    else:
        for bar in option_bars:
            if bar["time"] >= exit_time:
                exit_option_price = bar["close"]
                break
        if exit_option_price is None:
            exit_option_price = option_bars[-1]["close"]

    pnl = num_contracts * (exit_option_price - option_entry_price) * 100

    return {
        "pnl": round(pnl, 2),
        "entry_price": option_entry_price,
        "exit_price": exit_option_price,
        "contracts": num_contracts,
        "premium": round(total_premium, 2),
        "exit_reason": exit_reason,
        "hold_mins": hold_mins,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    ensure_cache()

    # Load the existing trade data for ATM baseline (dates, signals, SPX bars)
    with open(os.path.join(SCRIPT_DIR, "options_trades.json")) as f:
        atm_trades = json.load(f)

    print(f"Loaded {len(atm_trades)} ATM trades (VIX >= {VIX_FILTER} filtered)")
    print(f"Testing offsets: {OFFSETS}")
    print()

    # We need SPX intraday bars for simulation
    # Load from the same source as script 12
    import csv
    SPX_INTRADAY = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
    spx_intraday = {}
    with open(SPX_INTRADAY, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row["date"]
            if d not in spx_intraday:
                spx_intraday[d] = []
            spx_intraday[d].append({
                "time": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })

    # Results by offset - process all offsets per day in one pass
    trades_by_offset = {o: [] for o in OFFSETS}
    skipped_by_offset = {o: 0 for o in OFFSETS}

    non_atm_offsets = [o for o in OFFSETS if o != 0]

    for i, atm in enumerate(atm_trades):
        d = atm["date"]
        score = atm["score"]
        risk = atm["risk"]
        entry_open = atm["entry_open"]
        first_bar_bullish = atm["first_bar_bullish"]
        pt = atm["pt"]
        sl = atm["sl"]
        ts = atm["ts"]

        # ATM (offset 0): reuse existing data
        trades_by_offset[0].append({
            "date": d,
            "pnl": atm["opt_pnl"],
            "premium": atm["opt_premium"],
            "contracts": atm["opt_contracts"],
            "entry_price": atm["opt_entry_price"],
            "exit_price": atm["opt_exit_price"],
            "exit_reason": atm["opt_exit_reason"],
            "hold_mins": atm["opt_hold_mins"],
            "score": score,
        })

        # SPX bars for this day (shared across offsets)
        spx_bars = spx_intraday.get(d, [])
        if not spx_bars:
            for o in non_atm_offsets:
                skipped_by_offset[o] += 1
            continue

        # Compute risk (shared across offsets)
        max_prem = get_max_premium(score)
        n_pos = len([s for s in atm.get("signals", [])
                    if "negative" not in s.lower() and "bail" not in s.lower()])
        clamped = max(0, min(score, 80))
        r = MIN_RISK + (max_prem - MIN_RISK) * (clamped / 80)
        if n_pos >= 5: r = min(r * 1.3, max_prem)
        elif n_pos >= 3: r = min(r * 1.15, max_prem)
        r = max(MIN_RISK, min(max_prem, round(r / 1000) * 1000))

        # Process each non-ATM offset
        for offset in non_atm_offsets:
            target_strike = entry_open + offset

            ticker, actual_strike = construct_option_ticker(d, target_strike)
            if not ticker:
                skipped_by_offset[offset] += 1
                continue

            # Only 1 API call per offset/day: get bars (skip 10s entry to save API quota)
            opt_bars = get_option_bars(ticker, d)
            if not opt_bars:
                skipped_by_offset[offset] += 1
                continue

            result = simulate_trade(
                opt_bars, spx_bars, entry_open, pt, sl, ts,
                score, first_bar_bullish, r, None  # Use 1st bar close for entry
            )

            if result:
                result["date"] = d
                result["score"] = score
                trades_by_offset[offset].append(result)
            else:
                skipped_by_offset[offset] += 1

        if (i + 1) % 25 == 0:
            counts = " | ".join(f"{o:+d}:{len(trades_by_offset[o])}" for o in OFFSETS)
            print(f"  Day {i+1}/{len(atm_trades)}: {counts}")

    for offset in OFFSETS:
        print(f"  Offset {offset:+d}: {len(trades_by_offset[offset])} trades, {skipped_by_offset[offset]} skipped")

    all_results = {}
    for offset in OFFSETS:
        trades = trades_by_offset[offset]
        label = f"{'ITM' if offset < 0 else 'OTM' if offset > 0 else 'ATM'} {offset:+d}"

        # Compute stats
        if not trades:
            all_results[offset] = None
            continue

        total_pnl = sum(t["pnl"] for t in trades)
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        wr = len(wins) / len(trades) * 100
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

        cum = 0; peak = 0; max_dd = 0
        for t in trades:
            cum += t["pnl"]
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        gross_wins = sum(t["pnl"] for t in wins) if wins else 0
        gross_losses = abs(sum(t["pnl"] for t in losses)) if losses else 1
        pf = gross_wins / gross_losses

        days = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
                datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
        years = days / 365.25 if days > 0 else 1
        annual = total_pnl / years
        calmar = annual / max_dd if max_dd > 0 else 999

        worst = min(t["pnl"] for t in trades)
        best = max(t["pnl"] for t in trades)
        avg_premium = sum(t["premium"] for t in trades) / len(trades)

        # Monthly
        monthly = defaultdict(float)
        for t in trades:
            monthly[t["date"][:7]] += t["pnl"]
        pos_m = len([v for v in monthly.values() if v > 0])
        neg_m = len([v for v in monthly.values() if v <= 0])

        all_results[offset] = {
            "label": label,
            "offset": offset,
            "trades": len(trades),
            "wr": wr,
            "pnl": total_pnl,
            "avg_trade": total_pnl / len(trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best": best,
            "worst": worst,
            "max_dd": max_dd,
            "pf": pf,
            "calmar": calmar,
            "annual": annual,
            "avg_premium": avg_premium,
            "pos_months": pos_m,
            "neg_months": neg_m,
        }

    # ── Summary ──────────────────────────────────────────────────────
    print("\n\n")
    print("=" * 120)
    print("DELTA / STRIKE OFFSET COMPARISON")
    print("=" * 120)
    print(f"\n{'Offset':<12} {'Label':<10} {'Trades':>6} {'WR':>6} {'Total P&L':>12} {'Avg/Trade':>10} "
          f"{'MaxDD':>10} {'PF':>5} {'Calmar':>7} {'Annual':>11} {'AvgPrem':>10} {'Best':>10} {'Worst':>10} {'Mo+/-':>7}")
    print("-" * 140)

    for offset in OFFSETS:
        r = all_results.get(offset)
        if not r:
            print(f"{offset:>+4} pts     {'N/A':<10} -- no data --")
            continue
        marker = " <-- CURRENT" if offset == 0 else ""
        print(f"{offset:>+4} pts     {r['label']:<10} {r['trades']:>6} {r['wr']:>5.1f}% ${r['pnl']:>11,.0f} "
              f"${r['avg_trade']:>9,.0f} ${r['max_dd']:>9,.0f} {r['pf']:>5.2f} "
              f"{r['calmar']:>7.2f} ${r['annual']:>10,.0f} ${r['avg_premium']:>9,.0f} "
              f"${r['best']:>9,.0f} ${r['worst']:>9,.0f} {r['pos_months']:>3}/{r['neg_months']:<3}{marker}")

    # Best by calmar
    valid = {k: v for k, v in all_results.items() if v is not None}
    best_calmar = max(valid.values(), key=lambda r: r["calmar"])
    best_pnl = max(valid.values(), key=lambda r: r["pnl"])

    print(f"\nBest by Calmar: {best_calmar['label']} ({best_calmar['calmar']:.2f})")
    print(f"Best by P&L:    {best_pnl['label']} (${best_pnl['pnl']:,.0f})")

    # Save results
    report_path = os.path.join(SCRIPT_DIR, "delta_comparison_report.txt")
    with open(report_path, "w") as f:
        f.write("DELTA / STRIKE OFFSET COMPARISON\n")
        f.write("=" * 80 + "\n\n")
        for offset in OFFSETS:
            r = all_results.get(offset)
            if not r:
                continue
            f.write(f"{r['label']} (offset {offset:+d} pts):\n")
            f.write(f"  Trades: {r['trades']}  WR: {r['wr']:.1f}%  PF: {r['pf']:.2f}\n")
            f.write(f"  Total P&L: ${r['pnl']:,.0f}  Annual: ${r['annual']:,.0f}\n")
            f.write(f"  Max DD: ${r['max_dd']:,.0f}  Calmar: {r['calmar']:.2f}\n")
            f.write(f"  Avg Win: ${r['avg_win']:,.0f}  Avg Loss: ${r['avg_loss']:,.0f}\n")
            f.write(f"  Best: ${r['best']:,.0f}  Worst: ${r['worst']:,.0f}\n")
            f.write(f"  Avg Premium: ${r['avg_premium']:,.0f}\n")
            f.write(f"  Months +/-: {r['pos_months']}/{r['neg_months']}\n\n")
        f.write(f"Best by Calmar: {best_calmar['label']} ({best_calmar['calmar']:.2f})\n")
        f.write(f"Best by P&L:    {best_pnl['label']} (${best_pnl['pnl']:,.0f})\n")

    print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    main()
