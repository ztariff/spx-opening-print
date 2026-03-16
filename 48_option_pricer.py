#!/usr/bin/env python3
"""
Phase 2: Real Option Pricing for Discovered Edges
===================================================
Takes the top directional edges from 47_edge_discovery.py and prices them
with real SPXW 0DTE option contracts from the 53k+ cached contracts.

Trade structures tested per edge:
  1. Long ATM call (for bullish edges)  / Long ATM put (for bearish edges)
  2. Long OTM call/put (~5pt OTM) — cheaper entry, higher leverage
  3. Bull call spread / Bear put spread (defined risk)
  4. Bear call spread / Bull put spread (credit spread, short premium)
  5. Underlying SPX points (benchmark)

All P&L from real option bars — NO Black-Scholes, NO synthetic data.
"""

import csv, json, os, sys, statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from glob import glob

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / 'options_cache'

# ═══════════════════════════════════════════════════════════════
# OPTION DATA LOADING
# ═══════════════════════════════════════════════════════════════

_cache = {}

def load_option_bars(ticker, date_str):
    """Load 1-min option bars from cache. Returns list of bars or None."""
    key = f"{ticker}_{date_str}"
    if key in _cache:
        return _cache[key]

    # File naming: O_SPXW180103C02700000_2018-01-03.json
    # ticker like O:SPXW180103C02700000 → filename O_SPXW180103C02700000
    fn = ticker.replace(':', '_') + f'_{date_str}.json'
    path = CACHE_DIR / fn
    if not path.exists():
        _cache[key] = None
        return None

    with open(path) as f:
        data = json.load(f)

    if not data:
        _cache[key] = None
        return None

    # Convert timestamps to minutes-of-day in ET
    bars = []
    for bar in data:
        ts = bar['t'] / 1000  # ms → sec
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        # ET = UTC-5 (EST) or UTC-4 (EDT)
        # Approximate: use month to determine DST
        month = dt.month
        if 3 <= month <= 10:  # roughly EDT
            et = dt - timedelta(hours=4)
        else:
            et = dt - timedelta(hours=5)
        mins = et.hour * 60 + et.minute
        if mins < 570 or mins >= 960:
            continue
        bars.append({
            'mins': mins,
            'o': bar['o'], 'h': bar['h'], 'l': bar['l'], 'c': bar['c'],
            'v': bar.get('v', 0),
        })

    bars.sort(key=lambda x: x['mins'])
    _cache[key] = bars if bars else None
    return _cache[key]


def build_spxw_ticker(date_str, cp, strike):
    """Build SPXW ticker. date_str='2024-03-15', cp='C'/'P', strike=5200."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    ymd = dt.strftime('%y%m%d')
    strike_fmt = f"{int(strike * 1000):08d}"
    return f"O:SPXW{ymd}{cp}{strike_fmt}"


def find_option_at_time(bars, target_mins, tolerance=3):
    """Find the bar closest to target_mins within tolerance."""
    if not bars:
        return None
    best = None
    best_diff = 999
    for b in bars:
        diff = abs(b['mins'] - target_mins)
        if diff < best_diff:
            best_diff = diff
            best = b
    if best_diff <= tolerance:
        return best
    return None


def get_strike_from_price(price, round_to=5):
    """Round SPX price to nearest strike increment."""
    return round(price / round_to) * round_to


# ═══════════════════════════════════════════════════════════════
# TRADE PRICING ENGINE
# ═══════════════════════════════════════════════════════════════

def price_single_leg(date_str, entry_mins, exit_mins, spx_entry_price, direction, structure):
    """
    Price a single-leg option trade.
    structure: 'long_atm_call', 'long_atm_put', 'long_otm_call', 'long_otm_put'
    direction: 1=bullish, -1=bearish
    Returns dict with option P&L or None if data unavailable.
    """
    atm_strike = get_strike_from_price(spx_entry_price)

    if structure == 'long_atm_call':
        strike = atm_strike
        cp = 'C'
        opt_direction = 1  # long
    elif structure == 'long_atm_put':
        strike = atm_strike
        cp = 'P'
        opt_direction = 1
    elif structure == 'long_otm_call':
        strike = atm_strike + 5
        cp = 'C'
        opt_direction = 1
    elif structure == 'long_otm_put':
        strike = atm_strike - 5
        cp = 'P'
        opt_direction = 1
    elif structure == 'short_atm_call':
        strike = atm_strike
        cp = 'C'
        opt_direction = -1
    elif structure == 'short_atm_put':
        strike = atm_strike
        cp = 'P'
        opt_direction = -1
    else:
        return None

    ticker = build_spxw_ticker(date_str, cp, strike)
    bars = load_option_bars(ticker, date_str)
    if not bars:
        return None

    entry_bar = find_option_at_time(bars, entry_mins, tolerance=3)
    exit_bar = find_option_at_time(bars, exit_mins, tolerance=3)
    if not entry_bar or not exit_bar:
        return None

    entry_price = entry_bar['c']
    exit_price = exit_bar['c']

    if entry_price <= 0:
        return None

    # P&L per contract (×100 multiplier)
    pnl_per_contract = opt_direction * (exit_price - entry_price) * 100

    return {
        'ticker': ticker,
        'strike': strike,
        'cp': cp,
        'opt_direction': opt_direction,
        'entry_opt_price': entry_price,
        'exit_opt_price': exit_price,
        'pnl_per_contract': pnl_per_contract,
        'pnl_pct': opt_direction * (exit_price - entry_price) / entry_price * 100,
    }


def price_spread(date_str, entry_mins, exit_mins, spx_entry_price, direction, structure):
    """
    Price a vertical spread.
    structure: 'bear_call_spread', 'bull_put_spread', 'bull_call_spread', 'bear_put_spread'
    Returns dict with spread P&L or None.
    """
    atm_strike = get_strike_from_price(spx_entry_price)
    width = 5  # 5-point wide spread

    if structure == 'bear_call_spread':
        # Sell ATM call, buy ATM+5 call
        short_strike = atm_strike
        long_strike = atm_strike + width
        cp = 'C'
    elif structure == 'bull_put_spread':
        # Sell ATM put, buy ATM-5 put
        short_strike = atm_strike
        long_strike = atm_strike - width
        cp = 'P'
    elif structure == 'bull_call_spread':
        # Buy ATM call, sell ATM+5 call
        short_strike = atm_strike + width
        long_strike = atm_strike
        cp = 'C'
    elif structure == 'bear_put_spread':
        # Buy ATM put, sell ATM-5 put
        short_strike = atm_strike - width
        long_strike = atm_strike
        cp = 'P'
    else:
        return None

    short_ticker = build_spxw_ticker(date_str, cp, short_strike)
    long_ticker = build_spxw_ticker(date_str, cp, long_strike)

    short_bars = load_option_bars(short_ticker, date_str)
    long_bars = load_option_bars(long_ticker, date_str)
    if not short_bars or not long_bars:
        return None

    short_entry = find_option_at_time(short_bars, entry_mins, tolerance=3)
    short_exit = find_option_at_time(short_bars, exit_mins, tolerance=3)
    long_entry = find_option_at_time(long_bars, entry_mins, tolerance=3)
    long_exit = find_option_at_time(long_bars, exit_mins, tolerance=3)

    if not all([short_entry, short_exit, long_entry, long_exit]):
        return None

    # Credit/debit at entry
    if structure in ('bear_call_spread', 'bull_put_spread'):
        # Credit spread: sell short_strike, buy long_strike
        credit = short_entry['c'] - long_entry['c']
        debit_to_close = short_exit['c'] - long_exit['c']
        pnl_per_contract = (credit - debit_to_close) * 100
    else:
        # Debit spread: buy long_strike, sell short_strike
        debit = long_entry['c'] - short_entry['c']
        credit_at_close = long_exit['c'] - short_exit['c']
        pnl_per_contract = (credit_at_close - debit) * 100

    return {
        'short_ticker': short_ticker,
        'long_ticker': long_ticker,
        'short_strike': short_strike,
        'long_strike': long_strike,
        'cp': cp,
        'pnl_per_contract': pnl_per_contract,
    }


# ═══════════════════════════════════════════════════════════════
# EDGE SELECTION — best edges per VIX regime with robust N
# ═══════════════════════════════════════════════════════════════

SELECTED_EDGES = [
    # LOW VIX — Morning strong bull momentum
    {
        'name': 'Strong_FB_LowVIX',
        'desc': 'Strong bullish first bar, VIX<20, trail 0.10% SL 5pts',
        'json_label': 'FB_StrongBull_10|VIX_lt20|Trail10_SL5_TS30',
        'direction': 1,  # bullish
        'vix_regime': 'low',
        'structures': ['long_atm_call', 'long_otm_call', 'bull_call_spread'],
    },
    # MID VIX — Morning strong bull momentum
    {
        'name': 'Strong_FB_MidVIX',
        'desc': 'Strong bullish first bar, VIX 16-25, trail 0.10% SL 5pts',
        'json_label': 'FB_StrongBull_10|VIX_Mid|Trail10_SL5_TS30',
        'direction': 1,
        'vix_regime': 'mid',
        'structures': ['long_atm_call', 'long_otm_call', 'bull_call_spread'],
    },
    # LOW VIX — Gap up fade
    {
        'name': 'GapUp_Fade_LowVIX',
        'desc': 'Fade gap-up >20bps, VIX<20, trail 0.15% SL 8pts, above 50dma',
        'json_label': 'GapUp_Fade_20|VIX_lt20|Trail15_SL8_TS45|Above50d|AllDays',
        'direction': -1,  # bearish
        'vix_regime': 'low',
        'structures': ['long_atm_put', 'long_otm_put', 'bear_put_spread'],
    },
    # MID VIX — Gap up fade
    {
        'name': 'GapUp_Fade_MidVIX',
        'desc': 'Fade gap-up >20bps, VIX Mid, trail 0.05% SL 15pts, above 50d',
        'json_label': 'GapUp_Fade_20|VIX_Mid|Trail05_SL15_TS30|Above50d|AllDays',
        'direction': -1,
        'vix_regime': 'mid',
        'structures': ['long_atm_put', 'long_otm_put', 'bear_put_spread'],
    },
    # LOW VIX — Gap up continuation
    {
        'name': 'GapUp_Cont_LowVIX',
        'desc': 'Continue gap-up >20bps, VIX<20, PT 5pts SL 3pts',
        'json_label': 'GapUp_Cont_20|VIX_lt20|PT5_SL3_TS15',
        'direction': 1,
        'vix_regime': 'low',
        'structures': ['long_atm_call', 'long_otm_call', 'bull_call_spread'],
    },
    # HIGH VIX — Gap down continuation (bearish, big moves)
    {
        'name': 'GapDn_Cont_HighVIX',
        'desc': 'Gap-down >50bps continues, VIX>25, below 20d',
        'json_label': 'GapDn_Cont_50|VIX_gt25|PT50_SL25_TS180|Below20d|AllDays',
        'direction': -1,
        'vix_regime': 'high',
        'structures': ['long_atm_put', 'long_otm_put', 'bear_call_spread'],
    },
    # EXTREME VIX — OR breakout bearish
    {
        'name': 'OR5_Bear_ExtVIX',
        'desc': '5-min OR bearish breakout, VIX>30, trail 0.20%',
        'json_label': 'OR5_Bear|VIX_gt30|Trail20_SL10_TS60|Below20d|AllDays',
        'direction': -1,
        'vix_regime': 'extreme',
        'structures': ['long_atm_put', 'long_otm_put', 'bear_call_spread'],
    },
    # MID VIX — Gap up continuation (50bps, OOS VALIDATED)
    {
        'name': 'GapUp_Cont50_MidVIX_OOS',
        'desc': 'Gap-up >50bps cont, VIX Mid, below 20d — OOS VALIDATED',
        'json_label': 'GapUp_Cont_50|VIX_Mid|PT_50bps_SL_25bps_TS30|Below20d|AllDays',
        'direction': 1,
        'vix_regime': 'mid',
        'structures': ['long_atm_call', 'long_otm_call', 'bull_call_spread'],
    },
    # AFTERNOON — PM trend in extreme VIX
    {
        'name': 'PM_Trend_ExtVIX',
        'desc': 'PM trend continuation, VIX extreme, trail 0.05%',
        'json_label': 'PM_Trend_30|VIX_gt30|PT5_SL2_TS5',
        'direction': 0,  # mixed — direction embedded in trades
        'vix_regime': 'extreme',
        'structures': ['long_atm_call', 'long_atm_put'],  # depends on direction per trade
    },
    # MORNING — OR30 bearish breakout low VIX
    {
        'name': 'OR30_Bear_LowVIX',
        'desc': '30-min OR bearish, VIX Low, %PT/SL, hold to close-ish',
        'json_label': 'OR30_Bear|VIX_Low|PT_100bps_SL_50bps_TS60',
        'direction': -1,
        'vix_regime': 'low',
        'structures': ['long_atm_put', 'long_otm_put', 'bear_put_spread'],
    },
]


def convert_time_to_mins(time_str):
    """Convert HH:MM or HH:MM:SS to minutes since midnight."""
    parts = time_str.split(':')
    return int(parts[0]) * 60 + int(parts[1])


def load_edge_trades(label):
    """Load trades from saved edge CSVs by matching label."""
    results_dir = SCRIPT_DIR / 'backtest_results'

    # First try to find from edge_discovery_results.json to get trade details
    # But the JSON doesn't store individual trades — we need the CSV files
    # Match by label prefix in filename
    safe = label.replace('|', '_').replace(' ', '_')[:50]

    # Search all edge CSV files
    for csv_path in sorted(results_dir.glob('edge_*.csv')):
        fn = csv_path.stem
        # Check if this file matches our label
        # The filename format is edge_NN_LABEL
        label_part = fn.split('_', 2)[2] if len(fn.split('_', 2)) > 2 else ''
        if safe.startswith(label_part[:20]) or label_part.startswith(safe[:20]):
            trades = []
            with open(csv_path) as f:
                for row in csv.DictReader(f):
                    trades.append(row)
            if trades:
                return trades

    return None


def run_edge_discovery_fresh(label):
    """Re-run the edge signal to get trades since CSV matching may fail.
    This reloads from the full discovery results JSON."""
    # Load the full results
    with open(SCRIPT_DIR / 'backtest_results' / 'edge_discovery_results.json') as f:
        results = json.load(f)

    for r in results:
        if r['label'] == label:
            return r
    return None


# ═══════════════════════════════════════════════════════════════
# MAIN PRICING LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 140)
    print("PHASE 2: REAL OPTION PRICING FOR TOP EDGES")
    print("=" * 140)

    # Pre-index available SPXW cache files for fast lookup
    print("Indexing option cache…")
    available_files = set()
    for f in os.listdir(CACHE_DIR):
        if f.startswith('O_SPXW'):
            available_files.add(f)
    print(f"  {len(available_files)} SPXW files indexed")

    # Load all edge trade CSVs
    all_edge_results = []

    for edge in SELECTED_EDGES:
        print(f"\n{'─'*140}")
        print(f"  Edge: {edge['name']} — {edge['desc']}")
        print(f"  Label: {edge['json_label']}")

        # Try to load trades from CSV
        trades = load_edge_trades(edge['json_label'])
        if not trades:
            print(f"  ⚠ No trade CSV found for this edge, skipping")
            continue

        print(f"  {len(trades)} trades to price")

        # For each structure, price all trades
        for structure in edge['structures']:
            results = {
                'edge': edge['name'],
                'structure': structure,
                'trades_total': len(trades),
                'trades_priced': 0,
                'trades_missed': 0,
                'pnl_list': [],
                'pnl_pct_list': [],
                'trade_details': [],
            }

            for trade in trades:
                date_str = trade['date']
                entry_time = trade['entry_time']
                exit_time = trade['exit_time']
                entry_price = float(trade['entry_price'])
                trade_direction = int(trade['direction'])

                entry_mins = convert_time_to_mins(entry_time)
                exit_mins = convert_time_to_mins(exit_time)

                # Determine which structures to use based on trade direction
                actual_structure = structure
                if edge['direction'] == 0:
                    # Mixed direction — pick based on individual trade
                    if trade_direction == 1:
                        if 'put' in structure:
                            continue  # skip puts for bullish trades
                    else:
                        if 'call' in structure and 'bear' not in structure:
                            continue  # skip calls for bearish trades

                # Price it
                if 'spread' in actual_structure:
                    result = price_spread(date_str, entry_mins, exit_mins,
                                          entry_price, trade_direction, actual_structure)
                else:
                    result = price_single_leg(date_str, entry_mins, exit_mins,
                                             entry_price, trade_direction, actual_structure)

                if result:
                    results['trades_priced'] += 1
                    results['pnl_list'].append(result['pnl_per_contract'])
                    if 'pnl_pct' in result:
                        results['pnl_pct_list'].append(result['pnl_pct'])
                    results['trade_details'].append({
                        'date': date_str,
                        'entry_time': entry_time,
                        'exit_time': exit_time,
                        'spx_price': entry_price,
                        'direction': trade_direction,
                        'und_pts': float(trade['und_pts']),
                        'opt_pnl': result['pnl_per_contract'],
                    })
                else:
                    results['trades_missed'] += 1

            # Compute stats
            if results['trades_priced'] >= 5:
                pnl = results['pnl_list']
                n = len(pnl)
                avg_pnl = statistics.mean(pnl)
                total_pnl = sum(pnl)
                wins = [p for p in pnl if p > 0]
                losses = [p for p in pnl if p <= 0]
                wr = len(wins) / n * 100
                avg_win = statistics.mean(wins) if wins else 0
                avg_loss = statistics.mean(losses) if losses else 0
                gross_win = sum(wins)
                gross_loss = abs(sum(losses))
                pf = gross_win / gross_loss if gross_loss > 0 else 99
                std = statistics.stdev(pnl) if n > 1 else 0
                sharpe = avg_pnl / std if std > 0 else 0

                # Max drawdown
                cum = 0
                peak = 0
                max_dd = 0
                for p in pnl:
                    cum += p
                    if cum > peak:
                        peak = cum
                    dd = peak - cum
                    if dd > max_dd:
                        max_dd = dd

                stats = {
                    'edge': edge['name'],
                    'structure': structure,
                    'n': n,
                    'priced_pct': round(n / results['trades_total'] * 100, 1),
                    'wr': round(wr, 1),
                    'avg_pnl': round(avg_pnl, 2),
                    'total_pnl': round(total_pnl, 2),
                    'sharpe': round(sharpe, 3),
                    'pf': round(pf, 2),
                    'max_dd': round(max_dd, 2),
                    'avg_win': round(avg_win, 2),
                    'avg_loss': round(avg_loss, 2),
                }

                print(f"    {structure:25s}  N={n:>4} ({stats['priced_pct']}% priced)  "
                      f"WR={wr:>5.1f}%  AvgPnL=${avg_pnl:>+8.2f}  "
                      f"TotalPnL=${total_pnl:>+10.2f}  Sharpe={sharpe:>6.3f}  "
                      f"PF={pf:>5.2f}  MaxDD=${max_dd:>9.2f}")

                all_edge_results.append(stats)

                # Save individual trade details
                det_file = SCRIPT_DIR / 'backtest_results' / f"opt_{edge['name']}_{structure}.csv"
                if results['trade_details']:
                    with open(det_file, 'w', newline='') as f:
                        w = csv.DictWriter(f, fieldnames=results['trade_details'][0].keys())
                        w.writeheader()
                        w.writerows(results['trade_details'])
            else:
                missed = results['trades_missed']
                priced = results['trades_priced']
                print(f"    {structure:25s}  Only {priced} priced ({missed} missed) — insufficient data")

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*140}")
    print("OPTION-PRICED EDGE SUMMARY — Sorted by Sharpe Ratio")
    print(f"{'='*140}")
    print(f"{'Edge':>25s} {'Structure':>25s} {'N':>5} {'%Priced':>8} {'WR%':>6} "
          f"{'AvgPnL':>10} {'TotalPnL':>12} {'Sharpe':>7} {'PF':>6} {'MaxDD':>10}")
    print("-" * 140)

    all_edge_results.sort(key=lambda x: x['sharpe'], reverse=True)
    for r in all_edge_results:
        print(f"{r['edge']:>25s} {r['structure']:>25s} {r['n']:>5} {r['priced_pct']:>7.1f}% "
              f"{r['wr']:>5.1f}% ${r['avg_pnl']:>+9.2f} ${r['total_pnl']:>+11.2f} "
              f"{r['sharpe']:>7.3f} {r['pf']:>5.2f} ${r['max_dd']:>9.2f}")

    # Save results
    out_file = SCRIPT_DIR / 'backtest_results' / 'option_priced_edges.json'
    with open(out_file, 'w') as f:
        json.dump(all_edge_results, f, indent=2)
    print(f"\n  Saved {len(all_edge_results)} results to option_priced_edges.json")

    # ═══════════════════════════════════════════════════════════════
    # PORTFOLIO COMBINATIONS
    # ═══════════════════════════════════════════════════════════════
    if len(all_edge_results) >= 3:
        print(f"\n{'='*140}")
        print("PORTFOLIO ANALYSIS — Best 3-strategy combos by combined Sharpe")
        print(f"{'='*140}")

        # For each result that has trade details saved, load and combine
        from itertools import combinations as combos

        # Load trade detail CSVs
        edge_trades = {}
        for r in all_edge_results:
            det_file = SCRIPT_DIR / 'backtest_results' / f"opt_{r['edge']}_{r['structure']}.csv"
            if det_file.exists():
                trades_by_date = {}
                with open(det_file) as f:
                    for row in csv.DictReader(f):
                        trades_by_date[row['date']] = float(row['opt_pnl'])
                edge_trades[f"{r['edge']}|{r['structure']}"] = trades_by_date

        if len(edge_trades) >= 3:
            best_combos = []
            keys = list(edge_trades.keys())

            for combo in combos(keys, 3):
                # Merge daily P&L
                all_dates = set()
                for k in combo:
                    all_dates.update(edge_trades[k].keys())

                daily_pnl = []
                for d in sorted(all_dates):
                    day_total = sum(edge_trades[k].get(d, 0) for k in combo)
                    daily_pnl.append(day_total)

                if len(daily_pnl) < 20:
                    continue

                avg = statistics.mean(daily_pnl)
                std = statistics.stdev(daily_pnl)
                sharpe = avg / std if std > 0 else 0
                total = sum(daily_pnl)
                wins = [p for p in daily_pnl if p > 0]
                wr = len(wins) / len(daily_pnl) * 100

                cum = 0
                peak = 0
                max_dd = 0
                for p in daily_pnl:
                    cum += p
                    if cum > peak:
                        peak = cum
                    dd = peak - cum
                    if dd > max_dd:
                        max_dd = dd

                best_combos.append({
                    'combo': combo,
                    'sharpe': sharpe,
                    'total': total,
                    'wr': wr,
                    'n_days': len(daily_pnl),
                    'max_dd': max_dd,
                })

            best_combos.sort(key=lambda x: x['sharpe'], reverse=True)

            for i, c in enumerate(best_combos[:10]):
                print(f"\n  #{i+1}  Sharpe={c['sharpe']:.3f}  "
                      f"Total=${c['total']:+,.2f}  WR={c['wr']:.1f}%  "
                      f"MaxDD=${c['max_dd']:,.2f}  Days={c['n_days']}")
                for k in c['combo']:
                    print(f"       → {k}")

    print(f"\n{'='*120}")
    print("DONE")
    print(f"{'='*120}")


if __name__ == '__main__':
    main()
