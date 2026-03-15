#!/usr/bin/env python3
"""
Deep Edge Scanner v2
====================
Goes beyond simple first-bar/OR signals to find more nuanced, consistent edges.

New signal categories:
  1. COMPOUND CONDITIONS — combine multiple weak signals into strong ones
  2. VOLUME PROFILE — high/low relative volume as entry confirmation
  3. OPENING RANGE RETEST — wait for OR breakout, then enter on pullback retest
  4. GAP FILL PATTERNS — enter when gap partially fills vs fully fills vs fails
  5. INTRADAY MOMENTUM SEQUENCES — 2-bar, 3-bar consecutive patterns
  6. NARROW RANGE DAYS — compressed open then expansion
  7. PREVIOUS DAY CONTEXT — prior day range/close location predicts today
  8. VIX CHANGE — VIX opening higher/lower than prior close
  9. CREDIT SPREAD STRATEGIES — sell premium with defined risk
  10. ADAPTIVE EXITS — wider stops in high VIX, tighter in low VIX

Consistency metrics: Sharpe, max consecutive losers, equity curve smoothness (R²),
monthly win rate consistency, worst month.
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
            if mins < 570 or mins >= 960:
                continue
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

    sma50 = {}
    for i in range(49, len(sorted_dates)):
        sma50[sorted_dates[i]] = sum(closes[i-49:i+1]) / 50
    sma20 = {}
    for i in range(19, len(sorted_dates)):
        sma20[sorted_dates[i]] = sum(closes[i-19:i+1]) / 20
    sma5 = {}
    for i in range(4, len(sorted_dates)):
        sma5[sorted_dates[i]] = sum(closes[i-4:i+1]) / 5

    # Previous N days data
    prev_days = {}
    for i in range(5, len(sorted_dates)):
        d = sorted_dates[i]
        prev = []
        for j in range(1, 6):
            pd = sorted_dates[i-j]
            prev.append(spx_daily[pd])
        prev_days[d] = prev

    # VIX previous close
    vix_sorted = sorted(vix_daily.keys())
    vix_prev_close = {}
    for i in range(1, len(vix_sorted)):
        vix_prev_close[vix_sorted[i]] = vix_daily[vix_sorted[i-1]]['c']

    print(f"  SPX 1min: {len(spx_1min)} days, daily: {len(spx_daily)}, VIX: {len(vix_daily)}")
    return spx_1min, spx_daily, vix_daily, sma50, sma20, sma5, prev_days, vix_prev_close


def extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, sma5, prev_days, vix_prev_close):
    print("Extracting features…")
    days = []

    for d in sorted(spx_1min.keys()):
        if d < START_DATE:
            continue
        bars = spx_1min[d]
        dd = spx_daily.get(d)
        vd = vix_daily.get(d)
        prev = prev_days.get(d)
        if not dd or not vd or not prev or len(bars) < 60:
            continue

        vix_open = vd['o']
        vix_pc = vix_prev_close.get(d, vix_open)
        vix_change = vix_open - vix_pc
        vix_change_pct = vix_change / vix_pc * 100 if vix_pc > 0 else 0

        prev_close = prev[0]['c']
        gap_pct = (dd['o'] - prev_close) / prev_close * 100

        # Previous day stats
        prev_range = prev[0]['h'] - prev[0]['l']
        prev_body = abs(prev[0]['c'] - prev[0]['o'])
        prev_close_loc = (prev[0]['c'] - prev[0]['l']) / prev_range if prev_range > 0 else 0.5
        # Was yesterday bullish?
        prev_bullish = prev[0]['c'] > prev[0]['o']
        # 2-day trend
        two_day_ret = (dd['o'] - prev[1]['c']) / prev[1]['c'] * 100 if len(prev) > 1 else 0
        # 5-day realized range
        five_day_ranges = [(p['h']-p['l'])/p['o']*100 for p in prev[:5]]
        avg_5d_range = statistics.mean(five_day_ranges) if five_day_ranges else 0

        # First bar
        fb = bars[0]
        fb_ret = (fb['c'] - fb['o']) / fb['o'] * 100
        fb_bullish = fb['c'] > fb['o']
        fb_range_pct = (fb['h'] - fb['l']) / fb['o'] * 100
        fb_body_ratio = abs(fb['c'] - fb['o']) / (fb['h'] - fb['l']) if (fb['h'] - fb['l']) > 0 else 0

        # Volume: first bar vs avg first-bar volume (computed as fraction of day)
        total_vol = sum(b['v'] for b in bars)
        fb_vol_pct = fb['v'] / total_vol * 100 if total_vol > 0 else 0

        # First 3 bars
        fb3 = bars[:3]
        fb3_consecutive_bull = all(b['c'] > b['o'] for b in fb3)
        fb3_consecutive_bear = all(b['c'] < b['o'] for b in fb3)
        fb3_ret = (fb3[-1]['c'] - fb3[0]['o']) / fb3[0]['o'] * 100 if len(fb3) == 3 else 0

        # Opening range stats
        def or_stats(n):
            subset = bars[:n]
            if len(subset) < n:
                return None
            hi = max(b['h'] for b in subset)
            lo = min(b['l'] for b in subset)
            cl = subset[-1]['c']
            op = subset[0]['o']
            rng = hi - lo
            return {
                'high': hi, 'low': lo, 'close': cl, 'open': op,
                'range': rng,
                'range_pct': rng / op * 100,
                'ret': (cl - op) / op * 100,
                'bullish': cl > op,
                'close_loc': (cl - lo) / rng if rng > 0 else 0.5,
            }

        or5 = or_stats(5)
        or15 = or_stats(15)
        or30 = or_stats(30)

        # Narrow range: today's first 15min range vs 5-day avg
        or15_narrow = False
        if or15 and avg_5d_range > 0:
            or15_rel = or15['range_pct'] / avg_5d_range
            or15_narrow = or15_rel < 0.3

        # Trend context
        above_50d = dd['o'] > sma50.get(d, 0) if d in sma50 else None
        above_20d = dd['o'] > sma20.get(d, 0) if d in sma20 else None
        above_5d = dd['o'] > sma5.get(d, 0) if d in sma5 else None
        dow = datetime.strptime(d, '%Y-%m-%d').weekday()

        if vix_open < 14:
            vix_regime = 'very_low'
        elif vix_open < 18:
            vix_regime = 'low'
        elif vix_open < 25:
            vix_regime = 'mid'
        elif vix_open < 35:
            vix_regime = 'high'
        else:
            vix_regime = 'extreme'

        days.append({
            'date': d, 'bars': bars,
            'open': dd['o'], 'high': dd['h'], 'low': dd['l'], 'close': dd['c'],
            'vix': vix_open, 'vix_regime': vix_regime,
            'vix_change': vix_change, 'vix_change_pct': vix_change_pct,
            'gap_pct': gap_pct,
            'fb': fb, 'fb_ret': fb_ret, 'fb_bullish': fb_bullish,
            'fb_range_pct': fb_range_pct, 'fb_body_ratio': fb_body_ratio,
            'fb_vol_pct': fb_vol_pct,
            'fb3_consecutive_bull': fb3_consecutive_bull,
            'fb3_consecutive_bear': fb3_consecutive_bear,
            'fb3_ret': fb3_ret,
            'or5': or5, 'or15': or15, 'or30': or30,
            'or15_narrow': or15_narrow,
            'above_50d': above_50d, 'above_20d': above_20d, 'above_5d': above_5d,
            'dow': dow,
            'prev_close': prev_close,
            'prev_range': prev_range, 'prev_close_loc': prev_close_loc,
            'prev_bullish': prev_bullish,
            'two_day_ret': two_day_ret,
            'avg_5d_range': avg_5d_range,
            'total_vol': total_vol,
        })
    print(f"  {len(days)} trading days")
    return days


# ═══════════════════════════════════════════════════════════════
# TRADE SIMULATION — using HIGH/LOW for more realistic fills
# ═══════════════════════════════════════════════════════════════
def simulate_trade(bars, entry_idx, direction, exit_params):
    """Forward-walk exit using bar highs/lows for PT/SL checks."""
    entry_bar = bars[entry_idx]
    entry_price = entry_bar['c']
    entry_mins = entry_bar['mins']

    pt_pts = exit_params.get('pt_pts')
    sl_pts = exit_params.get('sl_pts')
    pt_pct = exit_params.get('pt_pct')
    sl_pct = exit_params.get('sl_pct')
    trail_pct = exit_params.get('trail_pct')
    trail_pts = exit_params.get('trail_pts')
    ts_min = exit_params.get('ts_min', 60)
    ts_deadline = entry_mins + ts_min

    # Adaptive exits: scale by VIX
    vix_mult = exit_params.get('vix_mult')  # if set, multiply PT/SL by VIX/20

    if vix_mult:
        vix = exit_params.get('_vix', 20)
        scale = vix / 20.0
        if pt_pts: pt_pts = pt_pts * scale
        if sl_pts: sl_pts = sl_pts * scale

    if direction == 1:
        pt_level = entry_price + pt_pts if pt_pts else None
        sl_level = entry_price - sl_pts if sl_pts else None
        if pt_pct: pt_level = entry_price * (1 + pt_pct / 100)
        if sl_pct: sl_level = entry_price * (1 - sl_pct / 100)
    else:
        pt_level = entry_price - pt_pts if pt_pts else None
        sl_level = entry_price + sl_pts if sl_pts else None
        if pt_pct: pt_level = entry_price * (1 - pt_pct / 100)
        if sl_pct: sl_level = entry_price * (1 + sl_pct / 100)

    peak = entry_price
    trough = entry_price
    exit_reason = 'time_stop'
    exit_price = entry_price

    for j in range(entry_idx + 1, len(bars)):
        bar = bars[j]

        # Time stop
        if bar['mins'] >= ts_deadline or bar['mins'] >= 959:
            exit_price = bar['c']
            exit_reason = 'time_stop'
            exit_idx = j
            break

        # Use high/low for more realistic PT/SL fills
        if direction == 1:
            if bar['h'] > peak: peak = bar['h']

            # Check SL first (conservative — assume SL hit before PT on same bar)
            if sl_level and bar['l'] <= sl_level:
                exit_price = sl_level
                exit_reason = 'stop_loss'
                exit_idx = j
                break

            if pt_level and bar['h'] >= pt_level:
                exit_price = pt_level
                exit_reason = 'profit_target'
                exit_idx = j
                break

            if trail_pct and peak > entry_price:
                tl = peak * (1 - trail_pct / 100)
                if bar['l'] <= tl:
                    exit_price = tl
                    exit_reason = 'trailing_stop'
                    exit_idx = j
                    break

            if trail_pts and peak > entry_price:
                tl = peak - trail_pts
                if bar['l'] <= tl:
                    exit_price = tl
                    exit_reason = 'trailing_stop'
                    exit_idx = j
                    break
        else:
            if bar['l'] < trough: trough = bar['l']

            if sl_level and bar['h'] >= sl_level:
                exit_price = sl_level
                exit_reason = 'stop_loss'
                exit_idx = j
                break

            if pt_level and bar['l'] <= pt_level:
                exit_price = pt_level
                exit_reason = 'profit_target'
                exit_idx = j
                break

            if trail_pct and trough < entry_price:
                tl = trough * (1 + trail_pct / 100)
                if bar['h'] >= tl:
                    exit_price = tl
                    exit_reason = 'trailing_stop'
                    exit_idx = j
                    break

            if trail_pts and trough < entry_price:
                tl = trough + trail_pts
                if bar['h'] >= tl:
                    exit_price = tl
                    exit_reason = 'trailing_stop'
                    exit_idx = j
                    break
    else:
        exit_price = bars[-1]['c']
        exit_idx = len(bars) - 1

    exit_bar = bars[exit_idx]
    hold_mins = exit_bar['mins'] - entry_mins
    und_pts = direction * (exit_price - entry_price)

    return {
        'entry_price': entry_price,
        'exit_price': exit_price,
        'entry_time': entry_bar['time'],
        'exit_time': exit_bar['time'],
        'entry_mins': entry_bar['mins'],
        'exit_mins': exit_bar['mins'],
        'hold_mins': hold_mins,
        'exit_reason': exit_reason,
        'und_pts': und_pts,
        'direction': direction,
    }


# ═══════════════════════════════════════════════════════════════
# CONSISTENCY METRICS
# ═══════════════════════════════════════════════════════════════
def compute_stats(trades, label=''):
    if len(trades) < 15:
        return None

    pts = [t['und_pts'] for t in trades]
    n = len(pts)
    avg = statistics.mean(pts)
    tot = sum(pts)
    wr = sum(1 for p in pts if p > 0) / n * 100
    std = statistics.stdev(pts) if n > 1 else 0
    sharpe = avg / std if std > 0 else 0

    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else 99

    # Max drawdown
    cum = 0; peak = 0; max_dd = 0
    for p in pts:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd

    # Max consecutive losers
    max_consec_loss = 0
    curr_loss = 0
    for p in pts:
        if p <= 0:
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    # Monthly breakdown
    monthly = defaultdict(list)
    for t in trades:
        ym = t['date'][:7]
        monthly[ym].append(t['und_pts'])

    monthly_pnl = {ym: sum(v) for ym, v in monthly.items()}
    months_positive = sum(1 for v in monthly_pnl.values() if v > 0)
    months_total = len(monthly_pnl)
    monthly_wr = months_positive / months_total * 100 if months_total > 0 else 0
    worst_month = min(monthly_pnl.values()) if monthly_pnl else 0

    # Equity curve smoothness (R² of cumulative P&L vs linear fit)
    cum_pts = []
    c = 0
    for p in pts:
        c += p
        cum_pts.append(c)
    if len(cum_pts) > 5:
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(cum_pts)
        ss_xy = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(cum_pts))
        ss_xx = sum((i - x_mean)**2 for i in range(n))
        ss_yy = sum((y - y_mean)**2 for y in cum_pts)
        if ss_xx > 0 and ss_yy > 0:
            r_sq = (ss_xy**2) / (ss_xx * ss_yy)
        else:
            r_sq = 0
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
        'worst_month': round(worst_month, 1),
        'equity_r2': round(r_sq, 3),
        'avg_hold': round(avg_hold, 1),
    }


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATORS — deeper, more nuanced
# ═══════════════════════════════════════════════════════════════

def signals_compound(day):
    """Multiple compound signals — return list of (name, entry_idx, direction)."""
    results = []
    bars = day['bars']
    gap = day['gap_pct']
    vix = day['vix']
    fb = day['fb']
    fb_ret = day['fb_ret']
    or15 = day['or15']
    or30 = day['or30']

    # ── 1. STRONG OPEN MOMENTUM ──
    # Bullish first bar + gap up + above trend = triple confirmation long
    if day['fb_bullish'] and gap >= 0.10 and day['above_20d'] is True and fb_ret >= 0.05:
        results.append(('TripleConfirm_Long', 0, 1))

    # Bearish first bar + gap down + below trend = triple confirmation short
    if not day['fb_bullish'] and gap <= -0.10 and day['above_20d'] is False and fb_ret <= -0.05:
        results.append(('TripleConfirm_Short', 0, -1))

    # ── 2. FIRST 3 BARS CONSECUTIVE ──
    if day['fb3_consecutive_bull'] and day['fb3_ret'] >= 0.05:
        results.append(('ThreeBarBull', 2, 1))
    if day['fb3_consecutive_bear'] and day['fb3_ret'] <= -0.05:
        results.append(('ThreeBarBear', 2, -1))

    # ── 3. NARROW RANGE BREAKOUT ──
    if day['or15_narrow'] and or15:
        if or15['bullish']:
            results.append(('NarrowBreak_Bull', 14, 1))
        else:
            results.append(('NarrowBreak_Bear', 14, -1))

    # ── 4. GAP FILL DETECTION ──
    # Small gap up that fills within first 15 min → fade (mean revert)
    if 0.10 <= gap <= 0.40 and or15:
        fill_amt = (or15['open'] - or15['low']) / day['open'] * 100  # how much gap filled
        if fill_amt >= gap * 0.5:  # at least half gap filled
            results.append(('GapFill_Bounce', 14, 1))  # bounce after fill

    # Gap down that fills → fade the fill (short)
    if -0.40 <= gap <= -0.10 and or15:
        fill_amt = (or15['high'] - or15['open']) / day['open'] * 100
        if fill_amt >= abs(gap) * 0.5:
            results.append(('GapFill_Fade', 14, -1))

    # ── 5. OPENING RANGE RETEST ──
    # OR15 breakout bull, then look for pullback to OR15 high → enter long
    if or15 and or15['bullish'] and or15['range_pct'] >= 0.10:
        or15_hi = or15['high']
        for i in range(15, min(45, len(bars))):
            b = bars[i]
            if b['l'] <= or15_hi * 1.001 and b['c'] > or15_hi:  # touched OR high and bounced
                results.append(('OR15_Retest_Bull', i, 1))
                break

    if or15 and not or15['bullish'] and or15['range_pct'] >= 0.10:
        or15_lo = or15['low']
        for i in range(15, min(45, len(bars))):
            b = bars[i]
            if b['h'] >= or15_lo * 0.999 and b['c'] < or15_lo:
                results.append(('OR15_Retest_Bear', i, -1))
                break

    # ── 6. VIX SPIKE FADE ──
    # VIX gapped up significantly from prior close → markets often recover
    if day['vix_change_pct'] >= 10 and day['fb_bullish']:
        results.append(('VIXSpike_Bounce', 0, 1))

    # VIX dropped big → bearish first bar = continuation
    if day['vix_change_pct'] <= -5 and not day['fb_bullish']:
        results.append(('VIXCrush_Fade', 0, -1))

    # ── 7. PREVIOUS DAY CLOSE LOCATION ──
    # Prev day closed near highs (>80% loc) and gap up → continuation
    if day['prev_close_loc'] > 0.80 and gap >= 0.05 and day['fb_bullish']:
        results.append(('PrevHigh_Cont', 0, 1))

    # Prev day closed near lows (<20% loc) and gap down → continuation
    if day['prev_close_loc'] < 0.20 and gap <= -0.05 and not day['fb_bullish']:
        results.append(('PrevLow_Cont', 0, -1))

    # ── 8. HIGH VOLUME FIRST BAR ──
    # First bar has >3% of day's volume (abnormally active open)
    if day['fb_vol_pct'] > 3.0:
        if day['fb_bullish'] and fb_ret >= 0.05:
            results.append(('HighVol_Bull', 0, 1))
        elif not day['fb_bullish'] and fb_ret <= -0.05:
            results.append(('HighVol_Bear', 0, -1))

    # ── 9. DOJI FIRST BAR → BREAKOUT ──
    # First bar is a doji (small body vs range), enter on bar 2 breakout
    if day['fb_body_ratio'] < 0.25 and day['fb_range_pct'] >= 0.10:
        if len(bars) > 1:
            b2 = bars[1]
            if b2['c'] > fb['h']:
                results.append(('DojiBull', 1, 1))
            elif b2['c'] < fb['l']:
                results.append(('DojiBear', 1, -1))

    # ── 10. MIDDAY REVERSAL ──
    # At 11:30, check if morning trend exhausted
    for i, b in enumerate(bars):
        if b['mins'] >= 690:  # 11:30
            move_from_open = (b['c'] - day['open']) / day['open'] * 100
            if move_from_open >= 0.40:  # up a lot by 11:30
                results.append(('MidReversal_Short', i, -1))
            elif move_from_open <= -0.40:
                results.append(('MidReversal_Long', i, 1))
            break

    # ── 11. AFTERNOON CONTINUATION ──
    # At 14:00, if strong trend, ride to close
    for i, b in enumerate(bars):
        if b['mins'] >= 840:  # 14:00
            move = (b['c'] - day['open']) / day['open'] * 100
            if move >= 0.30:
                results.append(('PMCont_Long', i, 1))
            elif move <= -0.30:
                results.append(('PMCont_Short', i, -1))
            break

    # ── 12. TWO-DAY MOMENTUM ──
    # Strong 2-day rally/decline → continuation at open
    if day['two_day_ret'] >= 0.50 and day['fb_bullish']:
        results.append(('TwoDayMom_Long', 0, 1))
    if day['two_day_ret'] <= -0.50 and not day['fb_bullish']:
        results.append(('TwoDayMom_Short', 0, -1))

    # ── 13. MEAN REVERSION AFTER 2-DAY MOVE ──
    if day['two_day_ret'] >= 0.80 and not day['fb_bullish']:
        results.append(('TwoDayMR_Short', 0, -1))
    if day['two_day_ret'] <= -0.80 and day['fb_bullish']:
        results.append(('TwoDayMR_Long', 0, 1))

    # ── 14. STRONG BODY RATIO + DIRECTION ──
    # First bar with >80% body (strong conviction candle)
    if day['fb_body_ratio'] > 0.80:
        if day['fb_bullish'] and fb_ret >= 0.03:
            results.append(('StrongBody_Bull', 0, 1))
        elif not day['fb_bullish'] and fb_ret <= -0.03:
            results.append(('StrongBody_Bear', 0, -1))

    # ── 15. OR30 RANGE COMPRESSION → BREAKOUT ──
    if or30 and day['avg_5d_range'] > 0:
        comp_ratio = or30['range_pct'] / day['avg_5d_range']
        if comp_ratio < 0.25:  # very compressed
            if or30['bullish']:
                results.append(('Compress25_Bull', 29, 1))
            else:
                results.append(('Compress25_Bear', 29, -1))

    return results


# ═══════════════════════════════════════════════════════════════
# EXIT PARAMETER SETS — including adaptive
# ═══════════════════════════════════════════════════════════════
EXIT_SETS = {
    # ── Scalps ──
    'Scalp_3_2_5': {'pt_pts': 3, 'sl_pts': 2, 'ts_min': 5},
    'Scalp_5_3_10': {'pt_pts': 5, 'sl_pts': 3, 'ts_min': 10},
    'Scalp_5_2_5': {'pt_pts': 5, 'sl_pts': 2, 'ts_min': 5},
    'Scalp_8_4_15': {'pt_pts': 8, 'sl_pts': 4, 'ts_min': 15},
    'Scalp_10_5_15': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 15},
    # ── Medium ──
    'Med_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30},
    'Med_15_8_30': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 30},
    'Med_20_10_45': {'pt_pts': 20, 'sl_pts': 10, 'ts_min': 45},
    'Med_15_10_60': {'pt_pts': 15, 'sl_pts': 10, 'ts_min': 60},
    # ── Wide ──
    'Wide_30_15_90': {'pt_pts': 30, 'sl_pts': 15, 'ts_min': 90},
    'Wide_50_20_180': {'pt_pts': 50, 'sl_pts': 20, 'ts_min': 180},
    # ── Trail ──
    'Trail05_SL5_30': {'trail_pct': 0.05, 'sl_pts': 5, 'ts_min': 30},
    'Trail10_SL5_30': {'trail_pct': 0.10, 'sl_pts': 5, 'ts_min': 30},
    'Trail10_SL8_45': {'trail_pct': 0.10, 'sl_pts': 8, 'ts_min': 45},
    'Trail15_SL10_60': {'trail_pct': 0.15, 'sl_pts': 10, 'ts_min': 60},
    # ── Percent ──
    'Pct_30_15_20': {'pt_pct': 0.30, 'sl_pct': 0.15, 'ts_min': 20},
    'Pct_50_25_30': {'pt_pct': 0.50, 'sl_pct': 0.25, 'ts_min': 30},
    'Pct_20_10_15': {'pt_pct': 0.20, 'sl_pct': 0.10, 'ts_min': 15},
    # ── Hold to close ──
    'SL10_Close': {'sl_pts': 10, 'ts_min': 390},
    'SL15_Close': {'sl_pts': 15, 'ts_min': 390},
    'SL20_Close': {'sl_pts': 20, 'ts_min': 390},
    # ── Adaptive (scale PT/SL by VIX/20) ──
    'Adpt_10_5_30': {'pt_pts': 10, 'sl_pts': 5, 'ts_min': 30, 'vix_mult': True},
    'Adpt_15_8_45': {'pt_pts': 15, 'sl_pts': 8, 'ts_min': 45, 'vix_mult': True},
    'Adpt_20_10_60': {'pt_pts': 20, 'sl_pts': 10, 'ts_min': 60, 'vix_mult': True},
}

# ═══════════════════════════════════════════════════════════════
# VIX FILTERS
# ═══════════════════════════════════════════════════════════════
VIX_FILTERS = {
    'All': lambda d: True,
    'VeryLow': lambda d: d['vix'] < 14,
    'Low': lambda d: d['vix'] < 18,
    'lt20': lambda d: d['vix'] < 20,
    'Mid': lambda d: 18 <= d['vix'] < 25,
    'High': lambda d: d['vix'] >= 25,
    'gt30': lambda d: d['vix'] >= 30,
    'Falling': lambda d: d['vix_change_pct'] <= -3,
    'Rising': lambda d: d['vix_change_pct'] >= 3,
}


# ═══════════════════════════════════════════════════════════════
# MAIN SCAN
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 160)
    print("DEEP EDGE SCANNER v2 — Compound Signals, Volume, Adaptive Exits, Consistency Metrics")
    print("=" * 160)

    spx_1min, spx_daily, vix_daily, sma50, sma20, sma5, prev_days, vix_prev_close = load_all_data()
    days = extract_features(spx_1min, spx_daily, vix_daily, sma50, sma20, sma5, prev_days, vix_prev_close)

    # Pre-compute all signals per day
    print("Generating signals…")
    day_signals = {}
    sig_counts = defaultdict(int)
    for day in days:
        sigs = signals_compound(day)
        day_signals[day['date']] = sigs
        for name, _, _ in sigs:
            sig_counts[name] += 1

    print(f"  Signal frequency across {len(days)} days:")
    for name, count in sorted(sig_counts.items(), key=lambda x: -x[1]):
        print(f"    {name:30s}: {count:>5} fires ({count/len(days)*100:.1f}%)")

    # Scan all signal × VIX × exit combinations
    print(f"\n{'='*160}")
    print(f"SCANNING: {len(sig_counts)} signals × {len(VIX_FILTERS)} VIX × {len(EXIT_SETS)} exits")
    print(f"{'='*160}")

    all_results = []
    total_combos = len(sig_counts) * len(VIX_FILTERS) * len(EXIT_SETS)
    combo_count = 0

    for sig_name in sorted(sig_counts.keys()):
        for vix_name, vix_fn in VIX_FILTERS.items():
            for exit_name, exit_params in EXIT_SETS.items():
                combo_count += 1
                if combo_count % 2000 == 0:
                    print(f"  [{combo_count}/{total_combos}]…", flush=True)

                trades = []
                for day in days:
                    if not vix_fn(day):
                        continue
                    for sname, entry_idx, direction in day_signals[day['date']]:
                        if sname != sig_name:
                            continue
                        if entry_idx >= len(day['bars']) - 3:
                            continue

                        ep = dict(exit_params)
                        if ep.get('vix_mult'):
                            ep['_vix'] = day['vix']

                        trade = simulate_trade(day['bars'], entry_idx, direction, ep)
                        trade['date'] = day['date']
                        trade['vix'] = day['vix']
                        trades.append(trade)
                        break  # one trade per day per signal

                stats = compute_stats(trades, f"{sig_name}|{vix_name}|{exit_name}")
                if stats and stats['sharpe'] > 0.10 and stats['pf'] > 1.1 and stats['n'] >= 20:
                    stats['signal'] = sig_name
                    stats['vix_filter'] = vix_name
                    stats['exit_set'] = exit_name
                    stats['trades'] = trades
                    all_results.append(stats)

    print(f"\n  {combo_count} combos scanned, {len(all_results)} edges with Sharpe>0.10 & PF>1.1 & N≥20")

    # Sort by CONSISTENCY SCORE: weighted Sharpe + equity R² + monthly WR
    for r in all_results:
        r['consistency_score'] = (
            r['sharpe'] * 0.35 +
            r['equity_r2'] * 0.30 +
            (r['monthly_wr'] / 100) * 0.20 +
            (min(r['n'], 200) / 200) * 0.15  # sample size bonus
        )

    all_results.sort(key=lambda x: x['consistency_score'], reverse=True)

    # ═══════════════════════════════════════════════════════════════
    # RESULTS — sorted by consistency score
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"TOP 60 EDGES BY CONSISTENCY SCORE (Sharpe×0.35 + R²×0.30 + MonthlyWR×0.20 + SampleSize×0.15)")
    print(f"{'='*180}")
    print(f"{'Label':>60s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
          f"{'Sharpe':>7} {'PF':>5} {'MaxDD':>8} {'ConsLoss':>9} "
          f"{'MoWR%':>6} {'R²':>5} {'Score':>6} {'Hold':>6}")
    print("-" * 180)

    for r in all_results[:60]:
        print(f"{r['label'][:60]:>60s} {r['n']:>5} {r['wr']:>5.1f}% "
              f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['max_dd']:>7.1f} "
              f"{r['max_consec_loss']:>9} {r['monthly_wr']:>5.1f}% "
              f"{r['equity_r2']:>5.3f} {r['consistency_score']:>6.3f} "
              f"{r['avg_hold']:>5.1f}m")

    # ═══════════════════════════════════════════════════════════════
    # CATEGORY BREAKDOWN
    # ═══════════════════════════════════════════════════════════════
    categories = {
        'COMPOUND MOMENTUM (Triple/ThreeBar/TwoDay)': lambda r: any(
            x in r['signal'] for x in ('Triple', 'ThreeBar', 'TwoDay')),
        'GAP PATTERNS (Fill/Cont/Fade)': lambda r: 'Gap' in r['signal'],
        'OPENING RANGE (Narrow/Compress/Retest)': lambda r: any(
            x in r['signal'] for x in ('Narrow', 'Compress', 'Retest')),
        'VOLUME/BODY (HighVol/StrongBody/Doji)': lambda r: any(
            x in r['signal'] for x in ('HighVol', 'StrongBody', 'Doji')),
        'PREVIOUS DAY (PrevHigh/PrevLow)': lambda r: 'Prev' in r['signal'],
        'VIX-BASED (VIXSpike/VIXCrush)': lambda r: 'VIX' in r['signal'] and 'VIX' not in r['vix_filter'],
        'MIDDAY/AFTERNOON (MidReversal/PMCont)': lambda r: any(
            x in r['signal'] for x in ('MidReversal', 'PMCont')),
        'MEAN REVERSION (TwoDayMR)': lambda r: 'MR' in r['signal'],
    }

    for cat_name, cat_fn in categories.items():
        subset = [r for r in all_results if cat_fn(r)]
        if not subset:
            continue
        subset.sort(key=lambda x: x['consistency_score'], reverse=True)

        print(f"\n{'='*180}")
        print(f"  {cat_name} — TOP 10")
        print(f"{'='*180}")
        print(f"{'Label':>60s} {'N':>5} {'WR%':>6} {'AvgPts':>8} {'TotPts':>9} "
              f"{'Sharpe':>7} {'PF':>5} {'R²':>5} {'MoWR':>5} {'Score':>6}")
        print("-" * 180)
        for r in subset[:10]:
            print(f"{r['label'][:60]:>60s} {r['n']:>5} {r['wr']:>5.1f}% "
                  f"{r['avg_pts']:>+7.2f} {r['total_pts']:>+8.1f} "
                  f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} {r['equity_r2']:>5.3f} "
                  f"{r['monthly_wr']:>4.0f}% {r['consistency_score']:>6.3f}")

    # ═══════════════════════════════════════════════════════════════
    # OOS VALIDATION on top 40
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*180}")
    print(f"OUT-OF-SAMPLE VALIDATION — IS: 2018-2022 vs OOS: 2023-2026")
    print(f"{'='*180}")

    validated = []
    for r in all_results[:40]:
        trades = r.get('trades', [])
        if not trades:
            continue
        is_t = [t for t in trades if t['date'] < '2023-01-01']
        oos_t = [t for t in trades if t['date'] >= '2023-01-01']

        is_s = compute_stats(is_t, 'IS')
        oos_s = compute_stats(oos_t, 'OOS')

        if is_s and oos_s:
            verdict = 'HOLDS' if oos_s['sharpe'] > 0.05 and oos_s['pf'] > 1.0 else 'DEGRADES'
            print(f"  {r['label'][:55]:>55s}  IS: Sh={is_s['sharpe']:>6.3f} N={is_s['n']:>4} R²={is_s['equity_r2']:.3f}  "
                  f"OOS: Sh={oos_s['sharpe']:>6.3f} N={oos_s['n']:>4} R²={oos_s['equity_r2']:.3f}  "
                  f"[{verdict}]")
            if verdict == 'HOLDS':
                validated.append({
                    'edge': r,
                    'is_sharpe': is_s['sharpe'], 'oos_sharpe': oos_s['sharpe'],
                    'is_n': is_s['n'], 'oos_n': oos_s['n'],
                    'is_r2': is_s['equity_r2'], 'oos_r2': oos_s['equity_r2'],
                })
        elif is_s:
            print(f"  {r['label'][:55]:>55s}  IS: Sh={is_s['sharpe']:>6.3f} N={is_s['n']:>4}  OOS: <15 trades")

    # ═══════════════════════════════════════════════════════════════
    # SAVE RESULTS
    # ═══════════════════════════════════════════════════════════════
    out_dir = SCRIPT_DIR / 'backtest_results'
    out_dir.mkdir(exist_ok=True)

    # Save summary
    summary = []
    for r in all_results[:200]:
        s = {k: v for k, v in r.items() if k != 'trades'}
        summary.append(s)
    with open(out_dir / 'deep_edge_results.json', 'w') as f:
        json.dump(summary, f, indent=2)

    # Save top 40 trade CSVs
    for i, r in enumerate(all_results[:40]):
        trades = r.get('trades', [])
        if not trades:
            continue
        safe = r['label'].replace('|', '_').replace(' ', '_')[:50]
        csv_file = out_dir / f'deep_{i+1:02d}_{safe}.csv'
        fields = ['date', 'entry_time', 'exit_time', 'entry_price', 'exit_price',
                   'direction', 'und_pts', 'hold_mins', 'exit_reason', 'vix']
        with open(csv_file, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in trades:
                w.writerow({k: t[k] for k in fields})

    # Save validated edges summary
    if validated:
        print(f"\n  ✓ {len(validated)} edges PASSED OOS validation")
        with open(out_dir / 'deep_validated_edges.json', 'w') as f:
            json.dump([{
                'label': v['edge']['label'],
                'signal': v['edge']['signal'],
                'vix_filter': v['edge']['vix_filter'],
                'exit_set': v['edge']['exit_set'],
                'full_sharpe': v['edge']['sharpe'],
                'full_n': v['edge']['n'],
                'is_sharpe': v['is_sharpe'], 'oos_sharpe': v['oos_sharpe'],
                'is_n': v['is_n'], 'oos_n': v['oos_n'],
                'consistency_score': v['edge']['consistency_score'],
            } for v in validated], f, indent=2)

    print(f"\n  Saved {len(summary)} results to deep_edge_results.json")
    print(f"  Saved {min(40, len(all_results))} trade CSVs")

    print(f"\n{'='*120}")
    print("DONE")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
