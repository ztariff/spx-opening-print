#!/usr/bin/env python3
"""
Closing Print Scanner v2 — Comprehensive
==========================================
Finds ALL viable edges in the final 2 hours (2:00–4:00 PM ET).
Both long and short, across all VIX regimes, with multiple option structures.

Phase 1: Underlying scan — signals × filters × exits
Phase 2: IS/OOS validation (2018-2022 vs 2023-2026)
Phase 3: Price with real SPXW 0DTE options — multiple structures per edge

Option structures tested:
  BULLISH: long_call, long_itm_call, long_otm_call, bull_call_5, bull_call_10
  BEARISH: long_put, long_itm_put, long_otm_put, bear_put_5, credit_call_5

All CLAUDE.md rules apply. No fabricated data. No hindsight. Surface gaps.
"""

import csv, json, math, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════
def load_all_data():
    print("Loading data…")
    spx_1min = defaultdict(list)
    with open(SCRIPT_DIR / 'spx_1min_bars.csv') as f:
        for row in csv.DictReader(f):
            d = row['date']; t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960: continue
            spx_1min[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
                'v': float(row.get('volume', 0)),
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

    sma20 = {}
    for i in range(19, len(sorted_dates)):
        sma20[sorted_dates[i]] = sum(closes[i-19:i+1]) / 20
    sma50 = {}
    for i in range(49, len(sorted_dates)):
        sma50[sorted_dates[i]] = sum(closes[i-49:i+1]) / 50

    rsi5 = {}
    for i in range(5, len(sorted_dates)):
        gains, losses = [], []
        for j in range(i-4, i+1):
            chg = closes[j] - closes[j-1]
            if chg > 0: gains.append(chg)
            else: losses.append(abs(chg))
        ag = statistics.mean(gains) if gains else 0
        al = statistics.mean(losses) if losses else 0
        if al == 0 and ag == 0: rsi5[sorted_dates[i]] = 50
        elif al == 0: rsi5[sorted_dates[i]] = 100
        else: rsi5[sorted_dates[i]] = 100 - 100/(1+ag/al)

    prev_days = {}
    for i in range(5, len(sorted_dates)):
        prev_days[sorted_dates[i]] = [spx_daily[sorted_dates[i-j]] for j in range(1, 6)]

    vix_sorted = sorted(vix_daily.keys())
    vix_prev_close = {}
    for i in range(1, len(vix_sorted)):
        vix_prev_close[vix_sorted[i]] = vix_daily[vix_sorted[i-1]]['c']

    atr5 = {}
    for i in range(5, len(sorted_dates)):
        trs = []
        for j in range(i-4, i+1):
            d2, d1 = sorted_dates[j], sorted_dates[j-1]
            hi, lo, pc = spx_daily[d2]['h'], spx_daily[d2]['l'], spx_daily[d1]['c']
            trs.append(max(hi-lo, abs(hi-pc), abs(lo-pc)))
        atr5[sorted_dates[i]] = statistics.mean(trs)

    print(f"  SPX 1min: {len(spx_1min)} days")
    return spx_1min, spx_daily, vix_daily, sma20, sma50, prev_days, vix_prev_close, rsi5, atr5


# ═══════════════════════════════════════════════════════════════
# FEATURE EXTRACTION
# ═══════════════════════════════════════════════════════════════
def extract_features(spx_1min, spx_daily, vix_daily, sma20, sma50,
                     prev_days, vix_prev_close, rsi5, atr5):
    print("Extracting closing-window features…")
    days = []
    for d in sorted(spx_1min.keys()):
        if d < START_DATE: continue
        all_bars = spx_1min[d]
        dd = spx_daily.get(d); vd = vix_daily.get(d)
        prev = prev_days.get(d)
        if not dd or not vd or not prev or len(all_bars) < 300: continue

        morning = [b for b in all_bars if b['mins'] < 840]
        afternoon = [b for b in all_bars if b['mins'] >= 840]
        if len(morning) < 200 or len(afternoon) < 5: continue

        vix_open = vd['o']
        vix_pc = vix_prev_close.get(d, vix_open)
        vix_change = (vix_open - vix_pc) / vix_pc * 100 if vix_pc > 0 else 0
        prev_close = prev[0]['c']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100
        prev_bullish = prev[0]['c'] > prev[0]['o']
        prev_range = prev[0]['h'] - prev[0]['l']
        prev_close_loc = (prev[0]['c'] - prev[0]['l']) / prev_range if prev_range > 0 else 0.5

        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        # Morning summary
        mo = morning[0]['o']; mc = morning[-1]['c']
        mh = max(b['h'] for b in morning); ml = min(b['l'] for b in morning)
        mr = mh - ml
        morn_ret = (mc - mo) / mo * 100
        morn_bullish = mc > mo
        morn_close_loc = (mc - ml) / mr if mr > 0 else 0.5
        range_pos = (mc - ml) / mr if mr > 0 else 0.5

        # Last hour of morning (1-2PM)
        lhm = [b for b in morning if b['mins'] >= 780]
        lh_ret = (lhm[-1]['c'] - lhm[0]['o']) / lhm[0]['o'] * 100 if lhm else 0
        new_high_lh = max(b['h'] for b in lhm) >= mh * 0.999 if lhm else False
        new_low_lh = min(b['l'] for b in lhm) <= ml * 1.001 if lhm else False

        # Last 15 and 5 bars before 2PM
        l15 = morning[-15:]
        m15_ret = (l15[-1]['c'] - l15[0]['o']) / l15[0]['o'] * 100
        l5 = morning[-5:]
        m5_ret = (l5[-1]['c'] - l5[0]['o']) / l5[0]['o'] * 100
        m5_consec_bull = all(b['c'] > b['o'] for b in l5)
        m5_consec_bear = all(b['c'] < b['o'] for b in l5)

        # Morning body ratio
        morn_body_ratio = abs(mc - mo) / mr if mr > 0 else 0
        intraday_vol = mr / mo * 100

        # Closing first bar (CFB = 14:00)
        cfb = afternoon[0]
        cfb_ret = (cfb['c'] - cfb['o']) / cfb['o'] * 100
        cfb_bull = cfb['c'] > cfb['o']
        cfb_br = abs(cfb['c'] - cfb['o']) / (cfb['h'] - cfb['l']) if (cfb['h'] - cfb['l']) > 0 else 0

        # 2-bar closing
        if len(afternoon) > 1:
            cfb2_bull = afternoon[0]['c'] > afternoon[0]['o'] and afternoon[1]['c'] > afternoon[1]['o']
            cfb2_bear = afternoon[0]['c'] < afternoon[0]['o'] and afternoon[1]['c'] < afternoon[1]['o']
            cfb2_ret = (afternoon[1]['c'] - afternoon[0]['o']) / afternoon[0]['o'] * 100
        else:
            cfb2_bull = cfb2_bear = False; cfb2_ret = 0

        # 3-bar closing
        c3 = afternoon[:3]
        if len(c3) == 3:
            cfb3_bull = all(b['c'] > b['o'] for b in c3)
            cfb3_bear = all(b['c'] < b['o'] for b in c3)
            cfb3_ret = (c3[-1]['c'] - c3[0]['o']) / c3[0]['o'] * 100
        else:
            cfb3_bull = cfb3_bear = False; cfb3_ret = 0

        # COR5 (14:00-14:04)
        c5 = afternoon[:5]
        if len(c5) == 5:
            c5h = max(b['h'] for b in c5); c5l = min(b['l'] for b in c5)
            c5r = c5h - c5l
            cor5_ret = (c5[-1]['c'] - c5[0]['o']) / c5[0]['o'] * 100
            cor5_bull = c5[-1]['c'] > c5[0]['o']
            cor5_cloc = (c5[-1]['c'] - c5l) / c5r if c5r > 0 else 0.5
        else:
            cor5_ret = 0; cor5_bull = False; cor5_cloc = 0.5

        # COR15 (14:00-14:14)
        c15 = afternoon[:15]
        if len(c15) >= 15:
            c15h = max(b['h'] for b in c15); c15l = min(b['l'] for b in c15)
            c15r = c15h - c15l
            cor15_ret = (c15[-1]['c'] - c15[0]['o']) / c15[0]['o'] * 100
            cor15_bull = c15[-1]['c'] > c15[0]['o']
            cor15_cloc = (c15[-1]['c'] - c15l) / c15r if c15r > 0 else 0.5
        else:
            cor15_ret = 0; cor15_bull = False; cor15_cloc = 0.5

        # COR30 (14:00-14:29)
        c30 = afternoon[:30]
        if len(c30) >= 30:
            c30h = max(b['h'] for b in c30); c30l = min(b['l'] for b in c30)
            c30r = c30h - c30l
            cor30_ret = (c30[-1]['c'] - c30[0]['o']) / c30[0]['o'] * 100
            cor30_bull = c30[-1]['c'] > c30[0]['o']
            cor30_cloc = (c30[-1]['c'] - c30l) / c30r if c30r > 0 else 0.5
        else:
            cor30_ret = 0; cor30_bull = False; cor30_cloc = 0.5

        days.append({
            'date': d, 'afternoon': afternoon, 'morning': morning,
            'open': dd['o'], 'close': dd['c'],
            'vix': vix_open, 'vix_change': vix_change,
            'gap_pct': gap_pct,
            'prev_bullish': prev_bullish, 'prev_close_loc': prev_close_loc,
            'above_20d': above_20d, 'above_50d': above_50d, 'dow': dow,
            'rsi5': rsi5.get(d, 50), 'atr5': atr5.get(d, 30),
            # Morning
            'morn_ret': morn_ret, 'morn_bullish': morn_bullish,
            'morn_close_loc': morn_close_loc, 'range_pos': range_pos,
            'morn_body_ratio': morn_body_ratio, 'intraday_vol': intraday_vol,
            'lh_ret': lh_ret, 'new_high_lh': new_high_lh, 'new_low_lh': new_low_lh,
            'm15_ret': m15_ret, 'm5_ret': m5_ret,
            'm5_consec_bull': m5_consec_bull, 'm5_consec_bear': m5_consec_bear,
            'price_at_2pm': mc,
            # CFB
            'cfb': cfb, 'cfb_ret': cfb_ret, 'cfb_bull': cfb_bull, 'cfb_br': cfb_br,
            'cfb2_bull': cfb2_bull, 'cfb2_bear': cfb2_bear, 'cfb2_ret': cfb2_ret,
            'cfb3_bull': cfb3_bull, 'cfb3_bear': cfb3_bear, 'cfb3_ret': cfb3_ret,
            'cor5_ret': cor5_ret, 'cor5_bull': cor5_bull, 'cor5_cloc': cor5_cloc,
            'cor15_ret': cor15_ret, 'cor15_bull': cor15_bull, 'cor15_cloc': cor15_cloc,
            'cor30_ret': cor30_ret, 'cor30_bull': cor30_bull, 'cor30_cloc': cor30_cloc,
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# SIGNALS — BULL + BEAR
# ═══════════════════════════════════════════════════════════════
def closing_signals(d):
    """Returns list of (name, entry_idx_in_afternoon, direction)."""
    r = []
    cr = d['cfb_ret']; mr = d['morn_ret']

    # ── BULLISH CFB (entry at bar 0 close = 14:01) ──
    if d['cfb_br'] > 0.75 and d['cfb_bull'] and cr >= 0.03:
        r.append(('CFB_StrongBull', 0, 1))
    if d['cfb_bull'] and cr >= 0.02 and d['morn_bullish']:
        r.append(('CFB_MornCont', 0, 1))
    if d['cfb_bull'] and cr >= 0.02 and d['range_pos'] > 0.75:
        r.append(('CFB_NearHigh', 0, 1))
    if d['cfb_br'] > 0.70 and d['cfb_bull'] and cr >= 0.03 and d['above_20d'] is True:
        r.append(('CFB_Trend', 0, 1))
    if mr <= -0.15 and d['cfb_bull'] and cr >= 0.03:
        r.append(('CFB_DipBounce', 0, 1))
    if mr <= -0.20 and d['cfb_bull'] and cr >= 0.03 and d['range_pos'] < 0.40:
        r.append(('CFB_ReversalLow', 0, 1))
    if mr >= 0.20 and d['cfb_bull'] and cr >= 0.02 and d['range_pos'] > 0.70:
        r.append(('CFB_RallyExt', 0, 1))
    if abs(mr) <= 0.10 and d['cfb_bull'] and cr >= 0.04 and d['cfb_br'] > 0.65:
        r.append(('CFB_FlatBreak', 0, 1))
    if d['lh_ret'] >= 0.05 and d['cfb_bull'] and cr >= 0.02:
        r.append(('CFB_LHMom', 0, 1))
    if d['new_high_lh'] and d['cfb_bull'] and cr >= 0.02:
        r.append(('CFB_NewHighCont', 0, 1))
    if d['morn_bullish'] and d['prev_bullish'] and d['cfb_bull'] and cr >= 0.02:
        r.append(('CFB_TripleMom', 0, 1))
    if d['cfb_bull'] and cr >= 0.02 and d['above_20d'] is True and d['vix'] < 18:
        r.append(('CFB_LowVIX_Trend', 0, 1))
    if d['cfb_br'] > 0.75 and d['cfb_bull'] and cr >= 0.03 and d['intraday_vol'] < 0.60:
        r.append(('CFB_QuietStrong', 0, 1))
    # CFB bull + prev_bullish + trend
    if (d['cfb_bull'] and cr >= 0.03 and d['prev_bullish'] and d['above_20d'] is True):
        r.append(('CFB_TrendPrev', 0, 1))

    # ── BEARISH CFB ──
    if d['cfb_br'] > 0.75 and not d['cfb_bull'] and cr <= -0.03:
        r.append(('CFB_StrongBear', 0, -1))
    if not d['cfb_bull'] and cr <= -0.02 and not d['morn_bullish']:
        r.append(('CFB_BearCont', 0, -1))
    if not d['cfb_bull'] and cr <= -0.02 and d['range_pos'] < 0.25:
        r.append(('CFB_NearLow', 0, -1))
    if d['cfb_br'] > 0.70 and not d['cfb_bull'] and cr <= -0.03 and d['above_20d'] is False:
        r.append(('CFB_BearTrend', 0, -1))
    if mr >= 0.15 and not d['cfb_bull'] and cr <= -0.03:
        r.append(('CFB_RipFade', 0, -1))
    if mr >= 0.20 and not d['cfb_bull'] and cr <= -0.03 and d['range_pos'] > 0.60:
        r.append(('CFB_ReversalHigh', 0, -1))
    if not d['cfb_bull'] and cr <= -0.02 and d['above_20d'] is False and d['vix'] >= 18:
        r.append(('CFB_BearVIXTrend', 0, -1))
    if d['new_low_lh'] and not d['cfb_bull'] and cr <= -0.02:
        r.append(('CFB_NewLowCont', 0, -1))

    # ── 2-BAR ──
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.04:
        r.append(('C2_Bull', 1, 1))
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.03 and d['morn_bullish']:
        r.append(('C2_BullMorn', 1, 1))
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.03 and d['range_pos'] > 0.70:
        r.append(('C2_BullHigh', 1, 1))
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.04:
        r.append(('C2_Bear', 1, -1))
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.03 and not d['morn_bullish']:
        r.append(('C2_BearMorn', 1, -1))
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.03 and d['range_pos'] < 0.30:
        r.append(('C2_BearLow', 1, -1))

    # ── 3-BAR ──
    if d['cfb3_bull'] and d['cfb3_ret'] >= 0.05:
        r.append(('C3_Bull', 2, 1))
    if d['cfb3_bull'] and d['cfb3_ret'] >= 0.04 and d['above_20d'] is True:
        r.append(('C3_BullTrend', 2, 1))
    if d['cfb3_bear'] and d['cfb3_ret'] <= -0.05:
        r.append(('C3_Bear', 2, -1))
    if d['cfb3_bear'] and d['cfb3_ret'] <= -0.04 and d['above_20d'] is False:
        r.append(('C3_BearTrend', 2, -1))

    # ── COR5 (entry at bar 4 = 14:05) ──
    if d['cor5_bull'] and d['cor5_ret'] >= 0.04 and d['cor5_cloc'] > 0.70:
        r.append(('COR5_Bull', 4, 1))
    if d['cor5_bull'] and d['cor5_ret'] >= 0.03 and d['morn_bullish']:
        r.append(('COR5_BullMorn', 4, 1))
    if not d['cor5_bull'] and d['cor5_ret'] <= -0.04 and d['cor5_cloc'] < 0.30:
        r.append(('COR5_Bear', 4, -1))
    if not d['cor5_bull'] and d['cor5_ret'] <= -0.03 and not d['morn_bullish']:
        r.append(('COR5_BearMorn', 4, -1))

    # ── COR15 (entry at bar 14 = 14:15) ──
    if d['cor15_bull'] and d['cor15_ret'] >= 0.05 and d['cor15_cloc'] > 0.70:
        r.append(('COR15_Bull', 14, 1))
    if d['cor15_bull'] and d['cor15_ret'] >= 0.04 and d['above_20d'] is True:
        r.append(('COR15_BullTrend', 14, 1))
    if d['cor15_bull'] and d['cor15_ret'] >= 0.04 and d['morn_bullish']:
        r.append(('COR15_BullMorn', 14, 1))
    if not d['cor15_bull'] and d['cor15_ret'] <= -0.05 and d['cor15_cloc'] < 0.30:
        r.append(('COR15_Bear', 14, -1))
    if not d['cor15_bull'] and d['cor15_ret'] <= -0.04 and d['above_20d'] is False:
        r.append(('COR15_BearTrend', 14, -1))

    # ── COR30 (entry at bar 29 = 14:30) ──
    if d['cor30_bull'] and d['cor30_ret'] >= 0.06 and d['cor30_cloc'] > 0.70:
        r.append(('COR30_Bull', 29, 1))
    if d['cor30_bull'] and d['cor30_ret'] >= 0.05 and d['above_20d'] is True:
        r.append(('COR30_BullTrend', 29, 1))
    if not d['cor30_bull'] and d['cor30_ret'] <= -0.06 and d['cor30_cloc'] < 0.30:
        r.append(('COR30_Bear', 29, -1))
    if not d['cor30_bull'] and d['cor30_ret'] <= -0.05 and d['above_20d'] is False:
        r.append(('COR30_BearTrend', 29, -1))

    # ── MORNING-ONLY (entry at bar 0 open = 14:00) ──
    if d['morn_close_loc'] > 0.80 and d['morn_bullish'] and d['above_20d'] is True:
        r.append(('Morn_HighTrend', 0, 1))
    if mr >= 0.25 and d['new_high_lh'] and d['morn_close_loc'] > 0.70:
        r.append(('Morn_Rally', 0, 1))
    if mr <= -0.10 and d['morn_close_loc'] > 0.40 and d['lh_ret'] >= 0.05:
        r.append(('Morn_DipRecover', 0, 1))
    if abs(mr) <= 0.08 and d['m5_ret'] >= 0.04 and d['m5_consec_bull']:
        r.append(('Morn_QuietBreak', 0, 1))
    if d['morn_bullish'] and d['prev_bullish'] and d['above_20d'] is True and d['above_50d'] is True:
        r.append(('Morn_AllAligned', 0, 1))
    if d['morn_close_loc'] < 0.20 and not d['morn_bullish'] and d['above_20d'] is False:
        r.append(('Morn_LowTrend', 0, -1))
    if mr <= -0.25 and d['new_low_lh'] and d['morn_close_loc'] < 0.30:
        r.append(('Morn_Selloff', 0, -1))
    if abs(mr) <= 0.08 and d['m5_ret'] <= -0.04 and d['m5_consec_bear']:
        r.append(('Morn_QuietBreakBear', 0, -1))

    return r


# ═══════════════════════════════════════════════════════════════
# EXITS — from 1-min micro to hold-to-close
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    # Ultra-micro (1-3 min)
    'uM_1_05_2':  {'pt_pts': 1, 'sl_pts': 0.5, 'ts_min': 2},
    'uM_1_1_3':   {'pt_pts': 1, 'sl_pts': 1, 'ts_min': 3},
    'uM_2_1_3':   {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
    # Micro (3-10 min)
    'M_2_1_5':    {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 5},
    'M_2_15_5':   {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
    'M_3_1_5':    {'pt_pts': 3, 'sl_pts': 1, 'ts_min': 5},
    'M_3_15_5':   {'pt_pts': 3, 'sl_pts': 1.5, 'ts_min': 5},
    'M_3_2_5':    {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 5},
    'M_4_2_5':    {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 5},
    'M_4_2_8':    {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
    'M_5_2_8':    {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 8},
    'M_5_2_10':   {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10},
    'M_5_3_10':   {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 10},
    # Standard (10-30 min)
    'S_5_3_15':   {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 15},
    'S_8_3_15':   {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15},
    'S_8_4_15':   {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
    'S_10_4_20':  {'pt_pts': 10, 'sl_pts': 4, 'ts_min': 20},
    'S_10_5_30':  {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30},
    'S_15_5_30':  {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    'S_15_8_60':  {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 60},
    # Asymmetric
    'A_8_2_10':   {'pt_pts': 8, 'sl_pts': 2, 'ts_min': 10},
    'A_10_3_15':  {'pt_pts': 10, 'sl_pts': 3, 'ts_min': 15},
    'A_12_4_20':  {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
    'A_15_5_30':  {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    'A_20_5_60':  {'pt_pts': 20, 'sl_pts': 5, 'ts_min': 60},
    # Hold-to-close
    'HTC_5_3':    {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 120},
    'HTC_10_5':   {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 120},
    'HTC_15_8':   {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 120},
    'HTC_20_10':  {'pt_pts': 20, 'sl_pts': 10, 'ts_min': 120},
    'HTC_pure':   {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
    # Adaptive
    'Av_5_2_10':  {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
    'Av_8_3_15':  {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15, 'vix_mult': True},
    'Av_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30, 'vix_mult': True},
    # Trail
    'Tr3_2_10':   {'trail_pct': 0.03, 'sl_pts': 2, 'ts_min': 10},
    'Tr5_3_15':   {'trail_pct': 0.05, 'sl_pts': 3, 'ts_min': 15},
    'Tr5_2_10':   {'trail_pct': 0.05, 'sl_pts': 2, 'ts_min': 10},
    'Tr3_5_30':   {'trail_pct': 0.03, 'sl_pts': 5, 'ts_min': 30},
}

# ═══════════════════════════════════════════════════════════════
# FILTERS — cover all VIX regimes + day-of-week + combos
# ═══════════════════════════════════════════════════════════════
FILTERS = {
    'All':        lambda d: True,
    # VIX regimes
    'VLow':       lambda d: d['vix'] < 14,
    'Low':        lambda d: d['vix'] < 18,
    'lt20':       lambda d: d['vix'] < 20,
    'lt22':       lambda d: d['vix'] < 22,
    'Mid':        lambda d: 18 <= d['vix'] < 25,
    'MidHi':      lambda d: 18 <= d['vix'] < 30,
    'Hi':         lambda d: d['vix'] >= 22,
    'VHi':        lambda d: d['vix'] >= 28,
    # DOW
    'Mon':        lambda d: d['dow'] == 0,
    'TuWe':       lambda d: d['dow'] in (1, 2),
    'Fri':        lambda d: d['dow'] == 4,
    'NotMon':     lambda d: d['dow'] != 0,
    'NotFri':     lambda d: d['dow'] != 4,
    # Combined
    'Low_TuWe':   lambda d: d['vix'] < 18 and d['dow'] in (1, 2),
    'Low_NotMon': lambda d: d['vix'] < 18 and d['dow'] != 0,
    'lt20_TuWe':  lambda d: d['vix'] < 20 and d['dow'] in (1, 2),
    'lt20_NotMon':lambda d: d['vix'] < 20 and d['dow'] != 0,
    'Mid_TuWe':   lambda d: 18 <= d['vix'] < 25 and d['dow'] in (1, 2),
    'Mid_NotMon': lambda d: 18 <= d['vix'] < 25 and d['dow'] != 0,
    'Hi_NotFri':  lambda d: d['vix'] >= 22 and d['dow'] != 4,
    'Hi_TuWe':    lambda d: d['vix'] >= 22 and d['dow'] in (1, 2),
    # Morning context
    'MornBull':   lambda d: d['morn_bullish'],
    'MornBear':   lambda d: not d['morn_bullish'],
    'MornBull_Low': lambda d: d['morn_bullish'] and d['vix'] < 18,
    'MornBull_Hi':  lambda d: d['morn_bullish'] and d['vix'] >= 22,
    'MornBear_Hi':  lambda d: not d['morn_bullish'] and d['vix'] >= 22,
    'NearHigh':   lambda d: d['range_pos'] > 0.70,
    'NearLow':    lambda d: d['range_pos'] < 0.30,
}


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
    eb = bars[entry_idx]
    ep = eb['c']; em = eb['mins']
    pt = exit_params.get('pt_pts'); sl = exit_params.get('sl_pts')
    trail = exit_params.get('trail_pct')
    ts = exit_params.get('ts_min', 120)
    deadline = min(em + ts, 959)

    vm = exit_params.get('vix_mult')
    if vm:
        vix = exit_params.get('_vix', 20)
        sc = vix / 20.0
        if pt: pt = pt * sc
        if sl: sl = sl * sc

    if direction == 1:
        ptl = ep + pt if pt else None; sll = ep - sl if sl else None
    else:
        ptl = ep - pt if pt else None; sll = ep + sl if sl else None

    peak = trough = ep; xp = ep; xr = 'time_stop'; xi = len(bars)-1

    for j in range(entry_idx+1, len(bars)):
        b = bars[j]
        if b['mins'] >= deadline or b['mins'] >= 959:
            xp = b['c']; xr = 'time_stop'; xi = j; break
        if direction == 1:
            if b['h'] > peak: peak = b['h']
            if sll and b['l'] <= sll: xp=sll; xr='stop_loss'; xi=j; break
            if ptl and b['h'] >= ptl: xp=ptl; xr='profit_target'; xi=j; break
            if trail and peak > ep:
                tl = peak*(1-trail/100)
                if b['l'] <= tl: xp=tl; xr='trailing_stop'; xi=j; break
        else:
            if b['l'] < trough: trough = b['l']
            if sll and b['h'] >= sll: xp=sll; xr='stop_loss'; xi=j; break
            if ptl and b['l'] <= ptl: xp=ptl; xr='profit_target'; xi=j; break
            if trail and trough < ep:
                tl = trough*(1+trail/100)
                if b['h'] >= tl: xp=tl; xr='trailing_stop'; xi=j; break
    else:
        xp = bars[-1]['c']; xi = len(bars)-1

    xb = bars[xi]
    return {
        'entry_price': round(ep,2), 'exit_price': round(xp,2),
        'entry_time': eb['time'], 'exit_time': xb['time'],
        'entry_mins': em, 'exit_mins': xb['mins'],
        'hold_mins': xb['mins']-em, 'exit_reason': xr,
        'und_pts': round(direction*(xp-ep),2), 'direction': direction,
    }


# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════
def compute_stats(trades, label=''):
    if len(trades) < 12: return None
    pts = [t['und_pts'] for t in trades]
    n = len(pts); avg = statistics.mean(pts); tot = sum(pts)
    wr = sum(1 for p in pts if p > 0) / n * 100
    std = statistics.stdev(pts) if n > 1 else 0
    sh = avg/std if std > 0 else 0

    gw = sum(p for p in pts if p > 0); gl = abs(sum(p for p in pts if p <= 0))
    pf = gw/gl if gl > 0 else 99

    cum = pk = dd = 0
    for p in pts:
        cum += p
        if cum > pk: pk = cum
        if pk-cum > dd: dd = pk-cum

    mcl = cl = 0
    for p in pts:
        if p <= 0: cl += 1; mcl = max(mcl, cl)
        else: cl = 0

    mo = defaultdict(list)
    for t in trades: mo[t['date'][:7]].append(t['und_pts'])
    mwr = sum(1 for v in mo.values() if sum(v) > 0) / len(mo) * 100 if mo else 0

    cum_pts = []; c = 0
    for p in pts: c += p; cum_pts.append(c)
    if n > 5:
        xm = (n-1)/2; ym = statistics.mean(cum_pts)
        sxy = sum((i-xm)*(y-ym) for i,y in enumerate(cum_pts))
        sxx = sum((i-xm)**2 for i in range(n))
        syy = sum((y-ym)**2 for y in cum_pts)
        r2 = (sxy**2)/(sxx*syy) if sxx>0 and syy>0 else 0
    else: r2 = 0

    ah = statistics.mean(t['hold_mins'] for t in trades)

    return {
        'label': label, 'n': n, 'wr': round(wr,1), 'avg': round(avg,2),
        'tot': round(tot,1), 'sh': round(sh,3), 'pf': round(pf,2),
        'dd': round(dd,1), 'mcl': mcl, 'mwr': round(mwr,1),
        'r2': round(r2,3), 'ah': round(ah,1),
    }


# ═══════════════════════════════════════════════════════════════
# OPTION PRICING
# ═══════════════════════════════════════════════════════════════
_oc = {}
def load_opt(ticker, date):
    key = f"{ticker}_{date}"
    if key in _oc: return _oc[key]
    fn = ticker.replace(':','_') + f'_{date}.json'
    p = CACHE_DIR / fn
    if not p.exists(): _oc[key] = None; return None
    with open(p) as f: data = json.load(f)
    if not data: _oc[key] = None; return None
    bars = []
    for b in data:
        ts = b['t']/1000; dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        et = dt - timedelta(hours=4 if 3 <= dt.month <= 10 else 5)
        m = et.hour*60+et.minute
        if m < 570 or m >= 960: continue
        bars.append({'mins':m, 'o':b['o'], 'h':b['h'], 'l':b['l'], 'c':b['c']})
    bars.sort(key=lambda x: x['mins'])
    _oc[key] = bars if bars else None
    return _oc[key]

def btick(date, cp, strike):
    dt = datetime.strptime(date, '%Y-%m-%d')
    return f"O:SPXW{dt.strftime('%y%m%d')}{cp}{int(strike*1000):08d}"

def fbar(bars, target, tol=3):
    if not bars: return None
    best = min(bars, key=lambda b: abs(b['mins']-target))
    return best if abs(best['mins']-target) <= tol else None

def gstrike(px, rnd=5): return round(px/rnd)*rnd

def price_opt(date, em, xm, spx, direction, struct):
    atm = gstrike(spx)
    def single(cp, k):
        t = btick(date, cp, k); bars = load_opt(t, date)
        if not bars: return None, None
        e, x = fbar(bars, em), fbar(bars, xm)
        if not e or not x or e['c'] <= 0: return None, None
        return round((x['c']-e['c'])*100, 2), {'ticker':t, 'ep':round(e['c'],2), 'xp':round(x['c'],2)}
    def spread(cp, lk, sk):
        lt, st = btick(date, cp, lk), btick(date, cp, sk)
        lb, sb = load_opt(lt, date), load_opt(st, date)
        if not lb or not sb: return None, None
        le, lx = fbar(lb, em), fbar(lb, xm)
        se, sx = fbar(sb, em), fbar(sb, xm)
        if not all([le,lx,se,sx]): return None, None
        d = le['c']-se['c']; c = lx['c']-sx['c']
        return round((c-d)*100,2), {'lt':lt,'st':st}
    def credit(cp, sell_k, buy_k):
        st, lt = btick(date,cp,sell_k), btick(date,cp,buy_k)
        sb, lb = load_opt(st,date), load_opt(lt,date)
        if not sb or not lb: return None, None
        se,sx = fbar(sb,em), fbar(sb,xm)
        le,lx = fbar(lb,em), fbar(lb,xm)
        if not all([se,sx,le,lx]): return None, None
        cr = se['c']-le['c']; dc = sx['c']-lx['c']
        return round((cr-dc)*100,2), {'sell':st,'buy':lt}

    if struct == 'long_call': return single('C', atm)
    elif struct == 'long_itm_call': return single('C', atm-5)
    elif struct == 'long_otm_call': return single('C', atm+5)
    elif struct == 'long_put': return single('P', atm)
    elif struct == 'long_itm_put': return single('P', atm+5)
    elif struct == 'long_otm_put': return single('P', atm-5)
    elif struct == 'bull_call_5': return spread('C', atm, atm+5)
    elif struct == 'bull_call_10': return spread('C', atm, atm+10)
    elif struct == 'bear_put_5': return spread('P', atm, atm-5)
    elif struct == 'bear_put_10': return spread('P', atm, atm-10)
    elif struct == 'credit_call_5': return credit('C', atm+5, atm+10)
    elif struct == 'credit_put_5': return credit('P', atm-5, atm-10)
    return None, None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*160)
    print("CLOSING PRINT v2 — Comprehensive Multi-Regime, Multi-Structure Scanner")
    print("="*160)

    data = load_all_data()
    days = extract_features(*data)

    print("Generating signals…")
    day_sigs = {}; sig_counts = defaultdict(int)
    for day in days:
        sigs = closing_signals(day)
        day_sigs[day['date']] = sigs
        for name,_,_ in sigs: sig_counts[name] += 1

    print(f"\n  Signal frequency ({len(days)} days):")
    for name, cnt in sorted(sig_counts.items(), key=lambda x: -x[1]):
        print(f"    {name:30s}: {cnt:>5} ({cnt/len(days)*100:.1f}%)")

    # ── PHASE 1 ──
    total = len(sig_counts) * len(FILTERS) * len(EXIT_SETS)
    print(f"\n{'='*160}")
    print(f"PHASE 1: {len(sig_counts)} signals × {len(FILTERS)} filters × {len(EXIT_SETS)} exits = {total} combos")
    print(f"{'='*160}")

    all_res = []
    combo = 0
    for sig in sorted(sig_counts.keys()):
        for fn, ff in FILTERS.items():
            for en, ep in EXIT_SETS.items():
                combo += 1
                if combo % 20000 == 0: print(f"  [{combo}/{total}]…", flush=True)
                trades = []
                for day in days:
                    if not ff(day): continue
                    for sn, ei, di in day_sigs[day['date']]:
                        if sn != sig: continue
                        ab = day['afternoon']
                        if ei >= len(ab)-3: continue
                        epp = dict(ep)
                        if epp.get('vix_mult'): epp['_vix'] = day['vix']
                        t = simulate_trade(ab, ei, di, epp)
                        t['date'] = day['date']; t['vix'] = day['vix']
                        trades.append(t)
                        break
                st = compute_stats(trades, f"{sig}|{fn}|{en}")
                if st and st['sh'] > 0.18 and st['pf'] > 1.15 and st['n'] >= 12:
                    st['signal'] = sig; st['filter'] = fn; st['exit'] = en
                    st['trades'] = trades
                    all_res.append(st)

    print(f"\n  {combo} combos → {len(all_res)} edges (Sh>0.18, PF>1.15, N≥12)")
    all_res.sort(key=lambda x: x['sh'], reverse=True)

    # Print top 120
    print(f"\n{'='*180}")
    print(f"TOP 120 UNDERLYING EDGES")
    print(f"{'='*180}")
    hdr = f"{'Label':>70s} {'N':>4} {'WR':>5} {'Avg':>7} {'Tot':>8} {'Sh':>6} {'PF':>5} {'DD':>6} {'CL':>3} {'MWR':>5} {'R²':>5} {'Hld':>5}"
    print(hdr); print("-"*180)
    for r in all_res[:120]:
        print(f"{r['label'][:70]:>70s} {r['n']:>4} {r['wr']:>4.1f}% {r['avg']:>+6.2f} {r['tot']:>+7.1f} "
              f"{r['sh']:>6.3f} {r['pf']:>5.2f} {r['dd']:>5.1f} {r['mcl']:>3} {r['mwr']:>4.1f}% "
              f"{r['r2']:>5.3f} {r['ah']:>4.1f}m")

    # ── PHASE 2: OOS ──
    print(f"\n{'='*180}")
    print(f"PHASE 2: IS/OOS VALIDATION (2018-2022 vs 2023-2026)")
    print(f"{'='*180}")

    validated = []
    for r in all_res[:200]:
        trades = r.get('trades', [])
        if not trades: continue
        ist = [t['und_pts'] for t in trades if t['date'] < '2023-01-01']
        oost = [t['und_pts'] for t in trades if t['date'] >= '2023-01-01']
        if len(ist) < 5 or len(oost) < 5: continue
        isa = statistics.mean(ist); iss = statistics.stdev(ist) if len(ist)>1 else 0
        ish = isa/iss if iss > 0 else 0
        oosa = statistics.mean(oost); ooss = statistics.stdev(oost) if len(oost)>1 else 0
        oosh = oosa/ooss if ooss > 0 else 0
        holds = oosh > 0.05 and oosa > 0
        tag = 'HOLDS' if holds else 'DEGRADES'
        if r in all_res[:60] or holds:
            print(f"  {r['label'][:65]:>65s}  IS:{ish:>6.3f}(N={len(ist):>3})  OOS:{oosh:>6.3f}(N={len(oost):>3})  [{tag}]")
        if holds:
            validated.append({'edge': r, 'ish': round(ish,3), 'oosh': round(oosh,3),
                              'isn': len(ist), 'oosn': len(oost)})

    print(f"\n  {len(validated)} edges PASSED OOS")

    if not validated:
        print("  No edges survived. Saving underlying for review.")
        out = SCRIPT_DIR / 'backtest_results'; out.mkdir(exist_ok=True)
        with open(out / 'closing_v2_underlying.json', 'w') as f:
            json.dump([{k:v for k,v in r.items() if k != 'trades'} for r in all_res[:300]], f, indent=2)
        return

    # ── PHASE 3: OPTION PRICING ──
    print(f"\n{'='*180}")
    print(f"PHASE 3: PRICING TOP {min(60, len(validated))} EDGES WITH REAL SPXW OPTIONS")
    print(f"{'='*180}")

    bull_structs = ['long_call','long_itm_call','long_otm_call','bull_call_5','bull_call_10']
    bear_structs = ['long_put','long_itm_put','long_otm_put','bear_put_5','credit_call_5']
    opt_res = []

    for vi, v in enumerate(validated[:60]):
        r = v['edge']; trades = r['trades']
        if not trades: continue
        di = trades[0]['direction']
        structs = bull_structs if di == 1 else bear_structs

        print(f"\n  [{vi+1}] {r['label']} — N={len(trades)}, dir={di}, UndSh={r['sh']}")

        for struct in structs:
            priced = []; missed = 0; details = []
            for trade in trades:
                pnl, info = price_opt(trade['date'], trade['entry_mins'], trade['exit_mins'],
                                      trade['entry_price'], trade['direction'], struct)
                if pnl is not None:
                    priced.append(pnl)
                    details.append({
                        'date': trade['date'], 'opt_pnl': pnl,
                        'entry_time': trade['entry_time'], 'exit_time': trade['exit_time'],
                        'spx_entry': trade['entry_price'], 'spx_exit': trade['exit_price'],
                        'hold_mins': trade['hold_mins'], 'exit_reason': trade['exit_reason'],
                        'und_pts': trade['und_pts'], 'vix': trade['vix'], 'info': info,
                    })
                else:
                    missed += 1

            if len(priced) < 8:
                print(f"    {struct:18s} {len(priced)} priced ({missed} miss) — skip")
                continue

            avg = statistics.mean(priced); tot = sum(priced)
            wr = sum(1 for p in priced if p > 0)/len(priced)*100
            std = statistics.stdev(priced) if len(priced)>1 else 0
            sh = avg/std if std > 0 else 0
            gw = sum(p for p in priced if p > 0)
            gl = abs(sum(p for p in priced if p <= 0))
            pf = gw/gl if gl > 0 else 99
            cum=pk=mdd=0
            for p in priced:
                cum+=p
                if cum>pk: pk=cum
                if pk-cum>mdd: mdd=pk-cum
            # R²
            cl=[]; c=0
            for p in priced: c+=p; cl.append(c)
            n=len(priced); xm=(n-1)/2; ym=statistics.mean(cl)
            sxy=sum((i-xm)*(y-ym) for i,y in enumerate(cl))
            sxx=sum((i-xm)**2 for i in range(n))
            syy=sum((y-ym)**2 for y in cl)
            r2=(sxy**2)/(sxx*syy) if sxx>0 and syy>0 else 0
            # IS/OOS opt
            ip=[d['opt_pnl'] for d in details if d['date']<'2023-01-01']
            op=[d['opt_pnl'] for d in details if d['date']>='2023-01-01']
            ish = statistics.mean(ip)/statistics.stdev(ip) if len(ip)>3 and statistics.stdev(ip)>0 else 0
            oosh = statistics.mean(op)/statistics.stdev(op) if len(op)>3 and statistics.stdev(op)>0 else 0
            vs = "HOLDS" if oosh > 0.05 else "WEAK"
            mp = missed/(len(priced)+missed)*100

            print(f"    {struct:18s} N={n:>3}({100-mp:.0f}%) WR={wr:.1f}% Avg=${avg:>+.2f} Tot=${tot:>+.2f} "
                  f"Sh={sh:.3f} PF={pf:.2f} DD=${mdd:.2f} R²={r2:.3f} IS={ish:.3f} OOS={oosh:.3f} [{vs}]")

            opt_res.append({
                'label': f"{r['label']}|{struct}",
                'signal': r['signal'], 'filter': r['filter'],
                'exit': r['exit'], 'struct': struct, 'direction': di,
                'n': n, 'wr': round(wr,1), 'avg': round(avg,2), 'tot': round(tot,2),
                'sh': round(sh,3), 'pf': round(pf,2), 'mdd': round(mdd,2),
                'r2': round(r2,3), 'ish': round(ish,3), 'oosh': round(oosh,3),
                'und_sh': r['sh'], 'pct': round(100-mp,1), 'details': details,
            })

    # ── FINAL RANKING ──
    opt_res.sort(key=lambda x: x['sh'], reverse=True)

    print(f"\n\n{'='*200}")
    print("FINAL RANKING — Option-Priced Closing Print Edges")
    print(f"{'='*200}")
    hdr = (f"{'Label':>85s} {'N':>4} {'%':>4} {'WR':>5} {'Avg$':>9} {'Tot$':>11} "
           f"{'Sh':>6} {'PF':>5} {'MDD$':>9} {'R²':>5} {'IS':>6} {'OOS':>6} {'USh':>6}")
    print(hdr); print("-"*200)
    for r in opt_res[:80]:
        print(f"{r['label'][:85]:>85s} {r['n']:>4} {r['pct']:>3.0f}% {r['wr']:>4.1f}% "
              f"${r['avg']:>+8.2f} ${r['tot']:>+10.2f} {r['sh']:>6.3f} {r['pf']:>5.2f} "
              f"${r['mdd']:>8.2f} {r['r2']:>5.3f} {r['ish']:>6.3f} {r['oosh']:>6.3f} {r['und_sh']:>6.3f}")

    # ── SAVE ──
    out = SCRIPT_DIR / 'backtest_results'; out.mkdir(exist_ok=True)
    sv = [{k:v for k,v in r.items() if k!='details'} for r in opt_res[:150]]
    with open(out / 'closing_v2_option_results.json', 'w') as f: json.dump(sv, f, indent=2)
    und = [{k:v for k,v in r.items() if k!='trades'} for r in all_res[:300]]
    with open(out / 'closing_v2_underlying.json', 'w') as f: json.dump(und, f, indent=2)

    good = [r for r in opt_res if r['oosh'] > 0.05 and r['sh'] > 0.10]
    if good:
        all_trades = []
        for edge in good:
            for d in edge['details']:
                rec = {
                    'date': d['date'], 'strategy': edge['label'], 'structure': edge['struct'],
                    'direction': 'LONG' if edge['direction']==1 else 'SHORT',
                    'spx_entry': d['spx_entry'], 'spx_exit': d['spx_exit'],
                    'entry_time': d['entry_time'], 'exit_time': d['exit_time'],
                    'hold_mins': d['hold_mins'], 'exit_reason': d['exit_reason'],
                    'und_pts': d['und_pts'], 'vix': d['vix'], 'opt_pnl': d['opt_pnl'],
                }
                info = d.get('info', {})
                if info:
                    if 'ticker' in info:
                        rec['opt_ticker'] = info['ticker']
                        rec['opt_entry_px'] = info['ep']
                        rec['opt_exit_px'] = info['xp']
                all_trades.append(rec)
        with open(out / 'closing_v2_verified_trades.json', 'w') as f:
            json.dump(sorted(all_trades, key=lambda x: x['date']), f, indent=2)
        print(f"\n  Saved {len(all_trades)} verified closing trades")

    print(f"\n  Saved {len(sv)} option results, {len(und)} underlying results")

    # ── SUMMARY ──
    print(f"\n{'='*160}")
    print("SUMMARY")
    print(f"{'='*160}")
    print(f"  Signals: {len(sig_counts)}  |  Combos: {combo}  |  Und edges: {len(all_res)}  |  OOS: {len(validated)}  |  Opt results: {len(opt_res)}")

    if opt_res:
        print(f"\n  BEST OPTION SHARPE: {opt_res[0]['sh']} — {opt_res[0]['label']}")
    if good:
        print(f"\n  EDGES WITH POSITIVE OOS OPTION SHARPE: {len(good)}")
        for e in good[:20]:
            print(f"    {e['label']:>80s}  Sh={e['sh']:.3f}  OOS={e['oosh']:.3f}  N={e['n']}  D={'L' if e['direction']==1 else 'S'}")
    else:
        print("\n  No edges with positive OOS option Sharpe found.")

    # Group by VIX regime
    print(f"\n  BY VIX REGIME:")
    for regime, check in [('VeryLow (<14)', lambda f: 'VLow' in f),
                          ('Low (<18)', lambda f: f in ('Low','Low_TuWe','Low_NotMon','MornBull_Low')),
                          ('Mid (18-25)', lambda f: 'Mid' in f),
                          ('High (22+)', lambda f: 'Hi' in f or 'VHi' in f or 'MornBull_Hi' in f or 'MornBear_Hi' in f)]:
        regime_edges = [e for e in (good if good else opt_res[:30]) if check(e['filter'])]
        if regime_edges:
            print(f"\n    {regime}:")
            for e in regime_edges[:5]:
                print(f"      {e['label'][:75]:>75s}  Sh={e['sh']:.3f}  OOS={e['oosh']:.3f}  N={e['n']}")
        else:
            print(f"\n    {regime}: no surviving edges")

    print(f"\n{'='*160}")
    print("DONE")
    print(f"{'='*160}")


if __name__ == '__main__':
    main()
