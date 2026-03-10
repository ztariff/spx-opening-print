"""
Find the exact start date for I:SPX and I:VIX data on Polygon.
Uses binary search by month, then narrows to exact date.
"""
import requests, os, time

API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
output = []

def log(msg):
    print(msg, flush=True)
    output.append(msg)

def has_data(ticker, date_from, date_to, timespan="day"):
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/{timespan}/{date_from}/{date_to}"
           f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
    time.sleep(0.05)
    r = requests.get(url, timeout=30)
    d = r.json()
    # Index tickers use 'queryCount', equities use 'resultsCount'
    count = d.get("queryCount", d.get("resultsCount", 0))
    return count > 0, count

# ── Find I:SPX daily data start ──
log("=== Finding I:SPX DAILY data start ===")
# Test month by month from 2020-01 to 2023-06
for year in range(2020, 2024):
    for month in range(1, 13):
        date_from = f"{year}-{month:02d}-01"
        date_to = f"{year}-{month:02d}-28"
        found, count = has_data("I:SPX", date_from, date_to, "day")
        log(f"  {date_from} to {date_to}: {'YES' if found else 'no'} (count={count})")
        if found:
            # Found the month - now find exact date
            log(f"\n  Narrowing down within {year}-{month:02d}...")
            for day in range(1, 29):
                d = f"{year}-{month:02d}-{day:02d}"
                found2, count2 = has_data("I:SPX", d, d, "day")
                if found2:
                    log(f"  FIRST DATE WITH DATA: {d}")
                    break
            break
    else:
        continue
    break

# ── Find I:SPX 1-min data start ──
log("\n=== Finding I:SPX 1-MIN data start ===")
for year in range(2020, 2024):
    for month in range(1, 13):
        date_from = f"{year}-{month:02d}-01"
        date_to = f"{year}-{month:02d}-28"
        found, count = has_data("I:SPX", date_from, date_to, "minute")
        log(f"  {date_from} to {date_to}: {'YES' if found else 'no'} (count={count})")
        if found:
            log(f"\n  Narrowing down within {year}-{month:02d}...")
            for day in range(1, 29):
                d = f"{year}-{month:02d}-{day:02d}"
                found2, count2 = has_data("I:SPX", d, d, "minute")
                if found2:
                    log(f"  FIRST DATE WITH DATA: {d}")
                    break
            break
    else:
        continue
    break

# ── Find I:VIX daily data start ──
log("\n=== Finding I:VIX DAILY data start ===")
for year in range(2020, 2024):
    for month in range(1, 13):
        date_from = f"{year}-{month:02d}-01"
        date_to = f"{year}-{month:02d}-28"
        found, count = has_data("I:VIX", date_from, date_to, "day")
        log(f"  {date_from} to {date_to}: {'YES' if found else 'no'} (count={count})")
        if found:
            log(f"\n  Narrowing down within {year}-{month:02d}...")
            for day in range(1, 29):
                d = f"{year}-{month:02d}-{day:02d}"
                found2, count2 = has_data("I:VIX", d, d, "day")
                if found2:
                    log(f"  FIRST DATE WITH DATA: {d}")
                    break
            break
    else:
        continue
    break

# ── Find I:VIX 1-min data start ──
log("\n=== Finding I:VIX 1-MIN data start ===")
for year in range(2020, 2024):
    for month in range(1, 13):
        date_from = f"{year}-{month:02d}-01"
        date_to = f"{year}-{month:02d}-28"
        found, count = has_data("I:VIX", date_from, date_to, "minute")
        log(f"  {date_from} to {date_to}: {'YES' if found else 'no'} (count={count})")
        if found:
            log(f"\n  Narrowing down within {year}-{month:02d}...")
            for day in range(1, 29):
                d = f"{year}-{month:02d}-{day:02d}"
                found2, count2 = has_data("I:VIX", d, d, "minute")
                if found2:
                    log(f"  FIRST DATE WITH DATA: {d}")
                    break
            break
    else:
        continue
    break

# Save
out_path = os.path.join(SCRIPT_DIR, "ticker_cutoff_results.txt")
with open(out_path, "w") as f:
    f.write("\n".join(output))
log(f"\nResults saved to {out_path}")
