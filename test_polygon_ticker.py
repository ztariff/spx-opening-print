"""Test what historical data Polygon has for SPX/SPY/VIX."""
import requests, json, os

API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
output = []

def log(msg):
    print(msg, flush=True)
    output.append(msg)

# Test SPY and other ETFs for historical 1-min data
log("=== 1-MIN BARS on 2020-03-16 ===")
for ticker in ["SPY", "I:SPX", "VIXY", "UVXY"]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/minute/2020-03-16/2020-03-16"
           f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {ticker:12s} -> results={count}")
    if d.get("results") and count > 0:
        log(f"    First bar: o={d['results'][0].get('o')} c={d['results'][0].get('c')}")

# Test SPY daily going back
log("\n=== DAILY BARS on 2018-01-02 ===")
for ticker in ["SPY", "I:SPX", "I:VIX", "TLT"]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/day/2018-01-02/2018-01-05"
           f"?adjusted=true&sort=asc&limit=10&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {ticker:12s} -> results={count}")
    if d.get("results") and count > 0:
        log(f"    First: date={d['results'][0].get('t')} o={d['results'][0].get('o')}")

# Find when I:SPX 1-min data starts by testing year by year
log("\n=== FINDING WHEN I:SPX 1-MIN DATA STARTS ===")
for year in [2018, 2019, 2020, 2021, 2022, 2023]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/I:SPX"
           f"/range/1/minute/{year}-06-15/{year}-06-15"
           f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {year}-06-15: results={count}")

# Find when I:SPX daily data starts
log("\n=== FINDING WHEN I:SPX DAILY DATA STARTS ===")
for year in [2018, 2019, 2020, 2021, 2022, 2023]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/I:SPX"
           f"/range/1/day/{year}-01-01/{year}-01-31"
           f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {year}-01: results={count}")

# Same for I:VIX daily
log("\n=== FINDING WHEN I:VIX DAILY DATA STARTS ===")
for year in [2018, 2019, 2020, 2021, 2022, 2023]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/I:VIX"
           f"/range/1/day/{year}-01-01/{year}-01-31"
           f"?adjusted=true&sort=asc&limit=3&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {year}-01: results={count}")

# Test VIX ETFs for daily
log("\n=== VIX PROXY DAILY on 2018-01-02 ===")
for ticker in ["VIXY", "UVXY", "VXX", "VIXM"]:
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/day/2018-01-02/2018-01-05"
           f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")
    r = requests.get(url)
    d = r.json()
    count = d.get("resultsCount", 0)
    log(f"  {ticker:12s} -> results={count}")

# Save results
out_path = os.path.join(SCRIPT_DIR, "ticker_test_results.txt")
with open(out_path, "w") as f:
    f.write("\n".join(output))
log(f"\nResults saved to ticker_test_results.txt")
