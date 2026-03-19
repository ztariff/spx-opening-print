#!/usr/bin/env python3
"""
61_widened_portfolio_calendar.py — Updated Portfolio + Trade Calendar
======================================================================
Generates the widened portfolio (opening + closing) with real option-priced
trades and outputs:
  1. backtest_results/widened_portfolio.json — edge definitions
  2. backtest_results/widened_portfolio_trades.json — all verified trades
  3. widened_trade_calendar.html — interactive calendar

WIDENED OPENING EDGES (from 60_widen_opening_edges.py):
  1. Bull_GapCont|Fri|Std_10_5_15|bull_call_5 — KEEP ORIGINAL
  2. StrongBody_Bull|VeryLow|Micro_2_1.5_5|long_call — KEEP ORIGINAL
  3. StrongBody_PrevBull|Fri|Asym_12_4_20|bull_call_10 — ADD VIX<20, br 0.80→0.75
  4. StrongBody_Trend|VeryLow|Micro_2_1.5_5|long_call — DROP above_20d
  5. TripleBull_BodyTrendPrev|VeryLow|Micro_4_2_8|long_call — DROP above_20d, VIX<14→<15

WIDENED CLOSING EDGES (from 59_widen_edges.py):
  1. C2_BullHigh|VLow|uM_2_1_3|bull_call_5 — KEEP ORIGINAL
  2. CFB_Trend|VLow|M_5_2_8|long_itm_call — KEEP ORIGINAL
  3. CFB_StrongBull|Hi_TuWe|Av_5_2_10|bull_call_10 — br 0.75→0.65
  4. CFB_BearTrend|Low|HTC_pure|credit_call_5 — VIX<18→<20, br 0.70→0.60
  5. C2_BearMorn|VLow|uM_1_05_2|bear_put_5 — DROP morning req
  6. CFB_ReversalLow|Low_TuWe|HTC_pure|bull_call_5 — KEEP ORIGINAL
  7. CFB_FlatBreak|MidHi|M_2_15_5|long_itm_call — ret 0.04→0.03

All CLAUDE.md rules apply. No fabricated data. Real SPXW option bars only.
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
    return spx_1min, spx_daily, vix_daily, sma20, sma50, prev_days, vix_prev_close, atr5


# ═══════════════════════════════════════════════════════════════
# FEATURE EXTRACTION
# ═══════════════════════════════════════════════════════════════
def extract_features(spx_1min, spx_daily, vix_daily, sma20, sma50, prev_days, vix_prev_close, atr5):
    print("Extracting features…")
    days = []
    for d in sorted(spx_1min.keys()):
        if d < START_DATE: continue
        all_bars = spx_1min[d]
        dd = spx_daily.get(d); vd = vix_daily.get(d)
        prev = prev_days.get(d)
        if not dd or not vd or not prev or len(all_bars) < 60: continue

        vix_open = vd['o']
        vix_pc = vix_prev_close.get(d, vix_open)
        prev_close = prev[0]['c']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100
        prev_bullish = prev[0]['c'] > prev[0]['o']
        prev_range = prev[0]['h'] - prev[0]['l']
        prev_close_loc = (prev[0]['c'] - prev[0]['l']) / prev_range if prev_range > 0 else 0.5

        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        # First bar (opening print)
        fb = all_bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_body_ratio = abs(fb['c'] - fb['o']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0

        # Morning/afternoon split for closing print
        morning = [b for b in all_bars if b['mins'] < 840]
        afternoon = [b for b in all_bars if b['mins'] >= 840]

        # Morning features
        morn_ret = morn_bullish = range_pos = 0
        cfb_ret = cfb_bull = cfb_br = 0
        cfb2_bull = cfb2_bear = False; cfb2_ret = 0

        if len(morning) >= 200 and len(afternoon) >= 5:
            mo = morning[0]['o']; mc = morning[-1]['c']
            mh = max(b['h'] for b in morning); ml = min(b['l'] for b in morning)
            mr = mh - ml
            morn_ret = (mc - mo) / mo * 100
            morn_bullish = mc > mo
            range_pos = (mc - ml) / mr if mr > 0 else 0.5

            cfb = afternoon[0]
            cfb_ret = (cfb['c'] - cfb['o']) / cfb['o'] * 100
            cfb_bull = cfb['c'] > cfb['o']
            cfb_br = abs(cfb['c'] - cfb['o']) / (cfb['h'] - cfb['l']) if (cfb['h'] - cfb['l']) > 0 else 0

            if len(afternoon) > 1:
                cfb2_bull = afternoon[0]['c'] > afternoon[0]['o'] and afternoon[1]['c'] > afternoon[1]['o']
                cfb2_bear = afternoon[0]['c'] < afternoon[0]['o'] and afternoon[1]['c'] < afternoon[1]['o']
                cfb2_ret = (afternoon[1]['c'] - afternoon[0]['o']) / afternoon[0]['o'] * 100

        days.append({
            'date': d, 'bars': all_bars, 'morning': morning, 'afternoon': afternoon,
            'open': dd['o'], 'close': dd['c'],
            'vix': vix_open, 'gap_pct': gap_pct,
            'prev_bullish': prev_bullish, 'prev_close_loc': prev_close_loc,
            'above_20d': above_20d, 'above_50d': above_50d, 'dow': dow,
            'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish, 'fb_body_ratio': fb_body_ratio,
            'morn_ret': morn_ret, 'morn_bullish': morn_bullish, 'range_pos': range_pos,
            'cfb_ret': cfb_ret, 'cfb_bull': cfb_bull, 'cfb_br': cfb_br,
            'cfb2_bull': cfb2_bull, 'cfb2_bear': cfb2_bear, 'cfb2_ret': cfb2_ret,
            'price_at_2pm': morning[-1]['c'] if morning else dd['o'],
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
    eb = bars[entry_idx]
    ep = eb['c']; em = eb['mins']
    pt = exit_params.get('pt_pts'); sl = exit_params.get('sl_pts')
    trail = exit_params.get('trail_pct')
    ts = exit_params.get('ts_min', 60)
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
        if not bars: return None, t
        e, x = fbar(bars, em), fbar(bars, xm)
        if not e or not x or e['c'] <= 0: return None, t
        return round((x['c']-e['c'])*100, 2), t
    def spread(cp, lk, sk):
        lt, st = btick(date, cp, lk), btick(date, cp, sk)
        lb, sb = load_opt(lt, date), load_opt(st, date)
        if not lb or not sb: return None, f"{lt}|{st}"
        le, lx = fbar(lb, em), fbar(lb, xm)
        se, sx = fbar(sb, em), fbar(sb, xm)
        if not all([le,lx,se,sx]): return None, f"{lt}|{st}"
        d = le['c']-se['c']; c = lx['c']-sx['c']
        return round((c-d)*100,2), f"{lt}|{st}"
    def credit(cp, sell_k, buy_k):
        st, lt = btick(date,cp,sell_k), btick(date,cp,buy_k)
        sb, lb = load_opt(st,date), load_opt(lt,date)
        if not sb or not lb: return None, f"{st}|{lt}"
        se,sx = fbar(sb,em), fbar(sb,xm)
        le,lx = fbar(lb,em), fbar(lb,xm)
        if not all([se,sx,le,lx]): return None, f"{st}|{lt}"
        cr = se['c']-le['c']; dc = sx['c']-lx['c']
        return round((cr-dc)*100,2), f"{st}|{lt}"

    if struct == 'long_call': return single('C', atm)
    elif struct == 'long_itm_call': return single('C', atm-5)
    elif struct == 'long_otm_call': return single('C', atm+5)
    elif struct == 'long_put': return single('P', atm)
    elif struct == 'bull_call_5': return spread('C', atm, atm+5)
    elif struct == 'bull_call_10': return spread('C', atm, atm+10)
    elif struct == 'bear_put_5': return spread('P', atm, atm-5)
    elif struct == 'credit_call_5': return credit('C', atm+5, atm+10)
    return None, None, None, None


COMMISSION_PER_CONTRACT = 0.65   # $ per contract per leg per fill

def legs_for_struct(struct):
    """Return number of option legs in the structure."""
    if struct in ('long_call', 'long_itm_call', 'long_otm_call', 'long_put'):
        return 1
    return 2   # spreads: bull_call_5, bull_call_10, bear_put_5, credit_call_5

def commission_1lot(struct):
    """Round-trip commission for 1 contract of this structure."""
    return COMMISSION_PER_CONTRACT * legs_for_struct(struct) * 2   # open + close

def price_opt_full(date, em, xm, spx, direction, struct):
    """Like price_opt but also returns (entry_px_str, exit_px_str) for sizing.
    Returned pnl INCLUDES round-trip commissions for 1 contract."""
    atm = gstrike(spx)
    comm = commission_1lot(struct)
    def single_full(cp, k):
        t = btick(date, cp, k); bars = load_opt(t, date)
        if not bars: return None, t, None, None
        e, x = fbar(bars, em), fbar(bars, xm)
        if not e or not x or e['c'] <= 0: return None, t, None, None
        return round((x['c']-e['c'])*100 - comm, 2), t, e['c'], x['c']
    def spread_full(cp, lk, sk):
        lt, st = btick(date, cp, lk), btick(date, cp, sk)
        lb, sb = load_opt(lt, date), load_opt(st, date)
        if not lb or not sb: return None, f"{lt}|{st}", None, None
        le, lx = fbar(lb, em), fbar(lb, xm)
        se, sx = fbar(sb, em), fbar(sb, xm)
        if not all([le,lx,se,sx]): return None, f"{lt}|{st}", None, None
        d = le['c']-se['c']; c = lx['c']-sx['c']
        return round((c-d)*100 - comm,2), f"{lt}|{st}", f"{le['c']}/{se['c']}", f"{lx['c']}/{sx['c']}"
    def credit_full(cp, sell_k, buy_k):
        st, lt = btick(date,cp,sell_k), btick(date,cp,buy_k)
        sb, lb = load_opt(st,date), load_opt(lt,date)
        if not sb or not lb: return None, f"{st}|{lt}", None, None
        se,sx = fbar(sb,em), fbar(sb,xm)
        le,lx = fbar(lb,em), fbar(lb,xm)
        if not all([se,sx,le,lx]): return None, f"{st}|{lt}", None, None
        cr = se['c']-le['c']; dc = sx['c']-lx['c']
        return round((cr-dc)*100 - comm,2), f"{st}|{lt}", f"{se['c']}/{le['c']}", f"{sx['c']}/{lx['c']}"

    if struct == 'long_call': return single_full('C', atm)
    elif struct == 'long_itm_call': return single_full('C', atm-5)
    elif struct == 'long_otm_call': return single_full('C', atm+5)
    elif struct == 'long_put': return single_full('P', atm)
    elif struct == 'bull_call_5': return spread_full('C', atm, atm+5)
    elif struct == 'bull_call_10': return spread_full('C', atm, atm+10)
    elif struct == 'bear_put_5': return spread_full('P', atm, atm-5)
    elif struct == 'credit_call_5': return credit_full('C', atm+5, atm+10)
    return None, None, None, None


# ═══════════════════════════════════════════════════════════════
# GRADING & POSITION SIZING ($25K–$200K risk per trade)
# ═══════════════════════════════════════════════════════════════

def compute_strat_stats(trades):
    """Compute per-strategy Sharpe and WR for grading."""
    from collections import defaultdict
    by_key = defaultdict(list)
    for t in trades:
        key = (t['strategy'], t['structure'])
        by_key[key].append(t['opt_pnl'])
    stats = {}
    for key, pnls in by_key.items():
        wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.5
        if len(pnls) >= 2:
            avg = statistics.mean(pnls)
            std = statistics.stdev(pnls)
            sharpe = avg / std if std > 0 else 0
        else:
            sharpe = 0
        stats[key] = {'sharpe': sharpe, 'wr': wr}
    return stats


def grade_and_size_trades(all_trades):
    """Add grade, contracts, risk, sized_pnl to each trade."""
    ss = compute_strat_stats(all_trades)

    all_sharpes = [v['sharpe'] for v in ss.values()]
    sharpe_min, sharpe_max = min(all_sharpes), max(all_sharpes)
    all_vix = [t['vix'] for t in all_trades if t['vix']]
    vix_min, vix_max = min(all_vix), max(all_vix)
    all_hold = [t['hold_mins'] for t in all_trades]
    hold_min, hold_max = min(all_hold), max(all_hold)

    for t in all_trades:
        key = (t['strategy'], t['structure'])
        s = ss.get(key, {'sharpe': 0, 'wr': 0.5})

        # Sharpe score (0-1) — 40% weight
        sharpe_score = (s['sharpe'] - sharpe_min) / (sharpe_max - sharpe_min) if sharpe_max > sharpe_min else 0.5
        # Win rate score (0-1) — 25% weight
        wr_score = s['wr']
        # VIX score (0-1, lower VIX = higher) — 20% weight
        vix = t.get('vix', 20)
        vix_score = 1.0 - (vix - vix_min) / (vix_max - vix_min) if vix_max > vix_min else 0.5
        # Hold efficiency (0-1, shorter = better) — 15% weight
        hold = t.get('hold_mins', 15)
        hold_score = 1.0 - (hold - hold_min) / (hold_max - hold_min) if hold_max > hold_min else 0.5

        grade = round((sharpe_score * 0.40 + wr_score * 0.25 + vix_score * 0.20 + hold_score * 0.15) * 100, 1)
        t['grade'] = grade

        # Size: map grade to risk budget $25K-$200K
        MIN_RISK, MAX_RISK = 25000, 200000
        norm = max(0, min(1, (grade - 30) / 60))
        risk_budget = MIN_RISK + norm * (MAX_RISK - MIN_RISK)

        # Per-contract risk from entry price
        entry_px = t.get('opt_entry_px', '')
        structure = t['structure']
        if structure in ('bull_call_5', 'bull_call_10', 'bear_put_5'):
            if isinstance(entry_px, str) and '/' in entry_px:
                prices = entry_px.split('/')
                net_debit = abs(float(prices[0]) - float(prices[1]))
            else:
                net_debit = float(entry_px) if entry_px else 1
            per_contract_risk = net_debit * 100
        elif structure == 'credit_call_5':
            # Credit spread: risk = width - credit received
            if isinstance(entry_px, str) and '/' in entry_px:
                prices = entry_px.split('/')
                credit = float(prices[0]) - float(prices[1])
                per_contract_risk = (500 - credit * 100)
            else:
                per_contract_risk = 500
        else:
            per_contract_risk = float(entry_px) * 100 if entry_px else 100

        if per_contract_risk <= 0:
            per_contract_risk = 100

        contracts = max(1, round(risk_budget / per_contract_risk))
        actual_risk = contracts * per_contract_risk
        # opt_pnl already includes round-trip commission for 1 contract,
        # so scaling by contracts correctly accounts for all commissions
        sized_pnl = t['opt_pnl'] * contracts

        t['contracts'] = contracts
        t['risk'] = round(actual_risk, 2)
        t['sized_pnl'] = round(sized_pnl, 2)
        t['commission'] = round(commission_1lot(t['structure']) * contracts, 2)


def make_readable_ticker(ticker_str):
    """Convert O:SPXW240115C02725000 to SPXW Jan 15 '24 $2,725 Call."""
    import re
    parts = ticker_str.split('|')
    readable = []
    months = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    for p in parts:
        p = p.strip()
        m = re.match(r'O:SPXW(\d{6})([CP])(\d{8})', p)
        if m:
            dstr, cp, sk = m.groups()
            yr, mo, dy = int(dstr[:2]), int(dstr[2:4]), int(dstr[4:6])
            strike = int(sk) / 1000
            cpname = 'Call' if cp == 'C' else 'Put'
            readable.append(f"SPXW {months[mo]} {dy} '{dstr[:2]} ${strike:,.0f} {cpname}")
        else:
            readable.append(p)
    return ' / '.join(readable)


# ═══════════════════════════════════════════════════════════════
# EDGE DEFINITIONS — THE WIDENED PORTFOLIO
# ═══════════════════════════════════════════════════════════════
OPENING_EDGES = [
    {
        'name': 'Friday Gap & Go',
        'label': 'Bull_GapCont|Fri|Std_10_5_15|long_call',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
        'color': '#33aaff',
        'signal': lambda d: d['fb_bullish'] and d['fb_ret'] >= 0.02 and 0.05 <= d['gap_pct'] <= 0.30,
        'filter': lambda d: d['dow'] == 4,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'bars',
        'note': 'ORIGINAL — Friday gap-up continuation',
    },
    {
        'name': 'Calm Open Scalp',
        'label': 'StrongBody_Bull|VeryLow|Micro_2_1.5_5|long_call',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'color': '#00d4aa',
        'signal': lambda d: d['fb_body_ratio'] > 0.80 and d['fb_bullish'] and d['fb_ret'] >= 0.03,
        'filter': lambda d: d['vix'] < 14,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'bars',
        'note': 'ORIGINAL — Strong body VLow VIX scalp',
    },
    {
        'name': 'Friday Follow-Through',
        'label': 'StrongBody_PrevBull|Fri+VIX<20|Asym_12_4_20|long_call',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
        'color': '#ff9933',
        'signal': lambda d: d['fb_body_ratio'] > 0.75 and d['fb_bullish'] and d['fb_ret'] >= 0.03 and d['prev_bullish'],
        'filter': lambda d: d['dow'] == 4 and d['vix'] < 20,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'bars',
        'note': 'WIDENED — body ratio 0.80→0.75, added VIX<20',
    },
    # OP4_Trend_VLow_w REMOVED — redundant with OP2 (identical filter minus above_20d,
    # fires on same days, produces identical P&L; double-counts rather than adds conviction)
    {
        'name': 'Multi-Day Momentum',
        'label': 'TripleBull(no_a20d)|VIX<15|Micro_4_2_8|long_call',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
        'color': '#ff66cc',
        'signal': lambda d: d['fb_body_ratio'] > 0.75 and d['fb_bullish'] and d['fb_ret'] >= 0.03 and d['prev_bullish'],
        'filter': lambda d: d['vix'] < 15,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'bars',
        'note': 'WIDENED — dropped above_20d, VIX <14→<15',
    },
]

CLOSING_EDGES = [
    {
        'name': 'PM Rally at Highs',
        'label': 'C2_BullHigh|VLow|uM_2_1_3|bull_call_5',
        'session': 'closing',
        'struct': 'bull_call_5',
        'exit': {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
        'color': '#33ffaa',
        'signal': lambda d: d['cfb2_bull'] and d['cfb2_ret'] >= 0.03 and d['range_pos'] > 0.70,
        'filter': lambda d: d['vix'] < 14,
        'entry_idx': 1,  # 2-bar signal, enter after bar 1
        'direction': 1,
        'bars_key': 'afternoon',
        'note': 'ORIGINAL — 2-bar bull at high, VLow VIX',
    },
    {
        'name': 'PM Trend Continuation',
        'label': 'CFB_Trend|VLow|M_5_2_8|long_itm_call',
        'session': 'closing',
        'struct': 'long_itm_call',
        'exit': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 8},
        'color': '#66ddff',
        'signal': lambda d: d['cfb_br'] > 0.70 and d['cfb_bull'] and d['cfb_ret'] >= 0.03 and d.get('above_20d') is True,
        'filter': lambda d: d['vix'] < 14,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'afternoon',
        'note': 'ORIGINAL — CFB trend-aligned, VLow VIX',
    },
    {
        'name': 'High VIX PM Breakout',
        'label': 'CFB_StrongBull(br>0.65)|Hi_TuWe|Av_5_2_10|bull_call_10',
        'session': 'closing',
        'struct': 'bull_call_10',
        'exit': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
        'color': '#ffaa33',
        'signal': lambda d: d['cfb_br'] > 0.65 and d['cfb_bull'] and d['cfb_ret'] >= 0.03,
        'filter': lambda d: d['vix'] >= 22 and d['dow'] in (1, 2),
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'afternoon',
        'note': 'WIDENED — body ratio 0.75→0.65',
    },
    {
        'name': 'PM Fade Below Trend',
        'label': 'CFB_BearTrend(br>0.60)|VIX<20|HTC_pure|credit_call_5',
        'session': 'closing',
        'struct': 'credit_call_5',
        'exit': {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
        'color': '#ff6666',
        'signal': lambda d: d['cfb_br'] > 0.60 and not d['cfb_bull'] and d['cfb_ret'] <= -0.03 and d.get('above_20d') is False,
        'filter': lambda d: d['vix'] < 20,
        'entry_idx': 0,
        'direction': -1,
        'bars_key': 'afternoon',
        'note': 'WIDENED — VIX <18→<20, body ratio 0.70→0.60',
    },
    {
        'name': 'Calm Day Breakdown',
        'label': 'C2_BearMorn(no_morn)|VLow|uM_1_05_2|bear_put_5',
        'session': 'closing',
        'struct': 'bear_put_5',
        'exit': {'pt_pts': 1, 'sl_pts': 0.5, 'ts_min': 2},
        'color': '#ff44aa',
        'signal': lambda d: d['cfb2_bear'] and d['cfb2_ret'] <= -0.03,
        'filter': lambda d: d['vix'] < 14,
        'entry_idx': 1,
        'direction': -1,
        'bars_key': 'afternoon',
        'note': 'WIDENED — dropped morning bearish requirement',
    },
    {
        'name': 'Afternoon Bounce Back',
        'label': 'CFB_ReversalLow|Low_TuWe|HTC_pure|bull_call_5',
        'session': 'closing',
        'struct': 'bull_call_5',
        'exit': {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
        'color': '#aaff33',
        'signal': lambda d: d['morn_ret'] <= -0.20 and d['cfb_bull'] and d['cfb_ret'] >= 0.03 and d['range_pos'] < 0.40,
        'filter': lambda d: d['vix'] < 18 and d['dow'] in (1, 2),
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'afternoon',
        'note': 'ORIGINAL — afternoon reversal after morning dip',
    },
    {
        'name': 'Quiet Morning Breakout',
        'label': 'CFB_FlatBreak(ret>=0.03)|MidHi|M_2_15_5|long_itm_call',
        'session': 'closing',
        'struct': 'long_itm_call',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'color': '#dddd33',
        'signal': lambda d: abs(d['morn_ret']) <= 0.10 and d['cfb_bull'] and d['cfb_ret'] >= 0.03 and d['cfb_br'] > 0.65,
        'filter': lambda d: 18 <= d['vix'] < 30,
        'entry_idx': 0,
        'direction': 1,
        'bars_key': 'afternoon',
        'note': 'WIDENED — return threshold 0.04→0.03',
    },
]

ALL_EDGES = OPENING_EDGES + CLOSING_EDGES


# ═══════════════════════════════════════════════════════════════
# MAIN — RUN ALL EDGES, PRICE WITH OPTIONS, BUILD CALENDAR
# ═══════════════════════════════════════════════════════════════
def main():
    print("="*120)
    print("61 — Widened Portfolio: Opening + Closing Print Edges")
    print("="*120)

    data = load_all_data()
    days = extract_features(*data)

    all_trades = []
    portfolio = []

    for edge in ALL_EDGES:
        print(f"\n  {edge['name']} — {edge['label']}")
        sig_fn = edge['signal']
        flt_fn = edge['filter']
        exit_params = dict(edge['exit'])
        struct = edge['struct']
        entry_idx = edge['entry_idx']
        direction = edge['direction']
        bars_key = edge['bars_key']

        priced = []; missed = 0; trades_for_edge = []

        for day in days:
            if not flt_fn(day): continue
            if not sig_fn(day): continue
            bars = day[bars_key]
            if not bars or entry_idx >= len(bars) - 3: continue

            epp = dict(exit_params)
            if epp.get('vix_mult'): epp['_vix'] = day['vix']
            t = simulate_trade(bars, entry_idx, direction, epp)
            t['date'] = day['date']; t['vix'] = day['vix']

            # Price with options (full version returns entry/exit prices)
            pnl, ticker, epx, xpx = price_opt_full(day['date'], t['entry_mins'], t['exit_mins'],
                                                     t['entry_price'], direction, struct)
            if pnl is not None:
                priced.append(pnl)
                trade_rec = {
                    'date': day['date'],
                    'strategy': edge['name'],
                    'label': edge['label'],
                    'session': edge['session'],
                    'structure': struct,
                    'direction': 'LONG' if direction == 1 else 'SHORT',
                    'spx_entry': t['entry_price'],
                    'spx_exit': t['exit_price'],
                    'entry_time': t['entry_time'],
                    'exit_time': t['exit_time'],
                    'hold_mins': t['hold_mins'],
                    'exit_reason': t['exit_reason'],
                    'und_pts': t['und_pts'],
                    'vix': round(day['vix'], 1),
                    'opt_pnl': pnl,
                    'opt_ticker': ticker if ticker else '',
                    'opt_entry_px': str(epx) if epx is not None else '',
                    'opt_exit_px': str(xpx) if xpx is not None else '',
                    'color': edge['color'],
                }
                trades_for_edge.append(trade_rec)
                all_trades.append(trade_rec)
            else:
                missed += 1

        # Stats
        n = len(priced); cov = n / (n + missed) * 100 if (n + missed) > 0 else 0
        if n >= 3:
            avg = statistics.mean(priced); tot = sum(priced)
            wr = sum(1 for p in priced if p > 0) / n * 100
            std = statistics.stdev(priced) if n > 1 else 0
            sh = avg / std if std > 0 else 0
            gw = sum(p for p in priced if p > 0)
            gl = abs(sum(p for p in priced if p <= 0))
            pf = gw / gl if gl > 0 else 99

            # IS/OOS
            is_p = [t['opt_pnl'] for t in trades_for_edge if t['date'] < '2023-01-01']
            oos_p = [t['opt_pnl'] for t in trades_for_edge if t['date'] >= '2023-01-01']
            is_sh = oos_sh = 0
            if len(is_p) >= 3:
                m = statistics.mean(is_p); s = statistics.stdev(is_p) if len(is_p)>1 else 0
                is_sh = m/s if s > 0 else 0
            if len(oos_p) >= 3:
                m = statistics.mean(oos_p); s = statistics.stdev(oos_p) if len(oos_p)>1 else 0
                oos_sh = m/s if s > 0 else 0
        else:
            avg = tot = wr = sh = pf = is_sh = oos_sh = 0

        print(f"    N={n}, missed={missed}, cov={cov:.0f}%, WR={wr:.1f}%, "
              f"Avg=${avg:+.2f}, Sh={sh:.3f}, PF={pf:.2f}, IS={is_sh:.3f}, OOS={oos_sh:.3f}")

        portfolio.append({
            'name': edge['name'],
            'label': edge['label'],
            'session': edge['session'],
            'struct': struct,
            'note': edge['note'],
            'color': edge['color'],
            'n': n, 'missed': missed, 'cov_pct': round(cov, 1),
            'wr': round(wr, 1), 'avg': round(avg, 2), 'tot': round(tot, 2),
            'sh': round(sh, 3), 'pf': round(pf, 2),
            'is_sh': round(is_sh, 3), 'oos_sh': round(oos_sh, 3),
            'n_is': len([t for t in trades_for_edge if t['date'] < '2023-01-01']),
            'n_oos': len([t for t in trades_for_edge if t['date'] >= '2023-01-01']),
        })

    # Sort trades by date
    all_trades.sort(key=lambda x: (x['date'], x['entry_time']))

    # ═══════════════════════════════════════════════════════════════
    # GRADING & POSITION SIZING
    # ═══════════════════════════════════════════════════════════════
    grade_and_size_trades(all_trades)

    # Add readable ticker
    for t in all_trades:
        t['readable_ticker'] = make_readable_ticker(t['opt_ticker'])

    # ═══════════════════════════════════════════════════════════════
    # SAVE JSON
    # ═══════════════════════════════════════════════════════════════
    with open(OUT_DIR / 'widened_portfolio.json', 'w') as f:
        json.dump(portfolio, f, indent=2)
    with open(OUT_DIR / 'widened_portfolio_trades.json', 'w') as f:
        json.dump(all_trades, f, indent=2)

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    unique_dates = len(set(t['date'] for t in all_trades))
    op_trades = [t for t in all_trades if t['session'] == 'opening']
    cl_trades = [t for t in all_trades if t['session'] == 'closing']

    print(f"\n{'='*120}")
    print(f"WIDENED PORTFOLIO SUMMARY")
    print(f"{'='*120}")
    print(f"  Total trades: {len(all_trades)}")
    print(f"  Unique dates: {unique_dates}")
    print(f"  Opening trades: {len(op_trades)}")
    print(f"  Closing trades: {len(cl_trades)}")
    print(f"  Date range: {all_trades[0]['date']} to {all_trades[-1]['date']}")
    print()

    hdr = f"  {'Name':<30s} {'Sess':>5} {'N':>4} {'WR':>5} {'Avg$':>8} {'Sh':>6} {'PF':>5} {'IS':>5} {'OOS':>5} {'Note'}"
    print(hdr)
    print("  " + "-"*120)
    for e in portfolio:
        print(f"  {e['name']:<30s} {e['session']:>5} {e['n']:>4} {e['wr']:>4.1f}% ${e['avg']:>+6.2f} "
              f"{e['sh']:>6.3f} {e['pf']:>5.2f} {e['is_sh']:>5.3f} {e['oos_sh']:>5.3f} {e['note']}")

    # ═══════════════════════════════════════════════════════════════
    # GENERATE CALENDAR HTML
    # ═══════════════════════════════════════════════════════════════
    print(f"\nGenerating calendar HTML…")
    generate_calendar_html(all_trades, portfolio, data[0])

    print(f"\n  Saved: backtest_results/widened_portfolio.json")
    print(f"  Saved: backtest_results/widened_portfolio_trades.json")
    print(f"  Saved: widened_trade_calendar.html")
    print(f"\nDONE")


def generate_calendar_html(all_trades, portfolio, spx_1min):
    """Generate interactive trade calendar with SPX + option candlestick charts."""

    # ── Collect SPX bars for trade dates ──
    trade_dates = sorted(set(t['date'] for t in all_trades))
    spx_bars_dict = {}
    for d in trade_dates:
        if d in spx_1min:
            spx_bars_dict[d] = [
                {'time': f"{b['mins']//60:02d}:{b['mins']%60:02d}",
                 'open': round(b['o'], 2), 'high': round(b['h'], 2),
                 'low': round(b['l'], 2), 'close': round(b['c'], 2)}
                for b in spx_1min[d]
            ]

    # ── Collect option bars for each trade's primary ticker ──
    opt_bars_dict = {}
    for t in all_trades:
        tickers = t['opt_ticker'].split('|')
        for tk in tickers:
            tk = tk.strip()
            if not tk:
                continue
            bar_key = f"{tk}|{t['date']}"
            if bar_key in opt_bars_dict:
                continue
            tk_clean = tk.replace(':', '_')
            fname = CACHE_DIR / f"{tk_clean}_{t['date']}.json"
            if fname.exists():
                with open(fname) as f:
                    raw = json.load(f)
                if raw:
                    bars = []
                    for b in raw:
                        ts = b.get('t', 0)
                        if isinstance(ts, (int, float)):
                            dt_obj = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                            offset_h = 5 if dt_obj.month >= 11 or dt_obj.month <= 2 else 4
                            et = dt_obj.replace(tzinfo=None) - timedelta(hours=offset_h)
                            time_str = et.strftime('%H:%M')
                        else:
                            time_str = str(ts)
                        bars.append({
                            't': time_str,
                            'o': b.get('o', 0), 'h': b.get('h', 0),
                            'l': b.get('l', 0), 'c': b.get('c', 0)
                        })
                    if bars:
                        opt_bars_dict[bar_key] = bars

    trades_json = json.dumps(all_trades)
    portfolio_json = json.dumps(portfolio)
    spx_bars_json = json.dumps(spx_bars_dict)
    opt_bars_json = json.dumps(opt_bars_dict)

    unique_dates = sorted(set(t['date'] for t in all_trades))
    total_n = len(all_trades)

    # Use single braces in raw JS by doubling them in the f-string
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SPX 0DTE — Widened Portfolio Calendar</title>
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; }}
.header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 24px 32px; border-bottom: 1px solid #2a2a4a;
}}
.header h1 {{ font-size: 22px; font-weight: 600; color: #fff; margin-bottom: 4px; }}
.header .subtitle {{ color: #8888aa; font-size: 13px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.badge {{
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 700;
}}
.badge-open {{ background: #0a2a1a; color: #00d4aa; border: 1px solid #1a5a3a; }}
.badge-close {{ background: #1a0a2a; color: #aa66ff; border: 1px solid #3a1a5a; }}
.refresh-wrap {{ position: absolute; top: 18px; right: 32px; text-align: right; }}
.refresh-btn {{
    background: #1a1a2e; border: 1px solid #3a3a5a; color: #ccc; padding: 10px 20px;
    border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600;
    transition: all 0.2s; display: flex; align-items: center; gap: 8px;
}}
.refresh-btn:hover {{ background: #2a2a4e; border-color: #5a5a8a; color: #fff; }}
.refresh-btn.running {{ color: #ffaa33; cursor: wait; }}
.refresh-btn.done {{ color: #00d4aa; border-color: #00d4aa; }}
.refresh-status {{ font-size: 11px; color: #666; margin-top: 4px; }}
.refresh-status.ok {{ color: #00d4aa; }}
.refresh-status.err {{ color: #ff4466; }}
.header {{ position: relative; }}
.tabs {{
    display: flex; flex-wrap: wrap; gap: 0; padding: 0 32px; background: #111118; border-bottom: 1px solid #1a1a2a;
}}
.tab {{
    padding: 10px 18px; font-size: 12px; font-weight: 600; cursor: pointer;
    border-bottom: 2px solid transparent; color: #666; transition: all 0.2s;
}}
.tab:hover {{ color: #aaa; }}
.tab.active {{ color: #fff; border-color: #00d4aa; }}
.stats-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 32px;
    background: #111118; border-bottom: 1px solid #1a1a2a;
}}
.stat {{ background: #1a1a28; border-radius: 6px; padding: 10px 16px; min-width: 90px; flex: 1; border: 1px solid #2a2a3a; }}
.stat .lbl {{ font-size: 10px; color: #6666aa; text-transform: uppercase; letter-spacing: 1px; }}
.stat .val {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}
.g {{ color: #00d4aa; }} .r {{ color: #ff4466; }}
.main {{ padding: 16px 32px; }}
.month-nav {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }}
.month-nav button {{
    background: #2a2a3e; border: 1px solid #3a3a5a; color: #ccc;
    padding: 6px 14px; border-radius: 5px; cursor: pointer; font-size: 13px;
}}
.month-nav button:hover {{ background: #3a3a5e; }}
.month-nav .mt {{ font-size: 18px; font-weight: 600; }}
.cal {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 3px; }}
.ch {{ text-align: center; font-size: 11px; color: #6666aa; padding: 6px 0; font-weight: 600; }}
.cd {{
    border-radius: 6px; padding: 5px; font-size: 11px;
    cursor: default; display: flex; flex-direction: column;
    min-height: 85px; border: 1px solid transparent; transition: all 0.15s;
}}
.cd.empty {{ background: transparent; }}
.cd.nt {{ background: #111118; color: #333; }}
.cd.win {{ background: linear-gradient(135deg, #0a2a1a 0%, #0d3320 100%); border-color: #1a5a3a; cursor: pointer; }}
.cd.loss {{ background: linear-gradient(135deg, #2a0a0f 0%, #331015 100%); border-color: #5a1a2a; cursor: pointer; }}
.cd.win:hover {{ border-color: #00d4aa; }}
.cd.loss:hover {{ border-color: #ff4466; }}
.cd.selected {{ border-color: #5577ff !important; box-shadow: 0 0 10px rgba(85,119,255,0.3); }}
.cd .dn {{ font-weight: 600; font-size: 12px; }}
.cd .dp {{ font-size: 10px; font-weight: 700; margin-top: auto; }}
.cd .dots {{ display: flex; gap: 2px; margin-top: 2px; flex-wrap: wrap; }}
.cd .dot {{ padding: 1px 4px; border-radius: 6px; font-size: 7px; font-weight: 700; }}
.eq {{ margin-bottom: 16px; }}
.eq h3 {{ font-size: 14px; color: #aaa; margin-bottom: 6px; }}
.eq canvas {{ width: 100%; height: 180px; display: block; background: #131722; border-radius: 8px; border: 1px solid #2a2e3d; }}
.detail {{
    background: #13131f; border-radius: 10px;
    border: 1px solid #2a2a3a; padding: 20px; margin-top: 16px;
    display: none;
}}
.detail.vis {{ display: block; }}
.trade-section {{
    padding: 16px; border-radius: 8px; margin-bottom: 12px;
    border: 1px solid #3a2a2a; background: #1a0f0f;
}}
.trade-section-header {{
    font-size: 14px; font-weight: 700; margin-bottom: 10px;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}}
.strat-label {{ padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; }}
.trade-info-details {{ display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; }}
.trade-info-details .di {{ font-size: 12px; white-space: nowrap; }}
.trade-info-details .di .dlabel {{ color: #6666aa; margin-right: 4px; }}
.trade-info-details .di .dvalue {{ font-weight: 600; }}
.grade-badge {{
    display: inline-flex; align-items: center; gap: 3px;
    padding: 2px 8px; border-radius: 8px; font-size: 10px; font-weight: 700;
}}
.grade-a {{ background: #0a3a1a; color: #00ff88; border: 1px solid #00ff8844; }}
.grade-b {{ background: #0a2a1a; color: #00d4aa; border: 1px solid #00d4aa44; }}
.grade-c {{ background: #2a2a0a; color: #dddd33; border: 1px solid #dddd3344; }}
.grade-d {{ background: #2a1a0a; color: #ff9933; border: 1px solid #ff993344; }}
.chart-wrap {{
    margin-top: 10px; border-radius: 8px; overflow: hidden;
    border: 1px solid #2a2e3d; background: #131722;
}}
.chart-wrap .ch-header {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; background: #1e2130; border-bottom: 1px solid #2a2e3d; font-size: 12px;
}}
.chart-wrap .ch-header .ch-title {{ font-weight: 600; font-size: 13px; }}
.chart-wrap .ch-header .ch-sub {{ color: #888; margin-left: 10px; }}
</style>
</head>
<body>

<div class="header">
    <h1>SPX 0DTE — Widened Portfolio Calendar</h1>
    <div class="subtitle">
        <span class="badge badge-open">OPENING 5 edges</span>
        <span class="badge badge-close">CLOSING 7 edges</span>
        <span id="tradeCount">{total_n} trades &mdash; {len(unique_dates)} dates &mdash; {unique_dates[0]} to {unique_dates[-1]}</span>
    </div>
    <div class="refresh-wrap">
        <button class="refresh-btn" id="refreshBtn" onclick="refreshCalendar()">&#8635; Refresh Data</button>
        <div class="refresh-status" id="refreshStatus"></div>
    </div>
</div>

<div class="tabs" id="tabBar"></div>
<div class="stats-bar" id="statsBar"></div>

<div class="main">
    <div class="eq">
        <h3>Cumulative Sized P&amp;L ($25K&ndash;$200K risk per trade)</h3>
        <canvas id="eqCanvas"></canvas>
    </div>
    <div class="month-nav">
        <button onclick="prevMonth()">&larr; Prev</button>
        <span class="mt" id="monthTitle"></span>
        <button onclick="nextMonth()">Next &rarr;</button>
    </div>
    <div class="cal" id="calGrid"></div>
    <div class="detail" id="detailPanel">
        <div id="detailContent"></div>
    </div>
</div>

<script>
var allTrades = {trades_json};
var portfolio = {portfolio_json};
var spxBars = {spx_bars_json};
var optBars = {opt_bars_json};

var activeFilter = 'all';
var currentYear, currentMonth;
var charts = [];

function fmtPnl(v) {{
    var sign = v >= 0 ? '+$' : '-$';
    return sign + Math.abs(Math.round(v)).toLocaleString();
}}
function fmtRisk(v) {{
    if (v >= 1000) return '$' + (v/1000).toFixed(0) + 'K';
    return '$' + v.toLocaleString();
}}
function gradeClass(g) {{
    if (g >= 75) return 'grade-a'; if (g >= 60) return 'grade-b';
    if (g >= 45) return 'grade-c'; return 'grade-d';
}}
function gradeLabel(g) {{
    if (g >= 75) return 'A'; if (g >= 60) return 'B'; if (g >= 45) return 'C'; return 'D';
}}

function init() {{
    buildTabs();
    var dates = allTrades.map(function(t){{ return t.date; }}).sort();
    if (dates.length) {{
        var last = dates[dates.length-1];
        currentYear = parseInt(last.substring(0,4));
        currentMonth = parseInt(last.substring(5,7)) - 1;
    }} else {{
        currentYear = 2025; currentMonth = 0;
    }}
    updateAll();
}}

function buildTabs() {{
    var html = '<div class="tab active" onclick="setFilter(\\'all\\')">All</div>';
    html += '<div class="tab" onclick="setFilter(\\'opening\\')">Opening</div>';
    html += '<div class="tab" onclick="setFilter(\\'closing\\')">Closing</div>';
    portfolio.forEach(function(e) {{
        html += '<div class="tab" onclick="setFilter(\\'' + e.name + '\\')" style="border-left: 3px solid ' + e.color + '">' + e.name + '</div>';
    }});
    document.getElementById('tabBar').innerHTML = html;
}}

function setFilter(f) {{
    activeFilter = f;
    var tabs = document.querySelectorAll('.tab');
    tabs.forEach(function(t) {{ t.classList.remove('active'); }});
    tabs.forEach(function(t) {{
        if ((f === 'all' && t.textContent === 'All') ||
            (f === 'opening' && t.textContent === 'Opening') ||
            (f === 'closing' && t.textContent === 'Closing') ||
            t.textContent === f) {{
            t.classList.add('active');
        }}
    }});
    updateAll();
}}

function getFiltered() {{
    if (activeFilter === 'all') return allTrades;
    if (activeFilter === 'opening') return allTrades.filter(function(t){{ return t.session === 'opening'; }});
    if (activeFilter === 'closing') return allTrades.filter(function(t){{ return t.session === 'closing'; }});
    return allTrades.filter(function(t){{ return t.strategy === activeFilter; }});
}}

function updateAll() {{
    var ft = getFiltered();
    updateStats(ft);
    drawEquity(ft);
    drawCalendar(ft);
    document.getElementById('detailPanel').className = 'detail';
}}

function updateStats(trades) {{
    var n = trades.length;
    var pnls = trades.map(function(t){{ return t.sized_pnl || 0; }});
    var tot = pnls.reduce(function(a,b){{ return a+b; }}, 0);
    var wins = pnls.filter(function(p){{ return p > 0; }});
    var losses = pnls.filter(function(p){{ return p <= 0; }});
    var wr = n > 0 ? (wins.length/n*100).toFixed(1) : '0.0';
    var avgW = wins.length ? Math.round(wins.reduce(function(a,b){{return a+b;}},0)/wins.length) : 0;
    var avgL = losses.length ? Math.round(losses.reduce(function(a,b){{return a+b;}},0)/losses.length) : 0;
    var avgRisk = n > 0 ? Math.round(trades.reduce(function(a,t){{return a+(t.risk||0);}},0)/n) : 0;
    var best = pnls.length ? Math.max.apply(null, pnls) : 0;
    var worst = pnls.length ? Math.min.apply(null, pnls) : 0;
    var cumul = 0, peak = 0, dd = 0;
    for (var i = 0; i < pnls.length; i++) {{
        cumul += pnls[i]; if (cumul > peak) peak = cumul; if (cumul - peak < dd) dd = cumul - peak;
    }}
    var items = [
        ['Sized P&L', fmtPnl(tot), tot >= 0 ? 'g' : 'r'],
        ['Win Rate', wr + '%', parseFloat(wr)>=50 ? 'g' : 'r'],
        ['Trades', n, ''],
        ['Avg Risk', fmtRisk(avgRisk), ''],
        ['Avg Win', fmtPnl(avgW), 'g'],
        ['Avg Loss', fmtPnl(avgL), 'r'],
        ['Best', fmtPnl(best), 'g'],
        ['Max DD', fmtPnl(dd), 'r']
    ];
    var h = '';
    for (var j = 0; j < items.length; j++) {{
        h += '<div class="stat"><div class="lbl">' + items[j][0] + '</div><div class="val ' + items[j][2] + '">' + items[j][1] + '</div></div>';
    }}
    document.getElementById('statsBar').innerHTML = h;
}}

function drawEquity(trades) {{
    var canvas = document.getElementById('eqCanvas');
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.parentElement.clientWidth; var h = 180;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#131722'; ctx.fillRect(0, 0, w, h);
    if (!trades.length) return;
    var sorted = trades.slice().sort(function(a,b){{ return a.date.localeCompare(b.date); }});
    var cum = [0]; var c2 = 0;
    sorted.forEach(function(t) {{ c2 += (t.sized_pnl || 0); cum.push(c2); }});
    var mn = Math.min.apply(null, cum); var mx = Math.max.apply(null, cum);
    var pad = 16; var gw = w - 2*pad; var gh = h - 2*pad;
    var range = mx - mn || 1;
    // IS/OOS divider
    var isEnd = -1;
    for (var i = 0; i < sorted.length; i++) {{ if (sorted[i].date >= '2023-01-01') {{ isEnd = i; break; }} }}
    if (isEnd > 0) {{
        var dx = pad + (isEnd / (cum.length-1)) * gw;
        ctx.strokeStyle = '#4444aa'; ctx.lineWidth = 1; ctx.setLineDash([6,3]);
        ctx.beginPath(); ctx.moveTo(dx, pad); ctx.lineTo(dx, h-pad); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = '#4444aa'; ctx.font = '10px sans-serif';
        ctx.fillText('IS', dx - 20, pad + 12); ctx.fillText('OOS', dx + 5, pad + 12);
    }}
    // Zero line
    if (mn < 0 && mx > 0) {{
        var zy = pad + gh - ((0 - mn) / range) * gh;
        ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
        ctx.beginPath(); ctx.moveTo(pad, zy); ctx.lineTo(w-pad, zy); ctx.stroke();
    }}
    ctx.beginPath(); ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 2;
    for (var i = 0; i < cum.length; i++) {{
        var x = pad + (i / (cum.length-1)) * gw;
        var y = pad + gh - ((cum[i] - mn) / range) * gh;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }}
    ctx.stroke();
    ctx.lineTo(pad + gw, pad + gh); ctx.lineTo(pad, pad + gh);
    ctx.closePath(); ctx.fillStyle = 'rgba(0,212,170,0.08)'; ctx.fill();
    ctx.fillStyle = '#00d4aa'; ctx.font = 'bold 12px sans-serif';
    ctx.fillText(fmtPnl(c2), w - pad - 80, Math.max(pad + gh - ((c2 - mn) / range) * gh - 8, 18));
}}

function drawCalendar(trades) {{
    var monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    document.getElementById('monthTitle').textContent = monthNames[currentMonth] + ' ' + currentYear;
    var byDate = {{}};
    trades.forEach(function(t) {{ if (!byDate[t.date]) byDate[t.date] = []; byDate[t.date].push(t); }});
    var firstDay = new Date(currentYear, currentMonth, 1);
    var lastDay = new Date(currentYear, currentMonth + 1, 0);
    var fd = firstDay.getDay(); fd = fd === 0 ? 6 : fd - 1;
    var html = '<div class="ch">Mon</div><div class="ch">Tue</div><div class="ch">Wed</div><div class="ch">Thu</div><div class="ch">Fri</div>';
    var calDays = [];
    for (var dd = 1; dd <= lastDay.getDate(); dd++) {{
        var dt = new Date(currentYear, currentMonth, dd);
        var dow = dt.getDay();
        if (dow === 0 || dow === 6) continue;
        var iso = currentYear + '-' + String(currentMonth+1).padStart(2,'0') + '-' + String(dd).padStart(2,'0');
        calDays.push({{ num: dd, date: iso, dow: dow === 0 ? 6 : dow - 1 }});
    }}
    if (calDays.length > 0) {{
        for (var e = 0; e < calDays[0].dow; e++) html += '<div class="cd empty"></div>';
    }}
    var prevDow = calDays.length > 0 ? calDays[0].dow : 0;
    calDays.forEach(function(cd, idx) {{
        if (idx > 0) {{
            var gap = cd.dow - prevDow - 1; if (gap < 0) gap += 5;
            for (var g = 0; g < gap; g++) html += '<div class="cd empty"></div>';
        }}
        prevDow = cd.dow;
        var dayTrades = byDate[cd.date] || [];
        if (dayTrades.length === 0) {{
            html += '<div class="cd nt"><span class="dn">' + cd.num + '</span></div>';
        }} else {{
            var pnl = dayTrades.reduce(function(a,t){{ return a + (t.sized_pnl || 0); }}, 0);
            var cls = pnl >= 0 ? 'win' : 'loss'; var pcls = pnl >= 0 ? 'g' : 'r';
            html += '<div class="cd ' + cls + '" onclick="showTrade(\\'' + cd.date + '\\')">';
            html += '<span class="dn">' + cd.num + '</span>';
            html += '<div class="dots">';
            dayTrades.forEach(function(t) {{
                html += '<span class="dot" style="background:' + t.color + ';color:#000">' + (t.session === 'opening' ? 'O' : 'C') + '</span>';
            }});
            html += '</div>';
            html += '<span class="dp ' + pcls + '">' + fmtPnl(pnl) + '</span>';
            html += '</div>';
        }}
    }});
    document.getElementById('calGrid').innerHTML = html;
}}

function showTrade(dateStr) {{
    // Highlight selected day
    var allDays = document.querySelectorAll('.cd');
    for (var i = 0; i < allDays.length; i++) allDays[i].classList.remove('selected');
    // find the clicked day
    var calDays = document.querySelectorAll('.cd');
    calDays.forEach(function(d) {{ if (d.onclick && d.getAttribute('onclick') && d.getAttribute('onclick').indexOf(dateStr) >= 0) d.classList.add('selected'); }});

    var ft = getFiltered();
    var dayTrades = ft.filter(function(t) {{ return t.date === dateStr; }});
    if (!dayTrades.length) return;

    var panel = document.getElementById('detailPanel');
    panel.classList.add('vis');
    panel.style.display = 'block';

    var totalPnl = dayTrades.reduce(function(s, t) {{ return s + (t.sized_pnl || 0); }}, 0);
    var pnlCls = totalPnl >= 0 ? 'g' : 'r';

    var html = '<div style="font-size:15px;font-weight:600;color:#fff;margin-bottom:4px">' + dateStr + '</div>';
    if (dayTrades.length > 1) {{
        html += '<div class="' + pnlCls + '" style="font-size:22px;font-weight:800;margin:2px 0 12px">' + fmtPnl(totalPnl) + ' combined (' + dayTrades.length + ' trades)</div>';
    }}

    // SPX intraday chart
    var spxData = spxBars[dateStr];
    if (spxData && spxData.length) {{
        html += '<div class="chart-wrap" style="margin-bottom:12px">';
        html += '<div class="ch-header"><span class="ch-title">SPX Intraday</span><span class="ch-sub">1-min bars</span></div>';
        html += '<div id="spxLegend_0" style="padding:6px 12px;font-family:monospace;font-size:11px;color:#aaa;background:#131722;min-height:22px"></div>';
        html += '<div id="spxChart_0" style="height:500px"></div>';
        html += '</div>';
    }}

    var chartDivs = [];
    var chartIdx = 0;

    // Group by strategy
    var byStrat = {{}};
    dayTrades.forEach(function(t) {{ if (!byStrat[t.strategy]) byStrat[t.strategy] = []; byStrat[t.strategy].push(t); }});

    var stratKeys = Object.keys(byStrat);
    for (var sk = 0; sk < stratKeys.length; sk++) {{
        var strat = stratKeys[sk];
        var sts = byStrat[strat];
        var stratPnl = sts.reduce(function(s, t) {{ return s + (t.sized_pnl || 0); }}, 0);
        var sPnlCls = stratPnl >= 0 ? 'g' : 'r';
        var sColor = sts[0].color || '#999';

        html += '<div class="trade-section">';
        html += '<div class="trade-section-header">';
        html += '<span class="strat-label" style="background:' + sColor + '22;color:' + sColor + '">' + strat + '</span>';
        html += '<span class="' + sPnlCls + '" style="font-size:20px;font-weight:800">' + fmtPnl(stratPnl) + '</span>';
        html += '</div>';

        for (var si = 0; si < sts.length; si++) {{
            var t = sts[si];
            var tPnlCls = (t.sized_pnl || 0) >= 0 ? 'g' : 'r';
            var holdStr = t.hold_mins >= 60 ? Math.floor(t.hold_mins/60) + 'h ' + (t.hold_mins%60) + 'm' : t.hold_mins + ' min';
            var gc = gradeClass(t.grade || 0);
            var gl = gradeLabel(t.grade || 0);

            html += '<div style="padding:10px 0;border-top:1px solid #2a2a3a">';
            html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">';
            html += '<span style="font-size:13px;font-weight:700;color:' + sColor + '">' + t.structure.toUpperCase().replace(/_/g,' ') + '</span>';
            html += '<span class="' + tPnlCls + '" style="font-weight:700;font-size:15px">' + fmtPnl(t.sized_pnl || 0) + '</span>';
            html += '<span class="grade-badge ' + gc + '">Grade ' + gl + ' (' + (t.grade || 0).toFixed(0) + ')</span>';
            html += '<span style="font-size:11px;color:#888">' + (t.contracts || 1) + ' contracts @ ' + fmtRisk(t.risk || 0) + ' risk</span>';
            html += '</div>';

            html += '<div style="font-size:12px;color:#ccc;margin-bottom:6px">' + (t.readable_ticker || t.opt_ticker) + '</div>';

            html += '<div class="trade-info-details">';
            var ep = t.opt_entry_px || ''; var xp = t.opt_exit_px || '';
            if (typeof ep === 'string' && ep.indexOf('/') >= 0) {{
                html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">$' + ep + '</span></span>';
                html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">$' + xp + '</span></span>';
            }} else {{
                html += '<span class="di"><span class="dlabel">Entry</span><span class="dvalue">$' + (ep ? Number(ep).toFixed(2) : '?') + '</span></span>';
                html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">$' + (xp ? Number(xp).toFixed(2) : '?') + '</span></span>';
            }}
            html += '<span class="di"><span class="dlabel">SPX</span><span class="dvalue">' + t.spx_entry.toFixed(1) + ' \\u2192 ' + t.spx_exit.toFixed(1) + '</span></span>';
            html += '<span class="di"><span class="dlabel">Hold</span><span class="dvalue">' + holdStr + '</span></span>';
            html += '<span class="di"><span class="dlabel">Exit</span><span class="dvalue">' + t.exit_reason + '</span></span>';
            html += '<span class="di"><span class="dlabel">VIX</span><span class="dvalue">' + t.vix + '</span></span>';
            html += '<span class="di"><span class="dlabel">1-lot P&L</span><span class="dvalue">$' + t.opt_pnl.toFixed(2) + '</span></span>';
            html += '</div>';

            // Option chart
            var primaryTicker = t.opt_ticker.split('|')[0].trim();
            var barKey = primaryTicker + '|' + t.date;
            if (optBars[barKey]) {{
                var cid = 'optChart_' + chartIdx;
                var lid = 'optLegend_' + chartIdx;
                html += '<div class="chart-wrap" style="margin-top:8px">';
                html += '<div class="ch-header"><span class="ch-title">' + (t.readable_ticker || primaryTicker).split(' / ')[0] + '</span><span class="ch-sub">1-min option bars</span></div>';
                html += '<div id="' + lid + '" style="padding:6px 12px;font-family:monospace;font-size:11px;color:#aaa;background:#131722;min-height:22px"></div>';
                html += '<div id="' + cid + '" style="height:500px"></div>';
                html += '</div>';
                chartDivs.push({{id: cid, legendId: lid, barKey: barKey, entry_time: t.entry_time, exit_time: t.exit_time, entry_px: t.opt_entry_px, exit_px: t.opt_exit_px, pnl: t.sized_pnl || 0}});
                chartIdx++;
            }}
            html += '</div>';
        }}
        html += '</div>';
    }}

    document.getElementById('detailContent').innerHTML = html;

    // ── Destroy old charts ──
    for (var ci = 0; ci < charts.length; ci++) {{
        try {{ charts[ci].remove(); }} catch(e) {{}}
    }}
    charts = [];

    // ── Render SPX chart ──
    if (spxData && spxData.length) {{
        var spxContainer = document.getElementById('spxChart_0');
        if (spxContainer) {{
            var spxChart = LightweightCharts.createChart(spxContainer, {{
                width: spxContainer.clientWidth, height: 500,
                layout: {{ background: {{ type: 'solid', color: '#131722' }}, textColor: '#999', fontSize: 11 }},
                grid: {{ vertLines: {{ color: '#1e222d' }}, horzLines: {{ color: '#1e222d' }} }},
                crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
                timeScale: {{ timeVisible: true, secondsVisible: false, borderColor: '#2a2e3d' }},
                rightPriceScale: {{ borderColor: '#2a2e3d' }}
            }});
            var spxSeries = spxChart.addCandlestickSeries({{
                upColor: '#26a69a', downColor: '#ef5350',
                borderUpColor: '#26a69a', borderDownColor: '#ef5350',
                wickUpColor: '#26a69a', wickDownColor: '#ef5350'
            }});
            var spxChartData = spxData.map(function(b) {{
                var ts = Math.floor(new Date(dateStr + 'T' + b.time + ':00').getTime() / 1000);
                return {{ time: ts, open: b.open, high: b.high, low: b.low, close: b.close }};
            }});
            spxSeries.setData(spxChartData);

            // Entry/exit markers from all trades
            var markers = [];
            dayTrades.forEach(function(t) {{
                var ets = Math.floor(new Date(dateStr + 'T' + t.entry_time + ':00').getTime() / 1000);
                markers.push({{ time: ets, position: 'belowBar', color: t.color || '#2196F3', shape: 'arrowUp', text: t.strategy.split('_')[0] + ' IN' }});
                var xts = Math.floor(new Date(dateStr + 'T' + t.exit_time + ':00').getTime() / 1000);
                markers.push({{ time: xts, position: 'aboveBar', color: (t.sized_pnl||0) >= 0 ? '#00d4aa' : '#ff4466', shape: 'arrowDown', text: fmtPnl(t.sized_pnl||0) }});
            }});
            markers.sort(function(a,b) {{ return a.time - b.time; }});
            spxSeries.setMarkers(markers);
            spxChart.timeScale().fitContent();
            charts.push(spxChart);

            // SPX legend
            (function(ch, series, data, legId) {{
                var leg = document.getElementById(legId);
                if (!leg) return;
                function showBar(d) {{
                    var col = d.close >= d.open ? '#26a69a' : '#ef5350';
                    leg.innerHTML = '<span style="color:#666">O</span> <span style="color:'+col+'">'+d.open.toFixed(2)+'</span> '
                        + '<span style="color:#666">H</span> <span style="color:'+col+'">'+d.high.toFixed(2)+'</span> '
                        + '<span style="color:#666">L</span> <span style="color:'+col+'">'+d.low.toFixed(2)+'</span> '
                        + '<span style="color:#666">C</span> <span style="color:'+col+';font-weight:700">'+d.close.toFixed(2)+'</span>';
                }}
                if (data.length) showBar(data[data.length-1]);
                ch.subscribeCrosshairMove(function(param) {{
                    if (!param || !param.time) {{ if (data.length) showBar(data[data.length-1]); return; }}
                    var d = param.seriesData.get(series);
                    if (d) showBar(d);
                }});
            }})(spxChart, spxSeries, spxChartData, 'spxLegend_0');
        }}
    }}

    // ── Render option charts ──
    for (var di = 0; di < chartDivs.length; di++) {{
        var cfg = chartDivs[di];
        var container = document.getElementById(cfg.id);
        if (!container) continue;
        var bars = optBars[cfg.barKey];
        if (!bars || !bars.length) continue;

        var chart = LightweightCharts.createChart(container, {{
            width: container.clientWidth, height: 500,
            layout: {{ background: {{ type: 'solid', color: '#131722' }}, textColor: '#999', fontSize: 11 }},
            grid: {{ vertLines: {{ color: '#1e222d' }}, horzLines: {{ color: '#1e222d' }} }},
            crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
            timeScale: {{ timeVisible: true, secondsVisible: false, borderColor: '#2a2e3d' }},
            rightPriceScale: {{ borderColor: '#2a2e3d' }}
        }});
        var candleSeries = chart.addCandlestickSeries({{
            upColor: '#26a69a', downColor: '#ef5350',
            borderUpColor: '#26a69a', borderDownColor: '#ef5350',
            wickUpColor: '#26a69a', wickDownColor: '#ef5350'
        }});
        var chartData = bars.map(function(b) {{
            var ts = Math.floor(new Date(dateStr + 'T' + b.t + ':00').getTime() / 1000);
            return {{ time: ts, open: b.o, high: b.h, low: b.l, close: b.c }};
        }});
        candleSeries.setData(chartData);

        // OHLC legend
        (function(ch, series, data, legId) {{
            var leg = document.getElementById(legId);
            if (!leg) return;
            function showBar(d) {{
                var col = d.close >= d.open ? '#26a69a' : '#ef5350';
                var chg = d.close - d.open;
                var pct = d.open !== 0 ? (chg / d.open * 100) : 0;
                var s = chg >= 0 ? '+' : '';
                leg.innerHTML = '<span style="color:#666">O</span> <span style="color:'+col+'">'+d.open.toFixed(2)+'</span> '
                    + '<span style="color:#666">H</span> <span style="color:'+col+'">'+d.high.toFixed(2)+'</span> '
                    + '<span style="color:#666">L</span> <span style="color:'+col+'">'+d.low.toFixed(2)+'</span> '
                    + '<span style="color:#666">C</span> <span style="color:'+col+';font-weight:700">'+d.close.toFixed(2)+'</span> '
                    + '<span style="color:'+col+'">'+s+chg.toFixed(2)+' ('+s+pct.toFixed(1)+'%)</span>';
            }}
            if (data.length) showBar(data[data.length-1]);
            ch.subscribeCrosshairMove(function(param) {{
                if (!param || !param.time) {{ if (data.length) showBar(data[data.length-1]); return; }}
                var d = param.seriesData.get(series);
                if (d) showBar(d);
            }});
        }})(chart, candleSeries, chartData, cfg.legendId);

        // Entry/exit markers
        var markers = [];
        var entryTs = Math.floor(new Date(dateStr + 'T' + cfg.entry_time + ':00').getTime() / 1000);
        var entryLabel = typeof cfg.entry_px === 'string' && cfg.entry_px.indexOf('/') >= 0 ? '$' + cfg.entry_px : '$' + (cfg.entry_px ? Number(cfg.entry_px).toFixed(2) : '?');
        markers.push({{ time: entryTs, position: 'belowBar', color: '#2196F3', shape: 'arrowUp', text: 'BUY ' + entryLabel }});
        var exitTs = Math.floor(new Date(dateStr + 'T' + cfg.exit_time + ':00').getTime() / 1000);
        var exitLabel = typeof cfg.exit_px === 'string' && cfg.exit_px.indexOf('/') >= 0 ? '$' + cfg.exit_px : '$' + (cfg.exit_px ? Number(cfg.exit_px).toFixed(2) : '?');
        markers.push({{ time: exitTs, position: 'aboveBar', color: cfg.pnl >= 0 ? '#00d4aa' : '#ff4466', shape: 'arrowDown', text: 'SELL ' + exitLabel }});
        markers.sort(function(a,b) {{ return a.time - b.time; }});
        candleSeries.setMarkers(markers);
        chart.timeScale().fitContent();
        charts.push(chart);
    }}

    panel.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

function prevMonth() {{ currentMonth--; if (currentMonth < 0) {{ currentMonth = 11; currentYear--; }} drawCalendar(getFiltered()); }}
function nextMonth() {{ currentMonth++; if (currentMonth > 11) {{ currentMonth = 0; currentYear++; }} drawCalendar(getFiltered()); }}

init();

// ── Refresh Calendar — fetch new trades from Polygon via backend ──
function refreshCalendar() {{
    var btn = document.getElementById('refreshBtn');
    var status = document.getElementById('refreshStatus');
    if (btn.classList.contains('running')) return;

    var prevCount = allTrades.length;
    btn.classList.add('running');
    btn.innerHTML = '&#8635; Refreshing&hellip;';
    status.textContent = 'Fetching new data from Polygon...';
    status.className = 'refresh-status';

    fetch('/api/refresh_calendar', {{ method: 'POST' }})
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
            if (data.ok) {{
                var newTrades = data.trades || [];
                if (newTrades.length > 0) {{
                    for (var i = 0; i < newTrades.length; i++) {{ allTrades.push(newTrades[i]); }}
                    updateAll();
                    var dates = {{}};
                    allTrades.forEach(function(t) {{ dates[t.date] = true; }});
                    var sortedDates = Object.keys(dates).sort();
                    document.getElementById('tradeCount').textContent =
                        allTrades.length + ' trades \\u2014 ' + sortedDates.length +
                        ' dates \\u2014 ' + sortedDates[0] + ' to ' + sortedDates[sortedDates.length - 1];
                }}
                var diff = allTrades.length - prevCount;
                var msg = data.message || (diff > 0 ? '+' + diff + ' new trades' : 'No new trades');
                status.textContent = msg;
                status.className = 'refresh-status ok';
                btn.classList.remove('running');
                btn.classList.add('done');
                btn.innerHTML = '&#10003; Done';
            }} else {{
                throw new Error(data.error || 'Refresh failed');
            }}
        }})
        .catch(function(e) {{
            status.textContent = 'Error: ' + e.message;
            status.className = 'refresh-status err';
            btn.classList.remove('running');
            btn.innerHTML = '&#8635; Refresh Data';
        }})
        .finally(function() {{
            setTimeout(function() {{
                btn.classList.remove('done');
                btn.innerHTML = '&#8635; Refresh Data';
            }}, 5000);
        }});
}}

// Also load any previously-refreshed trades on page load
(function loadNewTrades() {{
    fetch('/api/calendar_trades')
        .then(function(r) {{ return r.json(); }})
        .then(function(newTrades) {{
            if (newTrades && newTrades.length > 0) {{
                var existing = {{}};
                allTrades.forEach(function(t) {{ existing[t.date + '|' + t.strategy] = true; }});
                var added = 0;
                newTrades.forEach(function(t) {{
                    var key = t.date + '|' + t.strategy;
                    if (!existing[key]) {{
                        allTrades.push(t);
                        existing[key] = true;
                        added++;
                    }}
                }});
                if (added > 0) {{
                    updateAll();
                    var dates = {{}};
                    allTrades.forEach(function(t) {{ dates[t.date] = true; }});
                    var sortedDates = Object.keys(dates).sort();
                    document.getElementById('tradeCount').textContent =
                        allTrades.length + ' trades \\u2014 ' + sortedDates.length +
                        ' dates \\u2014 ' + sortedDates[0] + ' to ' + sortedDates[sortedDates.length - 1];
                    document.getElementById('refreshStatus').textContent =
                        added + ' new trades loaded from previous refresh';
                    document.getElementById('refreshStatus').className = 'refresh-status ok';
                }}
            }}
        }})
        .catch(function() {{}}); // silently fail if endpoint not available
}})();

</script>
</body>
</html>"""

    with open(SCRIPT_DIR / 'widened_trade_calendar.html', 'w') as f:
        f.write(html)


if __name__ == '__main__':
    main()
