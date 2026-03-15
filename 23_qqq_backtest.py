"""
SPX Opening Print Strategy — Script 23: QQQ Backtest
=====================================================
Test the opening print strategy on QQQ (Nasdaq 100 ETF).

Approach:
- Fetch QQQ daily + 1-min data from Polygon (QQQ has data going back far)
- Use same VIX data (market-wide fear gauge)
- Adapt signals: use QQQ prices instead of SPX, scale PT/SL proportionally
- Run linear backtest (no options) for quick comparison
- Compare to SPX linear results on the same dates

QQQ trades at ~$500 vs SPX ~$5800, so PT/SL need scaling.
We use percentage-based PT/SL instead of fixed points.
"""

import os, csv, json, math, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from statistics import mean, stdev
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEY = os.environ.get("POLYGON_API_KEY", "")
BASE_URL = "https://api.polygon.io"

QQQ_DAILY_FILE = os.path.join(SCRIPT_DIR, "qqq_daily_bars.csv")
QQQ_1MIN_FILE = os.path.join(SCRIPT_DIR, "qqq_1min_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
TLT_DAILY_FILE = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")

# Filters (same as SPX strategy)
VIX_FILTER = 16
VIX_CAP = 30
BULLISH_ONLY = True
SMA50_FILTER = True
RSI_FILTER = (35, 70)

# ── Data fetching ────────────────────────────────────────────────────

def fetch_polygon(url):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Error: {e}")
        return None

def fetch_daily_bars(ticker, start, end, outfile):
    """Fetch daily bars from Polygon."""
    if os.path.exists(outfile):
        with open(outfile) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if len(rows) > 100:
                print(f"  {outfile} already exists ({len(rows)} rows), skipping fetch")
                return

    print(f"  Fetching {ticker} daily bars {start} to {end}...")
    all_bars = []
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={API_KEY}"
    data = fetch_polygon(url)
    if data and data.get("results"):
        for r in data["results"]:
            dt = datetime.fromtimestamp(r["t"] / 1000)
            all_bars.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
                "volume": r.get("v", 0)
            })

    with open(outfile, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(all_bars)
    print(f"  Saved {len(all_bars)} daily bars to {outfile}")

def fetch_1min_bars(ticker, start, end, outfile):
    """Fetch 1-min bars from Polygon in monthly chunks."""
    if os.path.exists(outfile):
        with open(outfile) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if len(rows) > 10000:
                print(f"  {outfile} already exists ({len(rows)} rows), skipping fetch")
                return

    print(f"  Fetching {ticker} 1-min bars {start} to {end}...")
    all_bars = []
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    while current < end_dt:
        chunk_end = min(current + timedelta(days=30), end_dt)
        s = current.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")

        url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{s}/{e}"
               f"?adjusted=true&sort=asc&limit=50000&apiKey={API_KEY}")
        data = fetch_polygon(url)
        if data and data.get("results"):
            for r in data["results"]:
                dt = datetime.fromtimestamp(r["t"] / 1000)
                bar_time = dt.strftime("%H:%M")
                if "09:30" <= bar_time <= "16:00":
                    all_bars.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "time": bar_time,
                        "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
                        "volume": r.get("v", 0)
                    })

        current = chunk_end + timedelta(days=1)
        time.sleep(0.15)
        if current.month != (current - timedelta(days=1)).month:
            print(f"    ... through {e} ({len(all_bars)} bars)")

    with open(outfile, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "time", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(all_bars)
    print(f"  Saved {len(all_bars)} 1-min bars to {outfile}")

# ── Data loading ─────────────────────────────────────────────────────

def load_daily(filepath):
    data = {}
    dates = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row["date"]
            data[d] = {k: float(row[k]) for k in ["open", "high", "low", "close"]}
            dates.append(d)
    return data, sorted(set(dates))

def load_intraday(filepath):
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
            idx = i
            break
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
            idx = i
            break
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

def evaluate_signals_qqq(d, bars, intra_dates, intra_idx, qqq_intraday,
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
    gap_pct = 0
    if idx > 0:
        prev_d = intra_dates[idx - 1]
        prev_bars = qqq_intraday.get(prev_d, [])
        if prev_bars:
            prev_close = prev_bars[-1]["close"]
            gap_pct = (entry_open - prev_close) / prev_close * 100
            gap_dir = "up" if gap_pct > 0 else "down"
            if gap_dir == "up":
                signals.append(f"Gap up ({gap_pct:+.2f}%)"); score += 10
            elif gap_dir == "down" and abs(gap_pct) > 0.5:  # ~0.5% = ~30 SPX pts equivalent
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

    if d in set(qqq_dates):
        ma50 = compute_ma(qqq_daily, qqq_dates, d, 50)
        ma200 = compute_ma(qqq_daily, qqq_dates, d, 200)
        ma10 = compute_ma(qqq_daily, qqq_dates, d, 10)
        ma20 = compute_ma(qqq_daily, qqq_dates, d, 20)
        above_all = all(entry_open > ma for ma in [ma10, ma20, ma50, ma200] if ma)
        below_all = all(entry_open < ma for ma in [ma10, ma20, ma50, ma200] if ma)
        if ma50:
            pct_from_50 = (entry_open - ma50) / ma50 * 100
            if -2 < pct_from_50 < 0: signals.append(f"Just below 50d MA"); score += 15
            elif pct_from_50 < -2: signals.append(f"Below 50d MA"); score += 10
            elif pct_from_50 > 5: signals.append(f"Far above 50d MA"); score -= 10
        if not above_all and not below_all:
            signals.append("Mixed MAs"); score += 8

    if d in set(qqq_dates):
        wd = dt.weekday()
        if wd > 0:
            mon_date = (dt - timedelta(days=wd)).strftime("%Y-%m-%d")
            if mon_date in qqq_daily:
                wtd_ret = (entry_open - qqq_daily[mon_date]["open"]) / qqq_daily[mon_date]["open"] * 100
                if wtd_ret < -1: signals.append(f"Deep red week"); score += 15
                elif wtd_ret < 0: signals.append(f"Red week"); score += 5
        month_start = dt.replace(day=1).strftime("%Y-%m-%d")
        for sd in qqq_dates:
            if sd >= month_start and sd[:7] == d[:7] and sd in qqq_daily:
                mtd_ret = (entry_open - qqq_daily[sd]["open"]) / qqq_daily[sd]["open"] * 100
                if mtd_ret < -1: signals.append(f"Red month"); score += 10
                break

    tlt_date_idx_map = {td: ti for ti, td in enumerate(tlt_dates)}
    if d in tlt_date_idx_map:
        tidx = tlt_date_idx_map[d]
        if tidx >= 5:
            tlt_5d_ago = tlt_daily.get(tlt_dates[tidx - 5])
            tlt_prev = tlt_daily.get(tlt_dates[tidx - 1])
            if tlt_5d_ago and tlt_prev:
                tlt_5d_ret = (tlt_prev["close"] - tlt_5d_ago["close"]) / tlt_5d_ago["close"] * 100
                if 0 < tlt_5d_ret < 1: signals.append(f"Bonds mildly up 5d"); score += 8

    # PT/SL/TS in percentage terms, converted to QQQ points
    # SPX: PT=50 on ~5800 = 0.86%. QQQ at ~500 = 4.3 pts
    # We use percentage-based approach
    signal_set = set(s.split(" (")[0] for s in signals)
    # Default: ~0.86% PT, ~0.17% SL
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

    pt = entry_open * pt_pct / 100  # Convert to QQQ points
    sl = entry_open * sl_pct / 100

    n_positive = len([s for s in signals if "negative" not in s.lower() and "bail" not in s.lower()])
    if n_positive < 1:
        return None

    return {
        "score": score, "signals": signals,
        "pt": pt, "sl": sl, "ts": ts,
        "entry_open": entry_open,
        "first_bar_bullish": first_bar_bullish,
        "dow": dt.strftime("%A"),
        "n_positive": n_positive,
        "vix": round(vix_daily[d]["open"], 1) if d in vix_daily else None,
    }

# ── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("QQQ OPENING PRINT BACKTEST")
    print("=" * 80)

    # Step 1: Fetch data
    print("\n--- Fetching Data ---")
    fetch_daily_bars("QQQ", "2018-01-01", "2026-03-10", QQQ_DAILY_FILE)
    time.sleep(0.2)
    fetch_1min_bars("QQQ", "2018-01-01", "2026-03-10", QQQ_1MIN_FILE)

    # Step 2: Load data
    print("\n--- Loading Data ---")
    qqq_daily, qqq_dates = load_daily(QQQ_DAILY_FILE)
    qqq_intraday = load_intraday(QQQ_1MIN_FILE)
    vix_daily_data, _ = load_daily(VIX_DAILY)
    tlt_daily_data, tlt_dates = load_daily(TLT_DAILY_FILE)

    intra_dates = sorted(qqq_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}
    print(f"  QQQ daily: {len(qqq_daily)} days")
    print(f"  QQQ intraday: {len(intra_dates)} days")
    print(f"  VIX daily: {len(vix_daily_data)} days")

    # Step 3: Evaluate signals
    print("\n--- Evaluating Signals ---")
    trade_days = []
    for d in intra_dates:
        bars = qqq_intraday.get(d, [])
        if not bars or len(bars) < 10:
            continue

        result = evaluate_signals_qqq(d, bars, intra_dates, intra_idx, qqq_intraday,
                                       vix_daily_data, qqq_daily, qqq_dates,
                                       tlt_daily_data, tlt_dates)
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

    print(f"  Trade days: {len(trade_days)}")

    # Step 4: Simulate linear trades
    print("\n--- Simulating Trades ---")
    RISK_PER_TRADE = 100000  # Fixed $100k risk per trade for comparison

    trades = []
    for d, sig in trade_days:
        bars = qqq_intraday[d]
        entry = sig["entry_open"]
        pt = sig["pt"]
        sl = sig["sl"]
        ts = sig["ts"]

        dollars_per_point = RISK_PER_TRADE / sl if sl > 0 else 0

        pnl_pts = 0
        exit_reason = "Time Stop"
        exit_time = ""
        for bi in range(1, min(ts + 1, len(bars))):
            bar = bars[bi]
            if bar["low"] <= entry - sl:
                pnl_pts = -sl
                exit_reason = "Stop Loss"
                exit_time = bar["time"]
                break
            if bar["high"] >= entry + pt:
                pnl_pts = pt
                exit_reason = "Profit Target"
                exit_time = bar["time"]
                break
        else:
            last_idx = min(ts, len(bars) - 1)
            pnl_pts = bars[last_idx]["close"] - entry
            exit_time = bars[last_idx]["time"]

        pnl_dollars = pnl_pts * dollars_per_point
        pnl_pct = pnl_pts / entry * 100

        trades.append({
            "date": d, "entry": entry, "score": sig["score"],
            "pt_pts": pt, "sl_pts": sl,
            "pnl_pts": round(pnl_pts, 2),
            "pnl_dollars": round(pnl_dollars, 2),
            "pnl_pct": round(pnl_pct, 3),
            "exit_reason": exit_reason,
            "exit_time": exit_time,
            "vix": sig["vix"],
            "dow": sig["dow"],
        })

    # Step 5: Stats
    print(f"\n{'='*80}")
    print("QQQ OPENING PRINT — LINEAR BACKTEST RESULTS")
    print(f"{'='*80}")

    if not trades:
        print("No trades!")
        return

    pnls = [t["pnl_dollars"] for t in trades]
    pnl_pcts = [t["pnl_pct"] for t in trades]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(pnls) * 100

    cum = peak = max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    days = (datetime.strptime(trades[-1]["date"], "%Y-%m-%d") -
            datetime.strptime(trades[0]["date"], "%Y-%m-%d")).days
    tpy = len(pnls) / (days / 365.25)
    sharpe = (mean(pnls) / stdev(pnls)) * math.sqrt(tpy) if stdev(pnls) > 0 else 0
    calmar = total / max_dd if max_dd > 0 else 999
    gw = sum(wins)
    gl = sum(abs(p) for p in losses)
    pf = gw / gl if gl > 0 else 999

    print(f"  Risk per trade: ${RISK_PER_TRADE:,}")
    print(f"  Date range: {trades[0]['date']} to {trades[-1]['date']}")
    print(f"  Trades: {len(trades)}  |  WR: {wr:.1f}%")
    print(f"  Total P&L: ${total:,.0f}")
    print(f"  Max Drawdown: ${max_dd:,.0f}")
    print(f"  Sharpe Ratio: {sharpe:.2f}")
    print(f"  Calmar Ratio: {calmar:.2f}")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Avg Win: ${mean(wins):,.0f}  |  Avg Loss: ${mean([abs(p) for p in losses]):,.0f}")
    print(f"  Best: ${max(pnls):,.0f}  |  Worst: ${min(pnls):,.0f}")

    # By exit reason
    print(f"\n  By exit reason:")
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["pnl_dollars"])
    for reason in sorted(by_reason.keys()):
        rp = by_reason[reason]
        rwr = sum(1 for p in rp if p > 0) / len(rp) * 100
        print(f"    {reason:20s}: {len(rp):>3} trades, avg ${mean(rp):>10,.0f}, WR {rwr:.0f}%")

    # Yearly
    print(f"\n  Yearly breakdown:")
    yearly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
    for t in trades:
        yr = t["date"][:4]
        yearly[yr]["pnl"] += t["pnl_dollars"]
        yearly[yr]["count"] += 1
        if t["pnl_dollars"] > 0: yearly[yr]["wins"] += 1
    cum = 0
    for yr in sorted(yearly.keys()):
        y = yearly[yr]
        cum += y["pnl"]
        ywr = y["wins"] / y["count"] * 100 if y["count"] else 0
        print(f"    {yr}: {y['count']:>3} trades, P&L ${y['pnl']:>10,.0f}, Cum ${cum:>12,.0f}, WR {ywr:.0f}%")

    # Compare to SPX on matching dates
    print(f"\n{'='*80}")
    print("COMPARISON: QQQ vs SPX (percentage returns on matching trade dates)")
    print(f"{'='*80}")
    print(f"\n  QQQ avg return per trade: {mean(pnl_pcts):.3f}%")
    print(f"  QQQ median return: {sorted(pnl_pcts)[len(pnl_pcts)//2]:.3f}%")
    print(f"  QQQ stdev of returns: {stdev(pnl_pcts):.3f}%")

    # Sharpe on percentage returns (more comparable)
    pct_sharpe = (mean(pnl_pcts) / stdev(pnl_pcts)) * math.sqrt(tpy) if stdev(pnl_pcts) > 0 else 0
    print(f"  QQQ Sharpe (pct-based): {pct_sharpe:.2f}")

if __name__ == "__main__":
    main()
