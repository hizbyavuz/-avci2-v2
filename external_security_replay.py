#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay independent EVM security ground truth through Avci's frozen static gate.

Uses the same EVM security fields/thresholds as scanner.enrich_evm_candidate:
- is_honeypot
- hidden_owner
- can_take_back_ownership
- owner_change_balance
- selfdestruct
- is_blacklisted
- is_open_source == false
- buy_tax >= 0.10
- sell_tax >= 0.10

Research-only. No trading and no rule changes.
"""
from __future__ import annotations
import csv,json,os,sqlite3,time
from collections import defaultdict
from datetime import datetime,timezone
import requests

CORPUS=os.getenv("SECURITY_CORPUS","security_adversarial_corpus.csv")
DB=os.getenv("AVCI_DB","avci2.db")
TOKEN=os.getenv("GOPLUS_ACCESS_TOKEN","").strip()
PER_CLASS=int(os.getenv("SECURITY_REPLAY_PER_CLASS","100"))
BATCH=int(os.getenv("SECURITY_REPLAY_BATCH","50"))
VERSION="external-security-replay-v1-20260926"
HARD=("is_honeypot","hidden_owner","can_take_back_ownership",
      "owner_change_balance","selfdestruct","is_blacklisted")

def b01(v):
    if v is None:return None
    s=str(v).strip().lower()
    if s in ("1","true","yes"):return True
    if s in ("0","false","no"):return False
    return None
def num(v):
    try:return float(v)
    except Exception:return 0.0
def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS external_security_replay(
      network_id TEXT,contract TEXT,label TEXT,matched INTEGER,detected INTEGER,
      reasons_json TEXT,provider TEXT,source_url TEXT,replayed_at_utc TEXT,version TEXT,
      PRIMARY KEY(network_id,contract,label,version))""")
    c.commit()
def classify(tok):
    reasons=[]
    for k in HARD:
        if b01(tok.get(k)) is True:reasons.append(k.upper())
    if b01(tok.get("is_open_source")) is False:reasons.append("NOT_OPEN_SOURCE")
    if num(tok.get("sell_tax"))>=0.10:reasons.append("SELL_TAX_HIGH")
    if num(tok.get("buy_tax"))>=0.10:reasons.append("BUY_TAX_HIGH")
    return reasons
def fetch(chain,addresses):
    url=f"https://api.gopluslabs.io/api/v1/token_security/{chain}"
    headers={"accept":"application/json"}
    if TOKEN:headers["Authorization"]=f"Bearer {TOKEN}"
    r=requests.get(url,params={"contract_addresses":",".join(addresses)},headers=headers,timeout=30)
    r.raise_for_status()
    return (r.json().get("result") or {})
def main():
    if not os.path.exists(CORPUS):raise SystemExit("security corpus missing")
    rows=list(csv.DictReader(open(CORPUS,newline="",encoding="utf-8")))
    chosen=[]
    for label in ("BAD","GOOD"):
        group=[r for r in rows if (r.get("label") or "").upper()==label and str(r.get("network_id") or "") in ("1","56")]
        group=sorted(group,key=lambda r:(str(r.get("network_id")),str(r.get("contract"))))
        chosen.extend(group[:PER_CLASS])
    bychain=defaultdict(list)
    for r in chosen:bychain[str(r["network_id"])].append(r)
    stamp=datetime.now(timezone.utc).isoformat();counts=defaultdict(int);errs=[]
    with sqlite3.connect(DB,timeout=60) as c:
        init(c)
        for chain,group in bychain.items():
            for i in range(0,len(group),BATCH):
                part=group[i:i+BATCH]
                try:result=fetch(chain,[r["contract"].lower() for r in part])
                except Exception as e:
                    errs.append(f"{chain}:{i}:{type(e).__name__}:{str(e)[:140]}")
                    continue
                for r in part:
                    addr=r["contract"].lower()
                    tok=result.get(addr) or result.get(r["contract"]) or {}
                    matched=int(bool(tok));reasons=classify(tok) if tok else []
                    detected=int(bool(reasons)) if matched else 0
                    c.execute("INSERT OR REPLACE INTO external_security_replay VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (chain,addr,r["label"].upper(),matched,detected,json.dumps(reasons),
                       "GOPLUS_CURRENT_STATIC_REPLAY",r.get("source_url") or "",stamp,VERSION))
                    counts[f'{r["label"].lower()}_attempted']+=1
                    counts[f'{r["label"].lower()}_matched']+=matched
                    counts[f'{r["label"].lower()}_detected']+=detected
                time.sleep(.25)
        c.commit()
    report={"version":VERSION,"generated_at_utc":stamp,"per_class_limit":PER_CLASS,
            "counts":dict(counts),"errors":errs,
            "limitation":"Current static replay, not historical signal-time provider state."}
    open("security_external_replay_report.json","w",encoding="utf-8").write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
