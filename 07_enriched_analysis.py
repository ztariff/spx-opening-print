"""
SPX Opening Print Strategy — Phase 7: Enriched Multi-Factor Analysis
======================================================================
Combines ALL available data to determine when buying the opening print
has the most edge. Every factor is observable at or before 9:30 AM ET.

NEW FACTORS (on top of Phase 3/5):
  - VIX level at 9:30 (bucketed)
  - VIX change from prior close (rising/falling vol)
  - VIX term structure proxy (level vs 20d avg)
  - SPX vs 10-day, 20-day, 50-day, 200-day moving averages
  - SPX above/below all key MAs (trend alignment)
  - Weekly return coming in (red/green week so far)
  - Monthly return coming in (red/green month so far)
  - Yearly return coming in (red/green year so far)
  - TLT daily direction (bonds up = risk-off? or rate cut hope?)
  - TLT 5-day trend (bonds trending)
  - 10Y yield level and direction
  - Gap size in points (bucketed more granularly)
  - Combo scoring system

Requires:
  spx_1min_bars.csv     (from Phase 1)
  vix_1min_bars.csv     (from Phase 6)
  spx_daily_bars.csv    (from Phase 6)
  tlt_daily_bars.csv    (from Phase 6)
  us10y_daily_bars.csv  (from Phase 6)

Usage:
    python 07_enriched_analysis.py

Output:
    enriched_report.txt
    enriched_factors.csv       (per-day data with all factors)
    enriched_edge_summary.csv  (condition → stats)
"""

import os
import csv
from collections import defaultdict
from statistics import mean, median, stdev
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_1MIN = os.path.join(SCRIPT_DIR, "spx_1min_bars.csv")
VIX_DAILY = os.path.join(SCRIPT_DIR, "vix_daily_bars.csv")
SPX_DAILY = os.path.join(SCRIPT_DIR, "spx_daily_bars.csv")
TLT_DAILY = os.path.join(SCRIPT_DIR, "tlt_daily_bars.csv")
US10Y_DAILY = os.path.join(SCRIPT_DIR, "us10y_daily_bars.csv")

REPORT_FILE = os.path.join(SCRIPT_DIR, "enriched_report.txt")
FACTORS_CSV = os.path.join(SCRIPT_DIR, "enriched_factors.csv")
EDGE_CSV = os.path.join(SCRIPT_DIR, "enriched_edge_summary.csv")

PROFIT_TARGETS = [5, 10, 15, 20, 30, 50]
STOP_LOSSES = [2, 5, 8, 10, 15, 20]
TIME_STOPS = [15, 30, 60, 120, 240, 390]

report_lines = []


def p(line=""):
    report_lines.append(line)
    print(line)


# ── Data Loaders ────────────────────────────────────────────────────────

def load_spx_intraday():
    """Load SPX 1-min bars grouped by date (RTH only)."""
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


def load_vix_open():
    """Load VIX open and prior close from daily bars.

    Uses VIX daily OHLC data:
      - 'open' = VIX level at market open (what you see at 9:30)
      - prior day's 'close' = VIX close from yesterday (known pre-market)
    """
    if not os.path.exists(VIX_DAILY):
        print(f"WARNING: {VIX_DAILY} not found, skipping VIX factors")
        return {}, {}

    # Load all daily VIX bars
    vix_data = {}
    with open(VIX_DAILY, "r") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            try:
                vix_data[d] = {
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                }
            except (ValueError, KeyError):
                continue

    # Build open map (VIX at 9:30) and prior-close map
    vix_open = {}
    vix_prev = {}
    dates = sorted(vix_data.keys())
    for i, d in enumerate(dates):
        vix_open[d] = vix_data[d]["open"]
        if i > 0:
            vix_prev[d] = vix_data[dates[i - 1]]["close"]

    print(f"  VIX: {len(vix_open)} open readings, {len(vix_prev)} prior-close readings")
    return vix_open, vix_prev


def load_daily_csv(filepath):
    """Load daily OHLC from a CSV file. Returns dict date → {open, high, low, close}."""
    data = {}
    if not os.path.exists(filepath):
        print(f"WARNING: {filepath} not found")
        return data
    with open(filepath, "r") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            try:
                data[d] = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            except (ValueError, KeyError):
                continue
    return data


def compute_ma(daily_data, dates, target_date, period):
    """Compute simple moving average of close prices for N days ending before target_date."""
    idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            idx = i
            break
    if idx is None or idx < period:
        return None
    closes = []
    for j in range(idx - period, idx):
        if dates[j] in daily_data:
            closes.append(daily_data[dates[j]]["close"])
    if len(closes) < period * 0.8:  # Allow some missing days
        return None
    return mean(closes)


# ── Trade Simulation ───────────────────────────────────────────────────

def simulate_trade(bars, entry_price, pt, sl, ts, entry_idx=0):
    for i in range(entry_idx + 1, min(entry_idx + ts + 1, len(bars))):
        bar = bars[i]
        if bar["low"] <= entry_price - sl:
            return (-sl, "sl", i - entry_idx)
        if bar["high"] >= entry_price + pt:
            return (pt, "pt", i - entry_idx)
    last = min(entry_idx + ts, len(bars) - 1)
    return (bars[last]["close"] - entry_price, "ts", last - entry_idx)


def quick_grid(days_list):
    """Quick grid search, returns best by avg P&L."""
    best = None
    for pt in PROFIT_TARGETS:
        for sl in STOP_LOSSES:
            for ts in TIME_STOPS:
                pnls = []
                for day in days_list:
                    pnl, _, _ = simulate_trade(day["bars"], day["entry_open"], pt, sl, ts)
                    pnls.append(pnl)
                if not pnls:
                    continue
                avg = mean(pnls)
                wins = [x for x in pnls if x > 0]
                losses = [x for x in pnls if x <= 0]
                gw = sum(wins) if wins else 0
                gl = abs(sum(losses)) if losses else 0.01
                result = {
                    "pt": pt, "sl": sl, "ts": ts,
                    "n": len(pnls), "wr": round(len(wins)/len(pnls)*100, 1),
                    "avg": round(avg, 2), "pf": round(gw/gl, 2),
                    "total": round(sum(pnls), 1),
                }
                if best is None or avg > best["avg"]:
                    best = result
    return best


# ── Main ───────────────────────────────────────────────────────────────

def main():
    p("Loading all data sources...")
    spx_intraday = load_spx_intraday()
    vix_open, vix_prev = load_vix_open()
    spx_daily = load_daily_csv(SPX_DAILY)
    tlt_daily = load_daily_csv(TLT_DAILY)
    us10y_daily = load_daily_csv(US10Y_DAILY)

    p(f"  SPX intraday days: {len(spx_intraday)}")
    p(f"  VIX open readings: {len(vix_open)}")
    p(f"  SPX daily bars:    {len(spx_daily)}")
    p(f"  TLT daily bars:    {len(tlt_daily)}")
    p(f"  US10Y daily bars:  {len(us10y_daily)}")

    spx_dates = sorted(spx_daily.keys())
    spx_date_idx = {d: i for i, d in enumerate(spx_dates)}
    tlt_dates = sorted(tlt_daily.keys())
    tlt_date_idx = {d: i for i, d in enumerate(tlt_dates)}

    # ── Build enriched day records ─────────────────────────────────────
    p("\nBuilding enriched day records...")
    intra_dates = sorted(spx_intraday.keys())
    intra_idx = {d: i for i, d in enumerate(intra_dates)}

    enriched = []

    for d in intra_dates:
        bars = spx_intraday[d]
        if len(bars) < 10:
            continue

        dt = datetime.strptime(d, "%Y-%m-%d")
        idx = intra_idx[d]
        rec = {"date": d, "bars": bars}

        rec["entry_open"] = bars[0]["open"]
        rec["entry_1min"] = bars[0]["close"]
        rec["eod_pnl"] = bars[-1]["close"] - bars[0]["open"]
        rec["day_of_week"] = dt.strftime("%A")

        # First bar
        if bars[0]["close"] > bars[0]["open"]:
            rec["first_bar"] = "bullish"
        elif bars[0]["close"] < bars[0]["open"]:
            rec["first_bar"] = "bearish"
        else:
            rec["first_bar"] = "doji"

        # ── Gap (prior intraday close → today open) ────────────────
        if idx > 0:
            prev_d = intra_dates[idx - 1]
            prev_bars = spx_intraday.get(prev_d, [])
            if prev_bars:
                prev_close = prev_bars[-1]["close"]
                gap = bars[0]["open"] - prev_close
                gap_pct = gap / prev_close * 100
                rec["gap_pts"] = round(gap, 2)
                rec["gap_pct"] = round(gap_pct, 4)
                rec["gap_dir"] = "up" if gap > 0 else "down"

                abs_gap = abs(gap)
                if abs_gap < 3:
                    rec["gap_bucket"] = "tiny (<3pts)"
                elif abs_gap < 8:
                    rec["gap_bucket"] = "small (3-8pts)"
                elif abs_gap < 15:
                    rec["gap_bucket"] = "medium (8-15pts)"
                elif abs_gap < 30:
                    rec["gap_bucket"] = "large (15-30pts)"
                elif abs_gap < 50:
                    rec["gap_bucket"] = "very large (30-50pts)"
                else:
                    rec["gap_bucket"] = "huge (>50pts)"

                rec["gap_dir_bucket"] = f"{rec['gap_dir']} {rec['gap_bucket']}"

                # Prior day return
                prev_open = prev_bars[0]["open"]
                prev_ret = (prev_close - prev_open) / prev_open * 100
                rec["prev_day_dir"] = "up" if prev_ret > 0.1 else "down" if prev_ret < -0.1 else "flat"

        # ── VIX at 9:30 ────────────────────────────────────────────
        if d in vix_open:
            vix_val = vix_open[d]
            rec["vix_open"] = round(vix_val, 2)

            if vix_val < 13:
                rec["vix_bucket"] = "very low (<13)"
            elif vix_val < 16:
                rec["vix_bucket"] = "low (13-16)"
            elif vix_val < 20:
                rec["vix_bucket"] = "normal (16-20)"
            elif vix_val < 25:
                rec["vix_bucket"] = "elevated (20-25)"
            elif vix_val < 30:
                rec["vix_bucket"] = "high (25-30)"
            else:
                rec["vix_bucket"] = "very high (>30)"

            # VIX change from prior close
            if d in vix_prev and vix_prev[d]:
                vix_chg = vix_val - vix_prev[d]
                vix_chg_pct = vix_chg / vix_prev[d] * 100
                rec["vix_change"] = round(vix_chg, 2)
                rec["vix_change_pct"] = round(vix_chg_pct, 2)
                if vix_chg_pct > 5:
                    rec["vix_move"] = "vol spiking (>5%)"
                elif vix_chg_pct > 1:
                    rec["vix_move"] = "vol rising (1-5%)"
                elif vix_chg_pct > -1:
                    rec["vix_move"] = "vol flat"
                elif vix_chg_pct > -5:
                    rec["vix_move"] = "vol falling (1-5%)"
                else:
                    rec["vix_move"] = "vol crushing (>5%)"

        # ── Moving Averages (computed from SPX daily) ──────────────
        if d in spx_date_idx:
            today_open = bars[0]["open"]
            for period in [10, 20, 50, 200]:
                ma = compute_ma(spx_daily, spx_dates, d, period)
                if ma:
                    rec[f"ma_{period}"] = round(ma, 2)
                    rec[f"above_ma_{period}"] = today_open > ma
                    pct_from = (today_open - ma) / ma * 100
                    rec[f"pct_from_ma_{period}"] = round(pct_from, 2)

            # Trend alignment: above all major MAs?
            above_all = all(rec.get(f"above_ma_{p}", False) for p in [10, 20, 50, 200])
            below_all = all(not rec.get(f"above_ma_{p}", True) for p in [10, 20, 50, 200])
            rec["ma_alignment"] = "all above" if above_all else "all below" if below_all else "mixed"

            # Price vs 50d MA buckets
            pct_50 = rec.get("pct_from_ma_50")
            if pct_50 is not None:
                if pct_50 > 5:
                    rec["ma50_position"] = "far above 50d (>5%)"
                elif pct_50 > 2:
                    rec["ma50_position"] = "above 50d (2-5%)"
                elif pct_50 > 0:
                    rec["ma50_position"] = "just above 50d (0-2%)"
                elif pct_50 > -2:
                    rec["ma50_position"] = "just below 50d (0-2%)"
                elif pct_50 > -5:
                    rec["ma50_position"] = "below 50d (2-5%)"
                else:
                    rec["ma50_position"] = "far below 50d (>5%)"

            # Price vs 200d MA buckets
            pct_200 = rec.get("pct_from_ma_200")
            if pct_200 is not None:
                if pct_200 > 10:
                    rec["ma200_position"] = "far above 200d (>10%)"
                elif pct_200 > 5:
                    rec["ma200_position"] = "above 200d (5-10%)"
                elif pct_200 > 0:
                    rec["ma200_position"] = "near 200d above (0-5%)"
                elif pct_200 > -5:
                    rec["ma200_position"] = "near 200d below (0-5%)"
                else:
                    rec["ma200_position"] = "far below 200d (>5%)"

        # ── Weekly / Monthly / Yearly returns coming in ────────────
        if d in spx_date_idx:
            sidx = spx_date_idx[d]

            # Week-to-date: from Monday's open to today's open
            wd = dt.weekday()  # 0=Mon
            if wd > 0 and sidx >= wd:
                # Find this week's Monday
                mon_date = (dt - timedelta(days=wd)).strftime("%Y-%m-%d")
                if mon_date in spx_daily:
                    week_start = spx_daily[mon_date]["open"]
                    wtd_ret = (bars[0]["open"] - week_start) / week_start * 100
                    rec["wtd_return"] = round(wtd_ret, 4)
                    rec["week_color"] = "green" if wtd_ret > 0.05 else "red" if wtd_ret < -0.05 else "flat"

                    if wtd_ret > 1:
                        rec["wtd_bucket"] = "strong green (>1%)"
                    elif wtd_ret > 0:
                        rec["wtd_bucket"] = "green (0-1%)"
                    elif wtd_ret > -1:
                        rec["wtd_bucket"] = "red (0-1%)"
                    else:
                        rec["wtd_bucket"] = "deep red (>1%)"

            # Month-to-date: from 1st trading day of month to today's open
            month_start_date = dt.replace(day=1).strftime("%Y-%m-%d")
            # Find first trading day of month
            for sd in spx_dates:
                if sd >= month_start_date and sd[:7] == d[:7]:
                    if sd in spx_daily:
                        mtd_start = spx_daily[sd]["open"]
                        mtd_ret = (bars[0]["open"] - mtd_start) / mtd_start * 100
                        rec["mtd_return"] = round(mtd_ret, 4)
                        rec["month_color"] = "green" if mtd_ret > 0.1 else "red" if mtd_ret < -0.1 else "flat"

                        if mtd_ret > 3:
                            rec["mtd_bucket"] = "strong green (>3%)"
                        elif mtd_ret > 1:
                            rec["mtd_bucket"] = "green (1-3%)"
                        elif mtd_ret > 0:
                            rec["mtd_bucket"] = "slight green (0-1%)"
                        elif mtd_ret > -1:
                            rec["mtd_bucket"] = "slight red (0-1%)"
                        elif mtd_ret > -3:
                            rec["mtd_bucket"] = "red (1-3%)"
                        else:
                            rec["mtd_bucket"] = "deep red (>3%)"
                    break

            # Year-to-date: from Jan 2 (or first trading day) to today
            year_start = f"{dt.year}-01-01"
            for sd in spx_dates:
                if sd >= year_start and sd[:4] == d[:4]:
                    if sd in spx_daily:
                        ytd_start = spx_daily[sd]["open"]
                        ytd_ret = (bars[0]["open"] - ytd_start) / ytd_start * 100
                        rec["ytd_return"] = round(ytd_ret, 4)
                        rec["year_color"] = "green" if ytd_ret > 0.5 else "red" if ytd_ret < -0.5 else "flat"
                    break

        # ── TLT / Bonds ────────────────────────────────────────────
        if d in tlt_date_idx:
            tidx = tlt_date_idx[d]
            if tidx > 0:
                prev_tlt_d = tlt_dates[tidx - 1]
                if prev_tlt_d in tlt_daily:
                    tlt_prev_close = tlt_daily[prev_tlt_d]["close"]
                    tlt_today_open = tlt_daily[d]["open"] if d in tlt_daily else None

                    # Prior day TLT direction
                    tlt_prev = tlt_daily[prev_tlt_d]
                    tlt_prev_ret = (tlt_prev["close"] - tlt_prev["open"]) / tlt_prev["open"] * 100
                    rec["tlt_prev_dir"] = "up" if tlt_prev_ret > 0.1 else "down" if tlt_prev_ret < -0.1 else "flat"

                    # TLT 5-day trend
                    if tidx >= 5:
                        tlt_5d_ago = tlt_daily.get(tlt_dates[tidx - 5])
                        if tlt_5d_ago:
                            tlt_5d_ret = (tlt_prev_close - tlt_5d_ago["close"]) / tlt_5d_ago["close"] * 100
                            rec["tlt_5d_ret"] = round(tlt_5d_ret, 2)
                            if tlt_5d_ret > 1:
                                rec["tlt_5d_trend"] = "bonds rallying (>1%)"
                            elif tlt_5d_ret > 0:
                                rec["tlt_5d_trend"] = "bonds up (0-1%)"
                            elif tlt_5d_ret > -1:
                                rec["tlt_5d_trend"] = "bonds down (0-1%)"
                            else:
                                rec["tlt_5d_trend"] = "bonds selling (>1%)"

        # ── 10Y Yield ──────────────────────────────────────────────
        if d in us10y_daily:
            rec["us10y"] = us10y_daily[d]["close"]
            if rec["us10y"] < 3.5:
                rec["yield_bucket"] = "low (<3.5%)"
            elif rec["us10y"] < 4.0:
                rec["yield_bucket"] = "moderate (3.5-4%)"
            elif rec["us10y"] < 4.5:
                rec["yield_bucket"] = "elevated (4-4.5%)"
            elif rec["us10y"] < 5.0:
                rec["yield_bucket"] = "high (4.5-5%)"
            else:
                rec["yield_bucket"] = "very high (>5%)"

        # ── Streak (same as before) ────────────────────────────────
        streak = 0
        for j in range(idx - 1, max(idx - 15, -1), -1):
            if j < 0:
                break
            sd = intra_dates[j]
            sb = spx_intraday.get(sd, [])
            if sb and len(sb) >= 10:
                day_ret = sb[-1]["close"] - sb[0]["open"]
                if streak == 0:
                    streak = 1 if day_ret > 0 else -1
                elif streak > 0 and day_ret > 0:
                    streak += 1
                elif streak < 0 and day_ret < 0:
                    streak -= 1
                else:
                    break
        rec["streak"] = streak
        rec["streak_3plus_down"] = streak <= -3

        enriched.append(rec)

    p(f"Enriched {len(enriched)} trading days\n")

    # ── Analyze each factor ────────────────────────────────────────────
    def groupby(data, key):
        groups = defaultdict(list)
        for row in data:
            if key in row and row[key] is not None:
                groups[row[key]].append(row)
        return groups

    def analyze_condition(groups, condition_name, min_n=20):
        """Analyze and print stats for a condition, return edge rows."""
        p(f"\n{'='*80}")
        p(f"CONDITION: {condition_name}")
        p(f"{'='*80}")
        p(f"  {'Group':<35s} {'N':>5s} {'WR%':>7s} {'AvgPnL':>9s} {'MedPnL':>9s} {'StdDev':>8s}")
        p(f"  {'-'*35} {'-'*5} {'-'*7} {'-'*9} {'-'*9} {'-'*8}")

        rows = []
        for gname in sorted(groups.keys()):
            vals = [r["eod_pnl"] for r in groups[gname]]
            n = len(vals)
            if n < min_n:
                continue
            wr = sum(1 for v in vals if v > 0) / n * 100
            avg = mean(vals)
            med = median(vals)
            sd = stdev(vals) if n > 1 else 0
            p(f"  {str(gname):<35s} {n:>5d} {wr:>6.1f}% {avg:>+9.2f} {med:>+9.2f} {sd:>8.2f}")
            rows.append({
                "condition": condition_name, "group": gname,
                "n": n, "wr": round(wr, 1), "avg": round(avg, 2),
                "median": round(med, 2), "stdev": round(sd, 2),
            })
        return rows

    p("=" * 80)
    p("SPX OPENING PRINT — ENRICHED MULTI-FACTOR ANALYSIS")
    p("=" * 80)
    p(f"Total days: {len(enriched)}")

    all_edge_rows = []

    # ── VIX ─────────────────────────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "vix_bucket"), "VIX Level at Open"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "vix_move"), "VIX Change (prior close → open)"))

    # ── Moving Averages ─────────────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "ma_alignment"), "MA Alignment (all MAs)"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "ma50_position"), "Price vs 50-Day MA"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "ma200_position"), "Price vs 200-Day MA"))

    for period in [10, 20, 50, 200]:
        key = f"above_ma_{period}"
        groups = groupby(enriched, key)
        renamed = {}
        for k, v in groups.items():
            renamed[f"{'above' if k else 'below'} {period}d MA"] = v
        all_edge_rows.extend(analyze_condition(renamed, f"Above/Below {period}-Day MA"))

    # ── Weekly / Monthly / Yearly ───────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "week_color"), "Week-to-Date Color"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "wtd_bucket"), "Week-to-Date Return Bucket"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "month_color"), "Month-to-Date Color"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "mtd_bucket"), "Month-to-Date Return Bucket"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "year_color"), "Year-to-Date Color"))

    # ── Gap (more granular) ─────────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "gap_bucket"), "Gap Size (absolute)"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "gap_dir_bucket"), "Gap Direction + Size"))

    # ── Bonds ───────────────────────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "tlt_prev_dir"), "TLT Prior Day Direction"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "tlt_5d_trend"), "TLT 5-Day Trend"))
    all_edge_rows.extend(analyze_condition(groupby(enriched, "yield_bucket"), "10-Year Yield Level"))

    # ── First bar (confirm) ─────────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "first_bar"), "First Bar Direction (confirm)"))

    # ── Day of week (confirm) ──────────────────────────────────────────
    all_edge_rows.extend(analyze_condition(groupby(enriched, "day_of_week"), "Day of Week (confirm)"))

    # ══════════════════════════════════════════════════════════════════════
    # GRID SEARCH ON BEST NEW FACTOR COMBOS
    # ══════════════════════════════════════════════════════════════════════
    p(f"\n\n{'='*80}")
    p("GRID SEARCH: NEW FACTOR COMBINATIONS")
    p(f"{'='*80}")

    combo_filters = {
        "Bullish 1st bar + VIX elevated (20-25)": lambda r: r.get("first_bar") == "bullish" and r.get("vix_bucket") == "elevated (20-25)",
        "Bullish 1st bar + VIX high (25-30)": lambda r: r.get("first_bar") == "bullish" and r.get("vix_bucket") == "high (25-30)",
        "Bullish 1st bar + VIX very high (>30)": lambda r: r.get("first_bar") == "bullish" and r.get("vix_bucket") == "very high (>30)",
        "Bullish 1st bar + vol crushing": lambda r: r.get("first_bar") == "bullish" and r.get("vix_move") == "vol crushing (>5%)",
        "Bullish 1st bar + vol falling": lambda r: r.get("first_bar") == "bullish" and r.get("vix_move") == "vol falling (1-5%)",
        "Bullish 1st bar + all above MAs": lambda r: r.get("first_bar") == "bullish" and r.get("ma_alignment") == "all above",
        "Bullish 1st bar + all below MAs": lambda r: r.get("first_bar") == "bullish" and r.get("ma_alignment") == "all below",
        "Bullish 1st bar + mixed MAs": lambda r: r.get("first_bar") == "bullish" and r.get("ma_alignment") == "mixed",
        "Bullish 1st bar + red week": lambda r: r.get("first_bar") == "bullish" and r.get("week_color") == "red",
        "Bullish 1st bar + green week": lambda r: r.get("first_bar") == "bullish" and r.get("week_color") == "green",
        "Bullish 1st bar + deep red week": lambda r: r.get("first_bar") == "bullish" and r.get("wtd_bucket") == "deep red (>1%)",
        "Bullish 1st bar + red month": lambda r: r.get("first_bar") == "bullish" and r.get("month_color") == "red",
        "Bullish 1st bar + green month": lambda r: r.get("first_bar") == "bullish" and r.get("month_color") == "green",
        "Bullish 1st bar + deep red month": lambda r: r.get("first_bar") == "bullish" and r.get("mtd_bucket") in ("red (1-3%)", "deep red (>3%)"),
        "Bullish 1st bar + gap up + vol falling": lambda r: r.get("first_bar") == "bullish" and r.get("gap_dir") == "up" and r.get("vix_move") in ("vol falling (1-5%)", "vol crushing (>5%)"),
        "Bullish 1st bar + gap up + red week": lambda r: r.get("first_bar") == "bullish" and r.get("gap_dir") == "up" and r.get("week_color") == "red",
        "Bullish 1st bar + gap up + above all MAs": lambda r: r.get("first_bar") == "bullish" and r.get("gap_dir") == "up" and r.get("ma_alignment") == "all above",
        "Bullish 1st bar + Monday + vol falling": lambda r: r.get("first_bar") == "bullish" and r.get("day_of_week") == "Monday" and r.get("vix_move") in ("vol falling (1-5%)", "vol crushing (>5%)"),
        "Bullish 1st bar + bonds rallying": lambda r: r.get("first_bar") == "bullish" and r.get("tlt_5d_trend") == "bonds rallying (>1%)",
        "Bullish 1st bar + bonds selling": lambda r: r.get("first_bar") == "bullish" and r.get("tlt_5d_trend") == "bonds selling (>1%)",
        "Bullish 1st bar + 3+ down + vol elevated+": lambda r: r.get("first_bar") == "bullish" and r.get("streak_3plus_down") and r.get("vix_bucket") in ("elevated (20-25)", "high (25-30)", "very high (>30)"),
        "Bullish 1st bar + below 50d MA": lambda r: r.get("first_bar") == "bullish" and r.get("above_ma_50") == False,
        "Bullish 1st bar + just below 50d": lambda r: r.get("first_bar") == "bullish" and r.get("ma50_position") == "just below 50d (0-2%)",
        "Bullish 1st bar + far above 50d": lambda r: r.get("first_bar") == "bullish" and r.get("ma50_position") == "far above 50d (>5%)",
        "Bearish 1st bar + VIX very high": lambda r: r.get("first_bar") == "bearish" and r.get("vix_bucket") in ("high (25-30)", "very high (>30)"),
        "Bearish 1st bar + below all MAs": lambda r: r.get("first_bar") == "bearish" and r.get("ma_alignment") == "all below",
        "Bearish 1st bar + Thursday": lambda r: r.get("first_bar") == "bearish" and r.get("day_of_week") == "Thursday",
        "Gap up small + bullish 1st bar": lambda r: r.get("gap_bucket") == "small (3-8pts)" and r.get("first_bar") == "bullish",
        "Gap up medium + bullish 1st bar": lambda r: r.get("gap_bucket") == "medium (8-15pts)" and r.get("first_bar") == "bullish",
    }

    p(f"\n  {'Setup':<55s} {'N':>4s} {'WR%':>6s} {'AvgEOD':>8s} {'BestAvg':>8s} {'PF':>6s} {'PT':>4s} {'SL':>4s} {'TS':>5s}")
    p(f"  {'-'*55} {'----':>4s} {'------':>6s} {'--------':>8s} {'--------':>8s} {'------':>6s} {'----':>4s} {'----':>4s} {'-----':>5s}")

    grid_results = []
    for label, filt in combo_filters.items():
        filtered = [r for r in enriched if filt(r)]
        n = len(filtered)
        if n < 15:
            continue

        eod = [r["eod_pnl"] for r in filtered]
        wr = sum(1 for v in eod if v > 0) / n * 100
        avg_eod = mean(eod)

        best = quick_grid(filtered)
        if best:
            p(f"  {label:<55s} {n:>4d} {wr:>5.1f}% {avg_eod:>+8.2f} "
              f"{best['avg']:>+8.2f} {best['pf']:>6.2f} {best['pt']:>4d} {best['sl']:>4d} {best['ts']:>5d}")
            grid_results.append({
                "label": label, "n": n, "wr_eod": round(wr, 1),
                "avg_eod": round(avg_eod, 2), **best,
            })

    # Sort by best avg
    grid_results.sort(key=lambda x: x["avg"], reverse=True)

    p(f"\n\n{'='*80}")
    p("TOP ENRICHED SETUPS (ranked by best grid avg P&L)")
    p(f"{'='*80}")
    p(f"  {'#':>3s} {'Setup':<55s} {'N':>4s} {'WR%':>6s} {'AvgEOD':>8s} {'BestAvg':>8s} {'PF':>6s} {'PT':>4s} {'SL':>4s} {'TS':>5s}")
    for i, r in enumerate(grid_results[:25]):
        p(f"  {i+1:>3d} {r['label']:<55s} {r['n']:>4d} {r['wr_eod']:>5.1f}% {r['avg_eod']:>+8.2f} "
          f"{r['avg']:>+8.2f} {r['pf']:>6.2f} {r['pt']:>4d} {r['sl']:>4d} {r['ts']:>5d}")

    # ── Save everything ────────────────────────────────────────────────
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_lines))

    # Save edge summary
    if all_edge_rows:
        with open(EDGE_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_edge_rows[0].keys())
            writer.writeheader()
            writer.writerows(all_edge_rows)

    # Save enriched per-day factors
    factor_keys = ["date", "entry_open", "eod_pnl", "day_of_week", "first_bar",
                   "gap_pts", "gap_pct", "gap_dir", "gap_bucket", "prev_day_dir",
                   "vix_open", "vix_bucket", "vix_change", "vix_move",
                   "ma_alignment", "ma50_position", "ma200_position",
                   "above_ma_10", "above_ma_20", "above_ma_50", "above_ma_200",
                   "pct_from_ma_50", "pct_from_ma_200",
                   "wtd_return", "week_color", "wtd_bucket",
                   "mtd_return", "month_color", "mtd_bucket",
                   "ytd_return", "year_color",
                   "tlt_prev_dir", "tlt_5d_trend",
                   "us10y", "yield_bucket",
                   "streak"]
    with open(FACTORS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=factor_keys, extrasaction="ignore")
        writer.writeheader()
        for rec in enriched:
            writer.writerow(rec)

    p(f"\nFiles saved:")
    p(f"  {REPORT_FILE}")
    p(f"  {FACTORS_CSV}")
    p(f"  {EDGE_CSV}")


if __name__ == "__main__":
    main()
