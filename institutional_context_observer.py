#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Institutional context observer for Avci.

Adds:
- official macro calendar proximity (FOMC/CPI/PPI/Employment where discoverable)
- optional X crowding for Binance candidates using cashtag query
- explicit provenance and missing-data status

Research/reporting only; does not alter frozen signal rules.
"""
from __future__ import annotations
import json, os, re, sqlite3, sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import requests

VERSION="institutional-context-v1-20260926"
BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")
GVDB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
TOKEN=os.getenv("X_API_BEARER_TOKEN","")
UA={"User-Agent":"avci-institutional-context/1.0"}

def now():return datetime.now(timezone.utc)
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def base(sym):return sym[:-4] if sym.endswith("USDT") else sym
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS institutional_context(
      source TEXT,batch_key TEXT,asset_key TEXT,macro_state TEXT,macro_events_json TEXT,
      social_status TEXT,social_last_15m REAL,social_previous_45m REAL,social_ratio REAL,
      social_source TEXT,version TEXT,created_at_utc TEXT,
      PRIMARY KEY(source,batch_key,asset_key,version))""")
    c.commit()

def bls_events():
    out=[]
    try:
        r=requests.get("https://www.bls.gov/schedule/news_release/bls.ics",headers=UA,timeout=15)
        r.raise_for_status()
        text=r.text
        chunks=text.split("BEGIN:VEVENT")
        for ch in chunks[1:]:
            sm=re.search(r"SUMMARY:(.+)",ch)
            dm=re.search(r"DTSTART(?:;[^:]*)?:(\d{8}T?\d*)",ch)
            if not sm or not dm:continue
            title=sm.group(1).strip()
            if not any(k in title.lower() for k in ("consumer price","producer price","employment situation")):continue
            raw=dm.group(1)
            try:
                if "T" in raw:
                    d=datetime.strptime(raw[:15],"%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                else:d=datetime.strptime(raw[:8],"%Y%m%d").replace(tzinfo=timezone.utc)
            except Exception:continue
            out.append({"type":"BLS","title":title,"time_utc":d.isoformat(),"source":"bls.ics"})
    except Exception:
        pass
    return out

def fomc_events():
    # Official published 2026/2027 meeting dates; stored as observation context,
    # not a trading rule. Update automatically from official page if regex succeeds.
    out=[]
    fallback=["2026-10-28T18:00:00+00:00","2026-12-09T19:00:00+00:00",
              "2027-01-27T19:00:00+00:00","2027-03-17T18:00:00+00:00"]
    try:
        r=requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",headers=UA,timeout=15)
        r.raise_for_status()
        # Current page is semi-structured; fallback keeps only officially known
        # future decision dates if parsing layout changes.
        text=re.sub("<[^>]+>"," ",r.text)
        # no blind parsing to avoid wrong dates from minutes/older years
    except Exception:
        text=""
    for x in fallback:
        out.append({"type":"FOMC","title":"FOMC policy decision window","time_utc":x,
                    "source":"Federal Reserve meeting calendar"})
    return out

def macro_state(ts):
    events=bls_events()+fomc_events()
    near=[]
    for e in events:
        try:d=datetime.fromisoformat(e["time_utc"])
        except Exception:continue
        hours=(d-ts).total_seconds()/3600
        if abs(hours)<=24:
            near.append({**e,"hours_from_signal":hours})
    if any(abs(e["hours_from_signal"])<=6 for e in near):state="HIGH_IMPACT_6H"
    elif near:state="MACRO_24H"
    else:state="CLEAR_24H"
    return state,near

def x_counts(symbol):
    if not TOKEN:return {"status":"UNAVAILABLE","reason":"X_API_BEARER_TOKEN_MISSING"}
    b=base(symbol)
    # Cashtag reduces ticker ambiguity versus plain symbol search.
    end=now()-timedelta(minutes=1);start=end-timedelta(minutes=60)
    params={"query":f'${b} (crypto OR token) -is:retweet',
            "start_time":start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time":end.strftime("%Y-%m-%dT%H:%M:%SZ"),"granularity":"minute"}
    try:
        r=requests.get("https://api.x.com/2/tweets/counts/recent",params=params,
          headers={"Authorization":f"Bearer {TOKEN}"},timeout=12)
        r.raise_for_status();data=r.json().get("data") or []
        recent=previous=0;boundary=end-timedelta(minutes=15)
        for row in data:
            d=datetime.fromisoformat(row["start"].replace("Z","+00:00"));n=int(row["tweet_count"])
            if boundary<=d<end:recent+=n
            elif start<=d<boundary:previous+=n
        ratio=recent/(previous/3) if previous else None
        return {"status":"OBSERVED","last_15m":recent,"previous_45m":previous,"ratio":ratio,
                "source":"X cashtag counts"}
    except Exception as e:
        return {"status":"UNAVAILABLE","reason":type(e).__name__}

def binance():
    if not os.path.exists(BDB):return
    with sqlite3.connect(BDB,timeout=30) as c:
        c.row_factory=sqlite3.Row;init(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:return
        ts=scan["scan_time_utc"]; t=datetime.fromisoformat(ts.replace("Z","+00:00"))
        syms=[r[0] for r in c.execute("""SELECT symbol FROM features WHERE scan_time_utc=?
          AND selection_class='CANDIDATE'""",(ts,)).fetchall()]
        ms,me=macro_state(t)
        for sym in syms:
            sx=x_counts(sym)
            c.execute("""INSERT OR REPLACE INTO institutional_context VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BINANCE",ts,sym,ms,json.dumps(me),sx.get("status"),
               sx.get("last_15m"),sx.get("previous_45m"),sx.get("ratio"),
               sx.get("source") or sx.get("reason"),VERSION,now().isoformat()))
        c.commit();print("institutional context BINANCE",len(syms),ms)

def gate():
    if not (os.path.exists(GDB) and os.path.exists(GVDB)):return
    with sqlite3.connect(GDB,timeout=30) as c,sqlite3.connect(GVDB,timeout=30) as v:
        c.row_factory=v.row_factory=sqlite3.Row;init(c)
        h=c.execute("""SELECT batch_id,scan_ts FROM gate_scan_health WHERE status='VALID'
          ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not h:return
        batch=str(h["batch_id"]);ts=datetime.fromtimestamp(int(h["scan_ts"]),timezone.utc)
        ms,me=macro_state(ts)
        ev=v.execute("""SELECT network_id,token_contract FROM validation_events
          WHERE batch_id=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')""",(batch,)).fetchall()
        for e in ev:
            key=f"{e['network_id']}:{e['token_contract']}"
            c.execute("""INSERT OR REPLACE INTO institutional_context VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("GATE",batch,key,ms,json.dumps(me),"FROM_SCANNER_SNAPSHOT",None,None,None,
               "Gate exact-contract X counts are collected inside scanner when token is available.",
               VERSION,now().isoformat()))
        c.commit();print("institutional context GATE",len(ev),ms)

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":binance()
    elif m=="gate":gate()
    else:raise SystemExit("usage: institutional_context_observer.py binance|gate")
if __name__=="__main__":main()
