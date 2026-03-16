#!/usr/bin/env python3
"""
59_widen_edges.py — Parameter Relaxation Scanner
=================================================
For each of the 7 portfolio edges, systematically relax parameters
(VIX bands, return thresholds, body ratios, range positions, DOW filters)
and measure: N gained, Sharpe change, WR change, PF change, R² change.

Keeps ALL real data — no fabrication. Just widens the selection criteria.

Phase 1: Underlying-only scan (runs in sandbox)
Phase 2: For promising relaxations, prices with real cached options

Output: backtest_results/closing_v2_widened_results.json
"""

import csv, json, math, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'
OUT_DIR = SCRIPT_DIR / 'backtest_results'
OUT_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (exact copy from 57)
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
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION (exact copy from 57)
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    'uM_1_05_2':  {'pt_pts': 1, 'sl_pts': 0.5, 'ts_min': 2},
    'uM_1_1_3':   {'pt_pts': 1, 'sl_pts': 1, 'ts_min': 3},
    'uM_2_1_3':   {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
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
    'S_5_3_15':   {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 15},
    'S_8_3_15':   {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15},
    'S_8_4_15':   {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
    'S_10_4_20':  {'pt_pts': 10, 'sl_pts': 4, 'ts_min': 20},
    'S_10_5_30':  {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30},
    'S_15_5_30':  {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    'S_15_8_60':  {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 60},
    'A_8_2_10':   {'pt_pts': 8, 'sl_pts': 2, 'ts_min': 10},
    'A_10_3_15':  {'pt_pts': 10, 'sl_pts': 3, 'ts_min': 15},
    'A_12_4_20':  {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
    'A_15_5_30':  {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    'A_20_5_60':  {'pt_pts': 20, 'sl_pts': 5, 'ts_min': 60},
    'HTC_5_3':    {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 120},
    'HTC_10_5':   {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 120},
    'HTC_15_8':   {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 120},
    'HTC_20_10':  {'pt_pts': 20, 'sl_pts': 10, 'ts_min': 120},
    'HTC_pure':   {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
    'Av_5_2_10':  {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
    'Av_8_3_15':  {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15, 'vix_mult': True},
    'Av_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30, 'vix_mult': True},
    'Tr3_2_10':   {'trail_pct': 0.03, 'sl_pts': 2, 'ts_min': 10},
    'Tr5_3_15':   {'trail_pct': 0.05, 'sl_pts': 3, 'ts_min': 15},
    'Tr5_2_10':   {'trail_pct': 0.05, 'sl_pts': 2, 'ts_min': 10},
    'Tr3_5_30':   {'trail_pct': 0.03, 'sl_pts': 5, 'ts_min': 30},
}

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


def compute_stats(trades, label=''):
    if len(trades) < 5:
        return None
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

    cum_pts = []; c = 0
    for p in pts: c += p; cum_pts.append(c)
    if n > 5:
        xm = (n-1)/2; ym = statistics.mean(cum_pts)
        sxy = sum((i-xm)*(y-ym) for i,y in enumerate(cum_pts))
        sxx = sum((i-xm)**2 for i in range(n))
        syy = sum((y-ym)**2 for y in cum_pts)
        r2 = (sxy**2)/(sxx*syy) if sxx>0 and syy>0 else 0
    else: r2 = 0

    return {
        'label': label, 'n': n, 'wr': round(wr,1), 'avg': round(avg,2),
        'tot': round(tot,1), 'sh': round(sh,3), 'pf': round(pf,2),
        'dd': round(dd,1), 'r2': round(r2,3),
    }


# ═══════════════════════════════════════════════════════════════
# OPTION PRICING (exact copy from 57)
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
# PARAMETERIZED SIGNAL FUNCTIONS
# Each returns: fires=True/False, entry_idx=int, direction=int
# Parameters are passed in so we can sweep them
# ═══════════════════════════════════════════════════════════════

def sig_C2_BullHigh(d, cfb2_ret_min=0.03, range_pos_min=0.70):
    """Original: cfb2_bull, cfb2_ret>=0.03, range_pos>0.70"""
    if d['cfb2_bull'] and d['cfb2_ret'] >= cfb2_ret_min and d['range_pos'] > range_pos_min:
        return True, 1, 1
    return False, 1, 1

def sig_CFB_Trend(d, cfb_br_min=0.70, cfb_ret_min=0.03, above_20d_req=True):
    """Original: cfb_br>0.70, cfb_bull, cfb_ret>=0.03, above_20d=True"""
    if (d['cfb_br'] > cfb_br_min and d['cfb_bull'] and d['cfb_ret'] >= cfb_ret_min
        and (d['above_20d'] is True if above_20d_req else True)):
        return True, 0, 1
    return False, 0, 1

def sig_CFB_StrongBull(d, cfb_br_min=0.75, cfb_ret_min=0.03):
    """Original: cfb_br>0.75, cfb_bull, cfb_ret>=0.03"""
    if d['cfb_br'] > cfb_br_min and d['cfb_bull'] and d['cfb_ret'] >= cfb_ret_min:
        return True, 0, 1
    return False, 0, 1

def sig_CFB_BearTrend(d, cfb_br_min=0.70, cfb_ret_max=-0.03, above_20d_req=True):
    """Original: cfb_br>0.70, bearish, cfb_ret<=-0.03, above_20d=False"""
    if (d['cfb_br'] > cfb_br_min and not d['cfb_bull'] and d['cfb_ret'] <= cfb_ret_max
        and (d['above_20d'] is False if above_20d_req else True)):
        return True, 0, -1
    return False, 0, -1

def sig_C2_BearMorn(d, cfb2_ret_max=-0.03, morn_bear_req=True):
    """Original: cfb2_bear, cfb2_ret<=-0.03, morn_bearish"""
    if (d['cfb2_bear'] and d['cfb2_ret'] <= cfb2_ret_max
        and (not d['morn_bullish'] if morn_bear_req else True)):
        return True, 1, -1
    return False, 1, -1

def sig_CFB_ReversalLow(d, morn_ret_max=-0.20, cfb_ret_min=0.03, range_pos_max=0.40):
    """Original: morn_ret<=-0.20, cfb_bull, cfb_ret>=0.03, range_pos<0.40"""
    if (d['morn_ret'] <= morn_ret_max and d['cfb_bull'] and d['cfb_ret'] >= cfb_ret_min
        and d['range_pos'] < range_pos_max):
        return True, 0, 1
    return False, 0, 1

def sig_CFB_FlatBreak(d, morn_flat_band=0.10, cfb_ret_min=0.04, cfb_br_min=0.65):
    """Original: abs(morn_ret)<=0.10, cfb_bull, cfb_ret>=0.04, cfb_br>0.65"""
    if (abs(d['morn_ret']) <= morn_flat_band and d['cfb_bull']
        and d['cfb_ret'] >= cfb_ret_min and d['cfb_br'] > cfb_br_min):
        return True, 0, 1
    return False, 0, 1


# ═══════════════════════════════════════════════════════════════
# VIX FILTER VARIANTS FOR EACH EDGE
# ═══════════════════════════════════════════════════════════════

def make_vix_filters(base_regime):
    """Return dict of (name, lambda) for VIX filter variants around the base."""
    filters = {}
    if base_regime == 'VLow':  # original: <14
        filters['VLow_orig(<14)'] = lambda d: d['vix'] < 14
        filters['VLow_w1(<15)'] = lambda d: d['vix'] < 15
        filters['VLow_w2(<16)'] = lambda d: d['vix'] < 16
        filters['VLow_w3(<17)'] = lambda d: d['vix'] < 17
        filters['VLow_w4(<18)'] = lambda d: d['vix'] < 18
    elif base_regime == 'Low':  # original: <18
        filters['Low_orig(<18)'] = lambda d: d['vix'] < 18
        filters['Low_w1(<20)'] = lambda d: d['vix'] < 20
        filters['Low_w2(<22)'] = lambda d: d['vix'] < 22
        filters['Low_w3(<25)'] = lambda d: d['vix'] < 25
    elif base_regime == 'Hi_TuWe':  # original: >=22, Tue/Wed
        filters['Hi_TuWe_orig(>=22,TW)'] = lambda d: d['vix'] >= 22 and d['dow'] in (1,2)
        filters['Hi_alldays(>=22)'] = lambda d: d['vix'] >= 22
        filters['Hi_TuWe_w1(>=20,TW)'] = lambda d: d['vix'] >= 20 and d['dow'] in (1,2)
        filters['Hi_alldays_w1(>=20)'] = lambda d: d['vix'] >= 20
        filters['Hi_TuWe_w2(>=18,TW)'] = lambda d: d['vix'] >= 18 and d['dow'] in (1,2)
        filters['Hi_alldays_w2(>=18)'] = lambda d: d['vix'] >= 18
    elif base_regime == 'Low_TuWe':  # original: <18, Tue/Wed
        filters['Low_TuWe_orig(<18,TW)'] = lambda d: d['vix'] < 18 and d['dow'] in (1,2)
        filters['Low_alldays(<18)'] = lambda d: d['vix'] < 18
        filters['Low_TuWe_w1(<20,TW)'] = lambda d: d['vix'] < 20 and d['dow'] in (1,2)
        filters['Low_alldays_w1(<20)'] = lambda d: d['vix'] < 20
        filters['Low_TuWe_w2(<22,TW)'] = lambda d: d['vix'] < 22 and d['dow'] in (1,2)
    elif base_regime == 'MidHi':  # original: 18<=vix<30
        filters['MidHi_orig(18-30)'] = lambda d: 18 <= d['vix'] < 30
        filters['MidHi_w1(16-30)'] = lambda d: 16 <= d['vix'] < 30
        filters['MidHi_w2(18-35)'] = lambda d: 18 <= d['vix'] < 35
        filters['MidHi_w3(16-35)'] = lambda d: 16 <= d['vix'] < 35
        filters['MidHi_w4(15+)'] = lambda d: d['vix'] >= 15
    return filters


# ═══════════════════════════════════════════════════════════════
# DEFINE THE 7 EDGES AND THEIR RELAXATION GRIDS
# ═══════════════════════════════════════════════════════════════

EDGES = [
    {
        'name': 'Edge1_C2_BullHigh_VLow',
        'orig_label': 'C2_BullHigh|VLow|uM_2_1_3|bull_call_5',
        'exit_key': 'uM_2_1_3',
        'struct': 'bull_call_5',
        'vix_base': 'VLow',
        'signal_variants': [
            ('orig(ret>=0.03,rp>0.70)', {'cfb2_ret_min': 0.03, 'range_pos_min': 0.70}),
            ('w1(ret>=0.025,rp>0.70)',  {'cfb2_ret_min': 0.025, 'range_pos_min': 0.70}),
            ('w2(ret>=0.02,rp>0.70)',   {'cfb2_ret_min': 0.02, 'range_pos_min': 0.70}),
            ('w3(ret>=0.03,rp>0.60)',   {'cfb2_ret_min': 0.03, 'range_pos_min': 0.60}),
            ('w4(ret>=0.025,rp>0.60)',  {'cfb2_ret_min': 0.025, 'range_pos_min': 0.60}),
            ('w5(ret>=0.02,rp>0.60)',   {'cfb2_ret_min': 0.02, 'range_pos_min': 0.60}),
            ('w6(ret>=0.02,rp>0.50)',   {'cfb2_ret_min': 0.02, 'range_pos_min': 0.50}),
        ],
        'sig_func': sig_C2_BullHigh,
    },
    {
        'name': 'Edge2_CFB_Trend_VLow',
        'orig_label': 'CFB_Trend|VLow|M_5_2_8|long_itm_call',
        'exit_key': 'M_5_2_8',
        'struct': 'long_itm_call',
        'vix_base': 'VLow',
        'signal_variants': [
            ('orig(br>0.70,ret>=0.03,a20d)',  {'cfb_br_min': 0.70, 'cfb_ret_min': 0.03, 'above_20d_req': True}),
            ('w1(br>0.60,ret>=0.03,a20d)',    {'cfb_br_min': 0.60, 'cfb_ret_min': 0.03, 'above_20d_req': True}),
            ('w2(br>0.70,ret>=0.02,a20d)',    {'cfb_br_min': 0.70, 'cfb_ret_min': 0.02, 'above_20d_req': True}),
            ('w3(br>0.60,ret>=0.02,a20d)',    {'cfb_br_min': 0.60, 'cfb_ret_min': 0.02, 'above_20d_req': True}),
            ('w4(br>0.70,ret>=0.03,no_a20d)', {'cfb_br_min': 0.70, 'cfb_ret_min': 0.03, 'above_20d_req': False}),
            ('w5(br>0.60,ret>=0.02,no_a20d)', {'cfb_br_min': 0.60, 'cfb_ret_min': 0.02, 'above_20d_req': False}),
            ('w6(br>0.50,ret>=0.02,a20d)',    {'cfb_br_min': 0.50, 'cfb_ret_min': 0.02, 'above_20d_req': True}),
        ],
        'sig_func': sig_CFB_Trend,
    },
    {
        'name': 'Edge3_CFB_StrongBull_HiTuWe',
        'orig_label': 'CFB_StrongBull|Hi_TuWe|Av_5_2_10|bull_call_10',
        'exit_key': 'Av_5_2_10',
        'struct': 'bull_call_10',
        'vix_base': 'Hi_TuWe',
        'signal_variants': [
            ('orig(br>0.75,ret>=0.03)', {'cfb_br_min': 0.75, 'cfb_ret_min': 0.03}),
            ('w1(br>0.70,ret>=0.03)',   {'cfb_br_min': 0.70, 'cfb_ret_min': 0.03}),
            ('w2(br>0.65,ret>=0.03)',   {'cfb_br_min': 0.65, 'cfb_ret_min': 0.03}),
            ('w3(br>0.75,ret>=0.02)',   {'cfb_br_min': 0.75, 'cfb_ret_min': 0.02}),
            ('w4(br>0.70,ret>=0.02)',   {'cfb_br_min': 0.70, 'cfb_ret_min': 0.02}),
            ('w5(br>0.65,ret>=0.02)',   {'cfb_br_min': 0.65, 'cfb_ret_min': 0.02}),
            ('w6(br>0.60,ret>=0.02)',   {'cfb_br_min': 0.60, 'cfb_ret_min': 0.02}),
        ],
        'sig_func': sig_CFB_StrongBull,
    },
    {
        'name': 'Edge4_CFB_BearTrend_Low',
        'orig_label': 'CFB_BearTrend|Low|HTC_pure|credit_call_5',
        'exit_key': 'HTC_pure',
        'struct': 'credit_call_5',
        'vix_base': 'Low',
        'signal_variants': [
            ('orig(br>0.70,ret<=-0.03,b20d)',  {'cfb_br_min': 0.70, 'cfb_ret_max': -0.03, 'above_20d_req': True}),
            ('w1(br>0.60,ret<=-0.03,b20d)',    {'cfb_br_min': 0.60, 'cfb_ret_max': -0.03, 'above_20d_req': True}),
            ('w2(br>0.70,ret<=-0.02,b20d)',    {'cfb_br_min': 0.70, 'cfb_ret_max': -0.02, 'above_20d_req': True}),
            ('w3(br>0.60,ret<=-0.02,b20d)',    {'cfb_br_min': 0.60, 'cfb_ret_max': -0.02, 'above_20d_req': True}),
            ('w4(br>0.70,ret<=-0.03,no_b20d)', {'cfb_br_min': 0.70, 'cfb_ret_max': -0.03, 'above_20d_req': False}),
            ('w5(br>0.60,ret<=-0.02,no_b20d)', {'cfb_br_min': 0.60, 'cfb_ret_max': -0.02, 'above_20d_req': False}),
            ('w6(br>0.50,ret<=-0.02,b20d)',    {'cfb_br_min': 0.50, 'cfb_ret_max': -0.02, 'above_20d_req': True}),
        ],
        'sig_func': sig_CFB_BearTrend,
    },
    {
        'name': 'Edge5_C2_BearMorn_VLow',
        'orig_label': 'C2_BearMorn|VLow|uM_1_05_2|bear_put_5',
        'exit_key': 'uM_1_05_2',
        'struct': 'bear_put_5',
        'vix_base': 'VLow',
        'signal_variants': [
            ('orig(ret<=-0.03,morn_bear)',  {'cfb2_ret_max': -0.03, 'morn_bear_req': True}),
            ('w1(ret<=-0.025,morn_bear)',   {'cfb2_ret_max': -0.025, 'morn_bear_req': True}),
            ('w2(ret<=-0.02,morn_bear)',    {'cfb2_ret_max': -0.02, 'morn_bear_req': True}),
            ('w3(ret<=-0.03,no_morn_req)',  {'cfb2_ret_max': -0.03, 'morn_bear_req': False}),
            ('w4(ret<=-0.025,no_morn_req)', {'cfb2_ret_max': -0.025, 'morn_bear_req': False}),
            ('w5(ret<=-0.02,no_morn_req)',  {'cfb2_ret_max': -0.02, 'morn_bear_req': False}),
        ],
        'sig_func': sig_C2_BearMorn,
    },
    {
        'name': 'Edge6_CFB_ReversalLow_LowTuWe',
        'orig_label': 'CFB_ReversalLow|Low_TuWe|HTC_pure|bull_call_5',
        'exit_key': 'HTC_pure',
        'struct': 'bull_call_5',
        'vix_base': 'Low_TuWe',
        'signal_variants': [
            ('orig(mr<=-0.20,ret>=0.03,rp<0.40)',  {'morn_ret_max': -0.20, 'cfb_ret_min': 0.03, 'range_pos_max': 0.40}),
            ('w1(mr<=-0.15,ret>=0.03,rp<0.40)',    {'morn_ret_max': -0.15, 'cfb_ret_min': 0.03, 'range_pos_max': 0.40}),
            ('w2(mr<=-0.20,ret>=0.02,rp<0.40)',    {'morn_ret_max': -0.20, 'cfb_ret_min': 0.02, 'range_pos_max': 0.40}),
            ('w3(mr<=-0.15,ret>=0.02,rp<0.40)',    {'morn_ret_max': -0.15, 'cfb_ret_min': 0.02, 'range_pos_max': 0.40}),
            ('w4(mr<=-0.20,ret>=0.03,rp<0.50)',    {'morn_ret_max': -0.20, 'cfb_ret_min': 0.03, 'range_pos_max': 0.50}),
            ('w5(mr<=-0.15,ret>=0.02,rp<0.50)',    {'morn_ret_max': -0.15, 'cfb_ret_min': 0.02, 'range_pos_max': 0.50}),
            ('w6(mr<=-0.10,ret>=0.02,rp<0.50)',    {'morn_ret_max': -0.10, 'cfb_ret_min': 0.02, 'range_pos_max': 0.50}),
        ],
        'sig_func': sig_CFB_ReversalLow,
    },
    {
        'name': 'Edge7_CFB_FlatBreak_MidHi',
        'orig_label': 'CFB_FlatBreak|MidHi|M_2_15_5|long_itm_call',
        'exit_key': 'M_2_15_5',
        'struct': 'long_itm_call',
        'vix_base': 'MidHi',
        'signal_variants': [
            ('orig(flat<=0.10,ret>=0.04,br>0.65)',  {'morn_flat_band': 0.10, 'cfb_ret_min': 0.04, 'cfb_br_min': 0.65}),
            ('w1(flat<=0.12,ret>=0.04,br>0.65)',    {'morn_flat_band': 0.12, 'cfb_ret_min': 0.04, 'cfb_br_min': 0.65}),
            ('w2(flat<=0.15,ret>=0.04,br>0.65)',    {'morn_flat_band': 0.15, 'cfb_ret_min': 0.04, 'cfb_br_min': 0.65}),
            ('w3(flat<=0.10,ret>=0.03,br>0.65)',    {'morn_flat_band': 0.10, 'cfb_ret_min': 0.03, 'cfb_br_min': 0.65}),
            ('w4(flat<=0.15,ret>=0.03,br>0.65)',    {'morn_flat_band': 0.15, 'cfb_ret_min': 0.03, 'cfb_br_min': 0.65}),
            ('w5(flat<=0.10,ret>=0.04,br>0.55)',    {'morn_flat_band': 0.10, 'cfb_ret_min': 0.04, 'cfb_br_min': 0.55}),
            ('w6(flat<=0.15,ret>=0.03,br>0.55)',    {'morn_flat_band': 0.15, 'cfb_ret_min': 0.03, 'cfb_br_min': 0.55}),
            ('w7(flat<=0.20,ret>=0.03,br>0.55)',    {'morn_flat_band': 0.20, 'cfb_ret_min': 0.03, 'cfb_br_min': 0.55}),
        ],
        'sig_func': sig_CFB_FlatBreak,
    },
]


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*160)
    print("59_widen_edges.py — Parameter Relaxation Scanner")
    print("="*160)

    data = load_all_data()
    days = extract_features(*data)

    all_results = []
    all_trades_for_pricing = []

    for edge in EDGES:
        print(f"\n{'='*120}")
        print(f"  {edge['name']}  (original: {edge['orig_label']})")
        print(f"{'='*120}")

        vix_filters = make_vix_filters(edge['vix_base'])
        exit_params = dict(EXIT_SETS[edge['exit_key']])
        sig_func = edge['sig_func']
        struct = edge['struct']

        combos_for_edge = []

        for sv_name, sv_params in edge['signal_variants']:
            for vf_name, vf_func in vix_filters.items():
                trades = []
                for day in days:
                    if not vf_func(day): continue
                    fires, entry_idx, direction = sig_func(day, **sv_params)
                    if not fires: continue
                    ab = day['afternoon']
                    if entry_idx >= len(ab) - 3: continue
                    epp = dict(exit_params)
                    if epp.get('vix_mult'): epp['_vix'] = day['vix']
                    t = simulate_trade(ab, entry_idx, direction, epp)
                    t['date'] = day['date']; t['vix'] = day['vix']
                    trades.append(t)

                if len(trades) < 5: continue
                label = f"{edge['name']}|sig={sv_name}|vix={vf_name}"
                st = compute_stats(trades, label)
                if not st: continue

                # IS/OOS split
                ist = [t for t in trades if t['date'] < '2023-01-01']
                oost = [t for t in trades if t['date'] >= '2023-01-01']
                is_pts = [t['und_pts'] for t in ist]
                oos_pts = [t['und_pts'] for t in oost]
                if len(is_pts) >= 3:
                    is_avg = statistics.mean(is_pts)
                    is_std = statistics.stdev(is_pts) if len(is_pts)>1 else 0
                    is_sh = is_avg/is_std if is_std > 0 else 0
                else: is_sh = 0
                if len(oos_pts) >= 3:
                    oos_avg = statistics.mean(oos_pts)
                    oos_std = statistics.stdev(oos_pts) if len(oos_pts)>1 else 0
                    oos_sh = oos_avg/oos_std if oos_std > 0 else 0
                else: oos_sh = 0

                is_orig = 'orig' in sv_name and 'orig' in vf_name

                rec = {
                    'edge': edge['name'],
                    'orig_label': edge['orig_label'],
                    'sig_variant': sv_name,
                    'vix_variant': vf_name,
                    'struct': struct,
                    'exit': edge['exit_key'],
                    'n': st['n'],
                    'n_is': len(ist), 'n_oos': len(oost),
                    'wr': st['wr'],
                    'avg': st['avg'],
                    'tot': st['tot'],
                    'sh': st['sh'],
                    'pf': st['pf'],
                    'dd': st['dd'],
                    'r2': st['r2'],
                    'is_sh': round(is_sh, 3),
                    'oos_sh': round(oos_sh, 3),
                    'is_original': is_orig,
                    'label': label,
                }
                combos_for_edge.append(rec)

                # Store trades for option pricing if promising
                if st['sh'] > 0.15 and oos_sh > 0 and st['n'] >= 8:
                    all_trades_for_pricing.append({
                        'label': label,
                        'struct': struct,
                        'trades': trades,
                        'rec': rec,
                    })

        # Print summary for this edge
        combos_for_edge.sort(key=lambda x: (-x['n'], -x['sh']))
        print(f"\n  {'Label':<85s} {'N':>4} {'IS':>4} {'OOS':>4} {'WR':>5} {'Avg':>7} {'Sh':>6} {'PF':>5} {'R²':>5} {'ISSh':>6} {'OOSSh':>6}")
        print("  " + "-"*140)
        for r in combos_for_edge:
            marker = " <<<" if r['is_original'] else ""
            print(f"  {r['label'][-83:]:>83s} {r['n']:>4} {r['n_is']:>4} {r['n_oos']:>4} "
                  f"{r['wr']:>4.1f}% {r['avg']:>+6.2f} {r['sh']:>6.3f} {r['pf']:>5.2f} "
                  f"{r['r2']:>5.3f} {r['is_sh']:>6.3f} {r['oos_sh']:>6.3f}{marker}")
        all_results.extend(combos_for_edge)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: OPTION PRICING for promising widened combos
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*160}")
    print(f"PHASE 2: OPTION PRICING — {len(all_trades_for_pricing)} promising combos")
    print(f"{'='*160}")

    opt_results = []
    for item in all_trades_for_pricing:
        label = item['label']
        struct = item['struct']
        trades = item['trades']
        rec = item['rec']

        priced = []; missed = 0
        for trade in trades:
            pnl, info = price_opt(trade['date'], trade['entry_mins'], trade['exit_mins'],
                                  trade['entry_price'], trade['direction'], struct)
            if pnl is not None:
                priced.append(pnl)
            else:
                missed += 1

        if len(priced) < 5: continue
        avg = statistics.mean(priced); tot = sum(priced)
        wr = sum(1 for p in priced if p > 0)/len(priced)*100
        std = statistics.stdev(priced) if len(priced)>1 else 0
        sh = avg/std if std > 0 else 0
        gw = sum(p for p in priced if p > 0)
        gl = abs(sum(p for p in priced if p <= 0))
        pf = gw/gl if gl > 0 else 99
        cov = len(priced) / (len(priced)+missed) * 100

        # IS/OOS on option P&L
        is_opt = []; oos_opt = []
        for trade in trades:
            pnl, _ = price_opt(trade['date'], trade['entry_mins'], trade['exit_mins'],
                               trade['entry_price'], trade['direction'], struct)
            if pnl is None: continue
            if trade['date'] < '2023-01-01': is_opt.append(pnl)
            else: oos_opt.append(pnl)
        ois_sh = oos_sh = 0
        if len(is_opt) >= 3:
            m = statistics.mean(is_opt); s = statistics.stdev(is_opt) if len(is_opt)>1 else 0
            ois_sh = m/s if s > 0 else 0
        if len(oos_opt) >= 3:
            m = statistics.mean(oos_opt); s = statistics.stdev(oos_opt) if len(oos_opt)>1 else 0
            oos_sh = m/s if s > 0 else 0

        opt_results.append({
            'label': label,
            'edge': rec['edge'],
            'sig_variant': rec['sig_variant'],
            'vix_variant': rec['vix_variant'],
            'struct': struct,
            'exit': rec['exit'],
            'und_n': rec['n'],
            'opt_n': len(priced),
            'cov_pct': round(cov, 1),
            'opt_wr': round(wr, 1),
            'opt_avg': round(avg, 2),
            'opt_tot': round(tot, 2),
            'opt_sh': round(sh, 3),
            'opt_pf': round(pf, 2),
            'opt_is_sh': round(ois_sh, 3),
            'opt_oos_sh': round(oos_sh, 3),
            'und_sh': rec['sh'],
            'und_wr': rec['wr'],
            'is_original': rec['is_original'],
        })

    opt_results.sort(key=lambda x: x['opt_sh'], reverse=True)

    print(f"\n  {'Label':<90s} {'uN':>3} {'oN':>3} {'Cov':>4} {'WR':>5} {'Avg$':>8} {'Sh':>6} {'PF':>5} {'ISsh':>6} {'OOSsh':>6} {'uSh':>5}")
    print("  " + "-"*145)
    for r in opt_results:
        marker = " <<<" if r['is_original'] else ""
        print(f"  {r['label'][-88:]:>88s} {r['und_n']:>3} {r['opt_n']:>3} {r['cov_pct']:>3.0f}% "
              f"{r['opt_wr']:>4.1f}% ${r['opt_avg']:>+7.2f} {r['opt_sh']:>6.3f} {r['opt_pf']:>5.2f} "
              f"{r['opt_is_sh']:>6.3f} {r['opt_oos_sh']:>6.3f} {r['und_sh']:>5.3f}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════
    with open(OUT_DIR / 'closing_v2_widened_underlying.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    with open(OUT_DIR / 'closing_v2_widened_options.json', 'w') as f:
        json.dump(opt_results, f, indent=2)

    # ═══════════════════════════════════════════════════════════════
    # BEST-PER-EDGE SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*160}")
    print("BEST WIDENED VARIANT PER EDGE (by option Sharpe, OOS>0)")
    print(f"{'='*160}")

    for edge in EDGES:
        ename = edge['name']
        edge_opts = [r for r in opt_results if r['edge'] == ename and r['opt_oos_sh'] > 0]
        orig = [r for r in edge_opts if r['is_original']]
        widened = [r for r in edge_opts if not r['is_original']]

        print(f"\n  {ename}:")
        if orig:
            o = orig[0]
            print(f"    ORIGINAL: N={o['opt_n']}, Sh={o['opt_sh']:.3f}, OOS={o['opt_oos_sh']:.3f}, WR={o['opt_wr']:.1f}%, Avg=${o['opt_avg']:+.2f}")
        else:
            print(f"    ORIGINAL: not priced or no positive OOS")

        if widened:
            widened.sort(key=lambda x: x['opt_sh'], reverse=True)
            # Show top 3 widened variants
            for i, w in enumerate(widened[:3]):
                delta_n = w['opt_n'] - (orig[0]['opt_n'] if orig else 0)
                delta_sh = w['opt_sh'] - (orig[0]['opt_sh'] if orig else 0)
                print(f"    WIDE #{i+1}: N={w['opt_n']}(+{delta_n}), Sh={w['opt_sh']:.3f}({delta_sh:+.3f}), "
                      f"OOS={w['opt_oos_sh']:.3f}, WR={w['opt_wr']:.1f}%, "
                      f"sig={w['sig_variant']}, vix={w['vix_variant']}")
        else:
            print(f"    No widened variants survived with positive OOS")

    # Summary text file
    with open(OUT_DIR / 'closing_v2_widened_summary.txt', 'w') as f:
        f.write("PARAMETER RELAXATION RESULTS\n")
        f.write("="*80 + "\n\n")
        f.write(f"Total underlying combos tested: {len(all_results)}\n")
        f.write(f"Total option-priced combos: {len(opt_results)}\n")
        f.write(f"Positive OOS option Sharpe: {sum(1 for r in opt_results if r['opt_oos_sh'] > 0)}\n\n")

        for edge in EDGES:
            ename = edge['name']
            edge_opts = [r for r in opt_results if r['edge'] == ename and r['opt_oos_sh'] > 0]
            orig = [r for r in edge_opts if r['is_original']]
            widened = sorted([r for r in edge_opts if not r['is_original']],
                           key=lambda x: x['opt_sh'], reverse=True)

            f.write(f"\n{ename}\n" + "-"*60 + "\n")
            if orig:
                o = orig[0]
                f.write(f"  ORIGINAL: N={o['opt_n']}, Sh={o['opt_sh']:.3f}, OOS={o['opt_oos_sh']:.3f}, WR={o['opt_wr']:.1f}%\n")
            for i, w in enumerate(widened[:5]):
                f.write(f"  WIDE #{i+1}: N={w['opt_n']}, Sh={w['opt_sh']:.3f}, OOS={w['opt_oos_sh']:.3f}, "
                        f"WR={w['opt_wr']:.1f}%, sig={w['sig_variant']}, vix={w['vix_variant']}\n")

    print(f"\n\nSaved: closing_v2_widened_underlying.json ({len(all_results)} combos)")
    print(f"Saved: closing_v2_widened_options.json ({len(opt_results)} combos)")
    print(f"Saved: closing_v2_widened_summary.txt")
    print("\nDONE")


if __name__ == '__main__':
    main()
