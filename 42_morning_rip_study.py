#!/usr/bin/env python3
"""
Morning Rip Fade Study
======================
Mirror of the velocity dip-buy thesis but inverted:
  - Track rolling LOW from open
  - Measure rally% and velocity of the rally
  - When thresholds are met, simulate a FADE entry (buy puts / short direction)
  - Measure what happens next: does SPX pull back? How much?

Uses underlying SPX 1-min bars only (no options needed).
Sweeps across multiple parameter combos for statistical significance.
"""

import csv, json, os, sys
from collections import defaultdict
from datetime import datetime
import itertools

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_FILE = os.path.join(SCRIPT_DIR, 'spx_1min_bars.csv')
VIX_FILE = os.path.join(SCRIPT_DIR, 'vix_daily_bars.csv')
OUT_FILE = os.path.join(SCRIPT_DIR, 'morning_rip_study_results.json')

# ── Load data ──────────────────────────────────────────────────────────────

def load_spx():
    """Return dict: date_str -> list of (time_str, o, h, l, c, mins_from_open)"""
    days = defaultdict(list)
    with open(SPX_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960:  # RTH only
                continue
            days[d].append({
                'time': t,
                'mins': mins,
                'o': float(row['open']),
                'h': float(row['high']),
                'l': float(row['low']),
                'c': float(row['close']),
                'idx': mins - 570,  # 0-based bar index from open
            })
    # Sort each day by time
    for d in days:
        days[d].sort(key=lambda x: x['mins'])
    return days

def load_vix():
    """Return dict: date_str -> {open, high, low, close}"""
    vix = {}
    with open(VIX_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            vix[row['date']] = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            }
    return vix

# ── Strategy logic ─────────────────────────────────────────────────────────

def run_study(spx_days, vix_data, params):
    """
    For each day, track rolling low from open.
    When rally% >= min_rally and velocity >= min_vel, mark entry.
    Then measure pullback over next N bars.

    params dict:
      min_rally:   minimum rally % from rolling low (e.g. 0.30)
      min_vel:     minimum velocity %/min (e.g. 0.05)
      max_vel:     maximum velocity %/min (e.g. 999 = no cap)
      vix_min:     minimum VIX open (0 = no filter)
      vix_max:     maximum VIX open (99 = no filter)
      entry_window_start: earliest bar index for entry (0 = 9:30)
      entry_window_end:   latest bar index for entry (e.g. 60 = 10:30)
      hold_bars:   how many bars to hold after entry
      stop_pct:    stop loss on the underlying (adverse move kills trade)
    """
    min_rally = params['min_rally']
    min_vel = params['min_vel']
    max_vel = params.get('max_vel', 999)
    vix_min = params.get('vix_min', 0)
    vix_max = params.get('vix_max', 99)
    win_start = params.get('entry_window_start', 0)
    win_end = params.get('entry_window_end', 60)
    hold_bars = params.get('hold_bars', 30)
    stop_pct = params.get('stop_pct', 0.50)

    trades = []

    for date_str in sorted(spx_days.keys()):
        bars = spx_days[date_str]
        if len(bars) < 30:
            continue

        # VIX filter
        vd = vix_data.get(date_str)
        if vd:
            if vd['open'] < vix_min or vd['open'] > vix_max:
                continue
            vix_open = vd['open']
        else:
            if vix_min > 0:  # Can't apply VIX filter without data
                continue
            vix_open = None

        # Track rolling low from open
        rolling_low = bars[0]['l']
        trough_idx = 0
        entered = False

        for i, bar in enumerate(bars):
            if bar['l'] < rolling_low:
                rolling_low = bar['l']
                trough_idx = i

            # Rally from rolling low
            rally_pct = (bar['c'] - rolling_low) / rolling_low * 100
            elapsed = i - trough_idx
            vel = rally_pct / elapsed if elapsed > 0 else 0

            # Check entry conditions
            if (not entered
                and win_start <= bar['idx'] <= win_end
                and rally_pct >= min_rally
                and vel >= min_vel
                and vel <= max_vel):

                # ENTRY: fade the rip (bearish)
                entry_px = bar['c']
                entry_bar = i
                entry_time = bar['time']
                entered = True

                # Measure outcome over hold_bars
                max_favorable = 0  # max drop from entry (good for puts)
                max_adverse = 0    # max rally from entry (bad for puts)
                exit_px = entry_px
                exit_time = entry_time
                exit_reason = 'hold_expired'
                stopped = False

                for j in range(i + 1, min(i + 1 + hold_bars, len(bars))):
                    bj = bars[j]
                    # Favorable = price drops (we're short / long puts)
                    drop_from_entry = (entry_px - bj['l']) / entry_px * 100
                    rise_from_entry = (bj['h'] - entry_px) / entry_px * 100

                    if drop_from_entry > max_favorable:
                        max_favorable = drop_from_entry
                    if rise_from_entry > max_adverse:
                        max_adverse = rise_from_entry

                    # Stop loss: price rallies too far above entry
                    if rise_from_entry >= stop_pct:
                        exit_px = entry_px * (1 + stop_pct / 100)
                        exit_time = bj['time']
                        exit_reason = 'stopped'
                        stopped = True
                        break

                    exit_px = bj['c']
                    exit_time = bj['time']

                # P&L for a short/put position
                pnl_pct = (entry_px - exit_px) / entry_px * 100

                trades.append({
                    'date': date_str,
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_px': round(entry_px, 2),
                    'exit_px': round(exit_px, 2),
                    'rolling_low': round(rolling_low, 2),
                    'rally_at_entry': round(rally_pct, 4),
                    'vel_at_entry': round(vel, 4),
                    'vix_open': round(vix_open, 2) if vix_open else None,
                    'pnl_pct': round(pnl_pct, 4),
                    'max_favorable': round(max_favorable, 4),
                    'max_adverse': round(max_adverse, 4),
                    'exit_reason': exit_reason,
                    'elapsed_bars': elapsed,
                })
                break  # Only one trade per day

    return trades

def compute_stats(trades):
    if not trades:
        return None
    n = len(trades)
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    stopped = [t for t in trades if t['exit_reason'] == 'stopped']

    pnls = [t['pnl_pct'] for t in trades]
    avg_pnl = sum(pnls) / n
    wr = len(wins) / n * 100
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    avg_mfe = sum(t['max_favorable'] for t in trades) / n
    avg_mae = sum(t['max_adverse'] for t in trades) / n

    # Simple Sharpe-like: avg / stdev
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
        'avg_pnl': round(avg_pnl, 4),
        'avg_win': round(avg_win, 4),
        'avg_loss': round(avg_loss, 4),
        'avg_mfe': round(avg_mfe, 4),
        'avg_mae': round(avg_mae, 4),
        'stop_rate': round(len(stopped) / n * 100, 1),
        'sharpe': round(sharpe, 3),
        'total_pnl': round(sum(pnls), 2),
    }

# ── Parameter sweep ────────────────────────────────────────────────────────

def main():
    print("Loading SPX 1-min bars...")
    spx_days = load_spx()
    print(f"  {len(spx_days)} trading days")

    print("Loading VIX daily data...")
    vix_data = load_vix()
    print(f"  {len(vix_data)} VIX days")

    # Parameter grid — trimmed for speed
    min_rallies = [0.20, 0.30, 0.40, 0.50, 0.70, 1.00]
    min_vels = [0, 0.03, 0.05, 0.10, 0.15]
    vix_ranges = [
        (0, 99, 'all'),
        (0, 18, 'vix_low'),
        (18, 25, 'vix_mid'),
        (25, 99, 'vix_25+'),
    ]
    entry_windows = [
        (0, 30, 'first_30min'),    # 9:30-10:00
        (0, 60, 'first_hour'),     # 9:30-10:30
        (0, 120, 'first_2hr'),     # 9:30-11:30
    ]
    hold_bars_list = [15, 30, 60, 120]
    stop_pcts = [0.30, 0.50, 1.00]

    total_combos = (len(min_rallies) * len(min_vels) * len(vix_ranges)
                    * len(entry_windows) * len(hold_bars_list) * len(stop_pcts))
    print(f"\nSweeping {total_combos} parameter combinations...")

    all_results = []
    count = 0

    for mr in min_rallies:
        for mv in min_vels:
            for vix_lo, vix_hi, vix_label in vix_ranges:
                for ws, we, win_label in entry_windows:
                    for hb in hold_bars_list:
                        for sp in stop_pcts:
                            count += 1
                            if count % 5000 == 0:
                                print(f"  {count}/{total_combos}...")

                            params = {
                                'min_rally': mr,
                                'min_vel': mv,
                                'vix_min': vix_lo,
                                'vix_max': vix_hi,
                                'entry_window_start': ws,
                                'entry_window_end': we,
                                'hold_bars': hb,
                                'stop_pct': sp,
                            }

                            trades = run_study(spx_days, vix_data, params)
                            stats = compute_stats(trades)

                            if stats and stats['n'] >= 5:
                                result = {
                                    'min_rally': mr,
                                    'min_vel': mv,
                                    'vix_range': vix_label,
                                    'entry_window': win_label,
                                    'hold_bars': hb,
                                    'stop_pct': sp,
                                    **stats,
                                }
                                # Also store sample trades for top configs
                                if stats['n'] >= 20 and stats['win_rate'] >= 55:
                                    result['sample_trades'] = trades[:10]
                                all_results.append(result)

    print(f"\nDone. {len(all_results)} configs with >= 5 trades.")

    # Sort by Sharpe
    all_results.sort(key=lambda x: x['sharpe'], reverse=True)

    # Print top results
    print(f"\n{'='*100}")
    print(f"TOP 30 BY SHARPE (min 5 trades)")
    print(f"{'='*100}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>12} {'Window':>14} {'Hold':>4} {'Stop':>5} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>7} {'MFE':>6} {'MAE':>6} {'StopR':>5} {'Sharpe':>7}")
    print("-" * 100)

    for r in all_results[:30]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>12} "
              f"{r['entry_window']:>14} {r['hold_bars']:>4} {r['stop_pct']:>4.2f}% "
              f"{r['n']:>4} {r['win_rate']:>5.1f} {r['avg_pnl']:>6.4f}% "
              f"{r['avg_mfe']:>5.4f} {r['avg_mae']:>5.4f} {r['stop_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # Also show top by N (statistical significance)
    sig_results = [r for r in all_results if r['n'] >= 30]
    sig_results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*100}")
    print(f"TOP 30 BY SHARPE (min 30 trades — statistically meaningful)")
    print(f"{'='*100}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>12} {'Window':>14} {'Hold':>4} {'Stop':>5} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>7} {'MFE':>6} {'MAE':>6} {'StopR':>5} {'Sharpe':>7}")
    print("-" * 100)

    for r in sig_results[:30]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>12} "
              f"{r['entry_window']:>14} {r['hold_bars']:>4} {r['stop_pct']:>4.2f}% "
              f"{r['n']:>4} {r['win_rate']:>5.1f} {r['avg_pnl']:>6.4f}% "
              f"{r['avg_mfe']:>5.4f} {r['avg_mae']:>5.4f} {r['stop_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # Highest N configs
    high_n = [r for r in all_results if r['n'] >= 100]
    high_n.sort(key=lambda x: x['avg_pnl'], reverse=True)

    print(f"\n{'='*100}")
    print(f"TOP 20 BY AVG PNL (min 100 trades — highest confidence)")
    print(f"{'='*100}")
    print(f"{'Rally':>6} {'Vel':>5} {'VIX':>12} {'Window':>14} {'Hold':>4} {'Stop':>5} "
          f"{'N':>4} {'WR%':>5} {'AvgPnL':>7} {'MFE':>6} {'MAE':>6} {'StopR':>5} {'Sharpe':>7}")
    print("-" * 100)

    for r in high_n[:20]:
        print(f"{r['min_rally']:>5.2f}% {r['min_vel']:>5.2f} {r['vix_range']:>12} "
              f"{r['entry_window']:>14} {r['hold_bars']:>4} {r['stop_pct']:>4.2f}% "
              f"{r['n']:>4} {r['win_rate']:>5.1f} {r['avg_pnl']:>6.4f}% "
              f"{r['avg_mfe']:>5.4f} {r['avg_mae']:>5.4f} {r['stop_rate']:>5.1f} {r['sharpe']:>7.3f}")

    # VIX regime breakdown for the best overall config
    print(f"\n{'='*100}")
    print(f"VIX REGIME ANALYSIS — Rally >= 0.40%, Vel >= 0.05, First Hour, Hold 60, Stop 0.50%")
    print(f"{'='*100}")

    base_params = {
        'min_rally': 0.40,
        'min_vel': 0.05,
        'entry_window_start': 0,
        'entry_window_end': 60,
        'hold_bars': 60,
        'stop_pct': 0.50,
    }

    for vix_lo, vix_hi, vix_label in [(0,99,'ALL'), (0,15,'<15'), (15,18,'15-18'),
                                       (18,22,'18-22'), (22,25,'22-25'), (25,30,'25-30'),
                                       (30,35,'30-35'), (35,99,'35+')]:
        p = {**base_params, 'vix_min': vix_lo, 'vix_max': vix_hi}
        trades = run_study(spx_days, vix_data, p)
        stats = compute_stats(trades)
        if stats:
            print(f"  VIX {vix_label:>6}: N={stats['n']:>4}  WR={stats['win_rate']:>5.1f}%  "
                  f"AvgPnL={stats['avg_pnl']:>7.4f}%  MFE={stats['avg_mfe']:>6.4f}%  "
                  f"MAE={stats['avg_mae']:>6.4f}%  StopRate={stats['stop_rate']:>5.1f}%")
        else:
            print(f"  VIX {vix_label:>6}: no trades")

    # Time-of-day analysis
    print(f"\n{'='*100}")
    print(f"ENTRY TIME ANALYSIS — Rally >= 0.40%, Vel >= 0.05, VIX ALL, Hold 60, Stop 0.50%")
    print(f"{'='*100}")

    for ws, we, label in [(0,10,'9:30-9:40'), (0,15,'9:30-9:45'), (0,20,'9:30-9:50'),
                           (0,30,'9:30-10:00'), (0,45,'9:30-10:15'), (0,60,'9:30-10:30'),
                           (0,90,'9:30-11:00'), (0,120,'9:30-11:30'),
                           (30,60,'10:00-10:30'), (60,120,'10:30-11:30')]:
        p = {**base_params, 'vix_min': 0, 'vix_max': 99,
             'entry_window_start': ws, 'entry_window_end': we}
        trades = run_study(spx_days, vix_data, p)
        stats = compute_stats(trades)
        if stats:
            print(f"  {label:>14}: N={stats['n']:>4}  WR={stats['win_rate']:>5.1f}%  "
                  f"AvgPnL={stats['avg_pnl']:>7.4f}%  MFE={stats['avg_mfe']:>6.4f}%  "
                  f"StopRate={stats['stop_rate']:>5.1f}%  Sharpe={stats['sharpe']:>6.3f}")
        else:
            print(f"  {label:>14}: no trades")

    # Save all results
    with open(OUT_FILE, 'w') as f:
        json.dump(all_results, f)
    print(f"\nSaved {len(all_results)} configs to {OUT_FILE}")

if __name__ == '__main__':
    main()
