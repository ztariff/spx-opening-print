"""Quick debug script to find the correct SPX options query on Polygon."""
import json
import urllib.request

API_KEY = "cBE5Kbq9yllt0Yj29mDQjBcIKfAYQlHF"
BASE = "https://api.polygon.io"
DATE = "2024-12-02"  # A known trading day

# Try different underlying tickers and approaches
tests = [
    # Direct contract search with different underlyings
    f"{BASE}/v3/reference/options/contracts?underlying_ticker=SPX&expiration_date={DATE}&contract_type=call&limit=5&apiKey={API_KEY}",
    f"{BASE}/v3/reference/options/contracts?underlying_ticker=SPXW&expiration_date={DATE}&contract_type=call&limit=5&apiKey={API_KEY}",
    f"{BASE}/v3/reference/options/contracts?underlying_ticker=I:SPX&expiration_date={DATE}&contract_type=call&limit=5&apiKey={API_KEY}",
    f"{BASE}/v3/reference/options/contracts?underlying_ticker=SPY&expiration_date={DATE}&contract_type=call&limit=5&apiKey={API_KEY}",
    # Try searching by ticker prefix
    f"{BASE}/v3/reference/options/contracts?underlying_ticker.gte=SPX&underlying_ticker.lte=SPXZ&expiration_date={DATE}&contract_type=call&limit=5&apiKey={API_KEY}",
    # Try a known option ticker format directly for bars
    f"{BASE}/v2/aggs/ticker/O:SPX241202C05900000/range/1/minute/{DATE}/{DATE}?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}",
    f"{BASE}/v2/aggs/ticker/O:SPXW241202C05900000/range/1/minute/{DATE}/{DATE}?adjusted=true&sort=asc&limit=5&apiKey={API_KEY}",
    # Try snapshot endpoint
    f"{BASE}/v3/snapshot/options/SPX?apiKey={API_KEY}",
]

for url in tests:
    # Mask the API key in output
    display_url = url.replace(API_KEY, "***")
    print(f"\n{'='*70}")
    print(f"GET {display_url}")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            status = data.get("status", "?")
            count = data.get("resultsCount", data.get("count", len(data.get("results", []))))
            print(f"  Status: {status}, Results: {count}")
            if data.get("results"):
                # Show first result
                print(f"  First result: {json.dumps(data['results'][0], indent=2)[:500]}")
            else:
                print(f"  Response keys: {list(data.keys())}")
                if "error" in data:
                    print(f"  Error: {data['error']}")
                if "message" in data:
                    print(f"  Message: {data['message']}")
    except Exception as e:
        print(f"  ERROR: {e}")
