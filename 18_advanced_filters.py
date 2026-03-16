"""
Script 18: Test Advanced Filters (MAs, RSI, regime detection)
=============================================================
Tests whether daily technicals add value on top of the current
VIX 16-30 + Bullish Only baseline.

Usage:
    python3 18_advanced_filters.py
"""

import os, json, csv, math
from collections import defaultdict
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = os.path.join(SCRIPT_DIR, "options_trades.json")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")


def load_daily(filepath):
    data = {}
    with open(filepath) as f:
        for row in csv.DictReader(f):
            try:
                data[row["date"]] = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            except (ValueError, KeyError):
                continue
    return data


def compute_sma(daily, dates, target_date, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period:
        return None
    closes = [daily[dates[j]]["close"] for j in range(idx - period, idx) if dates[j] in daily]
    if len(closes) < period * 0.8:
        return None
    return sum(closes) / len(closes)


def compute_ema(daily, dates, target_date, period):
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period:
        return None
    # Get closes for warmup
    start = max(0, idx - period * 3)  # extra warmup
    closes = [daily[dates[j]]["close"] for j in range(start, idx) if dates[j] in daily]
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema


def compute_rsi(daily, dates, target_date, period=14):
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period + 1:
        return None
    gains = []
    losses = []
    for j in range(idx - period, idx):
        if j < 1 or dates[j] not in daily or dates[j-1] not in daily:
            continue
        change = daily[dates[j]]["close"] - daily[dates[j-1]]["close"]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(daily, dates, target_date, period=14):
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period + 1:
        return None
    trs = []
    for j in range(idx - period, idx):
        if dates[j] not in daily or dates[j-1] not in daily:
            continue
        h = daily[dates[j]]["high"]
        l = daily[dates[j]]["low"]
        pc = daily[dates[j-1]]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return None
    return sum(trs) / len(trs)


def compute_stats(trades):
    if not trades:
        return None
    pnls = [t["opt_pnl"] for t in trades]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    cum = 0; peak = 0; max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    dr = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
          datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days / 365.25
    if dr < 0.5: dr = 0.5
    ann = total / dr
    calmar = ann / max_dd if max_dd > 0 else 999
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 999
    return {
        "trades": len(trades), "total_pnl": total,
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "max_dd": max_dd, "calmar": calmar, "pf": pf,
        "ann_return": ann,
    }


def ps(label, s):
    if not s: return
    print(f"  {label:55s}  N:{s['trades']:4d}  P&L:${s['total_pnl']:>10,.0f}  "
          f"WR:{s['wr']:5.1f}%  DD:${s['max_dd']:>9,.0f}  "
          f"Calmar:{s['calmar']:6.2f}  PF:{s['pf']:.2f}")


def main():
    trades = json.load(open(TRADES_FILE))
    spx_daily = load_daily(SPX_DAILY)
    vix_daily = load_daily(VIX_DAILY)
    tlt_daily = load_daily(TLT_DAILY)
    spx_dates = sorted(spx_daily.keys())
    vix_dates = sorted(vix_daily.keys())
    tlt_dates = sorted(tlt_daily.keys())

    print(f"Loaded {len(trades)} trades, {len(spx_dates)} SPX daily bars")

    # Pre-compute technicals for each trade date
    for t in trades:
        d = t["date"]
        t["sma20"] = compute_sma(spx_daily, spx_dates, d, 20)
        t["sma50"] = compute_sma(spx_daily, spx_dates, d, 50)
        t["sma200"] = compute_sma(spx_daily, spx_dates, d, 200)
        t["ema9"] = compute_ema(spx_daily, spx_dates, d, 9)
        t["ema21"] = compute_ema(spx_daily, spx_dates, d, 21)
        t["rsi14"] = compute_rsi(spx_daily, spx_dates, d, 14)
        t["rsi5"] = compute_rsi(spx_daily, spx_dates, d, 5)
        t["atr14"] = compute_atr(spx_daily, spx_dates, d, 14)

        # VIX RSI
        t["vix_rsi14"] = compute_rsi(vix_daily, vix_dates, d, 14)

        # TLT momentum
        t["tlt_sma20"] = compute_sma(tlt_daily, tlt_dates, d, 20)
        tlt_today = tlt_daily.get(d)
        t["tlt_above_sma20"] = (tlt_today["close"] > t["tlt_sma20"]) if tlt_today and t["tlt_sma20"] else None

        # Price vs MAs
        entry = t["entry_open"]
        t["above_sma20"] = entry > t["sma20"] if t["sma20"] else None
        t["above_sma50"] = entry > t["sma50"] if t["sma50"] else None
        t["above_sma200"] = entry > t["sma200"] if t["sma200"] else None

        # Distance from 50 SMA
        if t["sma50"]:
            t["pct_from_50"] = (entry - t["sma50"]) / t["sma50"] * 100
        else:
            t["pct_from_50"] = None

    # Baseline
    print("\n" + "=" * 120)
    print("BASELINE (current: VIX 16-30, Bullish only)")
    print("=" * 120)
    ps("Baseline", compute_stats(trades))

    # ── MA FILTERS ──
    print("\n" + "=" * 120)
    print("MOVING AVERAGE FILTERS")
    print("=" * 120)

    # Price vs SMA
    for ma_name, key in [("SMA20", "above_sma20"), ("SMA50", "above_sma50"), ("SMA200", "above_sma200")]:
        above = [t for t in trades if t[key] == True]
        below = [t for t in trades if t[key] == False]
        ps(f"Above {ma_name}", compute_stats(above))
        ps(f"Below {ma_name}", compute_stats(below))

    # EMA crossover
    ema_bull = [t for t in trades if t["ema9"] and t["ema21"] and t["ema9"] > t["ema21"]]
    ema_bear = [t for t in trades if t["ema9"] and t["ema21"] and t["ema9"] <= t["ema21"]]
    ps("EMA9 > EMA21 (bullish cross)", compute_stats(ema_bull))
    ps("EMA9 <= EMA21 (bearish cross)", compute_stats(ema_bear))

    # Distance from 50 SMA
    print("\n  --- Distance from 50 SMA ---")
    for lo, hi, label in [(-999, -5, "Far below (-5%+)"), (-5, -2, "Below (-5 to -2%)"),
                           (-2, 0, "Slightly below (0 to -2%)"), (0, 2, "Slightly above (0 to +2%)"),
                           (2, 5, "Above (+2 to +5%)"), (5, 999, "Far above (+5%+)")]:
        bucket = [t for t in trades if t["pct_from_50"] is not None and lo <= t["pct_from_50"] < hi]
        ps(f"  50 SMA dist {label}", compute_stats(bucket))

    # ── RSI FILTERS ──
    print("\n" + "=" * 120)
    print("RSI FILTERS")
    print("=" * 120)

    # RSI 14 buckets
    for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 100)]:
        bucket = [t for t in trades if t["rsi14"] is not None and lo <= t["rsi14"] < hi]
        ps(f"RSI14 {lo}-{hi}", compute_stats(bucket))

    # RSI 5 (short-term)
    print("\n  --- RSI 5 (short-term) ---")
    for lo, hi in [(0, 20), (20, 35), (35, 50), (50, 65), (65, 80), (80, 100)]:
        bucket = [t for t in trades if t["rsi5"] is not None and lo <= t["rsi5"] < hi]
        ps(f"RSI5 {lo}-{hi}", compute_stats(bucket))

    # RSI thresholds as filters
    print("\n  --- RSI as trade filter ---")
    for rsi_min in [30, 35, 40, 45]:
        filtered = [t for t in trades if t["rsi14"] is not None and t["rsi14"] >= rsi_min]
        ps(f"RSI14 >= {rsi_min}", compute_stats(filtered))
    for rsi_max in [65, 60, 55, 70]:
        filtered = [t for t in trades if t["rsi14"] is not None and t["rsi14"] <= rsi_max]
        ps(f"RSI14 <= {rsi_max}", compute_stats(filtered))
    # RSI band
    for lo, hi in [(30, 65), (35, 65), (30, 70), (35, 70), (40, 70)]:
        filtered = [t for t in trades if t["rsi14"] is not None and lo <= t["rsi14"] <= hi]
        ps(f"RSI14 {lo}-{hi}", compute_stats(filtered))

    # ── VIX RSI ──
    print("\n" + "=" * 120)
    print("VIX RSI FILTERS")
    print("=" * 120)
    for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 100)]:
        bucket = [t for t in trades if t["vix_rsi14"] is not None and lo <= t["vix_rsi14"] < hi]
        ps(f"VIX RSI14 {lo}-{hi}", compute_stats(bucket))

    # VIX RSI as filter
    for vix_rsi_max in [60, 55, 50, 65, 70]:
        filtered = [t for t in trades if t["vix_rsi14"] is not None and t["vix_rsi14"] <= vix_rsi_max]
        ps(f"VIX RSI14 <= {vix_rsi_max} (vol declining)", compute_stats(filtered))

    # ── TLT FILTERS ──
    print("\n" + "=" * 120)
    print("TLT (BOND) FILTERS")
    print("=" * 120)
    tlt_above = [t for t in trades if t["tlt_above_sma20"] == True]
    tlt_below = [t for t in trades if t["tlt_above_sma20"] == False]
    ps("TLT above SMA20 (bonds rising)", compute_stats(tlt_above))
    ps("TLT below SMA20 (bonds falling)", compute_stats(tlt_below))

    # ── REGIME FILTERS ──
    print("\n" + "=" * 120)
    print("REGIME FILTERS (combinations)")
    print("=" * 120)

    combos = [
        ("Above SMA50 only",
         lambda t: t["above_sma50"] == True),
        ("Below SMA50 only",
         lambda t: t["above_sma50"] == False),
        ("Above SMA200 + RSI14 >= 40",
         lambda t: t["above_sma200"] == True and t["rsi14"] is not None and t["rsi14"] >= 40),
        ("Above SMA50 + RSI14 35-70",
         lambda t: t["above_sma50"] == True and t["rsi14"] is not None and 35 <= t["rsi14"] <= 70),
        ("Below SMA50 + RSI14 < 40 (oversold pullback)",
         lambda t: t["above_sma50"] == False and t["rsi14"] is not None and t["rsi14"] < 40),
        ("Above SMA200 + VIX RSI <= 60",
         lambda t: t["above_sma200"] == True and t["vix_rsi14"] is not None and t["vix_rsi14"] <= 60),
        ("EMA9 > EMA21 + RSI14 >= 40",
         lambda t: t["ema9"] and t["ema21"] and t["ema9"] > t["ema21"] and t["rsi14"] is not None and t["rsi14"] >= 40),
        ("Above SMA50 + TLT above SMA20",
         lambda t: t["above_sma50"] == True and t["tlt_above_sma20"] == True),
        ("RSI14 30-65 + VIX RSI <= 60",
         lambda t: t["rsi14"] is not None and 30 <= t["rsi14"] <= 65 and t["vix_rsi14"] is not None and t["vix_rsi14"] <= 60),
        ("Above SMA200 only",
         lambda t: t["above_sma200"] == True),
        ("Below SMA200 only",
         lambda t: t["above_sma200"] == False),
        ("Within 3% of SMA50 (mean reversion zone)",
         lambda t: t["pct_from_50"] is not None and -3 <= t["pct_from_50"] <= 3),
        ("Below SMA50 but above SMA200 (pullback in uptrend)",
         lambda t: t["above_sma50"] == False and t["above_sma200"] == True),
        ("Above SMA20 + Above SMA50 (strong trend)",
         lambda t: t["above_sma20"] == True and t["above_sma50"] == True),
        ("Skip if RSI14 > 70 (overbought)",
         lambda t: t["rsi14"] is None or t["rsi14"] <= 70),
        ("Skip if RSI14 < 30 (deep oversold)",
         lambda t: t["rsi14"] is None or t["rsi14"] >= 30),
    ]

    results = []
    for label, fn in combos:
        filtered = [t for t in trades if fn(t)]
        stats = compute_stats(filtered)
        if stats and stats["trades"] >= 50:
            results.append((label, stats))
            ps(label, stats)

    # ── RANKING ──
    print("\n" + "=" * 120)
    print("TOP 10 BY CALMAR (min 100 trades)")
    print("=" * 120)
    ranked = [(l, s) for l, s in results if s["trades"] >= 100]
    ranked.sort(key=lambda x: x[1]["calmar"], reverse=True)
    for i, (label, stats) in enumerate(ranked[:10]):
        ps(f"#{i+1} {label}", stats)

    print("\n" + "=" * 120)
    print("TOP 10 BY P&L (min 100 trades)")
    print("=" * 120)
    ranked_pnl = [(l, s) for l, s in results if s["trades"] >= 100]
    ranked_pnl.sort(key=lambda x: x[1]["total_pnl"], reverse=True)
    for i, (label, stats) in enumerate(ranked_pnl[:10]):
        ps(f"#{i+1} {label}", stats)


if __name__ == "__main__":
    main()
