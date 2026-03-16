#!/usr/bin/env python3
"""
Overnight Low Support Strategy — Full Backtest v4 (2018-2026)
===================================================
Uses SPY extended-hours data from Polygon to find the REAL overnight low,
then tests strategies when SPX approaches that low during RTH using
REAL SPXW 0DTE option prices from Polygon.

v3 changes:
  - Much looser filters → more trades for statistical significance
  - Min trades lowered from 8 to 5
  - Save ALL configs that pass (not just top 80)
  - Added min_rally=0.002 (0.2%) and vel=0 to all sweeps
  - Wider proximity range for PS/LC
  - Better signal analysis and reporting

Run:  python3 40_overnight_low_backtest.py
"""

import urllib.request, json, time, csv, sys, os
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# ── CONFIG ──
API_KEY = os.environ.get("POLYGON_API_KEY", "")
SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'overnight_cache'
CACHE_DIR.mkdir(exist_ok=True)
OPT_CACHE_DIR = SCRIPT_DIR / 'options_cache'
OPT_CACHE_DIR.mkdir(exist_ok=True)

SPX_CSV = SCRIPT_DIR / 'spx_1min_bars.csv'
VIX_CSV = SCRIPT_DIR / 'vix_daily_bars.csv'

START_DATE = '2018-01-03'  # extended back to full dataset
MIN_TRADES = 5

# Rate limiter — paid tier
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


def fetch_bars(ticker, d_from, d_to, mult=1, span='minute', limit=50000):
    key = f"{ticker}_{d_from}_{d_to}_{mult}_{span}".replace(':', '_').replace('/', '_')
    cache = CACHE_DIR / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}"
           f"/{d_from}/{d_to}?adjusted=true&sort=asc&limit={limit}&apiKey={API_KEY}")
    data = polygon_get(url)
    if data and data.get('results'):
        cache.write_text(json.dumps(data['results']))
        return data['results']
    cache.write_text('[]')
    return []


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


# ── Time helpers (DST-aware ET) ──
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


def et_s(et):
    return f"{et.hour:02d}:{et.minute:02d}"


def time_to_mins(t_str):
    return int(t_str[:2]) * 60 + int(t_str[3:5])


# ── Discover ES ticker ──
def discover_ticker():
    today = datetime.utcnow()
    yy = str(today.year)[2:]
    d_from = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    d_to = today.strftime('%Y-%m-%d')
    months = ['H', 'M', 'U', 'Z']
    month_num = [3, 6, 9, 12]
    cur_m = today.month
    front = 'H'
    for i, mn in enumerate(month_num):
        if mn >= cur_m:
            front = months[i]
            break
    candidates = [f"C:ES{front}{yy}", 'C:ES1!', 'SPY']
    for t in candidates:
        print(f"  trying {t} …", end=' ', flush=True)
        bars = fetch_bars(t, d_from, d_to, mult=5, span='minute', limit=20)
        if bars:
            extended = [b for b in bars if et_m(ts_et(b['t'])) < 570 or et_m(ts_et(b['t'])) >= 960]
            print(f"OK {len(bars)} bars, {len(extended)} extended-hours")
            if extended or t == 'SPY':
                return t
        else:
            print("X")
    return 'SPY'


# ── Load local data ──
def load_spx():
    print(f"Loading {SPX_CSV} …")
    by_date = defaultdict(list)
    with open(SPX_CSV) as f:
        for row in csv.DictReader(f):
            by_date[row['date']].append({
                'time': row['time'],
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            })
    print(f"  {len(by_date)} days loaded")
    return by_date


def load_vix():
    vix = {}
    with open(VIX_CSV) as f:
        for row in csv.DictReader(f):
            vix[row['date']] = float(row['close'])
    return vix


# ── Build overnight signals ──
def build_overnight(es_ticker, trading_days, spx_data, vix_data):
    print(f"\nFetching {es_ticker} overnight data …")
    signals = {}
    batch = 5
    for i in range(0, len(trading_days), batch):
        chunk = trading_days[i:i + batch]
        prev_idx = max(0, i - 1)
        d_from = trading_days[prev_idx]
        d_to = chunk[-1]
        pct = i / len(trading_days) * 100
        print(f"  [{pct:5.1f}%] {d_from} -> {d_to}", flush=True)

        raw = fetch_bars(es_ticker, d_from, d_to, mult=1, span='minute', limit=50000)
        if not raw:
            continue

        overnight = defaultdict(list)
        for bar in raw:
            et = ts_et(bar['t'])
            mins = et_m(et)
            d_str = et.strftime('%Y-%m-%d')
            if mins >= 960:
                nxt = et + timedelta(days=1)
                while nxt.weekday() >= 5:
                    nxt += timedelta(days=1)
                overnight[nxt.strftime('%Y-%m-%d')].append(bar)
            elif mins < 570:
                overnight[d_str].append(bar)

        for day in chunk:
            on = overnight.get(day, [])
            spx = spx_data.get(day, [])
            if len(on) < 3 or len(spx) < 10:
                continue

            on_low = min(b['l'] for b in on)
            on_high = max(b['h'] for b in on)
            on_close = on[-1]['c']
            rally = (on_close - on_low) / on_low if on_low else 0

            idx = trading_days.index(day)
            prev = trading_days[idx - 1] if idx > 0 else None
            prior_close = spx_data[prev][-1]['c'] if prev and prev in spx_data else None
            drop = (prior_close - on_low) / prior_close if prior_close else 0

            scaled_low = on_low * 10 if es_ticker == 'SPY' else on_low
            vix = vix_data.get(day, vix_data.get(prev, 20) if prev else 20)

            signals[day] = dict(
                on_low=on_low, on_high=on_high, on_close=on_close,
                rally=rally, drop=drop, scaled_low=scaled_low,
                vix=vix, spx=spx, n_on=len(on),
            )
    print(f"  Built signals for {len(signals)} days")
    return signals


# ── Find closest option bar to a given ET time ──
def find_opt_bar(opt_bars, target_mins, date_str):
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
    return best if best_diff <= 5 else None


# ── Compute approach velocity ──
def compute_velocity(spx_bars, entry_idx, lookback=10):
    start_idx = max(0, entry_idx - lookback)
    if start_idx == entry_idx:
        return 0
    peak = max(b['h'] for b in spx_bars[start_idx:entry_idx + 1])
    current = spx_bars[entry_idx]['c']
    drop_pct = (peak - current) / peak * 100
    elapsed = entry_idx - start_idx
    return drop_pct / max(1, elapsed)


# ══════════════════════════════════════════════════════════════
# MAIN BACKTEST
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 120)
    print("OVERNIGHT LOW SUPPORT — v4 with REAL OPTION PRICES (2018-2026)")
    print("=" * 120)

    es_ticker = discover_ticker()
    print(f"Using: {es_ticker}\n")

    spx_data = load_spx()
    vix_data = load_vix()

    all_dates = sorted(d for d in spx_data if d >= START_DATE)
    print(f"Date range: {START_DATE} -> {all_dates[-1]}  ({len(all_dates)} days)")

    signals = build_overnight(es_ticker, all_dates, spx_data, vix_data)

    # ══════════════════════════════════════════════════════════════
    # Phase 1: Identify ALL entry signals (loosest possible filters)
    # ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 120)
    print("PHASE 1: Identifying entry signals")
    print("=" * 120)

    all_entries = []
    MAX_PROX = 20  # widened from 15

    for day, sig in sorted(signals.items()):
        spx = sig['spx']
        on_low = sig['scaled_low']
        vix = sig['vix']
        rally = sig['rally']

        already_below = False
        for j, bar in enumerate(spx):
            t_mins = time_to_mins(bar['time'])
            if t_mins < 575 or t_mins >= 930:
                continue

            if bar['l'] < on_low - 2:
                already_below = True

            if already_below:
                continue

            dist = bar['l'] - on_low

            if dist >= -2 and dist <= MAX_PROX and bar['c'] >= on_low - 1:
                velocity = compute_velocity(spx, j, lookback=10)
                exact_proximity = bar['l'] - on_low

                all_entries.append(dict(
                    date=day,
                    entry_idx=j,
                    entry_time=bar['time'],
                    entry_price=bar['c'],
                    entry_low=bar['l'],
                    on_low=on_low,
                    on_raw=sig['on_low'],
                    vix=vix,
                    rally=rally,
                    drop=sig['drop'],
                    velocity=velocity,
                    proximity=exact_proximity,
                    spx=spx,
                ))
                break

    print(f"Total entry signals found: {len(all_entries)}")

    # Distribution analysis
    print(f"\nSignal distribution by MIN RALLY filter:")
    for mr in [0, 0.001, 0.002, 0.003, 0.005, 0.008]:
        n = len([e for e in all_entries if e['rally'] >= mr])
        print(f"  Rally >= {mr*100:.1f}%: {n} signals")

    print(f"\nSignal distribution by PROXIMITY filter:")
    for p in [3, 5, 8, 10, 15, 20]:
        n = len([e for e in all_entries if e['proximity'] <= p])
        print(f"  Proximity <= {p}pts: {n} signals")

    print(f"\nVelocity distribution:")
    for label, lo, hi in [('None/Slow (<0.03)', 0, 0.03), ('Low (0.03-0.05)', 0.03, 0.05),
                           ('Med (0.05-0.10)', 0.05, 0.10), ('Fast (>=0.10)', 0.10, 999)]:
        n = len([e for e in all_entries if lo <= e['velocity'] < hi])
        print(f"  {label}: {n}")

    print(f"\nVIX distribution:")
    for label, lo, hi in [('VIX<15', 0, 15), ('VIX 15-18', 15, 18), ('VIX 18-25', 18, 25), ('VIX 25+', 25, 999)]:
        n = len([e for e in all_entries if lo <= e['vix'] < hi])
        print(f"  {label}: {n}")

    # Show all entries for transparency
    print(f"\nAll entry signals:")
    print(f"{'Date':<11} {'Time':>5} {'ONL':>7} {'Price':>7} {'Prox':>5} {'Rally%':>7} {'Vel':>6} {'VIX':>5}")
    for e in all_entries:
        print(f"{e['date']:<11} {e['entry_time']:>5} {e['on_low']:>7.0f} {e['entry_price']:>7.1f} "
              f"{e['proximity']:>5.1f} {e['rally']*100:>6.2f}% {e['velocity']:>6.3f} {e['vix']:>5.1f}")

    # ══════════════════════════════════════════════════════════════
    # Phase 2: Fetch option prices
    # ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 120)
    print("PHASE 2: Fetching REAL option prices from Polygon")
    print("=" * 120)

    STRIKE_OFFSETS_PUT = [0, -5, -10, -15, -20]
    STRIKE_OFFSETS_CALL = [0, 5, 10]

    opt_data = {}
    total_fetches = 0
    for i, entry in enumerate(all_entries):
        if i % 20 == 0:
            print(f"  [{i}/{len(all_entries)}] Fetching options for {entry['date']}…", flush=True)

        on_low_rounded = round(entry['on_low'] / 5) * 5

        for off in STRIKE_OFFSETS_PUT:
            strike = on_low_rounded + off
            k = (entry['date'], strike, 'P')
            if k not in opt_data:
                bars = fetch_option_bars(entry['date'], strike, 'P')
                opt_data[k] = bars
                total_fetches += 1

        for off in STRIKE_OFFSETS_CALL:
            strike = on_low_rounded + off
            k = (entry['date'], strike, 'C')
            if k not in opt_data:
                bars = fetch_option_bars(entry['date'], strike, 'C')
                opt_data[k] = bars
                total_fetches += 1

    print(f"  Fetched {total_fetches} new option chains ({len(opt_data)} total cached)")

    # ══════════════════════════════════════════════════════════════
    # Phase 3: Run ALL strategy configurations
    # ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 120)
    print("PHASE 3: Running strategy configurations")
    print("=" * 120)

    results = {}

    def run_naked_put(entries, strike_off, stop_mins, max_hold, label_extra=""):
        trades = []
        for entry in entries:
            on_low = entry['on_low']
            strike = round(on_low / 5) * 5 + strike_off
            k = (entry['date'], strike, 'P')
            put_bars = opt_data.get(k, [])
            if not put_bars:
                continue

            entry_mins = time_to_mins(entry['entry_time'])
            entry_opt = find_opt_bar(put_bars, entry_mins, entry['date'])
            if not entry_opt or entry_opt['c'] < 0.10:
                continue

            spx = entry['spx']
            eidx = entry['entry_idx']
            scaled_low = entry['on_low']

            cb = 0
            stopped = False
            xidx = eidx
            for jj in range(eidx + 1, min(eidx + max_hold + 1, len(spx))):
                if spx[jj]['c'] < scaled_low:
                    cb += 1
                else:
                    cb = 0
                if cb >= stop_mins:
                    stopped = True
                    xidx = jj
                    break
                xidx = jj

            exit_bar = spx[xidx]
            exit_mins = time_to_mins(exit_bar['time'])
            exit_opt = find_opt_bar(put_bars, exit_mins, entry['date'])
            if not exit_opt:
                continue

            sold_price = entry_opt['c']
            bought_price = exit_opt['c']
            pnl_per = (sold_price - bought_price) * 100
            contracts = max(1, min(30, round(50000 / (sold_price * 100))))
            total_pnl = round(pnl_per * contracts)

            trades.append(dict(
                date=entry['date'], et=entry['entry_time'], xt=exit_bar['time'],
                onl=round(scaled_low, 1), rally=round(entry['rally'] * 100, 2),
                vel=round(entry['velocity'], 3), vix=round(entry['vix'], 1),
                epx=round(entry['entry_price'], 1), xpx=round(exit_bar['c'], 1),
                strike=strike, opt_entry=round(sold_price, 2), opt_exit=round(bought_price, 2),
                pnl=total_pnl, cts=contracts, hm=xidx - eidx, stopped=stopped,
                move=round(exit_bar['c'] - entry['entry_price'], 2),
            ))
        return trades

    def run_put_spread(entries, spread_width, stop_mins, max_hold):
        trades = []
        for entry in entries:
            on_low = entry['on_low']
            short_strike = round(on_low / 5) * 5
            long_strike = short_strike - spread_width

            sk = (entry['date'], short_strike, 'P')
            lk = (entry['date'], long_strike, 'P')
            short_bars = opt_data.get(sk, [])
            long_bars = opt_data.get(lk, [])
            if not short_bars or not long_bars:
                continue

            entry_mins = time_to_mins(entry['entry_time'])
            short_entry = find_opt_bar(short_bars, entry_mins, entry['date'])
            long_entry = find_opt_bar(long_bars, entry_mins, entry['date'])
            if not short_entry or not long_entry:
                continue
            if short_entry['c'] < 0.10:
                continue

            credit = short_entry['c'] - long_entry['c']
            if credit <= 0:
                continue

            spx = entry['spx']
            eidx = entry['entry_idx']
            scaled_low = entry['on_low']

            cb = 0
            stopped = False
            xidx = eidx
            for jj in range(eidx + 1, min(eidx + max_hold + 1, len(spx))):
                if spx[jj]['c'] < scaled_low:
                    cb += 1
                else:
                    cb = 0
                if cb >= stop_mins:
                    stopped = True
                    xidx = jj
                    break
                xidx = jj

            exit_bar = spx[xidx]
            exit_mins = time_to_mins(exit_bar['time'])
            short_exit = find_opt_bar(short_bars, exit_mins, entry['date'])
            long_exit = find_opt_bar(long_bars, exit_mins, entry['date'])
            if not short_exit or not long_exit:
                continue

            debit = short_exit['c'] - long_exit['c']
            pnl_per = (credit - debit) * 100
            max_risk = (spread_width - credit) * 100
            contracts = max(1, min(50, round(50000 / max(1, max_risk))))
            total_pnl = round(pnl_per * contracts)

            trades.append(dict(
                date=entry['date'], et=entry['entry_time'], xt=exit_bar['time'],
                onl=round(scaled_low, 1), rally=round(entry['rally'] * 100, 2),
                vel=round(entry['velocity'], 3), vix=round(entry['vix'], 1),
                epx=round(entry['entry_price'], 1), xpx=round(exit_bar['c'], 1),
                strike=f"{short_strike}/{long_strike}", credit=round(credit, 2),
                debit=round(debit, 2), pnl=total_pnl, cts=contracts,
                hm=xidx - eidx, stopped=stopped,
                move=round(exit_bar['c'] - entry['entry_price'], 2),
            ))
        return trades

    def run_long_call(entries, strike_off, stop_mins, max_hold):
        trades = []
        for entry in entries:
            on_low = entry['on_low']
            strike = round(on_low / 5) * 5 + strike_off

            k = (entry['date'], strike, 'C')
            call_bars = opt_data.get(k, [])
            if not call_bars:
                continue

            entry_mins = time_to_mins(entry['entry_time'])
            entry_opt = find_opt_bar(call_bars, entry_mins, entry['date'])
            if not entry_opt or entry_opt['c'] < 0.10:
                continue

            spx = entry['spx']
            eidx = entry['entry_idx']
            scaled_low = entry['on_low']

            cb = 0
            stopped = False
            xidx = eidx
            for jj in range(eidx + 1, min(eidx + max_hold + 1, len(spx))):
                if spx[jj]['c'] < scaled_low:
                    cb += 1
                else:
                    cb = 0
                if cb >= stop_mins:
                    stopped = True
                    xidx = jj
                    break
                xidx = jj

            exit_bar = spx[xidx]
            exit_mins = time_to_mins(exit_bar['time'])
            exit_opt = find_opt_bar(call_bars, exit_mins, entry['date'])
            if not exit_opt:
                continue

            bought_price = entry_opt['c']
            sold_price = exit_opt['c']
            pnl_per = (sold_price - bought_price) * 100
            contracts = max(1, min(30, round(50000 / (bought_price * 100))))
            total_pnl = round(pnl_per * contracts)

            trades.append(dict(
                date=entry['date'], et=entry['entry_time'], xt=exit_bar['time'],
                onl=round(scaled_low, 1), rally=round(entry['rally'] * 100, 2),
                vel=round(entry['velocity'], 3), vix=round(entry['vix'], 1),
                epx=round(entry['entry_price'], 1), xpx=round(exit_bar['c'], 1),
                strike=strike, opt_entry=round(bought_price, 2), opt_exit=round(sold_price, 2),
                pnl=total_pnl, cts=contracts, hm=xidx - eidx, stopped=stopped,
                move=round(exit_bar['c'] - entry['entry_price'], 2),
            ))
        return trades

    # ── A) Short Naked Puts — WIDE sweep ──
    print("\n  Running NAKED PUT sweep …")
    np_count = 0
    for strike_off in [0, -5, -10]:
        for min_rally in [0, 0.002, 0.005, 0.008]:
            for stop_mins in [3, 5, 7]:
                for max_hold in [15, 30, 45, 60]:
                    for min_vel in [0, 0.03, 0.05]:
                        for max_prox in [5, 10, 15, 20]:
                            for vix_min in [0, 15, 18, 20]:
                                # Filter entries
                                filtered = [e for e in all_entries
                                            if e['rally'] >= min_rally
                                            and e['proximity'] <= max_prox
                                            and e['velocity'] >= min_vel
                                            and e['vix'] >= vix_min]

                                if len(filtered) < MIN_TRADES:
                                    continue

                                trades = run_naked_put(filtered, strike_off, stop_mins, max_hold)
                                s = compute_stats(trades)
                                if s:
                                    label = (f"NP off{strike_off} r{min_rally*100:.1f} "
                                             f"s{stop_mins} h{max_hold} v{min_vel:.2f} "
                                             f"p{max_prox} vx{vix_min}")
                                    results[label] = dict(cfg=dict(
                                        type='naked_put', off=strike_off, mr=min_rally,
                                        sm=stop_mins, hold=max_hold, vel=min_vel,
                                        prox=max_prox, vix_min=vix_min,
                                    ), trades=trades, **s)
                                    np_count += 1

    print(f"  Naked put configs with results: {np_count}")

    # ── B) Short Put Spreads — WIDE sweep ──
    print("\n  Running PUT SPREAD sweep …")
    ps_count = 0
    for spread_width in [10, 15, 20]:
        for min_rally in [0, 0.002, 0.005]:
            for stop_mins in [3, 5, 7]:
                for max_hold in [15, 30, 45, 60]:
                    for min_vel in [0, 0.03, 0.05]:
                        for max_prox in [5, 10, 15, 20]:
                            for vix_min in [0, 15, 18]:
                                filtered = [e for e in all_entries
                                            if e['rally'] >= min_rally
                                            and e['proximity'] <= max_prox
                                            and e['velocity'] >= min_vel
                                            and e['vix'] >= vix_min]

                                if len(filtered) < MIN_TRADES:
                                    continue

                                trades = run_put_spread(filtered, spread_width, stop_mins, max_hold)
                                s = compute_stats(trades)
                                if s:
                                    label = (f"PS w{spread_width} r{min_rally*100:.1f} "
                                             f"s{stop_mins} h{max_hold} v{min_vel:.2f} "
                                             f"p{max_prox} vx{vix_min}")
                                    results[label] = dict(cfg=dict(
                                        type='put_spread', width=spread_width, mr=min_rally,
                                        sm=stop_mins, hold=max_hold, vel=min_vel,
                                        prox=max_prox, vix_min=vix_min,
                                    ), trades=trades, **s)
                                    ps_count += 1

    print(f"  Put spread configs with results: {ps_count}")

    # ── C) Long Calls — WIDE sweep ──
    print("\n  Running LONG CALL sweep …")
    lc_count = 0
    for strike_off in [0, 5, 10]:
        for min_rally in [0, 0.002, 0.005]:
            for stop_mins in [3, 5, 7]:
                for max_hold in [5, 10, 15, 30, 60]:
                    for min_vel in [0, 0.03, 0.05]:
                        for max_prox in [5, 10, 15, 20]:
                            for vix_min in [0, 15, 20]:
                                filtered = [e for e in all_entries
                                            if e['rally'] >= min_rally
                                            and e['proximity'] <= max_prox
                                            and e['velocity'] >= min_vel
                                            and e['vix'] >= vix_min]

                                if len(filtered) < MIN_TRADES:
                                    continue

                                trades = run_long_call(filtered, strike_off, stop_mins, max_hold)
                                s = compute_stats(trades)
                                if s:
                                    label = (f"LC off{strike_off} r{min_rally*100:.1f} "
                                             f"s{stop_mins} h{max_hold} v{min_vel:.2f} "
                                             f"p{max_prox} vx{vix_min}")
                                    results[label] = dict(cfg=dict(
                                        type='long_call', off=strike_off, mr=min_rally,
                                        sm=stop_mins, hold=max_hold, vel=min_vel,
                                        prox=max_prox, vix_min=vix_min,
                                    ), trades=trades, **s)
                                    lc_count += 1

    print(f"  Long call configs with results: {lc_count}")

    # ══════════════════════════════════════════════════════════════
    # RESULTS
    # ══════════════════════════════════════════════════════════════
    print_results(results, all_entries, signals)

    # Save JSON — ALL results
    out = SCRIPT_DIR / 'overnight_low_results_v4.json'
    save = {}
    by_sh = sorted(results.items(), key=lambda x: x[1]['sh'], reverse=True)
    for k, v in by_sh:
        save[k] = {kk: vv for kk, vv in v.items() if kk not in ('trades',)}
        save[k]['last_30'] = v['trades'][-30:]
    out.write_text(json.dumps(save, indent=2))
    print(f"\nJSON -> {out}  ({len(save)} configs)")
    print("Done.")


def compute_stats(trades):
    if len(trades) < MIN_TRADES:
        return None
    w = [t for t in trades if t['pnl'] > 0]
    l = [t for t in trades if t['pnl'] <= 0]
    tp = sum(t['pnl'] for t in trades)
    gw = sum(t['pnl'] for t in w)
    gl = abs(sum(t['pnl'] for t in l))
    daily = defaultdict(float)
    for t in trades:
        daily[t['date']] += t['pnl']
    rets = list(daily.values())
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 if len(rets) > 1 else 1

    def vsplit(arr):
        if len(arr) < 3:
            return dict(n=len(arr), wr=0, avg=0)
        ww = len([t for t in arr if t['pnl'] > 0])
        return dict(n=len(arr), wr=round(ww / len(arr) * 100, 1),
                     avg=round(sum(t['pnl'] for t in arr) / len(arr)))

    return dict(
        n=len(trades), tp=round(tp), ap=round(tp / len(trades)),
        wr=round(len(w) / len(trades) * 100, 1),
        aw=round(gw / len(w)) if w else 0,
        al=round(-gl / len(l)) if l else 0,
        pf=round(gw / gl, 2) if gl else 999,
        sh=round(mu / sd * 250 ** 0.5, 2) if sd else 0,
        br=round(len([t for t in trades if t.get('move', 0) > 0]) / len(trades) * 100, 1),
        sr=round(len([t for t in trades if t['stopped']]) / len(trades) * 100, 1),
        vl=vsplit([t for t in trades if t['vix'] < 18]),
        vm=vsplit([t for t in trades if 18 <= t['vix'] < 25]),
        vh=vsplit([t for t in trades if t['vix'] >= 25]),
        vel_slow=vsplit([t for t in trades if t['vel'] < 0.05]),
        vel_med=vsplit([t for t in trades if 0.05 <= t['vel'] < 0.10]),
        vel_fast=vsplit([t for t in trades if t['vel'] >= 0.10]),
    )


def print_results(results, all_entries, signals):
    by_sh = sorted(results.items(), key=lambda x: x[1]['sh'], reverse=True)

    NP = [(k, v) for k, v in by_sh if v['cfg']['type'] == 'naked_put']
    PS = [(k, v) for k, v in by_sh if v['cfg']['type'] == 'put_spread']
    LC = [(k, v) for k, v in by_sh if v['cfg']['type'] == 'long_call']

    def show_top(title, items, n=25):
        print(f"\n{'='*170}")
        print(f"  {title}  ({len(items)} total configs)")
        print(f"{'='*170}")
        if not items:
            print("  ** No configs survived **")
            return
        hdr = (f"{'Config':<55} {'N':>4} {'WR':>6} {'Avg$':>8} {'Total$':>10} {'PF':>5} "
               f"{'Sh':>6} {'BR':>5} {'SR':>5} | "
               f"{'V<18':>10} {'V18-25':>10} {'V25+':>10} | "
               f"{'VelSlow':>10} {'VelMed':>10} {'VelFast':>10}")
        print(hdr)
        print(f"{'─'*170}")
        for k, r in items[:n]:
            vl = f"{r['vl']['n']:>2}/{r['vl']['wr']:.0f}%" if r['vl']['n'] > 0 else "  —"
            vm = f"{r['vm']['n']:>2}/{r['vm']['wr']:.0f}%" if r['vm']['n'] > 0 else "  —"
            vh = f"{r['vh']['n']:>2}/{r['vh']['wr']:.0f}%" if r['vh']['n'] > 0 else "  —"
            vs = f"{r['vel_slow']['n']:>2}/{r['vel_slow']['wr']:.0f}%" if r['vel_slow']['n'] > 0 else "  —"
            vmed = f"{r['vel_med']['n']:>2}/{r['vel_med']['wr']:.0f}%" if r['vel_med']['n'] > 0 else "  —"
            vf = f"{r['vel_fast']['n']:>2}/{r['vel_fast']['wr']:.0f}%" if r['vel_fast']['n'] > 0 else "  —"
            print(f"{k:<55} {r['n']:>4} {r['wr']:>5.1f}% {r['ap']:>8,} {r['tp']:>10,} "
                  f"{r['pf']:>5.1f} {r['sh']:>6.2f} {r['br']:>4.0f}% {r['sr']:>4.0f}% | "
                  f"{vl:>10} {vm:>10} {vh:>10} | {vs:>10} {vmed:>10} {vf:>10}")

    # Show tables sorted by Sharpe
    show_top("TOP 25 NAKED SHORT PUTS (by Sharpe) — REAL OPTION PRICES", NP)
    show_top("TOP 25 PUT SPREADS (by Sharpe) — REAL OPTION PRICES", PS)
    show_top("TOP 25 LONG CALLS (by Sharpe) — REAL OPTION PRICES", LC)

    # Also show by MOST TRADES for statistical significance
    def show_by_n(title, items, n=15):
        items_by_n = sorted(items, key=lambda x: x[1]['n'], reverse=True)
        print(f"\n{'='*170}")
        print(f"  {title}  (sorted by trade count)")
        print(f"{'='*170}")
        if not items_by_n:
            print("  ** No configs **")
            return
        hdr = (f"{'Config':<55} {'N':>4} {'WR':>6} {'Avg$':>8} {'Total$':>10} {'PF':>5} "
               f"{'Sh':>6} {'BR':>5} {'SR':>5}")
        print(hdr)
        print(f"{'─'*100}")
        for k, r in items_by_n[:n]:
            print(f"{k:<55} {r['n']:>4} {r['wr']:>5.1f}% {r['ap']:>8,} {r['tp']:>10,} "
                  f"{r['pf']:>5.1f} {r['sh']:>6.2f} {r['br']:>4.0f}% {r['sr']:>4.0f}%")

    show_by_n("NAKED PUTS — MOST TRADES", NP)
    show_by_n("PUT SPREADS — MOST TRADES", PS)
    show_by_n("LONG CALLS — MOST TRADES", LC)

    # ── Deep dive on best of each type ──
    for title, items in [("NAKED PUT", NP), ("PUT SPREAD", PS), ("LONG CALL", LC)]:
        if not items:
            print(f"\n  No {title} configs survived.\n")
            continue

        # Deep dive on best Sharpe AND most-traded config
        configs_to_show = []
        configs_to_show.append(("Best Sharpe", items[0]))
        # Also show best config with most trades (if different)
        by_n = sorted(items, key=lambda x: (-x[1]['n'], -x[1]['sh']))
        if by_n[0][0] != items[0][0]:
            configs_to_show.append(("Most Traded", by_n[0]))

        for subtitle, (k, r) in configs_to_show:
            print(f"\n\n{'='*120}")
            print(f"DEEP DIVE — {subtitle} {title}: {k}")
            print(f"{'='*120}")
            print(f"N={r['n']}  WR={r['wr']}%  Sharpe={r['sh']}  PF={r['pf']}  Total=${r['tp']:,}  Avg=${r['ap']:,}")
            print(f"Bounce Rate={r['br']}%  Stop Rate={r['sr']}%  Avg Win=${r['aw']:,}  Avg Loss=${r['al']:,}")
            print(f"\nVIX:  <18: n={r['vl']['n']} WR={r['vl']['wr']}% avg=${r['vl']['avg']:,}  "
                  f"18-25: n={r['vm']['n']} WR={r['vm']['wr']}% avg=${r['vm']['avg']:,}  "
                  f"25+: n={r['vh']['n']} WR={r['vh']['wr']}% avg=${r['vh']['avg']:,}")
            print(f"VEL:  Slow: n={r['vel_slow']['n']} WR={r['vel_slow']['wr']}% avg=${r['vel_slow']['avg']:,}  "
                  f"Med: n={r['vel_med']['n']} WR={r['vel_med']['wr']}% avg=${r['vel_med']['avg']:,}  "
                  f"Fast: n={r['vel_fast']['n']} WR={r['vel_fast']['wr']}% avg=${r['vel_fast']['avg']:,}")

            by_year = defaultdict(list)
            for t in r['trades']:
                by_year[t['date'][:4]].append(t)
            print(f"\nYearly:")
            for y in sorted(by_year):
                yt = by_year[y]
                yp = sum(t['pnl'] for t in yt)
                yw = len([t for t in yt if t['pnl'] > 0])
                print(f"  {y}: {len(yt):>4}T  WR {yw / len(yt) * 100:>5.1f}%  total ${yp:>10,}  avg ${yp // len(yt):>7,}")

            print(f"\nAll trades:")
            if r['cfg']['type'] == 'put_spread':
                print(f"{'Date':<11} {'Ent':>5} {'Ext':>5} {'ONL':>7} {'Rlly%':>6} {'Vel':>5} {'EPx':>7} {'XPx':>7} {'Mv':>6} {'Cred':>5} {'Debt':>5} {'PnL':>8} {'Hm':>3} {'St':>3} {'VIX':>5}")
                for t in r['trades']:
                    st = 'X' if t['stopped'] else ''
                    print(f"{t['date']:<11} {t['et']:>5} {t['xt']:>5} {t['onl']:>7.0f} {t['rally']:>5.2f}% {t['vel']:>5.3f} {t['epx']:>7.0f} {t['xpx']:>7.0f} {t['move']:>+6.1f} {t['credit']:>5.2f} {t['debit']:>5.2f} {t['pnl']:>+8,} {t['hm']:>3} {st:>3} {t['vix']:>5.1f}")
            else:
                print(f"{'Date':<11} {'Ent':>5} {'Ext':>5} {'ONL':>7} {'Rlly%':>6} {'Vel':>5} {'EPx':>7} {'XPx':>7} {'Mv':>6} {'OptE':>6} {'OptX':>6} {'PnL':>8} {'Hm':>3} {'St':>3} {'VIX':>5}")
                for t in r['trades']:
                    st = 'X' if t['stopped'] else ''
                    print(f"{t['date']:<11} {t['et']:>5} {t['xt']:>5} {t['onl']:>7.0f} {t['rally']:>5.2f}% {t['vel']:>5.3f} {t['epx']:>7.0f} {t['xpx']:>7.0f} {t['move']:>+6.1f} {t.get('opt_entry',0):>6.2f} {t.get('opt_exit',0):>6.2f} {t['pnl']:>+8,} {t['hm']:>3} {st:>3} {t['vix']:>5.1f}")

    # ── Pure bounce analysis (underlying only) ──
    print(f"\n\n{'='*120}")
    print("PURE UNDERLYING BOUNCE (no option model)")
    print("After SPX touches within Xpts of overnight low, where is it N min later?")
    print(f"{'='*120}")

    for mr_pct in [0, 0.2, 0.5, 0.8]:
        mr = mr_pct / 100
        print(f"\n— Min ON rally >= {mr_pct}% —")
        for prox in [5, 10, 15, 20]:
            for hold in [5, 10, 15, 30, 60]:
                sigs = []
                for e in all_entries:
                    if e['rally'] < mr or e['proximity'] > prox:
                        continue
                    xi = min(e['entry_idx'] + hold, len(e['spx']) - 1)
                    sigs.append(dict(
                        move=e['spx'][xi]['c'] - e['entry_price'],
                        vix=e['vix'], vel=e['velocity'],
                    ))
                if len(sigs) < 5:
                    continue
                moves = [s['move'] for s in sigs]
                br = len([m for m in moves if m > 0]) / len(moves) * 100
                am = sum(moves) / len(moves)
                fast = [s for s in sigs if s['vel'] >= 0.10]
                med = [s for s in sigs if 0.05 <= s['vel'] < 0.10]
                slow = [s for s in sigs if s['vel'] < 0.05]
                extras = []
                if len(fast) >= 3:
                    fbr = len([s for s in fast if s['move'] > 0]) / len(fast) * 100
                    extras.append(f"Fast({len(fast)}):{fbr:.0f}%")
                if len(med) >= 3:
                    mbr = len([s for s in med if s['move'] > 0]) / len(med) * 100
                    extras.append(f"Med({len(med)}):{mbr:.0f}%")
                if len(slow) >= 3:
                    sbr = len([s for s in slow if s['move'] > 0]) / len(slow) * 100
                    extras.append(f"Slow({len(slow)}):{sbr:.0f}%")
                estr = "  " + "  ".join(extras) if extras else ""
                print(f"  P{prox:>2} H{hold:>2}m: n={len(sigs):>4}, BR={br:>5.1f}%, AvgMv={am:>+6.2f}{estr}")


if __name__ == '__main__':
    main()
