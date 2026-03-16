#!/usr/bin/env python3
"""
Deep dive on the best morning rip fade config:
  Rally >= 0.70%, Vel >= 0.10, VIX >= 25, first 30 min

Outputs detailed trade-by-trade CSV for analysis.
Also tests some refinements.
"""

import csv, os, sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPX_FILE = os.path.join(SCRIPT_DIR, 'spx_1min_bars.csv')
VIX_FILE = os.path.join(SCRIPT_DIR, 'vix_daily_bars.csv')

def load_spx():
    days = defaultdict(list)
    with open(SPX_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row['date']
            t = row['time']
            hh, mm = int(t[:2]), int(t[3:5])
            mins = hh * 60 + mm
            if mins < 570 or mins >= 960:
                continue
            days[d].append({
                'time': t, 'mins': mins,
                'o': float(row['open']), 'h': float(row['high']),
                'l': float(row['low']), 'c': float(row['close']),
                'idx': mins - 570,
            })
    for d in days:
        days[d].sort(key=lambda x: x['mins'])
    return days

def load_vix():
    vix = {}
    with open(VIX_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            vix[row['date']] = {
                'open': float(row['open']), 'high': float(row['high']),
                'low': float(row['low']), 'close': float(row['close']),
            }
    return vix

def analyze_trades(spx_days, vix_data, min_rally, min_vel, vix_min, vix_max,
                   win_start, win_end):
    """Get all trades with full forward path data."""
    trades = []

    for date_str in sorted(spx_days.keys()):
        bars = spx_days[date_str]
        if len(bars) < 60:
            continue

        vd = vix_data.get(date_str)
        if not vd:
            continue
        if vd['open'] < vix_min or vd['open'] > vix_max:
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

            if (win_start <= bar['idx'] <= win_end
                and rally_pct >= min_rally
                and vel >= min_vel):

                entry_px = bar['c']
                entry_time = bar['time']
                entry_idx = i

                # Track full forward path: every bar for next 120 min
                path = []
                for j in range(i + 1, min(i + 121, len(bars))):
                    bj = bars[j]
                    bars_held = j - i
                    move_pct = (entry_px - bj['c']) / entry_px * 100  # positive = favorable for fade
                    min_px = min(bars[j]['l'] for j2 in range(i, j+1) for bars2 in [bars[j2]])
                    max_px_after = max(bk['h'] for bk in bars[i+1:j+1])
                    mfe = (entry_px - min(bk['l'] for bk in bars[i+1:j+1])) / entry_px * 100
                    mae = (max_px_after - entry_px) / entry_px * 100
                    path.append({
                        'bars_held': bars_held,
                        'close': round(bj['c'], 2),
                        'move_pct': round(move_pct, 4),
                        'mfe': round(mfe, 4),
                        'mae': round(mae, 4),
                    })

                # Day's close
                day_close = bars[-1]['c']
                day_move = (entry_px - day_close) / entry_px * 100

                trades.append({
                    'date': date_str,
                    'entry_time': entry_time,
                    'entry_px': round(entry_px, 2),
                    'rolling_low': round(rolling_low, 2),
                    'rally_at_entry': round(rally_pct, 4),
                    'vel_at_entry': round(vel, 4),
                    'vix_open': round(vd['open'], 2),
                    'elapsed_bars': elapsed,
                    'day_close': round(day_close, 2),
                    'day_move_pct': round(day_move, 4),
                    'path': path,
                })
                break  # One trade per day

    return trades

def main():
    print("Loading data...")
    spx_days = load_spx()
    vix_data = load_vix()
    print(f"  {len(spx_days)} days, {len(vix_data)} VIX days")

    # Run the best config
    configs = [
        ('BEST: Rally>=0.70, Vel>=0.10, VIX>=25, first30min',
         0.70, 0.10, 25, 99, 0, 30),
        ('WIDER: Rally>=0.50, Vel>=0.05, VIX>=25, first30min',
         0.50, 0.05, 25, 99, 0, 30),
        ('TIGHT: Rally>=1.00, Vel>=0.10, VIX>=25, first30min',
         1.00, 0.10, 25, 99, 0, 30),
        ('MID_VIX: Rally>=0.70, Vel>=0.10, VIX 18-25, first30min',
         0.70, 0.10, 18, 25, 0, 30),
        ('ALL_VIX: Rally>=0.70, Vel>=0.10, all VIX, first30min',
         0.70, 0.10, 0, 99, 0, 30),
        ('FAST: Rally>=0.50, Vel>=0.15, VIX>=25, first30min',
         0.50, 0.15, 25, 99, 0, 30),
    ]

    all_trades = {}

    for label, mr, mv, vmin, vmax, ws, we in configs:
        trades = analyze_trades(spx_days, vix_data, mr, mv, vmin, vmax, ws, we)
        all_trades[label] = trades
        print(f"\n{'='*80}")
        print(f"{label}: {len(trades)} trades")
        print(f"{'='*80}")

        if not trades:
            continue

        # Forward curve: avg move at each hold period
        print(f"\nForward P&L curve (fade direction, +ve = favorable):")
        print(f"{'Bars':>5} {'AvgMove':>8} {'Median':>8} {'WR%':>6} {'AvgMFE':>8} {'AvgMAE':>8}")
        for hold in [5, 10, 15, 20, 30, 45, 60, 90, 120]:
            moves = []
            mfes = []
            maes = []
            for t in trades:
                if len(t['path']) >= hold:
                    p = t['path'][hold - 1]
                    moves.append(p['move_pct'])
                    mfes.append(p['mfe'])
                    maes.append(p['mae'])
            if moves:
                avg = sum(moves) / len(moves)
                sorted_m = sorted(moves)
                med = sorted_m[len(sorted_m)//2]
                wr = sum(1 for m in moves if m > 0) / len(moves) * 100
                amfe = sum(mfes) / len(mfes)
                amae = sum(maes) / len(maes)
                print(f"{hold:>5} {avg:>7.4f}% {med:>7.4f}% {wr:>5.1f}% {amfe:>7.4f}% {amae:>7.4f}%")

        # Day close stats
        day_moves = [t['day_move_pct'] for t in trades]
        avg_day = sum(day_moves) / len(day_moves)
        day_wr = sum(1 for m in day_moves if m > 0) / len(day_moves) * 100
        print(f"\nBy day close: avg={avg_day:.4f}%, WR={day_wr:.1f}%")

        # Year breakdown
        print(f"\nBy year:")
        by_year = defaultdict(list)
        for t in trades:
            by_year[t['date'][:4]].append(t)
        for yr in sorted(by_year.keys()):
            yts = by_year[yr]
            ym = [t['day_move_pct'] for t in yts]
            ywr = sum(1 for m in ym if m > 0) / len(ym) * 100
            print(f"  {yr}: {len(yts):>3} trades, day_WR={ywr:.0f}%, avg_day_move={sum(ym)/len(ym):.4f}%")

        # VIX buckets
        print(f"\nBy VIX bucket:")
        for lo, hi, lbl in [(25,30,'25-30'), (30,35,'30-35'), (35,45,'35-45'), (45,99,'45+')]:
            vts = [t for t in trades if lo <= t['vix_open'] < hi]
            if vts:
                vm = [t['day_move_pct'] for t in vts]
                vwr = sum(1 for m in vm if m > 0) / len(vm) * 100
                print(f"  VIX {lbl}: {len(vts):>3} trades, day_WR={vwr:.0f}%, avg={sum(vm)/len(vm):.4f}%")

    # Save detailed trade list for best config
    best_trades = all_trades.get('BEST: Rally>=0.70, Vel>=0.10, VIX>=25, first30min', [])
    if best_trades:
        out_csv = os.path.join(SCRIPT_DIR, 'morning_rip_trades.csv')
        with open(out_csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['date', 'entry_time', 'entry_px', 'rolling_low', 'rally_pct',
                        'velocity', 'vix_open', 'elapsed_bars',
                        'move_15bar', 'move_30bar', 'move_60bar', 'day_close_move',
                        'mfe_30bar', 'mae_30bar'])
            for t in best_trades:
                m15 = t['path'][14]['move_pct'] if len(t['path']) >= 15 else ''
                m30 = t['path'][29]['move_pct'] if len(t['path']) >= 30 else ''
                m60 = t['path'][59]['move_pct'] if len(t['path']) >= 60 else ''
                mfe30 = t['path'][29]['mfe'] if len(t['path']) >= 30 else ''
                mae30 = t['path'][29]['mae'] if len(t['path']) >= 30 else ''
                w.writerow([t['date'], t['entry_time'], t['entry_px'], t['rolling_low'],
                           t['rally_at_entry'], t['vel_at_entry'], t['vix_open'],
                           t['elapsed_bars'], m15, m30, m60, t['day_move_pct'],
                           mfe30, mae30])
        print(f"\nSaved {len(best_trades)} detailed trades to {out_csv}")

if __name__ == '__main__':
    main()
