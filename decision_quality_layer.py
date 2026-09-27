#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decision-quality overlay for Binance Avci and Gate Web3 Avci.

Downstream only: never changes frozen scanner membership or thresholds.
Purpose:
- count independent evidence families instead of raw duplicated reasons
- flag stale data and late/chasing setups
- surface empirical probability/CI when the historical sample is real
- provide one machine-readable decision-quality record for Telegram
"""
import json, os, sqlite3, sys
from datetime import datetime, timezone

VERSION="decision-quality-v1-20260927"

def now(): return datetime.now(timezone.utc)
def parse_dt(x):
    try: return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception: return None
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def arr(x):
    try:
        v=json.loads(x or "[]")
        return v if isinstance(v,list) else []
    except Exception:return []

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS decision_quality(
      source TEXT NOT NULL,batch_key TEXT NOT NULL,asset_key TEXT NOT NULL,
      independent_families INTEGER NOT NULL,families_json TEXT NOT NULL,
      data_fresh INTEGER NOT NULL,data_age_minutes REAL,
      late_risk TEXT NOT NULL,late_reason TEXT,
      empirical_probability REAL,empirical_n INTEGER,
      empirical_ci_low REAL,empirical_ci_high REAL,
      regime_probability REAL,regime_n INTEGER,
      quality_status TEXT NOT NULL,blockers_json TEXT NOT NULL,
      version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
      PRIMARY KEY(source,batch_key,asset_key,version)
    )"""); c.commit()

def family_set(support,counter=()):
    text=" | ".join([str(x).lower() for x in list(support)+list(counter)])
    fam=set()
    groups={
      "FLOW":("akış","taker","order-book","orderbook","alıcı","satıcı","buy","sell"),
      "STRUCTURE":("retention","tutun","koruyor","breakout","reclaim","yeniden hız","re-ignition","tetik"),
      "RELATIVE":("btc","sektör","piyasa geneline","ayrış","rarity","sıra dışı"),
      "DERIVATIVES":("oi","funding","fonlama","vadeli","futures"),
      "LIVE":("15dk","canlı izleme","live pool","live-pool"),
      "HISTORY":("geçmiş","+10","kontrol","historical"),
      "CATALYST":("kataliz","haber","news","catalyst"),
      "EXECUTION":("spread","price-impact","slippage","exit","çıkış maliyeti","likidite"),
      "SECURITY":("güvenlik","security","sybil","wash","holder","deployer","wallet","funding graph","lp "),
      "ONCHAIN":("unique buyer","benzersiz alıcı","holder","on-chain","onchain","wallet"),
    }
    for name,keys in groups.items():
        if any(k in text for k in keys): fam.add(name)
    return fam

def overlay(c,source,batch,asset):
    if not table(c,"institutional_signal_overlay"): return None
    return c.execute("""SELECT * FROM institutional_signal_overlay
      WHERE source=? AND batch_key=? AND asset_key=?
      ORDER BY created_at_utc DESC LIMIT 1""",(source,str(batch),asset)).fetchone()

def lateness(change24,hist_gain=None):
    c=float(change24 or 0)
    hg=float(hist_gain) if hist_gain is not None else None
    if c>=25: return "HIGH",f"24 saatte zaten %{c:.1f} yükselmiş"
    if c>=15: return "MEDIUM",f"24 saatte %{c:.1f} yükselmiş; giriş geç kalmış olabilir"
    if hg is not None and hg>=100: return "MEDIUM",f"90g dipten +%{hg:.0f}; eski büyük koşucu"
    return "LOW","hareket henüz aşırı uzamış görünmüyor"

def save(c,source,batch,asset,fams,fresh,age,late,late_reason,ov,blockers):
    prob=n=lo=hi=rprob=rn=None
    if ov:
        prob=ov["success_probability"]; n=ov["success_n"]
        lo=ov["success_ci_low"]; hi=ov["success_ci_high"]
        rprob=ov["regime_probability"]; rn=ov["regime_n"]
    status="BLOCK" if blockers else ("WATCH" if len(fams)<3 or late=="HIGH" else "ELIGIBLE")
    c.execute("""INSERT OR REPLACE INTO decision_quality VALUES
      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (source,str(batch),asset,len(fams),json.dumps(sorted(fams)),
       int(bool(fresh)),age,late,late_reason,prob,n,lo,hi,rprob,rn,status,
       json.dumps(blockers,ensure_ascii=False),VERSION,now().isoformat()))

def binance(c):
    scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
      ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
    if not scan or not table(c,"binance_candidate_evidence"): return 0
    ts=scan["scan_time_utc"]; t=parse_dt(ts)
    age=(now()-t).total_seconds()/60 if t else None
    fresh=age is not None and age<=90
    rows=c.execute("""SELECT e.*,f.change_24h,f.raw_json,tr.execution_quality
      FROM binance_candidate_evidence e
      JOIN features f ON f.scan_time_utc=e.scan_time_utc AND f.symbol=e.symbol
      LEFT JOIN trade_readiness tr ON tr.source='BINANCE'
        AND tr.batch_key=e.scan_time_utc AND tr.asset_key=e.symbol
      WHERE e.scan_time_utc=?
        AND e.version=(SELECT version FROM binance_candidate_evidence
          WHERE scan_time_utc=? ORDER BY created_at_utc DESC LIMIT 1)""",(ts,ts)).fetchall()
    nout=0
    for r in rows:
        fam=family_set(arr(r["support_json"]),arr(r["counter_json"]))
        raw={}
        try: raw=json.loads(r["raw_json"] or "{}")
        except Exception: pass
        late,reason=lateness(r["change_24h"],raw.get("history_gain_90d_pct"))
        blockers=[]
        if not fresh: blockers.append("veri bayat")
        if r["execution_quality"]=="BAD": blockers.append("execution maliyeti uygun değil")
        if float(r["coverage_pct"] or 0)<40: blockers.append("veri kapsamı kritik düşük")
        ov=overlay(c,"BINANCE",ts,r["symbol"])
        save(c,"BINANCE",ts,r["symbol"],fam,fresh,age,late,reason,ov,blockers); nout+=1
    c.commit(); return nout

def gate(c):
    h=c.execute("""SELECT * FROM gate_scan_health WHERE status='VALID'
      ORDER BY scan_ts DESC LIMIT 1""").fetchone()
    if not h or not table(c,"gate_candidate_evidence"): return 0
    batch=str(h["batch_id"]); t=datetime.fromtimestamp(int(h["scan_ts"]),timezone.utc)
    age=(now()-t).total_seconds()/60; fresh=age<=75
    rows=c.execute("""SELECT e.*,tr.execution_quality,s.label security_label,s.hard_veto,
      o.change_24h
      FROM gate_candidate_evidence e
      LEFT JOIN trade_readiness tr ON tr.source='GATE'
        AND tr.batch_key=e.batch_id AND tr.asset_key=e.token_contract
      LEFT JOIN gate_security_confidence_history s ON s.batch_id=e.batch_id
        AND s.network_id=e.network_id AND s.token_contract=e.token_contract
      LEFT JOIN gate_early_observations o ON o.batch_id=e.batch_id
        AND o.network_id=e.network_id AND o.token_contract=e.token_contract
      WHERE e.batch_id=?
        AND e.version=(SELECT version FROM gate_candidate_evidence
          WHERE batch_id=? ORDER BY created_at_utc DESC LIMIT 1)""",(batch,batch)).fetchall()
    nout=0
    for r in rows:
        fam=family_set(arr(r["support_json"]),arr(r["counter_json"]))
        late,reason=lateness(r["change_24h"],None)
        blockers=[]
        if not fresh: blockers.append("veri bayat")
        if int(r["hard_veto"] or 0): blockers.append("güvenlik hard-veto")
        if (r["security_label"] or "").upper() not in ("STRONG","MEDIUM"):
            blockers.append("güvenlik yeterli değil")
        if r["execution_quality"]=="BAD": blockers.append("satış/çıkış uygulanabilir değil")
        if float(r["coverage_pct"] or 0)<40: blockers.append("veri kapsamı kritik düşük")
        asset=f"{r['network_id']}:{r['token_contract']}"
        ov=overlay(c,"GATE",batch,asset)
        save(c,"GATE",batch,r["token_contract"],fam,fresh,age,late,reason,ov,blockers); nout+=1
    c.commit(); return nout

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    db=os.getenv("BINANCE_DB","binance_avci2.db") if mode=="binance" else os.getenv("AVCI_DB","avci2.db")
    if mode not in ("binance","gate"): raise SystemExit("usage: decision_quality_layer.py binance|gate")
    if not os.path.exists(db): return
    with sqlite3.connect(db,timeout=60) as c:
        c.row_factory=sqlite3.Row; init(c)
        n=binance(c) if mode=="binance" else gate(c)
    print(f"decision quality | {mode} | {n} assets")

if __name__=="__main__": main()
