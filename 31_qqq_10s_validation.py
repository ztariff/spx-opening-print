"""
QQQ Opening Print — Script 31: 10-Second Resolution Validation
================================================================
Re-simulates all 539 QQQ trades using 10-second bars for BOTH the
underlying (QQQ) and the option, instead of 1-minute bars.

This validates whether the trailing stop exit pricing is realistic.

For each trade:
1. Fetch 10s QQQ underlying bars from Polygon (cached)
2. Fetch 10s option bars from Polygon (cached)
3. Re-run trailing stop logic at 10s resolution
4. Compare exit price/time/PnL to the 1-minute version

Usage:
    python3 31_qqq_10s_validation.py
"""

import os, csv, json, time, math, sys, urllib.request, urllib.error
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QQQ_TRADES_JSON = os.path.join(SCRIPT_DIR, "qqq_optimized_trades.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "qqq_options_cache")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "qqq_optimized_trades.json")  # overwrite with validated
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, "qqq_10s_validation_report.txt")

API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"
REQUEST_DELAY = 0.05

# Trailing stop params (from best config E in script 29)
TRAIL_PCT = 0.05
SL_PCT = 0.10
TS_BARS_MINS = 30  # time stop in minutes


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
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif e.code == 403:
                return None
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


def get_dst_offset(date_str):
    """Return ET offset from UTC (4 for DST, 5 for standard)."""
    dt_date = datetime.strptime(date_str, "%Y-%m-%d")
    year = dt_date.year
    mar1 = datetime(year, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = dst_start <= dt_date.replace(hour=12) < dst_end
    return 4 if is_dst else 5


def fetch_10s_bars(ticker, date_str, cache_prefix):
    """Fetch 10-second bars from Polygon. Returns list of bars with ET time."""
    safe_ticker = ticker.replace(':', '_').replace('/', '_')
    cache_key = f"{cache_prefix}_{date_str}_{safe_ticker}"
    cached = load_cache(cache_key)
    if cached is not None:
        return cached if cached != "none" else []

    offset_hours = get_dst_offset(date_str)

    all_bars = []
    # Polygon paginates 10s bars — may need multiple calls
    # 9:30 to 10:00 = 30 min = 180 bars at 10s each
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
        # Only keep bars from 9:30:00 to 10:05:00 (covers our 30-min window + buffer)
        if "09:30:00" <= t_str <= "10:05:00":
            all_bars.append({
                "time": t_str,
                "open": r["o"],
                "high": r["h"],
                "low": r["l"],
                "close": r["c"],
                "volume": r.get("v", 0),
            })

    # Handle pagination if needed
    while data and data.get("next_url"):
        next_url = data["next_url"] + f"&apiKey={API_KEY}"
        time.sleep(REQUEST_DELAY)
        data = api_get(next_url)
        if data and data.get("results"):
            for r in data["results"]:
                dt_utc = datetime.utcfromtimestamp(r["t"] / 1000)
                dt_et = dt_utc - timedelta(hours=offset_hours)
                t_str = dt_et.strftime("%H:%M:%S")
                if "09:30:00" <= t_str <= "10:05:00":
                    all_bars.append({
                        "time": t_str,
                        "open": r["o"],
                        "high": r["h"],
                        "low": r["l"],
                        "close": r["c"],
                        "volume": r.get("v", 0),
                    })

    all_bars.sort(key=lambda b: b["time"])
    save_cache(cache_key, all_bars if all_bars else "none")
    return all_bars


def simulate_trailing_10s(qqq_10s_bars, opt_10s_bars, entry_open, opt_entry_price,
                          sl_pct, trail_pct, ts_minutes, risk):
    """Simulate trailing stop using 10-second bars on both underlying and option.

    Returns dict with exit details, or None if can't simulate.
    """
    if not qqq_10s_bars or not opt_10s_bars or len(qqq_10s_bars) < 2:
        return None

    # Build option price lookup: time → bar
    opt_map = {}
    for bar in opt_10s_bars:
        opt_map[bar["time"]] = bar

    # Entry setup
    sl = entry_open * sl_pct / 100
    peak_price = entry_open

    contract_cost = opt_entry_price * 100
    num_contracts = max(1, int(risk / contract_cost))
    total_premium = num_contracts * contract_cost

    exit_time = None
    exit_reason = None
    exit_qqq_price = None
    started = False

    # Time stop: 30 min from 9:31 = 10:01
    ts_limit = "10:{:02d}:00".format(1 + (ts_minutes - 30) if ts_minutes > 30 else ts_minutes)
    # Actually compute properly: entry at 9:31, +30 min = 10:01
    entry_min = 9 * 60 + 31
    stop_min = entry_min + ts_minutes
    stop_h = stop_min // 60
    stop_m = stop_min % 60
    ts_limit_str = f"{stop_h:02d}:{stop_m:02d}:00"

    for bar in qqq_10s_bars:
        # Skip bars at or before entry (9:31:00 is end of first bar, start after)
        if bar["time"] <= "09:31:00":
            if bar["time"] >= "09:31:00":
                started = True
            continue
        if not started:
            started = True

        # Time stop check
        if bar["time"] >= ts_limit_str:
            exit_time = bar["time"]
            exit_reason = "Time Stop"
            exit_qqq_price = bar["close"]
            break

        # Update trailing peak
        if bar["high"] > peak_price:
            peak_price = bar["high"]

        # Hard stop loss from entry
        if bar["low"] <= entry_open - sl:
            exit_time = bar["time"]
            exit_reason = "Stop Loss"
            exit_qqq_price = entry_open - sl  # assume fill at stop level
            break

        # Trailing stop
        trail_level = peak_price * (1 - trail_pct / 100)
        if bar["low"] <= trail_level and peak_price > entry_open:
            exit_time = bar["time"]
            exit_reason = "Trailing Stop"
            exit_qqq_price = trail_level  # approximate fill at trail level
            break

    if exit_time is None:
        # Fell through — use last available bar
        if qqq_10s_bars:
            last = qqq_10s_bars[-1]
            exit_time = last["time"]
            exit_reason = "Time Stop"
            exit_qqq_price = last["close"]
        else:
            return None

    # Get option exit price at exit time (or nearest bar after)
    opt_exit_price = None
    # Try exact match first
    if exit_time in opt_map:
        opt_exit_price = opt_map[exit_time]["close"]
    else:
        # Find nearest option bar at or after exit time
        for bar in opt_10s_bars:
            if bar["time"] >= exit_time:
                opt_exit_price = bar["close"]
                break
        # If still nothing, try nearest before
        if opt_exit_price is None:
            for bar in reversed(opt_10s_bars):
                if bar["time"] <= exit_time:
                    opt_exit_price = bar["close"]
                    break
        # Last resort: use last option bar
        if opt_exit_price is None:
            opt_exit_price = opt_10s_bars[-1]["close"]

    pnl_per_contract = (opt_exit_price - opt_entry_price) * 100
    pnl_dollars = pnl_per_contract * num_contracts

    # Compute hold time
    entry_secs = 9 * 3600 + 31 * 60  # 9:31:00
    exit_parts = exit_time.split(":")
    exit_secs = int(exit_parts[0]) * 3600 + int(exit_parts[1]) * 60 + int(exit_parts[2])
    hold_secs = exit_secs - entry_secs
    hold_mins = max(1, round(hold_secs / 60))

    return {
        "opt_entry_price": opt_entry_price,
        "opt_exit_price": opt_exit_price,
        "num_contracts": num_contracts,
        "total_premium": total_premium,
        "pnl_dollars": round(pnl_dollars, 2),
        "exit_reason": exit_reason,
        "exit_time": exit_time[:5],  # HH:MM for compat
        "exit_time_10s": exit_time,   # Full HH:MM:SS
        "hold_mins": hold_mins,
        "hold_secs": hold_secs,
        "peak_qqq": round(peak_price, 4),
        "exit_qqq_price": round(exit_qqq_price, 4) if exit_qqq_price else None,
        "n_opt_10s_bars": len(opt_10s_bars),
        "n_qqq_10s_bars": len(qqq_10s_bars),
    }


def main():
    ensure_cache()

    log_f = open(OUTPUT_REPORT, "w")
    tee = Tee(sys.stdout, log_f)
    old_stdout = sys.stdout
    sys.stdout = tee

    print("=" * 80)
    print("QQQ Opening Print — 10-Second Resolution Validation")
    print("=" * 80)

    # Load existing 1-min trades
    with open(QQQ_TRADES_JSON, "r") as f:
        trades_1m = json.load(f)
    print(f"\nLoaded {len(trades_1m)} trades from 1-minute backtest")
    print(f"Trailing stop params: trail={TRAIL_PCT}%, SL={SL_PCT}%, TS={TS_BARS_MINS}min")

    # Process each trade
    results_10s = []
    comparisons = []
    api_calls = 0
    cached_hits = 0
    failed = 0

    for i, trade in enumerate(trades_1m):
        d = trade["date"]
        ticker = trade["option_ticker"]
        entry_open = trade["entry_open"]
        risk = trade["risk"]

        # Get entry price (use existing — already from 10s data)
        opt_entry_price = trade["opt_entry_price"]

        # Progress
        if (i + 1) % 50 == 0 or i == 0:
            print(f"\n  Processing trade {i+1}/{len(trades_1m)} ({d})...")

        # Fetch 10s QQQ underlying bars
        qqq_cache_key = f"qqq10s_{d}_QQQ"
        qqq_cached = load_cache(qqq_cache_key)
        if qqq_cached is not None:
            qqq_10s = qqq_cached if qqq_cached != "none" else []
            cached_hits += 1
        else:
            qqq_10s = fetch_10s_bars("QQQ", d, "qqq10s")
            api_calls += 1

        # Fetch 10s option bars
        safe_ticker = ticker.replace(':', '_').replace('/', '_')
        opt_cache_key = f"opt10s_{d}_{safe_ticker}"
        opt_cached = load_cache(opt_cache_key)
        if opt_cached is not None:
            opt_10s = opt_cached if opt_cached != "none" else []
            cached_hits += 1
        else:
            opt_10s = fetch_10s_bars(ticker, d, "opt10s")
            api_calls += 1

        if not qqq_10s or not opt_10s:
            # Can't validate — keep original
            results_10s.append(trade)
            failed += 1
            continue

        # Simulate at 10s resolution
        result = simulate_trailing_10s(
            qqq_10s, opt_10s, entry_open, opt_entry_price,
            SL_PCT, TRAIL_PCT, TS_BARS_MINS, risk
        )

        if not result:
            results_10s.append(trade)
            failed += 1
            continue

        # Compare to 1-min result
        old_pnl = trade["opt_pnl"]
        new_pnl = result["pnl_dollars"]
        old_exit = trade["opt_exit_price"]
        new_exit = result["opt_exit_price"]
        old_time = trade["opt_exit_time"]
        new_time = result["exit_time"]
        old_reason = trade["opt_exit_reason"]
        new_reason = result["exit_reason"]

        comparisons.append({
            "date": d,
            "old_pnl": old_pnl,
            "new_pnl": new_pnl,
            "diff": new_pnl - old_pnl,
            "old_exit_price": old_exit,
            "new_exit_price": new_exit,
            "old_exit_time": old_time,
            "new_exit_time": new_time,
            "old_reason": old_reason,
            "new_reason": new_reason,
            "contracts": result["num_contracts"],
            "n_opt_bars": result["n_opt_10s_bars"],
            "n_qqq_bars": result["n_qqq_10s_bars"],
        })

        # Build updated trade record
        updated = dict(trade)
        updated["opt_exit_price"] = result["opt_exit_price"]
        updated["opt_pnl"] = result["pnl_dollars"]
        updated["opt_exit_reason"] = result["exit_reason"]
        updated["opt_exit_time"] = result["exit_time"]
        updated["opt_hold_mins"] = result["hold_mins"]
        results_10s.append(updated)

    # ── Analysis ──────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"VALIDATION RESULTS")
    print(f"{'='*80}")
    print(f"  Trades validated: {len(comparisons)} / {len(trades_1m)}")
    print(f"  Failed (no 10s data): {failed}")
    print(f"  API calls made: {api_calls}")
    print(f"  Cache hits: {cached_hits}")

    if comparisons:
        diffs = [c["diff"] for c in comparisons]
        abs_diffs = [abs(d) for d in diffs]

        old_total = sum(c["old_pnl"] for c in comparisons)
        new_total = sum(c["new_pnl"] for c in comparisons)

        print(f"\n  1-Minute Total P&L:  ${old_total:,.0f}")
        print(f"  10-Second Total P&L: ${new_total:,.0f}")
        print(f"  Difference:          ${new_total - old_total:,.0f} ({(new_total - old_total) / abs(old_total) * 100:+.1f}%)")

        print(f"\n  Per-Trade Differences:")
        print(f"    Mean diff:    ${mean(diffs):,.0f}")
        print(f"    Median diff:  ${sorted(diffs)[len(diffs)//2]:,.0f}")
        print(f"    Std diff:     ${stdev(diffs):,.0f}")
        print(f"    Mean |diff|:  ${mean(abs_diffs):,.0f}")
        print(f"    Max better:   ${max(diffs):,.0f}")
        print(f"    Max worse:    ${min(diffs):,.0f}")

        # Trades where exit reason changed
        reason_changed = [c for c in comparisons if c["old_reason"] != c["new_reason"]]
        print(f"\n  Exit reason changed: {len(reason_changed)} / {len(comparisons)}")
        if reason_changed:
            for c in reason_changed[:10]:
                print(f"    {c['date']}: {c['old_reason']} → {c['new_reason']}  (PnL: ${c['old_pnl']:,.0f} → ${c['new_pnl']:,.0f})")

        # Time difference
        time_same = sum(1 for c in comparisons if c["old_exit_time"] == c["new_exit_time"])
        print(f"\n  Same exit minute: {time_same} / {len(comparisons)} ({time_same/len(comparisons)*100:.1f}%)")

        # Biggest PnL differences
        sorted_by_diff = sorted(comparisons, key=lambda c: c["diff"])
        print(f"\n  Top 10 Worst Changes (10s worse than 1m):")
        for c in sorted_by_diff[:10]:
            print(f"    {c['date']}: ${c['old_pnl']:>8,.0f} → ${c['new_pnl']:>8,.0f}  (${c['diff']:>+8,.0f})  exit: ${c['old_exit_price']:.2f}→${c['new_exit_price']:.2f}  {c['old_reason']}→{c['new_reason']}")

        print(f"\n  Top 10 Best Changes (10s better than 1m):")
        for c in sorted_by_diff[-10:]:
            print(f"    {c['date']}: ${c['old_pnl']:>8,.0f} → ${c['new_pnl']:>8,.0f}  (${c['diff']:>+8,.0f})  exit: ${c['old_exit_price']:.2f}→${c['new_exit_price']:.2f}  {c['old_reason']}→{c['new_reason']}")

        # New overall stats
        new_pnls = [t["opt_pnl"] for t in results_10s]
        total_pnl = sum(new_pnls)
        wins = sum(1 for p in new_pnls if p > 0)
        wr = wins / len(new_pnls) * 100
        gw = sum(p for p in new_pnls if p > 0)
        gl = sum(abs(p) for p in new_pnls if p <= 0)
        pf = gw / gl if gl > 0 else 999

        cum = peak = max_dd = 0
        for p in new_pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        dates_span = (datetime.strptime(results_10s[-1]["date"], "%Y-%m-%d") -
                      datetime.strptime(results_10s[0]["date"], "%Y-%m-%d")).days
        tpy = len(new_pnls) / (dates_span / 365.25) if dates_span > 0 else len(new_pnls)
        sharpe = (mean(new_pnls) / stdev(new_pnls)) * math.sqrt(tpy) if len(new_pnls) > 1 and stdev(new_pnls) > 0 else 0
        calmar = total_pnl / max_dd if max_dd > 0 else 999

        print(f"\n\n{'='*80}")
        print(f"FINAL 10-SECOND VALIDATED STATS")
        print(f"{'='*80}")
        print(f"  Trades:  {len(results_10s)}")
        print(f"  P&L:     ${total_pnl:,.0f}")
        print(f"  WR:      {wr:.1f}%")
        print(f"  PF:      {pf:.2f}")
        print(f"  Max DD:  ${max_dd:,.0f}")
        print(f"  Sharpe:  {sharpe:.2f}")
        print(f"  Calmar:  {calmar:.2f}")

        # 10s bar coverage stats
        opt_bar_counts = [c["n_opt_bars"] for c in comparisons]
        qqq_bar_counts = [c["n_qqq_bars"] for c in comparisons]
        print(f"\n  10s Bar Coverage:")
        print(f"    QQQ bars/trade: min={min(qqq_bar_counts)}, avg={mean(qqq_bar_counts):.0f}, max={max(qqq_bar_counts)}")
        print(f"    Opt bars/trade: min={min(opt_bar_counts)}, avg={mean(opt_bar_counts):.0f}, max={max(opt_bar_counts)}")

    # Save validated trades
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results_10s, f, indent=2)
    print(f"\n  Saved {len(results_10s)} validated trades to {OUTPUT_JSON}")

    sys.stdout = old_stdout
    log_f.close()
    print(f"\nDone! Report saved to {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
