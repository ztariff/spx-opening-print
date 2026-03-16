#!/usr/bin/env python3
"""
Morning Rip Fade — BEAR CALL SPREAD Backtest v4 (FIXED METHODOLOGY)
====================================================================
FIXES from v3:
  1. Multi-threshold entry finder: captures entry at EACH rally threshold
     crossing (0.15%, 0.20%, ..., 0.70%), so entry price/time/velocity
     are correct for each threshold being tested.
  2. Velocity properly captured at the moment each threshold is crossed,
     so we can accurately test whether velocity matters.

Spread structure fixed to best: 5-wide, ATM short, PT=50%, SL=3x, hold=60min.
Sweeps signal filters (rally, velocity, VIX, window) to find exact cliffs.

Run:  python3 45_rip_spread_v4.py
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
OUT_FILE = SCRIPT_DIR / 'morning_rip_spread_v4_results.json'

START_DATE = '2018-01-03'
MIN_TRADES = 3

_last_call = 0
RATE_DELAY = 0.15

# Rally thresholds to capture entries at
RALLY_THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.70, 1.00]


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


# ── FIXED: Multi-threshold entry finder ──
def find_entries_multi(spx_data, vix_data):
    """
    For each day, find the FIRST bar where the rally from rolling low
    crosses each threshold in RALLY_THRESHOLDS. Returns a dict keyed by
    (date, threshold) -> entry info.

    This ensures that when testing rally >= 0.40%, we use the entry price/time
    at the moment SPX actually hit +0.40%, NOT when it first crossed 0.15%.
    """
    entries = {}  # (date, threshold) -> entry_info

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
        thresholds_hit = set()

        for i, bar in enumerate(bars):
            if bar['l'] < rolling_low:
                rolling_low = bar['l']
                trough_idx = i

            rally_pct = (bar['c'] - rolling_low) / rolling_low * 100
            elapsed = i - trough_idx
            vel = rally_pct / elapsed if elapsed > 0 else 0

            # Only look at first 30 min for entries
            if bar['idx'] > 30:
                break

            # Check each threshold
            for thresh in RALLY_THRESHOLDS:
                if thresh in thresholds_hit:
                    continue
                if rally_pct >= thresh:
                    thresholds_hit.add(thresh)
                    entries[(date_str, thresh)] = {
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
                    }

    return entries


# ── Fetch all needed option chains ──
def fetch_all_options(entries_dict):
    """Pre-fetch call option bars for all unique (date, strike) pairs."""
    offsets = list(range(-5, 40, 5))
    opt_data = {}

    # Get unique dates and their entry prices
    date_prices = {}
    for (date_str, thresh), entry in entries_dict.items():
        if date_str not in date_prices:
            date_prices[date_str] = set()
        atm = round(entry['entry_price'] / 5) * 5
        date_prices[date_str].add(atm)

    total = sum(len(atms) * len(offsets) for atms in date_prices.values())
    print(f"  Need to check {total} option chains across {len(date_prices)} days")

    done = 0
    for date_str in sorted(date_prices.keys()):
        for atm in date_prices[date_str]:
            for off in offsets:
                strike = atm + off
                k = (date_str, strike, 'C')
                if k not in opt_data:
                    bars = fetch_option_bars(date_str, strike, 'C')
                    opt_data[k] = bars
                done += 1
                if done % 100 == 0:
                    print(f"  [{done}/{total}] …", flush=True)

    print(f"  {len(opt_data)} option chains loaded")
    return opt_data


# ── Bear Call Spread runner (uses correct threshold entry) ──
def run_bear_call_spread(entries_dict, opt_data, params):
    short_off = params['short_off']
    spread_width = params['spread_width']
    max_hold = params['max_hold']
    sl_mult = params.get('sl_mult', 3.0)
    profit_target = params.get('profit_target', 0.50)
    min_rally = params['min_rally']
    min_vel = params['min_vel']
    vix_min = params.get('vix_min', 0)
    vix_max = params.get('vix_max', 99)
    win_end = params.get('win_end', 30)

    # Find the right threshold level for this min_rally
    # Use the highest threshold that's <= min_rally
    best_thresh = RALLY_THRESHOLDS[0]
    for t in RALLY_THRESHOLDS:
        if t <= min_rally:
            best_thresh = t
        else:
            break

    trades = []
    dates_seen = set()

    for (date_str, thresh), entry in entries_dict.items():
        if thresh != best_thresh:
            continue
        if date_str in dates_seen:
            continue

        # NOW filter on the actual rally at entry (should be >= thresh >= min_rally in most cases)
        # But we need to verify: did the rally actually reach min_rally?
        # Since we captured at best_thresh <= min_rally, the rally_pct at entry is >= best_thresh
        # If best_thresh < min_rally, we need to find if rally continued to min_rally
        # Actually, if best_thresh == min_rally, entry rally_pct >= min_rally (correct)
        # If best_thresh < min_rally, we should skip (use exact threshold)
        # But we set best_thresh to be the highest <= min_rally from our list
        # So if min_rally = 0.40, best_thresh = 0.40 (it's in the list), entry rally >= 0.40

        if entry['rally_pct'] < min_rally:
            continue
        if entry['velocity'] < min_vel:
            continue
        if entry['vix_open'] < vix_min or entry['vix_open'] > vix_max:
            continue
        if entry['bars'][entry['entry_idx']]['idx'] > win_end:
            continue

        dates_seen.add(date_str)

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

        max_loss = (spread_width - credit) * 100
        max_profit = credit * 100
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

            if profit_target > 0 and cur_profit_pct >= profit_target * 100:
                exit_reason = 'profit_target'
                exit_credit = cur_spread
                exit_time = bars[j]['time']
                exit_spx = bars[j]['c']
                hold_mins = j - eidx
                break

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
    print("MORNING RIP FADE v4 — FIXED MULTI-THRESHOLD ENTRY METHODOLOGY")
    print("=" * 120)

    spx_data = load_spx()
    vix_data = load_vix()

    print("\n" + "=" * 80)
    print("PHASE 1: Finding entries at EACH rally threshold")
    print("=" * 80)

    entries_dict = find_entries_multi(spx_data, vix_data)

    # Report entry counts per threshold
    for thresh in RALLY_THRESHOLDS:
        n = len([k for k in entries_dict if k[1] == thresh])
        print(f"  Rally >= {thresh:.2f}%: {n} days with entry")

    # VIX distribution for lowest threshold
    base_entries = [e for (d, t), e in entries_dict.items() if t == RALLY_THRESHOLDS[0]]
    for lo, hi, label in [(0,15,'<15'), (15,18,'15-18'), (18,25,'18-25'),
                           (25,30,'25-30'), (30,35,'30-35'), (35,99,'35+')]:
        n = len([e for e in base_entries if lo <= e['vix_open'] < hi])
        print(f"  VIX {label}: {n}")

    print("\n" + "=" * 80)
    print("PHASE 2: Fetching option prices from Polygon")
    print("=" * 80)

    opt_data = fetch_all_options(entries_dict)

    print("\n" + "=" * 80)
    print("PHASE 3: Running bear call spread configurations")
    print("=" * 80)

    all_results = []
    count = 0

    # Fixed spread structure (proven best from v2)
    short_off = 0
    spread_width = 5
    sl_mult = 3.0
    profit_target = 0.50
    max_hold = 60

    # Sweep signal filters
    rally_levels = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.70, 1.00]
    vel_levels = [0, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15]
    vix_ranges = [
        (0, 15, '<15'), (15, 18, '15-18'), (15, 20, '15-20'), (18, 20, '18-20'),
        (15, 25, '15-25'), (18, 25, '18-25'), (20, 25, '20-25'), (20, 30, '20-30'),
        (25, 35, '25-35'), (30, 40, '30-40'),
        (0, 99, 'all'), (15, 99, '15+'), (18, 99, '18+'), (20, 99, '20+'),
        (25, 99, '25+'), (30, 99, '30+'),
    ]
    win_ends = [15, 30]

    total_combos = len(rally_levels) * len(vel_levels) * len(vix_ranges) * len(win_ends)
    print(f"  Testing {total_combos} signal filter combos")

    for min_rally in rally_levels:
        for min_vel in vel_levels:
            for vix_min, vix_max_val, vix_label in vix_ranges:
                for win_end in win_ends:
                    count += 1
                    if count % 200 == 0:
                        print(f"  [{count}/{total_combos}] …", flush=True)

                    params = {
                        'short_off': short_off,
                        'spread_width': spread_width,
                        'max_hold': max_hold,
                        'sl_mult': sl_mult,
                        'profit_target': profit_target,
                        'min_rally': min_rally,
                        'min_vel': min_vel,
                        'vix_min': vix_min,
                        'vix_max': vix_max_val,
                        'win_end': win_end,
                    }
                    trades = run_bear_call_spread(entries_dict, opt_data, params)
                    stats = compute_stats(trades)
                    if stats and stats['n'] >= MIN_TRADES:
                        result = {
                            'min_rally': min_rally,
                            'min_vel': min_vel,
                            'vix_range': vix_label,
                            'win_end': win_end,
                            'short_off': short_off,
                            'spread_width': spread_width,
                            'sl_mult': sl_mult,
                            'profit_target': profit_target,
                            'max_hold': max_hold,
                            **stats,
                        }
                        if stats['n'] >= 8 and stats['win_rate'] >= 55:
                            result['trades'] = trades
                        all_results.append(result)

    print(f"\n  Swept {count} combos, {len(all_results)} with >= {MIN_TRADES} trades")

    # ── ANALYSIS ──
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    # ─────────────────────────────────────────────────────────────
    # SECTION A: VELOCITY DEEP DIVE
    # Does velocity matter? Compare WITH vs WITHOUT velocity filter
    # for each (rally, VIX) combo
    # ─────────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("VELOCITY ANALYSIS: Does velocity matter at each (rally, VIX) combo?")
    print("For each combo, compare vel=0 (no filter) vs vel=0.10 (strict)")
    print(f"{'='*120}")
    print(f"{'Rally':>6} {'VIX':>7} {'Win':>4} | {'N(v=0)':>6} {'WR(v=0)':>7} {'Avg(v=0)':>8} | "
          f"{'N(v.10)':>7} {'WR(v.10)':>8} {'Avg(v.10)':>9} | {'Verdict':>12}")
    print("-" * 120)

    for min_rally in [0.20, 0.30, 0.40, 0.50, 0.70]:
        for vix_label in ['20+', '25+', '25-35', '30+']:
            for win_end in [30]:
                r_no_vel = [r for r in all_results
                           if r['min_rally'] == min_rally and r['vix_range'] == vix_label
                           and r['win_end'] == win_end and r['min_vel'] == 0]
                r_hi_vel = [r for r in all_results
                           if r['min_rally'] == min_rally and r['vix_range'] == vix_label
                           and r['win_end'] == win_end and r['min_vel'] == 0.10]

                nv = r_no_vel[0] if r_no_vel else None
                hv = r_hi_vel[0] if r_hi_vel else None

                nv_str = f"{nv['n']:>4} {nv['win_rate']:>6.1f}% ${nv['avg_pnl']:>7,}" if nv else "  --     --       --"
                hv_str = f"{hv['n']:>5} {hv['win_rate']:>7.1f}% ${hv['avg_pnl']:>8,}" if hv else "   --      --        --"

                if nv and hv and nv['n'] >= 5 and hv['n'] >= 5:
                    if hv['win_rate'] > nv['win_rate'] + 5:
                        verdict = "VEL HELPS"
                    elif nv['win_rate'] > hv['win_rate'] + 5:
                        verdict = "VEL HURTS"
                    else:
                        verdict = "~same"
                elif nv and (not hv or hv['n'] < 3):
                    verdict = "vel kills N"
                else:
                    verdict = "insuf data"

                print(f"{min_rally:>5.2f}% {vix_label:>7} {win_end:>4} | {nv_str} | {hv_str} | {verdict:>12}")

    # ─────────────────────────────────────────────────────────────
    # SECTION B: RALLY THRESHOLD CLIFF
    # Now with CORRECT entries at each threshold
    # ─────────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("RALLY THRESHOLD ANALYSIS (entries captured at correct threshold!)")
    print(f"{'='*120}")

    for vix_label in ['20+', '25+', '25-35', '30+', 'all']:
        print(f"\n  VIX={vix_label}, vel=0, win<=30:")
        print(f"  {'Rally':>6} {'N':>4} {'WR%':>6} {'AvgPnL':>8} {'TotPnL':>9} {'Sharpe':>7} {'StopR':>5}")
        print(f"  {'-'*55}")
        for min_rally in rally_levels:
            r = [x for x in all_results
                 if x['min_rally'] == min_rally and x['vix_range'] == vix_label
                 and x['min_vel'] == 0 and x['win_end'] == 30]
            if r:
                x = r[0]
                print(f"  {min_rally:>5.2f}% {x['n']:>4} {x['win_rate']:>5.1f}% ${x['avg_pnl']:>7,} "
                      f"${x['total_pnl']:>8,} {x['sharpe']:>7.3f} {x['stop_rate']:>5.1f}%")
            else:
                print(f"  {min_rally:>5.2f}%   --    --       --        --      --     --")

    # ─────────────────────────────────────────────────────────────
    # SECTION C: VIX CLIFF
    # ─────────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("VIX REGIME CLIFF (rally=0.30%, vel=0, win<=30)")
    print(f"{'='*120}")
    print(f"{'VIX':>8} {'N':>4} {'WR%':>6} {'AvgPnL':>8} {'TotPnL':>9} {'Sharpe':>7}")
    print("-" * 50)
    for vix_label in ['<15', '15-18', '15-20', '18-20', '18-25', '20-25', '20-30',
                       '25-35', '30-40', 'all', '15+', '18+', '20+', '25+', '30+']:
        r = [x for x in all_results
             if x['min_rally'] == 0.30 and x['vix_range'] == vix_label
             and x['min_vel'] == 0 and x['win_end'] == 30]
        if r:
            x = r[0]
            print(f"{vix_label:>8} {x['n']:>4} {x['win_rate']:>5.1f}% ${x['avg_pnl']:>7,} "
                  f"${x['total_pnl']:>8,} {x['sharpe']:>7.3f}")

    # ─────────────────────────────────────────────────────────────
    # SECTION D: TOP CONFIGS
    # ─────────────────────────────────────────────────────────────
    sig = [r for r in all_results if r['n'] >= 10]
    sig.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*140}")
    print(f"TOP 30 BY SHARPE (min 10 trades)")
    print(f"{'='*140}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>7} {'Win':>4} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgCr':>6} {'AvgHld':>6} "
          f"{'StopR':>5} {'TgtR':>5} {'Sharpe':>7}")
    print("-" * 120)

    for r in sig[:30]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>7} "
              f"{r['win_end']:>4} "
              f"{r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} ${r['avg_credit']:>5.2f} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['target_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # High-N
    high_n = [r for r in all_results if r['n'] >= 20]
    high_n.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*140}")
    print(f"TOP 20 BY SHARPE (min 20 trades — high confidence)")
    print(f"{'='*140}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>7} {'Win':>4} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>8} {'TotPnL':>9} {'AvgCr':>6} {'AvgHld':>6} "
          f"{'StopR':>5} {'TgtR':>5} {'Sharpe':>7}")
    print("-" * 120)

    for r in high_n[:20]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>7} "
              f"{r['win_end']:>4} "
              f"{r['n']:>4} {r['win_rate']:>5.1f} ${r['avg_pnl']:>7,} "
              f"${r['total_pnl']:>8,} ${r['avg_credit']:>5.2f} {r['avg_hold']:>5.1f}m "
              f"{r['stop_rate']:>5.1f} {r['target_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # ─────────────────────────────────────────────────────────────
    # SECTION E: ENTRY TIME COMPARISON (v3 bug check)
    # Show how entries differ between thresholds for same day
    # ─────────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("ENTRY COMPARISON: Same day, different thresholds")
    print("(Shows how entry price/time/velocity differ when captured correctly)")
    print(f"{'='*120}")

    # Get days that have entries at both 0.20% and 0.50%
    dates_020 = {d for (d, t) in entries_dict if t == 0.20}
    dates_050 = {d for (d, t) in entries_dict if t == 0.50}
    common = sorted(dates_020 & dates_050)[:15]

    print(f"{'Date':>12} | {'@0.20%':>7} {'Time':>6} {'Vel':>5} {'SPX':>8} | "
          f"{'@0.50%':>7} {'Time':>6} {'Vel':>5} {'SPX':>8} | {'TimeDiff':>8} {'PxDiff':>7}")
    print("-" * 110)

    for d in common:
        e1 = entries_dict.get((d, 0.20))
        e2 = entries_dict.get((d, 0.50))
        if e1 and e2:
            t1 = time_to_mins(e1['entry_time'])
            t2 = time_to_mins(e2['entry_time'])
            print(f"{d:>12} | {e1['rally_pct']:>6.3f}% {e1['entry_time']:>6} {e1['velocity']:>5.3f} "
                  f"{e1['entry_price']:>8.2f} | "
                  f"{e2['rally_pct']:>6.3f}% {e2['entry_time']:>6} {e2['velocity']:>5.3f} "
                  f"{e2['entry_price']:>8.2f} | "
                  f"{t2-t1:>7}m {e2['entry_price']-e1['entry_price']:>+7.2f}")

    # Save results
    save = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results]
    with open(OUT_FILE, 'w') as f:
        json.dump(save, f, indent=1)
    print(f"\nSaved {len(all_results)} configs to {OUT_FILE}")

    # Save trades for best config
    if sig and 'trades' in all_results[0]:
        # Find the best sig config with trades
        for r in sig:
            orig = [x for x in all_results if x.get('trades') and
                    x['min_rally'] == r['min_rally'] and x['min_vel'] == r['min_vel'] and
                    x['vix_range'] == r['vix_range'] and x['win_end'] == r['win_end']]
            if orig:
                trades_csv = SCRIPT_DIR / 'morning_rip_spread_v4_trades.csv'
                with open(trades_csv, 'w', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=orig[0]['trades'][0].keys())
                    w.writeheader()
                    w.writerows(orig[0]['trades'])
                print(f"Saved best config trades to {trades_csv}")
                break


if __name__ == '__main__':
    main()
