#!/usr/bin/env python3
"""
Morning Rip Fade — Options Backtest with REAL SPXW Prices
=========================================================
Strategy: When SPX rallies >= X% from rolling low in first 30 min
with velocity >= Y%/min and VIX in 25-35, buy 0DTE puts to fade the rip.

Uses Polygon for real SPXW option 1-min bars.
Caches everything in options_cache/ for re-runs.

Run:  python3 43_morning_rip_options_backtest.py
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
OUT_FILE = SCRIPT_DIR / 'morning_rip_options_results.json'

START_DATE = '2018-01-03'
MIN_TRADES = 3

# Rate limiter
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
                print(f"    rate-limited, sleeping 5s …")
                time.sleep(5)
            elif attempt < retries - 1:
                time.sleep(2)
            else:
                return None
    return None


def fetch_option_bars(date_str, strike, opt_type='P'):
    """Fetch REAL 0DTE SPXW option 1-min bars from Polygon."""
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


# ── Time helpers ──
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


# ── Load local data ──
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


# ── Find closest option bar ──
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


# ── Build option bar index by minute for fast lookup ──
def index_opt_bars(opt_bars):
    """Return dict: minute_of_day -> bar"""
    idx = {}
    for bar in opt_bars:
        et = ts_et(bar['t'])
        mins = et_m(et)
        idx[mins] = bar
    return idx


# ══════════════════════════════════════════════════════════════
# PHASE 1: Identify morning rip entries
# ══════════════════════════════════════════════════════════════
def find_entries(spx_data, vix_data):
    """Find all days where SPX had a fast morning rip.
    Uses the LOOSEST filters — we'll tighten in the sweep."""
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
        vix_open = vd['open']

        # Track rolling low from open
        rolling_low = bars[0]['l']
        trough_idx = 0

        for i, bar in enumerate(bars):
            if bar['l'] < rolling_low:
                rolling_low = bar['l']
                trough_idx = i

            rally_pct = (bar['c'] - rolling_low) / rolling_low * 100
            elapsed = i - trough_idx
            vel = rally_pct / elapsed if elapsed > 0 else 0

            # Loosest filter: rally >= 0.40%, any velocity, first 2 hours
            if (bar['idx'] <= 120
                and rally_pct >= 0.40
                and vel >= 0.03):

                entries.append({
                    'date': date_str,
                    'entry_idx': i,
                    'entry_time': bar['time'],
                    'entry_price': bar['c'],
                    'entry_high': bar['h'],
                    'rolling_low': rolling_low,
                    'rally_pct': rally_pct,
                    'velocity': vel,
                    'elapsed': elapsed,
                    'vix_open': vix_open,
                    'bars': bars,
                })
                break  # One entry per day

    return entries


# ══════════════════════════════════════════════════════════════
# PHASE 2: Fetch option prices for all entries
# ══════════════════════════════════════════════════════════════
def fetch_all_options(entries):
    """Pre-fetch put option bars for all entries at various strikes."""
    STRIKE_OFFSETS = [0, -5, -10, 5, 10]  # ATM and nearby
    opt_data = {}
    fetched = 0
    cached = 0

    for i, entry in enumerate(entries):
        if i % 25 == 0:
            print(f"  [{i}/{len(entries)}] Fetching options for {entry['date']}…", flush=True)

        # Round entry price to nearest 5 for strike selection
        atm = round(entry['entry_price'] / 5) * 5

        for off in STRIKE_OFFSETS:
            strike = atm + off
            k = (entry['date'], strike, 'P')
            if k not in opt_data:
                bars = fetch_option_bars(entry['date'], strike, 'P')
                opt_data[k] = bars
                if bars:
                    fetched += 1
                else:
                    cached += 1

            # Also fetch calls (for call selling strategy)
            kc = (entry['date'], strike, 'C')
            if kc not in opt_data:
                bars = fetch_option_bars(entry['date'], strike, 'C')
                opt_data[kc] = bars
                if bars:
                    fetched += 1

    print(f"  Fetched options: {fetched} new, {len(opt_data)} total in cache")
    return opt_data


# ══════════════════════════════════════════════════════════════
# PHASE 3: Strategy runners
# ══════════════════════════════════════════════════════════════

def run_long_put(entries, opt_data, params):
    """Buy ATM or OTM puts to fade the rip. Trail stop or time exit."""
    strike_off = params['strike_off']
    trail_pct = params['trail_pct']
    sl_pct = params['sl_pct']
    max_hold = params['max_hold']
    min_rally = params['min_rally']
    min_vel = params['min_vel']
    vix_min = params.get('vix_min', 0)
    vix_max = params.get('vix_max', 99)
    win_end = params.get('win_end', 30)

    trades = []

    for entry in entries:
        # Apply filters
        if entry['rally_pct'] < min_rally:
            continue
        if entry['velocity'] < min_vel:
            continue
        if entry['vix_open'] < vix_min or entry['vix_open'] > vix_max:
            continue
        if entry['bars'][entry['entry_idx']]['idx'] > win_end:
            continue

        atm = round(entry['entry_price'] / 5) * 5
        strike = atm + strike_off
        k = (entry['date'], strike, 'P')
        put_bars = opt_data.get(k, [])
        if not put_bars:
            continue

        entry_mins = time_to_mins(entry['entry_time'])
        entry_opt = find_opt_bar(put_bars, entry_mins)
        if not entry_opt or entry_opt['c'] < 0.20:
            continue

        bought_price = entry_opt['c']
        peak_price = bought_price
        bars = entry['bars']
        eidx = entry['entry_idx']

        exit_reason = 'hold_expired'
        exit_opt_price = bought_price
        exit_time = entry['entry_time']
        exit_spx = entry['entry_price']
        hold_mins = 0

        opt_idx = index_opt_bars(put_bars)

        for j in range(eidx + 1, min(eidx + max_hold + 1, len(bars))):
            bar_mins = bars[j]['mins']
            ob = opt_idx.get(bar_mins)
            if not ob:
                # Try +/-1 minute
                ob = opt_idx.get(bar_mins + 1) or opt_idx.get(bar_mins - 1)
            if not ob:
                continue

            cur_price = ob['c']
            if ob['h'] > peak_price:
                peak_price = ob['h']

            # Trail stop: option price drops trail_pct% from peak
            if peak_price > bought_price and trail_pct > 0:
                trail_level = peak_price * (1 - trail_pct / 100)
                if cur_price <= trail_level:
                    exit_reason = 'trail_stop'
                    exit_opt_price = cur_price
                    exit_time = bars[j]['time']
                    exit_spx = bars[j]['c']
                    hold_mins = j - eidx
                    break

            # Hard stop loss: option price drops sl_pct% from entry
            if sl_pct > 0:
                sl_level = bought_price * (1 - sl_pct / 100)
                if cur_price <= sl_level:
                    exit_reason = 'stop_loss'
                    exit_opt_price = cur_price
                    exit_time = bars[j]['time']
                    exit_spx = bars[j]['c']
                    hold_mins = j - eidx
                    break

            exit_opt_price = cur_price
            exit_time = bars[j]['time']
            exit_spx = bars[j]['c']
            hold_mins = j - eidx

        pnl_per_contract = (exit_opt_price - bought_price) * 100
        contracts = max(1, min(30, round(50000 / (bought_price * 100))))
        total_pnl = round(pnl_per_contract * contracts)

        trades.append({
            'date': entry['date'],
            'entry_time': entry['entry_time'],
            'exit_time': exit_time,
            'entry_spx': round(entry['entry_price'], 2),
            'exit_spx': round(exit_spx, 2),
            'strike': strike,
            'opt_entry': round(bought_price, 2),
            'opt_exit': round(exit_opt_price, 2),
            'opt_peak': round(peak_price, 2),
            'pnl': total_pnl,
            'pnl_pct': round((exit_opt_price / bought_price - 1) * 100, 1),
            'contracts': contracts,
            'hold_mins': hold_mins,
            'exit_reason': exit_reason,
            'rally_pct': round(entry['rally_pct'], 3),
            'velocity': round(entry['velocity'], 4),
            'vix_open': round(entry['vix_open'], 1),
            'spx_move': round(exit_spx - entry['entry_price'], 2),
        })

    return trades


def run_short_call(entries, opt_data, params):
    """Sell ATM or OTM calls to fade the rip. Time or stop exit."""
    strike_off = params['strike_off']  # positive = OTM
    max_hold = params['max_hold']
    sl_mult = params.get('sl_mult', 2.0)  # stop at N x credit received
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
        strike = atm + strike_off
        k = (entry['date'], strike, 'C')
        call_bars = opt_data.get(k, [])
        if not call_bars:
            continue

        entry_mins = time_to_mins(entry['entry_time'])
        entry_opt = find_opt_bar(call_bars, entry_mins)
        if not entry_opt or entry_opt['c'] < 0.20:
            continue

        sold_price = entry_opt['c']
        bars = entry['bars']
        eidx = entry['entry_idx']

        exit_reason = 'hold_expired'
        exit_opt_price = sold_price
        exit_time = entry['entry_time']
        exit_spx = entry['entry_price']
        hold_mins = 0

        opt_idx = index_opt_bars(call_bars)

        for j in range(eidx + 1, min(eidx + max_hold + 1, len(bars))):
            bar_mins = bars[j]['mins']
            ob = opt_idx.get(bar_mins) or opt_idx.get(bar_mins + 1) or opt_idx.get(bar_mins - 1)
            if not ob:
                continue

            cur_price = ob['c']

            # Stop: option price rises to sl_mult x sold price
            if sl_mult > 0 and cur_price >= sold_price * sl_mult:
                exit_reason = 'stop_loss'
                exit_opt_price = cur_price
                exit_time = bars[j]['time']
                exit_spx = bars[j]['c']
                hold_mins = j - eidx
                break

            exit_opt_price = cur_price
            exit_time = bars[j]['time']
            exit_spx = bars[j]['c']
            hold_mins = j - eidx

        pnl_per_contract = (sold_price - exit_opt_price) * 100
        contracts = max(1, min(20, round(50000 / (sold_price * 100))))
        total_pnl = round(pnl_per_contract * contracts)

        trades.append({
            'date': entry['date'],
            'entry_time': entry['entry_time'],
            'exit_time': exit_time,
            'entry_spx': round(entry['entry_price'], 2),
            'exit_spx': round(exit_spx, 2),
            'strike': strike,
            'opt_entry': round(sold_price, 2),
            'opt_exit': round(exit_opt_price, 2),
            'pnl': total_pnl,
            'pnl_pct': round((sold_price - exit_opt_price) / sold_price * 100, 1),
            'contracts': contracts,
            'hold_mins': hold_mins,
            'exit_reason': exit_reason,
            'rally_pct': round(entry['rally_pct'], 3),
            'velocity': round(entry['velocity'], 4),
            'vix_open': round(entry['vix_open'], 1),
            'spx_move': round(exit_spx - entry['entry_price'], 2),
        })

    return trades


# ── Stats computation ──
def compute_stats(trades):
    if not trades:
        return None
    n = len(trades)
    pnls = [t['pnl'] for t in trades]
    wins = [t for t in trades if t['pnl'] > 0]
    avg_pnl = sum(pnls) / n
    wr = len(wins) / n * 100
    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    losses = [t for t in trades if t['pnl'] <= 0]
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    stopped = [t for t in trades if t['exit_reason'] == 'stop_loss']
    trailed = [t for t in trades if t['exit_reason'] == 'trail_stop']
    avg_hold = sum(t['hold_mins'] for t in trades) / n

    if n > 1:
        mean = avg_pnl
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = var ** 0.5
        sharpe = mean / std if std > 0 else 0
    else:
        sharpe = 0

    return {
        'n': n,
        'win_rate': round(wr, 1),
        'avg_pnl': round(avg_pnl),
        'total_pnl': round(sum(pnls)),
        'avg_win': round(avg_win),
        'avg_loss': round(avg_loss),
        'avg_hold': round(avg_hold, 1),
        'stop_rate': round(len(stopped) / n * 100, 1),
        'trail_rate': round(len(trailed) / n * 100, 1),
        'sharpe': round(sharpe, 3),
    }


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 120)
    print("MORNING RIP FADE — OPTIONS BACKTEST with REAL SPXW PRICES")
    print("=" * 120)

    spx_data = load_spx()
    vix_data = load_vix()

    # Phase 1: Find entries (loose filters)
    print("\n" + "=" * 80)
    print("PHASE 1: Finding morning rip entries")
    print("=" * 80)

    entries = find_entries(spx_data, vix_data)
    print(f"Total entries (loose): {len(entries)}")

    # Distribution
    print(f"\nBy VIX range:")
    for lo, hi, label in [(0,18,'<18'), (18,25,'18-25'), (25,35,'25-35'), (35,99,'35+')]:
        n = len([e for e in entries if lo <= e['vix_open'] < hi])
        print(f"  VIX {label}: {n}")

    print(f"\nBy rally threshold:")
    for mr in [0.40, 0.50, 0.60, 0.70, 0.80, 1.00]:
        n = len([e for e in entries if e['rally_pct'] >= mr])
        print(f"  Rally >= {mr:.2f}%: {n}")

    print(f"\nBy velocity:")
    for mv in [0.03, 0.05, 0.08, 0.10, 0.15]:
        n = len([e for e in entries if e['velocity'] >= mv])
        print(f"  Vel >= {mv}: {n}")

    # Phase 2: Fetch options
    print("\n" + "=" * 80)
    print("PHASE 2: Fetching option prices from Polygon")
    print("=" * 80)

    opt_data = fetch_all_options(entries)

    # Phase 3: Run strategy sweep
    print("\n" + "=" * 80)
    print("PHASE 3: Running strategy configurations")
    print("=" * 80)

    all_results = []

    # ── A) LONG PUT sweep ──
    print("\n  Running LONG PUT sweep …")
    lp_count = 0

    for min_rally in [0.50, 0.70, 1.00]:
        for min_vel in [0.05, 0.10, 0.15]:
            for vix_min, vix_max, vix_label in [(0,99,'all'), (18,25,'18-25'),
                                                  (25,35,'25-35'), (25,99,'25+'),
                                                  (30,99,'30+')]:
                for win_end in [15, 30, 60]:
                    for strike_off in [0, -5, -10]:  # ATM, 5 OTM, 10 OTM
                        for trail_pct in [15, 25, 40]:
                            for sl_pct in [30, 50, 70]:
                                for max_hold in [10, 15, 20, 30, 60]:
                                    params = {
                                        'strike_off': strike_off,
                                        'trail_pct': trail_pct,
                                        'sl_pct': sl_pct,
                                        'max_hold': max_hold,
                                        'min_rally': min_rally,
                                        'min_vel': min_vel,
                                        'vix_min': vix_min,
                                        'vix_max': vix_max,
                                        'win_end': win_end,
                                    }
                                    trades = run_long_put(entries, opt_data, params)
                                    stats = compute_stats(trades)
                                    if stats and stats['n'] >= MIN_TRADES:
                                        lp_count += 1
                                        result = {
                                            'strategy': 'long_put',
                                            'min_rally': min_rally,
                                            'min_vel': min_vel,
                                            'vix_range': vix_label,
                                            'win_end': win_end,
                                            'strike_off': strike_off,
                                            'trail_pct': trail_pct,
                                            'sl_pct': sl_pct,
                                            'max_hold': max_hold,
                                            **stats,
                                        }
                                        if stats['n'] >= 10 and stats['win_rate'] >= 50:
                                            result['trades'] = trades[:15]
                                        all_results.append(result)

    print(f"    {lp_count} long put configs with >= {MIN_TRADES} trades")

    # ── B) SHORT CALL sweep ──
    print("\n  Running SHORT CALL sweep …")
    sc_count = 0

    for min_rally in [0.50, 0.70, 1.00]:
        for min_vel in [0.05, 0.10, 0.15]:
            for vix_min, vix_max, vix_label in [(0,99,'all'), (18,25,'18-25'),
                                                  (25,35,'25-35'), (25,99,'25+'),
                                                  (30,99,'30+')]:
                for win_end in [15, 30, 60]:
                    for strike_off in [0, 5, 10]:  # ATM, 5 OTM, 10 OTM
                        for sl_mult in [1.5, 2.0, 3.0]:
                            for max_hold in [10, 15, 20, 30, 60]:
                                params = {
                                    'strike_off': strike_off,
                                    'sl_mult': sl_mult,
                                    'max_hold': max_hold,
                                    'min_rally': min_rally,
                                    'min_vel': min_vel,
                                    'vix_min': vix_min,
                                    'vix_max': vix_max,
                                    'win_end': win_end,
                                }
                                trades = run_short_call(entries, opt_data, params)
                                stats = compute_stats(trades)
                                if stats and stats['n'] >= MIN_TRADES:
                                    sc_count += 1
                                    result = {
                                        'strategy': 'short_call',
                                        'min_rally': min_rally,
                                        'min_vel': min_vel,
                                        'vix_range': vix_label,
                                        'win_end': win_end,
                                        'strike_off': strike_off,
                                        'sl_mult': sl_mult,
                                        'max_hold': max_hold,
                                        **stats,
                                    }
                                    if stats['n'] >= 10 and stats['win_rate'] >= 50:
                                        result['trades'] = trades[:15]
                                    all_results.append(result)

    print(f"    {sc_count} short call configs with >= {MIN_TRADES} trades")

    # ── Sort and display results ──
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    # LONG PUT results
    lp_results = [r for r in all_results if r['strategy'] == 'long_put']
    lp_sig = [r for r in lp_results if r['n'] >= 10]
    lp_sig.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*130}")
    print(f"LONG PUT — TOP 30 BY SHARPE (min 10 trades)")
    print(f"{'='*130}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>6} {'Win':>4} {'K':>4} {'Trail':>5} {'SL':>4} "
          f"{'Hold':>4} {'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgHld':>6} "
          f"{'StopR':>5} {'TrailR':>6} {'Sharpe':>7}")
    print("-" * 130)

    for r in lp_sig[:30]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>6} "
              f"{r['win_end']:>4} {r['strike_off']:>+4} {r['trail_pct']:>4}% {r['sl_pct']:>3}% "
              f"{r['max_hold']:>4} {r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['trail_rate']:>5.1f}% {r['sharpe']:>7.3f}")

    # SHORT CALL results
    sc_results = [r for r in all_results if r['strategy'] == 'short_call']
    sc_sig = [r for r in sc_results if r['n'] >= 10]
    sc_sig.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*120}")
    print(f"SHORT CALL — TOP 30 BY SHARPE (min 10 trades)")
    print(f"{'='*120}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>6} {'Win':>4} {'K':>4} {'SLx':>4} "
          f"{'Hold':>4} {'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgHld':>6} "
          f"{'StopR':>5} {'Sharpe':>7}")
    print("-" * 120)

    for r in sc_sig[:30]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>6} "
              f"{r['win_end']:>4} {r['strike_off']:>+4} {r['sl_mult']:>4.1f} "
              f"{r['max_hold']:>4} {r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # OVERALL top 20
    top20 = [r for r in all_results if r['n'] >= 10]
    top20.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*130}")
    print(f"OVERALL TOP 20 (any strategy, min 10 trades)")
    print(f"{'='*130}")
    for i, r in enumerate(top20[:20]):
        strat = r['strategy']
        extra = f"trail={r.get('trail_pct','-')}% sl={r.get('sl_pct',r.get('sl_mult','-'))}" if strat == 'long_put' \
            else f"sl_mult={r.get('sl_mult','-')}"
        print(f"  {i+1:>2}. [{strat:>10}] rally>={r['min_rally']:.2f}% vel>={r['min_vel']:.2f} "
              f"vix={r['vix_range']} win<={r['win_end']}min K={r.get('strike_off',0):+d} "
              f"hold={r['max_hold']}min {extra}")
        print(f"      N={r['n']:>3} WR={r['win_rate']:>5.1f}% avgPnL=${r['avg_pnl']:>7,} "
              f"totPnL=${r['total_pnl']:>9,} avgHold={r['avg_hold']:.0f}m "
              f"Sharpe={r['sharpe']:.3f}")

    # High-N confidence configs
    high_n = [r for r in all_results if r['n'] >= 20]
    high_n.sort(key=lambda x: x['total_pnl'], reverse=True)

    print(f"\n{'='*130}")
    print(f"HIGH CONFIDENCE — TOP 20 BY TOTAL PNL (min 20 trades)")
    print(f"{'='*130}")
    for i, r in enumerate(high_n[:20]):
        strat = r['strategy']
        extra = f"trail={r.get('trail_pct','-')}% sl={r.get('sl_pct',r.get('sl_mult','-'))}" if strat == 'long_put' \
            else f"sl_mult={r.get('sl_mult','-')}"
        print(f"  {i+1:>2}. [{strat:>10}] rally>={r['min_rally']:.2f}% vel>={r['min_vel']:.2f} "
              f"vix={r['vix_range']} win<={r['win_end']}min K={r.get('strike_off',0):+d} "
              f"hold={r['max_hold']}min {extra}")
        print(f"      N={r['n']:>3} WR={r['win_rate']:>5.1f}% avgPnL=${r['avg_pnl']:>7,} "
              f"totPnL=${r['total_pnl']:>9,} avgHold={r['avg_hold']:.0f}m "
              f"Sharpe={r['sharpe']:.3f}")

    # Save all results
    save_results = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results]
    with open(OUT_FILE, 'w') as f:
        json.dump(save_results, f, indent=1)
    print(f"\nSaved {len(all_results)} configs to {OUT_FILE}")

    # Also save detailed trades for best config
    if top20:
        best = top20[0]
        if 'trades' in best:
            trades_csv = SCRIPT_DIR / 'morning_rip_best_trades.csv'
            with open(trades_csv, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=best['trades'][0].keys())
                w.writeheader()
                w.writerows(best['trades'])
            print(f"Saved best config trades to {trades_csv}")


if __name__ == '__main__':
    main()
