"""One-shot, read-only Binance HTTP accessibility probe for Railway.
No API keys, trading, Telegram, or persistent storage.
"""
import json
import urllib.error
import urllib.request

URLS = {
    "binance_futures": "https://fapi.binance.com/fapi/v1/time",
    "binance_spot": "https://api.binance.com/api/v3/time",
    "gate_futures_primary": "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT",
    "gate_futures_alternative": "https://fx-api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT",
}
for label, url in URLS.items():
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "avci-451-diagnostic/1.0"})
        with urllib.request.urlopen(request, timeout=8) as response:
            print("HTTP_PROBE", json.dumps({"source": label, "status": response.status, "accessible": response.status == 200}), flush=True)
    except urllib.error.HTTPError as exc:
        print("HTTP_PROBE", json.dumps({"source": label, "status": exc.code, "accessible": False}), flush=True)
    except Exception as exc:
        print("HTTP_PROBE", json.dumps({"source": label, "status": "ERROR", "error_type": type(exc).__name__, "accessible": False}), flush=True)
