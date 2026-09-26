#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only real execution evidence collector.

No order placement endpoints are used.

Binance:
- Reads candidate symbols from the research DB.
- Uses read-only signed GET /api/v3/myTrades when BINANCE_API_KEY and
  BINANCE_API_SECRET are available.
- Matches a trade to an Avci event only when exactly one candidate event has an
  eligible entry window for that symbol. Ambiguous trades remain unmatched.
- Never fabricates round-trip realized cost from a one-sided fill.

Gate Web3 / Solana:
- Uses the public wallet address plus Helius enhanced transaction history.
- Requires HELIUS_API_KEY and GATE_WEB3_SOLANA_ADDRESS.
- Archives SWAP-like transactions as raw execution evidence; no private key is
  ever needed and no transaction is sent.

Research-only.
"""
from __future__ import annotations
import csv, hashlib, hmac, json, os, sqlite3, time
from datetime import datetime, timezone
from urllib.parse import urlencode
import requests

BDB=os.getenv("BINANCE_DB","binance_avci2.db")
OUT=os.getenv("READONLY_FILL_OUT","read_only_execution_observations.csv")
WEB3_OUT=os.getenv("WEB3_EXECUTION_OUT","web3_execution_observations.json")
REPORT=os.getenv("READONLY_EXEC_REPORT","read_only_execution_report.json")
BKEY=os.getenv("BINANCE_API_KEY","").strip()
BSEC=os.getenv("BINANCE_API_SECRET","").strip()
HELIUS=os.getenv("HELIUS_API_KEY","").strip()
SOL_ADDR=os.getenv("GATE_WEB3_SOLANA_ADDRESS","").strip()

FIELDS=["source","event_key","asset","event_time_utc","side","quantity","expected_price",
        "filled_price","fee_pct","slippage_pct","total_realized_cost_pct",
        "external_order_id","notes"]

def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None

def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def binance_events():
    if not os.path.exists(BDB):return []
    with sqlite3.connect(BDB) as c:
        c.row_factory=sqlite3.Row
        if not table(c,"signal_events"):return []
        return [dict(r) for r in c.execute("""SELECT event_id,symbol,signal_time_utc,
          entry_delay_seconds,entry_time_utc,entry_price_raw,entry_price_exec,event_class
          FROM signal_events WHERE event_class='CANDIDATE'
          ORDER BY signal_time_utc DESC LIMIT 5000""")]

def signed_get(path,params):
    params=dict(params)
    params["timestamp"]=int(time.time()*1000)
    params["recvWindow"]=5000
    qs=urlencode(params)
    sig=hmac.new(BSEC.encode(),qs.encode(),hashlib.sha256).hexdigest()
    url=f"https://api.binance.com{path}?{qs}&signature={sig}"
    r=requests.get(url,headers={"X-MBX-APIKEY":BKEY},timeout=20)
    r.raise_for_status()
    return r.json()

def match_event(events,symbol,trade_ms):
    t=datetime.fromtimestamp(trade_ms/1000,timezone.utc)
    eligible=[]
    for e in events:
        if e["symbol"]!=symbol:continue
        st=dt(e["signal_time_utc"])
        if not st:continue
        delay=float(e.get("entry_delay_seconds") or 0)
        age=(t-st).total_seconds()
        if delay<=age<=72*3600:
            eligible.append(e)
    return eligible[0] if len(eligible)==1 else None

def collect_binance(report,rows):
    if not (BKEY and BSEC):
        report["binance"]="WAITING_GITHUB_SECRETS_BINANCE_API_KEY_AND_SECRET"
        return
    events=binance_events()
    symbols=sorted({e["symbol"] for e in events})[:250]
    raw_n=matched=ambiguous=0
    for symbol in symbols:
        try:
            trades=signed_get("/api/v3/myTrades",{"symbol":symbol,"limit":1000})
        except Exception as e:
            report.setdefault("binance_errors",[]).append(f"{symbol}:{type(e).__name__}:{str(e)[:100]}")
            continue
        for tr in trades if isinstance(trades,list) else []:
            raw_n+=1
            ev=match_event(events,symbol,int(tr.get("time") or 0))
            if ev is None:
                ambiguous+=1
                continue
            side="BUY" if tr.get("isBuyer") else "SELL"
            price=float(tr["price"]);qty=float(tr["qty"])
            quote=float(tr.get("quoteQty") or price*qty)
            commission=float(tr.get("commission") or 0)
            ca=str(tr.get("commissionAsset") or "")
            fee_quote=(commission if ca=="USDT" else commission*price if ca==symbol.removesuffix("USDT") else None)
            fee_pct=(100*fee_quote/quote) if fee_quote is not None and quote>0 else None
            expected=(ev.get("entry_price_raw") or ev.get("entry_price_exec")) if side=="BUY" else None
            slip=(100*(price/float(expected)-1)) if expected and float(expected)>0 and side=="BUY" else None
            rows.append({
              "source":"BINANCE","event_key":ev["event_id"],"asset":symbol,
              "event_time_utc":datetime.fromtimestamp(int(tr["time"])/1000,timezone.utc).isoformat(),
              "side":side,"quantity":qty,"expected_price":expected,"filled_price":price,
              "fee_pct":fee_pct,"slippage_pct":slip,
              "total_realized_cost_pct":"",
              "external_order_id":str(tr.get("orderId") or tr.get("id") or ""),
              "notes":"Read-only Binance myTrades. One-sided fill; total round-trip realized cost intentionally left blank."
            })
            matched+=1
    report["binance"]={"candidate_symbols":len(symbols),"raw_trades":raw_n,
                       "matched_unambiguous":matched,"unmatched_or_ambiguous":ambiguous}

def collect_web3(report):
    if not (HELIUS and SOL_ADDR):
        report["gate_web3"]="WAITING_GITHUB_SECRET_GATE_WEB3_SOLANA_ADDRESS_OR_HELIUS_API_KEY"
        return []
    url=f"https://api.helius.xyz/v0/addresses/{SOL_ADDR}/transactions"
    out=[];before=None
    for _ in range(10):
        params={"api-key":HELIUS,"limit":100}
        if before:params["before"]=before
        r=requests.get(url,params=params,timeout=30);r.raise_for_status()
        data=r.json()
        if not isinstance(data,list) or not data:break
        for tx in data:
            typ=str(tx.get("type") or "").upper()
            desc=str(tx.get("description") or "")
            if typ=="SWAP" or "swap" in desc.lower():
                out.append({
                  "signature":tx.get("signature"),"timestamp":tx.get("timestamp"),
                  "type":tx.get("type"),"source":tx.get("source"),
                  "fee":tx.get("fee"),"feePayer":tx.get("feePayer"),
                  "tokenTransfers":tx.get("tokenTransfers") or [],
                  "nativeTransfers":tx.get("nativeTransfers") or [],
                  "description":desc
                })
        before=data[-1].get("signature")
        if not before:break
    report["gate_web3"]={"wallet":SOL_ADDR,"swap_transactions":len(out),
                         "truth_class":"ONCHAIN_REAL_EXECUTION_EVIDENCE"}
    return out

def main():
    report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "no_order_placement":True}
    rows=[]
    try:collect_binance(report,rows)
    except Exception as e:report["binance_error"]=f"{type(e).__name__}:{e}"
    try:web3=collect_web3(report)
    except Exception as e:
        web3=[];report["gate_web3_error"]=f"{type(e).__name__}:{e}"
    with open(OUT,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    with open(WEB3_OUT,"w",encoding="utf-8") as f:json.dump(web3,f,ensure_ascii=False,indent=2)
    with open(REPORT,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
