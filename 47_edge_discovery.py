#!/usr/bin/env python3
"""
Comprehensive Edge Discovery Engine
=====================================
Scans SPX intraday data for exploitable patterns across:
  - VIX regimes: low (<16), mid (16-25), high (25-35), extreme (35+)
  - Time windows: open (9:30-10:00), mid-morning (10:00-11:00),
                  midday (11:00-13:00), afternoon (13:00-15:00), close (15:00-16:00)
  - Trade structures: long call, long put, bull call spread, bear call spread,
                      long stock + stop, short stock + stop
  - Signal types: first bar, opening range breakout, gap analysis,
                  momentum continuation, mean reversion, intraday drops/rips,
                  range compression breakout, trend filters

All P&L computed on UNDERLYING first to find directional edges.
Top edges then priced with real option data in Phase 2.

Run: python3 47_edge_discovery.py
"""

import csv, json, statistics, sys
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from itertools import combinations

SCRIPT_DIR = Path(__file__).parent.resolve()
START_DATE = '2018-06-01'  # enough history for robust stats

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_all_data():
    print("Loading data…")

    # SPX 1-min bars
    spx_1min = defaultdict(list)
    with open(SCRIPT_DIR / 'spx_1min_bars.csv') as f:
        for row in csv.DictReader(f):
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960:
                continue
            spx_1min[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            })
    for d in spx_1min:
        spx_1min[d].sort(key=lambda x: x['mins'])
    print(f"  SPX 1min: {len(spx_1min)} days")

    # SPX daily
    spx_daily = {}
    with open(SCRIPT_DIR / 'spx_daily_bars.csv') as f:
        for row in csv.DictReader(f):
            spx_daily[row['date']] = {
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            }

    # VIX daily
    vix_daily = {}
    with open(SCRIPT_DIR / 'vix_daily_bars.csv') as f:
        for row in csv.DictReader(f):
            vix_daily[row['date']] = {
                'o': float(row['open']), 'c': float(row['close']),
            }

    # 50-day SMA
    sorted_dates = sorted(spx_daily.keys())
    closes = [spx_daily[d]['c'] for d in sorted_dates]
    sma50 = {}
    for i in range(49, len(sorted_dates)):
        sma50[sorted_dates[i]] = sum(closes[i-49:i+1]) / 50

    # 20-day SMA (shorter trend)
    sma20 = {}
    for i in range(19, len(sorted_dates)):
        sma20[sorted_dates[i]] = sum(closes[i-19:i+1]) / 20

    # Previous day's range and close
    prev_data = {}
    for i in range(1, len(sorted_dates)):
        d = sorted_dates[i]
        pd = sorted_dates[i-1]
        prev = spx_daily[pd]
        prev_data[d] = {
            'prev_close': prev['c'],
            'prev_high': prev['h'],
            'prev_low': prev['l'],
            'prev_range': prev['h'] - prev['l'],
        }

    print(f"  SPX daily: {len(spx_daily)}, VIX: {len(vix_daily)}")
    return spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data


# ═══════════════════════════════════════════════════════════════
# FEATURE EXTRACTION — compute everything per day
# ═══════════════════════════════════════════════════════════════

def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data):
    print("Extracting features…")
    days = []

    for d in sorted(spx_1min.keys()):
        if d < START_DATE:
            continue
        bars = spx_1min[d]
        dd = spx_daily.get(d)
        vd = vix_daily.get(d)
        pd = prev_data.get(d)
        if not dd or not vd or not pd or len(bars) < 60:
            continue

        vix_open = vd['o']
        prev_close = pd['prev_close']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100

        # ── First bar ──
        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_range_pct = (fb['h'] - fb['l']) / fb['o'] * 100

        # ── First N bars ──
        def bar_stats(n):
            subset = bars[:n]
            if len(subset) < n:
                return None
            hi = max(b['h'] for b in subset)
            lo = min(b['l'] for b in subset)
            cl = subset[-1]['c']
            op = subset[0]['o']
            return {
                'high': hi, 'low': lo, 'close': cl, 'open': op,
                'range_pct': (hi - lo) / op * 100,
                'ret': (cl - op) / op * 100,
                'bullish': cl > op,
            }

        or5 = bar_stats(5)    # first 5 min
        or15 = bar_stats(15)  # first 15 min
        or30 = bar_stats(30)  # first 30 min

        # ── Trend context ──
        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None

        # ── Day of week ──
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        # ── VIX regime ──
        if vix_open < 16:
            vix_regime = 'low'
        elif vix_open < 25:
            vix_regime = 'mid'
        elif vix_open < 35:
            vix_regime = 'high'
        else:
            vix_regime = 'extreme'

        # ── Intraday high/low/close at various times ──
        def price_at(target_mins):
            for b in bars:
                if b['mins'] >= target_mins:
                    return b
            return bars[-1]

        def bars_between(start_mins, end_mins):
            return [b for b in bars if start_mins <= b['mins'] < end_mins]

        # Rolling high/low from open to each time point
        def rolling_hl(end_mins):
            subset = [b for b in bars if b['mins'] < end_mins]
            if not subset:
                return dd['o'], dd['o']
            return max(b['h'] for b in subset), min(b['l'] for b in subset)

        days.append({
            'date': d, 'bars': bars,
            'open': dd['o'], 'high': dd['h'], 'low': dd['l'], 'close': dd['c'],
            'vix': vix_open, 'vix_regime': vix_regime,
            'gap_pct': gap_pct,
            'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish,
            'fb_range_pct': fb_range_pct,
            'or5': or5, 'or15': or15, 'or30': or30,
            'above_50d': above_50d, 'above_20d': above_20d,
            'dow': dow,
            'prev_close': prev_close,
            'prev_range': pd['prev_range'],
        })

    print(f"  {len(days)} trading days with full features")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION — forward-walk exit logic
# ═══════════════════════════════════════════════════════════════

def simulate_trade(bars, entry_idx, direction, exit_params):
    """
    Forward-walk trade from entry_idx using exit_params.
    direction: 1 = long, -1 = short

    exit_params can include:
        pt_pts:     fixed profit target in SPX points
        sl_pts:     fixed stop loss in SPX points
        trail_pct:  trailing stop as % of entry price
        trail_pts:  trailing stop in SPX points
        ts_min:     time stop in minutes
        pt_pct:     profit target as % of entry
        sl_pct:     stop loss as % of entry
    """
    entry_bar = bars[entry_idx]
    entry_price = entry_bar['c']
    entry_mins = entry_bar['mins']

    pt_pts = exit_params.get('pt_pts')
    sl_pts = exit_params.get('sl_pts')
    pt_pct = exit_params.get('pt_pct')
    sl_pct = exit_params.get('sl_pct')
    trail_pct = exit_params.get('trail_pct')
    trail_pts = exit_params.get('trail_pts')
    ts_min = exit_params.get('ts_min', 60)
    ts_deadline = entry_mins + ts_min

    # Compute levels
    if direction == 1:
        pt_level = entry_price + pt_pts if pt_pts else None
        sl_level = entry_price - sl_pts if sl_pts else None
        if pt_pct:
            pt_level = entry_price * (1 + pt_pct / 100)
        if sl_pct:
            sl_level = entry_price * (1 - sl_pct / 100)
    else:
        pt_level = entry_price - pt_pts if pt_pts else None
        sl_level = entry_price + sl_pts if sl_pts else None
        if pt_pct:
            pt_level = entry_price * (1 - pt_pct / 100)
        if sl_pct:
            sl_level = entry_price * (1 + sl_pct / 100)

    peak = entry_price
    trough = entry_price
    exit_reason = 'time_stop'
    exit_idx = entry_idx

    for j in range(entry_idx + 1, len(bars)):
        bar = bars[j]
        price = bar['c']

        # Time stop
        if bar['mins'] >= ts_deadline or bar['mins'] >= 959:
            exit_idx = j
            exit_reason = 'time_stop'
            break

        if direction == 1:
            if price > peak:
                peak = price

            if pt_level and price >= pt_level:
                exit_idx = j
                exit_reason = 'profit_target'
                break

            if sl_level and price <= sl_level:
                exit_idx = j
                exit_reason = 'stop_loss'
                break

            if trail_pct and peak > entry_price:
                tl = peak * (1 - trail_pct / 100)
                if price <= tl:
                    exit_idx = j
                    exit_reason = 'trailing_stop'
                    break

            if trail_pts and peak > entry_price:
                tl = peak - trail_pts
                if price <= tl:
                    exit_idx = j
                    exit_reason = 'trailing_stop'
                    break
        else:
            if price < trough:
                trough = price

            if pt_level and price <= pt_level:
                exit_idx = j
                exit_reason = 'profit_target'
                break

            if sl_level and price >= sl_level:
                exit_idx = j
                exit_reason = 'stop_loss'
                break

            if trail_pct and trough < entry_price:
                tl = trough * (1 + trail_pct / 100)
                if price >= tl:
                    exit_idx = j
                    exit_reason = 'trailing_stop'
                    break

            if trail_pts and trough < entry_price:
                tl = trough + trail_pts
                if price >= tl:
                    exit_idx = j
                    exit_reason = 'trailing_stop'
                    break

    exit_bar = bars[exit_idx]
    exit_price = exit_bar['c']
    hold_mins = exit_bar['mins'] - entry_mins
    und_ret = direction * (exit_price - entry_price) / entry_price * 100
    und_pts = direction * (exit_price - entry_price)

    return {
        'entry_price': entry_price,
        'exit_price': exit_price,
        'entry_time': entry_bar['time'],
        'exit_time': exit_bar['time'],
        'hold_mins': hold_mins,
        'exit_reason': exit_reason,
        'und_ret_pct': und_ret,
        'und_pts': und_pts,
        'direction': direction,
    }


# ═══════════════════════════════════════════════════════════════
# EDGE TESTING FRAMEWORK
# ═══════════════════════════════════════════════════════════════

def compute_edge_stats(trades, label):
    if len(trades) < 15:
        return None

    rets = [t['und_ret_pct'] for t in trades]
    pts = [t['und_pts'] for t in trades]
    n = len(rets)
    avg_ret = statistics.mean(rets)
    wr = sum(1 for r in rets if r > 0) / n * 100

    if n > 1:
        std = statistics.stdev(rets)
        sharpe = avg_ret / std if std > 0 else 0
    else:
        sharpe = 0

    # Max drawdown in points
    cum = 0
    peak = 0
    max_dd = 0
    for p in pts:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_win = statistics.mean(wins) if wins else 0
    avg_loss = statistics.mean(losses) if losses else 0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else 99

    # Exit breakdown
    exits = defaultdict(int)
    for t in trades:
        exits[t['exit_reason']] += 1

    avg_hold = statistics.mean(t['hold_mins'] for t in trades)

    return {
        'label': label, 'n': n, 'wr': round(wr, 1),
        'avg_ret': round(avg_ret, 4), 'total_ret': round(sum(rets), 2),
        'avg_win': round(avg_win, 4), 'avg_loss': round(avg_loss, 4),
        'sharpe': round(sharpe, 3), 'pf': round(pf, 2),
        'max_dd_pts': round(max_dd, 1),
        'avg_pts': round(statistics.mean(pts), 2),
        'total_pts': round(sum(pts), 1),
        'avg_hold': round(avg_hold, 1),
        'exits': dict(exits),
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATORS — each returns (entry_bar_idx, direction)
# ═══════════════════════════════════════════════════════════════

def signal_first_bar_bull(day):
    if day['fb_bullish']:
        return 0, 1
    return None

def signal_first_bar_bear(day):
    if not day['fb_bullish']:
        return 0, -1
    return None

def signal_first_bar_strong_bull(day, min_ret=0.10):
    if day['fb_bullish'] and day['fb_ret'] >= min_ret:
        return 0, 1
    return None

def signal_or5_breakout_bull(day):
    if day['or5'] and day['or5']['bullish'] and day['or5']['range_pct'] >= 0.10:
        return 4, 1  # enter at bar 5 close
    return None

def signal_or5_breakout_bear(day):
    if day['or5'] and not day['or5']['bullish'] and day['or5']['range_pct'] >= 0.10:
        return 4, -1
    return None

def signal_or15_breakout_bull(day):
    if day['or15'] and day['or15']['bullish'] and day['or15']['range_pct'] >= 0.15:
        return 14, 1
    return None

def signal_or15_breakout_bear(day):
    if day['or15'] and not day['or15']['bullish'] and day['or15']['range_pct'] >= 0.15:
        return 14, -1
    return None

def signal_or30_breakout_bull(day):
    if day['or30'] and day['or30']['bullish'] and day['or30']['range_pct'] >= 0.20:
        return 29, 1
    return None

def signal_or30_breakout_bear(day):
    if day['or30'] and not day['or30']['bullish'] and day['or30']['range_pct'] >= 0.20:
        return 29, -1
    return None

def signal_gap_up_continuation(day, min_gap=0.20):
    if day['gap_pct'] >= min_gap and day['fb_bullish']:
        return 0, 1
    return None

def signal_gap_up_fade(day, min_gap=0.20):
    if day['gap_pct'] >= min_gap and not day['fb_bullish']:
        return 0, -1
    return None

def signal_gap_down_bounce(day, max_gap=-0.20):
    if day['gap_pct'] <= max_gap and day['fb_bullish']:
        return 0, 1
    return None

def signal_gap_down_continuation(day, max_gap=-0.20):
    if day['gap_pct'] <= max_gap and not day['fb_bullish']:
        return 0, -1
    return None

def signal_intraday_drop(day, drop_pct=0.30, start_mins=575, end_mins=900):
    """Scan for intraday drop from rolling high — forward walking."""
    bars = day['bars']
    rolling_high = 0
    peak_idx = 0
    for i, bar in enumerate(bars):
        if bar['mins'] < start_mins:
            if bar['h'] > rolling_high:
                rolling_high = bar['h']
                peak_idx = i
            continue
        if bar['mins'] >= end_mins:
            break
        if bar['h'] > rolling_high:
            rolling_high = bar['h']
            peak_idx = i
        if rolling_high <= 0:
            continue
        dp = (rolling_high - bar['c']) / rolling_high * 100
        if dp >= drop_pct:
            return i, 1  # buy the dip
    return None

def signal_intraday_rip(day, rip_pct=0.30, start_mins=575, end_mins=900):
    """Scan for intraday rally from rolling low — forward walking."""
    bars = day['bars']
    rolling_low = 999999
    trough_idx = 0
    for i, bar in enumerate(bars):
        if bar['mins'] < start_mins:
            if bar['l'] < rolling_low:
                rolling_low = bar['l']
                trough_idx = i
            continue
        if bar['mins'] >= end_mins:
            break
        if bar['l'] < rolling_low:
            rolling_low = bar['l']
            trough_idx = i
        rp = (bar['c'] - rolling_low) / rolling_low * 100
        if rp >= rip_pct:
            return i, -1  # fade the rip (short)
    return None

def signal_midday_mean_revert(day, threshold_pct=0.30):
    """At 11:00, if SPX has moved > threshold from open, fade it."""
    bars = day['bars']
    bar_11 = None
    for b in bars:
        if b['mins'] >= 660:
            bar_11 = b
            break
    if not bar_11:
        return None
    idx = bars.index(bar_11)
    move = (bar_11['c'] - day['open']) / day['open'] * 100
    if move >= threshold_pct:
        return idx, -1  # overbought, fade short
    elif move <= -threshold_pct:
        return idx, 1   # oversold, buy
    return None

def signal_afternoon_trend(day, threshold_pct=0.10):
    """At 13:00, if SPX trending, ride it to close."""
    bars = day['bars']
    bar_13 = None
    for b in bars:
        if b['mins'] >= 780:
            bar_13 = b
            break
    if not bar_13:
        return None
    idx = bars.index(bar_13)
    move = (bar_13['c'] - day['open']) / day['open'] * 100
    if move >= threshold_pct:
        return idx, 1   # continue long
    elif move <= -threshold_pct:
        return idx, -1  # continue short
    return None

def signal_range_compression_breakout(day):
    """If today's first 30min range is < 50% of yesterday's range, trade breakout."""
    if not day['or30']:
        return None
    or30_range = day['or30']['high'] - day['or30']['low']
    prev_range = day.get('prev_range', 0)
    if prev_range <= 0:
        return None
    ratio = or30_range / prev_range
    if ratio < 0.50:  # compressed
        # Trade in direction of breakout at bar 30
        if day['or30']['bullish']:
            return 29, 1
        else:
            return 29, -1
    return None


# ═══════════════════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 120)
    print("COMPREHENSIVE EDGE DISCOVERY ENGINE")
    print("=" * 120)

    spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data = load_all_data()
    days = extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data)

    # ── Define all signal generators ──
    signals = {
        'FB_Bull': signal_first_bar_bull,
        'FB_Bear': signal_first_bar_bear,
        'FB_StrongBull_10': lambda d: signal_first_bar_strong_bull(d, 0.10),
        'FB_StrongBull_20': lambda d: signal_first_bar_strong_bull(d, 0.20),
        'OR5_Bull': signal_or5_breakout_bull,
        'OR5_Bear': signal_or5_breakout_bear,
        'OR15_Bull': signal_or15_breakout_bull,
        'OR15_Bear': signal_or15_breakout_bear,
        'OR30_Bull': signal_or30_breakout_bull,
        'OR30_Bear': signal_or30_breakout_bear,
        'GapUp_Cont_20': lambda d: signal_gap_up_continuation(d, 0.20),
        'GapUp_Cont_50': lambda d: signal_gap_up_continuation(d, 0.50),
        'GapUp_Fade_20': lambda d: signal_gap_up_fade(d, 0.20),
        'GapUp_Fade_50': lambda d: signal_gap_up_fade(d, 0.50),
        'GapDn_Bounce_20': lambda d: signal_gap_down_bounce(d, -0.20),
        'GapDn_Bounce_50': lambda d: signal_gap_down_bounce(d, -0.50),
        'GapDn_Cont_20': lambda d: signal_gap_down_continuation(d, -0.20),
        'GapDn_Cont_50': lambda d: signal_gap_down_continuation(d, -0.50),
        'Drop_30bps': lambda d: signal_intraday_drop(d, 0.30),
        'Drop_50bps': lambda d: signal_intraday_drop(d, 0.50),
        'Drop_80bps': lambda d: signal_intraday_drop(d, 0.80),
        'Rip_30bps_Fade': lambda d: signal_intraday_rip(d, 0.30),
        'Rip_50bps_Fade': lambda d: signal_intraday_rip(d, 0.50),
        'Rip_80bps_Fade': lambda d: signal_intraday_rip(d, 0.80),
        'Midday_MR_20': lambda d: signal_midday_mean_revert(d, 0.20),
        'Midday_MR_40': lambda d: signal_midday_mean_revert(d, 0.40),
        'Midday_MR_60': lambda d: signal_midday_mean_revert(d, 0.60),
        'PM_Trend_10': lambda d: signal_afternoon_trend(d, 0.10),
        'PM_Trend_30': lambda d: signal_afternoon_trend(d, 0.30),
        'PM_Trend_50': lambda d: signal_afternoon_trend(d, 0.50),
        'RangeCompress': signal_range_compression_breakout,
    }

    # ── Define VIX filters ──
    vix_filters = {
        'AllVIX': lambda d: True,
        'VIX_Low': lambda d: d['vix_regime'] == 'low',
        'VIX_Mid': lambda d: d['vix_regime'] == 'mid',
        'VIX_High': lambda d: d['vix_regime'] == 'high',
        'VIX_Ext': lambda d: d['vix_regime'] == 'extreme',
        'VIX_lt20': lambda d: d['vix'] < 20,
        'VIX_20_30': lambda d: 20 <= d['vix'] < 30,
        'VIX_gt25': lambda d: d['vix'] >= 25,
        'VIX_gt30': lambda d: d['vix'] >= 30,
    }

    # ── Define trend filters ──
    trend_filters = {
        'AllTrend': lambda d: True,
        'Above50d': lambda d: d['above_50d'] is True,
        'Below50d': lambda d: d['above_50d'] is False,
        'Above20d': lambda d: d['above_20d'] is True,
        'Below20d': lambda d: d['above_20d'] is False,
    }

    # ── Define exit parameter sets ──
    exit_sets = {
        # Tight scalps
        'PT3_SL2_TS10': {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 10},
        'PT5_SL3_TS15': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 15},
        'PT8_SL4_TS15': {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
        'PT10_SL5_TS20': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 20},
        # Medium holds
        'PT15_SL8_TS30': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 30},
        'PT20_SL10_TS45': {'pt_pts': 20, 'sl_pts': 10, 'ts_min': 45},
        'PT30_SL15_TS60': {'pt_pts': 30, 'sl_pts': 15, 'ts_min': 60},
        # Wide holds
        'PT50_SL20_TS120': {'pt_pts': 50, 'sl_pts': 20, 'ts_min': 120},
        'PT50_SL25_TS180': {'pt_pts': 50, 'sl_pts': 25, 'ts_min': 180},
        # Trail-based
        'Trail10_SL5_TS30': {'trail_pct': 0.10, 'sl_pts': 5, 'ts_min': 30},
        'Trail15_SL8_TS45': {'trail_pct': 0.15, 'sl_pts': 8, 'ts_min': 45},
        'Trail20_SL10_TS60': {'trail_pct': 0.20, 'sl_pts': 10, 'ts_min': 60},
        'Trail05_SL15_TS30': {'trail_pct': 0.05, 'sl_pts': 15, 'ts_min': 30},
        # Hold to close
        'SL10_ToClose': {'sl_pts': 10, 'ts_min': 390},
        'SL20_ToClose': {'sl_pts': 20, 'ts_min': 390},
        'SL30_ToClose': {'sl_pts': 30, 'ts_min': 390},
        # Percent-based
        'PT_50bps_SL_25bps_TS30': {'pt_pct': 0.50, 'sl_pct': 0.25, 'ts_min': 30},
        'PT_100bps_SL_50bps_TS60': {'pt_pct': 1.00, 'sl_pct': 0.50, 'ts_min': 60},
        # Very tight for options
        'PT5_SL2_TS5': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 5},
        'PT8_SL3_TS10': {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 10},
    }

    # ── DOW filters ──
    dow_filters = {
        'AllDays': lambda d: True,
        'Monday': lambda d: d['dow'] == 0,
        'TueWedThu': lambda d: d['dow'] in (1, 2, 3),
        'Friday': lambda d: d['dow'] == 4,
    }

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: SCAN ALL COMBINATIONS
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 1: Scanning all signal × filter × exit combinations")
    print(f"{'='*120}")

    total_combos = len(signals) * len(vix_filters) * len(exit_sets)
    print(f"  {len(signals)} signals × {len(vix_filters)} VIX × {len(exit_sets)} exits = {total_combos} base combos")
    print(f"  (trend and DOW filters applied selectively to top results)")

    all_results = []
    combo_count = 0

    for sig_name, sig_fn in signals.items():
        for vix_name, vix_fn in vix_filters.items():
            for exit_name, exit_params in exit_sets.items():

                combo_count += 1
                if combo_count % 500 == 0:
                    print(f"  [{combo_count}/{total_combos}]…", flush=True)

                trades = []
                for day in days:
                    if not vix_fn(day):
                        continue

                    result = sig_fn(day)
                    if result is None:
                        continue

                    entry_idx, direction = result
                    if entry_idx >= len(day['bars']) - 5:
                        continue

                    trade = simulate_trade(day['bars'], entry_idx, direction, exit_params)
                    trade['date'] = day['date']
                    trade['vix'] = day['vix']
                    trade['gap_pct'] = day['gap_pct']
                    trades.append(trade)

                stats = compute_edge_stats(trades, f"{sig_name}|{vix_name}|{exit_name}")
                if stats and stats['sharpe'] > 0.05 and stats['pf'] > 1.0:
                    stats['signal'] = sig_name
                    stats['vix_filter'] = vix_name
                    stats['exit_set'] = exit_name
                    stats['trades_data'] = trades  # keep for Phase 2
                    all_results.append(stats)

    print(f"\n  Scanned {combo_count} combos, {len(all_results)} positive edges found")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1b: Apply trend + DOW filters to top results
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  Applying trend/DOW refinement to top 200 edges…")

    # Sort by Sharpe, take top 200
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)
    top200 = all_results[:200]

    refined = []
    for r in top200:
        sig_fn = signals[r['signal']]
        vix_fn = vix_filters[r['vix_filter']]
        exit_params = exit_sets[r['exit_set']]

        for trend_name, trend_fn in trend_filters.items():
            for dow_name, dow_fn in dow_filters.items():
                if trend_name == 'AllTrend' and dow_name == 'AllDays':
                    continue  # already tested

                trades = []
                for day in days:
                    if not vix_fn(day) or not trend_fn(day) or not dow_fn(day):
                        continue
                    result = sig_fn(day)
                    if result is None:
                        continue
                    entry_idx, direction = result
                    if entry_idx >= len(day['bars']) - 5:
                        continue
                    trade = simulate_trade(day['bars'], entry_idx, direction, exit_params)
                    trade['date'] = day['date']
                    trade['vix'] = day['vix']
                    trades.append(trade)

                label = f"{r['signal']}|{r['vix_filter']}|{r['exit_set']}|{trend_name}|{dow_name}"
                stats = compute_edge_stats(trades, label)
                if stats and stats['sharpe'] > 0.10 and stats['pf'] > 1.1 and stats['n'] >= 20:
                    stats['signal'] = r['signal']
                    stats['vix_filter'] = r['vix_filter']
                    stats['exit_set'] = r['exit_set']
                    stats['trend_filter'] = trend_name
                    stats['dow_filter'] = dow_name
                    stats['trades_data'] = trades
                    refined.append(stats)

    all_results.extend(refined)
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: REPORT
    # ═══════════════════════════════════════════════════════════════

    # Dedup — keep best Sharpe per signal+vix combo
    seen = set()
    deduped = []
    for r in all_results:
        key = (r['signal'], r.get('vix_filter', ''), r.get('trend_filter', ''), r.get('dow_filter', ''))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    print(f"\n{'='*140}")
    print(f"TOP 80 EDGES BY SHARPE (after dedup)")
    print(f"{'='*140}")
    print(f"{'Label':>65s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
          f"{'Sharpe':>7} {'PF':>5} {'MaxDD':>8} {'AvgHold':>8}")
    print("-" * 140)

    for r in deduped[:80]:
        print(f"{r['label'][:65]:>65s} {r['n']:>5} {r['wr']:>5.1f}% "
              f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['max_dd_pts']:>7.1f} "
              f"{r['avg_hold']:>6.1f}m")

    # ── Group by category ──
    categories = {
        'MORNING (entry before 10:00)': lambda r: r['signal'] in (
            'FB_Bull', 'FB_Bear', 'FB_StrongBull_10', 'FB_StrongBull_20',
            'OR5_Bull', 'OR5_Bear', 'OR15_Bull', 'OR15_Bear', 'OR30_Bull', 'OR30_Bear',
            'GapUp_Cont_20', 'GapUp_Cont_50', 'GapUp_Fade_20', 'GapUp_Fade_50',
            'GapDn_Bounce_20', 'GapDn_Bounce_50', 'GapDn_Cont_20', 'GapDn_Cont_50',
            'RangeCompress'),
        'MIDDAY (entry 11:00-13:00)': lambda r: r['signal'].startswith('Midday'),
        'AFTERNOON (entry 13:00+)': lambda r: r['signal'].startswith('PM_Trend'),
        'INTRADAY DIP BUY': lambda r: r['signal'].startswith('Drop'),
        'INTRADAY RIP FADE': lambda r: r['signal'].startswith('Rip'),
        'LOW VIX (<16)': lambda r: r.get('vix_filter') == 'VIX_Low',
        'MID VIX (16-25)': lambda r: r.get('vix_filter') == 'VIX_Mid',
        'HIGH VIX (25+)': lambda r: r.get('vix_filter') in ('VIX_High', 'VIX_Ext', 'VIX_gt25', 'VIX_gt30'),
    }

    for cat_name, cat_fn in categories.items():
        subset = [r for r in deduped if cat_fn(r)]
        if not subset:
            continue
        subset.sort(key=lambda x: x['sharpe'], reverse=True)

        print(f"\n{'='*140}")
        print(f"  {cat_name} — TOP 15")
        print(f"{'='*140}")
        print(f"{'Label':>65s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
              f"{'Sharpe':>7} {'PF':>5} {'AvgHold':>8}")
        print("-" * 140)

        for r in subset[:15]:
            print(f"{r['label'][:65]:>65s} {r['n']:>5} {r['wr']:>5.1f}% "
                  f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
                  f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['avg_hold']:>6.1f}m")

    # ═══════════════════════════════════════════════════════════════
    # SAVE TOP RESULTS
    # ═══════════════════════════════════════════════════════════════
    out_dir = SCRIPT_DIR / 'backtest_results'
    out_dir.mkdir(exist_ok=True)

    # Save summary (without trades data)
    summary = []
    for r in deduped[:200]:
        s = {k: v for k, v in r.items() if k != 'trades_data'}
        summary.append(s)

    with open(out_dir / 'edge_discovery_results.json', 'w') as f:
        json.dump(summary, f, indent=2)

    # Save top 30 edges' trade lists
    for i, r in enumerate(deduped[:30]):
        trades = r.get('trades_data', [])
        if not trades:
            continue
        safe_label = r['label'].replace('|', '_').replace(' ', '_')[:50]
        csv_file = out_dir / f'edge_{i+1:02d}_{safe_label}.csv'
        fields = [k for k in trades[0].keys()]
        with open(csv_file, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(trades)

    print(f"\n  Saved {len(summary)} edge summaries to edge_discovery_results.json")
    print(f"  Saved trade CSVs for top 30 edges")

    # ═══════════════════════════════════════════════════════════════
    # OUT-OF-SAMPLE SPLIT
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*140}")
    print(f"OUT-OF-SAMPLE VALIDATION — 2018-2022 in-sample, 2023-2026 out-of-sample")
    print(f"{'='*140}")

    for r in deduped[:30]:
        trades = r.get('trades_data', [])
        if not trades:
            continue
        is_trades = [t for t in trades if t['date'] < '2023-01-01']
        oos_trades = [t for t in trades if t['date'] >= '2023-01-01']

        is_stats = compute_edge_stats(is_trades, 'IS') if len(is_trades) >= 10 else None
        oos_stats = compute_edge_stats(oos_trades, 'OOS') if len(oos_trades) >= 10 else None

        if is_stats and oos_stats:
            degradation = (oos_stats['sharpe'] - is_stats['sharpe']) / max(0.001, abs(is_stats['sharpe'])) * 100
            verdict = 'HOLDS' if oos_stats['sharpe'] > 0.08 and oos_stats['pf'] > 1.0 else 'DEGRADES'
            print(f"  {r['label'][:55]:>55s}  IS: Sh={is_stats['sharpe']:>6.3f} N={is_stats['n']:>4}  "
                  f"OOS: Sh={oos_stats['sharpe']:>6.3f} N={oos_stats['n']:>4}  "
                  f"Δ={degradation:>+6.1f}%  [{verdict}]")
        elif is_stats:
            print(f"  {r['label'][:55]:>55s}  IS: Sh={is_stats['sharpe']:>6.3f} N={is_stats['n']:>4}  OOS: insufficient data")

    print(f"\n{'='*120}")
    print("DONE")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
