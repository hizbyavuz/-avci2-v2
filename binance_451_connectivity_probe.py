import json, urllib.request, urllib.error, time
urls={
 "futures_time":"https://fapi.binance.com/fapi/v1/time",
 "futures_exchange_info":"https://fapi.binance.com/fapi/v1/exchangeInfo",
 "futures_open_interest":"https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT",
 "spot_time":"https://api.binance.com/api/v3/time",
}
for name,url in urls.items():
 try:
  req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; connectivity-probe/1.0)"})
  with urllib.request.urlopen(req,timeout=9) as response:
   print("BINANCE_451_PROBE",json.dumps({"endpoint":name,"http_status":response.status,"ok":response.status==200}),flush=True)
 except urllib.error.HTTPError as e:
  print("BINANCE_451_PROBE",json.dumps({"endpoint":name,"http_status":e.code,"ok":False}),flush=True)
 except Exception as e:
  print("BINANCE_451_PROBE",json.dumps({"endpoint":name,"error":type(e).__name__,"ok":False}),flush=True)
 time.sleep(1)
print("BINANCE_451_PROBE_DONE",flush=True)
