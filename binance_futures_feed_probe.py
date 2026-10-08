#!/usr/bin/env python3
"""Read-only, public Binance Futures market-data connectivity probe.

Run on the SAME GitHub-hosted runner as the production Long/Short analyst.
Never uses keys, private streams, orders, geo spoofing, or proxy overrides.
Only validates real Binance Futures symbols and timestamps; never relabels Spot
or another exchange as Binance Futures. Emits a machine-readable report.
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
import websocket

VERSION="BINANCE_FUTURES_CONNECTIVITY_PROBE_V1"
HOSTS=(
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
)
# Official new March 2026 Futures WebSocket routes and legacy stream.
STREAMS=(
    ("MARKET_2026", "wss://fstream.binance.com/market/ws/btcusdt@kline_1m", "kline"),
    ("PUBLIC_2026", "wss://fstream.binance.com/public/ws/btcusdt@aggTrade", "aggTrade"),
    ("LEGACY", "wss://fstream.binance.com/ws/btcusdt@kline_1m", "kline"),
)
def now_ms(): return int(time.time()*1000)

def rest_check(host):
    result={"route":host,"transport":"REST","usable":False}
    t=time.monotonic()
    try:
        r=requests.get(host+"/fapi/v1/klines",params={
            "symbol":"BTCUSDT","interval":"1m","limit":4},
            timeout=8,headers={"User-Agent":"public-binance-futures-feed-probe/1.0"})
        result["status_code"]=r.status_code
        result["latency_ms"]=round((time.monotonic()-t)*1000)
        if r.status_code!=200:
            result["error"]="HTTP_"+str(r.status_code)
            return result
        bars=r.json()
        if not isinstance(bars,list) or len(bars)<2:
            result["error"]="MALFORMED_FUTURES_KLINES"
            return result
        # Ignore the currently open bar. Binance includes the close timestamp.
        closed=[b for b in bars if isinstance(b,list) and len(b)>=11
                and isinstance(b[6],int) and b[6]<=now_ms()]
        if not closed:
            result["error"]="NO_CLOSED_FUTURES_CANDLE"
            return result
        bar=closed[-1]
        age=(now_ms()-int(bar[6]))/1000
        px=float(bar[4])
        result.update({"candle_close_age_seconds":round(age,1),
           "close_price":px,"candle_open_time_ms":int(bar[0]),
           "candle_close_time_ms":int(bar[6]),"recent_closed_candles":len(closed)})
        result["usable"]=px>0 and 0<=age<180
        if not result["usable"]: result["error"]="STALE_OR_INVALID_FUTURES_CANDLE"
    except requests.RequestException as e:
        result["error"]=type(e).__name__+":"+str(e)[:140]
    except (ValueError,TypeError,KeyError) as e:
        result["error"]="INVALID_BODY:"+type(e).__name__
    result["latency_ms"]=round((time.monotonic()-t)*1000)
    return result

def ws_check(descriptor):
    name,url,kind=descriptor
    result={"route":name,"hostname":urlsplit(url).hostname,"transport":"WEBSOCKET",
            "usable":False}
    t=time.monotonic()
    ws=None
    try:
        # Honor GitHub runner's network configuration; no region/proxy bypass.
        ws=websocket.create_connection(url,timeout=10,enable_multithread=True)
        result["handshake"]=True
        until=time.monotonic()+11
        while time.monotonic()<until:
            raw=ws.recv()
            message=json.loads(raw)
            data=message.get("data",message) if isinstance(message,dict) else {}
            if not isinstance(data,dict):
                continue
            received_kind=data.get("e")
            if kind=="kline" and received_kind!="kline":continue
            if kind=="aggTrade" and received_kind!="aggTrade":continue
            if data.get("s")!="BTCUSDT":continue
            event_time=data.get("E")
            age=(now_ms()-int(event_time))/1000
            if kind=="kline":
                k=data.get("k") or {}
                value=float(k.get("c") or 0)
                valid=(k.get("s")=="BTCUSDT" and k.get("i")=="1m")
            else:
                value=float(data.get("p") or 0)
                valid=(data.get("s")=="BTCUSDT")
            result.update({"event_age_seconds":round(age,2),"last_price":value,
                           "stream_type":received_kind,"symbol":data.get("s")})
            result["usable"]=bool(valid and value>0 and -5<=age<45)
            if not result["usable"]: result["error"]="INVALID_OR_STALE_FUTURES_EVENT"
            break
        if not result.get("stream_type") and "error" not in result:
            result["error"]="NO_MARKET_EVENT"
    except Exception as e:
        result["error"]=type(e).__name__+":"+str(e)[:180]
    finally:
        if ws:
            try:ws.close()
            except Exception:pass
    result["latency_ms"]=round((time.monotonic()-t)*1000)
    return result

def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rest=list(pool.map(rest_check,HOSTS))
        ws=list(pool.map(ws_check,STREAMS))
    data={"version":VERSION,"checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
          "venue":"BINANCE_USDT_FUTURES_NATIVE","read_only":True,
          "no_live_trade_or_telegram_change":True,
          "rest":rest,"websocket":ws,
          "native_rest_available":any(x["usable"] for x in rest),
          "native_ws_available":any(x["usable"] for x in ws)}
    data["recommendation"]=(
        "REST_NATIVE_VALIDATED" if data["native_rest_available"] else
        "WS_NATIVE_VALIDATED_REQUIRES_BUFFERING" if data["native_ws_available"] else
        "NO_NATIVE_FEED_ON_THIS_RUNNER")
    dest=Path(os.getenv("BINANCE_PROBE_OUTPUT","binance_futures_feed_probe.json"))
    dest.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")
    print("FUTURES_FEED_RESULT",data["recommendation"])
    for row in rest+ws:
        print("PROBE",row["route"],"usable="+str(row["usable"]),
              "status="+str(row.get("status_code","")),
              "latency_ms="+str(row.get("latency_ms")),
              "error="+str(row.get("error","none")))

if __name__=="__main__":
    main()
