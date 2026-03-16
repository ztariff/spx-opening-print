#!/usr/bin/env python3
"""
Short-Side Edge Scanner
========================
Mirrors the 3-phase pipeline from 52_ultra_sharpe_scanner.py but focuses
exclusively on bearish (direction=-1) signals and put-side option structures.

Phase 1: Underlying scan — bearish signals × filters × exits
Phase 2: IS/OOS validation (2018-2022 vs 2023-2026)
Phase 3: Price validated edges with real SPXW put option data

All rules from CLAUDE.md apply:
  - No synthetic/fabricated data
  - No Black-Scholes substitution
  - Forward-walk simulation only (no hindsight)
  - Surface missing data gaps
"""

import csv, json, math, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from itertools import combinations

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (identical to 52/53)
# ═══════════════════════════════════════════════════════════════
def load_all_data():
    print("Loading data…")
    spx_1min = defaultdict(list)
    with open(SCRIPT_DIR / 'spx_1min_bars.csv') as f:
        for row in csv.DictReader(f):
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960: continue
            spx_1min[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
                'v': float(row.get('volume', 0)),
                'txn': int(float(row.get('transactions', 0) or 0)),
            })
    for d in spx_1min:
        spx_1min[d].sort(key=lambda x: x['mins'])

    spx_daily = {}
    with open(SCRIPT_DIR / 'spx_daily_bars.csv') as f:
        for row in csv.DictReader(f):
            spx_daily[row['date']] = {
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            }

    vix_daily = {}
    with open(SCRIPT_DIR / 'vix_daily_bars.csv') as f:
        for row in csv.DictReader(f):
            vix_daily[row['date']] = {
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            }

    sorted_dates = sorted(spx_daily.keys())
    closes = [spx_daily[d]['c'] for d in sorted_dates]

    sma50, sma20, sma10, sma5 = {}, {}, {}, {}
    for i in range(49, len(sorted_dates)):
        sma50[sorted_dates[i]] = sum(closes[i-49:i+1]) / 50
    for i in range(19, len(sorted_dates)):
        sma20[sorted_dates[i]] = sum(closes[i-19:i+1]) / 20
    for i in range(9, len(sorted_dates)):
        sma10[sorted_dates[i]] = sum(closes[i-9:i+1]) / 10
    for i in range(4, len(sorted_dates)):
        sma5[sorted_dates[i]] = sum(closes[i-4:i+1]) / 5

    rsi5 = {}
    for i in range(5, len(sorted_dates)):
        gains, losses = [], []
        for j in range(i-4, i+1):
            chg = closes[j] - closes[j-1]
            if chg > 0: gains.append(chg)
            else: losses.append(abs(chg))
        ag = statistics.mean(gains) if gains else 0
        al = statistics.mean(losses) if losses else 0
        if al == 0 and ag == 0:
            rsi5[sorted_dates[i]] = 50
        elif al == 0:
            rsi5[sorted_dates[i]] = 100
        else:
            rsi5[sorted_dates[i]] = 100 - 100/(1+ag/al)

    prev_days = {}
    for i in range(5, len(sorted_dates)):
        d = sorted_dates[i]
        prev = []
        for j in range(1, 6):
            prev.append(spx_daily[sorted_dates[i-j]])
        prev_days[d] = prev

    vix_sorted = sorted(vix_daily.keys())
    vix_prev_close = {}
    for i in range(1, len(vix_sorted)):
        vix_prev_close[vix_sorted[i]] = vix_daily[vix_sorted[i-1]]['c']

    atr5 = {}
    for i in range(5, len(sorted_dates)):
        trs = []
        for j in range(i-4, i+1):
            d2 = sorted_dates[j]
            d1 = sorted_dates[j-1]
            hi = spx_daily[d2]['h']
            lo = spx_daily[d2]['l']
            pc = spx_daily[d1]['c']
            trs.append(max(hi-lo, abs(hi-pc), abs(lo-pc)))
        atr5[sorted_dates[i]] = statistics.mean(trs)

    print(f"  SPX 1min: {len(spx_1min)} days, daily: {len(spx_daily)}, VIX: {len(vix_daily)}")
    return spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5, prev_days, vix_prev_close, rsi5, atr5


def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5, prev_days, vix_prev_close, rsi5, atr5):
    print("Extracting features…")
    days = []

    for d in sorted(spx_1min.keys()):
        if d < START_DATE: continue
        bars = spx_1min[d]
        dd = spx_daily.get(d)
        vd = vix_daily.get(d)
        prev = prev_days.get(d)
        if not dd or not vd or not prev or len(bars) < 60: continue

        vix_open = vd['o']
        vix_pc = vix_prev_close.get(d, vix_open)
        vix_change_pct = (vix_open - vix_pc) / vix_pc * 100 if vix_pc > 0 else 0

        prev_close = prev[0]['c']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100

        prev_range = prev[0]['h'] - prev[0]['l']
        prev_close_loc = (prev[0]['c'] - prev[0]['l']) / prev_range if prev_range > 0 else 0.5
        prev_bullish = prev[0]['c'] > prev[0]['o']

        five_day_ranges = [(p['h']-p['l'])/p['o']*100 for p in prev[:5]]
        avg_5d_range = statistics.mean(five_day_ranges) if five_day_ranges else 0

        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_range_pct = (fb['h'] - fb['l']) / fb['o'] * 100
        fb_body_ratio = abs(fb['c'] - fb['o']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0
        fb_upper_wick = (fb['h'] - max(fb['o'], fb['c'])) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0
        fb_lower_wick = (min(fb['o'], fb['c']) - fb['l']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0

        fb2_bear = len(bars) > 1 and bars[0]['c'] < bars[0]['o'] and bars[1]['c'] < bars[1]['o']
        fb2_ret = (bars[1]['c'] - bars[0]['o']) / bars[0]['o'] * 100 if len(bars) > 1 else 0

        fb3 = bars[:3]
        fb3_consecutive_bear = all(b['c'] < b['o'] for b in fb3)
        fb3_ret = (fb3[-1]['c'] - fb3[0]['o']) / fb3[0]['o'] * 100 if len(fb3) == 3 else 0

        def or_stats(n):
            subset = bars[:n]
            if len(subset) < n: return None
            hi = max(b['h'] for b in subset)
            lo = min(b['l'] for b in subset)
            cl = subset[-1]['c']
            op = subset[0]['o']
            rng = hi - lo
            return {
                'high': hi, 'low': lo, 'close': cl, 'open': op,
                'range': rng, 'range_pct': rng / op * 100,
                'ret': (cl - op) / op * 100,
                'bullish': cl > op,
                'close_loc': (cl - lo) / rng if rng > 0 else 0.5,
            }

        or5 = or_stats(5)
        or15 = or_stats(15)

        or15_narrow = False
        if or15 and avg_5d_range > 0:
            or15_narrow = (or15['range_pct'] / avg_5d_range) < 0.3

        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_10d = dd['o'] > sma10.get(d, 0) if d in sma10 else None
        above_5d = dd['o'] > sma5.get(d, 0) if d in sma5 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        if vix_open < 14: vix_regime = 'very_low'
        elif vix_open < 18: vix_regime = 'low'
        elif vix_open < 22: vix_regime = 'mid'
        elif vix_open < 30: vix_regime = 'high'
        else: vix_regime = 'extreme'

        days.append({
            'date': d, 'bars': bars,
            'open': dd['o'], 'high': dd['h'], 'low': dd['l'], 'close': dd['c'],
            'vix': vix_open, 'vix_regime': vix_regime,
            'vix_change_pct': vix_change_pct,
            'gap_pct': gap_pct,
            'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish,
            'fb_range_pct': fb_range_pct, 'fb_body_ratio': fb_body_ratio,
            'fb_upper_wick': fb_upper_wick, 'fb_lower_wick': fb_lower_wick,
            'fb2_bear': fb2_bear, 'fb2_ret': fb2_ret,
            'fb3_consecutive_bear': fb3_consecutive_bear, 'fb3_ret': fb3_ret,
            'or5': or5, 'or15': or15, 'or15_narrow': or15_narrow,
            'above_50d': above_50d, 'above_20d': above_20d,
            'above_10d': above_10d, 'above_5d': above_5d,
            'dow': dow,
            'prev_close': prev_close,
            'prev_close_loc': prev_close_loc, 'prev_bullish': prev_bullish,
            'avg_5d_range': avg_5d_range,
            'rsi5': rsi5.get(d, 50), 'atr5': atr5.get(d, 30),
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION (forward-walk, identical to 52/53)
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
    entry_bar = bars[entry_idx]
    entry_price = entry_bar['c']
    entry_mins = entry_bar['mins']

    pt_pts = exit_params.get('pt_pts')
    sl_pts = exit_params.get('sl_pts')
    trail_pct = exit_params.get('trail_pct')
    ts_min = exit_params.get('ts_min', 60)
    ts_deadline = entry_mins + ts_min

    vix_mult = exit_params.get('vix_mult')
    if vix_mult:
        vix = exit_params.get('_vix', 20)
        scale = vix / 20.0
        if pt_pts: pt_pts = pt_pts * scale
        if sl_pts: sl_pts = sl_pts * scale

    # Direction -1 (bearish): PT below entry, SL above entry
    if direction == 1:
        pt_level = entry_price + pt_pts if pt_pts else None
        sl_level = entry_price - sl_pts if sl_pts else None
    else:
        pt_level = entry_price - pt_pts if pt_pts else None
        sl_level = entry_price + sl_pts if sl_pts else None

    peak, trough = entry_price, entry_price
    exit_price = entry_price
    exit_reason = 'time_stop'
    exit_idx = len(bars) - 1

    for j in range(entry_idx + 1, len(bars)):
        bar = bars[j]
        if bar['mins'] >= ts_deadline or bar['mins'] >= 959:
            exit_price = bar['c']
            exit_reason = 'time_stop'
            exit_idx = j
            break

        if direction == 1:
            if bar['h'] > peak: peak = bar['h']
            if sl_level and bar['l'] <= sl_level:
                exit_price = sl_level; exit_reason = 'stop_loss'; exit_idx = j; break
            if pt_level and bar['h'] >= pt_level:
                exit_price = pt_level; exit_reason = 'profit_target'; exit_idx = j; break
            if trail_pct and peak > entry_price:
                tl = peak * (1 - trail_pct / 100)
                if bar['l'] <= tl:
                    exit_price = tl; exit_reason = 'trailing_stop'; exit_idx = j; break
        else:
            if bar['l'] < trough: trough = bar['l']
            if sl_level and bar['h'] >= sl_level:
                exit_price = sl_level; exit_reason = 'stop_loss'; exit_idx = j; break
            if pt_level and bar['l'] <= pt_level:
                exit_price = pt_level; exit_reason = 'profit_target'; exit_idx = j; break
            if trail_pct and trough < entry_price:
                tl = trough * (1 + trail_pct / 100)
                if bar['h'] >= tl:
                    exit_price = tl; exit_reason = 'trailing_stop'; exit_idx = j; break
    else:
        exit_price = bars[-1]['c']
        exit_idx = len(bars) - 1

    exit_bar = bars[exit_idx]
    return {
        'entry_price': round(entry_price, 2), 'exit_price': round(exit_price, 2),
        'entry_time': entry_bar['time'], 'exit_time': exit_bar['time'],
        'entry_mins': entry_bar['mins'], 'exit_mins': exit_bar['mins'],
        'hold_mins': exit_bar['mins'] - entry_mins,
        'exit_reason': exit_reason,
        'und_pts': round(direction * (exit_price - entry_price), 2),
        'direction': direction,
    }


# ═══════════════════════════════════════════════════════════════
# BEARISH SIGNALS ONLY
# ═══════════════════════════════════════════════════════════════
def bear_signals(day):
    """All bearish (direction=-1) signals from the scanner."""
    results = []
    fb_ret = day['fb_ret']
    or5 = day['or5']
    or15 = day['or15']
    gap = day['gap_pct']

    # S2: StrongBody_Bear
    if day['fb_body_ratio'] > 0.80 and not day['fb_bullish'] and fb_ret <= -0.03:
        results.append(('StrongBody_Bear', 0, -1))

    # S4: PrevLow_Cont
    if day['prev_close_loc'] < 0.20 and gap <= -0.05 and not day['fb_bullish']:
        results.append(('PrevLow_Cont', 0, -1))

    # S6: ThreeBarBear
    if day['fb3_consecutive_bear'] and day['fb3_ret'] <= -0.05:
        results.append(('ThreeBarBear', 2, -1))

    # S8: NarrowBreak_Bear
    if day['or15_narrow'] and or15 and not or15['bullish']:
        results.append(('NarrowBreak_Bear', 14, -1))

    # R5: StrongBody_BearTrend — strong bear + below 20d SMA
    if (day['fb_body_ratio'] > 0.80 and not day['fb_bullish'] and fb_ret <= -0.03
        and day['above_20d'] is False):
        results.append(('StrongBody_BearTrend', 0, -1))

    # C5: TwoBarBear
    if day['fb2_bear'] and day['fb2_ret'] <= -0.04:
        results.append(('TwoBarBear', 1, -1))

    # C6: Bear_VIXRise — bearish fb + rising VIX
    if not day['fb_bullish'] and fb_ret <= -0.03 and day['vix_change_pct'] >= 2:
        results.append(('Bear_VIXRise', 0, -1))

    # C7: Bear_GapCont — gap down + bear first bar
    if not day['fb_bullish'] and fb_ret <= -0.02 and -0.40 <= gap <= -0.05:
        results.append(('Bear_GapCont', 0, -1))

    # StrongMom_Bear — strong bearish momentum
    if not day['fb_bullish'] and fb_ret <= -0.05 and day['fb_body_ratio'] > 0.70:
        results.append(('StrongMom_Bear', 0, -1))

    # T4: TripleBear_BodyTrendGap — body + below trend + gap down
    if (day['fb_body_ratio'] > 0.70 and not day['fb_bullish'] and fb_ret <= -0.03
        and day['above_20d'] is False and gap <= -0.05):
        results.append(('TripleBear_BodyTrendGap', 0, -1))

    # T8: OR15_LowClose_BearTrend
    if (or15 and not or15['bullish'] and or15['close_loc'] < 0.25
        and or15['ret'] <= -0.05 and day['above_20d'] is False):
        results.append(('OR15_LowClose_BearTrend', 14, -1))

    # FlatOpen_Bear
    if (abs(gap) <= 0.08 and not day['fb_bullish'] and fb_ret <= -0.04
        and day['fb_body_ratio'] > 0.65):
        results.append(('FlatOpen_Bear', 0, -1))

    # SmallGapCont_Bear
    if (-0.25 <= gap <= -0.05 and not day['fb_bullish'] and fb_ret <= -0.02
        and day['fb_body_ratio'] > 0.55):
        results.append(('SmallGapCont_Bear', 0, -1))

    # RSI_OverboughtFade
    if day['rsi5'] > 75 and not day['fb_bullish'] and fb_ret <= -0.03:
        results.append(('RSI_OverboughtFade', 0, -1))

    # OR_Aligned_Bear
    if (or5 and or15 and not or5['bullish'] and not or15['bullish']
        and or15['ret'] <= -0.05):
        results.append(('OR_Aligned_Bear', 14, -1))

    # OR15_LowClose
    if (or15 and not or15['bullish'] and or15['close_loc'] < 0.20
        and or15['ret'] <= -0.05):
        results.append(('OR15_LowClose', 14, -1))

    # --- Additional bear signals not in original scanner ---

    # StrongBody_Bear_PrevBear — mirror of StrongBody_PrevBull
    if (day['fb_body_ratio'] > 0.80 and not day['fb_bullish'] and fb_ret <= -0.03
        and not day['prev_bullish']):
        results.append(('StrongBody_BearPrevBear', 0, -1))

    # StrongBody_Bear_Below50d — deep trend confirmation
    if (day['fb_body_ratio'] > 0.80 and not day['fb_bullish'] and fb_ret <= -0.03
        and day['above_50d'] is False):
        results.append(('StrongBody_Bear50d', 0, -1))

    # Bear_GapCont_BelowTrend
    if (not day['fb_bullish'] and fb_ret <= -0.02 and -0.40 <= gap <= -0.05
        and day['above_20d'] is False):
        results.append(('Bear_GapCont_Trend', 0, -1))

    # TwoBarBear_BelowTrend
    if day['fb2_bear'] and day['fb2_ret'] <= -0.04 and day['above_20d'] is False:
        results.append(('TwoBarBear_Trend', 1, -1))

    # StrongBody_Bear_CleanMove — low upper wick (no rejection)
    if (day['fb_body_ratio'] > 0.75 and not day['fb_bullish'] and fb_ret <= -0.03
        and day['fb_upper_wick'] < 0.12):
        results.append(('StrongBody_CleanBear', 0, -1))

    return results


# ═══════════════════════════════════════════════════════════════
# EXIT SETS (same as scanner)
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    'Micro_2_1_3': {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
    'Micro_2_1.5_5': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
    'Micro_3_1_5': {'pt_pts': 3, 'sl_pts': 1, 'ts_min': 5},
    'Micro_3_1.5_5': {'pt_pts': 3, 'sl_pts': 1.5, 'ts_min': 5},
    'Micro_3_2_5': {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 5},
    'Micro_4_2_5': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 5},
    'Micro_4_2_8': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
    'Micro_5_2_5': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 5},
    'Micro_5_2_8': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 8},
    'Micro_5_2_10': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10},
    'Micro_5_3_10': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 10},
    'Asym_8_2_10': {'pt_pts': 8, 'sl_pts': 2, 'ts_min': 10},
    'Asym_8_3_15': {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15},
    'Asym_10_3_15': {'pt_pts': 10, 'sl_pts': 3, 'ts_min': 15},
    'Asym_10_4_15': {'pt_pts': 10, 'sl_pts': 4, 'ts_min': 15},
    'Asym_12_4_20': {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
    'Asym_15_5_20': {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 20},
    'Std_5_3_10': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 10},
    'Std_8_4_15': {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
    'Std_10_5_15': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
    'Std_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30},
    'Std_15_8_30': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 30},
    'Adpt_5_2_10': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
    'Adpt_8_3_15': {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15, 'vix_mult': True},
    'Adpt_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30, 'vix_mult': True},
    'Trail03_SL2_10': {'trail_pct': 0.03, 'sl_pts': 2, 'ts_min': 10},
    'Trail05_SL3_15': {'trail_pct': 0.05, 'sl_pts': 3, 'ts_min': 15},
    'Trail05_SL2_10': {'trail_pct': 0.05, 'sl_pts': 2, 'ts_min': 10},
}

# ═══════════════════════════════════════════════════════════════
# FILTERS (same as scanner)
# ═══════════════════════════════════════════════════════════════
FILTERS = {
    'All': lambda d: True,
    'VeryLow': lambda d: d['vix'] < 14,
    'Low': lambda d: d['vix'] < 18,
    'lt20': lambda d: d['vix'] < 20,
    'lt22': lambda d: d['vix'] < 22,
    'Mid': lambda d: 18 <= d['vix'] < 25,
    'High': lambda d: d['vix'] >= 25,
    'Falling': lambda d: d['vix_change_pct'] <= -2,
    'Rising': lambda d: d['vix_change_pct'] >= 3,
    'TueWed': lambda d: d['dow'] in (1, 2),
    'NotMon': lambda d: d['dow'] != 0,
    'Fri': lambda d: d['dow'] == 4,
    'Mon': lambda d: d['dow'] == 0,
    'lt20_TueWed': lambda d: d['vix'] < 20 and d['dow'] in (1, 2),
    'lt20_NotMon': lambda d: d['vix'] < 20 and d['dow'] != 0,
    'Low_Falling': lambda d: d['vix'] < 18 and d['vix_change_pct'] <= -1,
    # Additional bear-relevant filters
    'High_Rising': lambda d: d['vix'] >= 22 and d['vix_change_pct'] >= 1,
    'Mid_Rising': lambda d: 18 <= d['vix'] < 30 and d['vix_change_pct'] >= 1,
    'BelowTrend': lambda d: d['above_20d'] is False,
    'BelowTrend_High': lambda d: d['above_20d'] is False and d['vix'] >= 22,
}

# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════
def compute_stats(trades, label=''):
    if len(trades) < 10:
        return None
    pts = [t['und_pts'] for t in trades]
    n = len(pts)
    avg = statistics.mean(pts)
    tot = sum(pts)
    wr = sum(1 for p in pts if p > 0) / n * 100
    std = statistics.stdev(pts) if n > 1 else 0
    sharpe = avg / std if std > 0 else 0

    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else 99

    cum = 0; peak_cum = 0; max_dd = 0
    for p in pts:
        cum += p
        if cum > peak_cum: peak_cum = cum
        dd = peak_cum - cum
        if dd > max_dd: max_dd = dd

    max_consec_loss = 0; curr_loss = 0
    for p in pts:
        if p <= 0:
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    monthly = defaultdict(list)
    for t in trades:
        monthly[t['date'][:7]].append(t['und_pts'])
    monthly_pnl = {ym: sum(v) for ym, v in monthly.items()}
    months_positive = sum(1 for v in monthly_pnl.values() if v > 0)
    months_total = len(monthly_pnl)
    monthly_wr = months_positive / months_total * 100 if months_total > 0 else 0

    cum_pts = []; c = 0
    for p in pts:
        c += p; cum_pts.append(c)
    if len(cum_pts) > 5:
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(cum_pts)
        ss_xy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(cum_pts))
        ss_xx = sum((i - x_mean)**2 for i in range(n))
        ss_yy = sum((y - y_mean)**2 for y in cum_pts)
        r_sq = (ss_xy**2) / (ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0
    else:
        r_sq = 0

    avg_hold = statistics.mean(t['hold_mins'] for t in trades)

    return {
        'label': label, 'n': n, 'wr': round(wr, 1),
        'avg_pts': round(avg, 2), 'total_pts': round(tot, 1),
        'sharpe': round(sharpe, 3), 'pf': round(pf, 2),
        'max_dd': round(max_dd, 1),
        'max_consec_loss': max_consec_loss,
        'monthly_wr': round(monthly_wr, 1),
        'equity_r2': round(r_sq, 3),
        'avg_hold': round(avg_hold, 1),
    }


# ═══════════════════════════════════════════════════════════════
# OPTION PRICING — real SPXW put data
# ═══════════════════════════════════════════════════════════════
_opt_cache = {}

def load_option_bars(ticker, date_str):
    key = f"{ticker}_{date_str}"
    if key in _opt_cache: return _opt_cache[key]
    fn = ticker.replace(':', '_') + f'_{date_str}.json'
    path = CACHE_DIR / fn
    if not path.exists():
        _opt_cache[key] = None; return None
    with open(path) as f:
        data = json.load(f)
    if not data:
        _opt_cache[key] = None; return None
    bars = []
    for bar in data:
        ts = bar['t'] / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        month = dt.month
        et = dt - timedelta(hours=4 if 3 <= month <= 10 else 5)
        mins = et.hour * 60 + et.minute
        if mins < 570 or mins >= 960: continue
        bars.append({'mins': mins, 'o': bar['o'], 'h': bar['h'], 'l': bar['l'], 'c': bar['c']})
    bars.sort(key=lambda x: x['mins'])
    _opt_cache[key] = bars if bars else None
    return _opt_cache[key]

def build_ticker(date_str, cp, strike):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return f"O:SPXW{dt.strftime('%y%m%d')}{cp}{int(strike*1000):08d}"

def find_bar(bars, target_mins, tol=3):
    if not bars: return None
    best = min(bars, key=lambda b: abs(b['mins'] - target_mins))
    return best if abs(best['mins'] - target_mins) <= tol else None

def get_strike(price, rnd=5):
    return round(price / rnd) * rnd

def price_with_provenance(date, entry_mins, exit_mins, spx_price, direction, struct):
    """Returns (pnl, ticker_info) or (None, None)"""
    atm = get_strike(spx_price)

    def try_single(cp, strike):
        t = build_ticker(date, cp, strike)
        bars = load_option_bars(t, date)
        if not bars: return None, None
        e, x = find_bar(bars, entry_mins), find_bar(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None, None
        pnl = (x['c'] - e['c']) * 100
        info = {'ticker': t, 'entry_px': round(e['c'],2), 'exit_px': round(x['c'],2)}
        return round(pnl, 2), info

    def try_spread(cp, long_k, short_k):
        lt = build_ticker(date, cp, long_k)
        st = build_ticker(date, cp, short_k)
        lb, sb = load_option_bars(lt, date), load_option_bars(st, date)
        if not lb or not sb: return None, None
        le, lx = find_bar(lb, entry_mins), find_bar(lb, exit_mins)
        se, sx = find_bar(sb, entry_mins), find_bar(sb, exit_mins)
        if not all([le, lx, se, sx]): return None, None
        debit = le['c'] - se['c']
        credit = lx['c'] - sx['c']
        pnl = (credit - debit) * 100
        info = {'long_ticker': lt, 'short_ticker': st,
                'long_entry': round(le['c'],2), 'long_exit': round(lx['c'],2),
                'short_entry': round(se['c'],2), 'short_exit': round(sx['c'],2)}
        return round(pnl, 2), info

    def try_credit_spread(cp, sell_strike, buy_strike):
        """Credit spread: sell closer-to-money, buy further OTM for protection."""
        st = build_ticker(date, cp, sell_strike)
        lt = build_ticker(date, cp, buy_strike)
        sb, lb = load_option_bars(st, date), load_option_bars(lt, date)
        if not sb or not lb: return None, None
        se, sx = find_bar(sb, entry_mins), find_bar(sb, exit_mins)
        le, lx = find_bar(lb, entry_mins), find_bar(lb, exit_mins)
        if not all([se, sx, le, lx]): return None, None
        credit_recv = se['c'] - le['c']
        debit_close = sx['c'] - lx['c']
        pnl = (credit_recv - debit_close) * 100
        info = {'sell_ticker': st, 'buy_ticker': lt,
                'sell_entry': round(se['c'],2), 'sell_exit': round(sx['c'],2),
                'buy_entry': round(le['c'],2), 'buy_exit': round(lx['c'],2)}
        return round(pnl, 2), info

    if struct == 'long_put':
        return try_single('P', atm)
    elif struct == 'long_otm_put':
        return try_single('P', atm - 5)
    elif struct == 'long_itm_put':
        return try_single('P', atm + 5)
    elif struct == 'bear_put_5':
        # Buy ATM put, sell ATM-5 put (debit spread)
        return try_spread('P', atm, atm - 5)
    elif struct == 'bear_put_10':
        return try_spread('P', atm, atm - 10)
    elif struct == 'credit_call_5':
        # Sell ATM+5 call, buy ATM+10 call (bear call credit spread)
        return try_credit_spread('C', atm + 5, atm + 10)
    return None, None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 160)
    print("SHORT-SIDE EDGE SCANNER — Bearish Signals + Real SPXW Put Option Pricing")
    print("=" * 160)

    data = load_all_data()
    days = extract_features(*data)

    # Pre-compute bearish signals
    print("Generating bearish signals…")
    day_signals = {}
    sig_counts = defaultdict(int)
    for day in days:
        sigs = bear_signals(day)
        day_signals[day['date']] = sigs
        for name, _, _ in sigs:
            sig_counts[name] += 1

    print(f"  Bearish signal frequency across {len(days)} days:")
    for name, count in sorted(sig_counts.items(), key=lambda x: -x[1]):
        pct = count / len(days) * 100
        print(f"    {name:35s}: {count:>5} fires ({pct:.1f}%)")

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Underlying scan
    # ═══════════════════════════════════════════════════════════════
    total = len(sig_counts) * len(FILTERS) * len(EXIT_SETS)
    print(f"\n{'='*160}")
    print(f"PHASE 1: UNDERLYING SCAN — {len(sig_counts)} bear signals × {len(FILTERS)} filters × {len(EXIT_SETS)} exits = {total} combos")
    print(f"{'='*160}")

    all_results = []
    combo = 0

    for sig_name in sorted(sig_counts.keys()):
        for filt_name, filt_fn in FILTERS.items():
            for exit_name, exit_params in EXIT_SETS.items():
                combo += 1
                if combo % 5000 == 0:
                    print(f"  [{combo}/{total}]…", flush=True)

                trades = []
                for day in days:
                    if not filt_fn(day): continue
                    for sname, entry_idx, direction in day_signals[day['date']]:
                        if sname != sig_name: continue
                        if entry_idx >= len(day['bars']) - 3: continue
                        ep = dict(exit_params)
                        if ep.get('vix_mult'):
                            ep['_vix'] = day['vix']
                        trade = simulate_trade(day['bars'], entry_idx, direction, ep)
                        trade['date'] = day['date']
                        trade['vix'] = day['vix']
                        trades.append(trade)
                        break  # one trade per day per signal

                stats = compute_stats(trades, f"{sig_name}|{filt_name}|{exit_name}")
                if stats and stats['sharpe'] > 0.20 and stats['pf'] > 1.2 and stats['n'] >= 15:
                    stats['signal'] = sig_name
                    stats['filter'] = filt_name
                    stats['exit_set'] = exit_name
                    stats['trades'] = trades
                    all_results.append(stats)

    print(f"\n  {combo} combos scanned, {len(all_results)} edges with Sharpe>0.20 & PF>1.2 & N>=15")

    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*180}")
    print(f"TOP 80 BEAR EDGES BY SHARPE (underlying SPX points)")
    print(f"{'='*180}")
    print(f"{'Label':>65s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
          f"{'Sharpe':>7} {'PF':>6} {'MaxDD':>8} {'CL':>4} {'MoWR%':>6} {'R²':>5} {'Hold':>6}")
    print("-" * 180)

    for r in all_results[:80]:
        print(f"{r['label'][:65]:>65s} {r['n']:>5} {r['wr']:>5.1f}% "
              f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['max_dd']:>7.1f} "
              f"{r['max_consec_loss']:>4} {r['monthly_wr']:>5.1f}% "
              f"{r['equity_r2']:>5.3f} {r['avg_hold']:>5.1f}m")

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: OOS Validation
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"PHASE 2: OUT-OF-SAMPLE VALIDATION — IS: 2018-2022 vs OOS: 2023-2026")
    print(f"{'='*180}")

    validated = []
    for r in all_results[:80]:
        trades = r.get('trades', [])
        if not trades: continue
        is_t = [t for t in trades if t['date'] < '2023-01-01']
        oos_t = [t for t in trades if t['date'] >= '2023-01-01']
        is_pts = [t['und_pts'] for t in is_t]
        oos_pts = [t['und_pts'] for t in oos_t]

        if len(is_pts) >= 8 and len(oos_pts) >= 8:
            is_avg = statistics.mean(is_pts)
            is_std = statistics.stdev(is_pts) if len(is_pts) > 1 else 0
            is_sh = is_avg / is_std if is_std > 0 else 0
            oos_avg = statistics.mean(oos_pts)
            oos_std = statistics.stdev(oos_pts) if len(oos_pts) > 1 else 0
            oos_sh = oos_avg / oos_std if oos_std > 0 else 0

            holds = oos_sh > 0.05 and oos_avg > 0
            verdict = 'HOLDS' if holds else 'DEGRADES'
            print(f"  {r['label'][:60]:>60s}  IS: Sh={is_sh:>6.3f} N={len(is_pts):>4}  "
                  f"OOS: Sh={oos_sh:>6.3f} N={len(oos_pts):>4}  [{verdict}]")
            if holds:
                validated.append({
                    'edge': r, 'is_sharpe': round(is_sh,3), 'oos_sharpe': round(oos_sh,3),
                    'is_n': len(is_pts), 'oos_n': len(oos_pts),
                })
        elif len(is_pts) >= 8:
            print(f"  {r['label'][:60]:>60s}  IS: N={len(is_pts):>4}  OOS: <8 trades ({len(oos_pts)})")

    print(f"\n  {len(validated)} of {min(80, len(all_results))} edges PASSED OOS validation")

    if len(validated) == 0:
        print("\n  NO BEAR EDGES SURVIVED OOS VALIDATION.")
        print("  This is a meaningful finding — short-side edges are harder to extract")
        print("  in an equity index with a long-term upward drift.")
        # Save results anyway
        out_dir = SCRIPT_DIR / 'backtest_results'
        out_dir.mkdir(exist_ok=True)
        und_summary = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results[:200]]
        with open(out_dir / 'bear_underlying_results.json', 'w') as f:
            json.dump(und_summary, f, indent=2)
        print(f"\n  Saved {len(und_summary)} underlying bear results for review")
        return

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Price with real SPXW options
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"PHASE 3: PRICING TOP {min(30, len(validated))} VALIDATED BEAR EDGES WITH REAL SPXW OPTIONS")
    print(f"{'='*180}")

    bear_structs = ['long_put', 'long_otm_put', 'long_itm_put', 'bear_put_5', 'bear_put_10', 'credit_call_5']
    opt_results = []

    for vi, v in enumerate(validated[:30]):
        r = v['edge']
        trades = r['trades']
        if not trades: continue

        print(f"\n{'─'*160}")
        print(f"  [{vi+1}] {r['label']} — {len(trades)} trades, UndSharpe={r['sharpe']}")

        for struct in bear_structs:
            priced = []
            missed = 0
            details = []

            for trade in trades:
                d = trade['date']
                pnl, info = price_with_provenance(
                    d, trade['entry_mins'], trade['exit_mins'],
                    trade['entry_price'], trade['direction'], struct)
                if pnl is not None:
                    priced.append(pnl)
                    details.append({
                        'date': d, 'opt_pnl': pnl,
                        'entry_time': trade['entry_time'],
                        'exit_time': trade['exit_time'],
                        'spx_entry': trade['entry_price'],
                        'spx_exit': trade['exit_price'],
                        'hold_mins': trade['hold_mins'],
                        'exit_reason': trade['exit_reason'],
                        'und_pts': trade['und_pts'],
                        'vix': trade['vix'],
                        'info': info,
                    })
                else:
                    missed += 1

            if len(priced) < 8:
                print(f"    {struct:20s}  {len(priced)} priced ({missed} missed) — insufficient")
                continue

            avg = statistics.mean(priced)
            tot = sum(priced)
            wins = sum(1 for p in priced if p > 0)
            wr = wins / len(priced) * 100
            std = statistics.stdev(priced) if len(priced) > 1 else 0
            sh = avg / std if std > 0 else 0
            gw = sum(p for p in priced if p > 0)
            gl = abs(sum(p for p in priced if p <= 0))
            pf = gw / gl if gl > 0 else 99

            cum = 0; pk = 0; mdd = 0
            for p in priced:
                cum += p
                if cum > pk: pk = cum
                dd = pk - cum
                if dd > mdd: mdd = dd

            # R²
            cum_list = []; c = 0
            for p in priced:
                c += p; cum_list.append(c)
            n = len(priced)
            x_m = (n-1)/2; y_m = statistics.mean(cum_list)
            ss_xy = sum((i-x_m)*(y-y_m) for i,y in enumerate(cum_list))
            ss_xx = sum((i-x_m)**2 for i in range(n))
            ss_yy = sum((y-y_m)**2 for y in cum_list)
            r2 = (ss_xy**2)/(ss_xx*ss_yy) if ss_xx>0 and ss_yy>0 else 0

            # IS/OOS option-level
            is_pnl = [d['opt_pnl'] for d in details if d['date'] < '2023-01-01']
            oos_pnl = [d['opt_pnl'] for d in details if d['date'] >= '2023-01-01']
            is_sh = statistics.mean(is_pnl)/statistics.stdev(is_pnl) if len(is_pnl)>5 and statistics.stdev(is_pnl)>0 else 0
            oos_sh = statistics.mean(oos_pnl)/statistics.stdev(oos_pnl) if len(oos_pnl)>5 and statistics.stdev(oos_pnl)>0 else 0

            v_str = "HOLDS" if oos_sh > 0.05 else "WEAK"
            miss_pct = missed / (len(priced) + missed) * 100

            print(f"    {struct:20s}  N={n:>4} ({100-miss_pct:.0f}%)  "
                  f"WR={wr:>5.1f}%  Avg=${avg:>+8.2f}  Tot=${tot:>+10.2f}  "
                  f"Sh={sh:>6.3f}  PF={pf:>5.2f}  DD=${mdd:>8.2f}  R²={r2:.3f}  "
                  f"IS={is_sh:.3f}  OOS={oos_sh:.3f}  [{v_str}]")

            opt_results.append({
                'label': f"{r['label']}|{struct}",
                'signal': r['signal'], 'filter': r['filter'],
                'exit_set': r['exit_set'], 'struct': struct,
                'n': n, 'wr': round(wr,1), 'avg_pnl': round(avg,2),
                'total_pnl': round(tot,2), 'sharpe': round(sh,3),
                'pf': round(pf,2), 'max_dd': round(mdd,2), 'r2': round(r2,3),
                'is_sh': round(is_sh,3), 'oos_sh': round(oos_sh,3),
                'und_sharpe': r['sharpe'],
                'priced_pct': round(100-miss_pct,1),
                'details': details,
            })

    # ═══════════════════════════════════════════════════════════════
    # FINAL RANKING
    # ═══════════════════════════════════════════════════════════════
    opt_results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n\n{'='*200}")
    print(f"FINAL RANKING — Bear Option-Priced Edges by Sharpe")
    print(f"{'='*200}")
    print(f"{'Label':>75s} {'N':>5} {'%':>5} {'WR%':>6} {'AvgPnL':>10} {'TotPnL':>12} "
          f"{'Sharpe':>7} {'PF':>6} {'MaxDD':>10} {'R²':>5} {'IS':>7} {'OOS':>7} {'UndSh':>7}")
    print("-" * 200)

    for r in opt_results[:50]:
        print(f"{r['label'][:75]:>75s} {r['n']:>5} {r.get('priced_pct',0):>4.0f}% {r['wr']:>5.1f}% "
              f"${r['avg_pnl']:>+9.2f} ${r['total_pnl']:>+11.2f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} ${r['max_dd']:>9.2f} "
              f"{r['r2']:>5.3f} {r['is_sh']:>7.3f} {r['oos_sh']:>7.3f} "
              f"{r['und_sharpe']:>7.3f}")

    # ═══════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════
    out_dir = SCRIPT_DIR / 'backtest_results'
    out_dir.mkdir(exist_ok=True)

    save_results = [{k: v for k, v in r.items() if k != 'details'} for r in opt_results[:100]]
    with open(out_dir / 'bear_option_results.json', 'w') as f:
        json.dump(save_results, f, indent=2)

    und_summary = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results[:200]]
    with open(out_dir / 'bear_underlying_results.json', 'w') as f:
        json.dump(und_summary, f, indent=2)

    # Export individual trades for any edges with positive OOS option Sharpe
    good_edges = [r for r in opt_results if r['oos_sh'] > 0.05 and r['sharpe'] > 0.15]
    if good_edges:
        all_bear_trades = []
        for edge in good_edges:
            for d in edge['details']:
                record = {
                    'date': d['date'],
                    'strategy': edge['label'],
                    'structure': edge['struct'],
                    'direction': 'SHORT',
                    'spx_entry': d['spx_entry'],
                    'spx_exit': d['spx_exit'],
                    'entry_time': d['entry_time'],
                    'exit_time': d['exit_time'],
                    'hold_mins': d['hold_mins'],
                    'exit_reason': d['exit_reason'],
                    'und_pts': d['und_pts'],
                    'vix': d['vix'],
                    'opt_pnl': d['opt_pnl'],
                }
                info = d.get('info', {})
                if info:
                    if 'ticker' in info:
                        record['opt_ticker'] = info['ticker']
                        record['opt_entry_px'] = info['entry_px']
                        record['opt_exit_px'] = info['exit_px']
                    elif 'long_ticker' in info:
                        record['opt_ticker'] = f"{info['long_ticker']}|{info['short_ticker']}"
                        record['opt_entry_px'] = f"{info['long_entry']}/{info['short_entry']}"
                        record['opt_exit_px'] = f"{info['long_exit']}/{info['short_exit']}"
                    elif 'sell_ticker' in info:
                        record['opt_ticker'] = f"{info['sell_ticker']}|{info['buy_ticker']}"
                        record['opt_entry_px'] = f"{info['sell_entry']}/{info['buy_entry']}"
                        record['opt_exit_px'] = f"{info['sell_exit']}/{info['buy_exit']}"
                all_bear_trades.append(record)

        with open(out_dir / 'bear_verified_trades.json', 'w') as f:
            json.dump(sorted(all_bear_trades, key=lambda x: x['date']), f, indent=2)
        print(f"\n  Saved {len(all_bear_trades)} verified bear trades")

    print(f"\n  Saved {len(save_results)} option-priced bear results to bear_option_results.json")
    print(f"  Saved {len(und_summary)} underlying bear results to bear_underlying_results.json")

    # Summary
    print(f"\n{'='*160}")
    print(f"SUMMARY")
    print(f"{'='*160}")
    print(f"  Bear signals tested: {len(sig_counts)}")
    print(f"  Total combos scanned: {combo}")
    print(f"  Edges found (underlying): {len(all_results)}")
    print(f"  OOS validated: {len(validated)}")
    print(f"  Option-priced results: {len(opt_results)}")
    if opt_results:
        best = opt_results[0]
        print(f"\n  BEST BEAR OPTION-PRICED SHARPE: {best['sharpe']}")
        print(f"     {best['label']}")
        print(f"     N={best['n']}  WR={best['wr']}%  PF={best['pf']}  R²={best['r2']}")
        print(f"     IS={best['is_sh']}  OOS={best['oos_sh']}")
    if good_edges:
        print(f"\n  EDGES WITH POSITIVE OOS OPTION SHARPE: {len(good_edges)}")
        for e in good_edges[:10]:
            print(f"    {e['label']:>70s}  Sh={e['sharpe']:.3f}  OOS={e['oos_sh']:.3f}  N={e['n']}")
    else:
        print(f"\n  NO BEAR EDGES WITH POSITIVE OOS OPTION SHARPE FOUND.")
        print(f"  The equity index long-term upward drift makes consistent short edges very difficult.")

    print(f"\n{'='*160}")
    print("DONE")
    print(f"{'='*160}")


if __name__ == '__main__':
    main()
