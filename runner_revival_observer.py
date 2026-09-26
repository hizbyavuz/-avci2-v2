#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Observe previously large runners that may be re-igniting.

Research/reporting only. Does not alter frozen Binance V1/V2 or Gate V5
candidate membership, thresholds, labels, or validation outcomes.
"""
import json, os, sqlite3, time
from datetime import datetime, timezone
import requests

BINANCE_DB=os.getenv("BINANCE_DB","binance_avci2.db")
GATE_DB=os.getenv("AVCI_DB","avci2.db")
GECKO="https://api.geckoterminal.com/api/v2"
VERSION="runner-revival-observer-v1-20260926"
session=requests.Session()
session.headers.update({"User-Agent":"avci-runner-revival/1.0"})

def now():
    return datetime.now(timezone.utc).isoformat()

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS runner_revival_observations(
      source TEXT NOT NULL,
      batch_key TEXT NOT NULL,
      asset_key TEXT NOT NULL,
      symbol TEXT,
      network_id TEXT,
      current_price REAL,
      lookback_days INTEGER,
      floor_price REAL,
      peak_price REAL,
      gain_floor_to_peak_pct REAL,
      drawdown_from_peak_pct REAL,
      current_gain_from_floor_pct REAL,
      change_24h_pct REAL,
      change_1h_pct REAL,
      volume_24h REAL,
      liquidity_usd REAL,
      class_label TEXT NOT NULL,
      reason TEXT,
      version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,batch_key,asset_key,version)
    )""")
    c.commit()

def classify(floor,peak,current,change24,change1):
    if not floor or not peak or floor<=0 or peak<=0 or not current or current<=0:
        return None
    run=(peak/floor-1)*100
    dd=(current/peak-1)*100
    current_gain=(current/floor-1)*100
    if run < 100:
        return None
    # Separate "old runner revival" from "still extended".
    if dd <= -25 and (change1 >= 1 or change24 >= 5):
        label="OLD_RUNNER_REVIVAL"
        reason="Geçmişte büyük yükseliş yaptı, zirveden belirgin geri çekildi ve yeniden hareketleniyor."
    elif dd > -25:
        label="STILL_EXTENDED"
        reason="Geçmişte büyük yükseliş yaptı ve hâlâ eski zirveye yakın; geç kalma riski yüksek."
    else:
        label="OLD_RUNNER_DORMANT"
        reason="Geçmişte büyük yükseliş yaptı ve zirveden geri çekildi; şu an güçlü yeniden canlanma yok."
    return label,run,dd,current_gain,reason

def binance_get(path,params=None):
    for base in ("https://data-api.binance.vision","https://api.binance.com"):
        try:
            r=session.get(base+path,params=params,timeout=15)
            if r.ok:
                return r.json()
        except Exception:
            pass
    return None

def run_binance():
    if not os.path.exists(BINANCE_DB):
        print("runner revival BINANCE DB yok"); return
    with sqlite3.connect(BINANCE_DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        scan=c.execute("SELECT scan_time_utc FROM scans WHERE health_status!='INVALID' ORDER BY scan_time_utc DESC LIMIT 1").fetchone()
        if not scan: return
        batch=scan["scan_time_utc"]
        rows=c.execute("""SELECT symbol,price,change_24h,change_1h,raw_json
          FROM features WHERE scan_time_utc=? AND selection_class='ALREADY_RISEN'
          ORDER BY change_24h DESC LIMIT 15""",(batch,)).fetchall()
        written=0
        for r in rows:
            k=binance_get("/api/v3/klines",{"symbol":r["symbol"],"interval":"1d","limit":90})
            if not isinstance(k,list) or len(k)<20: continue
            lows=[float(x[3]) for x in k[:-1]]
            highs=[float(x[2]) for x in k[:-1]]
            if not lows or not highs: continue
            floor=min(lows); peak=max(highs); current=float(r["price"] or k[-1][4])
            out=classify(floor,peak,current,float(r["change_24h"] or 0),float(r["change_1h"] or 0))
            if not out: continue
            label,run,dd,cg,reason=out
            raw=json.loads(r["raw_json"] or "{}")
            vol=float(raw.get("quote_volume_24h") or 0)
            c.execute("""INSERT OR REPLACE INTO runner_revival_observations
              (source,batch_key,asset_key,symbol,current_price,lookback_days,floor_price,peak_price,
               gain_floor_to_peak_pct,drawdown_from_peak_pct,current_gain_from_floor_pct,
               change_24h_pct,change_1h_pct,volume_24h,class_label,reason,version,created_at_utc)
              VALUES('BINANCE',?,?,?,?,90,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (batch,r["symbol"],r["symbol"],current,floor,peak,run,dd,cg,
               float(r["change_24h"] or 0),float(r["change_1h"] or 0),vol,label,reason,VERSION,now()))
            written+=1
        c.commit()
        print("runner revival BINANCE",batch,written)

def gt_get(path):
    try:
        r=session.get(GECKO+path,headers={"accept":"application/json;version=20230203"},timeout=20)
        if r.ok: return r.json()
    except Exception:
        return None
    return None

def included_map(payload):
    out={}
    for x in (payload or {}).get("included",[]) or []:
        if isinstance(x,dict) and x.get("id"):
            out[x["id"]]=x.get("attributes") or {}
    return out

def rel_id(pool,name):
    try: return pool["relationships"][name]["data"]["id"]
    except Exception: return None

def run_gate():
    if not os.path.exists(GATE_DB):
        print("runner revival GATE DB yok"); return
    with sqlite3.connect(GATE_DB,timeout=30) as c:
        c.row_factory=sqlite3.Row; init(c)
        h=c.execute("SELECT batch_id FROM gate_scan_health WHERE status='VALID' ORDER BY scan_ts DESC LIMIT 1").fetchone()
        if not h: return
        batch=str(h["batch_id"]); seen=set(); written=0
        for net in ("solana","base","bsc","eth","arbitrum"):
            payload=gt_get(f"/networks/{net}/trending_pools?include=base_token,quote_token")
            if not isinstance(payload,dict): continue
            inc=included_map(payload)
            for pool in (payload.get("data") or [])[:20]:
                a=pool.get("attributes") or {}
                token_id=rel_id(pool,"base_token")
                tok=inc.get(token_id,{})
                contract=str(tok.get("address") or "")
                symbol=str(tok.get("symbol") or tok.get("name") or "")
                pool_addr=str(a.get("address") or "")
                if not contract or not pool_addr: continue
                key=f"{net}:{contract.lower()}"
                if key in seen: continue
                seen.add(key)
                price=float(a.get("base_token_price_usd") or 0)
                if price<=0: continue
                changes=a.get("price_change_percentage") or {}
                ch24=float(changes.get("h24") or 0); ch1=float(changes.get("h1") or 0)
                vol=float((a.get("volume_usd") or {}).get("h24") or 0)
                liq=float(a.get("reserve_in_usd") or 0)
                ohlcv=gt_get(f"/networks/{net}/pools/{pool_addr}/ohlcv/day?aggregate=1&limit=90&currency=usd&token={contract}")
                try:
                    bars=ohlcv["data"]["attributes"]["ohlcv_list"]
                except Exception:
                    bars=[]
                if len(bars)<20: continue
                completed=bars[1:] if len(bars)>1 else bars
                lows=[float(x[3]) for x in completed if len(x)>=5 and float(x[3])>0]
                highs=[float(x[2]) for x in completed if len(x)>=5 and float(x[2])>0]
                if not lows or not highs: continue
                floor=min(lows); peak=max(highs)
                out=classify(floor,peak,price,ch24,ch1)
                if not out: continue
                label,run,dd,cg,reason=out
                c.execute("""INSERT OR REPLACE INTO runner_revival_observations
                  (source,batch_key,asset_key,symbol,network_id,current_price,lookback_days,floor_price,peak_price,
                   gain_floor_to_peak_pct,drawdown_from_peak_pct,current_gain_from_floor_pct,
                   change_24h_pct,change_1h_pct,volume_24h,liquidity_usd,class_label,reason,version,created_at_utc)
                  VALUES('GATE',?,?,?,?,?,90,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (batch,key,symbol,net,price,floor,peak,run,dd,cg,ch24,ch1,vol,liq,label,reason,VERSION,now()))
                written+=1
                time.sleep(.15)
        c.commit()
        print("runner revival GATE",batch,written)

if __name__=="__main__":
    mode=(os.sys.argv[1].lower() if len(os.sys.argv)>1 else "both")
    if mode in ("binance","both"): run_binance()
    if mode in ("gate","both"): run_gate()
