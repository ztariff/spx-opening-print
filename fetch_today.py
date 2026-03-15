#!/usr/bin/env python3
"""Fetch today's SPX 1-min bars from Polygon, check velocity signals, output to CSV."""
import urllib.request, json, sys, csv, os
from datetime import datetime, timedelta

API_KEY = os.environ.get("POLYGON_API_KEY", "")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def ts_et(ms):
    utc = datetime.utcfromtimestamp(ms / 1000)
    y = utc.year
    mar1 = datetime(y, 3, 1)
    mar_sun2 = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(y, 11, 1)
    nov_sun1 = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    if mar_sun2.replace(hour=7) <= utc < nov_sun1.replace(hour=6):
        off = timedelta(hours=-4)
    else:
        off = timedelta(hours=-5)
    return utc + off

today = datetime.utcnow().strftime('%Y-%m-%d')
if len(sys.argv) > 1:
    today = sys.argv[1]

print(f"Fetching SPX 1-min bars for {today}...")

# Polygon uses I:SPX for index tickers, fall back to SPY if needed
bars = []
used_ticker = None
for ticker in ['I:SPX', 'SPX', 'SPY']:
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute"
           f"/{today}/{today}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}")
    req = urllib.request.Request(url, headers={'User-Agent': 'Python/Backtest'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        bars = data.get('results', [])
        if bars:
            used_ticker = ticker
            print(f"  Got {len(bars)} bars from ticker '{ticker}'")
            break
        else:
            print(f"  '{ticker}' — no bars")
    except Exception as e:
        print(f"  '{ticker}' — error: {e}")

if not bars:
    print("No bars returned from any ticker.")
    sys.exit(1)

# If using SPY, scale to approximate SPX level
if used_ticker == 'SPY':
    spy_spx_ratio = 10.0
    print(f"  (Scaling SPY prices by ~{spy_spx_ratio}x to approximate SPX)")
    for b in bars:
        for k in ('o', 'h', 'l', 'c'):
            b[k] *= spy_spx_ratio

# Filter to RTH (9:30-16:00 ET = minutes 570-960)
rth = []
for b in bars:
    et = ts_et(b['t'])
    mins = et.hour * 60 + et.minute
    if 570 <= mins < 960:
        rth.append((et, b))

if not rth:
    print("No RTH bars found.")
    sys.exit(1)

# Fetch VIX
vix_open = vix_high = vix_low = vix_close = None
try:
    url2 = (f"https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day"
            f"/{today}/{today}?adjusted=true&sort=asc&limit=1&apiKey={API_KEY}")
    req2 = urllib.request.Request(url2, headers={'User-Agent': 'Python'})
    with urllib.request.urlopen(req2, timeout=15) as r2:
        vdata = json.loads(r2.read())
    vbars = vdata.get('results', [])
    if vbars:
        vix_open = vbars[0]['o']
        vix_high = vbars[0]['h']
        vix_low = vbars[0]['l']
        vix_close = vbars[0]['c']
except:
    pass

# Compute rolling high, drop%, velocity, signals for every RTH bar
rolling_high = rth[0][1]['h']
peak_idx = 0
max_drop = 0
max_vel = 0
rows = []

for i, (et, b) in enumerate(rth):
    if b['h'] > rolling_high:
        rolling_high = b['h']
        peak_idx = i

    drop_pct = (rolling_high - b['c']) / rolling_high * 100
    elapsed = i - peak_idx
    vel = drop_pct / elapsed if elapsed > 0 else 0

    if drop_pct > max_drop:
        max_drop = drop_pct
    if vel > max_vel and drop_pct > 0.05:
        max_vel = vel

    signal = ""
    if drop_pct >= 0.30 and vel >= 0.10:
        signal = "PANIC_FADE"
    elif drop_pct >= 0.30 and 0.05 <= vel < 0.10:
        signal = "DIP_BUY"
    elif drop_pct >= 0.20:
        signal = "CLOSE"

    rows.append({
        'time_et': et.strftime('%H:%M'),
        'open': round(b['o'], 2),
        'high': round(b['h'], 2),
        'low': round(b['l'], 2),
        'close': round(b['c'], 2),
        'rolling_high': round(rolling_high, 2),
        'drop_pct': round(drop_pct, 4),
        'elapsed_from_peak': elapsed,
        'velocity': round(vel, 5),
        'signal': signal,
    })

# Write main CSV — every RTH bar
out_path = os.path.join(SCRIPT_DIR, f'today_{today}.csv')
with open(out_path, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['time_et','open','high','low','close',
                                       'rolling_high','drop_pct','elapsed_from_peak',
                                       'velocity','signal'])
    w.writeheader()
    w.writerows(rows)

# Write summary CSV
open_px = rth[0][1]['o']
high_px = max(b['h'] for _, b in rth)
low_px = min(b['l'] for _, b in rth)
last_px = rth[-1][1]['c']
last_time = rth[-1][0].strftime('%H:%M')

summary_path = os.path.join(SCRIPT_DIR, f'today_{today}_summary.csv')
with open(summary_path, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['metric', 'value'])
    w.writerow(['date', today])
    w.writerow(['ticker_used', used_ticker])
    w.writerow(['rth_bars', len(rth)])
    w.writerow(['open', open_px])
    w.writerow(['high', high_px])
    w.writerow(['low', low_px])
    w.writerow(['last', last_px])
    w.writerow(['last_bar_time', last_time])
    w.writerow(['range_pts', round(high_px - low_px, 2)])
    w.writerow(['max_drop_pct', round(max_drop, 4)])
    w.writerow(['max_velocity', round(max_vel, 5)])
    w.writerow(['vix_open', vix_open or ''])
    w.writerow(['vix_high', vix_high or ''])
    w.writerow(['vix_low', vix_low or ''])
    w.writerow(['vix_close', vix_close or ''])
    # Count signals
    pf_count = sum(1 for r in rows if r['signal'] == 'PANIC_FADE')
    db_count = sum(1 for r in rows if r['signal'] == 'DIP_BUY')
    w.writerow(['panic_fade_signals', pf_count])
    w.writerow(['dip_buy_signals', db_count])
    if max_drop < 0.30:
        w.writerow(['verdict', f'Never reached 0.30% drop (max {max_drop:.3f}%) — no signals possible'])
    elif max_vel < 0.05:
        w.writerow(['verdict', f'Drop reached {max_drop:.3f}% but velocity never hit 0.05%/min'])
    elif pf_count > 0:
        w.writerow(['verdict', f'PANIC FADE triggered {pf_count} times'])
    elif db_count > 0:
        w.writerow(['verdict', f'DIP BUY triggered {db_count} times'])
    else:
        w.writerow(['verdict', 'Drop/velocity combo did not meet thresholds'])

print(f"\nWrote {len(rows)} bars to: {out_path}")
print(f"Wrote summary to: {summary_path}")
print("Done.")
