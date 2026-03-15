#!/usr/bin/env python3
"""
Price the deep-discovered edges with real SPXW 0DTE options.
Reads trade CSVs from 50_deep_edge_scanner.py output.
"""

import csv, json, os, statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from itertools import combinations

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'

_cache = {}

def load_option_bars(ticker, date_str):
    key = f"{ticker}_{date_str}"
    if key in _cache:
        return _cache[key]
    fn = ticker.replace(':', '_') + f'_{date_str}.json'
    path = CACHE_DIR / fn
    if not path.exists():
        _cache[key] = None
        return None
    with open(path) as f:
        data = json.load(f)
    if not data:
        _cache[key] = None
        return None
    bars = []
    for bar in data:
        ts = bar['t'] / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        month = dt.month
        et = dt - timedelta(hours=4 if 3 <= month <= 10 else 5)
        mins = et.hour * 60 + et.minute
        if mins < 570 or mins >= 960: continue
        bars.append({'mins': mins, 'o': bar['o'], 'h': bar['h'], 'l': bar['l'], 'c': bar['c'], 'v': bar.get('v',0)})
    bars.sort(key=lambda x: x['mins'])
    _cache[key] = bars if bars else None
    return _cache[key]

def build_ticker(date_str, cp, strike):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return f"O:SPXW{dt.strftime('%y%m%d')}{cp}{int(strike*1000):08d}"

def find_bar(bars, target_mins, tol=3):
    if not bars: return None
    best = min(bars, key=lambda b: abs(b['mins'] - target_mins))
    return best if abs(best['mins'] - target_mins) <= tol else None

def get_strike(price, rnd=5):
    return round(price / rnd) * rnd

def price_trade(date, entry_mins, exit_mins, spx_price, direction, struct):
    atm = get_strike(spx_price)

    if struct == 'long_call':
        t = build_ticker(date, 'C', atm)
        bars = load_option_bars(t, date)
        if not bars: return None
        e, x = find_bar(bars, entry_mins), find_bar(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return (x['c'] - e['c']) * 100

    elif struct == 'long_put':
        t = build_ticker(date, 'P', atm)
        bars = load_option_bars(t, date)
        if not bars: return None
        e, x = find_bar(bars, entry_mins), find_bar(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return (x['c'] - e['c']) * 100

    elif struct == 'long_otm_call':
        t = build_ticker(date, 'C', atm + 5)
        bars = load_option_bars(t, date)
        if not bars: return None
        e, x = find_bar(bars, entry_mins), find_bar(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return (x['c'] - e['c']) * 100

    elif struct == 'long_otm_put':
        t = build_ticker(date, 'P', atm - 5)
        bars = load_option_bars(t, date)
        if not bars: return None
        e, x = find_bar(bars, entry_mins), find_bar(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None
        return (x['c'] - e['c']) * 100

    elif struct == 'bull_call_spread':
        lt = build_ticker(date, 'C', atm)
        st = build_ticker(date, 'C', atm + 5)
        lb, sb = load_option_bars(lt, date), load_option_bars(st, date)
        if not lb or not sb: return None
        le, lx = find_bar(lb, entry_mins), find_bar(lb, exit_mins)
        se, sx = find_bar(sb, entry_mins), find_bar(sb, exit_mins)
        if not all([le, lx, se, sx]): return None
        debit = le['c'] - se['c']
        credit = lx['c'] - sx['c']
        return (credit - debit) * 100

    elif struct == 'bear_call_spread':
        st = build_ticker(date, 'C', atm)
        lt = build_ticker(date, 'C', atm + 5)
        sb, lb = load_option_bars(st, date), load_option_bars(lt, date)
        if not sb or not lb: return None
        se, sx = find_bar(sb, entry_mins), find_bar(sb, exit_mins)
        le, lx = find_bar(lb, entry_mins), find_bar(lb, exit_mins)
        if not all([se, sx, le, lx]): return None
        credit = se['c'] - le['c']
        debit = sx['c'] - lx['c']
        return (credit - debit) * 100

    elif struct == 'bear_put_spread':
        lt = build_ticker(date, 'P', atm)
        st = build_ticker(date, 'P', atm - 5)
        lb, sb = load_option_bars(lt, date), load_option_bars(st, date)
        if not lb or not sb: return None
        le, lx = find_bar(lb, entry_mins), find_bar(lb, exit_mins)
        se, sx = find_bar(sb, entry_mins), find_bar(sb, exit_mins)
        if not all([le, lx, se, sx]): return None
        debit = le['c'] - se['c']
        credit = lx['c'] - sx['c']
        return (credit - debit) * 100

    return None

# ─── Selected edges to price ───
EDGES_TO_PRICE = [
    # Bullish edges → long call, OTM call, bull call spread
    {'file': 'deep_01_StrongBody_Bull_VeryLow_Scalp_3_2_5.csv',
     'name': 'StrongBody_Bull_VeryLow_Scalp325', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_02_StrongBody_Bull_lt20_Scalp_5_2_5.csv',
     'name': 'StrongBody_Bull_lt20_Scalp525', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_06_StrongBody_Bull_lt20_Scalp_5_3_10.csv',
     'name': 'StrongBody_Bull_lt20_Scalp5310', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_09_NarrowBreak_Bull_Falling_Med_15_10_60.csv',
     'name': 'NarrowBreak_Bull_Falling', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_11_OR15_Retest_Bull_Mid_Med_10_5_30.csv',
     'name': 'OR15_Retest_Bull_Mid', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_14_PrevHigh_Cont_lt20_Adpt_10_5_30.csv',
     'name': 'PrevHigh_Cont_lt20', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    {'file': 'deep_20_TwoDayMom_Long_lt20_Pct_20_10_15.csv',
     'name': 'TwoDayMom_Long_lt20', 'dir': 1,
     'structs': ['long_call', 'long_otm_call', 'bull_call_spread']},
    # Bearish / Short edges → long put, bear call spread
    {'file': 'deep_24_TwoDayMR_Short_High_Trail05_SL5_30.csv',
     'name': 'TwoDayMR_Short_High', 'dir': -1,
     'structs': ['long_put', 'long_otm_put', 'bear_call_spread']},
    {'file': 'deep_27_PMCont_Short_Mid_Med_10_5_30.csv',
     'name': 'PMCont_Short_Mid', 'dir': -1,
     'structs': ['long_put', 'long_otm_put', 'bear_call_spread', 'bear_put_spread']},
]

def time_to_mins(t):
    parts = t.split(':')
    return int(parts[0]) * 60 + int(parts[1])

def compute_opt_stats(pnl_list, label):
    n = len(pnl_list)
    if n < 5: return None
    avg = statistics.mean(pnl_list)
    tot = sum(pnl_list)
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    wr = len(wins) / n * 100
    gw, gl = sum(wins), abs(sum(losses))
    pf = gw / gl if gl > 0 else 99
    std = statistics.stdev(pnl_list) if n > 1 else 0
    sh = avg / std if std > 0 else 0
    cum = 0; pk = 0; mdd = 0
    for p in pnl_list:
        cum += p
        if cum > pk: pk = cum
        dd = pk - cum
        if dd > mdd: mdd = dd

    # R²
    cum_list = []
    c = 0
    for p in pnl_list:
        c += p; cum_list.append(c)
    x_m = (n-1)/2; y_m = statistics.mean(cum_list)
    ss_xy = sum((i - x_m)*(y - y_m) for i, y in enumerate(cum_list))
    ss_xx = sum((i - x_m)**2 for i in range(n))
    ss_yy = sum((y - y_m)**2 for y in cum_list)
    r2 = (ss_xy**2)/(ss_xx*ss_yy) if ss_xx > 0 and ss_yy > 0 else 0

    return {
        'label': label, 'n': n, 'wr': round(wr,1),
        'avg_pnl': round(avg,2), 'total_pnl': round(tot,2),
        'sharpe': round(sh,3), 'pf': round(pf,2),
        'max_dd': round(mdd,2), 'r2': round(r2,3),
    }

def main():
    print("=" * 160)
    print("PRICING DEEP EDGES WITH REAL SPXW OPTIONS")
    print("=" * 160)

    results_dir = SCRIPT_DIR / 'backtest_results'
    all_results = []
    all_trade_pnl = {}  # for portfolio analysis

    for edge in EDGES_TO_PRICE:
        csv_path = results_dir / edge['file']
        if not csv_path.exists():
            print(f"\n  ⚠ Missing: {edge['file']}")
            continue

        trades = []
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                trades.append(row)

        print(f"\n{'─'*160}")
        print(f"  {edge['name']} — {len(trades)} trades, dir={edge['dir']}")

        for struct in edge['structs']:
            priced = []
            missed = 0
            details = []

            for trade in trades:
                d = trade['date']
                entry_mins = time_to_mins(trade['entry_time'])
                exit_mins = time_to_mins(trade['exit_time'])
                spx_price = float(trade['entry_price'])
                direction = int(trade['direction'])

                pnl = price_trade(d, entry_mins, exit_mins, spx_price, direction, struct)
                if pnl is not None:
                    priced.append(pnl)
                    details.append({'date': d, 'opt_pnl': pnl, 'und_pts': float(trade['und_pts'])})
                else:
                    missed += 1

            stats = compute_opt_stats(priced, f"{edge['name']}|{struct}")
            if stats:
                # OOS split
                is_pnl = [d['opt_pnl'] for d in details if d['date'] < '2023-01-01']
                oos_pnl = [d['opt_pnl'] for d in details if d['date'] >= '2023-01-01']
                is_sh = statistics.mean(is_pnl)/statistics.stdev(is_pnl) if len(is_pnl)>5 and statistics.stdev(is_pnl)>0 else 0
                oos_sh = statistics.mean(oos_pnl)/statistics.stdev(oos_pnl) if len(oos_pnl)>5 and statistics.stdev(oos_pnl)>0 else 0
                stats['is_sh'] = round(is_sh,3)
                stats['oos_sh'] = round(oos_sh,3)
                stats['priced_pct'] = round(len(priced)/(len(priced)+missed)*100, 1)

                v = "HOLDS" if oos_sh > 0.05 else "WEAK"
                print(f"    {struct:25s}  N={stats['n']:>4} ({stats['priced_pct']:.0f}%)  "
                      f"WR={stats['wr']:>5.1f}%  Avg=${stats['avg_pnl']:>+8.2f}  "
                      f"Tot=${stats['total_pnl']:>+10.2f}  Sh={stats['sharpe']:>6.3f}  "
                      f"PF={stats['pf']:>5.2f}  DD=${stats['max_dd']:>8.2f}  R²={stats['r2']:.3f}  "
                      f"IS={is_sh:.3f}  OOS={oos_sh:.3f}  [{v}]")

                all_results.append(stats)
                # Store for portfolio
                key = f"{edge['name']}|{struct}"
                all_trade_pnl[key] = {d['date']: d['opt_pnl'] for d in details}
            else:
                print(f"    {struct:25s}  {len(priced)} priced ({missed} missed) — insufficient")

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\n\n{'='*180}")
    print("FINAL RANKING — Option-Priced Deep Edges by Sharpe")
    print(f"{'='*180}")
    print(f"{'Label':>50s} {'N':>5} {'%':>5} {'WR%':>6} {'AvgPnL':>10} {'TotPnL':>12} "
          f"{'Sharpe':>7} {'PF':>6} {'MaxDD':>10} {'R²':>5} {'IS':>7} {'OOS':>7}")
    print("-" * 180)
    for r in all_results:
        print(f"{r['label'][:50]:>50s} {r['n']:>5} {r.get('priced_pct',0):>4.0f}% {r['wr']:>5.1f}% "
              f"${r['avg_pnl']:>+9.2f} ${r['total_pnl']:>+11.2f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} ${r['max_dd']:>9.2f} "
              f"{r['r2']:>5.3f} {r.get('is_sh',0):>7.3f} {r.get('oos_sh',0):>7.3f}")

    # Save
    with open(results_dir / 'deep_option_priced.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # ═══════════════════════════════════════════════════════════════
    # PORTFOLIO COMBOS
    # ═══════════════════════════════════════════════════════════════
    if len(all_trade_pnl) >= 2:
        print(f"\n{'='*160}")
        print("PORTFOLIO COMBINATIONS — Diversified by signal type")
        print(f"{'='*160}")

        keys = list(all_trade_pnl.keys())

        for sz in [2, 3, 4, 5]:
            if len(keys) < sz: continue
            best = []
            for combo in combinations(keys, sz):
                # Require different signal types
                sigs = set(k.split('|')[0] for k in combo)
                if len(sigs) < sz: continue

                all_dates = set()
                for k in combo: all_dates.update(all_trade_pnl[k].keys())
                daily = []
                for d in sorted(all_dates):
                    daily.append(sum(all_trade_pnl[k].get(d, 0) for k in combo))
                if len(daily) < 30: continue

                avg = statistics.mean(daily)
                std = statistics.stdev(daily)
                sh = avg / std if std > 0 else 0
                tot = sum(daily)
                wr = sum(1 for p in daily if p > 0) / len(daily) * 100
                cum = 0; pk = 0; mdd = 0
                for p in daily:
                    cum += p
                    if cum > pk: pk = cum
                    dd = pk - cum
                    if dd > mdd: mdd = dd

                best.append({
                    'combo': combo, 'sharpe': sh, 'total': tot,
                    'wr': wr, 'days': len(daily), 'max_dd': mdd,
                })

            best.sort(key=lambda x: x['sharpe'], reverse=True)
            print(f"\n  ── {sz}-Strategy Combos (top 5) ──")
            for i, c in enumerate(best[:5]):
                print(f"    #{i+1}  Sh={c['sharpe']:.3f}  Tot=${c['total']:+,.2f}  "
                      f"WR={c['wr']:.1f}%  DD=${c['max_dd']:,.2f}  Days={c['days']}")
                for k in c['combo']:
                    print(f"         → {k}")

    print(f"\n{'='*120}")
    print("DONE")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
