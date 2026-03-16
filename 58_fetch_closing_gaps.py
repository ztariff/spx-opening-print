#!/usr/bin/env python3
"""
Fetch missing afternoon option data for top closing print edges.
===============================================================
The v2 scanner found 140 OOS-validated underlying edges, but most couldn't be
option-priced because the cache was built for morning entries. This script:

1. Regenerates the top VLow VIX bull signal dates + required strikes
2. Identifies which option files are missing from options_cache/
3. Fetches them from Polygon
4. Re-prices all top edges with the new data

All CLAUDE.md rules apply. No fabricated data.
"""

import csv, json, math, os, statistics, sys, time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
BASE_URL = "https://api.polygon.io"
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# IMPORT THE FULL SCANNER LOGIC
# ═══════════════════════════════════════════════════════════════
# We re-use 57's data loading and signal generation, but we need to inline
# the functions since importing is tricky. Load minimally.

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

        mo = morning[0]['o']; mc = morning[-1]['c']
        mh = max(b['h'] for b in morning); ml = min(b['l'] for b in morning)
        mr = mh - ml
        morn_ret = (mc - mo) / mo * 100
        morn_bullish = mc > mo
        morn_close_loc = (mc - ml) / mr if mr > 0 else 0.5
        range_pos = (mc - ml) / mr if mr > 0 else 0.5

        lhm = [b for b in morning if b['mins'] >= 780]
        lh_ret = (lhm[-1]['c'] - lhm[0]['o']) / lhm[0]['o'] * 100 if lhm else 0
        new_high_lh = max(b['h'] for b in lhm) >= mh * 0.999 if lhm else False
        new_low_lh = min(b['l'] for b in lhm) <= ml * 1.001 if lhm else False

        l15 = morning[-15:]
        m15_ret = (l15[-1]['c'] - l15[0]['o']) / l15[0]['o'] * 100
        l5 = morning[-5:]
        m5_ret = (l5[-1]['c'] - l5[0]['o']) / l5[0]['o'] * 100
        m5_consec_bull = all(b['c'] > b['o'] for b in l5)
        m5_consec_bear = all(b['c'] < b['o'] for b in l5)

        morn_body_ratio = abs(mc - mo) / mr if mr > 0 else 0
        intraday_vol = mr / mo * 100

        cfb = afternoon[0]
        cfb_ret = (cfb['c'] - cfb['o']) / cfb['o'] * 100
        cfb_bull = cfb['c'] > cfb['o']
        cfb_br = abs(cfb['c'] - cfb['o']) / (cfb['h'] - cfb['l']) if (cfb['h'] - cfb['l']) > 0 else 0

        if len(afternoon) > 1:
            cfb2_bull = afternoon[0]['c'] > afternoon[0]['o'] and afternoon[1]['c'] > afternoon[1]['o']
            cfb2_bear = afternoon[0]['c'] < afternoon[0]['o'] and afternoon[1]['c'] < afternoon[1]['o']
            cfb2_ret = (afternoon[1]['c'] - afternoon[0]['o']) / afternoon[0]['o'] * 100
        else:
            cfb2_bull = cfb2_bear = False; cfb2_ret = 0

        c3 = afternoon[:3]
        if len(c3) == 3:
            cfb3_bull = all(b['c'] > b['o'] for b in c3)
            cfb3_bear = all(b['c'] < b['o'] for b in c3)
            cfb3_ret = (c3[-1]['c'] - c3[0]['o']) / c3[0]['o'] * 100
        else:
            cfb3_bull = cfb3_bear = False; cfb3_ret = 0

        c5 = afternoon[:5]
        if len(c5) == 5:
            c5h = max(b['h'] for b in c5); c5l = min(b['l'] for b in c5)
            c5r = c5h - c5l
            cor5_ret = (c5[-1]['c'] - c5[0]['o']) / c5[0]['o'] * 100
            cor5_bull = c5[-1]['c'] > c5[0]['o']
            cor5_cloc = (c5[-1]['c'] - c5l) / c5r if c5r > 0 else 0.5
        else:
            cor5_ret = 0; cor5_bull = False; cor5_cloc = 0.5

        c15 = afternoon[:15]
        if len(c15) >= 15:
            c15h = max(b['h'] for b in c15); c15l = min(b['l'] for b in c15)
            c15r = c15h - c15l
            cor15_ret = (c15[-1]['c'] - c15[0]['o']) / c15[0]['o'] * 100
            cor15_bull = c15[-1]['c'] > c15[0]['o']
            cor15_cloc = (c15[-1]['c'] - c15l) / c15r if c15r > 0 else 0.5
        else:
            cor15_ret = 0; cor15_bull = False; cor15_cloc = 0.5

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
            'morn_ret': morn_ret, 'morn_bullish': morn_bullish,
            'morn_close_loc': morn_close_loc, 'range_pos': range_pos,
            'morn_body_ratio': morn_body_ratio, 'intraday_vol': intraday_vol,
            'lh_ret': lh_ret, 'new_high_lh': new_high_lh, 'new_low_lh': new_low_lh,
            'm15_ret': m15_ret, 'm5_ret': m5_ret,
            'm5_consec_bull': m5_consec_bull, 'm5_consec_bear': m5_consec_bear,
            'price_at_2pm': mc,
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
# SIGNALS (from 57)
# ═══════════════════════════════════════════════════════════════
def closing_signals(d):
    r = []
    cr = d['cfb_ret']; mr = d['morn_ret']
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
    if d['vix'] < 14 and d['above_20d'] is True and d['cfb_bull'] and cr >= 0.02:
        r.append(('CFB_LowVIX_Trend', 0, 1))
    if d['intraday_vol'] < 0.5 and d['cfb_bull'] and cr >= 0.03 and d['cfb_br'] > 0.70:
        r.append(('CFB_QuietStrong', 0, 1))
    if d['cfb_bull'] and cr >= 0.02 and d['above_20d'] is True and d['prev_bullish']:
        r.append(('CFB_TrendPrev', 0, 1))
    # BEARISH
    if d['cfb_br'] > 0.75 and not d['cfb_bull'] and cr <= -0.03:
        r.append(('CFB_StrongBear', 0, -1))
    if not d['cfb_bull'] and cr <= -0.02 and not d['morn_bullish']:
        r.append(('CFB_BearCont', 0, -1))
    if not d['cfb_bull'] and cr <= -0.02 and d['range_pos'] < 0.25:
        r.append(('CFB_NearLow', 0, -1))
    if d['cfb_br'] > 0.70 and not d['cfb_bull'] and cr <= -0.03 and d['above_20d'] is False:
        r.append(('CFB_BearTrend', 0, -1))
    if mr >= 0.20 and not d['cfb_bull'] and cr <= -0.03 and d['range_pos'] > 0.60:
        r.append(('CFB_RipFade', 0, -1))
    if mr >= 0.20 and not d['cfb_bull'] and cr <= -0.03 and d['range_pos'] > 0.70:
        r.append(('CFB_ReversalHigh', 0, -1))
    if d['vix_change'] >= 3 and not d['cfb_bull'] and cr <= -0.02 and d['above_20d'] is False:
        r.append(('CFB_BearVIXTrend', 0, -1))
    if d['new_low_lh'] and not d['cfb_bull'] and cr <= -0.02:
        r.append(('CFB_NewLowCont', 0, -1))
    # MULTI-BAR BULL
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.02:
        r.append(('C2_Bull', 1, 1))
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.02 and d['morn_bullish']:
        r.append(('C2_BullMorn', 1, 1))
    if d['cfb2_bull'] and d['cfb2_ret'] >= 0.03 and d['range_pos'] > 0.65:
        r.append(('C2_BullHigh', 1, 1))
    # MULTI-BAR BEAR
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.02:
        r.append(('C2_Bear', 1, -1))
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.02 and not d['morn_bullish']:
        r.append(('C2_BearMorn', 1, -1))
    if d['cfb2_bear'] and d['cfb2_ret'] <= -0.03 and d['range_pos'] < 0.35:
        r.append(('C2_BearLow', 1, -1))
    # 3-BAR
    if d['cfb3_bull'] and d['cfb3_ret'] >= 0.02:
        r.append(('C3_Bull', 2, 1))
    if d['cfb3_bull'] and d['cfb3_ret'] >= 0.02 and d['above_20d'] is True:
        r.append(('C3_BullTrend', 2, 1))
    if d['cfb3_bear'] and d['cfb3_ret'] <= -0.02:
        r.append(('C3_Bear', 2, -1))
    if d['cfb3_bear'] and d['cfb3_ret'] <= -0.02 and d['above_20d'] is False:
        r.append(('C3_BearTrend', 2, -1))
    # COR5
    if d['cor5_bull'] and d['cor5_cloc'] > 0.70 and d['cor5_ret'] > 0.02:
        r.append(('COR5_Bull', 4, 1))
    if d['cor5_bull'] and d['cor5_cloc'] > 0.70 and d['cor5_ret'] > 0.02 and d['morn_bullish']:
        r.append(('COR5_BullMorn', 4, 1))
    if not d['cor5_bull'] and d['cor5_cloc'] < 0.30 and d['cor5_ret'] < -0.02:
        r.append(('COR5_Bear', 4, -1))
    if not d['cor5_bull'] and d['cor5_cloc'] < 0.30 and d['cor5_ret'] < -0.02 and not d['morn_bullish']:
        r.append(('COR5_BearMorn', 4, -1))
    # COR15
    if d['cor15_bull'] and d['cor15_cloc'] > 0.70 and d['cor15_ret'] > 0.02:
        r.append(('COR15_Bull', 14, 1))
    if d['cor15_bull'] and d['cor15_cloc'] > 0.70 and d['cor15_ret'] > 0.02 and d['above_20d'] is True:
        r.append(('COR15_BullTrend', 14, 1))
    if d['cor15_bull'] and d['cor15_cloc'] > 0.70 and d['cor15_ret'] > 0.02 and d['morn_bullish']:
        r.append(('COR15_BullMorn', 14, 1))
    if not d['cor15_bull'] and d['cor15_cloc'] < 0.30 and d['cor15_ret'] < -0.02:
        r.append(('COR15_Bear', 14, -1))
    if not d['cor15_bull'] and d['cor15_cloc'] < 0.30 and d['cor15_ret'] < -0.02 and d['above_20d'] is False:
        r.append(('COR15_BearTrend', 14, -1))
    # COR30
    if d['cor30_bull'] and d['cor30_cloc'] > 0.70 and d['cor30_ret'] > 0.02:
        r.append(('COR30_Bull', 29, 1))
    if d['cor30_bull'] and d['cor30_cloc'] > 0.70 and d['cor30_ret'] > 0.02 and d['above_20d'] is True:
        r.append(('COR30_BullTrend', 29, 1))
    if not d['cor30_bull'] and d['cor30_cloc'] < 0.30 and d['cor30_ret'] < -0.02:
        r.append(('COR30_Bear', 29, -1))
    if not d['cor30_bull'] and d['cor30_cloc'] < 0.30 and d['cor30_ret'] < -0.02 and d['above_20d'] is False:
        r.append(('COR30_BearTrend', 29, -1))
    # Morning
    if d['morn_bullish'] and d['range_pos'] > 0.80 and d['above_20d'] is True:
        r.append(('Morn_HighTrend', 0, 1))
    if d['morn_ret'] >= 0.10 and d['range_pos'] > 0.60:
        r.append(('Morn_Rally', 0, 1))
    if d['morn_ret'] < 0 and d['m15_ret'] > 0.05 and d['range_pos'] > 0.40:
        r.append(('Morn_DipRecover', 0, 1))
    if abs(d['morn_ret']) < 0.08 and d['morn_close_loc'] > 0.70 and d['cfb_bull'] and cr > 0.04:
        r.append(('Morn_QuietBreak', 0, 1))
    if d['morn_bullish'] and d['range_pos'] > 0.60 and d['above_20d'] is True and d['prev_bullish']:
        r.append(('Morn_AllAligned', 0, 1))
    if not d['morn_bullish'] and d['range_pos'] < 0.20 and d['above_20d'] is False:
        r.append(('Morn_LowTrend', 0, -1))
    if d['morn_ret'] <= -0.15 and d['range_pos'] < 0.30:
        r.append(('Morn_Selloff', 0, -1))
    if abs(d['morn_ret']) < 0.08 and d['morn_close_loc'] < 0.30 and not d['cfb_bull'] and cr < -0.04:
        r.append(('Morn_QuietBreakBear', 0, -1))
    return r


# ═══════════════════════════════════════════════════════════════
# FILTERS (from 57)
# ═══════════════════════════════════════════════════════════════
FILTERS = {
    'All': lambda d: True,
    'VLow': lambda d: d['vix'] < 14,
    'Low': lambda d: d['vix'] < 18,
    'Mid': lambda d: 18 <= d['vix'] < 25,
    'MidHi': lambda d: d['vix'] >= 18,
    'Hi': lambda d: d['vix'] >= 22,
    'VHi': lambda d: d['vix'] >= 28,
    'Mon': lambda d: d['dow'] == 0,
    'TuWe': lambda d: d['dow'] in (1,2),
    'ThFri': lambda d: d['dow'] in (3,4),
    'NotMon': lambda d: d['dow'] != 0,
    'NotFri': lambda d: d['dow'] != 4,
    'MornBull': lambda d: d['morn_bullish'],
    'MornBear': lambda d: not d['morn_bullish'],
    'NearHigh': lambda d: d['range_pos'] > 0.70,
    'NearLow': lambda d: d['range_pos'] < 0.30,
    'VLow_TuWe': lambda d: d['vix'] < 14 and d['dow'] in (1,2),
    'Low_TuWe': lambda d: d['vix'] < 18 and d['dow'] in (1,2),
    'Hi_TuWe': lambda d: d['vix'] >= 22 and d['dow'] in (1,2),
    'Hi_NotFri': lambda d: d['vix'] >= 22 and d['dow'] != 4,
    'Low_NotMon': lambda d: d['vix'] < 18 and d['dow'] != 0,
    'lt20_TuWe': lambda d: d['vix'] < 20 and d['dow'] in (1,2),
    'lt20_NotMon': lambda d: d['vix'] < 20 and d['dow'] != 0,
    'MornBull_Low': lambda d: d['morn_bullish'] and d['vix'] < 18,
    'MornBear_Low': lambda d: not d['morn_bullish'] and d['vix'] < 18,
    'MornBull_Hi': lambda d: d['morn_bullish'] and d['vix'] >= 22,
    'MornBear_Hi': lambda d: not d['morn_bullish'] and d['vix'] >= 22,
    'AboveSMA': lambda d: d['above_20d'] is True,
    'BelowSMA': lambda d: d['above_20d'] is False,
}

# ═══════════════════════════════════════════════════════════════
# EXIT SETS (from 57)
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    'uM_1_05_2': {'tp':1.0, 'sl':0.5, 'tmax':2},
    'uM_1_1_3': {'tp':1.0, 'sl':1.0, 'tmax':3},
    'uM_2_1_3': {'tp':2.0, 'sl':1.0, 'tmax':3},
    'M_2_1_5': {'tp':2.0, 'sl':1.0, 'tmax':5},
    'M_2_15_5': {'tp':2.0, 'sl':1.5, 'tmax':5},
    'M_3_1_5': {'tp':3.0, 'sl':1.0, 'tmax':5},
    'M_3_15_5': {'tp':3.0, 'sl':1.5, 'tmax':5},
    'M_3_2_5': {'tp':3.0, 'sl':2.0, 'tmax':5},
    'M_4_2_5': {'tp':4.0, 'sl':2.0, 'tmax':5},
    'M_4_2_8': {'tp':4.0, 'sl':2.0, 'tmax':8},
    'M_5_2_8': {'tp':5.0, 'sl':2.0, 'tmax':8},
    'M_5_2_10': {'tp':5.0, 'sl':2.0, 'tmax':10},
    'M_5_3_10': {'tp':5.0, 'sl':3.0, 'tmax':10},
    'S_5_3_15': {'tp':5.0, 'sl':3.0, 'tmax':15},
    'S_8_3_15': {'tp':8.0, 'sl':3.0, 'tmax':15},
    'S_8_4_15': {'tp':8.0, 'sl':4.0, 'tmax':15},
    'S_10_4_20': {'tp':10.0, 'sl':4.0, 'tmax':20},
    'S_10_5_30': {'tp':10.0, 'sl':5.0, 'tmax':30},
    'S_15_5_30': {'tp':15.0, 'sl':5.0, 'tmax':30},
    'S_15_8_60': {'tp':15.0, 'sl':8.0, 'tmax':60},
    'A_8_2_10': {'tp':8.0, 'sl':2.0, 'tmax':10},
    'A_10_3_15': {'tp':10.0, 'sl':3.0, 'tmax':15},
    'A_12_4_20': {'tp':12.0, 'sl':4.0, 'tmax':20},
    'A_15_5_30': {'tp':15.0, 'sl':5.0, 'tmax':30},
    'A_20_5_60': {'tp':20.0, 'sl':5.0, 'tmax':60},
    'Av_5_2_10': {'tp':5.0, 'sl':2.0, 'tmax':10, 'avg_exit':True},
    'Av_8_3_15': {'tp':8.0, 'sl':3.0, 'tmax':15, 'avg_exit':True},
    'Av_10_5_30': {'tp':10.0, 'sl':5.0, 'tmax':30, 'avg_exit':True},
    'HTC_5_3': {'tp':5.0, 'sl':3.0, 'tmax':120, 'htc':True},
    'HTC_10_5': {'tp':10.0, 'sl':5.0, 'tmax':120, 'htc':True},
    'HTC_15_8': {'tp':15.0, 'sl':8.0, 'tmax':120, 'htc':True},
    'HTC_pure': {'tp':999, 'sl':999, 'tmax':120, 'htc':True},
    'Tr3_2_10': {'tp':3.0, 'sl':2.0, 'tmax':10, 'trail':True},
    'Tr3_5_30': {'tp':3.0, 'sl':5.0, 'tmax':30, 'trail':True},
    'Tr5_2_10': {'tp':5.0, 'sl':2.0, 'tmax':10, 'trail':True},
    'Tr5_3_15': {'tp':5.0, 'sl':3.0, 'tmax':15, 'trail':True},
    'Tr5_5_30': {'tp':5.0, 'sl':5.0, 'tmax':30, 'trail':True},
}


def simulate_trade(bars, entry_idx, direction, params):
    """Simulate underlying trade, return dict with pts, entry/exit info."""
    ep = bars[entry_idx]['c']
    tp = params['tp']; sl = params['sl']; tmax = params['tmax']
    is_trail = params.get('trail', False)
    is_htc = params.get('htc', False)
    is_avg = params.get('avg_exit', False)

    best = ep; worst = ep
    em = bars[entry_idx]['mins']
    xi = entry_idx + 1
    xp = ep; reason = 'tmax'

    for i in range(entry_idx+1, min(entry_idx+1+tmax, len(bars))):
        b = bars[i]
        if direction == 1:
            if b['h'] > best: best = b['h']
            if b['l'] < worst: worst = b['l']
            move = b['h'] - ep
            drawdown = ep - b['l']
            if is_trail:
                trail_stop = best - sl
                if b['l'] <= trail_stop:
                    xi = i; xp = trail_stop; reason = 'trail'; break
            if move >= tp:
                xi = i; xp = ep + tp; reason = 'tp'; break
            if drawdown >= sl:
                xi = i; xp = ep - sl; reason = 'sl'; break
        else:
            if b['l'] < best: best = b['l']
            if b['h'] > worst: worst = b['h']
            move = ep - b['l']
            drawdown = b['h'] - ep
            if is_trail:
                trail_stop = best + sl
                if b['h'] >= trail_stop:
                    xi = i; xp = trail_stop; reason = 'trail'; break
            if move >= tp:
                xi = i; xp = ep - tp; reason = 'tp'; break
            if drawdown >= sl:
                xi = i; xp = ep + sl; reason = 'sl'; break
    else:
        xi = min(entry_idx + tmax, len(bars)-1)
        if is_htc or is_avg:
            xp = bars[xi]['c']
        else:
            xp = bars[xi]['c']

    pts = (xp - ep) * direction
    xm = bars[xi]['mins']
    return {
        'und_pts': round(pts, 2), 'entry_price': round(ep, 2),
        'exit_price': round(xp, 2), 'entry_min': em, 'exit_min': xm,
        'entry_idx': entry_idx, 'exit_idx': xi,
        'hold_mins': xm - em, 'reason': reason, 'direction': direction,
    }


def compute_stats(trades, label):
    if len(trades) < 5: return None
    rets = [t['und_pts'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    n = len(rets); wr = len(wins)/n*100
    avg = statistics.mean(rets); tot = sum(rets)
    sd = statistics.stdev(rets) if n > 1 else 0
    sh = avg/sd if sd > 0 else 0
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    pf = gw/gl if gl > 0 else (99.0 if gw > 0 else 0)
    cumr = []; s = 0
    for r in rets: s += r; cumr.append(s)
    peak = cumr[0]; dd = 0
    for c in cumr:
        if c > peak: peak = c
        if peak - c > dd: dd = peak - c

    # Consecutive losses
    mcl = 0; cl = 0
    for r in rets:
        if r <= 0: cl += 1; mcl = max(mcl, cl)
        else: cl = 0

    # Monthly WR
    months = defaultdict(list)
    for t in trades:
        m = t['date'][:7]; months[m].append(t['und_pts'])
    mwr = sum(1 for vs in months.values() if sum(vs) > 0) / max(len(months), 1) * 100

    # R²
    xs = list(range(n)); ys = cumr
    xbar = statistics.mean(xs); ybar = statistics.mean(ys)
    ssres = sum((y - (ybar + (sum((x-xbar)*(y-ybar) for x,y in zip(xs,ys)) / max(sum((x-xbar)**2 for x in xs),1e-9)) * (x-xbar)))**2 for x,y in zip(xs,ys))
    sstot = sum((y-ybar)**2 for y in ys)
    r2 = 1 - ssres/sstot if sstot > 0 else 0

    ah = statistics.mean([t['hold_mins'] for t in trades])

    return {
        'label': label, 'n': n, 'wr': wr, 'avg': avg, 'tot': tot,
        'sh': round(sh, 3), 'pf': round(pf, 2), 'dd': round(dd, 1),
        'mcl': mcl, 'mwr': round(mwr, 1), 'r2': round(r2, 3), 'ah': round(ah, 1),
    }


# ═══════════════════════════════════════════════════════════════
# OPTION HELPERS
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
# POLYGON FETCHER
# ═══════════════════════════════════════════════════════════════
def fetch_option_bars(ticker, date):
    """Fetch 1-min option bars from Polygon and cache."""
    fn = ticker.replace(':', '_') + f'_{date}.json'
    p = CACHE_DIR / fn
    if p.exists():
        return True  # already cached

    url = (f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute"
           f"/{date}/{date}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.get(url, timeout=15)

        data = resp.json()
        results = data.get('results', [])

        # Save even if empty (so we don't re-fetch)
        with open(p, 'w') as f:
            json.dump(results, f)

        return len(results) > 0
    except Exception as e:
        print(f"    ERROR fetching {ticker} {date}: {e}")
        return False


def get_needed_tickers(date, spx_price, direction, structs):
    """Return set of tickers needed to price all structs for this date."""
    atm = gstrike(spx_price)
    tickers = set()
    for struct in structs:
        if struct == 'long_call': tickers.add(btick(date, 'C', atm))
        elif struct == 'long_itm_call': tickers.add(btick(date, 'C', atm-5))
        elif struct == 'long_otm_call': tickers.add(btick(date, 'C', atm+5))
        elif struct == 'long_put': tickers.add(btick(date, 'P', atm))
        elif struct == 'long_itm_put': tickers.add(btick(date, 'P', atm+5))
        elif struct == 'long_otm_put': tickers.add(btick(date, 'P', atm-5))
        elif struct == 'bull_call_5':
            tickers.add(btick(date, 'C', atm))
            tickers.add(btick(date, 'C', atm+5))
        elif struct == 'bull_call_10':
            tickers.add(btick(date, 'C', atm))
            tickers.add(btick(date, 'C', atm+10))
        elif struct == 'bear_put_5':
            tickers.add(btick(date, 'P', atm))
            tickers.add(btick(date, 'P', atm-5))
        elif struct == 'credit_call_5':
            tickers.add(btick(date, 'C', atm+5))
            tickers.add(btick(date, 'C', atm+10))
    return tickers


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*140)
    print("CLOSING PRINT v2 — FETCH MISSING OPTION DATA & RE-PRICE")
    print("="*140)

    data = load_all_data()
    days = extract_features(*data)

    # Generate signals for all days
    print("Generating signals…")
    day_map = {d['date']: d for d in days}
    day_sigs = {}
    for day in days:
        sigs = closing_signals(day)
        day_sigs[day['date']] = sigs

    # ── TARGET EDGES ──
    # These are the top OOS-validated underlying edges that had option data gaps
    # We focus on the best 30+ edges that were VLow/Low bull + some bears
    TARGET_EDGES = [
        # VLow Bull edges (strongest OOS)
        ('CFB_Trend', 'VLow', ['M_5_3_10','M_3_1_5','M_2_1_5','S_5_3_15','S_8_3_15','A_10_3_15','M_5_2_10','M_4_2_8','M_5_2_8','Av_5_2_10','Av_8_3_15','S_10_4_20','A_12_4_20','A_8_2_10','uM_2_1_3','M_3_15_5','M_2_15_5','S_8_4_15'], 1),
        ('CFB_StrongBull', 'VLow', ['M_2_1_5','M_3_1_5','M_5_3_10','S_5_3_15','S_8_3_15','A_10_3_15','Av_5_2_10','Av_8_3_15','M_5_2_10','M_4_2_8','M_5_2_8','M_2_15_5','M_3_15_5','uM_2_1_3','A_8_2_10','S_8_4_15','M_3_2_5','M_4_2_5','Tr3_2_10','Tr3_5_30','Tr5_2_10','Tr5_3_15'], 1),
        ('CFB_TrendPrev', 'VLow', ['Av_8_3_15','Av_5_2_10','M_5_2_10','A_8_2_10','A_10_3_15','S_8_3_15','S_8_4_15','S_10_4_20','M_3_1_5','M_4_2_8','M_5_2_8','uM_2_1_3','S_5_3_15','A_12_4_20','M_2_1_5'], 1),
        ('C2_BullHigh', 'VLow', ['uM_1_1_3','uM_2_1_3','uM_1_05_2','M_2_1_5','M_2_15_5','M_3_1_5','M_3_15_5','M_3_2_5','M_4_2_5','Tr5_2_10','Tr5_3_15','Tr3_2_10','Tr3_5_30','Av_8_3_15','S_5_3_15'], 1),
        ('C2_Bull', 'VLow', ['S_8_3_15','S_5_3_15','A_10_3_15','Av_8_3_15','S_8_4_15','M_5_2_10','A_8_2_10'], 1),
        # VLow bear edges (for credit_call_5 — already have some, but try to fill gaps)
        ('C2_Bear', 'VLow', ['uM_1_1_3','uM_1_05_2'], -1),
        ('C2_BearMorn', 'VLow', ['uM_1_05_2','uM_1_1_3','M_2_1_5'], -1),
        # VHi bull edges (these had decent underlying Sharpe)
        ('CFB_StrongBull', 'VHi', ['S_10_5_30','M_3_2_5','HTC_5_3','M_5_3_10','S_15_5_30','A_15_5_30','HTC_10_5','S_10_4_20','A_12_4_20','S_5_3_15','S_8_3_15','A_10_3_15','uM_2_1_3','M_5_2_10','M_4_2_5','M_4_2_8','M_2_1_5'], 1),
        # Hi_TuWe/Hi_NotFri bull (had some priced)
        ('CFB_StrongBull', 'Hi_TuWe', ['M_5_2_10','S_5_3_15','HTC_5_3','A_8_2_10','M_4_2_8','M_5_3_10','S_8_3_15','M_5_2_8','S_8_4_15','Av_5_2_10','M_4_2_5','M_3_2_5','M_3_15_5'], 1),
        # Other promising
        ('CFB_FlatBreak', 'MidHi', ['M_2_15_5'], 1),
        ('CFB_FlatBreak', 'Mid', ['M_2_15_5'], 1),
        ('CFB_LowVIX_Trend', 'Mon', ['HTC_pure'], 1),
        ('CFB_RipFade', 'lt20_TuWe', ['M_4_2_8'], -1),
        ('CFB_BearCont', 'NearHigh', ['Tr3_5_30','Tr3_2_10'], -1),
        ('CFB_ReversalLow', 'Low_TuWe', ['HTC_pure'], 1),
        ('CFB_DipBounce', 'Low_TuWe', ['HTC_pure'], 1),
        ('CFB_ReversalLow', 'lt20_TuWe', ['HTC_pure'], 1),
        ('CFB_DipBounce', 'lt20_TuWe', ['HTC_pure'], 1),
        ('CFB_BearTrend', 'Low', ['HTC_pure'], -1),
        ('CFB_BearTrend', 'Low_NotMon', ['HTC_pure'], -1),
    ]

    bull_structs = ['long_call','long_itm_call','long_otm_call','bull_call_5','bull_call_10']
    bear_structs = ['long_put','long_itm_put','long_otm_put','bear_put_5','credit_call_5']

    # ── STEP 1: Identify all dates and required tickers ──
    print("\n" + "="*140)
    print("STEP 1: Identifying missing option data")
    print("="*140)

    needed = {}  # ticker -> set of dates
    edge_trades = {}  # (sig,filt,exit) -> list of trades with dates

    for sig, filt, exits, direction in TARGET_EDGES:
        ff = FILTERS[filt]
        structs = bull_structs if direction == 1 else bear_structs
        for exit_name in exits:
            ep = EXIT_SETS[exit_name]
            trades = []
            for day in days:
                if not ff(day): continue
                for sn, ei, di in day_sigs.get(day['date'], []):
                    if sn != sig: continue
                    if di != direction: continue
                    ab = day['afternoon']
                    if ei >= len(ab)-3: continue
                    epp = dict(ep)
                    t = simulate_trade(ab, ei, di, epp)
                    t['date'] = day['date']
                    t['vix'] = day['vix']
                    t['spx_at_entry'] = ab[ei]['c']
                    trades.append(t)
                    break

            key = (sig, filt, exit_name)
            edge_trades[key] = (trades, direction)

            # Identify missing tickers
            for t in trades:
                tickers = get_needed_tickers(t['date'], t['spx_at_entry'], direction, structs)
                for tk in tickers:
                    fn = tk.replace(':', '_') + f"_{t['date']}.json"
                    if not (CACHE_DIR / fn).exists():
                        if tk not in needed:
                            needed[tk] = set()
                        needed[tk].add(t['date'])

    # Count unique ticker-date pairs
    total_fetches = sum(len(dates) for dates in needed.values())
    print(f"\n  Total unique ticker×date combos to fetch: {total_fetches}")
    print(f"  Unique tickers: {len(needed)}")

    if total_fetches == 0:
        print("  All data already cached! Proceeding to re-price.")
    else:
        # ── STEP 2: FETCH FROM POLYGON ──
        print(f"\n{'='*140}")
        print(f"STEP 2: Fetching {total_fetches} option bar files from Polygon")
        print(f"{'='*140}")

        fetched = 0; hits = 0; empty = 0
        for ticker in sorted(needed.keys()):
            for date in sorted(needed[ticker]):
                fetched += 1
                if fetched % 50 == 0:
                    print(f"  [{fetched}/{total_fetches}] fetching…", flush=True)
                got = fetch_option_bars(ticker, date)
                if got:
                    hits += 1
                else:
                    empty += 1
                # Rate limiting: 100 req/s on top tier, stay safe at ~80/s
                time.sleep(0.013)

        print(f"\n  Fetched {fetched} files: {hits} with data, {empty} empty")

    # Clear option cache since we have new files
    _oc.clear()

    # ── STEP 3: RE-PRICE ALL EDGES ──
    print(f"\n{'='*140}")
    print(f"STEP 3: Re-pricing all target edges with real SPXW options")
    print(f"{'='*140}")

    all_opt_results = []
    all_verified_trades = []

    for (sig, filt, exit_name), (trades, direction) in sorted(edge_trades.items()):
        if len(trades) < 5: continue
        label_base = f"{sig}|{filt}|{exit_name}"

        structs = bull_structs if direction == 1 else bear_structs

        for struct in structs:
            opt_trades = []
            for t in trades:
                em = t['entry_min']; xm = t['exit_min']
                pnl, detail = price_opt(t['date'], em, xm, t['spx_at_entry'], direction, struct)
                if pnl is not None:
                    ot = dict(t)
                    ot['opt_pnl'] = pnl
                    ot['struct'] = struct
                    ot['detail'] = detail
                    ot['label'] = f"{label_base}|{struct}"
                    opt_trades.append(ot)

            n_priced = len(opt_trades)
            n_miss = len(trades) - n_priced
            coverage = n_priced / len(trades) if trades else 0

            if coverage < 0.50 or n_priced < 8:
                continue  # too few

            # Compute option stats
            rets = [t['opt_pnl'] for t in opt_trades]
            avg = statistics.mean(rets)
            tot = sum(rets)
            sd = statistics.stdev(rets) if len(rets) > 1 else 0
            sh = avg / sd if sd > 0 else 0
            wins = [r for r in rets if r > 0]
            losses = [r for r in rets if r <= 0]
            wr = len(wins) / len(rets) * 100
            gw = sum(wins) if wins else 0
            gl = abs(sum(losses)) if losses else 0
            pf = gw / gl if gl > 0 else 99.0

            # MDD
            cum = []; s = 0
            for r in rets: s += r; cum.append(s)
            peak = cum[0]; dd = 0
            for c in cum:
                if c > peak: peak = c
                if peak - c > dd: dd = peak - c

            # R²
            xs = list(range(len(rets))); ys = cum
            if len(xs) > 2:
                xbar = statistics.mean(xs); ybar = statistics.mean(ys)
                ssxy = sum((x-xbar)*(y-ybar) for x,y in zip(xs,ys))
                ssxx = sum((x-xbar)**2 for x in xs)
                slope = ssxy / ssxx if ssxx > 0 else 0
                yhat = [ybar + slope*(x-xbar) for x in xs]
                ssres = sum((y-yh)**2 for y,yh in zip(ys,yhat))
                sstot = sum((y-ybar)**2 for y in ys)
                r2 = 1 - ssres/sstot if sstot > 0 else 0
            else:
                r2 = 0

            # IS/OOS split
            is_rets = [t['opt_pnl'] for t in opt_trades if t['date'] < '2023-01-01']
            oos_rets = [t['opt_pnl'] for t in opt_trades if t['date'] >= '2023-01-01']
            is_sh = (statistics.mean(is_rets) / statistics.stdev(is_rets)) if len(is_rets) > 2 and statistics.stdev(is_rets) > 0 else 0
            oos_sh = (statistics.mean(oos_rets) / statistics.stdev(oos_rets)) if len(oos_rets) > 2 and statistics.stdev(oos_rets) > 0 else 0

            # Underlying Sharpe for reference
            und_rets = [t['und_pts'] for t in trades]
            und_sh = statistics.mean(und_rets) / statistics.stdev(und_rets) if len(und_rets) > 1 and statistics.stdev(und_rets) > 0 else 0

            holds = oos_sh > 0.0 and (is_sh > 0.0 or n_priced < 15)
            tag = 'HOLDS' if holds else 'WEAK'

            label = f"{label_base}|{struct}"
            print(f"    {label:>80s}  N={n_priced:>3}({coverage*100:.0f}%) WR={wr:.1f}% Avg=${avg:>+8.2f} Tot=${tot:>+9.2f} "
                  f"Sh={sh:.3f} PF={pf:.2f} DD=${dd:.2f} R²={r2:.3f} IS={is_sh:.3f} OOS={oos_sh:.3f} [{tag}]")

            result = {
                'label': label, 'n': n_priced, 'coverage': round(coverage, 2),
                'wr': round(wr, 1), 'avg': round(avg, 2), 'tot': round(tot, 2),
                'sh': round(sh, 3), 'pf': round(pf, 2), 'dd': round(dd, 2),
                'r2': round(r2, 3), 'is_sh': round(is_sh, 3), 'oos_sh': round(oos_sh, 3),
                'und_sh': round(und_sh, 3), 'tag': tag,
                'direction': 'L' if direction == 1 else 'S',
            }
            all_opt_results.append(result)

            if sh > 0.10:
                for t in opt_trades:
                    vt = {
                        'label': label, 'date': t['date'],
                        'entry_min': t['entry_min'], 'exit_min': t['exit_min'],
                        'direction': direction, 'struct': struct,
                        'und_pts': t['und_pts'], 'opt_pnl': t['opt_pnl'],
                        'spx_entry': t['entry_price'],
                        'vix': t['vix'],
                    }
                    if 'detail' in t and t['detail']:
                        vt['detail'] = t['detail']
                    all_verified_trades.append(vt)

    # ── FINAL RANKING ──
    print(f"\n{'='*140}")
    print("FINAL RANKING — Re-Priced Closing Print Edges (After Fetch)")
    print("="*140)

    all_opt_results.sort(key=lambda x: x['sh'], reverse=True)
    for r in all_opt_results:
        print(f"  {r['label']:>85s}  N={r['n']:>3} {r['coverage']*100:.0f}% WR={r['wr']:.1f}% "
              f"Avg=${r['avg']:>+8.2f} Sh={r['sh']:.3f} IS={r['is_sh']:.3f} OOS={r['oos_sh']:.3f} "
              f"R²={r['r2']:.3f} D={r['direction']} [{r['tag']}]")

    # Positive OOS Sharpe
    pos_oos = [r for r in all_opt_results if r['oos_sh'] > 0]
    print(f"\n  EDGES WITH POSITIVE OOS OPTION SHARPE: {len(pos_oos)}")
    for r in pos_oos:
        print(f"    {r['label']:>85s}  Sh={r['sh']:.3f}  OOS={r['oos_sh']:.3f}  N={r['n']}  D={r['direction']}")

    # Group by VIX regime
    print(f"\n  BY VIX REGIME:")
    for regime in ['VLow', 'Low', 'Mid', 'MidHi', 'Hi', 'VHi']:
        regime_edges = [r for r in pos_oos if f"|{regime}|" in r['label']]
        if regime_edges:
            labels = {'VLow': 'VeryLow (<14)', 'Low': 'Low (<18)', 'Mid': 'Mid (18-25)',
                      'MidHi': 'MidHi (18+)', 'Hi': 'High (22+)', 'VHi': 'VHigh (28+)'}
            print(f"\n    {labels.get(regime, regime)}:")
            for r in regime_edges[:10]:
                print(f"      {r['label']:>80s}  Sh={r['sh']:.3f}  OOS={r['oos_sh']:.3f}  N={r['n']}")

    # Save results
    out = SCRIPT_DIR / 'backtest_results'
    out.mkdir(exist_ok=True)
    with open(out / 'closing_v2_option_results.json', 'w') as f:
        json.dump(all_opt_results, f, indent=2)
    with open(out / 'closing_v2_verified_trades.json', 'w') as f:
        json.dump(all_verified_trades, f, indent=2)
    print(f"\n  Saved {len(all_opt_results)} option results, {len(all_verified_trades)} verified trades")

    print(f"\n{'='*140}")
    print("DONE")
    print("="*140)


if __name__ == '__main__':
    main()
