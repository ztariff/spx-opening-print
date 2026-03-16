"""
SPX 0DTE Opening Print — Live Dashboard
========================================
Flask app that monitors all 11 strategy edges in real-time via Polygon API.
Designed for deployment on Railway.

Environment variables:
    POLYGON_API_KEY  — your Polygon.io API key
    SECRET_KEY       — Flask session key (optional, defaults to random)
"""

import os, json, time, threading, logging, re
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict
from pathlib import Path

import requests
from flask import Flask, render_template, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────
POLYGON_KEY = os.environ.get('POLYGON_API_KEY', '')
DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / 'state.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('dashboard')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# ──────────────────────────────────────────────────────────────
# POLYGON API HELPERS
# ──────────────────────────────────────────────────────────────
BASE = 'https://api.polygon.io'

def poly_get(path, params=None):
    """Make authenticated GET to Polygon API."""
    p = params or {}
    p['apiKey'] = POLYGON_KEY
    try:
        r = requests.get(f'{BASE}{path}', params=p, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f'Polygon API error: {e}')
        return None

def get_spx_snapshot():
    """Get current SPX price from Polygon snapshot."""
    data = poly_get('/v3/snapshot', {'ticker.any_of': 'I:SPX', 'type': 'index'})
    if data and data.get('results'):
        r = data['results'][0]
        session = r.get('session', {})
        return {
            'price': session.get('close') or session.get('price') or r.get('value'),
            'open': session.get('open'),
            'high': session.get('high'),
            'low': session.get('low'),
            'prev_close': session.get('previous_close'),
        }
    return None

def get_vix_snapshot():
    """Get current VIX from Polygon snapshot."""
    data = poly_get('/v3/snapshot', {'ticker.any_of': 'I:VIX', 'type': 'index'})
    if data and data.get('results'):
        session = data['results'][0].get('session', {})
        return session.get('open') or session.get('close') or session.get('price')
    return None

def get_spx_bars_today(date_str, timespan='minute', multiplier=1):
    """Get SPX 1-min bars for a given date."""
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/{multiplier}/{timespan}/{date_str}/{date_str}',
                    {'adjusted': 'true', 'sort': 'asc', 'limit': 50000})
    if data and data.get('results'):
        bars = []
        for b in data['results']:
            ts_ms = b['t']
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            et = dt.astimezone(timezone(timedelta(hours=-5 if dt.month in (1,2,3,11,12) else -4)))
            mins = et.hour * 60 + et.minute
            if 570 <= mins <= 959:
                bars.append({
                    'mins': mins,
                    'time': et.strftime('%H:%M'),
                    'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'],
                    'v': b.get('v', 0)
                })
        bars.sort(key=lambda x: x['mins'])
        return bars
    return []

def get_prev_day_bar(date_str):
    """Get previous trading day's daily bar for SPX."""
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/day/{date_str}/{date_str}',
                    {'adjusted': 'true', 'sort': 'desc', 'limit': 5})
    if data and data.get('results'):
        for b in data['results']:
            return {'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']}
    return None

def get_prev_trading_days(before_date, n=5):
    """Get the last N trading days before a given date."""
    end = (datetime.strptime(before_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    start = (datetime.strptime(before_date, '%Y-%m-%d') - timedelta(days=n*2+5)).strftime('%Y-%m-%d')
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/day/{start}/{end}',
                    {'adjusted': 'true', 'sort': 'desc', 'limit': n})
    if data and data.get('results'):
        return [{'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']} for b in data['results'][:n]]
    return []

def get_sma20(before_date):
    """Compute 20-day SMA of SPX closes."""
    end = (datetime.strptime(before_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    start = (datetime.strptime(before_date, '%Y-%m-%d') - timedelta(days=50)).strftime('%Y-%m-%d')
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/day/{start}/{end}',
                    {'adjusted': 'true', 'sort': 'desc', 'limit': 20})
    if data and data.get('results') and len(data['results']) >= 20:
        closes = [b['c'] for b in data['results'][:20]]
        return sum(closes) / len(closes)
    return None

def get_option_quote(ticker):
    """Get current option price from Polygon snapshot."""
    data = poly_get(f'/v3/snapshot/options/{ticker}')
    if data and data.get('results'):
        r = data['results']
        day = r.get('day', {})
        return {
            'last': day.get('close') or day.get('last_updated') or r.get('details', {}).get('last_price'),
            'bid': r.get('last_quote', {}).get('bid'),
            'ask': r.get('last_quote', {}).get('ask'),
            'mid': round((r.get('last_quote', {}).get('bid', 0) + r.get('last_quote', {}).get('ask', 0)) / 2, 2)
        }
    return None

def get_option_snapshot(ticker):
    """Get option snapshot including greeks and quote."""
    clean = ticker.replace('O:', '')
    data = poly_get(f'/v3/snapshot/options/SPX/{clean}')
    if data and data.get('results'):
        r = data['results']
        q = r.get('last_quote', {})
        d = r.get('day', {})
        return {
            'bid': q.get('bid', 0),
            'ask': q.get('ask', 0),
            'mid': round((q.get('bid', 0) + q.get('ask', 0)) / 2, 2),
            'last': d.get('close') or d.get('last_updated', 0),
            'volume': d.get('volume', 0),
        }
    return None


# ──────────────────────────────────────────────────────────────
# OPTION TICKER BUILDER
# ──────────────────────────────────────────────────────────────
def build_option_ticker(date_str, cp, strike):
    """Build SPXW option ticker. cp='C' or 'P', strike=float."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return f"O:SPXW{dt.strftime('%y%m%d')}{cp}{int(strike*1000):08d}"

def gstrike(px, rnd=5):
    """Round to nearest strike increment."""
    return round(px / rnd) * rnd

def readable_ticker(ticker):
    """Convert O:SPXW240115C02725000 to human-readable."""
    months = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    m = re.match(r'O:SPXW(\d{6})([CP])(\d{8})', ticker)
    if m:
        dstr, cp, sk = m.groups()
        yr, mo, dy = int(dstr[:2]), int(dstr[2:4]), int(dstr[4:6])
        strike = int(sk) / 1000
        cpname = 'Call' if cp == 'C' else 'Put'
        return f"SPXW {months[mo]} {dy} '{dstr[:2]} ${strike:,.0f} {cpname}"
    return ticker


# ──────────────────────────────────────────────────────────────
# STRATEGY DEFINITIONS — 11 EDGES
# ──────────────────────────────────────────────────────────────
EDGES = [
    # ── OPENING EDGES (fire after 9:31 first bar) ──
    {
        'id': 'friday_gap_go',
        'name': 'Friday Gap & Go',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
        'color': '#33aaff',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'bars',
        'signal': lambda d: d['fb_bullish'] and d['fb_ret'] >= 0.02 and 0.05 <= d.get('gap_pct', 0) <= 0.30,
        'filter': lambda d: d['dow'] == 4,
        'sharpe': 0.703, 'wr': 0.757,
    },
    {
        'id': 'calm_open_scalp',
        'name': 'Calm Open Scalp',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'color': '#00d4aa',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'bars',
        'signal': lambda d: d.get('fb_body_ratio', 0) > 0.80 and d['fb_bullish'] and d['fb_ret'] >= 0.03,
        'filter': lambda d: d['vix'] < 14,
        'sharpe': 1.290, 'wr': 0.828,
    },
    {
        'id': 'friday_follow_through',
        'name': 'Friday Follow-Through',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
        'color': '#ff9933',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'bars',
        'signal': lambda d: d.get('fb_body_ratio', 0) > 0.75 and d['fb_bullish'] and d['fb_ret'] >= 0.03 and d.get('prev_bullish', False),
        'filter': lambda d: d['dow'] == 4 and d['vix'] < 20,
        'sharpe': 0.979, 'wr': 0.773,
    },
    {
        'id': 'multi_day_momentum',
        'name': 'Multi-Day Momentum',
        'session': 'opening',
        'struct': 'long_call',
        'exit': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
        'color': '#ff66cc',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'bars',
        'signal': lambda d: d.get('fb_body_ratio', 0) > 0.75 and d['fb_bullish'] and d['fb_ret'] >= 0.03 and d.get('prev_bullish', False),
        'filter': lambda d: d['vix'] < 15,
        'sharpe': 0.893, 'wr': 0.771,
    },

    # ── CLOSING EDGES (fire after 2:00 PM first bar) ──
    {
        'id': 'pm_rally_highs',
        'name': 'PM Rally at Highs',
        'session': 'closing',
        'struct': 'bull_call_5',
        'exit': {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
        'color': '#33ffaa',
        'direction': 1,
        'entry_idx': 1,   # 2-bar pattern — enter after second bar
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('cfb2_bull', False) and d.get('cfb2_ret', 0) >= 0.03 and d.get('range_pos', 0) > 0.70,
        'filter': lambda d: d['vix'] < 14,
        'sharpe': 1.398, 'wr': 1.0,
    },
    {
        'id': 'pm_trend_cont',
        'name': 'PM Trend Continuation',
        'session': 'closing',
        'struct': 'long_itm_call',
        'exit': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 8},
        'color': '#66ddff',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('cfb_br', 0) > 0.70 and d.get('cfb_bull', False) and d.get('cfb_ret', 0) >= 0.03 and d.get('above_20d') is True,
        'filter': lambda d: d['vix'] < 14,
        'sharpe': 1.046, 'wr': 0.818,
    },
    {
        'id': 'high_vix_pm_breakout',
        'name': 'High VIX PM Breakout',
        'session': 'closing',
        'struct': 'bull_call_10',
        'exit': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
        'color': '#ffaa33',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('cfb_br', 0) > 0.65 and d.get('cfb_bull', False) and d.get('cfb_ret', 0) >= 0.03,
        'filter': lambda d: d['vix'] >= 22 and d['dow'] in (1, 2),
        'sharpe': 0.851, 'wr': 0.833,
    },
    {
        'id': 'pm_fade_below_trend',
        'name': 'PM Fade Below Trend',
        'session': 'closing',
        'struct': 'credit_call_5',
        'exit': {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
        'color': '#ff6666',
        'direction': -1,
        'entry_idx': 0,
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('cfb_br', 0) > 0.60 and not d.get('cfb_bull', True) and d.get('cfb_ret', 0) <= -0.03 and d.get('above_20d') is False,
        'filter': lambda d: d['vix'] < 20,
        'sharpe': 0.664, 'wr': 0.889,
    },
    {
        'id': 'calm_day_breakdown',
        'name': 'Calm Day Breakdown',
        'session': 'closing',
        'struct': 'bear_put_5',
        'exit': {'pt_pts': 1, 'sl_pts': 0.5, 'ts_min': 2},
        'color': '#ff44aa',
        'direction': -1,
        'entry_idx': 1,   # 2-bar pattern
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('cfb2_bear', False) and d.get('cfb2_ret', 0) <= -0.03,
        'filter': lambda d: d['vix'] < 14,
        'sharpe': 0.642, 'wr': 0.75,
    },
    {
        'id': 'afternoon_bounce_back',
        'name': 'Afternoon Bounce Back',
        'session': 'closing',
        'struct': 'bull_call_5',
        'exit': {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},
        'color': '#aaff33',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'afternoon',
        'signal': lambda d: d.get('morn_ret', 0) <= -0.20 and d.get('cfb_bull', False) and d.get('cfb_ret', 0) >= 0.03 and d.get('range_pos', 1) < 0.40,
        'filter': lambda d: d['vix'] < 18 and d['dow'] in (1, 2),
        'sharpe': 0.704, 'wr': 0.80,
    },
    {
        'id': 'quiet_morning_breakout',
        'name': 'Quiet Morning Breakout',
        'session': 'closing',
        'struct': 'long_itm_call',
        'exit': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
        'color': '#dddd33',
        'direction': 1,
        'entry_idx': 0,
        'bars_key': 'afternoon',
        'signal': lambda d: abs(d.get('morn_ret', 999)) <= 0.10 and d.get('cfb_bull', False) and d.get('cfb_ret', 0) >= 0.03 and d.get('cfb_br', 0) > 0.65,
        'filter': lambda d: 18 <= d['vix'] < 30,
        'sharpe': 0.664, 'wr': 0.882,
    },
]


# ──────────────────────────────────────────────────────────────
# STATE MANAGEMENT — persists to disk
# ──────────────────────────────────────────────────────────────
class DashboardState:
    """Thread-safe state manager with disk persistence."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = self._load()

    def _default(self):
        return {
            'date': None,              # current trading date YYYY-MM-DD
            'spx_price': None,
            'spx_open': None,
            'vix': None,
            'prev_close': None,
            'prev_bullish': None,
            'above_20d': None,
            'gap_pct': None,
            'bars': [],                # all 1-min bars today
            'morning': [],             # bars < 2:00 PM
            'afternoon': [],           # bars >= 2:00 PM
            'features': {},            # computed features dict
            'signals_fired': {},       # edge_id -> timestamp when fired
            'active_trades': {},       # edge_id -> trade details
            'closed_trades': [],       # list of completed trades today
            'alerts': [],              # pending alerts to show
            'last_update': None,
            'market_open': False,
        }

    def _load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    saved = json.load(f)
                today = _today_str()
                # If it's a new day, reset but keep closed_trades from yesterday
                if saved.get('date') != today:
                    log.info(f"New trading day: {today} (was {saved.get('date')})")
                    return self._default()
                return saved
            except Exception as e:
                log.error(f"Error loading state: {e}")
        return self._default()

    def save(self):
        with self.lock:
            try:
                with open(STATE_FILE, 'w') as f:
                    json.dump(self.state, f, default=str)
            except Exception as e:
                log.error(f"Error saving state: {e}")

    def get(self, key, default=None):
        with self.lock:
            return self.state.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.state[key] = value

    def update(self, d):
        with self.lock:
            self.state.update(d)

    def get_full(self):
        with self.lock:
            return dict(self.state)

    def reset_for_new_day(self):
        with self.lock:
            self.state = self._default()
        self.save()


state = DashboardState()

# SSE clients
sse_clients = []
sse_lock = threading.Lock()


def _today_str():
    """Get today's date in ET."""
    now_utc = datetime.now(timezone.utc)
    # Rough ET offset
    m = now_utc.month
    offset = -4 if 3 <= m <= 10 else -5
    et = now_utc + timedelta(hours=offset)
    return et.strftime('%Y-%m-%d')

def _now_et():
    """Get current ET datetime."""
    now_utc = datetime.now(timezone.utc)
    m = now_utc.month
    offset = -4 if 3 <= m <= 10 else -5
    return now_utc + timedelta(hours=offset)

def _now_mins():
    """Current time as minutes from midnight in ET."""
    et = _now_et()
    return et.hour * 60 + et.minute


# ──────────────────────────────────────────────────────────────
# FEATURE COMPUTATION
# ──────────────────────────────────────────────────────────────
def compute_features(bars, prev_days, vix, sma20, today_open):
    """Compute all signal features from current bar data."""
    features = {
        'fb_bullish': False,
        'fb_ret': 0,
        'fb_body_ratio': 0,
        'gap_pct': 0,
        'prev_bullish': False,
        'above_20d': None,
        'vix': vix or 20,
        'dow': _now_et().weekday(),
        'cfb_bull': False,
        'cfb_ret': 0,
        'cfb_br': 0,
        'cfb2_bull': False,
        'cfb2_bear': False,
        'cfb2_ret': 0,
        'morn_ret': 0,
        'range_pos': 0.5,
    }

    if not bars:
        return features

    # ── Opening features (from first bar) ──
    fb = bars[0]
    features['fb_bullish'] = fb['c'] > fb['o']
    features['fb_ret'] = (fb['c'] - fb['o']) / fb['o'] * 100 if fb['o'] else 0
    rng = fb['h'] - fb['l']
    features['fb_body_ratio'] = abs(fb['c'] - fb['o']) / rng if rng > 0 else 0

    # Gap
    if prev_days and today_open:
        pc = prev_days[0]['c']
        features['gap_pct'] = (today_open - pc) / pc * 100 if pc else 0

    # Previous day bullish
    if prev_days:
        features['prev_bullish'] = prev_days[0]['c'] > prev_days[0]['o']

    # Above 20d SMA
    if sma20 and today_open:
        features['above_20d'] = today_open > sma20

    # VIX
    features['vix'] = vix or 20

    # Day of week
    features['dow'] = _now_et().weekday()

    # ── Afternoon features ──
    afternoon = [b for b in bars if b['mins'] >= 840]
    morning = [b for b in bars if b['mins'] < 840]

    if morning and len(morning) >= 5:
        mo = morning[0]['o']
        mc = morning[-1]['c']
        mh = max(b['h'] for b in morning)
        ml = min(b['l'] for b in morning)
        mr = mh - ml
        features['morn_ret'] = (mc - mo) / mo * 100 if mo else 0
        features['range_pos'] = (mc - ml) / mr if mr > 0 else 0.5

    if afternoon and len(afternoon) >= 1:
        cfb = afternoon[0]
        features['cfb_bull'] = cfb['c'] > cfb['o']
        features['cfb_ret'] = (cfb['c'] - cfb['o']) / cfb['o'] * 100 if cfb['o'] else 0
        cfb_rng = cfb['h'] - cfb['l']
        features['cfb_br'] = abs(cfb['c'] - cfb['o']) / cfb_rng if cfb_rng > 0 else 0

    if afternoon and len(afternoon) >= 2:
        features['cfb2_bull'] = afternoon[0]['c'] > afternoon[0]['o'] and afternoon[1]['c'] > afternoon[1]['o']
        features['cfb2_bear'] = afternoon[0]['c'] < afternoon[0]['o'] and afternoon[1]['c'] < afternoon[1]['o']
        features['cfb2_ret'] = (afternoon[1]['c'] - afternoon[0]['o']) / afternoon[0]['o'] * 100 if afternoon[0]['o'] else 0

    return features


# ──────────────────────────────────────────────────────────────
# TRADE CONSTRUCTION
# ──────────────────────────────────────────────────────────────
def build_trade_for_edge(edge, features, spx_price, date_str):
    """Build trade details when a signal fires: strikes, tickers, sizing."""
    atm = gstrike(spx_price)
    struct = edge['struct']
    direction = edge['direction']
    exit_params = edge['exit']

    # Build option tickers
    if struct == 'long_call':
        tickers = [build_option_ticker(date_str, 'C', atm)]
        legs = [{'ticker': tickers[0], 'side': 'buy', 'strike': atm, 'cp': 'Call'}]
    elif struct == 'long_itm_call':
        tickers = [build_option_ticker(date_str, 'C', atm - 5)]
        legs = [{'ticker': tickers[0], 'side': 'buy', 'strike': atm - 5, 'cp': 'Call'}]
    elif struct == 'bull_call_5':
        t1 = build_option_ticker(date_str, 'C', atm)
        t2 = build_option_ticker(date_str, 'C', atm + 5)
        tickers = [t1, t2]
        legs = [
            {'ticker': t1, 'side': 'buy', 'strike': atm, 'cp': 'Call'},
            {'ticker': t2, 'side': 'sell', 'strike': atm + 5, 'cp': 'Call'},
        ]
    elif struct == 'bull_call_10':
        t1 = build_option_ticker(date_str, 'C', atm)
        t2 = build_option_ticker(date_str, 'C', atm + 10)
        tickers = [t1, t2]
        legs = [
            {'ticker': t1, 'side': 'buy', 'strike': atm, 'cp': 'Call'},
            {'ticker': t2, 'side': 'sell', 'strike': atm + 10, 'cp': 'Call'},
        ]
    elif struct == 'bear_put_5':
        t1 = build_option_ticker(date_str, 'P', atm)
        t2 = build_option_ticker(date_str, 'P', atm - 5)
        tickers = [t1, t2]
        legs = [
            {'ticker': t1, 'side': 'sell', 'strike': atm, 'cp': 'Put'},
            {'ticker': t2, 'side': 'buy', 'strike': atm - 5, 'cp': 'Put'},
        ]
    elif struct == 'credit_call_5':
        t1 = build_option_ticker(date_str, 'C', atm + 5)
        t2 = build_option_ticker(date_str, 'C', atm + 10)
        tickers = [t1, t2]
        legs = [
            {'ticker': t1, 'side': 'sell', 'strike': atm + 5, 'cp': 'Call'},
            {'ticker': t2, 'side': 'buy', 'strike': atm + 10, 'cp': 'Call'},
        ]
    else:
        return None

    # VIX adjustment for exit levels
    vix = features.get('vix', 20)
    pt = exit_params.get('pt_pts')
    sl = exit_params.get('sl_pts')
    ts = exit_params.get('ts_min') or 60

    if exit_params.get('vix_mult') and vix:
        sc = vix / 20.0
        if pt: pt = round(pt * sc, 2)
        if sl: sl = round(sl * sc, 2)

    # P&L targets on SPX
    if direction == 1:
        pt_level = round(spx_price + pt, 2) if pt else None
        sl_level = round(spx_price - sl, 2) if sl else None
    else:
        pt_level = round(spx_price - pt, 2) if pt else None
        sl_level = round(spx_price + sl, 2) if sl else None

    # Grading (simplified for live — use backtest stats)
    # Use the edge's known sharpe/wr for grading
    sharpe_min, sharpe_max = 0.429, 1.398   # from backtest range
    sharpe_score = (edge['sharpe'] - sharpe_min) / (sharpe_max - sharpe_min)
    wr_score = edge['wr']
    vix_min, vix_max = 9, 35
    vix_score = 1.0 - (vix - vix_min) / (vix_max - vix_min) if vix_max > vix_min else 0.5
    vix_score = max(0, min(1, vix_score))
    hold_score = 0.5  # unknown for live trade

    grade = round((sharpe_score * 0.40 + wr_score * 0.25 + vix_score * 0.20 + hold_score * 0.15) * 100, 1)

    # Sizing
    MIN_RISK, MAX_RISK = 25000, 200000
    norm = max(0, min(1, (grade - 30) / 60))
    risk_budget = MIN_RISK + norm * (MAX_RISK - MIN_RISK)

    trade = {
        'edge_id': edge['id'],
        'edge_name': edge['name'],
        'struct': struct,
        'direction': direction,
        'color': edge['color'],
        'date': date_str,
        'entry_spx': spx_price,
        'atm_strike': atm,
        'legs': legs,
        'tickers': tickers,
        'pt_level': pt_level,
        'sl_level': sl_level,
        'ts_min': ts,
        'entry_time': _now_et().strftime('%H:%M:%S'),
        'entry_mins': _now_mins(),
        'deadline_mins': min(_now_mins() + ts, 959),
        'grade': grade,
        'risk_budget': round(risk_budget),
        'entry_prices': {},         # filled when we get option quotes
        'current_prices': {},       # updated every tick
        'per_contract_risk': None,
        'contracts': None,
        'status': 'pending_fill',   # pending_fill -> active -> closed
        'pnl': 0,
        'exit_reason': None,
        'exit_time': None,
        'exit_spx': None,
    }
    return trade


def fill_trade_prices(trade):
    """Fetch current option prices and compute sizing."""
    legs = trade['legs']
    entry_prices = {}

    for leg in legs:
        snap = get_option_snapshot(leg['ticker'])
        if snap and snap['mid'] > 0:
            entry_prices[leg['ticker']] = snap['mid']
        elif snap and snap['last'] and snap['last'] > 0:
            entry_prices[leg['ticker']] = snap['last']

    if len(entry_prices) != len(legs):
        log.warning(f"Could not get all option prices for {trade['edge_name']}")
        return False

    trade['entry_prices'] = entry_prices
    trade['current_prices'] = dict(entry_prices)

    # Compute per-contract risk
    struct = trade['struct']
    if struct in ('long_call', 'long_itm_call', 'long_otm_call'):
        px = list(entry_prices.values())[0]
        trade['per_contract_risk'] = round(px * 100, 2)
    elif struct in ('bull_call_5', 'bull_call_10', 'bear_put_5'):
        prices = list(entry_prices.values())
        net_debit = abs(prices[0] - prices[1])
        trade['per_contract_risk'] = round(net_debit * 100, 2)
    elif struct == 'credit_call_5':
        prices = list(entry_prices.values())
        credit = prices[0] - prices[1]  # sell - buy
        trade['per_contract_risk'] = round(500 - credit * 100, 2)

    if trade['per_contract_risk'] and trade['per_contract_risk'] > 0:
        trade['contracts'] = max(1, round(trade['risk_budget'] / trade['per_contract_risk']))
        trade['status'] = 'active'
        return True

    return False


def update_trade_pnl(trade):
    """Update current prices and compute unrealized P&L."""
    if trade['status'] != 'active':
        return

    current_prices = {}
    for leg in trade['legs']:
        snap = get_option_snapshot(leg['ticker'])
        if snap and snap['mid'] > 0:
            current_prices[leg['ticker']] = snap['mid']
        elif snap and snap['last'] and snap['last'] > 0:
            current_prices[leg['ticker']] = snap['last']

    if len(current_prices) != len(trade['legs']):
        return  # keep last known prices

    trade['current_prices'] = current_prices

    # Compute P&L
    struct = trade['struct']
    entry = trade['entry_prices']
    curr = current_prices
    contracts = trade['contracts'] or 1

    if struct in ('long_call', 'long_itm_call', 'long_otm_call'):
        tk = trade['legs'][0]['ticker']
        pnl_per = (curr[tk] - entry[tk]) * 100
    elif struct in ('bull_call_5', 'bull_call_10', 'bear_put_5'):
        buy_tk = trade['legs'][0]['ticker']
        sell_tk = trade['legs'][1]['ticker']
        entry_debit = entry[buy_tk] - entry[sell_tk]
        curr_value = curr[buy_tk] - curr[sell_tk]
        pnl_per = (curr_value - entry_debit) * 100
    elif struct == 'credit_call_5':
        sell_tk = trade['legs'][0]['ticker']
        buy_tk = trade['legs'][1]['ticker']
        entry_credit = entry[sell_tk] - entry[buy_tk]
        curr_debit = curr[sell_tk] - curr[buy_tk]
        pnl_per = (entry_credit - curr_debit) * 100
    else:
        pnl_per = 0

    trade['pnl'] = round(pnl_per * contracts, 2)


def check_trade_exit(trade, spx_price):
    """Check if SPX has hit TP, SL, or time stop."""
    if trade['status'] != 'active':
        return None

    now_mins = _now_mins()
    direction = trade['direction']
    pt = trade['pt_level']
    sl = trade['sl_level']
    deadline = trade['deadline_mins']

    # Time stop
    if now_mins >= deadline or now_mins >= 959:
        return 'time_stop'

    # SPX-level checks
    if direction == 1:
        if sl and spx_price <= sl:
            return 'stop_loss'
        if pt and spx_price >= pt:
            return 'profit_target'
    else:
        if sl and spx_price >= sl:
            return 'stop_loss'
        if pt and spx_price <= pt:
            return 'profit_target'

    return None


# ──────────────────────────────────────────────────────────────
# MAIN ENGINE LOOP — runs every second
# ──────────────────────────────────────────────────────────────
_last_bar_fetch = 0
_last_vix_fetch = 0
_initialized_today = False

def engine_tick():
    """Main engine tick — called every second by scheduler."""
    global _last_bar_fetch, _last_vix_fetch, _initialized_today

    if not POLYGON_KEY:
        return

    now = time.time()
    now_mins = _now_mins()
    today = _today_str()

    # Check if market hours (9:25 - 4:05 ET to give buffer)
    if now_mins < 565 or now_mins > 965:
        state.set('market_open', False)
        return

    state.set('market_open', True)

    # ── New day init ──
    if state.get('date') != today:
        log.info(f"Initializing new trading day: {today}")
        state.reset_for_new_day()
        state.set('date', today)
        _initialized_today = False

    if not _initialized_today:
        # Fetch previous day data, VIX, SMA
        prev_days = get_prev_trading_days(today, 5)
        if prev_days:
            state.set('prev_close', prev_days[0]['c'])
            state.set('prev_bullish', prev_days[0]['c'] > prev_days[0]['o'])

        vix = get_vix_snapshot()
        state.set('vix', vix)

        sma20 = get_sma20(today)
        state.set('sma20', sma20)

        _initialized_today = True
        _last_vix_fetch = now
        log.info(f"Day initialized: prev_close={state.get('prev_close')}, vix={vix}, sma20={sma20}")

    # ── Fetch SPX snapshot every second ──
    snap = get_spx_snapshot()
    if snap and snap.get('price'):
        state.set('spx_price', snap['price'])
        if snap.get('open'):
            state.set('spx_open', snap['open'])

    # ── Refresh VIX every 60s ──
    if now - _last_vix_fetch > 60:
        vix = get_vix_snapshot()
        if vix:
            state.set('vix', vix)
        _last_vix_fetch = now

    # ── Fetch 1-min bars every 5s ──
    if now - _last_bar_fetch > 5:
        bars = get_spx_bars_today(today)
        if bars:
            state.set('bars', bars)
            state.set('morning', [b for b in bars if b['mins'] < 840])
            state.set('afternoon', [b for b in bars if b['mins'] >= 840])
        _last_bar_fetch = now

    # ── Compute features ──
    bars = state.get('bars', [])
    features = compute_features(
        bars=bars,
        prev_days=[{'o': 0, 'h': 0, 'l': 0, 'c': state.get('prev_close', 0)}] if state.get('prev_close') else [],
        vix=state.get('vix'),
        sma20=state.get('sma20'),
        today_open=state.get('spx_open'),
    )
    features['prev_bullish'] = state.get('prev_bullish', False)
    features['above_20d'] = state.get('above_20d')
    if state.get('sma20') and state.get('spx_open'):
        features['above_20d'] = state.get('spx_open') > state.get('sma20')
    if state.get('prev_close') and state.get('spx_open'):
        features['gap_pct'] = (state.get('spx_open') - state.get('prev_close')) / state.get('prev_close') * 100

    state.set('features', features)

    # ── Check signals ──
    signals_fired = state.get('signals_fired', {})
    active_trades = state.get('active_trades', {})
    spx_price = state.get('spx_price')
    alerts = []

    for edge in EDGES:
        eid = edge['id']

        # Skip if already fired today
        if eid in signals_fired:
            continue

        # Check timing: opening edges need first bar (9:31+), closing need afternoon bar (2:01+)
        if edge['session'] == 'opening':
            if not bars or bars[0]['mins'] > 571:
                continue  # first bar not ready
            if now_mins < 571:
                continue  # wait for first bar to close
            # For entry_idx=0, need at least 1 bar
            if len(bars) < edge['entry_idx'] + 1:
                continue
        elif edge['session'] == 'closing':
            afternoon = state.get('afternoon', [])
            if edge['entry_idx'] == 0 and len(afternoon) < 1:
                continue
            if edge['entry_idx'] == 1 and len(afternoon) < 2:
                continue
            if now_mins < 841 + edge['entry_idx']:
                continue

        # Check filter first (day of week, VIX level)
        try:
            if not edge['filter'](features):
                continue
        except Exception:
            continue

        # Check signal
        try:
            if not edge['signal'](features):
                continue
        except Exception:
            continue

        # SIGNAL FIRED!
        log.info(f"SIGNAL FIRED: {edge['name']} at SPX {spx_price}")
        signals_fired[eid] = _now_et().strftime('%H:%M:%S')

        # Build trade
        if spx_price:
            trade = build_trade_for_edge(edge, features, spx_price, today)
            if trade:
                # Get option prices
                filled = fill_trade_prices(trade)
                if filled:
                    active_trades[eid] = trade
                    alerts.append({
                        'type': 'signal',
                        'edge': edge['name'],
                        'message': f"🔔 {edge['name']} — ENTER NOW",
                        'trade': trade,
                        'time': _now_et().strftime('%H:%M:%S'),
                    })
                else:
                    # Couldn't get option prices, still show signal
                    active_trades[eid] = trade
                    alerts.append({
                        'type': 'signal',
                        'edge': edge['name'],
                        'message': f"🔔 {edge['name']} — SIGNAL (getting prices...)",
                        'trade': trade,
                        'time': _now_et().strftime('%H:%M:%S'),
                    })

    state.set('signals_fired', signals_fired)
    state.set('active_trades', active_trades)

    # ── Update active trades ──
    closed_trades = state.get('closed_trades', [])
    to_close = []

    for eid, trade in active_trades.items():
        if trade['status'] == 'active':
            # Update P&L
            update_trade_pnl(trade)

            # Check exit
            if spx_price:
                exit_reason = check_trade_exit(trade, spx_price)
                if exit_reason:
                    trade['status'] = 'closed'
                    trade['exit_reason'] = exit_reason
                    trade['exit_time'] = _now_et().strftime('%H:%M:%S')
                    trade['exit_spx'] = spx_price
                    # Final P&L update
                    update_trade_pnl(trade)
                    to_close.append(eid)

                    emoji = '✅' if trade['pnl'] >= 0 else '❌'
                    alerts.append({
                        'type': 'exit',
                        'edge': trade['edge_name'],
                        'message': f"{emoji} {trade['edge_name']} — {exit_reason.upper().replace('_', ' ')} — P&L: ${trade['pnl']:+,.0f}",
                        'trade': trade,
                        'time': _now_et().strftime('%H:%M:%S'),
                    })

        elif trade['status'] == 'pending_fill':
            # Retry getting prices
            filled = fill_trade_prices(trade)

    for eid in to_close:
        closed_trades.append(active_trades[eid])

    state.set('active_trades', active_trades)
    state.set('closed_trades', closed_trades)

    if alerts:
        existing = state.get('alerts', [])
        existing.extend(alerts)
        state.set('alerts', existing)

    state.set('last_update', _now_et().strftime('%H:%M:%S'))

    # ── Push SSE update ──
    push_sse_update()

    # ── Save state periodically ──
    state.save()


def push_sse_update():
    """Push current state to all SSE clients."""
    s = state.get_full()
    # Build compact payload
    payload = {
        'spx': s.get('spx_price'),
        'vix': s.get('vix'),
        'time': s.get('last_update'),
        'market_open': s.get('market_open'),
        'features': s.get('features', {}),
        'signals': s.get('signals_fired', {}),
        'trades': {},
        'closed': [],
        'alerts': s.get('alerts', [])[-10:],  # last 10 alerts
        'gap_pct': s.get('features', {}).get('gap_pct'),
        'prev_bullish': s.get('prev_bullish'),
        'above_20d': s.get('features', {}).get('above_20d'),
    }

    # Serialize active trades (strip lambdas)
    for eid, t in s.get('active_trades', {}).items():
        payload['trades'][eid] = {
            'edge_id': t['edge_id'],
            'edge_name': t['edge_name'],
            'struct': t['struct'],
            'direction': t['direction'],
            'color': t['color'],
            'entry_spx': t['entry_spx'],
            'atm_strike': t['atm_strike'],
            'legs': t['legs'],
            'pt_level': t['pt_level'],
            'sl_level': t['sl_level'],
            'entry_time': t['entry_time'],
            'grade': t['grade'],
            'risk_budget': t['risk_budget'],
            'contracts': t['contracts'],
            'per_contract_risk': t['per_contract_risk'],
            'entry_prices': t['entry_prices'],
            'current_prices': t['current_prices'],
            'pnl': t['pnl'],
            'status': t['status'],
            'exit_reason': t.get('exit_reason'),
            'exit_time': t.get('exit_time'),
        }

    for t in s.get('closed_trades', []):
        payload['closed'].append({
            'edge_name': t['edge_name'],
            'pnl': t['pnl'],
            'exit_reason': t.get('exit_reason'),
            'contracts': t.get('contracts'),
            'entry_time': t.get('entry_time'),
            'exit_time': t.get('exit_time'),
        })

    data = json.dumps(payload, default=str)

    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.append(data)
            except Exception:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)


# ──────────────────────────────────────────────────────────────
# FLASK ROUTES
# ──────────────────────────────────────────────────────────────
@app.route('/')
def index():
    edges_info = []
    for e in EDGES:
        edges_info.append({
            'id': e['id'],
            'name': e['name'],
            'session': e['session'],
            'struct': e['struct'],
            'color': e['color'],
            'direction': e['direction'],
            'exit': {k: v for k, v in e['exit'].items() if k != 'vix_mult'},
            'sharpe': e['sharpe'],
            'wr': e['wr'],
        })
    return render_template('dashboard.html', edges=edges_info)


@app.route('/api/state')
def api_state():
    """Full state snapshot for initial page load."""
    push_sse_update()  # trigger a fresh build
    s = state.get_full()
    # Return same structure as SSE
    payload = {
        'spx': s.get('spx_price'),
        'vix': s.get('vix'),
        'time': s.get('last_update'),
        'market_open': s.get('market_open'),
        'features': s.get('features', {}),
        'signals': s.get('signals_fired', {}),
        'trades': {},
        'closed': [],
        'alerts': s.get('alerts', [])[-20:],
    }
    for eid, t in s.get('active_trades', {}).items():
        payload['trades'][eid] = {
            'edge_id': t['edge_id'],
            'edge_name': t['edge_name'],
            'struct': t['struct'],
            'direction': t['direction'],
            'color': t['color'],
            'entry_spx': t['entry_spx'],
            'atm_strike': t['atm_strike'],
            'legs': t['legs'],
            'pt_level': t['pt_level'],
            'sl_level': t['sl_level'],
            'entry_time': t['entry_time'],
            'grade': t['grade'],
            'risk_budget': t['risk_budget'],
            'contracts': t['contracts'],
            'per_contract_risk': t['per_contract_risk'],
            'entry_prices': t['entry_prices'],
            'current_prices': t['current_prices'],
            'pnl': t['pnl'],
            'status': t['status'],
            'exit_reason': t.get('exit_reason'),
            'exit_time': t.get('exit_time'),
        }
    for t in s.get('closed_trades', []):
        payload['closed'].append({
            'edge_name': t['edge_name'],
            'pnl': t['pnl'],
            'exit_reason': t.get('exit_reason'),
            'contracts': t.get('contracts'),
            'entry_time': t.get('entry_time'),
            'exit_time': t.get('exit_time'),
        })
    return jsonify(payload)


@app.route('/api/stream')
def api_stream():
    """SSE endpoint for real-time updates."""
    def event_stream():
        q = []
        with sse_lock:
            sse_clients.append(q)
        try:
            while True:
                if q:
                    data = q.pop(0)
                    yield f"data: {data}\n\n"
                else:
                    time.sleep(0.5)
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    return Response(event_stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/clear-alerts', methods=['POST'])
def clear_alerts():
    state.set('alerts', [])
    return jsonify({'ok': True})


# ──────────────────────────────────────────────────────────────
# SCHEDULER
# ──────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(engine_tick, 'interval', seconds=1, max_instances=1, coalesce=True)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
