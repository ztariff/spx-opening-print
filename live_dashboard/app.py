"""
SPX 0DTE Opening Print — Live Dashboard
========================================
Flask app that monitors all 11 strategy edges in real-time via Polygon API.
Designed for deployment on Railway.

Environment variables:
    POLYGON_API_KEY  — your Polygon.io API key
    SECRET_KEY       — Flask session key (optional, defaults to random)
"""

import os, json, time, threading, logging, re, statistics
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict
from pathlib import Path

import requests
from flask import Flask, render_template, jsonify, Response, request
from apscheduler.schedulers.background import BackgroundScheduler

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────
POLYGON_KEY = os.environ.get('POLYGON_API_KEY', '')
DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / 'state.json'
CALENDAR_TRADES_FILE = DATA_DIR / 'calendar_trades.json'

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
    """Get current SPX price via multiple fallback methods."""
    # Method 1: Index snapshot
    data = poly_get('/v3/snapshot', {'ticker.any_of': 'I:SPX', 'type': 'index'})
    if data and data.get('results'):
        r = data['results'][0]
        session = r.get('session', {})
        price = session.get('close') or session.get('price') or r.get('value')
        if price:
            return {
                'price': price,
                'open': session.get('open'),
                'high': session.get('high'),
                'low': session.get('low'),
                'prev_close': session.get('previous_close'),
            }

    # Method 2: Ticker snapshot v3
    data = poly_get('/v3/snapshot/indices', {'ticker.any_of': 'I:SPX'})
    if data and data.get('results'):
        r = data['results'][0]
        session = r.get('session', {})
        val = r.get('value') or session.get('close') or session.get('price')
        if val:
            return {
                'price': val,
                'open': session.get('open'),
                'high': session.get('high'),
                'low': session.get('low'),
                'prev_close': session.get('previous_close'),
            }

    # Method 3: Latest agg bar
    today = _today_str()
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/minute/{today}/{today}',
                    {'adjusted': 'true', 'sort': 'desc', 'limit': 1})
    if data and data.get('results'):
        b = data['results'][0]
        return {
            'price': b['c'],
            'open': None,
            'high': None,
            'low': None,
            'prev_close': None,
        }

    # Method 4: Previous close from daily
    data = poly_get(f'/v2/aggs/ticker/I:SPX/prev')
    if data and data.get('results'):
        b = data['results'][0]
        return {
            'price': b['c'],
            'open': b.get('o'),
            'high': None,
            'low': None,
            'prev_close': b.get('c'),
        }

    log.error("All SPX price methods failed")
    return None

def get_vix_snapshot():
    """Get current VIX via multiple fallback methods."""
    # Method 1: Index snapshot
    data = poly_get('/v3/snapshot', {'ticker.any_of': 'I:VIX', 'type': 'index'})
    if data and data.get('results'):
        session = data['results'][0].get('session', {})
        val = session.get('open') or session.get('close') or session.get('price')
        if val:
            return val

    # Method 2: Indices endpoint
    data = poly_get('/v3/snapshot/indices', {'ticker.any_of': 'I:VIX'})
    if data and data.get('results'):
        r = data['results'][0]
        val = r.get('value') or r.get('session', {}).get('close')
        if val:
            return val

    # Method 3: Latest agg bar
    today = _today_str()
    data = poly_get(f'/v2/aggs/ticker/I:VIX/range/1/minute/{today}/{today}',
                    {'adjusted': 'true', 'sort': 'desc', 'limit': 1})
    if data and data.get('results'):
        return data['results'][0]['c']

    # Method 4: Previous close
    data = poly_get(f'/v2/aggs/ticker/I:VIX/prev')
    if data and data.get('results'):
        return data['results'][0]['c']

    log.error("All VIX price methods failed")
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

COMMISSION_PER_CONTRACT = 0.65   # $ per contract per leg per fill

def commission_for_trade(struct, contracts):
    """Total round-trip commission: $0.65 × legs × 2 (open+close) × contracts."""
    legs = 1 if struct in ('long_call', 'long_itm_call', 'long_otm_call', 'long_put') else 2
    return round(COMMISSION_PER_CONTRACT * legs * 2 * contracts, 2)

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
        trade['commission'] = commission_for_trade(struct, trade['contracts'])
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

    # Deduct round-trip commissions from P&L
    comm = trade.get('commission', commission_for_trade(struct, contracts))
    trade['pnl'] = round(pnl_per * contracts - comm, 2)


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


@app.route('/api/debug')
def api_debug():
    """Debug endpoint — raw Polygon responses for troubleshooting."""
    results = {}
    today = _today_str()

    # Test each endpoint
    results['snapshot_v3'] = poly_get('/v3/snapshot', {'ticker.any_of': 'I:SPX', 'type': 'index'})
    results['snapshot_indices'] = poly_get('/v3/snapshot/indices', {'ticker.any_of': 'I:SPX'})
    results['aggs_latest'] = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/minute/{today}/{today}',
                                       {'adjusted': 'true', 'sort': 'desc', 'limit': 2})
    results['prev_close'] = poly_get(f'/v2/aggs/ticker/I:SPX/prev')
    results['vix_snapshot'] = poly_get('/v3/snapshot/indices', {'ticker.any_of': 'I:VIX'})
    results['vix_aggs'] = poly_get(f'/v2/aggs/ticker/I:VIX/range/1/minute/{today}/{today}',
                                    {'adjusted': 'true', 'sort': 'desc', 'limit': 2})
    results['today'] = today
    results['now_mins'] = _now_mins()
    results['polygon_key_set'] = bool(POLYGON_KEY)
    results['polygon_key_prefix'] = POLYGON_KEY[:8] + '...' if POLYGON_KEY else 'MISSING'

    return jsonify(results)


@app.route('/calendar')
def calendar():
    """Serve the backtest trade calendar."""
    cal_path = Path(__file__).parent / 'static' / 'widened_trade_calendar.html'
    if cal_path.exists():
        return cal_path.read_text(), 200, {'Content-Type': 'text/html'}
    return 'Calendar not generated yet. Place widened_trade_calendar.html in live_dashboard/static/', 404


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
# CALENDAR REFRESH — backfill new trades from Polygon
# ──────────────────────────────────────────────────────────────
CAL_TRADES_FILE = DATA_DIR / 'calendar_trades.json'

def _load_calendar_trades():
    """Load persisted calendar trades (new trades added via refresh)."""
    if CAL_TRADES_FILE.exists():
        try:
            with open(CAL_TRADES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_calendar_trades(trades):
    with open(CAL_TRADES_FILE, 'w') as f:
        json.dump(trades, f)

def _get_trading_days(start_date, end_date):
    """Get list of actual trading days between two dates from Polygon."""
    data = poly_get(f'/v2/aggs/ticker/I:SPX/range/1/day/{start_date}/{end_date}',
                    {'adjusted': 'true', 'sort': 'asc', 'limit': 500})
    if data and data.get('results'):
        days = []
        for b in data['results']:
            dt = datetime.fromtimestamp(b['t'] / 1000, tz=timezone.utc)
            days.append({
                'date': dt.strftime('%Y-%m-%d'),
                'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']
            })
        return days
    return []

def _get_vix_daily(start_date, end_date):
    """Get VIX daily bars."""
    data = poly_get(f'/v2/aggs/ticker/I:VIX/range/1/day/{start_date}/{end_date}',
                    {'adjusted': 'true', 'sort': 'asc', 'limit': 500})
    out = {}
    if data and data.get('results'):
        for b in data['results']:
            dt = datetime.fromtimestamp(b['t'] / 1000, tz=timezone.utc)
            out[dt.strftime('%Y-%m-%d')] = {'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']}
    return out

def _get_option_bars(ticker, date_str):
    """Fetch 1-min option bars from Polygon for a specific date."""
    data = poly_get(f'/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}',
                    {'adjusted': 'true', 'sort': 'asc', 'limit': 5000})
    if data and data.get('results'):
        bars = []
        for b in data['results']:
            dt = datetime.fromtimestamp(b['t'] / 1000, tz=timezone.utc)
            m = dt.month
            offset = -5 if m in (1, 2, 3, 11, 12) else -4
            et = dt + timedelta(hours=offset)
            bars.append({
                't_ms': b['t'],
                'time': et.strftime('%H:%M'),
                'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']
            })
        return bars
    return []

def _find_bar_at_mins(bars, target_mins):
    """Find the bar closest to target_mins."""
    best = None
    for b in bars:
        hh, mm = int(b['time'][:2]), int(b['time'][3:5])
        bm = hh * 60 + mm
        if bm >= target_mins:
            return b
        best = b
    return best

def _simulate_trade_on_bars(bars, entry_idx, direction, exit_params):
    """Simulate a trade on 1-min bars and return entry/exit details."""
    if entry_idx >= len(bars) - 1:
        return None
    eb = bars[entry_idx]
    ep = eb['c']
    em = eb['mins']
    pt = exit_params.get('pt_pts')
    sl = exit_params.get('sl_pts')
    ts = exit_params.get('ts_min', 60)
    deadline = min(em + ts, 959)

    if exit_params.get('vix_mult'):
        vix = exit_params.get('_vix', 20)
        sc = vix / 20.0
        if pt: pt = pt * sc
        if sl: sl = sl * sc

    if direction == 1:
        ptl = ep + pt if pt else None
        sll = ep - sl if sl else None
    else:
        ptl = ep - pt if pt else None
        sll = ep + sl if sl else None

    xp = ep; xr = 'time_stop'; xi = len(bars) - 1
    peak = trough = ep

    for j in range(entry_idx + 1, len(bars)):
        b = bars[j]
        if b['mins'] >= deadline or b['mins'] >= 959:
            xp = b['c']; xr = 'time_stop'; xi = j; break
        if direction == 1:
            if b['h'] > peak: peak = b['h']
            if sll and b['l'] <= sll: xp = sll; xr = 'stop_loss'; xi = j; break
            if ptl and b['h'] >= ptl: xp = ptl; xr = 'profit_target'; xi = j; break
        else:
            if b['l'] < trough: trough = b['l']
            if sll and b['h'] >= sll: xp = sll; xr = 'stop_loss'; xi = j; break
            if ptl and b['l'] <= ptl: xp = ptl; xr = 'profit_target'; xi = j; break

    return {
        'entry_price': ep, 'exit_price': xp,
        'entry_time': bars[entry_idx]['time'],
        'exit_time': bars[xi]['time'],
        'entry_mins': em, 'exit_mins': bars[xi]['mins'],
        'hold_mins': bars[xi]['mins'] - em,
        'exit_reason': xr,
        'und_pts': round(xp - ep, 2) if direction == 1 else round(ep - xp, 2),
    }

def _price_option_trade(date_str, entry_mins, exit_mins, spx_price, direction, struct):
    """Fetch option bars from Polygon and compute 1-lot P&L for a given structure.
    Returns (pnl, ticker_str, entry_px_str, exit_px_str) or (None, ...) on failure."""
    atm = gstrike(spx_price)
    legs_count = 1 if struct in ('long_call', 'long_itm_call', 'long_otm_call', 'long_put') else 2
    comm = COMMISSION_PER_CONTRACT * legs_count * 2

    def single(cp, k):
        tk = build_option_ticker(date_str, cp, k)
        bars = _get_option_bars(tk, date_str)
        if not bars: return None, tk, None, None
        e = _find_bar_at_mins(bars, entry_mins)
        x = _find_bar_at_mins(bars, exit_mins)
        if not e or not x or e['c'] <= 0: return None, tk, None, None
        return round((x['c'] - e['c']) * 100 - comm, 2), tk, e['c'], x['c']

    def spread(cp, lk, sk):
        lt = build_option_ticker(date_str, cp, lk)
        st = build_option_ticker(date_str, cp, sk)
        lb = _get_option_bars(lt, date_str)
        sb = _get_option_bars(st, date_str)
        if not lb or not sb: return None, f"{lt}|{st}", None, None
        le = _find_bar_at_mins(lb, entry_mins)
        lx = _find_bar_at_mins(lb, exit_mins)
        se = _find_bar_at_mins(sb, entry_mins)
        sx = _find_bar_at_mins(sb, exit_mins)
        if not all([le, lx, se, sx]): return None, f"{lt}|{st}", None, None
        d = le['c'] - se['c']; c = lx['c'] - sx['c']
        return round((c - d) * 100 - comm, 2), f"{lt}|{st}", f"{le['c']}/{se['c']}", f"{lx['c']}/{sx['c']}"

    def credit(cp, sell_k, buy_k):
        st = build_option_ticker(date_str, cp, sell_k)
        lt = build_option_ticker(date_str, cp, buy_k)
        sb = _get_option_bars(st, date_str)
        lb = _get_option_bars(lt, date_str)
        if not sb or not lb: return None, f"{st}|{lt}", None, None
        se = _find_bar_at_mins(sb, entry_mins)
        sx = _find_bar_at_mins(sb, exit_mins)
        le = _find_bar_at_mins(lb, entry_mins)
        lx = _find_bar_at_mins(lb, exit_mins)
        if not all([se, sx, le, lx]): return None, f"{st}|{lt}", None, None
        cr = se['c'] - le['c']; dc = sx['c'] - lx['c']
        return round((cr - dc) * 100 - comm, 2), f"{st}|{lt}", f"{se['c']}/{le['c']}", f"{sx['c']}/{lx['c']}"

    if struct == 'long_call': return single('C', atm)
    elif struct == 'long_itm_call': return single('C', atm - 5)
    elif struct == 'long_otm_call': return single('C', atm + 5)
    elif struct == 'long_put': return single('P', atm)
    elif struct == 'bull_call_5': return spread('C', atm, atm + 5)
    elif struct == 'bull_call_10': return spread('C', atm, atm + 10)
    elif struct == 'bear_put_5': return spread('P', atm, atm - 5)
    elif struct == 'credit_call_5': return credit('C', atm + 5, atm + 10)
    return None, None, None, None

def _make_readable_ticker(ticker_str):
    """Convert O:SPXW240115C02725000 to human-readable."""
    parts = ticker_str.split('|')
    months = ['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    readable = []
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


@app.route('/api/calendar_trades')
def api_calendar_trades():
    """Return persisted new calendar trades (added via refresh)."""
    return jsonify(_load_calendar_trades())


@app.route('/api/refresh_calendar', methods=['POST'])
def api_refresh_calendar():
    """Backfill calendar trades for new dates up to yesterday.

    Fetches SPX/VIX data from Polygon, runs all 11 edge signals,
    prices with real option bars, and persists new trades.
    """
    if not POLYGON_KEY:
        return jsonify({'ok': False, 'error': 'No POLYGON_API_KEY configured'}), 500

    existing_new = _load_calendar_trades()
    existing_dates = set(t['date'] for t in existing_new)

    # Read baked-in trades from the static calendar to find last known date
    cal_path = Path(__file__).parent / 'static' / 'widened_trade_calendar.html'
    baked_last_date = '2026-02-06'  # fallback
    if cal_path.exists():
        try:
            content = cal_path.read_text()
            import re as _re
            m = _re.search(r'var allTrades = (\[.*?\]);', content, _re.DOTALL)
            if m:
                baked = json.loads(m.group(1))
                if baked:
                    baked_dates = sorted(set(t['date'] for t in baked))
                    baked_last_date = baked_dates[-1]
        except Exception:
            pass

    # Combine baked + new to find the true last date
    all_dates = set()
    all_dates.add(baked_last_date)
    all_dates.update(existing_dates)
    last_date = max(all_dates)

    # Yesterday in ET
    now_utc = datetime.now(timezone.utc)
    m = now_utc.month
    offset = -4 if 3 <= m <= 10 else -5
    et_now = now_utc + timedelta(hours=offset)
    yesterday = (et_now - timedelta(days=1)).strftime('%Y-%m-%d')

    if last_date >= yesterday:
        return jsonify({'ok': True, 'new_trades': 0, 'message': 'Already up to date',
                        'total_new': len(existing_new)})

    # Fetch SPX daily bars for the range
    start = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"Calendar refresh: scanning {start} to {yesterday}")

    spx_days = _get_trading_days(start, yesterday)
    if not spx_days:
        return jsonify({'ok': True, 'new_trades': 0, 'message': 'No new trading days found',
                        'total_new': len(existing_new)})

    vix_data = _get_vix_daily(start, yesterday)

    # Also need a lookback window for prev_day, SMA, etc.
    lookback_start = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=60)).strftime('%Y-%m-%d')
    lookback_end = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    lookback_bars = _get_trading_days(lookback_start, lookback_end)
    lookback_by_date = {b['date']: b for b in lookback_bars}

    # Build full date series for SMA computation
    all_daily = {b['date']: b for b in lookback_bars}
    for b in spx_days:
        all_daily[b['date']] = b

    new_trades = []
    dates_to_scan = [d for d in spx_days if d['date'] not in existing_dates]

    # Strat-level stats needed for grading
    sharpe_min, sharpe_max = 0.429, 1.398

    for day_bar in dates_to_scan:
        d = day_bar['date']
        log.info(f"  Scanning {d}...")

        # Fetch 1-min bars
        bars_1m = get_spx_bars_today(d)
        if len(bars_1m) < 60:
            log.info(f"    Skipping {d}: only {len(bars_1m)} bars")
            continue

        # Get VIX
        vd = vix_data.get(d)
        vix_open = vd['o'] if vd else 20

        # Previous day
        sorted_daily = sorted(all_daily.keys())
        d_idx = sorted_daily.index(d) if d in sorted_daily else -1
        prev_bar = all_daily[sorted_daily[d_idx - 1]] if d_idx > 0 else None
        if not prev_bar:
            log.info(f"    Skipping {d}: no prev day data")
            continue

        # SMA20
        sma20_val = None
        if d_idx >= 20:
            closes = [all_daily[sorted_daily[i]]['c'] for i in range(d_idx - 20, d_idx)]
            sma20_val = sum(closes) / 20

        # Day of week
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        # Compute features
        gap_pct = (day_bar['o'] - prev_bar['c']) / prev_bar['c'] * 100 if prev_bar['c'] else 0
        prev_bullish = prev_bar['c'] > prev_bar['o']
        above_20d = day_bar['o'] > sma20_val if sma20_val else None

        fb = bars_1m[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100 if fb['o'] else 0
        fb_bullish = fb['c'] > fb['o']
        rng = fb['h'] - fb['l']
        fb_body_ratio = abs(fb['c'] - fb['o']) / rng if rng > 0 else 0

        morning = [b for b in bars_1m if b['mins'] < 840]
        afternoon = [b for b in bars_1m if b['mins'] >= 840]

        morn_ret = range_pos = 0
        cfb_ret = cfb_br = 0; cfb_bull = False
        cfb2_bull = cfb2_bear = False; cfb2_ret = 0

        if len(morning) >= 5 and len(afternoon) >= 1:
            mo = morning[0]['o']; mc = morning[-1]['c']
            mh = max(b['h'] for b in morning); ml = min(b['l'] for b in morning)
            mr = mh - ml
            morn_ret = (mc - mo) / mo * 100 if mo else 0
            range_pos = (mc - ml) / mr if mr > 0 else 0.5

            cfb = afternoon[0]
            cfb_ret = (cfb['c'] - cfb['o']) / cfb['o'] * 100 if cfb['o'] else 0
            cfb_bull = cfb['c'] > cfb['o']
            cfb_rng = cfb['h'] - cfb['l']
            cfb_br = abs(cfb['c'] - cfb['o']) / cfb_rng if cfb_rng > 0 else 0

        if len(afternoon) >= 2:
            cfb2_bull = afternoon[0]['c'] > afternoon[0]['o'] and afternoon[1]['c'] > afternoon[1]['o']
            cfb2_bear = afternoon[0]['c'] < afternoon[0]['o'] and afternoon[1]['c'] < afternoon[1]['o']
            cfb2_ret = (afternoon[1]['c'] - afternoon[0]['o']) / afternoon[0]['o'] * 100 if afternoon[0]['o'] else 0

        features = {
            'fb_bullish': fb_bullish, 'fb_ret': fb_ret, 'fb_body_ratio': fb_body_ratio,
            'gap_pct': gap_pct, 'prev_bullish': prev_bullish, 'above_20d': above_20d,
            'vix': vix_open, 'dow': dow,
            'cfb_bull': cfb_bull, 'cfb_ret': cfb_ret, 'cfb_br': cfb_br,
            'cfb2_bull': cfb2_bull, 'cfb2_bear': cfb2_bear, 'cfb2_ret': cfb2_ret,
            'morn_ret': morn_ret, 'range_pos': range_pos,
        }

        # Run all 11 edge signals
        for edge in EDGES:
            if not edge['filter'](features):
                continue
            if not edge['signal'](features):
                continue

            bars_for_edge = bars_1m if edge['bars_key'] == 'bars' else afternoon
            entry_idx = edge['entry_idx']
            if not bars_for_edge or entry_idx >= len(bars_for_edge) - 3:
                continue

            epp = dict(edge['exit'])
            if epp.get('vix_mult'):
                epp['_vix'] = vix_open

            t = _simulate_trade_on_bars(bars_for_edge, entry_idx, edge['direction'], epp)
            if not t:
                continue

            # Price with options
            pnl, ticker, epx, xpx = _price_option_trade(
                d, t['entry_mins'], t['exit_mins'],
                t['entry_price'], edge['direction'], edge['struct']
            )
            if pnl is None:
                log.info(f"    {edge['name']}: signal fired but option pricing failed")
                continue

            # Grade
            sharpe_score = (edge['sharpe'] - sharpe_min) / (sharpe_max - sharpe_min)
            wr_score = edge['wr']
            vix_min_g, vix_max_g = 9, 35
            vix_score = max(0, min(1, 1.0 - (vix_open - vix_min_g) / (vix_max_g - vix_min_g)))
            hold_score = 0.5
            grade = round((sharpe_score * 0.40 + wr_score * 0.25 + vix_score * 0.20 + hold_score * 0.15) * 100, 1)

            # Size
            MIN_RISK, MAX_RISK = 25000, 200000
            norm = max(0, min(1, (grade - 30) / 60))
            risk_budget = MIN_RISK + norm * (MAX_RISK - MIN_RISK)

            entry_px = epx
            struct = edge['struct']
            if struct in ('bull_call_5', 'bull_call_10', 'bear_put_5'):
                if isinstance(entry_px, str) and '/' in entry_px:
                    prices = entry_px.split('/')
                    net_debit = abs(float(prices[0]) - float(prices[1]))
                else:
                    net_debit = float(entry_px) if entry_px else 1
                per_contract_risk = net_debit * 100
            elif struct == 'credit_call_5':
                if isinstance(entry_px, str) and '/' in entry_px:
                    prices = entry_px.split('/')
                    credit_val = float(prices[0]) - float(prices[1])
                    per_contract_risk = 500 - credit_val * 100
                else:
                    per_contract_risk = 500
            else:
                per_contract_risk = float(entry_px) * 100 if entry_px else 100

            if per_contract_risk <= 0:
                per_contract_risk = 100

            contracts = max(1, round(risk_budget / per_contract_risk))
            actual_risk = contracts * per_contract_risk
            sized_pnl = pnl * contracts
            total_comm = commission_for_trade(struct, contracts)

            trade_rec = {
                'date': d,
                'strategy': edge['name'],
                'label': edge['id'],
                'session': edge['session'],
                'structure': struct,
                'direction': 'LONG' if edge['direction'] == 1 else 'SHORT',
                'spx_entry': t['entry_price'],
                'spx_exit': t['exit_price'],
                'entry_time': t['entry_time'],
                'exit_time': t['exit_time'],
                'hold_mins': t['hold_mins'],
                'exit_reason': t['exit_reason'],
                'und_pts': t['und_pts'],
                'vix': round(vix_open, 1),
                'opt_pnl': pnl,
                'opt_ticker': ticker or '',
                'opt_ticker_readable': _make_readable_ticker(ticker) if ticker else '',
                'opt_entry_px': str(epx) if epx is not None else '',
                'opt_exit_px': str(xpx) if xpx is not None else '',
                'color': edge['color'],
                'grade': grade,
                'contracts': contracts,
                'risk': round(actual_risk, 2),
                'sized_pnl': round(sized_pnl, 2),
                'commission': round(total_comm, 2),
            }
            new_trades.append(trade_rec)
            log.info(f"    {edge['name']}: SIGNAL → {struct} P&L ${pnl:.0f}/lot, sized ${sized_pnl:,.0f}")

    # Persist
    all_new = existing_new + new_trades
    _save_calendar_trades(all_new)

    log.info(f"Calendar refresh complete: {len(new_trades)} new trades found, {len(all_new)} total new")
    return jsonify({
        'ok': True,
        'new_trades': len(new_trades),
        'total_new': len(all_new),
        'trades': new_trades,
        'message': f'Found {len(new_trades)} new trades across {len(dates_to_scan)} days'
    })


# ──────────────────────────────────────────────────────────────
# SCHEDULER
# ──────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(engine_tick, 'interval', seconds=1, max_instances=1, coalesce=True)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
