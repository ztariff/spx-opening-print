#!/usr/bin/env python3
"""
Overnight Low Bounce Study — UNDERLYING ONLY (no options, no Polygon)
=====================================================================
Uses SPX 1-min bars + SPY extended hours to study:
  Does the overnight low act as support during RTH?
  What conditions predict a bounce vs breakdown?

This runs fast (no API calls for cached data) and gives us
the FULL 2018-2026 picture on the underlying setup.

For days where we don't have SPY overnight data, we use the
PRIOR DAY RTH LOW as a proxy for the overnight low. This isn't
perfect but gives us 2000+ days to study instead of ~800.

Run:  python3 41_overnight_bounce_study.py
"""

import csv, json, sys
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
SPX_CSV = SCRIPT_DIR / 'spx_1min_bars.csv'
VIX_CSV = SCRIPT_DIR / 'vix_daily_bars.csv'
CACHE_DIR = SCRIPT_DIR / 'overnight_cache'

START_DATE = '2018-01-03'  # need prior day for RTH low


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


def load_spy_overnight():
    """Load any cached SPY overnight data we already have."""
    overnight = {}
    if not CACHE_DIR.exists():
        return overnight

    # DST helper
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

    cache_files = sorted(CACHE_DIR.glob('*.json'))
    print(f"  Loading {len(cache_files)} SPY overnight cache files…")

    on_bars = defaultdict(list)
    for cf in cache_files:
        try:
            bars = json.loads(cf.read_text())
            if not bars:
                continue
            for bar in bars:
                et = ts_et(bar['t'])
                mins = et_m(et)
                d_str = et.strftime('%Y-%m-%d')
                if mins >= 960:  # after 4pm
                    nxt = et + timedelta(days=1)
                    while nxt.weekday() >= 5:
                        nxt += timedelta(days=1)
                    on_bars[nxt.strftime('%Y-%m-%d')].append(bar)
                elif mins < 570:  # before 9:30am
                    on_bars[d_str].append(bar)
        except:
            continue

    for day, bars in on_bars.items():
        if len(bars) >= 3:
            on_low = min(b['l'] for b in bars)
            on_high = max(b['h'] for b in bars)
            on_close = bars[-1]['c']
            rally = (on_close - on_low) / on_low if on_low else 0
            overnight[day] = {
                'on_low_spy': on_low,
                'scaled_low': on_low * 10,  # SPY -> SPX approximation
                'rally': rally,
                'n_bars': len(bars),
            }

    print(f"  SPY overnight data for {len(overnight)} days")
    return overnight


def time_to_mins(t_str):
    return int(t_str[:2]) * 60 + int(t_str[3:5])


def compute_velocity(bars, idx, lookback=10):
    start = max(0, idx - lookback)
    if start == idx:
        return 0
    peak = max(b['h'] for b in bars[start:idx + 1])
    current = bars[idx]['c']
    drop_pct = (peak - current) / peak * 100
    elapsed = idx - start
    return drop_pct / max(1, elapsed)


def main():
    print("=" * 120)
    print("OVERNIGHT LOW BOUNCE STUDY — UNDERLYING ONLY")
    print("2018-2026, ~2000 trading days")
    print("=" * 120)

    spx = load_spx()
    vix = load_vix()
    spy_on = load_spy_overnight()

    all_dates = sorted(d for d in spx if d >= START_DATE)
    print(f"\nDate range: {all_dates[0]} -> {all_dates[-1]} ({len(all_dates)} days)")
    print(f"SPY overnight data available for {len(spy_on)} of those days")

    # ═══════════════════════════════════════════════════════════
    # Build overnight low for EVERY day
    # Method 1: Use SPY overnight if available (accurate)
    # Method 2: Use prior day RTH low as proxy (approximate)
    # ═══════════════════════════════════════════════════════════
    signals = {}
    method_counts = {'spy': 0, 'prior_rth': 0, 'skip': 0}

    for i, day in enumerate(all_dates):
        bars = spx.get(day, [])
        if len(bars) < 20:
            method_counts['skip'] += 1
            continue

        v = vix.get(day, 20)
        prev_day = all_dates[i - 1] if i > 0 else None
        prev_bars = spx.get(prev_day, []) if prev_day else []

        # Get overnight low
        if day in spy_on:
            on_low = spy_on[day]['scaled_low']
            rally = spy_on[day]['rally']
            method = 'spy'
        elif prev_bars:
            # Use prior day RTH low as proxy
            on_low = min(b['l'] for b in prev_bars)
            # Estimate "rally" as distance from prior low to prior close
            prior_close = prev_bars[-1]['c']
            rally = (prior_close - on_low) / on_low if on_low else 0
            method = 'prior_rth'
        else:
            method_counts['skip'] += 1
            continue

        method_counts[method] += 1

        # Today's open
        today_open = bars[0]['o']

        # Does today's price approach the overnight low?
        signals[day] = {
            'on_low': on_low,
            'rally': rally,
            'vix': v,
            'bars': bars,
            'method': method,
            'open': today_open,
        }

    print(f"\nSignal sources: SPY overnight={method_counts['spy']}, "
          f"Prior RTH low={method_counts['prior_rth']}, Skip={method_counts['skip']}")

    # ═══════════════════════════════════════════════════════════
    # Phase 1: Find all approaches to overnight low
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 1: Finding approaches to overnight low")
    print(f"{'='*120}")

    entries = []
    for day, sig in sorted(signals.items()):
        bars = sig['bars']
        on_low = sig['on_low']

        already_below = False
        for j, bar in enumerate(bars):
            t_mins = time_to_mins(bar['time'])
            if t_mins < 575 or t_mins >= 930:
                continue

            if bar['l'] < on_low - 2:
                already_below = True
            if already_below:
                continue

            dist = bar['l'] - on_low
            if dist >= -2 and dist <= 20 and bar['c'] >= on_low - 1:
                velocity = compute_velocity(bars, j)
                entries.append({
                    'date': day,
                    'idx': j,
                    'time': bar['time'],
                    'price': bar['c'],
                    'low': bar['l'],
                    'on_low': on_low,
                    'dist': dist,
                    'vix': sig['vix'],
                    'rally': sig['rally'],
                    'vel': velocity,
                    'bars': bars,
                    'method': sig['method'],
                    'open': sig['open'],
                })
                break

    print(f"Total approaches: {len(entries)}")

    # By year
    by_year = defaultdict(list)
    for e in entries:
        by_year[e['date'][:4]].append(e)
    print("\nBy year:")
    for y in sorted(by_year):
        print(f"  {y}: {len(by_year[y])} approaches")

    # By method
    spy_entries = [e for e in entries if e['method'] == 'spy']
    rth_entries = [e for e in entries if e['method'] == 'prior_rth']
    print(f"\nBy method: SPY overnight={len(spy_entries)}, Prior RTH low={len(rth_entries)}")

    # ═══════════════════════════════════════════════════════════
    # Phase 2: Study bounces at various hold periods
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 2: Bounce rates — does the overnight low hold?")
    print(f"{'='*120}")

    def bounce_analysis(subset, label):
        if len(subset) < 5:
            return
        print(f"\n  {label} (N={len(subset)})")
        print(f"  {'Hold':>5} {'BR%':>6} {'AvgMv':>8} {'Med':>6} {'StdDv':>7} | {'VIX<18':>12} {'VIX18-25':>12} {'VIX25-35':>12} {'VIX35+':>12} | {'Vel<0.03':>12} {'Vel.03-.05':>12} {'Vel.05-.10':>12} {'Vel.10+':>12}")
        for hold in [5, 10, 15, 30, 45, 60, 90, 120]:
            moves = []
            vix_buckets = {'<18': [], '18-25': [], '25-35': [], '35+': []}
            vel_buckets = {'<0.03': [], '0.03-0.05': [], '0.05-0.10': [], '0.10+': []}

            for e in subset:
                xi = min(e['idx'] + hold, len(e['bars']) - 1)
                mv = e['bars'][xi]['c'] - e['price']
                moves.append(mv)

                if e['vix'] < 18: vix_buckets['<18'].append(mv)
                elif e['vix'] < 25: vix_buckets['18-25'].append(mv)
                elif e['vix'] < 35: vix_buckets['25-35'].append(mv)
                else: vix_buckets['35+'].append(mv)

                if e['vel'] < 0.03: vel_buckets['<0.03'].append(mv)
                elif e['vel'] < 0.05: vel_buckets['0.03-0.05'].append(mv)
                elif e['vel'] < 0.10: vel_buckets['0.05-0.10'].append(mv)
                else: vel_buckets['0.10+'].append(mv)

            br = len([m for m in moves if m > 0]) / len(moves) * 100
            avg = sum(moves) / len(moves)
            med = sorted(moves)[len(moves) // 2]
            std = (sum((m - avg) ** 2 for m in moves) / len(moves)) ** 0.5

            def bucket_str(arr):
                if len(arr) < 3:
                    return f"n={len(arr)}"
                b = len([m for m in arr if m > 0]) / len(arr) * 100
                return f"{len(arr):>3}/{b:.0f}%"

            vstr = "  ".join(f"{bucket_str(vix_buckets[k]):>12}" for k in ['<18', '18-25', '25-35', '35+'])
            velstr = "  ".join(f"{bucket_str(vel_buckets[k]):>12}" for k in ['<0.03', '0.03-0.05', '0.05-0.10', '0.10+'])

            print(f"  {hold:>4}m {br:>5.1f}% {avg:>+7.2f} {med:>+5.0f} {std:>7.2f} | {vstr} | {velstr}")

    # All entries
    bounce_analysis(entries, "ALL APPROACHES (any proximity)")

    # By proximity
    for prox_label, max_p in [("Within 5pts", 5), ("Within 10pts", 10), ("Within 15pts", 15)]:
        subset = [e for e in entries if e['dist'] <= max_p]
        bounce_analysis(subset, prox_label)

    # By rally size
    for rally_label, min_r in [("Rally >= 0.2%", 0.002), ("Rally >= 0.5%", 0.005), ("Rally >= 1.0%", 0.01)]:
        subset = [e for e in entries if e['rally'] >= min_r]
        bounce_analysis(subset, rally_label)

    # ═══════════════════════════════════════════════════════════
    # Phase 3: Stop-loss analysis (consecutive bars below)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 3: Stop-loss analysis — N consecutive bars below overnight low")
    print(f"{'='*120}")

    for prox in [10, 15, 20]:
        subset = [e for e in entries if e['dist'] <= prox]
        if len(subset) < 10:
            continue
        print(f"\n  Proximity <= {prox}pts (N={len(subset)})")

        for stop_n in [3, 5, 7]:
            for hold in [30, 60]:
                wins = 0
                losses = 0
                stopped = 0
                moves_w = []
                moves_l = []

                for e in subset:
                    bars = e['bars']
                    eidx = e['idx']
                    on_low = e['on_low']

                    cb = 0
                    was_stopped = False
                    xidx = eidx
                    for jj in range(eidx + 1, min(eidx + hold + 1, len(bars))):
                        if bars[jj]['c'] < on_low:
                            cb += 1
                        else:
                            cb = 0
                        if cb >= stop_n:
                            was_stopped = True
                            xidx = jj
                            break
                        xidx = jj

                    mv = bars[xidx]['c'] - e['price']
                    if was_stopped:
                        stopped += 1
                        # For short puts, stopped = loss
                        losses += 1
                        moves_l.append(mv)
                    else:
                        # Held the full period, check outcome
                        if mv >= -2:  # within 2 pts = win for short put
                            wins += 1
                            moves_w.append(mv)
                        else:
                            losses += 1
                            moves_l.append(mv)

                total = wins + losses
                wr = wins / total * 100 if total else 0
                sr = stopped / total * 100 if total else 0
                avg_w = sum(moves_w) / len(moves_w) if moves_w else 0
                avg_l = sum(moves_l) / len(moves_l) if moves_l else 0
                print(f"    Stop={stop_n} Hold={hold:>2}m: N={total:>4}, WR={wr:>5.1f}%, SR={sr:>5.1f}%, "
                      f"AvgWinMv={avg_w:>+6.1f}, AvgLossMv={avg_l:>+6.1f}")

    # ═══════════════════════════════════════════════════════════
    # Phase 4: Detailed filter analysis
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 4: Which filters create edge? (30-min hold, 5-bar stop, prox<=15)")
    print(f"{'='*120}")

    def run_sim(subset, stop_n=5, hold=30):
        results = []
        for e in subset:
            bars = e['bars']
            eidx = e['idx']
            on_low = e['on_low']

            cb = 0
            was_stopped = False
            xidx = eidx
            for jj in range(eidx + 1, min(eidx + hold + 1, len(bars))):
                if bars[jj]['c'] < on_low:
                    cb += 1
                else:
                    cb = 0
                if cb >= stop_n:
                    was_stopped = True
                    xidx = jj
                    break
                xidx = jj

            mv = bars[xidx]['c'] - e['price']
            results.append({
                'date': e['date'],
                'move': mv,
                'stopped': was_stopped,
                'vix': e['vix'],
                'vel': e['vel'],
                'rally': e['rally'],
                'time': e['time'],
                'method': e['method'],
            })
        return results

    base = [e for e in entries if e['dist'] <= 15]
    base_results = run_sim(base, stop_n=5, hold=60)

    def analyze(results, label):
        if len(results) < 5:
            return
        w = len([r for r in results if not r['stopped'] and r['move'] >= -2])
        total = len(results)
        stopped = len([r for r in results if r['stopped']])
        avg_mv = sum(r['move'] for r in results) / total
        print(f"  {label:<45} N={total:>4}  WR={w/total*100:>5.1f}%  SR={stopped/total*100:>5.1f}%  AvgMv={avg_mv:>+6.2f}")

    analyze(base_results, "BASELINE (no filters)")

    # VIX filters
    for vlo, vhi, vlabel in [(0, 15, "VIX < 15"), (15, 18, "VIX 15-18"), (18, 25, "VIX 18-25"),
                              (25, 35, "VIX 25-35"), (35, 999, "VIX 35+"), (0, 25, "VIX < 25")]:
        subset = [e for e in base if vlo <= e['vix'] < vhi]
        results = run_sim(subset, stop_n=5, hold=60)
        analyze(results, f"{vlabel}")

    # Velocity filters
    for vlo, vhi, vlabel in [(0, 0.03, "Vel < 0.03"), (0.03, 0.05, "Vel 0.03-0.05"),
                              (0.05, 0.10, "Vel 0.05-0.10"), (0.10, 999, "Vel 0.10+"),
                              (0.05, 999, "Vel >= 0.05")]:
        subset = [e for e in base if vlo <= e['vel'] < vhi]
        results = run_sim(subset, stop_n=5, hold=60)
        analyze(results, f"{vlabel}")

    # Rally filters
    for mr, mlabel in [(0.002, "Rally >= 0.2%"), (0.005, "Rally >= 0.5%"), (0.01, "Rally >= 1.0%")]:
        subset = [e for e in base if e['rally'] >= mr]
        results = run_sim(subset, stop_n=5, hold=60)
        analyze(results, f"{mlabel}")

    # Time of day
    for tlo, thi, tlabel in [("09:30", "10:00", "Early 9:30-10:00"), ("10:00", "11:00", "Mid 10:00-11:00"),
                               ("11:00", "16:00", "Late 11:00+"), ("09:30", "10:30", "First hour")]:
        subset = [e for e in base if tlo <= e['time'] < thi]
        results = run_sim(subset, stop_n=5, hold=60)
        analyze(results, f"{tlabel}")

    # Combined filters (the interesting ones)
    print(f"\n  --- COMBINED FILTERS ---")
    combos = [
        ("VIX<25 + Vel>=0.05", lambda e: e['vix'] < 25 and e['vel'] >= 0.05),
        ("VIX 18-25 + Vel>=0.05", lambda e: 18 <= e['vix'] < 25 and e['vel'] >= 0.05),
        ("VIX<25 + Vel>=0.05 + Rally>=0.3%", lambda e: e['vix'] < 25 and e['vel'] >= 0.05 and e['rally'] >= 0.003),
        ("VIX<25 + Vel>=0.05 + Early", lambda e: e['vix'] < 25 and e['vel'] >= 0.05 and e['time'] < '10:30'),
        ("VIX 18-25 + Vel 0.05-0.07 + Early", lambda e: 18 <= e['vix'] < 25 and 0.05 <= e['vel'] < 0.07 and e['time'] < '10:30'),
        ("VIX<25 + Rally>=0.5%", lambda e: e['vix'] < 25 and e['rally'] >= 0.005),
        ("VIX<25 + Rally>=0.5% + Vel>=0.05", lambda e: e['vix'] < 25 and e['rally'] >= 0.005 and e['vel'] >= 0.05),
        ("Exclude VIX 25-35 only", lambda e: e['vix'] < 25 or e['vix'] >= 35),
        ("VIX<25 + Vel>=0.03", lambda e: e['vix'] < 25 and e['vel'] >= 0.03),
    ]

    for label, filt in combos:
        subset = [e for e in base if filt(e)]
        results = run_sim(subset, stop_n=5, hold=60)
        analyze(results, label)

    # Also test with different stop/hold params for the best combos
    print(f"\n  --- BEST COMBO WITH DIFFERENT STOP/HOLD ---")
    best_subset = [e for e in base if e['vix'] < 25 and e['vel'] >= 0.05]
    for stop_n in [3, 5, 7]:
        for hold in [15, 30, 45, 60, 90]:
            results = run_sim(best_subset, stop_n=stop_n, hold=hold)
            if len(results) >= 5:
                w = len([r for r in results if not r['stopped'] and r['move'] >= -2])
                stopped = len([r for r in results if r['stopped']])
                avg = sum(r['move'] for r in results) / len(results)
                print(f"    Stop={stop_n} Hold={hold:>2}m: N={len(results):>4}  WR={w/len(results)*100:>5.1f}%  "
                      f"SR={stopped/len(results)*100:>5.1f}%  AvgMv={avg:>+6.2f}")

    # ═══════════════════════════════════════════════════════════
    # Phase 5: Year-by-year for best combo
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print("PHASE 5: Year-by-year for VIX<25 + Vel>=0.05, stop=5, hold=60")
    print(f"{'='*120}")

    best_results = run_sim(best_subset, stop_n=5, hold=60)
    yearly = defaultdict(list)
    for r in best_results:
        yearly[r['date'][:4]].append(r)

    print(f"{'Year':>5} {'N':>4} {'WR':>6} {'SR':>6} {'AvgMv':>8} {'Method':>20}")
    for y in sorted(yearly):
        yr = yearly[y]
        w = len([r for r in yr if not r['stopped'] and r['move'] >= -2])
        s = len([r for r in yr if r['stopped']])
        avg = sum(r['move'] for r in yr) / len(yr)
        spy_n = len([r for r in yr if r['method'] == 'spy'])
        rth_n = len([r for r in yr if r['method'] == 'prior_rth'])
        print(f"{y:>5} {len(yr):>4} {w/len(yr)*100:>5.1f}% {s/len(yr)*100:>5.1f}% {avg:>+7.2f} "
              f"SPY={spy_n} RTH={rth_n}")

    # Print all trades for the best combo
    print(f"\n  All trades:")
    print(f"  {'Date':<11} {'Time':>5} {'ONL':>6} {'Price':>6} {'Dist':>4} {'Vel':>6} {'VIX':>5} {'Rally':>6} {'Move':>6} {'Stop':>4} {'Meth':>5}")
    for r, e in sorted(zip(best_results, best_subset), key=lambda x: x[1]['date']):
        st = 'STOP' if r['stopped'] else ''
        win = 'W' if not r['stopped'] and r['move'] >= -2 else 'L'
        print(f"  {r['date']:<11} {r['time']:>5} {e['on_low']:>6.0f} {e['price']:>6.0f} {e['dist']:>4.0f} "
              f"{r['vel']:>6.3f} {r['vix']:>5.0f} {r['rally']*100:>5.2f}% {r['move']:>+5.0f} {st:>4} {r['method'][:3]:>5} {win}")


if __name__ == '__main__':
    main()
