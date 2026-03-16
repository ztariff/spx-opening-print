#!/usr/bin/env python3
"""
60_widen_opening_edges.py — Parameter Relaxation for Opening Print Edges
=========================================================================
For each of the 5 validated opening print edges (from 52_ultra_sharpe_scanner),
systematically relax parameters and measure N gained vs Sharpe/WR lost.

The 5 edges:
  1. Bull_GapCont|Fri|Std_10_5_15          — bull_call_5  (N=37, Sh=0.733)
  2. StrongBody_Bull|VeryLow|Micro_2_1.5_5 — long_call    (N=29, Sh=1.290)
  3. StrongBody_PrevBull|Fri|Asym_12_4_20  — bull_call_10 (N=23, Sh=0.951)
  4. StrongBody_Trend|VeryLow|Micro_2_1.5_5— long_call    (N=28, Sh=1.260)
  5. TripleBull_BodyTrendPrev|VeryLow|Micro_4_2_8 — long_call (N=19, Sh=0.853)

Output: backtest_results/opening_v2_widened_options.json
        backtest_results/opening_v2_widened_summary.txt
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
# DATA LOADING (from 52)
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

    print(f"  SPX 1min: {len(spx_1min)} days")
    return spx_1min, spx_daily, vix_daily, sma20, sma50, prev_days, vix_prev_close, rsi5, atr5


def extract_features(spx_1min, spx_daily, vix_daily, sma20, sma50, prev_days, vix_prev_close, rsi5, atr5):
    print("Extracting opening-window features…")
    days = []
    for d in sorted(spx_1min.keys()):
        if d < START_DATE: continue
        bars = spx_1min[d]
        dd = spx_daily.get(d); vd = vix_daily.get(d)
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

        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_body_ratio = abs(fb['c'] - fb['o']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0
        fb_lower_wick = (min(fb['o'], fb['c']) - fb['l']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0

        days.append({
            'date': d, 'bars': bars,
            'open': dd['o'], 'close': dd['c'],
            'vix': vix_open, 'vix_change_pct': vix_change_pct,
            'gap_pct': gap_pct,
            'prev_close_loc': prev_close_loc, 'prev_bullish': prev_bullish,
            'above_20d': above_20d, 'above_50d': above_50d, 'dow': dow,
            'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish,
            'fb_body_ratio': fb_body_ratio, 'fb_lower_wick': fb_lower_wick,
            'rsi5': rsi5.get(d, 50), 'atr5': atr5.get(d, 30),
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    'Micro_2_1.5_5': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
    'Micro_4_2_8':   {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
    'Std_10_5_15':   {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
    'Asym_12_4_20':  {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
}

def simulate_trade(bars, entry_idx, direction, exit_params):
    eb = bars[entry_idx]
    ep = eb['c']; em = eb['mins']
    pt = exit_params.get('pt_pts'); sl = exit_params.get('sl_pts')
    trail = exit_params.get('trail_pct')
    ts = exit_params.get('ts_min', 60)
    deadline = em + ts

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

    xp = ep; xr = 'time_stop'; xi = len(bars)-1
    peak = trough = ep

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
    if len(trades) < 5: return None
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
    return {'label': label, 'n': n, 'wr': round(wr,1), 'avg': round(avg,2),
            'tot': round(tot,1), 'sh': round(sh,3), 'pf': round(pf,2),
            'dd': round(dd,1), 'r2': round(r2,3)}


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

    if struct == 'long_call': return single('C', atm)
    elif struct == 'long_itm_call': return single('C', atm-5)
    elif struct == 'long_otm_call': return single('C', atm+5)
    elif struct == 'bull_call_5': return spread('C', atm, atm+5)
    elif struct == 'bull_call_10': return spread('C', atm, atm+10)
    return None, None


# ═══════════════════════════════════════════════════════════════
# PARAMETERIZED SIGNAL FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def sig_Bull_GapCont(d, fb_ret_min=0.02, gap_min=0.05, gap_max=0.30):
    """Original: fb_bullish, fb_ret>=0.02, 0.05<=gap<=0.30"""
    if d['fb_bullish'] and d['fb_ret'] >= fb_ret_min and gap_min <= d['gap_pct'] <= gap_max:
        return True, 0, 1
    return False, 0, 1

def sig_StrongBody_Bull(d, body_ratio_min=0.80, fb_ret_min=0.03):
    """Original: fb_body_ratio>0.80, fb_bullish, fb_ret>=0.03"""
    if d['fb_body_ratio'] > body_ratio_min and d['fb_bullish'] and d['fb_ret'] >= fb_ret_min:
        return True, 0, 1
    return False, 0, 1

def sig_StrongBody_PrevBull(d, body_ratio_min=0.80, fb_ret_min=0.03, prev_bull_req=True):
    """Original: fb_body_ratio>0.80, fb_bullish, fb_ret>=0.03, prev_bullish"""
    if (d['fb_body_ratio'] > body_ratio_min and d['fb_bullish'] and d['fb_ret'] >= fb_ret_min
        and (d['prev_bullish'] if prev_bull_req else True)):
        return True, 0, 1
    return False, 0, 1

def sig_StrongBody_Trend(d, body_ratio_min=0.80, fb_ret_min=0.03, above_20d_req=True):
    """Original: fb_body_ratio>0.80, fb_bullish, fb_ret>=0.03, above_20d"""
    if (d['fb_body_ratio'] > body_ratio_min and d['fb_bullish'] and d['fb_ret'] >= fb_ret_min
        and (d['above_20d'] is True if above_20d_req else True)):
        return True, 0, 1
    return False, 0, 1

def sig_TripleBull_BodyTrendPrev(d, body_ratio_min=0.75, fb_ret_min=0.03,
                                  above_20d_req=True, prev_bull_req=True):
    """Original: fb_body_ratio>0.75, fb_bullish, fb_ret>=0.03, above_20d, prev_bullish"""
    if (d['fb_body_ratio'] > body_ratio_min and d['fb_bullish'] and d['fb_ret'] >= fb_ret_min
        and (d['above_20d'] is True if above_20d_req else True)
        and (d['prev_bullish'] if prev_bull_req else True)):
        return True, 0, 1
    return False, 0, 1


# ═══════════════════════════════════════════════════════════════
# VIX / DOW FILTER VARIANTS
# ═══════════════════════════════════════════════════════════════
def make_vix_filters(base):
    f = {}
    if base == 'VeryLow':  # orig <14
        f['VLow_orig(<14)'] = lambda d: d['vix'] < 14
        f['VLow_w1(<15)'] = lambda d: d['vix'] < 15
        f['VLow_w2(<16)'] = lambda d: d['vix'] < 16
        f['VLow_w3(<17)'] = lambda d: d['vix'] < 17
        f['VLow_w4(<18)'] = lambda d: d['vix'] < 18
    elif base == 'Fri':  # orig Friday only
        f['Fri_orig'] = lambda d: d['dow'] == 4
        f['Fri_or_Mon'] = lambda d: d['dow'] in (0, 4)
        f['Fri_or_Thu'] = lambda d: d['dow'] in (3, 4)
        f['NotTuWe'] = lambda d: d['dow'] not in (1, 2)
        f['AllDays'] = lambda d: True
        f['Fri_VLow'] = lambda d: d['dow'] == 4 and d['vix'] < 18
        f['Fri_lt20'] = lambda d: d['dow'] == 4 and d['vix'] < 20
    return f


# ═══════════════════════════════════════════════════════════════
# DEFINE THE 5 EDGES AND THEIR RELAXATION GRIDS
# ═══════════════════════════════════════════════════════════════
EDGES = [
    {
        'name': 'Edge1_Bull_GapCont_Fri',
        'orig_label': 'Bull_GapCont|Fri|Std_10_5_15|bull_call_5',
        'exit_key': 'Std_10_5_15',
        'struct': 'bull_call_5',
        'vix_base': 'Fri',
        'signal_variants': [
            ('orig(ret>=0.02,gap=0.05-0.30)', {'fb_ret_min': 0.02, 'gap_min': 0.05, 'gap_max': 0.30}),
            ('w1(ret>=0.015,gap=0.05-0.30)',  {'fb_ret_min': 0.015, 'gap_min': 0.05, 'gap_max': 0.30}),
            ('w2(ret>=0.02,gap=0.03-0.30)',   {'fb_ret_min': 0.02, 'gap_min': 0.03, 'gap_max': 0.30}),
            ('w3(ret>=0.02,gap=0.05-0.40)',   {'fb_ret_min': 0.02, 'gap_min': 0.05, 'gap_max': 0.40}),
            ('w4(ret>=0.015,gap=0.03-0.30)',  {'fb_ret_min': 0.015, 'gap_min': 0.03, 'gap_max': 0.30}),
            ('w5(ret>=0.015,gap=0.03-0.40)',  {'fb_ret_min': 0.015, 'gap_min': 0.03, 'gap_max': 0.40}),
            ('w6(ret>=0.01,gap=0.03-0.40)',   {'fb_ret_min': 0.01, 'gap_min': 0.03, 'gap_max': 0.40}),
        ],
        'sig_func': sig_Bull_GapCont,
    },
    {
        'name': 'Edge2_StrongBody_Bull_VLow',
        'orig_label': 'StrongBody_Bull|VeryLow|Micro_2_1.5_5|long_call',
        'exit_key': 'Micro_2_1.5_5',
        'struct': 'long_call',
        'vix_base': 'VeryLow',
        'signal_variants': [
            ('orig(br>0.80,ret>=0.03)', {'body_ratio_min': 0.80, 'fb_ret_min': 0.03}),
            ('w1(br>0.75,ret>=0.03)',   {'body_ratio_min': 0.75, 'fb_ret_min': 0.03}),
            ('w2(br>0.70,ret>=0.03)',   {'body_ratio_min': 0.70, 'fb_ret_min': 0.03}),
            ('w3(br>0.80,ret>=0.025)',  {'body_ratio_min': 0.80, 'fb_ret_min': 0.025}),
            ('w4(br>0.75,ret>=0.025)',  {'body_ratio_min': 0.75, 'fb_ret_min': 0.025}),
            ('w5(br>0.70,ret>=0.025)',  {'body_ratio_min': 0.70, 'fb_ret_min': 0.025}),
            ('w6(br>0.80,ret>=0.02)',   {'body_ratio_min': 0.80, 'fb_ret_min': 0.02}),
            ('w7(br>0.75,ret>=0.02)',   {'body_ratio_min': 0.75, 'fb_ret_min': 0.02}),
            ('w8(br>0.65,ret>=0.03)',   {'body_ratio_min': 0.65, 'fb_ret_min': 0.03}),
        ],
        'sig_func': sig_StrongBody_Bull,
    },
    {
        'name': 'Edge3_StrongBody_PrevBull_Fri',
        'orig_label': 'StrongBody_PrevBull|Fri|Asym_12_4_20|bull_call_10',
        'exit_key': 'Asym_12_4_20',
        'struct': 'bull_call_10',
        'vix_base': 'Fri',
        'signal_variants': [
            ('orig(br>0.80,ret>=0.03,prev)',    {'body_ratio_min': 0.80, 'fb_ret_min': 0.03, 'prev_bull_req': True}),
            ('w1(br>0.75,ret>=0.03,prev)',      {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'prev_bull_req': True}),
            ('w2(br>0.70,ret>=0.03,prev)',      {'body_ratio_min': 0.70, 'fb_ret_min': 0.03, 'prev_bull_req': True}),
            ('w3(br>0.80,ret>=0.025,prev)',     {'body_ratio_min': 0.80, 'fb_ret_min': 0.025, 'prev_bull_req': True}),
            ('w4(br>0.75,ret>=0.025,prev)',     {'body_ratio_min': 0.75, 'fb_ret_min': 0.025, 'prev_bull_req': True}),
            ('w5(br>0.80,ret>=0.03,no_prev)',   {'body_ratio_min': 0.80, 'fb_ret_min': 0.03, 'prev_bull_req': False}),
            ('w6(br>0.75,ret>=0.03,no_prev)',   {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'prev_bull_req': False}),
            ('w7(br>0.80,ret>=0.02,prev)',      {'body_ratio_min': 0.80, 'fb_ret_min': 0.02, 'prev_bull_req': True}),
            ('w8(br>0.75,ret>=0.02,no_prev)',   {'body_ratio_min': 0.75, 'fb_ret_min': 0.02, 'prev_bull_req': False}),
        ],
        'sig_func': sig_StrongBody_PrevBull,
    },
    {
        'name': 'Edge4_StrongBody_Trend_VLow',
        'orig_label': 'StrongBody_Trend|VeryLow|Micro_2_1.5_5|long_call',
        'exit_key': 'Micro_2_1.5_5',
        'struct': 'long_call',
        'vix_base': 'VeryLow',
        'signal_variants': [
            ('orig(br>0.80,ret>=0.03,a20d)',    {'body_ratio_min': 0.80, 'fb_ret_min': 0.03, 'above_20d_req': True}),
            ('w1(br>0.75,ret>=0.03,a20d)',      {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'above_20d_req': True}),
            ('w2(br>0.70,ret>=0.03,a20d)',      {'body_ratio_min': 0.70, 'fb_ret_min': 0.03, 'above_20d_req': True}),
            ('w3(br>0.80,ret>=0.025,a20d)',     {'body_ratio_min': 0.80, 'fb_ret_min': 0.025, 'above_20d_req': True}),
            ('w4(br>0.75,ret>=0.025,a20d)',     {'body_ratio_min': 0.75, 'fb_ret_min': 0.025, 'above_20d_req': True}),
            ('w5(br>0.80,ret>=0.03,no_a20d)',   {'body_ratio_min': 0.80, 'fb_ret_min': 0.03, 'above_20d_req': False}),
            ('w6(br>0.75,ret>=0.03,no_a20d)',   {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'above_20d_req': False}),
            ('w7(br>0.80,ret>=0.02,a20d)',      {'body_ratio_min': 0.80, 'fb_ret_min': 0.02, 'above_20d_req': True}),
        ],
        'sig_func': sig_StrongBody_Trend,
    },
    {
        'name': 'Edge5_TripleBull_VLow',
        'orig_label': 'TripleBull_BodyTrendPrev|VeryLow|Micro_4_2_8|long_call',
        'exit_key': 'Micro_4_2_8',
        'struct': 'long_call',
        'vix_base': 'VeryLow',
        'signal_variants': [
            ('orig(br>0.75,ret>=0.03,a20d,prev)',     {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'above_20d_req': True, 'prev_bull_req': True}),
            ('w1(br>0.70,ret>=0.03,a20d,prev)',       {'body_ratio_min': 0.70, 'fb_ret_min': 0.03, 'above_20d_req': True, 'prev_bull_req': True}),
            ('w2(br>0.65,ret>=0.03,a20d,prev)',       {'body_ratio_min': 0.65, 'fb_ret_min': 0.03, 'above_20d_req': True, 'prev_bull_req': True}),
            ('w3(br>0.75,ret>=0.025,a20d,prev)',      {'body_ratio_min': 0.75, 'fb_ret_min': 0.025, 'above_20d_req': True, 'prev_bull_req': True}),
            ('w4(br>0.70,ret>=0.025,a20d,prev)',      {'body_ratio_min': 0.70, 'fb_ret_min': 0.025, 'above_20d_req': True, 'prev_bull_req': True}),
            ('w5(br>0.75,ret>=0.03,no_a20d,prev)',    {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'above_20d_req': False, 'prev_bull_req': True}),
            ('w6(br>0.75,ret>=0.03,a20d,no_prev)',    {'body_ratio_min': 0.75, 'fb_ret_min': 0.03, 'above_20d_req': True, 'prev_bull_req': False}),
            ('w7(br>0.70,ret>=0.025,a20d,no_prev)',   {'body_ratio_min': 0.70, 'fb_ret_min': 0.025, 'above_20d_req': True, 'prev_bull_req': False}),
            ('w8(br>0.65,ret>=0.025,no_a20d,no_prev)',{'body_ratio_min': 0.65, 'fb_ret_min': 0.025, 'above_20d_req': False, 'prev_bull_req': False}),
        ],
        'sig_func': sig_TripleBull_BodyTrendPrev,
    },
]


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*160)
    print("60_widen_opening_edges.py — Opening Print Parameter Relaxation")
    print("="*160)

    data = load_all_data()
    days = extract_features(*data)

    all_results = []
    all_for_pricing = []

    for edge in EDGES:
        print(f"\n{'='*120}")
        print(f"  {edge['name']}  (original: {edge['orig_label']})")
        print(f"{'='*120}")

        vix_filters = make_vix_filters(edge['vix_base'])
        exit_params = dict(EXIT_SETS[edge['exit_key']])
        sig_func = edge['sig_func']
        struct = edge['struct']

        combos = []

        for sv_name, sv_params in edge['signal_variants']:
            for vf_name, vf_func in vix_filters.items():
                trades = []
                for day in days:
                    if not vf_func(day): continue
                    fires, entry_idx, direction = sig_func(day, **sv_params)
                    if not fires: continue
                    ab = day['bars']
                    if entry_idx >= len(ab) - 3: continue
                    epp = dict(exit_params)
                    if epp.get('vix_mult'): epp['_vix'] = day['vix']
                    t = simulate_trade(ab, entry_idx, direction, epp)
                    t['date'] = day['date']; t['vix'] = day['vix']
                    trades.append(t)

                if len(trades) < 5: continue
                label = f"{edge['name']}|sig={sv_name}|flt={vf_name}"
                st = compute_stats(trades, label)
                if not st: continue

                ist = [t for t in trades if t['date'] < '2023-01-01']
                oost = [t for t in trades if t['date'] >= '2023-01-01']
                is_pts = [t['und_pts'] for t in ist]
                oos_pts = [t['und_pts'] for t in oost]
                is_sh = oos_sh = 0
                if len(is_pts) >= 3:
                    m = statistics.mean(is_pts); s = statistics.stdev(is_pts) if len(is_pts)>1 else 0
                    is_sh = m/s if s > 0 else 0
                if len(oos_pts) >= 3:
                    m = statistics.mean(oos_pts); s = statistics.stdev(oos_pts) if len(oos_pts)>1 else 0
                    oos_sh = m/s if s > 0 else 0

                is_orig = 'orig' in sv_name and 'orig' in vf_name

                rec = {
                    'edge': edge['name'], 'orig_label': edge['orig_label'],
                    'sig_variant': sv_name, 'vix_variant': vf_name,
                    'struct': struct, 'exit': edge['exit_key'],
                    'n': st['n'], 'n_is': len(ist), 'n_oos': len(oost),
                    'wr': st['wr'], 'avg': st['avg'], 'tot': st['tot'],
                    'sh': st['sh'], 'pf': st['pf'], 'dd': st['dd'], 'r2': st['r2'],
                    'is_sh': round(is_sh, 3), 'oos_sh': round(oos_sh, 3),
                    'is_original': is_orig, 'label': label,
                }
                combos.append(rec)

                if st['sh'] > 0.15 and oos_sh > 0 and st['n'] >= 8:
                    all_for_pricing.append({
                        'label': label, 'struct': struct,
                        'trades': trades, 'rec': rec,
                    })

        combos.sort(key=lambda x: (-x['n'], -x['sh']))
        print(f"\n  {'Label':<90s} {'N':>4} {'IS':>4} {'OOS':>4} {'WR':>5} {'Avg':>7} {'Sh':>6} {'PF':>5} {'R²':>5} {'ISSh':>6} {'OOSSh':>6}")
        print("  " + "-"*145)
        for r in combos:
            marker = " <<<" if r['is_original'] else ""
            print(f"  {r['label'][-88:]:>88s} {r['n']:>4} {r['n_is']:>4} {r['n_oos']:>4} "
                  f"{r['wr']:>4.1f}% {r['avg']:>+6.2f} {r['sh']:>6.3f} {r['pf']:>5.2f} "
                  f"{r['r2']:>5.3f} {r['is_sh']:>6.3f} {r['oos_sh']:>6.3f}{marker}")
        all_results.extend(combos)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: OPTION PRICING
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*160}")
    print(f"PHASE 2: OPTION PRICING — {len(all_for_pricing)} promising combos")
    print(f"{'='*160}")

    opt_results = []
    for item in all_for_pricing:
        label = item['label']; struct = item['struct']
        trades = item['trades']; rec = item['rec']

        priced = []; missed = 0
        for trade in trades:
            pnl, info = price_opt(trade['date'], trade['entry_mins'], trade['exit_mins'],
                                  trade['entry_price'], trade['direction'], struct)
            if pnl is not None: priced.append(pnl)
            else: missed += 1

        if len(priced) < 5: continue
        avg = statistics.mean(priced); tot = sum(priced)
        wr = sum(1 for p in priced if p > 0)/len(priced)*100
        std = statistics.stdev(priced) if len(priced)>1 else 0
        sh = avg/std if std > 0 else 0
        gw = sum(p for p in priced if p > 0)
        gl = abs(sum(p for p in priced if p <= 0))
        pf = gw/gl if gl > 0 else 99
        cov = len(priced) / (len(priced)+missed) * 100

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
            'label': label, 'edge': rec['edge'],
            'sig_variant': rec['sig_variant'], 'vix_variant': rec['vix_variant'],
            'struct': struct, 'exit': rec['exit'],
            'und_n': rec['n'], 'opt_n': len(priced),
            'cov_pct': round(cov, 1),
            'opt_wr': round(wr, 1), 'opt_avg': round(avg, 2),
            'opt_tot': round(tot, 2), 'opt_sh': round(sh, 3),
            'opt_pf': round(pf, 2),
            'opt_is_sh': round(ois_sh, 3), 'opt_oos_sh': round(oos_sh, 3),
            'und_sh': rec['sh'], 'und_wr': rec['wr'],
            'is_original': rec['is_original'],
        })

    opt_results.sort(key=lambda x: x['opt_sh'], reverse=True)

    print(f"\n  {'Label':<95s} {'uN':>3} {'oN':>3} {'Cov':>4} {'WR':>5} {'Avg$':>8} {'Sh':>6} {'PF':>5} {'ISsh':>6} {'OOSsh':>6} {'uSh':>5}")
    print("  " + "-"*150)
    for r in opt_results:
        marker = " <<<" if r['is_original'] else ""
        print(f"  {r['label'][-93:]:>93s} {r['und_n']:>3} {r['opt_n']:>3} {r['cov_pct']:>3.0f}% "
              f"{r['opt_wr']:>4.1f}% ${r['opt_avg']:>+7.2f} {r['opt_sh']:>6.3f} {r['opt_pf']:>5.2f} "
              f"{r['opt_is_sh']:>6.3f} {r['opt_oos_sh']:>6.3f} {r['und_sh']:>5.3f}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════
    with open(OUT_DIR / 'opening_v2_widened_underlying.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    with open(OUT_DIR / 'opening_v2_widened_options.json', 'w') as f:
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
        widened = sorted([r for r in edge_opts if not r['is_original']],
                        key=lambda x: x['opt_sh'], reverse=True)

        print(f"\n  {ename}:")
        if orig:
            o = orig[0]
            print(f"    ORIGINAL: N={o['opt_n']}, Sh={o['opt_sh']:.3f}, OOS={o['opt_oos_sh']:.3f}, WR={o['opt_wr']:.1f}%, Avg=${o['opt_avg']:+.2f}")
        else:
            print(f"    ORIGINAL: not priced or no positive OOS")

        for i, w in enumerate(widened[:3]):
            delta_n = w['opt_n'] - (orig[0]['opt_n'] if orig else 0)
            delta_sh = w['opt_sh'] - (orig[0]['opt_sh'] if orig else 0)
            print(f"    WIDE #{i+1}: N={w['opt_n']}({'+' if delta_n>=0 else ''}{delta_n}), Sh={w['opt_sh']:.3f}({delta_sh:+.3f}), "
                  f"OOS={w['opt_oos_sh']:.3f}, WR={w['opt_wr']:.1f}%, "
                  f"sig={w['sig_variant']}, flt={w['vix_variant']}")

    # Summary text file
    with open(OUT_DIR / 'opening_v2_widened_summary.txt', 'w') as f:
        f.write("OPENING PRINT PARAMETER RELAXATION RESULTS\n")
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
                        f"WR={w['opt_wr']:.1f}%, sig={w['sig_variant']}, flt={w['vix_variant']}\n")

    print(f"\n\nSaved: opening_v2_widened_underlying.json ({len(all_results)} combos)")
    print(f"Saved: opening_v2_widened_options.json ({len(opt_results)} combos)")
    print(f"Saved: opening_v2_widened_summary.txt")
    print("\nDONE")


if __name__ == '__main__':
    main()
