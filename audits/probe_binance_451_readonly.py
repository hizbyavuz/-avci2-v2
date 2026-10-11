"""One-shot, read-only Binance Futures 451 connectivity check. No credentials."""
import json
import urllib.error
import urllib.request

URLS = {
    "futures_exchange_info": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "futures_klines": "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=2",
    "spot_exchange_info": "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
}
for name, url in URLS.items():
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Avci451ReadOnlyProbe/1.0"})
        with urllib.request.urlopen(req, timeout=8) as response:
            response.read(512)
            print("CONNECTIVITY_PROBE", json.dumps({"endpoint":name,"http_status":response.status,"accessible":True}), flush=True)
    except urllib.error.HTTPError as exc:
        print("CONNECTIVITY_PROBE", json.dumps({"endpoint":name,"http_status":exc.code,"accessible":False}), flush=True)
    except Exception as exc:
        print("CONNECTIVITY_PROBE", json.dumps({"endpoint":name,"error_type":type(exc).__name__,"accessible":False}), flush=True)
