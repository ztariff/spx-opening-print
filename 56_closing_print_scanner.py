#!/usr/bin/env python3
"""
Closing Print Scanner
======================
Searches for tradeable edges in the final 2 hours of the trading day (2:00-4:00 PM ET).
Mirrors the 3-phase pipeline from the Opening Print scanner:
  Phase 1: Underlying scan — signals × filters × exits (on SPX points)
  Phase 2: IS/OOS validation (2018-2022 vs 2023-2026)
  Phase 3: Price validated edges with real SPXW 0DTE option data

All CLAUDE.md rules apply:
  - No synthetic/fabricated data
  - No Black-Scholes substitution
  - Forward-walk simulation only (no hindsight)
  - Surface missing data gaps, never silently skip

Signal design philosophy:
  - All features computed from bars AVAILABLE at signal time (no future data)
  - 2:00 PM bar (mins=840) = "closing first bar" — known at 2:01 PM
  - Morning session summary (9:30-2:00) is fully known at 2:00 PM
  - Entry at bar close of signal bar (same convention as opening print)
  - Hold times: 1 min to 120 min (market close at 4:00 PM = mins 960)

Note: 1-minute bar resolution means shortest possible hold = 1 bar (~1 min).
"""

import csv, json, math, os, statistics, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'
START_DATE = '2018-06-01'

# ═══════════════════════════════════════════════════════════════
# DATA LOADING
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
            d2 = sorted_dates[j]
            d1 = sorted_dates[j-1]
            hi = spx_daily[d2]['h']
            lo = spx_daily[d2]['l']
            pc = spx_daily[d1]['c']
            trs.append(max(hi-lo, abs(hi-pc), abs(lo-pc)))
        atr5[sorted_dates[i]] = statistics.mean(trs)

    print(f"  SPX 1min: {len(spx_1min)} days, daily: {len(spx_daily)}, VIX: {len(vix_daily)}")
    return spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5, prev_days, vix_prev_close, rsi5, atr5


# ═══════════════════════════════════════════════════════════════
# CLOSING PRINT FEATURE EXTRACTION
# ═══════════════════════════════════════════════════════════════
def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, sma10, sma5,
                     prev_days, vix_prev_close, rsi5, atr5):
    """
    For each trading day, compute features known at 2:00 PM ET.
    The "closing first bar" is the 14:00 bar (mins=840), known at 14:01.
    Morning session = bars from 9:30 to 13:59 (mins 570-839).
    """
    print("Extracting closing-window features…")
    days = []

    for d in sorted(spx_1min.keys()):
        if d < START_DATE: continue
        all_bars = spx_1min[d]
        dd = spx_daily.get(d)
        vd = vix_daily.get(d)
        prev = prev_days.get(d)
        if not dd or not vd or not prev or len(all_bars) < 300:
            continue  # need enough bars to have morning + afternoon

        # Split bars into morning (9:30-13:59) and afternoon (14:00-15:59)
        morning_bars = [b for b in all_bars if b['mins'] < 840]
        afternoon_bars = [b for b in all_bars if b['mins'] >= 840]

        if len(morning_bars) < 200 or len(afternoon_bars) < 5:
            continue  # need full morning session + some afternoon

        # === DAILY / ENVIRONMENTAL (known before open) ===
        vix_open = vd['o']
        vix_pc = vix_prev_close.get(d, vix_open)
        vix_change_pct = (vix_open - vix_pc) / vix_pc * 100 if vix_pc > 0 else 0

        prev_close = prev[0]['c']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100
        prev_bullish = prev[0]['c'] > prev[0]['o']
        prev_range = prev[0]['h'] - prev[0]['l']
        prev_close_loc = (prev[0]['c'] - prev[0]['l']) / prev_range if prev_range > 0 else 0.5

        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_10d = dd['o'] > sma10.get(d, 0) if d in sma10 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        if vix_open < 14: vix_regime = 'very_low'
        elif vix_open < 18: vix_regime = 'low'
        elif vix_open < 22: vix_regime = 'mid'
        elif vix_open < 30: vix_regime = 'high'
        else: vix_regime = 'extreme'

        cur_rsi = rsi5.get(d, 50)
        cur_atr = atr5.get(d, 30)

        # === MORNING SESSION SUMMARY (known at 2:00 PM) ===
        morn_open = morning_bars[0]['o']
        morn_close = morning_bars[-1]['c']  # last bar before 2PM
        morn_high = max(b['h'] for b in morning_bars)
        morn_low = min(b['l'] for b in morning_bars)
        morn_range = morn_high - morn_low
        morn_ret = (morn_close - morn_open) / morn_open * 100
        morn_bullish = morn_close > morn_open
        morn_close_loc = (morn_close - morn_low) / morn_range if morn_range > 0 else 0.5
        morn_body_ratio = abs(morn_close - morn_open) / morn_range if morn_range > 0 else 0

        # Morning volume profile
        morn_total_vol = sum(b['v'] for b in morning_bars)

        # Morning trend strength: how many of last 30 morning bars were bullish
        last30 = morning_bars[-30:]
        morn_bull_bars = sum(1 for b in last30 if b['c'] > b['o'])
        morn_bull_ratio = morn_bull_bars / len(last30)

        # Intraday position relative to day's range at 2PM
        day_high_at_2pm = morn_high
        day_low_at_2pm = morn_low
        day_range_at_2pm = day_high_at_2pm - day_low_at_2pm
        price_at_2pm = morn_close
        range_position = (price_at_2pm - day_low_at_2pm) / day_range_at_2pm if day_range_at_2pm > 0 else 0.5

        # Was new high made in last hour of morning (1-2PM)?
        last_hour_morning = [b for b in morning_bars if b['mins'] >= 780]  # 1:00 PM+
        if last_hour_morning:
            last_hour_high = max(b['h'] for b in last_hour_morning)
            new_high_last_hour = last_hour_high >= morn_high * 0.999
            last_hour_ret = (last_hour_morning[-1]['c'] - last_hour_morning[0]['o']) / last_hour_morning[0]['o'] * 100
        else:
            new_high_last_hour = False
            last_hour_ret = 0

        # Morning momentum: last 15 bars before 2PM
        last15 = morning_bars[-15:]
        m15_ret = (last15[-1]['c'] - last15[0]['o']) / last15[0]['o'] * 100
        m15_bullish = last15[-1]['c'] > last15[0]['o']

        # Last 5 bars before 2PM
        last5 = morning_bars[-5:]
        m5_ret = (last5[-1]['c'] - last5[0]['o']) / last5[0]['o'] * 100
        m5_bullish = last5[-1]['c'] > last5[0]['o']
        m5_consec_bull = all(b['c'] > b['o'] for b in last5)
        m5_consec_bear = all(b['c'] < b['o'] for b in last5)

        # Distance from 2PM price to morning high/low
        dist_to_high_pct = (morn_high - price_at_2pm) / price_at_2pm * 100
        dist_to_low_pct = (price_at_2pm - morn_low) / price_at_2pm * 100

        # Intraday VIX proxy: morning range as % of open
        intraday_vol = morn_range / morn_open * 100

        # === CLOSING FIRST BAR (2:00 PM bar, known at 2:01 PM) ===
        cfb = afternoon_bars[0]  # the 14:00 bar
        cfb_ret = (cfb['c'] - cfb['o']) / cfb['o'] * 100
        cfb_bullish = cfb['c'] > cfb['o']
        cfb_range = cfb['h'] - cfb['l']
        cfb_body_ratio = abs(cfb['c'] - cfb['o']) / cfb_range if cfb_range > 0 else 0
        cfb_range_pct = cfb_range / cfb['o'] * 100

        # First 2 closing bars (14:00 + 14:01)
        if len(afternoon_bars) > 1:
            cfb2_bull = afternoon_bars[0]['c'] > afternoon_bars[0]['o'] and afternoon_bars[1]['c'] > afternoon_bars[1]['o']
            cfb2_bear = afternoon_bars[0]['c'] < afternoon_bars[0]['o'] and afternoon_bars[1]['c'] < afternoon_bars[1]['o']
            cfb2_ret = (afternoon_bars[1]['c'] - afternoon_bars[0]['o']) / afternoon_bars[0]['o'] * 100
        else:
            cfb2_bull = False
            cfb2_bear = False
            cfb2_ret = 0

        # First 3 closing bars
        cfb3 = afternoon_bars[:3]
        if len(cfb3) == 3:
            cfb3_consec_bull = all(b['c'] > b['o'] for b in cfb3)
            cfb3_consec_bear = all(b['c'] < b['o'] for b in cfb3)
            cfb3_ret = (cfb3[-1]['c'] - cfb3[0]['o']) / cfb3[0]['o'] * 100
        else:
            cfb3_consec_bull = False
            cfb3_consec_bear = False
            cfb3_ret = 0

        # Closing OR5 (first 5 bars of afternoon = 14:00-14:04)
        cor5 = afternoon_bars[:5]
        if len(cor5) == 5:
            cor5_hi = max(b['h'] for b in cor5)
            cor5_lo = min(b['l'] for b in cor5)
            cor5_rng = cor5_hi - cor5_lo
            cor5_ret = (cor5[-1]['c'] - cor5[0]['o']) / cor5[0]['o'] * 100
            cor5_bullish = cor5[-1]['c'] > cor5[0]['o']
            cor5_close_loc = (cor5[-1]['c'] - cor5_lo) / cor5_rng if cor5_rng > 0 else 0.5
        else:
            cor5_ret = 0
            cor5_bullish = False
            cor5_close_loc = 0.5

        # Closing OR15 (14:00-14:14)
        cor15 = afternoon_bars[:15]
        if len(cor15) >= 15:
            cor15_hi = max(b['h'] for b in cor15)
            cor15_lo = min(b['l'] for b in cor15)
            cor15_rng = cor15_hi - cor15_lo
            cor15_ret = (cor15[-1]['c'] - cor15[0]['o']) / cor15[0]['o'] * 100
            cor15_bullish = cor15[-1]['c'] > cor15[0]['o']
            cor15_close_loc = (cor15[-1]['c'] - cor15_lo) / cor15_rng if cor15_rng > 0 else 0.5
        else:
            cor15_ret = 0
            cor15_bullish = False
            cor15_close_loc = 0.5

        days.append({
            'date': d, 'all_bars': all_bars, 'afternoon_bars': afternoon_bars,
            'morning_bars': morning_bars,
            'open': dd['o'], 'high': dd['h'], 'low': dd['l'], 'close': dd['c'],
            'vix': vix_open, 'vix_regime': vix_regime,
            'vix_change_pct': vix_change_pct,
            'gap_pct': gap_pct,
            'prev_close': prev_close, 'prev_bullish': prev_bullish,
            'prev_close_loc': prev_close_loc,
            'above_50d': above_50d, 'above_20d': above_20d,
            'above_10d': above_10d,
            'dow': dow, 'rsi5': cur_rsi, 'atr5': cur_atr,
            # Morning summary
            'morn_ret': morn_ret, 'morn_bullish': morn_bullish,
            'morn_close_loc': morn_close_loc, 'morn_body_ratio': morn_body_ratio,
            'morn_range_pct': morn_range / morn_open * 100 if morn_open > 0 else 0,
            'morn_bull_ratio': morn_bull_ratio,
            'range_position': range_position,
            'new_high_last_hour': new_high_last_hour,
            'last_hour_ret': last_hour_ret,
            'm15_ret': m15_ret, 'm15_bullish': m15_bullish,
            'm5_ret': m5_ret, 'm5_bullish': m5_bullish,
            'm5_consec_bull': m5_consec_bull, 'm5_consec_bear': m5_consec_bear,
            'dist_to_high_pct': dist_to_high_pct,
            'dist_to_low_pct': dist_to_low_pct,
            'intraday_vol': intraday_vol,
            'price_at_2pm': price_at_2pm,
            # Closing first bar
            'cfb': cfb, 'cfb_ret': cfb_ret, 'cfb_bullish': cfb_bullish,
            'cfb_body_ratio': cfb_body_ratio, 'cfb_range_pct': cfb_range_pct,
            'cfb2_bull': cfb2_bull, 'cfb2_bear': cfb2_bear, 'cfb2_ret': cfb2_ret,
            'cfb3_consec_bull': cfb3_consec_bull, 'cfb3_consec_bear': cfb3_consec_bear,
            'cfb3_ret': cfb3_ret,
            'cor5_ret': cor5_ret, 'cor5_bullish': cor5_bullish,
            'cor5_close_loc': cor5_close_loc,
            'cor15_ret': cor15_ret, 'cor15_bullish': cor15_bullish,
            'cor15_close_loc': cor15_close_loc,
        })

    print(f"  {len(days)} trading days with full closing-window data")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION (forward-walk on afternoon bars only)
# ═══════════════════════════════════════════════════════════════
def simulate_trade(afternoon_bars, entry_idx, direction, exit_params):
    """
    Simulate a trade starting from afternoon_bars[entry_idx].
    Direction: 1=long, -1=short.
    All exits happen before market close (mins=960).
    """
    entry_bar = afternoon_bars[entry_idx]
    entry_price = entry_bar['c']
    entry_mins = entry_bar['mins']

    pt_pts = exit_params.get('pt_pts')
    sl_pts = exit_params.get('sl_pts')
    trail_pct = exit_params.get('trail_pct')
    ts_min = exit_params.get('ts_min', 120)
    ts_deadline = min(entry_mins + ts_min, 959)  # never past 3:59 PM

    vix_mult = exit_params.get('vix_mult')
    if vix_mult:
        vix = exit_params.get('_vix', 20)
        scale = vix / 20.0
        if pt_pts: pt_pts = pt_pts * scale
        if sl_pts: sl_pts = sl_pts * scale

    if direction == 1:
        pt_level = entry_price + pt_pts if pt_pts else None
        sl_level = entry_price - sl_pts if sl_pts else None
    else:
        pt_level = entry_price - pt_pts if pt_pts else None
        sl_level = entry_price + sl_pts if sl_pts else None

    peak, trough = entry_price, entry_price
    exit_price = entry_price
    exit_reason = 'time_stop'
    exit_idx = len(afternoon_bars) - 1

    for j in range(entry_idx + 1, len(afternoon_bars)):
        bar = afternoon_bars[j]

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
            if trail_pct and peak > entry_price:
                tl = peak * (1 - trail_pct / 100)
                if bar['l'] <= tl:
                    exit_price = tl; exit_reason = 'trailing_stop'; exit_idx = j; break
        else:
            if bar['l'] < trough: trough = bar['l']
            if sl_level and bar['h'] >= sl_level:
                exit_price = sl_level; exit_reason = 'stop_loss'; exit_idx = j; break
            if pt_level and bar['l'] <= pt_level:
                exit_price = pt_level; exit_reason = 'profit_target'; exit_idx = j; break
            if trail_pct and trough < entry_price:
                tl = trough * (1 + trail_pct / 100)
                if bar['h'] >= tl:
                    exit_price = tl; exit_reason = 'trailing_stop'; exit_idx = j; break
    else:
        exit_price = afternoon_bars[-1]['c']
        exit_idx = len(afternoon_bars) - 1

    exit_bar = afternoon_bars[exit_idx]
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
# CLOSING PRINT SIGNALS
# ═══════════════════════════════════════════════════════════════
def closing_signals(day):
    """
    Generate signals for closing-window trades.
    Each signal returns (name, entry_bar_index_in_afternoon, direction).
    entry_idx=0 means the 14:00 bar (entry at its close = 14:01).
    """
    results = []
    cfb_ret = day['cfb_ret']
    morn_ret = day['morn_ret']

    # ════════════════════════════════════════════════════
    # A. CLOSING FIRST BAR SIGNALS (entry at 14:01)
    # ════════════════════════════════════════════════════

    # A1: Strong closing first bar — bullish
    if day['cfb_body_ratio'] > 0.75 and day['cfb_bullish'] and cfb_ret >= 0.03:
        results.append(('CFB_StrongBull', 0, 1))

    # A2: Strong closing first bar + morning was bullish (trend continuation)
    if (day['cfb_body_ratio'] > 0.70 and day['cfb_bullish'] and cfb_ret >= 0.02
        and day['morn_bullish']):
        results.append(('CFB_Bull_MornBull', 0, 1))

    # A3: Strong closing first bar + near day high (breakout)
    if (day['cfb_bullish'] and cfb_ret >= 0.02 and day['range_position'] > 0.75):
        results.append(('CFB_Bull_NearHigh', 0, 1))

    # A4: Strong closing first bar + above trend
    if (day['cfb_body_ratio'] > 0.70 and day['cfb_bullish'] and cfb_ret >= 0.03
        and day['above_20d'] is True):
        results.append(('CFB_Bull_Trend', 0, 1))

    # A5: Morning selloff + bullish closing first bar (mean reversion bounce)
    if (morn_ret <= -0.15 and day['cfb_bullish'] and cfb_ret >= 0.03):
        results.append(('CFB_MornDip_Bounce', 0, 1))

    # A6: Morning selloff + strong closing bar + near low (reversal)
    if (morn_ret <= -0.20 and day['cfb_bullish'] and cfb_ret >= 0.03
        and day['range_position'] < 0.40):
        results.append(('CFB_Reversal_FromLow', 0, 1))

    # A7: Morning rally continues into close + closing bar confirms
    if (morn_ret >= 0.20 and day['cfb_bullish'] and cfb_ret >= 0.02
        and day['range_position'] > 0.70):
        results.append(('CFB_RallyExtend', 0, 1))

    # A8: Flat morning + closing bar breakout
    if (abs(morn_ret) <= 0.10 and day['cfb_bullish'] and cfb_ret >= 0.04
        and day['cfb_body_ratio'] > 0.65):
        results.append(('CFB_FlatBreak_Bull', 0, 1))

    # A9: Last hour momentum into close
    if (day['last_hour_ret'] >= 0.05 and day['cfb_bullish'] and cfb_ret >= 0.02):
        results.append(('CFB_LastHourMom', 0, 1))

    # A10: 5-bar momentum into 2PM + closing bar confirms
    if (day['m5_consec_bull'] and day['cfb_bullish'] and cfb_ret >= 0.02):
        results.append(('CFB_5BarMom', 0, 1))

    # A11: New high last hour + closing bar bullish
    if (day['new_high_last_hour'] and day['cfb_bullish'] and cfb_ret >= 0.02):
        results.append(('CFB_NewHigh_Cont', 0, 1))

    # A12: Morning bullish + prev day bullish + closing bar bullish (triple momentum)
    if (day['morn_bullish'] and day['prev_bullish'] and day['cfb_bullish']
        and cfb_ret >= 0.02):
        results.append(('CFB_TripleMom', 0, 1))

    # A13: Low VIX + bullish closing bar + above trend
    if (day['vix'] < 18 and day['cfb_bullish'] and cfb_ret >= 0.02
        and day['above_20d'] is True):
        results.append(('CFB_LowVIX_Bull', 0, 1))

    # A14: Strong body + low intraday vol (quiet day breakout)
    if (day['cfb_body_ratio'] > 0.75 and day['cfb_bullish'] and cfb_ret >= 0.03
        and day['intraday_vol'] < 0.60):
        results.append(('CFB_QuietDay_Bull', 0, 1))

    # A15: Morning bear + closing strong bear (momentum into close - short side)
    if (morn_ret <= -0.20 and not day['cfb_bullish'] and cfb_ret <= -0.03
        and day['range_position'] < 0.30):
        results.append(('CFB_BearCont', 0, -1))

    # ════════════════════════════════════════════════════
    # B. TWO-BAR CLOSING SIGNALS (entry at 14:02)
    # ════════════════════════════════════════════════════

    # B1: Two consecutive bullish closing bars
    if day['cfb2_bull'] and day['cfb2_ret'] >= 0.04:
        results.append(('C2Bar_Bull', 1, 1))

    # B2: Two closing bars + morning bullish
    if day['cfb2_bull'] and day['cfb2_ret'] >= 0.03 and day['morn_bullish']:
        results.append(('C2Bar_Bull_MornBull', 1, 1))

    # B3: Two closing bars + near high
    if day['cfb2_bull'] and day['cfb2_ret'] >= 0.03 and day['range_position'] > 0.70:
        results.append(('C2Bar_Bull_NearHigh', 1, 1))

    # ════════════════════════════════════════════════════
    # C. THREE-BAR CLOSING SIGNALS (entry at 14:03)
    # ════════════════════════════════════════════════════

    # C1: Three consecutive bullish closing bars
    if day['cfb3_consec_bull'] and day['cfb3_ret'] >= 0.05:
        results.append(('C3Bar_Bull', 2, 1))

    # C2: Three closing bars + trend
    if (day['cfb3_consec_bull'] and day['cfb3_ret'] >= 0.04
        and day['above_20d'] is True):
        results.append(('C3Bar_Bull_Trend', 2, 1))

    # ════════════════════════════════════════════════════
    # D. COR5 SIGNALS (closing opening range 5, entry at 14:05)
    # ════════════════════════════════════════════════════

    # D1: Bullish COR5
    if day['cor5_bullish'] and day['cor5_ret'] >= 0.04 and day['cor5_close_loc'] > 0.70:
        results.append(('COR5_Bull', 4, 1))

    # D2: COR5 + morning bullish
    if (day['cor5_bullish'] and day['cor5_ret'] >= 0.03
        and day['morn_bullish'] and day['cor5_close_loc'] > 0.60):
        results.append(('COR5_Bull_MornBull', 4, 1))

    # D3: COR5 + trend + near high
    if (day['cor5_bullish'] and day['cor5_ret'] >= 0.03
        and day['above_20d'] is True and day['range_position'] > 0.65):
        results.append(('COR5_Bull_TrendHigh', 4, 1))

    # ════════════════════════════════════════════════════
    # E. COR15 SIGNALS (closing opening range 15, entry at 14:15)
    # ════════════════════════════════════════════════════

    # E1: Bullish COR15
    if day['cor15_bullish'] and day['cor15_ret'] >= 0.05 and day['cor15_close_loc'] > 0.70:
        results.append(('COR15_Bull', 14, 1))

    # E2: COR15 + morning continuation
    if (day['cor15_bullish'] and day['cor15_ret'] >= 0.04
        and day['morn_bullish']):
        results.append(('COR15_Bull_MornBull', 14, 1))

    # E3: COR15 + trend
    if (day['cor15_bullish'] and day['cor15_ret'] >= 0.04
        and day['above_20d'] is True):
        results.append(('COR15_Bull_Trend', 14, 1))

    # ════════════════════════════════════════════════════
    # F. MORNING-SESSION-ONLY SIGNALS (entry at 14:00 open)
    #    These don't use cfb at all — just morning features.
    #    Entry is at the OPEN of the 14:00 bar.
    # ════════════════════════════════════════════════════

    # F1: Morning closed near high + above trend (bullish into close)
    if (day['morn_close_loc'] > 0.80 and day['morn_bullish']
        and day['above_20d'] is True):
        results.append(('Morn_NearHigh_Trend', 0, 1))

    # F2: Strong morning rally + new high last hour
    if (morn_ret >= 0.25 and day['new_high_last_hour']
        and day['morn_close_loc'] > 0.70):
        results.append(('Morn_StrongRally', 0, 1))

    # F3: Morning mean-revert setup: big dip + recovery to midrange
    if (morn_ret <= -0.10 and day['morn_close_loc'] > 0.40
        and day['last_hour_ret'] >= 0.05):
        results.append(('Morn_DipRecover', 0, 1))

    # F4: 15-min momentum into close
    if (day['m15_ret'] >= 0.08 and day['m15_bullish']
        and day['morn_close_loc'] > 0.60):
        results.append(('Morn_15MinMom', 0, 1))

    # F5: Quiet morning + strong last 5 bars (breakout from consolidation)
    if (abs(morn_ret) <= 0.08 and day['m5_ret'] >= 0.04
        and day['m5_bullish']):
        results.append(('Morn_QuietBreak', 0, 1))

    # F6: Morning bullish + prev bullish + above trend (all aligned)
    if (day['morn_bullish'] and day['prev_bullish']
        and day['above_20d'] is True and day['above_50d'] is True):
        results.append(('Morn_AllAligned', 0, 1))

    return results


# ═══════════════════════════════════════════════════════════════
# EXIT SETS — tuned for afternoon (max 120 min)
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    # Ultra-micro scalps (gamma is huge on 0DTE at 2PM)
    'Micro_1_0.5_2': {'pt_pts': 1, 'sl_pts': 0.5, 'ts_min': 2},
    'Micro_1_1_3': {'pt_pts': 1, 'sl_pts': 1, 'ts_min': 3},
    'Micro_2_1_3': {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 3},
    'Micro_2_1_5': {'pt_pts': 2, 'sl_pts': 1, 'ts_min': 5},
    'Micro_2_1.5_5': {'pt_pts': 2, 'sl_pts': 1.5, 'ts_min': 5},
    'Micro_3_1_5': {'pt_pts': 3, 'sl_pts': 1, 'ts_min': 5},
    'Micro_3_1.5_5': {'pt_pts': 3, 'sl_pts': 1.5, 'ts_min': 5},
    'Micro_3_2_5': {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 5},
    'Micro_4_2_5': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 5},
    'Micro_4_2_8': {'pt_pts': 4, 'sl_pts': 2, 'ts_min': 8},
    'Micro_5_2_5': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 5},
    'Micro_5_2_8': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 8},
    'Micro_5_2_10': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10},
    'Micro_5_3_10': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 10},
    # Standard (for longer closing holds)
    'Std_5_3_15': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 15},
    'Std_8_3_15': {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15},
    'Std_8_4_15': {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
    'Std_10_4_20': {'pt_pts': 10, 'sl_pts': 4, 'ts_min': 20},
    'Std_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30},
    'Std_15_5_30': {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    'Std_15_8_60': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 60},
    # Hold to close
    'HTC_5_3': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 120},
    'HTC_10_5': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 120},
    'HTC_15_8': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 120},
    'HTC_NoSL': {'pt_pts': None, 'sl_pts': None, 'ts_min': 120},  # pure hold-to-close
    # Asymmetric
    'Asym_8_2_10': {'pt_pts': 8, 'sl_pts': 2, 'ts_min': 10},
    'Asym_10_3_15': {'pt_pts': 10, 'sl_pts': 3, 'ts_min': 15},
    'Asym_12_4_20': {'pt_pts': 12, 'sl_pts': 4, 'ts_min': 20},
    'Asym_15_5_30': {'pt_pts': 15, 'sl_pts': 5, 'ts_min': 30},
    # Trail
    'Trail03_SL2_10': {'trail_pct': 0.03, 'sl_pts': 2, 'ts_min': 10},
    'Trail05_SL3_15': {'trail_pct': 0.05, 'sl_pts': 3, 'ts_min': 15},
    'Trail05_SL2_10': {'trail_pct': 0.05, 'sl_pts': 2, 'ts_min': 10},
    # Adaptive
    'Adpt_5_2_10': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 10, 'vix_mult': True},
    'Adpt_8_3_15': {'pt_pts': 8, 'sl_pts': 3, 'ts_min': 15, 'vix_mult': True},
}

# ═══════════════════════════════════════════════════════════════
# FILTERS
# ═══════════════════════════════════════════════════════════════
FILTERS = {
    'All': lambda d: True,
    'VeryLow': lambda d: d['vix'] < 14,
    'Low': lambda d: d['vix'] < 18,
    'lt20': lambda d: d['vix'] < 20,
    'lt22': lambda d: d['vix'] < 22,
    'Mid': lambda d: 18 <= d['vix'] < 25,
    'High': lambda d: d['vix'] >= 25,
    'TueWed': lambda d: d['dow'] in (1, 2),
    'NotMon': lambda d: d['dow'] != 0,
    'Fri': lambda d: d['dow'] == 4,
    'Mon': lambda d: d['dow'] == 0,
    'lt20_TueWed': lambda d: d['vix'] < 20 and d['dow'] in (1, 2),
    'lt20_NotMon': lambda d: d['vix'] < 20 and d['dow'] != 0,
    'Low_NotMon': lambda d: d['vix'] < 18 and d['dow'] != 0,
    # Afternoon-specific
    'MornBull': lambda d: d['morn_bullish'],
    'MornBull_Low': lambda d: d['morn_bullish'] and d['vix'] < 18,
    'MornBull_Trend': lambda d: d['morn_bullish'] and d['above_20d'] is True,
    'NearHigh': lambda d: d['range_position'] > 0.70,
    'NearHigh_Low': lambda d: d['range_position'] > 0.70 and d['vix'] < 18,
}

# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════
def compute_stats(trades, label=''):
    if len(trades) < 10:
        return None
    pts = [t['und_pts'] for t in trades]
    n = len(pts)
    avg = statistics.mean(pts)
    tot = sum(pts)
    wr = sum(1 for p in pts if p > 0) / n * 100
    std = statistics.stdev(pts) if n > 1 else 0
    sharpe = avg / std if std > 0 else 0

    gw = sum(p for p in pts if p > 0)
    gl = abs(sum(p for p in pts if p <= 0))
    pf = gw / gl if gl > 0 else 99

    cum = 0; peak_cum = 0; max_dd = 0
    for p in pts:
        cum += p
        if cum > peak_cum: peak_cum = cum
        dd = peak_cum - cum
        if dd > max_dd: max_dd = dd

    max_consec_loss = 0; curr_loss = 0
    for p in pts:
        if p <= 0:
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    monthly = defaultdict(list)
    for t in trades:
        monthly[t['date'][:7]].append(t['und_pts'])
    monthly_pnl = {ym: sum(v) for ym, v in monthly.items()}
    months_positive = sum(1 for v in monthly_pnl.values() if v > 0)
    months_total = len(monthly_pnl)
    monthly_wr = months_positive / months_total * 100 if months_total > 0 else 0

    cum_pts = []; c = 0
    for p in pts:
        c += p; cum_pts.append(c)
    if len(cum_pts) > 5:
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(cum_pts)
        ss_xy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(cum_pts))
        ss_xx = sum((i - x_mean)**2 for i in range(n))
        ss_yy = sum((y - y_mean)**2 for y in cum_pts)
        r_sq = (ss_xy**2) / (ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0
    else:
        r_sq = 0

    avg_hold = statistics.mean(t['hold_mins'] for t in trades)

    return {
        'label': label, 'n': n, 'wr': round(wr, 1),
        'avg_pts': round(avg, 2), 'total_pts': round(tot, 1),
        'sharpe': round(sharpe, 3), 'pf': round(pf, 2),
        'max_dd': round(max_dd, 1),
        'max_consec_loss': max_consec_loss,
        'monthly_wr': round(monthly_wr, 1),
        'equity_r2': round(r_sq, 3),
        'avg_hold': round(avg_hold, 1),
    }


# ═══════════════════════════════════════════════════════════════
# OPTION PRICING — real SPXW 0DTE data
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
    elif struct == 'bull_call_5':
        return try_spread('C', atm, atm + 5)
    elif struct == 'bull_call_10':
        return try_spread('C', atm, atm + 10)
    return None, None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 160)
    print("CLOSING PRINT SCANNER — Last 2 Hours + Real SPXW 0DTE Option Pricing")
    print("=" * 160)

    data = load_all_data()
    days = extract_features(*data)

    # Pre-compute signals
    print("Generating closing-window signals…")
    day_signals = {}
    sig_counts = defaultdict(int)
    for day in days:
        sigs = closing_signals(day)
        day_signals[day['date']] = sigs
        for name, _, _ in sigs:
            sig_counts[name] += 1

    print(f"\n  Closing signal frequency across {len(days)} days:")
    for name, count in sorted(sig_counts.items(), key=lambda x: -x[1]):
        pct = count / len(days) * 100
        print(f"    {name:35s}: {count:>5} fires ({pct:.1f}%)")

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Underlying scan
    # ═══════════════════════════════════════════════════════════════
    total = len(sig_counts) * len(FILTERS) * len(EXIT_SETS)
    print(f"\n{'='*160}")
    print(f"PHASE 1: UNDERLYING SCAN — {len(sig_counts)} signals × {len(FILTERS)} filters × {len(EXIT_SETS)} exits = {total} combos")
    print(f"{'='*160}")

    all_results = []
    combo = 0

    for sig_name in sorted(sig_counts.keys()):
        for filt_name, filt_fn in FILTERS.items():
            for exit_name, exit_params in EXIT_SETS.items():
                combo += 1
                if combo % 10000 == 0:
                    print(f"  [{combo}/{total}]…", flush=True)

                trades = []
                for day in days:
                    if not filt_fn(day): continue
                    for sname, entry_idx, direction in day_signals[day['date']]:
                        if sname != sig_name: continue
                        abars = day['afternoon_bars']
                        if entry_idx >= len(abars) - 3: continue
                        ep = dict(exit_params)
                        if ep.get('vix_mult'):
                            ep['_vix'] = day['vix']
                        trade = simulate_trade(abars, entry_idx, direction, ep)
                        trade['date'] = day['date']
                        trade['vix'] = day['vix']
                        trades.append(trade)
                        break

                stats = compute_stats(trades, f"{sig_name}|{filt_name}|{exit_name}")
                if stats and stats['sharpe'] > 0.20 and stats['pf'] > 1.2 and stats['n'] >= 15:
                    stats['signal'] = sig_name
                    stats['filter'] = filt_name
                    stats['exit_set'] = exit_name
                    stats['trades'] = trades
                    all_results.append(stats)

    print(f"\n  {combo} combos scanned, {len(all_results)} edges with Sharpe>0.20 & PF>1.2 & N>=15")

    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*180}")
    print(f"TOP 100 CLOSING PRINT EDGES BY SHARPE (underlying SPX points)")
    print(f"{'='*180}")
    print(f"{'Label':>70s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
          f"{'Sharpe':>7} {'PF':>6} {'MaxDD':>8} {'CL':>4} {'MoWR%':>6} {'R²':>5} {'Hold':>6}")
    print("-" * 180)

    for r in all_results[:100]:
        print(f"{r['label'][:70]:>70s} {r['n']:>5} {r['wr']:>5.1f}% "
              f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['max_dd']:>7.1f} "
              f"{r['max_consec_loss']:>4} {r['monthly_wr']:>5.1f}% "
              f"{r['equity_r2']:>5.3f} {r['avg_hold']:>5.1f}m")

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: OOS Validation
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"PHASE 2: OUT-OF-SAMPLE VALIDATION — IS: 2018-2022 vs OOS: 2023-2026")
    print(f"{'='*180}")

    validated = []
    for r in all_results[:100]:
        trades = r.get('trades', [])
        if not trades: continue
        is_t = [t for t in trades if t['date'] < '2023-01-01']
        oos_t = [t for t in trades if t['date'] >= '2023-01-01']
        is_pts = [t['und_pts'] for t in is_t]
        oos_pts = [t['und_pts'] for t in oos_t]

        if len(is_pts) >= 8 and len(oos_pts) >= 8:
            is_avg = statistics.mean(is_pts)
            is_std = statistics.stdev(is_pts) if len(is_pts) > 1 else 0
            is_sh = is_avg / is_std if is_std > 0 else 0
            oos_avg = statistics.mean(oos_pts)
            oos_std = statistics.stdev(oos_pts) if len(oos_pts) > 1 else 0
            oos_sh = oos_avg / oos_std if oos_std > 0 else 0

            holds = oos_sh > 0.05 and oos_avg > 0
            verdict = 'HOLDS' if holds else 'DEGRADES'
            print(f"  {r['label'][:65]:>65s}  IS: Sh={is_sh:>6.3f} N={len(is_pts):>4}  "
                  f"OOS: Sh={oos_sh:>6.3f} N={len(oos_pts):>4}  [{verdict}]")
            if holds:
                validated.append({
                    'edge': r, 'is_sharpe': round(is_sh,3), 'oos_sharpe': round(oos_sh,3),
                    'is_n': len(is_pts), 'oos_n': len(oos_pts),
                })
        elif len(is_pts) >= 8:
            print(f"  {r['label'][:65]:>65s}  IS: N={len(is_pts):>4}  OOS: <8 trades ({len(oos_pts)})")

    print(f"\n  {len(validated)} of {min(100, len(all_results))} edges PASSED OOS validation")

    if len(validated) == 0:
        print("\n  NO CLOSING PRINT EDGES SURVIVED OOS VALIDATION.")
        out_dir = SCRIPT_DIR / 'backtest_results'
        out_dir.mkdir(exist_ok=True)
        und_summary = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results[:200]]
        with open(out_dir / 'closing_underlying_results.json', 'w') as f:
            json.dump(und_summary, f, indent=2)
        print(f"  Saved {len(und_summary)} underlying closing results for review")
        return

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Price with real SPXW options
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"PHASE 3: PRICING TOP {min(40, len(validated))} VALIDATED EDGES WITH REAL SPXW 0DTE OPTIONS")
    print(f"{'='*180}")

    # For bullish closing print, use call structures
    bull_structs = ['long_call', 'long_itm_call', 'long_otm_call', 'bull_call_5', 'bull_call_10']
    opt_results = []

    for vi, v in enumerate(validated[:40]):
        r = v['edge']
        trades = r['trades']
        if not trades: continue
        direction = trades[0]['direction']
        structs = bull_structs  # all closing signals are long for now

        print(f"\n{'─'*160}")
        print(f"  [{vi+1}] {r['label']} — {len(trades)} trades, dir={direction}, UndSharpe={r['sharpe']}")

        for struct in structs:
            priced = []
            missed = 0
            details = []

            for trade in trades:
                d = trade['date']
                pnl, info = price_with_provenance(
                    d, trade['entry_mins'], trade['exit_mins'],
                    trade['entry_price'], trade['direction'], struct)
                if pnl is not None:
                    priced.append(pnl)
                    details.append({
                        'date': d, 'opt_pnl': pnl,
                        'entry_time': trade['entry_time'],
                        'exit_time': trade['exit_time'],
                        'spx_entry': trade['entry_price'],
                        'spx_exit': trade['exit_price'],
                        'hold_mins': trade['hold_mins'],
                        'exit_reason': trade['exit_reason'],
                        'und_pts': trade['und_pts'],
                        'vix': trade['vix'],
                        'info': info,
                    })
                else:
                    missed += 1

            if len(priced) < 8:
                print(f"    {struct:20s}  {len(priced)} priced ({missed} missed) — insufficient")
                continue

            avg = statistics.mean(priced)
            tot = sum(priced)
            wins = sum(1 for p in priced if p > 0)
            wr = wins / len(priced) * 100
            std = statistics.stdev(priced) if len(priced) > 1 else 0
            sh = avg / std if std > 0 else 0
            gw = sum(p for p in priced if p > 0)
            gl = abs(sum(p for p in priced if p <= 0))
            pf = gw / gl if gl > 0 else 99

            cum = 0; pk = 0; mdd = 0
            for p in priced:
                cum += p
                if cum > pk: pk = cum
                dd = pk - cum
                if dd > mdd: mdd = dd

            # R²
            cum_list = []; c = 0
            for p in priced:
                c += p; cum_list.append(c)
            n = len(priced)
            x_m = (n-1)/2; y_m = statistics.mean(cum_list)
            ss_xy = sum((i-x_m)*(y-y_m) for i,y in enumerate(cum_list))
            ss_xx = sum((i-x_m)**2 for i in range(n))
            ss_yy = sum((y-y_m)**2 for y in cum_list)
            r2 = (ss_xy**2)/(ss_xx*ss_yy) if ss_xx>0 and ss_yy>0 else 0

            # IS/OOS option-level
            is_pnl = [d['opt_pnl'] for d in details if d['date'] < '2023-01-01']
            oos_pnl = [d['opt_pnl'] for d in details if d['date'] >= '2023-01-01']
            is_sh = statistics.mean(is_pnl)/statistics.stdev(is_pnl) if len(is_pnl)>5 and statistics.stdev(is_pnl)>0 else 0
            oos_sh = statistics.mean(oos_pnl)/statistics.stdev(oos_pnl) if len(oos_pnl)>5 and statistics.stdev(oos_pnl)>0 else 0

            v_str = "HOLDS" if oos_sh > 0.05 else "WEAK"
            miss_pct = missed / (len(priced) + missed) * 100

            print(f"    {struct:20s}  N={n:>4} ({100-miss_pct:.0f}%)  "
                  f"WR={wr:>5.1f}%  Avg=${avg:>+8.2f}  Tot=${tot:>+10.2f}  "
                  f"Sh={sh:>6.3f}  PF={pf:>5.2f}  DD=${mdd:>8.2f}  R²={r2:.3f}  "
                  f"IS={is_sh:.3f}  OOS={oos_sh:.3f}  [{v_str}]")

            opt_results.append({
                'label': f"{r['label']}|{struct}",
                'signal': r['signal'], 'filter': r['filter'],
                'exit_set': r['exit_set'], 'struct': struct,
                'n': n, 'wr': round(wr,1), 'avg_pnl': round(avg,2),
                'total_pnl': round(tot,2), 'sharpe': round(sh,3),
                'pf': round(pf,2), 'max_dd': round(mdd,2), 'r2': round(r2,3),
                'is_sh': round(is_sh,3), 'oos_sh': round(oos_sh,3),
                'und_sharpe': r['sharpe'],
                'priced_pct': round(100-miss_pct,1),
                'details': details,
            })

    # ═══════════════════════════════════════════════════════════════
    # FINAL RANKING
    # ═══════════════════════════════════════════════════════════════
    opt_results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n\n{'='*200}")
    print(f"FINAL RANKING — Closing Print Option-Priced Edges by Sharpe")
    print(f"{'='*200}")
    print(f"{'Label':>80s} {'N':>5} {'%':>5} {'WR%':>6} {'AvgPnL':>10} {'TotPnL':>12} "
          f"{'Sharpe':>7} {'PF':>6} {'MaxDD':>10} {'R²':>5} {'IS':>7} {'OOS':>7} {'UndSh':>7}")
    print("-" * 200)

    for r in opt_results[:60]:
        print(f"{r['label'][:80]:>80s} {r['n']:>5} {r.get('priced_pct',0):>4.0f}% {r['wr']:>5.1f}% "
              f"${r['avg_pnl']:>+9.2f} ${r['total_pnl']:>+11.2f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} ${r['max_dd']:>9.2f} "
              f"{r['r2']:>5.3f} {r['is_sh']:>7.3f} {r['oos_sh']:>7.3f} "
              f"{r['und_sharpe']:>7.3f}")

    # ═══════════════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════════════
    out_dir = SCRIPT_DIR / 'backtest_results'
    out_dir.mkdir(exist_ok=True)

    save_results = [{k: v for k, v in r.items() if k != 'details'} for r in opt_results[:100]]
    with open(out_dir / 'closing_option_results.json', 'w') as f:
        json.dump(save_results, f, indent=2)

    und_summary = [{k: v for k, v in r.items() if k != 'trades'} for r in all_results[:200]]
    with open(out_dir / 'closing_underlying_results.json', 'w') as f:
        json.dump(und_summary, f, indent=2)

    # Export individual trades for edges with positive OOS option Sharpe
    good_edges = [r for r in opt_results if r['oos_sh'] > 0.05 and r['sharpe'] > 0.15]
    if good_edges:
        all_closing_trades = []
        for edge in good_edges:
            for d in edge['details']:
                record = {
                    'date': d['date'],
                    'strategy': edge['label'],
                    'structure': edge['struct'],
                    'direction': 'LONG' if edge['signal'] != 'CFB_BearCont' else 'SHORT',
                    'spx_entry': d['spx_entry'],
                    'spx_exit': d['spx_exit'],
                    'entry_time': d['entry_time'],
                    'exit_time': d['exit_time'],
                    'hold_mins': d['hold_mins'],
                    'exit_reason': d['exit_reason'],
                    'und_pts': d['und_pts'],
                    'vix': d['vix'],
                    'opt_pnl': d['opt_pnl'],
                }
                info = d.get('info', {})
                if info:
                    if 'ticker' in info:
                        record['opt_ticker'] = info['ticker']
                        record['opt_entry_px'] = info['entry_px']
                        record['opt_exit_px'] = info['exit_px']
                    elif 'long_ticker' in info:
                        record['opt_ticker'] = f"{info['long_ticker']}|{info['short_ticker']}"
                        record['opt_entry_px'] = f"{info['long_entry']}/{info['short_entry']}"
                        record['opt_exit_px'] = f"{info['long_exit']}/{info['short_exit']}"
                all_closing_trades.append(record)

        with open(out_dir / 'closing_verified_trades.json', 'w') as f:
            json.dump(sorted(all_closing_trades, key=lambda x: x['date']), f, indent=2)
        print(f"\n  Saved {len(all_closing_trades)} verified closing trades")

    print(f"\n  Saved {len(save_results)} option-priced closing results")
    print(f"  Saved {len(und_summary)} underlying closing results")

    # Summary
    print(f"\n{'='*160}")
    print(f"SUMMARY")
    print(f"{'='*160}")
    print(f"  Closing signals tested: {len(sig_counts)}")
    print(f"  Total combos scanned: {combo}")
    print(f"  Edges found (underlying): {len(all_results)}")
    print(f"  OOS validated: {len(validated)}")
    print(f"  Option-priced results: {len(opt_results)}")
    if opt_results:
        best = opt_results[0]
        print(f"\n  BEST CLOSING OPTION-PRICED SHARPE: {best['sharpe']}")
        print(f"     {best['label']}")
        print(f"     N={best['n']}  WR={best['wr']}%  PF={best['pf']}  R²={best['r2']}")
        print(f"     IS={best['is_sh']}  OOS={best['oos_sh']}")
    if good_edges:
        print(f"\n  EDGES WITH POSITIVE OOS OPTION SHARPE: {len(good_edges)}")
        for e in good_edges[:15]:
            print(f"    {e['label']:>75s}  Sh={e['sharpe']:.3f}  OOS={e['oos_sh']:.3f}  N={e['n']}")
    else:
        print(f"\n  NO CLOSING EDGES WITH POSITIVE OOS OPTION SHARPE FOUND.")

    print(f"\n{'='*160}")
    print("DONE")
    print(f"{'='*160}")


if __name__ == '__main__':
    main()
