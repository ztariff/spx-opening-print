#!/usr/bin/env python3
"""
Verified Trades Export
======================
Re-runs the top validated edges from 52_ultra_sharpe_scanner.py and exports
every single trade with full provenance:
  - Date, signal name, filter, exit params
  - SPX entry/exit price and time
  - SPXW option ticker used
  - Option entry/exit price (real bar close)
  - Option P&L per contract
  - Exit reason (PT, SL, time_stop)

Outputs a single CSV with all trades for the dashboard.
Every number traces back to a real cached SPXW option bar JSON file.
"""

import csv, json, math, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# DATA LOADING (identical to 52)
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
        if al == 0 and ag == 0: rsi5[sorted_dates[i]] = 50
        elif al == 0: rsi5[sorted_dates[i]] = 100
        else: rsi5[sorted_dates[i]] = 100 - 100/(1+ag/al)

    prev_days = {}
    for i in range(5, len(sorted_dates)):
        d = sorted_dates[i]
        prev = [spx_daily[sorted_dates[i-j]] for j in range(1, 6)]
        prev_days[d] = prev

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

    return spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5, prev_days, vix_prev_close, rsi5, atr5


def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5, prev_days, vix_prev_close, rsi5, atr5):
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
        two_day_ret = (dd['o'] - prev[1]['c']) / prev[1]['c'] * 100 if len(prev) > 1 else 0
        five_day_ranges = [(p['h']-p['l'])/p['o']*100 for p in prev[:5]]
        avg_5d_range = statistics.mean(five_day_ranges) if five_day_ranges else 0

        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_range_pct = (fb['h'] - fb['l']) / fb['o'] * 100
        fb_body_ratio = abs(fb['c'] - fb['o']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0
        fb_lower_wick = (min(fb['o'], fb['c']) - fb['l']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0
        total_vol = sum(b['v'] for b in bars)
        fb_vol_pct = fb['v'] / total_vol * 100 if total_vol > 0 else 0

        fb2_bull = len(bars) > 1 and bars[0]['c'] > bars[0]['o'] and bars[1]['c'] > bars[1]['o']
        fb2_ret = (bars[1]['c'] - bars[0]['o']) / bars[0]['o'] * 100 if len(bars) > 1 else 0
        fb3 = bars[:3]
        fb3_consecutive_bull = all(b['c'] > b['o'] for b in fb3)
        fb3_ret = (fb3[-1]['c'] - fb3[0]['o']) / fb3[0]['o'] * 100 if len(fb3) == 3 else 0

        def or_stats(n):
            subset = bars[:n]
            if len(subset) < n: return None
            hi, lo = max(b['h'] for b in subset), min(b['l'] for b in subset)
            cl, op, rng = subset[-1]['c'], subset[0]['o'], hi - lo
            return {'high': hi, 'low': lo, 'close': cl, 'open': op, 'range': rng,
                    'range_pct': rng / op * 100, 'ret': (cl - op) / op * 100,
                    'bullish': cl > op, 'close_loc': (cl - lo) / rng if rng > 0 else 0.5}

        or5, or15 = or_stats(5), or_stats(15)
        or15_narrow = or15 and avg_5d_range > 0 and (or15['range_pct'] / avg_5d_range) < 0.3

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
            'vix': vix_open, 'vix_regime': vix_regime, 'vix_change_pct': vix_change_pct,
            'gap_pct': gap_pct, 'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish,
            'fb_range_pct': fb_range_pct, 'fb_body_ratio': fb_body_ratio,
            'fb_lower_wick': fb_lower_wick, 'fb_vol_pct': fb_vol_pct,
            'fb2_bull': fb2_bull, 'fb2_ret': fb2_ret,
            'fb3_consecutive_bull': fb3_consecutive_bull, 'fb3_ret': fb3_ret,
            'or5': or5, 'or15': or15, 'or15_narrow': or15_narrow,
            'above_50d': above_50d, 'above_20d': above_20d,
            'above_10d': above_10d, 'above_5d': above_5d,
            'dow': dow, 'prev_close': prev_close, 'prev_close_loc': prev_close_loc,
            'prev_bullish': prev_bullish, 'two_day_ret': two_day_ret,
            'avg_5d_range': avg_5d_range, 'total_vol': total_vol,
            'rsi5': rsi5.get(d, 50), 'atr5': atr5.get(d, 30),
        })
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION (identical to 52)
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
    entry_bar = bars[entry_idx]
    entry_price = entry_bar['c']
    entry_mins = entry_bar['mins']
    pt_pts = exit_params.get('pt_pts')
    sl_pts = exit_params.get('sl_pts')
    ts_min = exit_params.get('ts_min', 60)
    ts_deadline = entry_mins + ts_min

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
        else:
            if bar['l'] < trough: trough = bar['l']
            if sl_level and bar['h'] >= sl_level:
                exit_price = sl_level; exit_reason = 'stop_loss'; exit_idx = j; break
            if pt_level and bar['l'] <= pt_level:
                exit_price = pt_level; exit_reason = 'profit_target'; exit_idx = j; break
    else:
        exit_price = bars[-1]['c']; exit_idx = len(bars) - 1

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
# OPTION PRICING WITH FULL PROVENANCE
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

    if struct == 'long_call':
        return try_single('C', atm)
    elif struct == 'long_itm_call':
        return try_single('C', atm - 5)
    elif struct == 'long_otm_call':
        return try_single('C', atm + 5)
    elif struct == 'long_put':
        return try_single('P', atm)
    elif struct == 'long_otm_put':
        return try_single('P', atm - 5)
    elif struct == 'bull_call_5':
        return try_spread('C', atm, atm + 5)
    elif struct == 'bull_call_10':
        return try_spread('C', atm, atm + 10)
    elif struct == 'bear_put_5':
        return try_spread('P', atm, atm - 5)
    return None, None


# ═══════════════════════════════════════════════════════════════
# EDGE DEFINITIONS — the validated winners from 52
# ═══════════════════════════════════════════════════════════════
EDGES = [
    {
        'name': 'StrongBody_Bull',
        'signal': lambda d: d['fb_body_ratio'] > 0.80 and d['fb_bullish'] and d['fb_ret'] >= 0.03,
        'entry_idx': 0, 'direction': 1,
        'filter': lambda d: d['vix'] < 14,
        'filter_name': 'VeryLow',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'exit_name': 'Micro_2_1.5_5',
        'structs': ['long_call', 'long_itm_call', 'long_otm_call', 'bull_call_10'],
    },
    {
        'name': 'StrongBody_Trend',
        'signal': lambda d: (d['fb_body_ratio'] > 0.80 and d['fb_bullish'] and d['fb_ret'] >= 0.03
                              and d['above_20d'] is True),
        'entry_idx': 0, 'direction': 1,
        'filter': lambda d: d['vix'] < 14,
        'filter_name': 'VeryLow',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'exit_name': 'Micro_2_1.5_5',
        'structs': ['long_call', 'long_otm_call', 'bull_call_10'],
    },
    {
        'name': 'Bull_GapCont',
        'signal': lambda d: d['fb_bullish'] and d['fb_ret'] >= 0.02 and 0.05 <= d['gap_pct'] <= 0.30,
        'entry_idx': 0, 'direction': 1,
        'filter': lambda d: d['dow'] == 4,  # Friday
        'filter_name': 'Fri',
        'exit': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
        'exit_name': 'Std_10_5_15',
        'structs': ['long_call', 'long_otm_call', 'bull_call_5', 'bull_call_10'],
    },
    {
        'name': 'StrongBody_PrevBull',
        'signal': lambda d: (d['fb_body_ratio'] > 0.80 and d['fb_bullish'] and d['fb_ret'] >= 0.03
                              and d['prev_bullish']),
        'entry_idx': 0, 'direction': 1,
        'filter': lambda d: d['dow'] == 4,  # Friday
        'filter_name': 'Fri',
        'exit': {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
        'exit_name': 'Asym_12_4_20',
        'structs': ['long_call', 'long_otm_call', 'bull_call_10'],
    },
    {
        'name': 'TripleBull_BodyTrendPrev',
        'signal': lambda d: (d['fb_body_ratio'] > 0.75 and d['fb_bullish'] and d['fb_ret'] >= 0.03
                              and d['above_20d'] is True and d['prev_bullish']),
        'entry_idx': 0, 'direction': 1,
        'filter': lambda d: d['vix'] < 14,
        'filter_name': 'VeryLow',
        'exit': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
        'exit_name': 'Micro_4_2_8',
        'structs': ['long_call', 'long_otm_call', 'bull_call_10'],
    },
]


def main():
    print("=" * 120)
    print("VERIFIED TRADES EXPORT — Real SPXW Option Data Only")
    print("=" * 120)

    data = load_all_data()
    days = extract_features(*data)
    print(f"  {len(days)} trading days loaded")

    all_trades = []  # master list for dashboard

    for edge in EDGES:
        print(f"\n{'─'*100}")
        print(f"  Edge: {edge['name']}|{edge['filter_name']}|{edge['exit_name']}")

        # Find qualifying days
        qualifying = []
        for day in days:
            if edge['filter'](day) and edge['signal'](day):
                qualifying.append(day)

        print(f"  Qualifying days: {len(qualifying)}")

        for struct in edge['structs']:
            priced_trades = []
            for day in qualifying:
                bars = day['bars']
                idx = edge['entry_idx']
                if idx >= len(bars) - 3: continue

                trade = simulate_trade(bars, idx, edge['direction'], edge['exit'])
                pnl, info = price_with_provenance(
                    day['date'], trade['entry_mins'], trade['exit_mins'],
                    trade['entry_price'], trade['direction'], struct)

                if pnl is not None:
                    record = {
                        'date': day['date'],
                        'strategy': f"{edge['name']}|{edge['filter_name']}|{edge['exit_name']}",
                        'structure': struct,
                        'direction': 'LONG' if edge['direction'] == 1 else 'SHORT',
                        'spx_entry': trade['entry_price'],
                        'spx_exit': trade['exit_price'],
                        'entry_time': trade['entry_time'],
                        'exit_time': trade['exit_time'],
                        'hold_mins': trade['hold_mins'],
                        'exit_reason': trade['exit_reason'],
                        'und_pts': trade['und_pts'],
                        'vix': round(day['vix'], 1),
                        'opt_pnl': pnl,
                    }
                    # Add option provenance
                    if info:
                        if 'ticker' in info:
                            record['opt_ticker'] = info['ticker']
                            record['opt_entry_px'] = info['entry_px']
                            record['opt_exit_px'] = info['exit_px']
                        elif 'long_ticker' in info:
                            record['opt_ticker'] = f"{info['long_ticker']}|{info['short_ticker']}"
                            record['opt_entry_px'] = f"{info['long_entry']}/{info['short_entry']}"
                            record['opt_exit_px'] = f"{info['long_exit']}/{info['short_exit']}"

                    priced_trades.append(record)

            if priced_trades:
                pnls = [t['opt_pnl'] for t in priced_trades]
                avg = statistics.mean(pnls)
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                std = statistics.stdev(pnls) if len(pnls) > 1 else 0
                sh = avg / std if std > 0 else 0
                print(f"    {struct:20s}  N={len(priced_trades):>4}  WR={wr:.1f}%  "
                      f"Avg=${avg:>+.2f}  Sharpe={sh:.3f}")
                all_trades.extend(priced_trades)
            else:
                print(f"    {struct:20s}  0 trades priced")

    # Save master CSV
    out_path = SCRIPT_DIR / 'backtest_results' / 'verified_trades.csv'
    if all_trades:
        fields = ['date', 'strategy', 'structure', 'direction', 'spx_entry', 'spx_exit',
                  'entry_time', 'exit_time', 'hold_mins', 'exit_reason', 'und_pts', 'vix',
                  'opt_pnl', 'opt_ticker', 'opt_entry_px', 'opt_exit_px']
        with open(out_path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            for t in sorted(all_trades, key=lambda x: x['date']):
                w.writerow(t)
        print(f"\n  Saved {len(all_trades)} verified trades to {out_path.name}")

    # Also save as JSON for the dashboard
    json_path = SCRIPT_DIR / 'backtest_results' / 'verified_trades.json'
    with open(json_path, 'w') as f:
        json.dump(sorted(all_trades, key=lambda x: x['date']), f, indent=2)
    print(f"  Saved JSON to {json_path.name}")

    # Summary stats per strategy+structure
    print(f"\n{'='*120}")
    print("SUMMARY BY STRATEGY+STRUCTURE")
    print(f"{'='*120}")
    combos = defaultdict(list)
    for t in all_trades:
        combos[f"{t['strategy']}|{t['structure']}"].append(t['opt_pnl'])

    summary = []
    for label, pnls in sorted(combos.items()):
        n = len(pnls)
        avg = statistics.mean(pnls)
        tot = sum(pnls)
        wr = sum(1 for p in pnls if p > 0) / n * 100
        std = statistics.stdev(pnls) if n > 1 else 0
        sh = avg / std if std > 0 else 0

        cum = 0; pk = 0; mdd = 0
        for p in pnls:
            cum += p
            if cum > pk: pk = cum
            dd = pk - cum
            if dd > mdd: mdd = dd

        # IS/OOS
        is_pnl = [t['opt_pnl'] for t in all_trades if f"{t['strategy']}|{t['structure']}" == label and t['date'] < '2023-01-01']
        oos_pnl = [t['opt_pnl'] for t in all_trades if f"{t['strategy']}|{t['structure']}" == label and t['date'] >= '2023-01-01']
        is_sh = statistics.mean(is_pnl)/statistics.stdev(is_pnl) if len(is_pnl) > 3 and statistics.stdev(is_pnl) > 0 else 0
        oos_sh = statistics.mean(oos_pnl)/statistics.stdev(oos_pnl) if len(oos_pnl) > 3 and statistics.stdev(oos_pnl) > 0 else 0

        print(f"  {label[:65]:>65s}  N={n:>3}  WR={wr:.1f}%  Avg=${avg:>+.2f}  Tot=${tot:>+.2f}  "
              f"Sh={sh:.3f}  MDD=${mdd:.2f}  IS={is_sh:.3f}  OOS={oos_sh:.3f}")
        summary.append({'label': label, 'n': n, 'wr': round(wr,1), 'avg': round(avg,2),
                        'total': round(tot,2), 'sharpe': round(sh,3), 'mdd': round(mdd,2),
                        'is_sharpe': round(is_sh,3), 'oos_sharpe': round(oos_sh,3)})

    with open(SCRIPT_DIR / 'backtest_results' / 'verified_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*120}")
    print("DONE — All trades verified with real SPXW option data")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
