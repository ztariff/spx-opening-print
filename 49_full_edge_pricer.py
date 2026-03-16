#!/usr/bin/env python3
"""
Full Edge Pricer — regenerates trades + prices with real options
================================================================
Combines edge signal generation with real SPXW 0DTE option pricing.
No CSV matching — generates trades fresh for each edge, then prices them.
"""

import csv, json, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from itertools import combinations

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (same as 47)
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
            if mins < 570 or mins >= 960:
                continue
            spx_1min[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
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
                'o': float(row['open']), 'c': float(row['close']),
            }

    sorted_dates = sorted(spx_daily.keys())
    closes = [spx_daily[d]['c'] for d in sorted_dates]
    sma50 = {}
    for i in range(49, len(sorted_dates)):
        sma50[sorted_dates[i]] = sum(closes[i-49:i+1]) / 50
    sma20 = {}
    for i in range(19, len(sorted_dates)):
        sma20[sorted_dates[i]] = sum(closes[i-19:i+1]) / 20

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

    print(f"  SPX 1min: {len(spx_1min)} days, daily: {len(spx_daily)}, VIX: {len(vix_daily)}")
    return spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data


def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data):
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

        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_range_pct = (fb['h'] - fb['l']) / fb['o'] * 100

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

        or5 = bar_stats(5)
        or15 = bar_stats(15)
        or30 = bar_stats(30)

        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        if vix_open < 16:
            vix_regime = 'low'
        elif vix_open < 25:
            vix_regime = 'mid'
        elif vix_open < 35:
            vix_regime = 'high'
        else:
            vix_regime = 'extreme'

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
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
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

    if direction == 1:
        pt_level = entry_price + pt_pts if pt_pts else None
        sl_level = entry_price - sl_pts if sl_pts else None
        if pt_pct: pt_level = entry_price * (1 + pt_pct / 100)
        if sl_pct: sl_level = entry_price * (1 - sl_pct / 100)
    else:
        pt_level = entry_price - pt_pts if pt_pts else None
        sl_level = entry_price + sl_pts if sl_pts else None
        if pt_pct: pt_level = entry_price * (1 - pt_pct / 100)
        if sl_pct: sl_level = entry_price * (1 + sl_pct / 100)

    peak = entry_price
    trough = entry_price
    exit_reason = 'time_stop'
    exit_idx = entry_idx

    for j in range(entry_idx + 1, len(bars)):
        bar = bars[j]
        price = bar['c']

        if bar['mins'] >= ts_deadline or bar['mins'] >= 959:
            exit_idx = j
            exit_reason = 'time_stop'
            break

        if direction == 1:
            if price > peak: peak = price
            if pt_level and price >= pt_level:
                exit_idx = j; exit_reason = 'profit_target'; break
            if sl_level and price <= sl_level:
                exit_idx = j; exit_reason = 'stop_loss'; break
            if trail_pct and peak > entry_price:
                tl = peak * (1 - trail_pct / 100)
                if price <= tl: exit_idx = j; exit_reason = 'trailing_stop'; break
            if trail_pts and peak > entry_price:
                tl = peak - trail_pts
                if price <= tl: exit_idx = j; exit_reason = 'trailing_stop'; break
        else:
            if price < trough: trough = price
            if pt_level and price <= pt_level:
                exit_idx = j; exit_reason = 'profit_target'; break
            if sl_level and price >= sl_level:
                exit_idx = j; exit_reason = 'stop_loss'; break
            if trail_pct and trough < entry_price:
                tl = trough * (1 + trail_pct / 100)
                if price >= tl: exit_idx = j; exit_reason = 'trailing_stop'; break
            if trail_pts and trough < entry_price:
                tl = trough + trail_pts
                if price >= tl: exit_idx = j; exit_reason = 'trailing_stop'; break

    exit_bar = bars[exit_idx]
    return {
        'entry_price': entry_bar['c'],
        'exit_price': exit_bar['c'],
        'entry_time': entry_bar['time'],
        'exit_time': exit_bar['time'],
        'entry_mins': entry_bar['mins'],
        'exit_mins': exit_bar['mins'],
        'hold_mins': exit_bar['mins'] - entry_mins,
        'exit_reason': exit_reason,
        'und_pts': direction * (exit_bar['c'] - entry_bar['c']),
        'direction': direction,
    }


# ═══════════════════════════════════════════════════════════════
# OPTION PRICING
# ═══════════════════════════════════════════════════════════════
_opt_cache = {}

def load_option_bars(ticker, date_str):
    key = f"{ticker}_{date_str}"
    if key in _opt_cache:
        return _opt_cache[key]
    fn = ticker.replace(':', '_') + f'_{date_str}.json'
    path = CACHE_DIR / fn
    if not path.exists():
        _opt_cache[key] = None
        return None
    with open(path) as f:
        data = json.load(f)
    if not data:
        _opt_cache[key] = None
        return None
    bars = []
    for bar in data:
        ts = bar['t'] / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        month = dt.month
        et = dt - timedelta(hours=4 if 3 <= month <= 10 else 5)
        mins = et.hour * 60 + et.minute
        if mins < 570 or mins >= 960:
            continue
        bars.append({'mins': mins, 'o': bar['o'], 'h': bar['h'], 'l': bar['l'], 'c': bar['c'], 'v': bar.get('v',0)})
    bars.sort(key=lambda x: x['mins'])
    _opt_cache[key] = bars if bars else None
    return _opt_cache[key]

def build_spxw_ticker(date_str, cp, strike):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    ymd = dt.strftime('%y%m%d')
    return f"O:SPXW{ymd}{cp}{int(strike * 1000):08d}"

def find_bar_at(bars, target_mins, tol=3):
    if not bars: return None
    best = min(bars, key=lambda b: abs(b['mins'] - target_mins))
    return best if abs(best['mins'] - target_mins) <= tol else None

def get_strike(price, rnd=5):
    return round(price / rnd) * rnd

def price_option_trade(date_str, entry_mins, exit_mins, spx_price, direction, struct):
    """Price a trade with a specific option structure. Returns pnl_per_contract or None."""
    atm = get_strike(spx_price)

    if struct == 'long_call':
        ticker = build_spxw_ticker(date_str, 'C', atm)
        bars = load_option_bars(ticker, date_str)
        if not bars: return None
        e = find_bar_at(bars, entry_mins)
        x = find_bar_at(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return {'pnl': (x['c'] - e['c']) * 100, 'entry_opt': e['c'], 'exit_opt': x['c'], 'ticker': ticker}

    elif struct == 'long_put':
        ticker = build_spxw_ticker(date_str, 'P', atm)
        bars = load_option_bars(ticker, date_str)
        if not bars: return None
        e = find_bar_at(bars, entry_mins)
        x = find_bar_at(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return {'pnl': (x['c'] - e['c']) * 100, 'entry_opt': e['c'], 'exit_opt': x['c'], 'ticker': ticker}

    elif struct == 'long_otm_call':
        ticker = build_spxw_ticker(date_str, 'C', atm + 5)
        bars = load_option_bars(ticker, date_str)
        if not bars: return None
        e = find_bar_at(bars, entry_mins)
        x = find_bar_at(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return {'pnl': (x['c'] - e['c']) * 100, 'entry_opt': e['c'], 'exit_opt': x['c'], 'ticker': ticker}

    elif struct == 'long_otm_put':
        ticker = build_spxw_ticker(date_str, 'P', atm - 5)
        bars = load_option_bars(ticker, date_str)
        if not bars: return None
        e = find_bar_at(bars, entry_mins)
        x = find_bar_at(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return {'pnl': (x['c'] - e['c']) * 100, 'entry_opt': e['c'], 'exit_opt': x['c'], 'ticker': ticker}

    elif struct == 'bear_call_spread':
        # Sell ATM call, buy ATM+5 call (credit spread, bearish)
        short_t = build_spxw_ticker(date_str, 'C', atm)
        long_t = build_spxw_ticker(date_str, 'C', atm + 5)
        sb = load_option_bars(short_t, date_str)
        lb = load_option_bars(long_t, date_str)
        if not sb or not lb: return None
        se = find_bar_at(sb, entry_mins); sx = find_bar_at(sb, exit_mins)
        le = find_bar_at(lb, entry_mins); lx = find_bar_at(lb, exit_mins)
        if not all([se, sx, le, lx]): return None
        credit = se['c'] - le['c']
        debit = sx['c'] - lx['c']
        return {'pnl': (credit - debit) * 100, 'credit': credit, 'debit': debit}

    elif struct == 'bull_call_spread':
        # Buy ATM call, sell ATM+5 call (debit spread, bullish)
        long_t = build_spxw_ticker(date_str, 'C', atm)
        short_t = build_spxw_ticker(date_str, 'C', atm + 5)
        lb = load_option_bars(long_t, date_str)
        sb = load_option_bars(short_t, date_str)
        if not lb or not sb: return None
        le = find_bar_at(lb, entry_mins); lx = find_bar_at(lb, exit_mins)
        se = find_bar_at(sb, entry_mins); sx = find_bar_at(sb, exit_mins)
        if not all([le, lx, se, sx]): return None
        debit = le['c'] - se['c']
        credit = lx['c'] - sx['c']
        return {'pnl': (credit - debit) * 100, 'debit': debit, 'credit': credit}

    elif struct == 'bear_put_spread':
        # Buy ATM put, sell ATM-5 put (debit spread, bearish)
        long_t = build_spxw_ticker(date_str, 'P', atm)
        short_t = build_spxw_ticker(date_str, 'P', atm - 5)
        lb = load_option_bars(long_t, date_str)
        sb = load_option_bars(short_t, date_str)
        if not lb or not sb: return None
        le = find_bar_at(lb, entry_mins); lx = find_bar_at(lb, exit_mins)
        se = find_bar_at(sb, entry_mins); sx = find_bar_at(sb, exit_mins)
        if not all([le, lx, se, sx]): return None
        debit = le['c'] - se['c']
        credit = lx['c'] - sx['c']
        return {'pnl': (credit - debit) * 100, 'debit': debit, 'credit': credit}

    elif struct == 'underlying':
        pts = direction * (0)  # already computed in und_pts
        return {'pnl': 0}  # placeholder

    return None


# ═══════════════════════════════════════════════════════════════
# EDGE DEFINITIONS — signal + filter + exit as callables
# ═══════════════════════════════════════════════════════════════
EDGES = [
    # ── LOW VIX ──
    {
        'name': '01_StrongFB_LowVIX',
        'desc': 'Strong bullish first bar (>0.10%), VIX<20, trail 0.10% SL 5pts 30m',
        'signal': lambda d: (0, 1) if d['fb_bullish'] and d['fb_ret'] >= 0.10 else None,
        'filter': lambda d: d['vix'] < 20,
        'exit': {'trail_pct': 0.10, 'sl_pts': 5, 'ts_min': 30},
        'direction': 1,
        'structures': ['long_call', 'long_otm_call', 'bull_call_spread'],
    },
    {
        'name': '02_GapUp_Fade_LowVIX',
        'desc': 'Fade gap-up >20bps, VIX<20, above 50d, trail 0.15% SL 8pts 45m',
        'signal': lambda d: (0, -1) if d['gap_pct'] >= 0.20 and not d['fb_bullish'] else None,
        'filter': lambda d: d['vix'] < 20 and d['above_50d'] is True,
        'exit': {'trail_pct': 0.15, 'sl_pts': 8, 'ts_min': 45},
        'direction': -1,
        'structures': ['long_put', 'long_otm_put', 'bear_put_spread', 'bear_call_spread'],
    },
    {
        'name': '03_GapUp_Cont_LowVIX',
        'desc': 'Continue gap-up >20bps, VIX<20, PT 5pts SL 3pts 15m',
        'signal': lambda d: (0, 1) if d['gap_pct'] >= 0.20 and d['fb_bullish'] else None,
        'filter': lambda d: d['vix'] < 20,
        'exit': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 15},
        'direction': 1,
        'structures': ['long_call', 'long_otm_call', 'bull_call_spread'],
    },
    {
        'name': '04_OR30_Bear_LowVIX',
        'desc': '30-min OR bearish, VIX Low, 1% PT 0.5% SL 60m',
        'signal': lambda d: (29, -1) if d['or30'] and not d['or30']['bullish'] and d['or30']['range_pct'] >= 0.20 else None,
        'filter': lambda d: d['vix'] < 16,
        'exit': {'pt_pct': 1.00, 'sl_pct': 0.50, 'ts_min': 60},
        'direction': -1,
        'structures': ['long_put', 'bear_call_spread', 'bear_put_spread'],
    },
    {
        'name': '05_GapDn_Bounce_LowVIX',
        'desc': 'Gap-down >20bps bounce, VIX<16, PT 3pts SL 2pts 10m',
        'signal': lambda d: (0, 1) if d['gap_pct'] <= -0.20 and d['fb_bullish'] else None,
        'filter': lambda d: d['vix'] < 16,
        'exit': {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 10},
        'direction': 1,
        'structures': ['long_call', 'bull_call_spread'],
    },
    # ── MID VIX ──
    {
        'name': '06_StrongFB_MidVIX',
        'desc': 'Strong bullish first bar, VIX 16-25, trail 0.10% SL 5pts 30m',
        'signal': lambda d: (0, 1) if d['fb_bullish'] and d['fb_ret'] >= 0.10 else None,
        'filter': lambda d: 16 <= d['vix'] < 25,
        'exit': {'trail_pct': 0.10, 'sl_pts': 5, 'ts_min': 30},
        'direction': 1,
        'structures': ['long_call', 'long_otm_call', 'bull_call_spread'],
    },
    {
        'name': '07_GapUp_Cont50_MidVIX',
        'desc': 'Gap-up >50bps cont, VIX Mid, below 20d — OOS VALIDATED',
        'signal': lambda d: (0, 1) if d['gap_pct'] >= 0.50 and d['fb_bullish'] else None,
        'filter': lambda d: 16 <= d['vix'] < 25 and d['above_20d'] is False,
        'exit': {'pt_pct': 0.50, 'sl_pct': 0.25, 'ts_min': 30},
        'direction': 1,
        'structures': ['long_call', 'long_otm_call', 'bull_call_spread'],
    },
    {
        'name': '08_GapUp_Fade_MidVIX',
        'desc': 'Fade gap-up >20bps, VIX Mid, above 50d, trail 0.05% SL 15pts 30m',
        'signal': lambda d: (0, -1) if d['gap_pct'] >= 0.20 and not d['fb_bullish'] else None,
        'filter': lambda d: 16 <= d['vix'] < 25 and d['above_50d'] is True,
        'exit': {'trail_pct': 0.05, 'sl_pts': 15, 'ts_min': 30},
        'direction': -1,
        'structures': ['long_put', 'bear_call_spread', 'bear_put_spread'],
    },
    # ── HIGH VIX ──
    {
        'name': '09_GapDn_Cont_HighVIX',
        'desc': 'Gap-down >50bps continues, VIX High, below 20d, PT 50 SL 25 180m',
        'signal': lambda d: (0, -1) if d['gap_pct'] <= -0.50 and not d['fb_bullish'] else None,
        'filter': lambda d: d['vix'] >= 25 and d['above_20d'] is False,
        'exit': {'pt_pts': 50, 'sl_pts': 25, 'ts_min': 180},
        'direction': -1,
        'structures': ['long_put', 'long_otm_put', 'bear_call_spread'],
    },
    {
        'name': '10_OR5_Bear_HighVIX',
        'desc': '5-min OR bearish breakout, VIX>30, below 20d, trail 0.20% SL 10 60m',
        'signal': lambda d: (4, -1) if d['or5'] and not d['or5']['bullish'] and d['or5']['range_pct'] >= 0.10 else None,
        'filter': lambda d: d['vix'] >= 30 and d['above_20d'] is False,
        'exit': {'trail_pct': 0.20, 'sl_pts': 10, 'ts_min': 60},
        'direction': -1,
        'structures': ['long_put', 'long_otm_put', 'bear_call_spread'],
    },
    {
        'name': '11_GapDn_Cont20_VIX30',
        'desc': 'Gap-down >20bps continues, VIX>30, 50bps PT 25bps SL 30m',
        'signal': lambda d: (0, -1) if d['gap_pct'] <= -0.20 and not d['fb_bullish'] else None,
        'filter': lambda d: d['vix'] >= 30,
        'exit': {'pt_pct': 0.50, 'sl_pct': 0.25, 'ts_min': 30},
        'direction': -1,
        'structures': ['long_put', 'bear_call_spread'],
    },
    # ── AFTERNOON ──
    {
        'name': '12_PM_Trend_HighVIX',
        'desc': 'PM trend continuation, VIX>30, PT 5pts SL 2pts 5m',
        'signal': lambda d: _pm_trend_signal(d, 0.30),
        'filter': lambda d: d['vix'] >= 30,
        'exit': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 5},
        'direction': 0,  # mixed
        'structures': ['long_call', 'long_put'],  # direction-dependent
    },
    # ── MIDDAY MEAN REVERSION ──
    {
        'name': '13_Midday_MR_LowVIX',
        'desc': 'Midday mean revert >60bps, VIX Low, trail 0.20% SL 10 60m',
        'signal': lambda d: _midday_mr_signal(d, 0.60),
        'filter': lambda d: d['vix'] < 16,
        'exit': {'trail_pct': 0.20, 'sl_pts': 10, 'ts_min': 60},
        'direction': 0,
        'structures': ['long_call', 'long_put'],
    },
]

def _pm_trend_signal(day, threshold_pct):
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
        return idx, 1
    elif move <= -threshold_pct:
        return idx, -1
    return None

def _midday_mr_signal(day, threshold_pct):
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
        return idx, -1
    elif move <= -threshold_pct:
        return idx, 1
    return None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 140)
    print("FULL EDGE PRICER — Signal Generation + Real Option Pricing")
    print("=" * 140)

    spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data = load_all_data()
    days = extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, prev_data)

    out_dir = SCRIPT_DIR / 'backtest_results'
    out_dir.mkdir(exist_ok=True)

    all_results = []
    all_trade_details = {}  # for portfolio analysis

    for edge in EDGES:
        print(f"\n{'─'*140}")
        print(f"  {edge['name']}: {edge['desc']}")

        # Generate trades
        trades = []
        for day in days:
            if not edge['filter'](day):
                continue
            result = edge['signal'](day)
            if result is None:
                continue
            entry_idx, direction = result
            if entry_idx >= len(day['bars']) - 5:
                continue
            trade = simulate_trade(day['bars'], entry_idx, direction, edge['exit'])
            trade['date'] = day['date']
            trade['vix'] = day['vix']
            trades.append(trade)

        if len(trades) < 10:
            print(f"  Only {len(trades)} trades — skipping (need ≥10)")
            continue

        # Underlying stats
        pts = [t['und_pts'] for t in trades]
        avg_pts = statistics.mean(pts)
        tot_pts = sum(pts)
        und_wr = sum(1 for p in pts if p > 0) / len(pts) * 100
        std_pts = statistics.stdev(pts) if len(pts) > 1 else 0
        und_sharpe = avg_pts / std_pts if std_pts > 0 else 0

        # In-sample / Out-of-sample split
        is_trades = [t for t in trades if t['date'] < '2023-01-01']
        oos_trades = [t for t in trades if t['date'] >= '2023-01-01']

        is_pts = [t['und_pts'] for t in is_trades]
        oos_pts = [t['und_pts'] for t in oos_trades]

        is_sharpe = statistics.mean(is_pts) / statistics.stdev(is_pts) if len(is_pts) > 5 and statistics.stdev(is_pts) > 0 else 0
        oos_sharpe = statistics.mean(oos_pts) / statistics.stdev(oos_pts) if len(oos_pts) > 5 and statistics.stdev(oos_pts) > 0 else 0

        print(f"  Underlying: N={len(trades)}  WR={und_wr:.1f}%  AvgPts={avg_pts:+.2f}  TotPts={tot_pts:+.1f}  Sharpe={und_sharpe:.3f}")
        print(f"  IS({len(is_trades)}): Sharpe={is_sharpe:.3f}   OOS({len(oos_trades)}): Sharpe={oos_sharpe:.3f}")

        # Price with each option structure
        for struct in edge['structures']:
            priced = []
            missed = 0

            for trade in trades:
                # For mixed direction edges, match structure to trade direction
                if edge['direction'] == 0:
                    if trade['direction'] == 1 and struct == 'long_put':
                        continue
                    if trade['direction'] == -1 and struct == 'long_call':
                        continue

                result = price_option_trade(
                    trade['date'], trade['entry_mins'], trade['exit_mins'],
                    trade['entry_price'], trade['direction'], struct
                )
                if result and result['pnl'] is not None:
                    priced.append({
                        'date': trade['date'],
                        'direction': trade['direction'],
                        'und_pts': trade['und_pts'],
                        'opt_pnl': result['pnl'],
                        'entry_time': trade['entry_time'],
                        'exit_time': trade['exit_time'],
                    })
                else:
                    missed += 1

            n = len(priced)
            if n < 5:
                print(f"    {struct:25s}  {n} priced ({missed} missed) — insufficient")
                continue

            pnl_list = [p['opt_pnl'] for p in priced]
            avg_pnl = statistics.mean(pnl_list)
            tot_pnl = sum(pnl_list)
            wins = [p for p in pnl_list if p > 0]
            losses = [p for p in pnl_list if p <= 0]
            wr = len(wins) / n * 100
            gw = sum(wins); gl = abs(sum(losses))
            pf = gw / gl if gl > 0 else 99
            std = statistics.stdev(pnl_list) if n > 1 else 0
            sharpe = avg_pnl / std if std > 0 else 0

            cum = 0; peak = 0; max_dd = 0
            for p in pnl_list:
                cum += p
                if cum > peak: peak = cum
                dd = peak - cum
                if dd > max_dd: max_dd = dd

            # OOS split for option P&L
            is_opt = [p['opt_pnl'] for p in priced if p['date'] < '2023-01-01']
            oos_opt = [p['opt_pnl'] for p in priced if p['date'] >= '2023-01-01']
            is_sh = statistics.mean(is_opt) / statistics.stdev(is_opt) if len(is_opt) > 5 and statistics.stdev(is_opt) > 0 else 0
            oos_sh = statistics.mean(oos_opt) / statistics.stdev(oos_opt) if len(oos_opt) > 5 and statistics.stdev(oos_opt) > 0 else 0

            print(f"    {struct:25s}  N={n:>4} ({n/(n+missed)*100:.0f}%)  "
                  f"WR={wr:>5.1f}%  Avg=${avg_pnl:>+8.2f}  Tot=${tot_pnl:>+10.2f}  "
                  f"Sh={sharpe:>6.3f}  PF={pf:>5.2f}  DD=${max_dd:>8.2f}  "
                  f"IS_Sh={is_sh:.3f}  OOS_Sh={oos_sh:.3f}")

            key = f"{edge['name']}|{struct}"
            all_results.append({
                'edge': edge['name'], 'structure': struct,
                'n': n, 'n_total': n + missed, 'wr': round(wr, 1),
                'avg_pnl': round(avg_pnl, 2), 'total_pnl': round(tot_pnl, 2),
                'sharpe': round(sharpe, 3), 'pf': round(pf, 2),
                'max_dd': round(max_dd, 2),
                'is_sharpe': round(is_sh, 3), 'oos_sharpe': round(oos_sh, 3),
                'und_sharpe': round(und_sharpe, 3),
            })

            # Store for portfolio analysis
            all_trade_details[key] = {p['date']: p['opt_pnl'] for p in priced}

            # Save trade CSV
            csv_file = out_dir / f'opt2_{edge["name"]}_{struct}.csv'
            with open(csv_file, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=priced[0].keys())
                w.writeheader()
                w.writerows(priced)

    # ═══════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*160}")
    print("FINAL OPTION-PRICED EDGE RANKING — Sorted by Sharpe")
    print(f"{'='*160}")
    print(f"{'Edge':>28s} {'Structure':>25s} {'N':>5} {'WR%':>6} "
          f"{'AvgPnL':>10} {'TotalPnL':>12} {'Sharpe':>7} {'PF':>6} "
          f"{'MaxDD':>10} {'IS_Sh':>7} {'OOS_Sh':>7}")
    print("-" * 160)

    all_results.sort(key=lambda x: x['sharpe'], reverse=True)
    for r in all_results:
        print(f"{r['edge']:>28s} {r['structure']:>25s} {r['n']:>5} {r['wr']:>5.1f}% "
              f"${r['avg_pnl']:>+9.2f} ${r['total_pnl']:>+11.2f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} ${r['max_dd']:>9.2f} "
              f"{r['is_sharpe']:>7.3f} {r['oos_sharpe']:>7.3f}")

    # Save
    with open(out_dir / 'full_edge_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # ═══════════════════════════════════════════════════════════════
    # PORTFOLIO COMBOS (2, 3, 4 strategies)
    # ═══════════════════════════════════════════════════════════════
    if len(all_trade_details) >= 2:
        print(f"\n{'='*140}")
        print("PORTFOLIO COMBINATIONS — Best combos by Sharpe")
        print(f"{'='*140}")

        keys = list(all_trade_details.keys())

        for combo_size in [2, 3, 4]:
            if len(keys) < combo_size:
                continue
            best = []
            for combo in combinations(keys, combo_size):
                # Don't combine same edge different structures
                edge_names = set(k.split('|')[0] for k in combo)
                if len(edge_names) < combo_size:
                    continue  # skip — same edge repeated

                all_dates = set()
                for k in combo:
                    all_dates.update(all_trade_details[k].keys())

                daily = []
                for d in sorted(all_dates):
                    day_pnl = sum(all_trade_details[k].get(d, 0) for k in combo)
                    daily.append(day_pnl)

                if len(daily) < 20:
                    continue

                avg = statistics.mean(daily)
                std = statistics.stdev(daily)
                sh = avg / std if std > 0 else 0
                total = sum(daily)
                wr = sum(1 for p in daily if p > 0) / len(daily) * 100

                cum = 0; pk = 0; mdd = 0
                for p in daily:
                    cum += p
                    if cum > pk: pk = cum
                    dd = pk - cum
                    if dd > mdd: mdd = dd

                best.append({
                    'combo': combo, 'sharpe': sh, 'total': total,
                    'wr': wr, 'days': len(daily), 'max_dd': mdd,
                })

            best.sort(key=lambda x: x['sharpe'], reverse=True)
            print(f"\n  ── {combo_size}-Strategy Combos (top 5) ──")
            for i, c in enumerate(best[:5]):
                print(f"    #{i+1}  Sh={c['sharpe']:.3f}  Tot=${c['total']:+,.2f}  "
                      f"WR={c['wr']:.1f}%  DD=${c['max_dd']:,.2f}  Days={c['days']}")
                for k in c['combo']:
                    print(f"         → {k}")

    print(f"\n{'='*120}")
    print("DONE — All results saved to backtest_results/")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
