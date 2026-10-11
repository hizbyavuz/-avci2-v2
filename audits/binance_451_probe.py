"""One-shot, read-only Binance connectivity probe. No API keys or trading."""
import json, time, urllib.request, urllib.error, socket
TARGETS = [
 ("futures_time", "https://fapi.binance.com/fapi/v1/time"),
 ("futures_exchange_info", "https://fapi.binance.com/fapi/v1/exchangeInfo"),
 ("futures_funding", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"),
 ("spot_control", "https://api.binance.com/api/v3/time"),
]
for name, url in TARGETS:
    start=time.monotonic()
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"AvciConnectivityProbe/1.0"})
        with urllib.request.urlopen(req,timeout=8) as response:
            sample=response.read(160)
            print("BINANCE_451_PROBE",json.dumps({"target":name,"status":response.status,"elapsed_ms":round((time.monotonic()-start)*1000),"json":sample.lstrip().startswith((b"{",b"["))}),flush=True)
    except urllib.error.HTTPError as exc:
        print("BINANCE_451_PROBE",json.dumps({"target":name,"status":exc.code,"elapsed_ms":round((time.monotonic()-start)*1000),"error":"HTTPError"}),flush=True)
    except Exception as exc:
        print("BINANCE_451_PROBE",json.dumps({"target":name,"status":None,"elapsed_ms":round((time.monotonic()-start)*1000),"error":type(exc).__name__}),flush=True)
print("BINANCE_451_PROBE_COMPLETE",flush=True)
