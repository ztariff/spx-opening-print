#!/usr/bin/env python3
"""
Morning Rip Fade — BEAR CALL SPREAD Backtest with REAL SPXW Prices
===================================================================
Strategy: When SPX rallies fast in the opening minutes with VIX elevated,
sell a bear call spread (sell near-ATM call, buy higher call for protection).

Spread structures tested:
  - 5-wide, 10-wide, 15-wide, 20-wide, 25-wide
  - Short leg: ATM, ATM+5, ATM+10
  - Various hold periods and stops

Run:  python3 44_morning_rip_spread_backtest.py
"""

import urllib.request, json, time, csv, sys, os
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# ── CONFIG ──
API_KEY = os.environ.get("POLYGON_API_KEY", "")
SCRIPT_DIR = Path(__file__).parent.resolve()
OPT_CACHE_DIR = SCRIPT_DIR / 'options_cache'
OPT_CACHE_DIR.mkdir(exist_ok=True)

SPX_CSV = SCRIPT_DIR / 'spx_1min_bars.csv'
VIX_CSV = SCRIPT_DIR / 'vix_daily_bars.csv'
OUT_FILE = SCRIPT_DIR / 'morning_rip_spread_results.json'

START_DATE = '2018-01-03'
MIN_TRADES = 3

_last_call = 0
RATE_DELAY = 0.15


def polygon_get(url, retries=3):
    global _last_call
    wait = RATE_DELAY - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries):
        try:
            _last_call = time.time()
            req = urllib.request.Request(url, headers={'User-Agent': 'Python/Backtest'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if '429' in str(e):
                time.sleep(5)
            elif attempt < retries - 1:
                time.sleep(2)
            else:
                return None
    return None


def fetch_option_bars(date_str, strike, opt_type='C'):
    parts = date_str.split('-')
    yy, mm, dd = parts[0][2:], parts[1], parts[2]
    sc = str(int(strike * 1000)).zfill(8)
    ticker = f"O:SPXW{yy}{mm}{dd}{opt_type}{sc}"

    key = f"{ticker}_{date_str}".replace(':', '_')
    cache = OPT_CACHE_DIR / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute"
           f"/{date_str}/{date_str}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    data = polygon_get(url)
    if data and data.get('results'):
        cache.write_text(json.dumps(data['results']))
        return data['results']
    cache.write_text('[]')
    return []


def _dst_off(utc):
    y = utc.year
    mar1 = datetime(y, 3, 1)
    mar_sun2 = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(y, 11, 1)
    nov_sun1 = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    if mar_sun2.replace(hour=7) <= utc < nov_sun1.replace(hour=6):
        return timedelta(hours=-4)
    return timedelta(hours=-5)


def ts_et(ms):
    utc = datetime.utcfromtimestamp(ms / 1000)
    return utc + _dst_off(utc)


def et_m(et):
    return et.hour * 60 + et.minute


def time_to_mins(t_str):
    return int(t_str[:2]) * 60 + int(t_str[3:5])


def load_spx():
    print(f"Loading {SPX_CSV} …")
    by_date = defaultdict(list)
    with open(SPX_CSV) as f:
        for row in csv.DictReader(f):
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960:
                continue
            by_date[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
                'idx': mins - 570,
            })
    for d in by_date:
        by_date[d].sort(key=lambda x: x['mins'])
    print(f"  {len(by_date)} days loaded")
    return by_date


def load_vix():
    vix = {}
    with open(VIX_CSV) as f:
        for row in csv.DictReader(f):
            vix[row['date']] = {
                'open': float(row['open']),
                'close': float(row['close']),
            }
    return vix


def index_opt_bars(opt_bars):
    idx = {}
    for bar in opt_bars:
        et = ts_et(bar['t'])
        mins = et_m(et)
        idx[mins] = bar
    return idx


def find_opt_bar(opt_bars, target_mins):
    if not opt_bars:
        return None
    best = None
    best_diff = 9999
    for bar in opt_bars:
        et = ts_et(bar['t'])
        mins = et_m(et)
        diff = abs(mins - target_mins)
        if diff < best_diff:
            best_diff = diff
            best = bar
    return best if best_diff <= 3 else None


# ── Find morning rip entries ──
def find_entries(spx_data, vix_data):
    entries = []
    for date_str in sorted(spx_data.keys()):
        if date_str < START_DATE:
            continue
        bars = spx_data[date_str]
        if len(bars) < 60:
            continue

        vd = vix_data.get(date_str)
        if not vd:
            continue

        rolling_low = bars[0]['l']
        trough_idx = 0

        for i, bar in enumerate(bars):
            if bar['l'] < rolling_low:
                rolling_low = bar['l']
                trough_idx = i

            rally_pct = (bar['c'] - rolling_low) / rolling_low * 100
            elapsed = i - trough_idx
            vel = rally_pct / elapsed if elapsed > 0 else 0

            # Very loose filter: rally >= 0.15%, any vel, first 30 min
            if bar['idx'] <= 30 and rally_pct >= 0.15:
                entries.append({
                    'date': date_str,
                    'entry_idx': i,
                    'entry_time': bar['time'],
                    'entry_price': bar['c'],
                    'rolling_low': rolling_low,
                    'rally_pct': rally_pct,
                    'velocity': vel,
                    'elapsed': elapsed,
                    'vix_open': vd['open'],
                    'bars': bars,
                })
                break

    return entries


# ── Fetch all needed option chains ──
def fetch_all_options(entries):
    """Pre-fetch call option bars for short and long legs."""
    # Need strikes from ATM-5 to ATM+35 to cover all spread combos
    offsets = list(range(-5, 40, 5))  # -5, 0, 5, 10, 15, 20, 25, 30, 35
    opt_data = {}
    new_fetches = 0

    for i, entry in enumerate(entries):
        if i % 25 == 0:
            print(f"  [{i}/{len(entries)}] {entry['date']}…", flush=True)

        atm = round(entry['entry_price'] / 5) * 5

        for off in offsets:
            strike = atm + off
            k = (entry['date'], strike, 'C')
            if k not in opt_data:
                bars = fetch_option_bars(entry['date'], strike, 'C')
                opt_data[k] = bars
                if not (OPT_CACHE_DIR / f"O_SPXW{entry['date'].replace('-','')[2:]}C{str(int(strike*1000)).zfill(8)}_{entry['date']}.json").exists():
                    new_fetches += 1

    print(f"  {len(opt_data)} option chains loaded, {new_fetches} new API calls")
    return opt_data


# ── Bear Call Spread runner ──
def run_bear_call_spread(entries, opt_data, params):
    short_off = params['short_off']     # offset from ATM for short leg
    spread_width = params['spread_width']  # distance between legs
    max_hold = params['max_hold']
    sl_mult = params.get('sl_mult', 2.0)  # stop at N x max_loss
    profit_target = params.get('profit_target', 0.80)  # close at X% of max credit
    min_rally = params['min_rally']
    min_vel = params['min_vel']
    vix_min = params.get('vix_min', 0)
    vix_max = params.get('vix_max', 99)
    win_end = params.get('win_end', 30)

    trades = []

    for entry in entries:
        if entry['rally_pct'] < min_rally:
            continue
        if entry['velocity'] < min_vel:
            continue
        if entry['vix_open'] < vix_min or entry['vix_open'] > vix_max:
            continue
        if entry['bars'][entry['entry_idx']]['idx'] > win_end:
            continue

        atm = round(entry['entry_price'] / 5) * 5
        short_strike = atm + short_off
        long_strike = short_strike + spread_width

        sk = (entry['date'], short_strike, 'C')
        lk = (entry['date'], long_strike, 'C')
        short_bars = opt_data.get(sk, [])
        long_bars = opt_data.get(lk, [])
        if not short_bars or not long_bars:
            continue

        entry_mins = time_to_mins(entry['entry_time'])
        short_entry = find_opt_bar(short_bars, entry_mins)
        long_entry = find_opt_bar(long_bars, entry_mins)
        if not short_entry or not long_entry:
            continue

        credit = short_entry['c'] - long_entry['c']
        if credit <= 0.05:
            continue

        max_loss = (spread_width - credit) * 100  # per contract
        max_profit = credit * 100

        # Size: risk $50k per trade
        contracts = max(1, min(50, round(50000 / max(1, max_loss))))

        bars = entry['bars']
        eidx = entry['entry_idx']

        short_idx = index_opt_bars(short_bars)
        long_idx = index_opt_bars(long_bars)

        exit_reason = 'hold_expired'
        exit_credit = credit
        exit_time = entry['entry_time']
        exit_spx = entry['entry_price']
        hold_mins = 0
        peak_profit_pct = 0

        for j in range(eidx + 1, min(eidx + max_hold + 1, len(bars))):
            bar_mins = bars[j]['mins']

            sb = short_idx.get(bar_mins) or short_idx.get(bar_mins+1) or short_idx.get(bar_mins-1)
            lb = long_idx.get(bar_mins) or long_idx.get(bar_mins+1) or long_idx.get(bar_mins-1)
            if not sb or not lb:
                continue

            cur_spread = sb['c'] - lb['c']
            cur_pnl_per = (credit - cur_spread) * 100
            cur_profit_pct = cur_pnl_per / max_profit * 100 if max_profit > 0 else 0

            if cur_profit_pct > peak_profit_pct:
                peak_profit_pct = cur_profit_pct

            # Profit target: spread narrowed enough
            if profit_target > 0 and cur_profit_pct >= profit_target * 100:
                exit_reason = 'profit_target'
                exit_credit = cur_spread
                exit_time = bars[j]['time']
                exit_spx = bars[j]['c']
                hold_mins = j - eidx
                break

            # Stop loss: spread widened past sl_mult x credit
            cur_loss = (cur_spread - credit) * 100
            if sl_mult > 0 and cur_loss >= credit * 100 * sl_mult:
                exit_reason = 'stop_loss'
                exit_credit = cur_spread
                exit_time = bars[j]['time']
                exit_spx = bars[j]['c']
                hold_mins = j - eidx
                break

            exit_credit = cur_spread
            exit_time = bars[j]['time']
            exit_spx = bars[j]['c']
            hold_mins = j - eidx

        pnl_per = (credit - exit_credit) * 100
        total_pnl = round(pnl_per * contracts)

        trades.append({
            'date': entry['date'],
            'entry_time': entry['entry_time'],
            'exit_time': exit_time,
            'entry_spx': round(entry['entry_price'], 2),
            'exit_spx': round(exit_spx, 2),
            'short_strike': short_strike,
            'long_strike': long_strike,
            'credit': round(credit, 2),
            'exit_spread': round(exit_credit, 2),
            'pnl': total_pnl,
            'pnl_pct': round(pnl_per / max(0.01, credit) * 100, 1),
            'contracts': contracts,
            'max_risk': round(max_loss * contracts),
            'hold_mins': hold_mins,
            'exit_reason': exit_reason,
            'peak_profit_pct': round(peak_profit_pct, 1),
            'rally_pct': round(entry['rally_pct'], 3),
            'velocity': round(entry['velocity'], 4),
            'vix_open': round(entry['vix_open'], 1),
            'spx_move': round(exit_spx - entry['entry_price'], 2),
        })

    return trades


def compute_stats(trades):
    if not trades:
        return None
    n = len(trades)
    pnls = [t['pnl'] for t in trades]
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    stopped = [t for t in trades if t['exit_reason'] == 'stop_loss']
    profit_tgt = [t for t in trades if t['exit_reason'] == 'profit_target']
    avg_pnl = sum(pnls) / n
    wr = len(wins) / n * 100

    if n > 1:
        var = sum((p - avg_pnl) ** 2 for p in pnls) / (n - 1)
        sharpe = avg_pnl / (var ** 0.5) if var > 0 else 0
    else:
        sharpe = 0

    avg_credit = sum(t['credit'] for t in trades) / n
    avg_hold = sum(t['hold_mins'] for t in trades) / n

    return {
        'n': n,
        'win_rate': round(wr, 1),
        'avg_pnl': round(avg_pnl),
        'total_pnl': round(sum(pnls)),
        'avg_win': round(sum(t['pnl'] for t in wins) / len(wins)) if wins else 0,
        'avg_loss': round(sum(t['pnl'] for t in losses) / len(losses)) if losses else 0,
        'avg_credit': round(avg_credit, 2),
        'avg_hold': round(avg_hold, 1),
        'stop_rate': round(len(stopped) / n * 100, 1),
        'target_rate': round(len(profit_tgt) / n * 100, 1),
        'sharpe': round(sharpe, 3),
    }


def main():
    print("=" * 120)
    print("MORNING RIP FADE — BEAR CALL SPREAD BACKTEST with REAL SPXW PRICES")
    print("=" * 120)

    spx_data = load_spx()
    vix_data = load_vix()

    print("\n" + "=" * 80)
    print("PHASE 1: Finding morning rip entries")
    print("=" * 80)

    entries = find_entries(spx_data, vix_data)
    print(f"Total entries (loose): {len(entries)}")

    # Show distribution
    for lo, hi, label in [(0,18,'<18'), (18,25,'18-25'), (25,30,'25-30'),
                           (30,35,'30-35'), (35,99,'35+')]:
        n = len([e for e in entries if lo <= e['vix_open'] < hi])
        print(f"  VIX {label}: {n}")

    print("\n" + "=" * 80)
    print("PHASE 2: Fetching option prices from Polygon")
    print("=" * 80)

    opt_data = fetch_all_options(entries)

    print("\n" + "=" * 80)
    print("PHASE 3: Running bear call spread configurations")
    print("=" * 80)

    all_results = []
    count = 0

    # v3: Find the degradation cliff. Push into untested territory.
    # Fix spread structure to best: 5-wide, short=+0, PT=50%, hold=60, SL=3x
    # Sweep ONLY the signal filters to find exact boundaries.
    for min_rally in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.70]:
        for min_vel in [0, 0.02, 0.03, 0.05, 0.08, 0.10]:
            for vix_min, vix_max, vix_label in [(0,15,'<15'), (15,18,'15-18'),
                                                  (15,20,'15-20'), (18,20,'18-20'),
                                                  (15,25,'15-25'), (18,25,'18-25'),
                                                  (20,25,'20-25'), (20,30,'20-30'),
                                                  (25,35,'25-35'),
                                                  (0,99,'all'), (15,99,'15+'),
                                                  (18,99,'18+'), (20,99,'20+'),
                                                  (25,99,'25+'), (30,99,'30+')]:
                for win_end in [15, 30]:
                    for short_off in [0]:
                        for spread_width in [5]:
                            for sl_mult in [3.0]:
                                for profit_target in [0.50]:
                                    for max_hold in [60]:
                                        count += 1
                                        params = {
                                            'short_off': short_off,
                                            'spread_width': spread_width,
                                            'max_hold': max_hold,
                                            'sl_mult': sl_mult,
                                            'profit_target': profit_target,
                                            'min_rally': min_rally,
                                            'min_vel': min_vel,
                                            'vix_min': vix_min,
                                            'vix_max': vix_max,
                                            'win_end': win_end,
                                        }
                                        trades = run_bear_call_spread(entries, opt_data, params)
                                        stats = compute_stats(trades)
                                        if stats and stats['n'] >= MIN_TRADES:
                                            result = {
                                                'short_off': short_off,
                                                'spread_width': spread_width,
                                                'min_rally': min_rally,
                                                'min_vel': min_vel,
                                                'vix_range': vix_label,
                                                'win_end': win_end,
                                                'sl_mult': sl_mult,
                                                'profit_target': profit_target,
                                                'max_hold': max_hold,
                                                **stats,
                                            }
                                            if stats['n'] >= 8 and stats['win_rate'] >= 55:
                                                result['trades'] = trades
                                            all_results.append(result)

    print(f"\n  Swept {count} combos, {len(all_results)} with >= {MIN_TRADES} trades")

    # Sort and display
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    sig = [r for r in all_results if r['n'] >= 10]
    sig.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*140}")
    print(f"TOP 30 BY SHARPE (min 10 trades)")
    print(f"{'='*140}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>6} {'Win':>4} {'Short':>5} {'Width':>5} "
          f"{'SLx':>4} {'PT':>4} {'Hold':>4} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgCr':>6} {'AvgHld':>6} "
          f"{'StopR':>5} {'TgtR':>5} {'Sharpe':>7}")
    print("-" * 140)

    for r in sig[:30]:
        pt = f"{r['profit_target']:.0%}" if r['profit_target'] > 0 else 'none'
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>6} "
              f"{r['win_end']:>4} {r['short_off']:>+5} {r['spread_width']:>5} "
              f"{r['sl_mult']:>4.1f} {pt:>4} {r['max_hold']:>4} "
              f"{r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} ${r['avg_credit']:>5.2f} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['target_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # High-N results
    high_n = [r for r in all_results if r['n'] >= 15]
    high_n.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*140}")
    print(f"TOP 30 BY SHARPE (min 15 trades — higher confidence)")
    print(f"{'='*140}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>6} {'Win':>4} {'Short':>5} {'Width':>5} "
          f"{'SLx':>4} {'PT':>4} {'Hold':>4} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgCr':>6} {'AvgHld':>6} "
          f"{'StopR':>5} {'TgtR':>5} {'Sharpe':>7}")
    print("-" * 140)

    for r in high_n[:30]:
        pt = f"{r['profit_target']:.0%}" if r['profit_target'] > 0 else 'none'
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>6} "
              f"{r['win_end']:>4} {r['short_off']:>+5} {r['spread_width']:>5} "
              f"{r['sl_mult']:>4.1f} {pt:>4} {r['max_hold']:>4} "
              f"{r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} ${r['avg_credit']:>5.2f} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['target_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # Highest total PnL
    by_pnl = [r for r in all_results if r['n'] >= 10]
    by_pnl.sort(key=lambda x: x['total_pnl'], reverse=True)

    print(f"\n{'='*140}")
    print(f"TOP 20 BY TOTAL PNL (min 10 trades)")
    print(f"{'='*140}")
    for i, r in enumerate(by_pnl[:20]):
        pt = f"{r['profit_target']:.0%}" if r['profit_target'] > 0 else 'none'
        print(f"  {i+1:>2}. short={r['short_off']:+d} width={r['spread_width']} "
              f"rally>={r['min_rally']:.2f}% vel>={r['min_vel']:.2f} vix={r['vix_range']} "
              f"win<={r['win_end']}m hold={r['max_hold']}m SL={r['sl_mult']}x PT={pt}")
        print(f"      N={r['n']:>3} WR={r['win_rate']:>5.1f}% avgPnL=${r['avg_pnl']:>7,} "
              f"totPnL=${r['total_pnl']:>9,} avgCredit=${r['avg_credit']:.2f} "
              f"avgHold={r['avg_hold']:.0f}m Sharpe={r['sharpe']:.3f}")

    # Width analysis
    print(f"\n{'='*100}")
    print(f"SPREAD WIDTH ANALYSIS (best Sharpe per width, N>=10)")
    print(f"{'='*100}")
    for w in [5, 10, 15, 20, 25]:
        wr = [r for r in all_results if r['spread_width'] == w and r['n'] >= 10]
        if wr:
            wr.sort(key=lambda x: x['sharpe'], reverse=True)
            best = wr[0]
            profitable = len([r for r in wr if r['avg_pnl'] > 0])
            print(f"  Width={w:>2}: {len(wr)} configs, {profitable} profitable ({profitable/len(wr)*100:.0f}%)")
            pt = f"{best['profit_target']:.0%}" if best['profit_target'] > 0 else 'none'
            print(f"    Best: short={best['short_off']:+d} rally>={best['min_rally']} vel>={best['min_vel']} "
                  f"vix={best['vix_range']} N={best['n']} WR={best['win_rate']}% "
                  f"avgPnL=${best['avg_pnl']:,} Sharpe={best['sharpe']}")

    # VIX analysis
    print(f"\n{'='*100}")
    print(f"VIX REGIME ANALYSIS (best Sharpe per regime, N>=10)")
    print(f"{'='*100}")
    for vl in ['all', '18-25', '25-35', '25+', '30+']:
        vr = [r for r in all_results if r['vix_range'] == vl and r['n'] >= 10]
        if vr:
            vr.sort(key=lambda x: x['sharpe'], reverse=True)
            best = vr[0]
            profitable = len([r for r in vr if r['avg_pnl'] > 0])
            print(f"  VIX {vl:>5}: {len(vr)} configs, {profitable} profitable ({profitable/len(vr)*100:.0f}%)")
            print(f"    Best: N={best['n']} WR={best['win_rate']}% avgPnL=${best['avg_pnl']:,} "
                  f"totPnL=${best['total_pnl']:,} Sharpe={best['sharpe']}")

    # Save
    save = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results]
    with open(OUT_FILE, 'w') as f:
        json.dump(save, f, indent=1)
    print(f"\nSaved {len(all_results)} configs to {OUT_FILE}")

    # Save trades for best config
    if sig and 'trades' in sig[0]:
        trades_csv = SCRIPT_DIR / 'morning_rip_spread_trades.csv'
        with open(trades_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=sig[0]['trades'][0].keys())
            w.writeheader()
            w.writerows(sig[0]['trades'])
        print(f"Saved best config trades to {trades_csv}")


if __name__ == '__main__':
    main()
