#!/usr/bin/env python3
"""Isolated native Binance USD-M market-wide Futures observer.
No orders, Telegram, or modification of the frozen Long/Short V3.1 motor.
"""
import argparse
import json
import math
import re
import time
from pathlib import Path
import websocket

STREAM="wss://fstream.binance.com/market/ws/!ticker@arr"
VERSION="NATIVE_BINANCE_UM_MARKET_WATCH_V1"
SYM=re.compile(r"^[A-Z0-9]{2,40}USDT$")
WINDOWS={5:100000,15:160000,30:180000}

def parse_tickers(obj,now_ms):
    if isinstance(obj,dict):obj=obj.get("data",obj)
    if not isinstance(obj,list):return []
    valid=[]
    for x in obj:
        if not isinstance(x,dict) or x.get("e")!="24hrTicker" or x.get("st")!=1:continue
        s=x.get("s")
        if not isinstance(s,str) or not SYM.fullmatch(s):continue
        try:t=int(x["E"]);p=float(x["c"]);q=float(x.get("q") or 0)
        except (ValueError,TypeError,KeyError):continue
        if math.isfinite(p) and p>0 and math.isfinite(q) and q>=0 and -5000<=now_ms-t<45000:
            valid.append((s,t,p,q))
    return valid

def add(hist,s,t,p):
    xs=hist.setdefault(s,[])
    if not xs or t>xs[-1][0]:xs.append([t,p])
    while len(xs)>2 and xs[1][0]<t-7200000:xs.pop(0)

def metrics(hist,latest,now):
    output={}
    for mins,tolerance in WINDOWS.items():
        d={"comparable":0,"missing_baseline":0,"up3":0,"down3":0,
           "up5":0,"down5":0,"up10":0,"down10":0,"strongest_up":[],"strongest_down":[]}
        for sym,(last,price,vol) in latest.items():
            if not 0<=now-last<30000:continue
            target=last-mins*60000
            earlier=[x for x in hist.get(sym,[]) if x[0]<=target and target-x[0]<=tolerance]
            if not earlier:
                d["missing_baseline"]+=1
                continue
            t0,p0=earlier[-1]
            if p0<=0:continue
            pct=(price/p0-1)*100
            d["comparable"]+=1
            for level in (3,5,10):
                if pct>=level:d["up"+str(level)]+=1
                if pct<=-level:d["down"+str(level)]+=1
            item={"symbol":sym,"pct":round(pct,3),"actual_seconds":round((last-t0)/1000),
                  "last_price":price,"source":"BINANCE_FUTURES_UM_WS"}
            d["strongest_up" if pct>=0 else "strongest_down"].append(item)
        d["strongest_up"]=sorted(d["strongest_up"],key=lambda x:-x["pct"])[:10]
        d["strongest_down"]=sorted(d["strongest_down"],key=lambda x:x["pct"])[:10]
        output[str(mins)]=d
    return output

def restore(path):
    if not path.is_file():return {}
    try:
        content=json.loads(path.read_text())
        if content.get("version")!=VERSION:return {}
        return {s:[[int(t),float(p)] for t,p in rows if float(p)>0][-240:]
                for s,rows in content["history"].items() if SYM.fullmatch(s) and isinstance(rows,list)}
    except (OSError,ValueError,KeyError,TypeError):
        return {}

def run(seconds,state,output):
    hist=restore(state)
    latest={}
    seen=set()
    issues=[]
    count=0
    ws=None
    try:
        ws=websocket.create_connection(STREAM,timeout=10,enable_multithread=True)
        deadline=time.monotonic()+seconds
        snapshot=time.monotonic()+min(seconds,9)
        while time.monotonic()<deadline:
            try:
                ws.settimeout(max(0.2,min(3.0,deadline-time.monotonic())))
                data=json.loads(ws.recv())
                for s,t,p,q in parse_tickers(data,int(time.time()*1000)):
                    if s not in latest or t>latest[s][0]:
                        latest[s]=(t,p,q)
                        seen.add(s)
                        count+=1
            except websocket.WebSocketTimeoutException:pass
            except (ValueError,TypeError):issues.append("MALFORMED_WS_EVENT")
            if time.monotonic()>=snapshot:
                now=int(time.time()*1000)
                for s,(t,p,q) in latest.items():
                    if 0<=now-t<30000:add(hist,s,t,p)
                snapshot=time.monotonic()+20
    except Exception as e:
        issues.append("CONNECTION_"+type(e).__name__+":"+str(e)[:100])
    finally:
        if ws:
            try:ws.close()
            except Exception:pass
    now=int(time.time()*1000)
    fresh={s:r for s,r in latest.items() if 0<=now-r[0]<30000}
    for s,(t,p,q) in fresh.items():add(hist,s,t,p)
    hist={s:rows for s,rows in hist.items() if rows and rows[-1][0]>=now-7200000}
    if len(fresh)<50:issues.append("INSUFFICIENT_MARKET_COVERAGE")
    report={"version":VERSION,"source":"BINANCE_FUTURES_UM_WS","read_only":True,
        "timestamp_ms":now,"observed_usdm_usdt":len(seen),"fresh_usdm_usdt":len(fresh),
        "events":count,"stored_symbols":len(hist),"issues":sorted(set(issues)),
        "windows":metrics(hist,fresh,now),
        "scope":"Changed USD-M Futures USDT tickers only. Without exchangeInfo, cannot prove complete active-perpetual membership. Comparisons show ACTUAL elapsed seconds, and missing history is NOT filled."}
    state.parent.mkdir(parents=True,exist_ok=True)
    state.write_text(json.dumps({"version":VERSION,"history":hist},separators=(",",":")))
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print("NATIVE_BINANCE_UM","observed",len(seen),"fresh",len(fresh),"events",count,"issues",report["issues"])
    for window,r in report["windows"].items():
        print("WINDOW",window,"up5",r["up5"],"down5",r["down5"],"up10",r["up10"],"down10",r["down10"],"comparable",r["comparable"],"missing",r["missing_baseline"])

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=int,default=90)
    parser.add_argument("--state",default="native_um_market_watch_state.json")
    parser.add_argument("--output",default="native_um_market_watch_report.json")
    a=parser.parse_args()
    if not 5<=a.seconds<=150:parser.error("seconds must be 5..150")
    run(a.seconds,Path(a.state),Path(a.output))
