"""Read-only Gate USDT perpetual API connectivity test. No credentials, DB, Telegram or orders."""
import json, time, urllib.request, urllib.error
BASE="https://api.gateio.ws/api/v4/futures/usdt"
TESTS=[
 ("contracts", "/contracts?limit=1"),
 ("candlesticks", "/candlesticks?contract=BTC_USDT&interval=5m&limit=2"),
 ("contract_stats", "/contract_stats?contract=BTC_USDT&interval=5m&limit=2"),
 ("order_book", "/order_book?contract=BTC_USDT&limit=5"),
 ("tickers", "/tickers?contract=BTC_USDT"),
]
for name,path in TESTS:
 start=time.monotonic()
 try:
  req=urllib.request.Request(BASE+path,headers={"Accept":"application/json","User-Agent":"AvciGateReadOnlyProbe/1.0"})
  with urllib.request.urlopen(req,timeout=12) as response:
   payload=json.loads(response.read(500000))
   shape=type(payload).__name__
   count=len(payload) if isinstance(payload,list) else None
   print("GATE_V4_PROBE "+json.dumps({"endpoint":name,"http":response.status,"json_type":shape,"items":count,"elapsed_ms":round((time.monotonic()-start)*1000),"ok":response.status==200 and isinstance(payload,(list,dict))}),flush=True)
 except urllib.error.HTTPError as exc:
  print("GATE_V4_PROBE "+json.dumps({"endpoint":name,"http":exc.code,"ok":False,"elapsed_ms":round((time.monotonic()-start)*1000)}),flush=True)
 except Exception as exc:
  print("GATE_V4_PROBE "+json.dumps({"endpoint":name,"error":type(exc).__name__,"ok":False,"elapsed_ms":round((time.monotonic()-start)*1000)}),flush=True)
print("GATE_V4_PROBE_COMPLETE",flush=True)
