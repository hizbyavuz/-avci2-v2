#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execution size/slippage curve observer.

Measures actual quote/depth loss at $200/$500/$1k/$2k/$5k.
Research-only, never places orders.
"""
from __future__ import annotations
import json, os, sqlite3, sys
from datetime import datetime, timezone
import requests

VERSION="execution-size-curve-v1-20260926"
SIZES=(200,500,1000,2000,5000)
USDC_MINT="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUPITER=os.getenv("JUPITER_API_KEY","")
ZEROX=os.getenv("ZEROX_API_KEY","")
STABLE={
 "eth":("1","0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",6),
 "base":("8453","0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",6),
 "arbitrum":("42161","0xaf88d065e77c8cC2239327D5ED3bA432268e5831",6),
 "bsc":("56","0x55d398326f99059fF775485246999027B3197955",18),
}
def now():return datetime.now(timezone.utc).isoformat()
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def obj(x):
    try:return json.loads(x or "{}")
    except Exception:return {}
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS execution_size_curve(
      source TEXT,batch_key TEXT,asset_key TEXT,size_usd REAL,
      buy_impact_bps REAL,sell_impact_bps REAL,roundtrip_loss_pct REAL,
      provider TEXT,status TEXT,error TEXT,version TEXT,created_at_utc TEXT,
      PRIMARY KEY(source,batch_key,asset_key,size_usd,version))""")
    c.commit()

def book_impact(levels,size,buy=True):
    if not levels:return None
    best=float(levels[0][0])
    if best<=0:return None
    if buy:
        remaining=float(size);base=spent=0.0
        for p,q in levels:
            p=float(p);q=float(q);avail=p*q;x=min(remaining,avail)
            base+=x/p;spent+=x;remaining-=x
            if remaining<=1e-8:break
        if remaining>1e-6 or base<=0:return None
        avg=spent/base;return (avg/best-1)*10000
    base_need=float(size)/best;remaining=base_need;received=0.0
    for p,q in levels:
        p=float(p);q=float(q);x=min(remaining,q);received+=x*p;remaining-=x
        if remaining<=1e-10:break
    if remaining>1e-8:return None
    avg=received/base_need;return (1-avg/best)*10000

def binance():
    db=os.getenv("BINANCE_DB","binance_avci2.db")
    if not os.path.exists(db):return
    with sqlite3.connect(db,timeout=30) as c:
        c.row_factory=sqlite3.Row;init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:return
        ts=scan[0]
        syms=[r[0] for r in c.execute("""SELECT symbol FROM features WHERE scan_time_utc=?
          AND selection_class='CANDIDATE'""",(ts,))]
        for sym in syms:
            r=c.execute("""SELECT raw_json FROM orderbook_snap WHERE scan_time_utc=? AND symbol=?
              ORDER BY id DESC LIMIT 1""",(ts,sym)).fetchone()
            raw=obj(r[0]) if r else {};asks=raw.get("asks") or [];bids=raw.get("bids") or []
            for size in SIZES:
                bi=book_impact(asks,size,True);si=book_impact(bids,size,False)
                rt=((bi or 0)+(si or 0))/100 if bi is not None and si is not None else None
                c.execute("INSERT OR REPLACE INTO execution_size_curve VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("BINANCE",ts,sym,size,bi,si,rt,"BINANCE_L2",
                   "OK" if rt is not None else "INSUFFICIENT_DEPTH",None,VERSION,now()))
        c.commit();print("execution curve BINANCE",len(syms))

def jupiter_loss(mint,decimals,price,size):
    if not JUPITER:return None,"JUPITER_KEY_MISSING"
    try:
        amount=int((size/price)*(10**int(decimals)))
        r=requests.get("https://api.jup.ag/swap/v1/quote",
          params={"inputMint":mint,"outputMint":USDC_MINT,"amount":str(amount)},
          headers={"x-api-key":JUPITER,"accept":"application/json"},timeout=15)
        r.raise_for_status();d=r.json();out=float(d["outAmount"])/(10**6)
        return 100*(size-out)/size,None
    except Exception as e:return None,f"{type(e).__name__}:{str(e)[:100]}"

def zerox_loss(network,token,decimals,price,size):
    if not ZEROX:return None,"ZEROX_KEY_MISSING"
    if network not in STABLE:return None,"UNSUPPORTED_CHAIN"
    chain,stable,sdec=STABLE[network]
    try:
        amount=int((size/price)*(10**int(decimals)))
        r=requests.get("https://api.0x.org/swap/allowance-holder/price",
          params={"chainId":chain,"sellToken":token,"buyToken":stable,"sellAmount":str(amount)},
          headers={"0x-api-key":ZEROX,"0x-version":"v2","accept":"application/json"},timeout=15)
        r.raise_for_status();d=r.json()
        if d.get("liquidityAvailable") is False:return None,"NO_LIQUIDITY"
        out=float(d["buyAmount"])/(10**sdec)
        return 100*(size-out)/size,None
    except Exception as e:return None,f"{type(e).__name__}:{str(e)[:100]}"

def gate():
    db=os.getenv("AVCI_DB","avci2.db");vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
    if not (os.path.exists(db) and os.path.exists(vdb)):return
    with sqlite3.connect(db,timeout=30) as c,sqlite3.connect(vdb,timeout=30) as v:
        c.row_factory=v.row_factory=sqlite3.Row;init(c)
        h=c.execute("""SELECT batch_id FROM gate_scan_health WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not h:return
        batch=str(h[0]);events=v.execute("""SELECT network_id,token_contract,signal_iso FROM validation_events
          WHERE batch_id=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')""",(batch,)).fetchall()
        for e in events:
            r=c.execute("""SELECT raw_json FROM snapshots WHERE network_id=? AND token_contract=?
              AND zaman_utc>=? ORDER BY id ASC LIMIT 1""",(e["network_id"],e["token_contract"],e["signal_iso"])).fetchone()
            item=obj(r[0]) if r else {};price=float(item.get("price_usd") or 0);dec=int(item.get("decimals") or 0)
            key=f"{e['network_id']}:{e['token_contract']}"
            for size in SIZES:
                loss=err=None;provider=""
                if price<=0 or dec<=0:err="PRICE_OR_DECIMALS_MISSING"
                elif e["network_id"]=="solana":
                    loss,err=jupiter_loss(e["token_contract"],dec,price,size);provider="JUPITER"
                else:
                    loss,err=zerox_loss(e["network_id"],e["token_contract"],dec,price,size);provider="0X"
                c.execute("INSERT OR REPLACE INTO execution_size_curve VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("GATE",batch,key,size,None,None,loss,provider,
                   "OK" if loss is not None else "UNAVAILABLE",err,VERSION,now()))
        c.commit();print("execution curve GATE",batch,len(events))

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":binance()
    elif m=="gate":gate()
    else:raise SystemExit("usage: execution_size_curve.py binance|gate")
if __name__=="__main__":main()
