"""
Debug script: Figure out why I:SPX and I:VIX return 0 results from Polygon.
Prints full API responses to diagnose the issue.
"""
import requests, json, os

API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
output = []

def log(msg):
    print(msg, flush=True)
    output.append(msg)

def test_url(label, url):
    """Fetch URL and print full response details."""
    log(f"\n  [{label}]")
    log(f"  URL: {url}")
    try:
        r = requests.get(url, timeout=30)
        log(f"  HTTP Status: {r.status_code}")
        d = r.json()
        # Print key fields
        log(f"  ticker: {d.get('ticker')}")
        log(f"  status: {d.get('status')}")
        log(f"  resultsCount: {d.get('resultsCount', 'N/A')}")
        log(f"  queryCount: {d.get('queryCount', 'N/A')}")
        log(f"  request_id: {d.get('request_id', 'N/A')}")
        if d.get("results"):
            log(f"  First result: {json.dumps(d['results'][0])}")
        # Print full response if small
        full = json.dumps(d, indent=2)
        if len(full) < 1500:
            log(f"  Full response:\n{full}")
        else:
            log(f"  (Response too large, {len(full)} chars)")
    except Exception as e:
        log(f"  ERROR: {e}")
        log(f"  Raw response: {r.text[:500] if 'r' in dir() else 'N/A'}")

# ── Test 1: I:SPX for a RECENT date we know has data ──
log("=" * 80)
log("TEST 1: I:SPX on recent date (2026-03-03) - we know this data exists in our CSV")
log("=" * 80)

test_url("I:SPX 1-min 2026-03-03",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/minute/2026-03-03/2026-03-03"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("I:SPX daily 2026-03-03",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Test 2: Same dates but with SPY (known to work) ──
log("\n" + "=" * 80)
log("TEST 2: SPY on same date (control test)")
log("=" * 80)

test_url("SPY 1-min 2026-03-03",
    f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/minute/2026-03-03/2026-03-03"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Test 3: URL encoding variants for I:SPX ──
log("\n" + "=" * 80)
log("TEST 3: Different URL encodings for SPX index")
log("=" * 80)

# Maybe the colon needs URL encoding?
test_url("I%3ASPX (URL-encoded colon)",
    f"https://api.polygon.io/v2/aggs/ticker/I%3ASPX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("SPX (no prefix)",
    f"https://api.polygon.io/v2/aggs/ticker/SPX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("$SPX.X",
    f"https://api.polygon.io/v2/aggs/ticker/$SPX.X/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Test 4: VIX variants ──
log("\n" + "=" * 80)
log("TEST 4: VIX ticker variants")
log("=" * 80)

test_url("I:VIX daily 2026-03-01 to 2026-03-05",
    f"https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("I%3AVIX (URL-encoded)",
    f"https://api.polygon.io/v2/aggs/ticker/I%3AVIX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("VIX (no prefix)",
    f"https://api.polygon.io/v2/aggs/ticker/VIX/range/1/day/2026-03-01/2026-03-05"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Test 5: Check subscription / entitlement info ──
log("\n" + "=" * 80)
log("TEST 5: Check Polygon subscription info")
log("=" * 80)

# Ticker details endpoint - shows what data is available
test_url("Ticker details for I:SPX",
    f"https://api.polygon.io/v3/reference/tickers/I:SPX?apiKey={API_KEY}")

test_url("Ticker details for I:VIX",
    f"https://api.polygon.io/v3/reference/tickers/I:VIX?apiKey={API_KEY}")

# Search for SPX tickers
test_url("Search for 'SPX' tickers",
    f"https://api.polygon.io/v3/reference/tickers?search=SPX&type=index&limit=10&apiKey={API_KEY}")

# ── Test 6: Snapshot endpoint (different API) ──
log("\n" + "=" * 80)
log("TEST 6: Snapshot endpoints")
log("=" * 80)

test_url("Index snapshot I:SPX",
    f"https://api.polygon.io/v3/snapshot/indices/I:SPX?apiKey={API_KEY}")

# ── Test 7: Historical dates that should work ──
log("\n" + "=" * 80)
log("TEST 7: I:SPX for 2023-02-14 (first date in our existing CSV)")
log("=" * 80)

test_url("I:SPX 1-min 2023-02-14",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/minute/2023-02-14/2023-02-14"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("I:SPX daily 2023-02-14",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/day/2023-02-14/2023-02-14"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Test 8: I:SPX for 2020 (the date that failed in script 16) ──
log("\n" + "=" * 80)
log("TEST 8: I:SPX for historical date 2020-03-16")
log("=" * 80)

test_url("I:SPX 1-min 2020-03-16",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/minute/2020-03-16/2020-03-16"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

test_url("I:SPX daily 2020-03-16",
    f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/1/day/2020-03-16/2020-03-16"
    f"?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}")

# ── Save ──
out_path = os.path.join(SCRIPT_DIR, "ticker_debug_results.txt")
with open(out_path, "w") as f:
    f.write("\n".join(output))
log(f"\nResults saved to {out_path}")
