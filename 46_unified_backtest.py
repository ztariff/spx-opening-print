#!/usr/bin/env python3
"""
Unified Backtest — ALL Opening Print Strategies
=================================================
Forward-walking signal detection, real option prices from Polygon cache,
no hindsight, no fabricated data.

Strategies:
  Opening (fire at 9:31 first-bar close):
    - spx_original: bullish FB + VIX >= 16.  PT 50pts, SL 10pts, TS 30m
    - edge_a:       bullish FB.              Trail 0.15%, SL 5pts, TS 15m
    - edge_b:       bullish FB, ret >= 0.05%.Trail 0.05%, SL 15pts, TS 30m
    - edge_c:       bearish FB. BUY PUT.     Trail 0.15%, SL 3pts, TS 60m
    - edge_d:       bullish FB, no spx_orig. Trail 0.20%, SL 5pts, TS 30m
    - edge_e:       bullish FB.              PT 8pts, SL 2pts, TS 15m
    - edge_f:       bullish FB + spx_orig.   Trail 0.05%, SL 15pts, TS 30m
    - qqq:          QQQ bullish FB, ret>=0.10%. Trail 0.05%, SL(pct) 0.10%, TS 30m

  Velocity (scan all day for intraday drops):
    - vel_panic_fade: drop>=0.30%, vel 0.10-1.0%/min. OPT trail 10%, SL 30%, TS 5m
    - vel_dip_buy:    drop>=0.30%, vel 0.05-0.10%/min, VIX>=18. OPT trail 15%, SL 40%, TS 30m

  Rip Fade (bear call spread — already backtested in 45_rip_spread_v4.py, imported here)

Run:  python3 46_unified_backtest.py
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

SPX_1MIN_CSV = SCRIPT_DIR / 'spx_1min_bars.csv'
QQQ_1MIN_CSV = SCRIPT_DIR / 'qqq_1min_bars.csv'
SPX_DAILY_CSV = SCRIPT_DIR / 'spx_daily_bars.csv'
VIX_DAILY_CSV = SCRIPT_DIR / 'vix_daily_bars.csv'

OUT_DIR = SCRIPT_DIR / 'backtest_results'
OUT_DIR.mkdir(exist_ok=True)

START_DATE = '2018-01-03'

_last_call = 0
RATE_DELAY = 0.15

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_1min_bars(csv_path, label=''):
    """Load 1-min bars from CSV, return dict of date -> list of bars."""
    print(f"Loading {label or csv_path} …")
    by_date = defaultdict(list)

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960:  # 9:30 - 16:00
                continue
            by_date[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
            })

    for d in by_date:
        by_date[d].sort(key=lambda x: x['mins'])
    print(f"  {len(by_date)} days loaded")
    return by_date


def load_daily(csv_path):
    """Load daily bars, return dict of date -> {open, high, low, close}."""
    data = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            data[row['date']] = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            }
    return data


def compute_50dma(daily_data):
    """Compute 50-day SMA of close prices. Return dict date -> sma50."""
    dates = sorted(daily_data.keys())
    closes = [daily_data[d]['close'] for d in dates]
    sma = {}
    for i in range(49, len(dates)):
        sma[dates[i]] = sum(closes[i-49:i+1]) / 50
    return sma


# ═══════════════════════════════════════════════════════════════
# POLYGON OPTION DATA
# ═══════════════════════════════════════════════════════════════

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


# In-memory option cache to avoid repeated disk reads
_mem_opt_cache = {}

def fetch_option_bars(date_str, strike, opt_type='C', underlying='SPX'):
    """Fetch 1-min option bars from Polygon with disk + memory caching."""
    parts = date_str.split('-')
    yy, mm, dd = parts[0][2:], parts[1], parts[2]
    sc = str(int(strike * 1000)).zfill(8)

    if underlying == 'SPX':
        ticker = f"O:SPXW{yy}{mm}{dd}{opt_type}{sc}"
    elif underlying == 'QQQ':
        ticker = f"O:QQQ{yy}{mm}{dd}{opt_type}{sc}"
    else:
        return []

    key = f"{ticker}_{date_str}".replace(':', '_')

    # Check memory cache first (fastest)
    if key in _mem_opt_cache:
        return _mem_opt_cache[key]

    # Check disk cache
    cache = OPT_CACHE_DIR / f"{key}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        _mem_opt_cache[key] = data
        return data

    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute"
           f"/{date_str}/{date_str}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    data = polygon_get(url)
    if data and data.get('results'):
        cache.write_text(json.dumps(data['results']))
        _mem_opt_cache[key] = data['results']
        return data['results']
    cache.write_text('[]')
    _mem_opt_cache[key] = []
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


def find_opt_bar(opt_bars, target_mins, max_diff=3):
    """Find option bar closest to target minute-of-day."""
    best, best_diff = None, 9999
    for bar in opt_bars:
        et = ts_et(bar['t'])
        mins = et_m(et)
        diff = abs(mins - target_mins)
        if diff < best_diff:
            best_diff = diff
            best = bar
    return best if best_diff <= max_diff else None


def index_opt_bars(opt_bars):
    """Index option bars by minute-of-day for fast lookup."""
    idx = {}
    for bar in opt_bars:
        et = ts_et(bar['t'])
        mins = et_m(et)
        idx[mins] = bar
    return idx


# ═══════════════════════════════════════════════════════════════
# STRATEGY DEFINITIONS
# ═══════════════════════════════════════════════════════════════

STRATEGIES = {
    'spx_original': {
        'name': 'SPX Original',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': None, 'vix_min': 16,
        'require_spx_orig': None,  # N/A — this IS spx_original
        'exit_mode': 'fixed',  # PT/SL/TS in points
        'pt_pts': 50, 'sl_pts': 10, 'trail_pct': None,
        'ts_min': 30,
        'base_risk': 150000, 'min_risk': 25000, 'max_risk': 200000,
    },
    'edge_a': {
        'name': 'Edge A — SPX Scalp',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': None, 'vix_min': None,
        'require_spx_orig': None,
        'exit_mode': 'trail',
        'pt_pts': None, 'sl_pts': 5, 'trail_pct': 0.15,
        'ts_min': 15,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'edge_b': {
        'name': 'Edge B — Filtered Scalp',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': 0.05, 'vix_min': None,
        'require_spx_orig': None,
        'exit_mode': 'trail',
        'pt_pts': None, 'sl_pts': 15, 'trail_pct': 0.05,
        'ts_min': 30,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'edge_c': {
        'name': 'Edge C — Bearish Short',
        'underlying': 'SPX', 'opt_type': 'P', 'direction': -1,
        'entry': 'opening',
        'fb_bullish': False,  # requires BEARISH first bar
        'fb_min_pct': None, 'vix_min': None,
        'require_spx_orig': None,
        'exit_mode': 'trail',
        'pt_pts': None, 'sl_pts': 3, 'trail_pct': 0.15,
        'ts_min': 60,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'edge_d': {
        'name': 'Edge D — Missed Days',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': None, 'vix_min': None,
        'require_spx_orig': False,  # only when spx_original does NOT fire
        'exit_mode': 'trail',
        'pt_pts': None, 'sl_pts': 5, 'trail_pct': 0.20,
        'ts_min': 30,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'edge_e': {
        'name': 'Edge E — Ultra Scalp',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': None, 'vix_min': None,
        'require_spx_orig': None,
        'exit_mode': 'fixed',
        'pt_pts': 8, 'sl_pts': 2, 'trail_pct': None,
        'ts_min': 15,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'edge_f': {
        'name': 'Edge F — Current + Trail',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': None, 'vix_min': None,
        'require_spx_orig': True,  # only when spx_original fires
        'exit_mode': 'trail',
        'pt_pts': None, 'sl_pts': 15, 'trail_pct': 0.05,
        'ts_min': 30,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'qqq': {
        'name': 'QQQ Scalp',
        'underlying': 'QQQ', 'opt_type': 'C', 'direction': 1,
        'entry': 'opening',
        'fb_bullish': True, 'fb_min_pct': 0.10, 'vix_min': None,
        'require_spx_orig': None,
        'exit_mode': 'trail_pct',  # percentage-based SL instead of points
        'pt_pts': None, 'sl_pct': 0.10, 'trail_pct': 0.05,
        'ts_min': 30,
        'base_risk': 75000, 'min_risk': 50000, 'max_risk': 200000,
    },
    'vel_panic_fade': {
        'name': 'Panic Fade',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'velocity',
        'vel_drop_pct': 0.30, 'vel_min_speed': 0.10, 'vel_max_speed': 1.0,
        'vix_min': None,
        'exit_mode': 'option_trail',  # exit based on option price, not underlying
        'opt_trail_pct': 10.0, 'opt_sl_pct': 30.0,
        'ts_min': 5, 'tier': 1,
        'active_start': 575, 'active_end': 900,  # 9:35-15:00
        'base_risk': 50000, 'min_risk': 25000, 'max_risk': 100000,
    },
    'vel_dip_buy': {
        'name': 'Dip Buy',
        'underlying': 'SPX', 'opt_type': 'C', 'direction': 1,
        'entry': 'velocity',
        'vel_drop_pct': 0.30, 'vel_min_speed': 0.05, 'vel_max_speed': 0.10,
        'vix_min': 18,
        'exit_mode': 'option_trail',
        'opt_trail_pct': 15.0, 'opt_sl_pct': 40.0,
        'ts_min': 30, 'tier': 2,
        'active_start': 575, 'active_end': 900,
        'base_risk': 35000, 'min_risk': 25000, 'max_risk': 100000,
    },
}


# ═══════════════════════════════════════════════════════════════
# RISK SIZING
# ═══════════════════════════════════════════════════════════════

def compute_risk(strat, vix_open, fb_ret):
    """Match dashboard computeRisk() exactly."""
    if strat.get('entry') == 'velocity':
        return strat['base_risk']  # velocity uses dynamic sizing at runtime; for backtest use base

    risk = strat['base_risk']
    if vix_open and vix_open >= 25:
        risk = min(risk * 1.3, strat['max_risk'])
    if abs(fb_ret) > 0.20:
        risk = min(risk * 1.2, strat['max_risk'])

    return max(strat['min_risk'], min(strat['max_risk'], round(risk / 1000) * 1000))


def compute_contracts(risk, opt_price):
    cost = opt_price * 100
    return max(1, int(risk / cost))


# ═══════════════════════════════════════════════════════════════
# OPENING STRATEGY — SIGNAL DETECTION + EXIT SIMULATION
# ═══════════════════════════════════════════════════════════════

def run_opening_strategies(spx_data, qqq_data, vix_data, spx_sma50):
    """
    For each trading day, evaluate first bar to determine which opening
    strategies fire, then simulate exits bar-by-bar on the underlying.
    Finally fetch real option prices for entry/exit to compute P&L.
    """
    all_trades = defaultdict(list)  # strat_id -> list of trades
    dates = sorted(set(spx_data.keys()) & set(vix_data.keys()))

    # Opening strategies
    opening_strats = {k: v for k, v in STRATEGIES.items() if v['entry'] == 'opening'}

    print(f"\n{'='*80}")
    print(f"OPENING STRATEGIES — processing {len(dates)} trading days")
    print(f"{'='*80}")

    api_calls_needed = 0
    processed = 0

    for date_str in dates:
        if date_str < START_DATE:
            continue

        spx_bars = spx_data.get(date_str, [])
        qqq_bars = qqq_data.get(date_str, [])
        vd = vix_data.get(date_str)
        if not vd or len(spx_bars) < 5:
            continue

        vix_open = vd['open']
        vix_close = vd['close']

        # ── First Bar evaluation (9:30-9:31) ──
        spx_fb = spx_bars[0] if spx_bars and spx_bars[0]['mins'] == 570 else None
        qqq_fb = qqq_bars[0] if qqq_bars and qqq_bars[0]['mins'] == 570 else None

        if not spx_fb:
            # Try 9:31 bar
            spx_fb = next((b for b in spx_bars if b['mins'] == 571), None)
        if not qqq_fb:
            qqq_fb = next((b for b in qqq_bars if b['mins'] == 571), None)

        spx_fb_bullish = spx_fb and spx_fb['c'] > spx_fb['o']
        spx_fb_ret = ((spx_fb['c'] - spx_fb['o']) / spx_fb['o'] * 100) if spx_fb else 0
        spx_entry_price = spx_fb['c'] if spx_fb else None

        qqq_fb_bullish = qqq_fb and qqq_fb['c'] > qqq_fb['o']
        qqq_fb_ret = ((qqq_fb['c'] - qqq_fb['o']) / qqq_fb['o'] * 100) if qqq_fb else 0
        qqq_entry_price = qqq_fb['c'] if qqq_fb else None

        # SPX Original fires?
        spx_original_fired = spx_fb_bullish and vix_open >= 16

        # Check each opening strategy
        for strat_id, strat in opening_strats.items():
            # ── SIGNAL DETECTION ──
            if strat['underlying'] == 'QQQ':
                if not qqq_fb:
                    continue
                fb_bullish = qqq_fb_bullish
                fb_ret = qqq_fb_ret
                entry_price = qqq_entry_price
                bars = qqq_bars
            else:
                if not spx_fb:
                    continue
                fb_bullish = spx_fb_bullish
                fb_ret = spx_fb_ret
                entry_price = spx_entry_price
                bars = spx_bars

            # Bullish/bearish check
            if strat['fb_bullish'] is True and not fb_bullish:
                continue
            if strat['fb_bullish'] is False and fb_bullish:
                continue  # edge_c needs BEARISH

            # First bar return minimum
            if strat.get('fb_min_pct') and abs(fb_ret) < strat['fb_min_pct']:
                continue

            # VIX minimum
            if strat.get('vix_min') and vix_open < strat['vix_min']:
                continue

            # SPX Original dependency
            if strat.get('require_spx_orig') is True and not spx_original_fired:
                continue
            if strat.get('require_spx_orig') is False and spx_original_fired:
                continue

            # ── SIGNAL FIRED — simulate exit on underlying ──
            entry_bar_idx = 0  # first bar (9:30 or 9:31)
            entry_mins = bars[entry_bar_idx]['mins']

            # Compute exit levels
            direction = strat['direction']
            ts_min = strat['ts_min']
            ts_deadline = entry_mins + ts_min

            sl_level = None
            pt_level = None

            if strat['exit_mode'] == 'fixed':
                pt_pts = strat.get('pt_pts', 50)
                sl_pts = strat.get('sl_pts', 10)
                if direction == 1:
                    pt_level = entry_price + pt_pts
                    sl_level = entry_price - sl_pts
                else:
                    pt_level = entry_price - pt_pts
                    sl_level = entry_price + sl_pts
            elif strat['exit_mode'] == 'trail':
                sl_pts = strat.get('sl_pts', 15)
                if direction == 1:
                    sl_level = entry_price - sl_pts
                else:
                    sl_level = entry_price + sl_pts
            elif strat['exit_mode'] == 'trail_pct':
                sl_pct = strat.get('sl_pct', 0.10)
                sl_dollar = entry_price * sl_pct / 100
                if direction == 1:
                    sl_level = entry_price - sl_dollar
                else:
                    sl_level = entry_price + sl_dollar

            trail_pct = strat.get('trail_pct')

            # Walk forward bar-by-bar
            peak_price = entry_price
            trough_price = entry_price
            exit_reason = 'time_stop'
            exit_bar_idx = min(entry_bar_idx + ts_min, len(bars) - 1)

            for j in range(entry_bar_idx + 1, len(bars)):
                bar = bars[j]
                price = bar['c']

                # Time stop
                if bar['mins'] >= ts_deadline:
                    exit_bar_idx = j
                    exit_reason = 'time_stop'
                    break

                if direction == 1:
                    if price > peak_price:
                        peak_price = price

                    # Fixed PT
                    if pt_level and price >= pt_level:
                        exit_bar_idx = j
                        exit_reason = 'profit_target'
                        break

                    # Hard SL
                    if sl_level and price <= sl_level:
                        exit_bar_idx = j
                        exit_reason = 'stop_loss'
                        break

                    # Trailing stop
                    if trail_pct and peak_price > entry_price:
                        trail_level = peak_price * (1 - trail_pct / 100)
                        if price <= trail_level:
                            exit_bar_idx = j
                            exit_reason = 'trailing_stop'
                            break
                else:
                    # Bearish direction (edge_c)
                    if price < trough_price:
                        trough_price = price

                    # Hard SL (price going UP is bad)
                    if sl_level and price >= sl_level:
                        exit_bar_idx = j
                        exit_reason = 'stop_loss'
                        break

                    # Trailing stop on downside
                    if trail_pct and trough_price < entry_price:
                        trail_level = trough_price * (1 + trail_pct / 100)
                        if price >= trail_level:
                            exit_bar_idx = j
                            exit_reason = 'trailing_stop'
                            break

            exit_bar = bars[exit_bar_idx]
            exit_price = exit_bar['c']
            hold_mins = exit_bar['mins'] - entry_mins

            all_trades[strat_id].append({
                'date': date_str,
                'entry_time': bars[entry_bar_idx]['time'],
                'exit_time': exit_bar['time'],
                'entry_price': round(entry_price, 2),
                'exit_price': round(exit_price, 2),
                'direction': direction,
                'hold_mins': hold_mins,
                'exit_reason': exit_reason,
                'vix_open': round(vix_open, 1),
                'fb_ret': round(fb_ret, 3),
                # Option P&L fields filled in next phase
                'opt_entry': None,
                'opt_exit': None,
                'opt_contracts': None,
                'pnl': None,
                'risk': compute_risk(strat, vix_open, fb_ret),
            })

        processed += 1
        if processed % 200 == 0:
            print(f"  [{processed}] signals scanned…", flush=True)

    # Print signal counts
    print(f"\n  Signal counts:")
    for sid, trades in sorted(all_trades.items()):
        print(f"    {sid:20s}: {len(trades):>5} trades")

    return all_trades


# ═══════════════════════════════════════════════════════════════
# VELOCITY STRATEGY — SIGNAL DETECTION + EXIT SIMULATION
# ═══════════════════════════════════════════════════════════════

def run_velocity_strategies(spx_data, vix_data):
    """
    For each trading day, scan bar-by-bar for intraday drops matching
    velocity criteria. One signal per strategy per day.
    Exit is based on OPTION price (trail/SL on the option itself).
    """
    all_trades = defaultdict(list)
    vel_strats = {k: v for k, v in STRATEGIES.items() if v['entry'] == 'velocity'}
    dates = sorted(set(spx_data.keys()) & set(vix_data.keys()))

    print(f"\n{'='*80}")
    print(f"VELOCITY STRATEGIES — scanning {len(dates)} trading days")
    print(f"{'='*80}")

    processed = 0

    for date_str in dates:
        if date_str < START_DATE:
            continue

        bars = spx_data.get(date_str, [])
        vd = vix_data.get(date_str)
        if not vd or len(bars) < 30:
            continue

        vix_open = vd['open']

        for strat_id, strat in vel_strats.items():
            # VIX filter
            if strat.get('vix_min') and vix_open < strat['vix_min']:
                continue

            active_start = strat.get('active_start', 575)
            active_end = strat.get('active_end', 900)
            vel_drop_pct = strat['vel_drop_pct']
            vel_min_speed = strat['vel_min_speed']
            vel_max_speed = strat['vel_max_speed']

            # Track rolling high and scan for drops
            rolling_high = 0
            peak_idx = 0
            signal_bar_idx = None

            for i, bar in enumerate(bars):
                if bar['mins'] < active_start or bar['mins'] >= active_end:
                    if bar['mins'] < active_start:
                        # Still track high before active window
                        if bar['h'] > rolling_high:
                            rolling_high = bar['h']
                            peak_idx = i
                    continue

                if bar['h'] > rolling_high:
                    rolling_high = bar['h']
                    peak_idx = i

                if rolling_high <= 0:
                    continue

                drop_pct = (rolling_high - bar['c']) / rolling_high * 100
                mins_since_peak = i - peak_idx
                velocity = drop_pct / mins_since_peak if mins_since_peak > 0 else 0

                if (drop_pct >= vel_drop_pct and
                    velocity >= vel_min_speed and
                    velocity <= vel_max_speed):
                    signal_bar_idx = i
                    break  # one signal per day per strategy

            if signal_bar_idx is None:
                continue

            entry_bar = bars[signal_bar_idx]
            entry_price = entry_bar['c']
            entry_mins = entry_bar['mins']

            all_trades[strat_id].append({
                'date': date_str,
                'entry_time': entry_bar['time'],
                'exit_time': None,  # filled after option price walk
                'entry_price': round(entry_price, 2),
                'exit_price': None,
                'direction': 1,
                'hold_mins': None,
                'exit_reason': None,
                'vix_open': round(vix_open, 1),
                'drop_pct': round(drop_pct, 3),
                'velocity': round(velocity, 4),
                'entry_bar_idx': signal_bar_idx,
                'bars': bars,  # keep reference for option exit simulation
                'opt_entry': None,
                'opt_exit': None,
                'opt_contracts': None,
                'pnl': None,
                'risk': strat['base_risk'],
            })

        processed += 1
        if processed % 200 == 0:
            print(f"  [{processed}] days scanned…", flush=True)

    print(f"\n  Signal counts:")
    for sid, trades in sorted(all_trades.items()):
        print(f"    {sid:20s}: {len(trades):>5} trades")

    return all_trades


# ═══════════════════════════════════════════════════════════════
# OPTION P&L — FETCH REAL PRICES AND COMPUTE
# ═══════════════════════════════════════════════════════════════

def prefetch_options(trades_by_strat):
    """Pre-fetch all unique option contracts into memory to avoid redundant disk reads."""
    print(f"\n  Pre-fetching option data into memory…")
    needed = set()
    for strat_id, trades in trades_by_strat.items():
        strat = STRATEGIES[strat_id]
        underlying = strat['underlying']
        opt_type = strat['opt_type']
        for trade in trades:
            entry_price = trade['entry_price']
            if underlying == 'SPX':
                strike = round(entry_price / 5) * 5
            else:
                strike = round(entry_price)
            needed.add((trade['date'], strike, opt_type, underlying))

    print(f"  {len(needed)} unique option contracts needed")
    done = 0
    for date_str, strike, opt_type, underlying in sorted(needed):
        fetch_option_bars(date_str, strike, opt_type, underlying)
        done += 1
        if done % 200 == 0:
            print(f"    [{done}/{len(needed)}] loaded…", flush=True)
    print(f"  Pre-fetch complete: {len(needed)} contracts in memory")


def price_opening_trades(trades_by_strat):
    """
    For opening strategies: fetch option bar at entry time and exit time,
    compute P&L from real option prices.
    """
    print(f"\n{'='*80}")
    print(f"PRICING OPENING TRADES — fetching real option data")
    print(f"{'='*80}")

    # Pre-fetch all options into memory first
    prefetch_options(trades_by_strat)

    total = sum(len(t) for t in trades_by_strat.values())
    done = 0
    failed = 0

    for strat_id, trades in trades_by_strat.items():
        strat = STRATEGIES[strat_id]
        opt_type = strat['opt_type']
        direction = strat['direction']
        underlying = strat['underlying']

        for trade in trades:
            done += 1
            if done % 50 == 0:
                print(f"  [{done}/{total}] pricing…", flush=True)

            entry_price_und = trade['entry_price']
            date_str = trade['date']

            # Compute strike
            if underlying == 'SPX':
                strike = round(entry_price_und / 5) * 5
            else:
                strike = round(entry_price_und)

            # Fetch option bars
            opt_bars = fetch_option_bars(date_str, strike, opt_type, underlying)
            if not opt_bars:
                failed += 1
                continue

            entry_mins = int(trade['entry_time'][:2]) * 60 + int(trade['entry_time'][3:5])
            exit_mins = int(trade['exit_time'][:2]) * 60 + int(trade['exit_time'][3:5])

            opt_at_entry = find_opt_bar(opt_bars, entry_mins)
            opt_at_exit = find_opt_bar(opt_bars, exit_mins)

            if not opt_at_entry or not opt_at_exit:
                failed += 1
                continue

            opt_entry_price = opt_at_entry['c']
            opt_exit_price = opt_at_exit['c']

            if opt_entry_price < 0.05:
                failed += 1
                continue

            contracts = compute_contracts(trade['risk'], opt_entry_price)
            pnl = round(direction * (opt_exit_price - opt_entry_price) * contracts * 100)

            trade['opt_entry'] = round(opt_entry_price, 2)
            trade['opt_exit'] = round(opt_exit_price, 2)
            trade['opt_contracts'] = contracts
            trade['pnl'] = pnl
            trade['strike'] = strike

    priced = sum(1 for trades in trades_by_strat.values() for t in trades if t['pnl'] is not None)
    print(f"  Priced {priced}/{total} trades ({failed} missing option data)")


def price_velocity_trades(trades_by_strat):
    """
    For velocity strategies: fetch option bars, simulate trail/SL exit
    on option price (not underlying), compute P&L.
    """
    print(f"\n{'='*80}")
    print(f"PRICING VELOCITY TRADES — option-price-based exits")
    print(f"{'='*80}")

    total = sum(len(t) for t in trades_by_strat.values())
    done = 0
    failed = 0

    for strat_id, trades in trades_by_strat.items():
        strat = STRATEGIES[strat_id]
        opt_trail_pct = strat['opt_trail_pct']
        opt_sl_pct = strat['opt_sl_pct']
        ts_min = strat['ts_min']

        for trade in trades:
            done += 1
            if done % 50 == 0:
                print(f"  [{done}/{total}] pricing velocity…", flush=True)

            date_str = trade['date']
            entry_price_und = trade['entry_price']
            strike = round(entry_price_und / 5) * 5

            opt_bars = fetch_option_bars(date_str, strike, 'C', 'SPX')
            if not opt_bars:
                failed += 1
                # Clean up temp fields
                trade.pop('bars', None)
                trade.pop('entry_bar_idx', None)
                continue

            entry_mins = int(trade['entry_time'][:2]) * 60 + int(trade['entry_time'][3:5])
            opt_entry = find_opt_bar(opt_bars, entry_mins)

            if not opt_entry or opt_entry['c'] < 0.05:
                failed += 1
                trade.pop('bars', None)
                trade.pop('entry_bar_idx', None)
                continue

            opt_entry_price = opt_entry['c']
            contracts = compute_contracts(trade['risk'], opt_entry_price)
            ts_deadline = entry_mins + ts_min

            # Index option bars for fast lookup
            opt_idx = index_opt_bars(opt_bars)

            # Walk forward bar-by-bar on OPTION prices
            peak_opt = opt_entry_price
            exit_reason = 'time_stop'
            exit_opt_price = opt_entry_price
            exit_time = trade['entry_time']
            hold_mins = 0

            for m in range(1, ts_min + 120):  # walk up to ts_min + buffer
                target_mins = entry_mins + m

                if target_mins >= ts_deadline:
                    # Time stop — use last known price
                    ob = opt_idx.get(target_mins) or opt_idx.get(target_mins - 1)
                    if ob:
                        exit_opt_price = ob['c']
                        et = ts_et(ob['t'])
                        exit_time = f"{et.hour:02d}:{et.minute:02d}"
                    exit_reason = 'time_stop'
                    hold_mins = m
                    break

                ob = opt_idx.get(target_mins)
                if not ob:
                    continue

                cur_price = ob['c']
                if cur_price > peak_opt:
                    peak_opt = cur_price

                # Trailing stop
                if opt_trail_pct and peak_opt > opt_entry_price:
                    trail_level = peak_opt * (1 - opt_trail_pct / 100)
                    if cur_price <= trail_level:
                        exit_opt_price = cur_price
                        et = ts_et(ob['t'])
                        exit_time = f"{et.hour:02d}:{et.minute:02d}"
                        exit_reason = 'trailing_stop'
                        hold_mins = m
                        break

                # SL on option price
                if opt_sl_pct:
                    sl_level = opt_entry_price * (1 - opt_sl_pct / 100)
                    if cur_price <= sl_level:
                        exit_opt_price = cur_price
                        et = ts_et(ob['t'])
                        exit_time = f"{et.hour:02d}:{et.minute:02d}"
                        exit_reason = 'stop_loss'
                        hold_mins = m
                        break

                exit_opt_price = cur_price
                et = ts_et(ob['t'])
                exit_time = f"{et.hour:02d}:{et.minute:02d}"
                hold_mins = m

            pnl = round((exit_opt_price - opt_entry_price) * contracts * 100)

            trade['opt_entry'] = round(opt_entry_price, 2)
            trade['opt_exit'] = round(exit_opt_price, 2)
            trade['opt_contracts'] = contracts
            trade['pnl'] = pnl
            trade['exit_time'] = exit_time
            trade['hold_mins'] = hold_mins
            trade['exit_reason'] = exit_reason
            trade['strike'] = strike

            # Clean up temp fields
            trade.pop('bars', None)
            trade.pop('entry_bar_idx', None)

    priced = sum(1 for trades in trades_by_strat.values() for t in trades if t['pnl'] is not None)
    print(f"  Priced {priced}/{total} trades ({failed} missing option data)")


# ═══════════════════════════════════════════════════════════════
# RIP FADE — IMPORT FROM EXISTING BACKTEST
# ═══════════════════════════════════════════════════════════════

def load_rip_fade_trades():
    """Load Rip Fade trades from the v4 backtest results if available."""
    trades_csv = SCRIPT_DIR / 'morning_rip_spread_v4_trades.csv'
    results_json = SCRIPT_DIR / 'morning_rip_spread_v4_results.json'

    if trades_csv.exists():
        trades = []
        with open(trades_csv) as f:
            for row in csv.DictReader(f):
                trades.append({
                    'date': row['date'],
                    'entry_time': row['entry_time'],
                    'exit_time': row['exit_time'],
                    'entry_price': float(row['entry_spx']),
                    'exit_price': float(row['exit_spx']),
                    'direction': -1,
                    'hold_mins': int(row['hold_mins']),
                    'exit_reason': row['exit_reason'],
                    'vix_open': float(row['vix_open']),
                    'opt_entry': float(row['credit']),
                    'opt_exit': float(row['exit_spread']),
                    'opt_contracts': int(row['contracts']),
                    'pnl': int(row['pnl']),
                    'strike': int(float(row['short_strike'])),
                    'rally_pct': float(row['rally_pct']),
                    'velocity': float(row['velocity']),
                })
        print(f"  Loaded {len(trades)} Rip Fade trades from {trades_csv}")
        return trades
    else:
        print(f"  WARNING: {trades_csv} not found — run 45_rip_spread_v4.py first")
        return []


# ═══════════════════════════════════════════════════════════════
# ANALYSIS & REPORTING
# ═══════════════════════════════════════════════════════════════

def compute_stats(trades):
    """Compute performance statistics for a list of trades."""
    priced = [t for t in trades if t['pnl'] is not None]
    if not priced:
        return None

    n = len(priced)
    pnls = [t['pnl'] for t in priced]
    wins = [t for t in priced if t['pnl'] > 0]
    losses = [t for t in priced if t['pnl'] <= 0]

    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n
    wr = len(wins) / n * 100

    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0

    # Sharpe (per-trade)
    if n > 1:
        var = sum((p - avg_pnl) ** 2 for p in pnls) / (n - 1)
        sharpe = avg_pnl / (var ** 0.5) if var > 0 else 0
    else:
        sharpe = 0

    # Max drawdown
    equity = 0
    peak_equity = 0
    max_dd = 0
    for p in pnls:
        equity += p
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

    # Profit factor
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    avg_hold = sum(t.get('hold_mins', 0) for t in priced) / n

    exit_reasons = defaultdict(int)
    for t in priced:
        exit_reasons[t.get('exit_reason', 'unknown')] += 1

    return {
        'n': n,
        'win_rate': round(wr, 1),
        'avg_pnl': round(avg_pnl),
        'total_pnl': round(total_pnl),
        'avg_win': round(avg_win),
        'avg_loss': round(avg_loss),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_dd),
        'profit_factor': round(profit_factor, 2),
        'avg_hold_mins': round(avg_hold, 1),
        'exit_reasons': dict(exit_reasons),
    }


def print_comparison(all_stats):
    """Print side-by-side comparison of all strategies."""
    print(f"\n{'='*140}")
    print(f"STRATEGY COMPARISON — Real Option P&L")
    print(f"{'='*140}")
    print(f"{'Strategy':>25s} {'N':>5} {'WR%':>6} {'AvgPnL':>9} {'TotalPnL':>11} {'AvgWin':>9} "
          f"{'AvgLoss':>9} {'Sharpe':>7} {'MaxDD':>10} {'PF':>6} {'AvgHold':>8}")
    print("-" * 140)

    ranked = sorted(all_stats.items(), key=lambda x: x[1]['sharpe'] if x[1] else -999, reverse=True)

    for strat_id, stats in ranked:
        if not stats:
            print(f"  {strat_id:>23s}   -- no priced trades --")
            continue
        name = STRATEGIES.get(strat_id, {}).get('name', strat_id)
        print(f"  {name:>23s} {stats['n']:>5} {stats['win_rate']:>5.1f}% ${stats['avg_pnl']:>8,} "
              f"${stats['total_pnl']:>10,} ${stats['avg_win']:>8,} ${stats['avg_loss']:>8,} "
              f"{stats['sharpe']:>7.3f} ${stats['max_drawdown']:>9,} {stats['profit_factor']:>5.2f} "
              f"{stats['avg_hold_mins']:>6.1f}m")

    print()

    # Exit reason breakdown
    print(f"{'Strategy':>25s} {'PT':>6} {'SL':>6} {'Trail':>6} {'TimeStop':>9}")
    print("-" * 60)
    for strat_id, stats in ranked:
        if not stats:
            continue
        name = STRATEGIES.get(strat_id, {}).get('name', strat_id)
        er = stats['exit_reasons']
        n = stats['n']
        pt = er.get('profit_target', 0)
        sl = er.get('stop_loss', 0)
        trail = er.get('trailing_stop', 0)
        ts = er.get('time_stop', 0) + er.get('hold_expired', 0)
        print(f"  {name:>23s} {pt/n*100:>5.1f}% {sl/n*100:>5.1f}% {trail/n*100:>5.1f}% {ts/n*100:>8.1f}%")


def save_results(all_trades, all_stats):
    """Save per-strategy trade CSVs and summary JSON."""
    # Summary
    summary = {}
    for strat_id, stats in all_stats.items():
        if stats:
            summary[strat_id] = stats

    summary_file = OUT_DIR / 'strategy_comparison.json'
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"  Saved summary to {summary_file}")

    # Per-strategy trade CSVs
    for strat_id, trades in all_trades.items():
        priced = [t for t in trades if t['pnl'] is not None]
        if not priced:
            continue

        # Determine fieldnames from first trade
        fields = [k for k in priced[0].keys() if k not in ('bars', 'entry_bar_idx')]
        csv_file = OUT_DIR / f'{strat_id}_trades.csv'
        with open(csv_file, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            w.writerows(priced)
        print(f"  Saved {len(priced)} trades to {csv_file}")

    # Combined equity curve CSV (all strategies, sorted by date)
    all_priced = []
    for strat_id, trades in all_trades.items():
        for t in trades:
            if t['pnl'] is not None:
                all_priced.append({
                    'date': t['date'],
                    'strategy': strat_id,
                    'pnl': t['pnl'],
                    'entry_time': t['entry_time'],
                    'exit_time': t.get('exit_time', ''),
                    'exit_reason': t.get('exit_reason', ''),
                })
    all_priced.sort(key=lambda x: (x['date'], x['entry_time']))

    eq_file = OUT_DIR / 'all_trades_combined.csv'
    with open(eq_file, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['date', 'strategy', 'pnl', 'entry_time', 'exit_time', 'exit_reason'])
        w.writeheader()
        w.writerows(all_priced)
    print(f"  Saved combined {len(all_priced)} trades to {eq_file}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 120)
    print("UNIFIED BACKTEST — ALL OPENING PRINT STRATEGIES")
    print("Forward-walking signals, real option prices, no hindsight")
    print("=" * 120)

    # Load data
    spx_1min = load_1min_bars(SPX_1MIN_CSV, 'SPX 1min')
    qqq_1min = load_1min_bars(QQQ_1MIN_CSV, 'QQQ 1min')
    spx_daily = load_daily(SPX_DAILY_CSV)
    vix_daily = load_daily(VIX_DAILY_CSV)
    spx_sma50 = compute_50dma(spx_daily)

    # Phase 1: Detect signals for opening strategies
    opening_trades = run_opening_strategies(spx_1min, qqq_1min, vix_daily, spx_sma50)

    # Phase 2: Detect signals for velocity strategies
    velocity_trades = run_velocity_strategies(spx_1min, vix_daily)

    # Phase 3: Load Rip Fade trades from existing backtest
    rip_fade_trades = load_rip_fade_trades()

    # Phase 4: Price opening trades with real option data
    price_opening_trades(opening_trades)

    # Phase 5: Price velocity trades with option-based exit simulation
    price_velocity_trades(velocity_trades)

    # Merge all trades
    all_trades = dict(opening_trades)
    all_trades.update(velocity_trades)
    if rip_fade_trades:
        all_trades['rip_fade'] = rip_fade_trades

    # Phase 6: Compute stats and compare
    all_stats = {}
    for strat_id, trades in all_trades.items():
        all_stats[strat_id] = compute_stats(trades)

    print_comparison(all_stats)

    # Phase 7: Save results
    print(f"\n{'='*80}")
    print("SAVING RESULTS")
    print(f"{'='*80}")
    save_results(all_trades, all_stats)

    # Phase 8: Equity curve analysis
    print(f"\n{'='*80}")
    print("EQUITY CURVES — cumulative P&L by strategy")
    print(f"{'='*80}")

    for strat_id in sorted(all_trades.keys()):
        trades = sorted([t for t in all_trades[strat_id] if t['pnl'] is not None],
                       key=lambda x: x['date'])
        if not trades:
            continue
        equity = 0
        peak = 0
        max_dd = 0
        name = STRATEGIES.get(strat_id, {}).get('name', strat_id)

        for t in trades:
            equity += t['pnl']
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        print(f"  {name:>23s}: final=${equity:>10,}  peak=${peak:>10,}  maxDD=${max_dd:>9,}  "
              f"trades={len(trades)}")

    # Phase 9: Best combinations
    print(f"\n{'='*80}")
    print("COMBINATION ANALYSIS — which subset produces best risk-adjusted returns?")
    print(f"{'='*80}")

    strat_ids = [s for s in all_trades.keys() if all_stats.get(s)]
    # Try all individual strategies + some combos
    from itertools import combinations

    best_combos = []
    for r in range(1, min(len(strat_ids) + 1, 7)):
        for combo in combinations(strat_ids, r):
            # Merge trades by date, compute combined daily P&L
            daily_pnl = defaultdict(float)
            total_trades = 0
            for sid in combo:
                for t in all_trades[sid]:
                    if t['pnl'] is not None:
                        daily_pnl[t['date']] += t['pnl']
                        total_trades += 1

            if total_trades < 20:
                continue

            pnls = [v for _, v in sorted(daily_pnl.items())]
            n_days = len(pnls)
            total = sum(pnls)
            avg = total / n_days if n_days > 0 else 0

            if n_days > 1:
                var = sum((p - avg) ** 2 for p in pnls) / (n_days - 1)
                sharpe = avg / (var ** 0.5) if var > 0 else 0
            else:
                sharpe = 0

            # Max drawdown
            eq = 0
            pk = 0
            mdd = 0
            for p in pnls:
                eq += p
                if eq > pk:
                    pk = eq
                dd = pk - eq
                if dd > mdd:
                    mdd = dd

            best_combos.append({
                'combo': combo,
                'n_strats': len(combo),
                'n_trades': total_trades,
                'n_days': n_days,
                'total_pnl': round(total),
                'avg_daily_pnl': round(avg),
                'sharpe': round(sharpe, 3),
                'max_dd': round(mdd),
                'calmar': round(total / mdd, 2) if mdd > 0 else 999,
            })

    best_combos.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n  TOP 30 COMBINATIONS BY SHARPE:")
    print(f"  {'Strategies':<60s} {'#Str':>4} {'#Trd':>5} {'#Days':>5} "
          f"{'TotalPnL':>11} {'AvgDaily':>9} {'Sharpe':>7} {'MaxDD':>10} {'Calmar':>7}")
    print("  " + "-" * 135)

    for c in best_combos[:30]:
        names = '+'.join(c['combo'])
        if len(names) > 58:
            names = names[:55] + '...'
        print(f"  {names:<60s} {c['n_strats']:>4} {c['n_trades']:>5} {c['n_days']:>5} "
              f"${c['total_pnl']:>10,} ${c['avg_daily_pnl']:>8,} {c['sharpe']:>7.3f} "
              f"${c['max_dd']:>9,} {c['calmar']:>6.2f}")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")


if __name__ == '__main__':
    main()
