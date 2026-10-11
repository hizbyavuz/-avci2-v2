"""Read-only Gate Futures field coverage check, not Binance venue parity."""
import json
import urllib.error
import urllib.request

BASE = "https://api.gateio.ws/api/v4"
URLS = {
    "futures_contract": "/futures/usdt/contracts/BTC_USDT",
    "futures_ticker": "/futures/usdt/tickers?contract=BTC_USDT",
    "futures_candles": "/futures/usdt/candlesticks?contract=BTC_USDT&interval=1m&limit=2",
    "futures_trades": "/futures/usdt/trades?contract=BTC_USDT&limit=2",
    "futures_orderbook": "/futures/usdt/order_book?contract=BTC_USDT&limit=5",
    "futures_stats": "/futures/usdt/contract_stats?contract=BTC_USDT&interval=5m&limit=2",
}
for label, path in URLS.items():
    try:
        req = urllib.request.Request(BASE + path, headers={"User-Agent":"avci-451-diagnostic/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            obj = json.load(response)
            sample = obj[0] if isinstance(obj, list) and obj else obj
            keys = sorted(sample.keys()) if isinstance(sample, dict) else []
            print("FIELD_PROBE", json.dumps({"source":label,"http_status":response.status,
                "records":len(obj) if isinstance(obj,list) else 1,"fields":keys[:50]}), flush=True)
    except urllib.error.HTTPError as exc:
        print("FIELD_PROBE", json.dumps({"source":label,"http_status":exc.code}), flush=True)
    except Exception as exc:
        print("FIELD_PROBE", json.dumps({"source":label,"error_type":type(exc).__name__}), flush=True)
