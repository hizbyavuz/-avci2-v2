#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capital-trust gate for Avci.

This layer does NOT trade and does NOT alter frozen signal rules.
It answers a stricter question: has the system earned permission to be
considered for real capital yet?

Fail-closed by design. Missing evidence keeps real-money mode CLOSED.
"""
import json, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone

VERSION="capital-trust-v1.1-effective-n-20260927"
MIN_CANDIDATES=100
MIN_CONTROLS=80
MIN_EFFECTIVE_CANDIDATES=100.0
MIN_EFFECTIVE_CONTROLS=80.0
MIN_REGIME_N=15
MIN_REGIMES=2
MIN_PROFIT_FACTOR=1.20
MIN_EXPECTANCY_PCT=0.25
MIN_HIT10_LIFT=1.20
MIN_HIT10_DIFF_PP=5.0
MIN_STRONG_LABEL_N=30

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def mean(xs): return sum(xs)/len(xs) if xs else None
def effective_n(keys):
    counts=defaultdict(int)
    for key in keys:
        counts[key]+=1
    sizes=list(counts.values())
    raw=sum(sizes)
    return (raw*raw/sum(x*x for x in sizes)) if sizes else 0.0

def pf(xs):
    pos=sum(x for x in xs if x>0); neg=-sum(x for x in xs if x<0)
    if neg<=0: return None if pos<=0 else 999.0
    return pos/neg

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS capital_trust_status(
      source TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      version TEXT NOT NULL,
      candidate_n INTEGER NOT NULL,
      control_n INTEGER NOT NULL,
      candidate_expectancy REAL,
      control_expectancy REAL,
      profit_factor REAL,
      candidate_hit10 REAL,
      control_hit10 REAL,
      hit10_lift REAL,
      regime_pass_count INTEGER NOT NULL,
      security_status TEXT,
      blockers_json TEXT NOT NULL,
      passes_json TEXT NOT NULL,
      created_at_utc TEXT NOT NULL
    )""")

def classify(cand_n,ctrl_n,cand_eff,ctrl_eff,cand_net,ctrl_net,cand_hit,ctrl_hit,regime_pass,security_ok=True,brake_ok=True):
    passes=[]; blockers=[]
    if cand_n>=MIN_CANDIDATES: passes.append(f"kapalı aday örneği yeterli ({cand_n})")
    else: blockers.append(f"kapalı aday örneği yetersiz ({cand_n}/{MIN_CANDIDATES})")
    if ctrl_n>=MIN_CONTROLS: passes.append(f"kontrol örneği yeterli ({ctrl_n})")
    else: blockers.append(f"kontrol örneği yetersiz ({ctrl_n}/{MIN_CONTROLS})")
    if cand_eff>=MIN_EFFECTIVE_CANDIDATES:
        passes.append(f"aday effective-N yeterli ({cand_eff:.1f})")
    else:
        blockers.append(f"aday effective-N yetersiz ({cand_eff:.1f}/{MIN_EFFECTIVE_CANDIDATES:.0f}); aynı gün/rejim kümeleri bağımsız sayılmıyor")
    if ctrl_eff>=MIN_EFFECTIVE_CONTROLS:
        passes.append(f"kontrol effective-N yeterli ({ctrl_eff:.1f})")
    else:
        blockers.append(f"kontrol effective-N yetersiz ({ctrl_eff:.1f}/{MIN_EFFECTIVE_CONTROLS:.0f}); aynı gün/rejim kümeleri bağımsız sayılmıyor")
    ce=mean(cand_net); be=mean(ctrl_net)
    p=pf(cand_net)
    if ce is not None and ce>=MIN_EXPECTANCY_PCT: passes.append(f"maliyet sonrası beklenti pozitif (%{ce:.2f})")
    else: blockers.append("maliyet sonrası beklenti henüz yeterli değil")
    if p is not None and p>=MIN_PROFIT_FACTOR: passes.append(f"profit factor yeterli ({p:.2f})")
    else: blockers.append("profit factor henüz yeterli değil")
    lift=(cand_hit/ctrl_hit) if cand_hit is not None and ctrl_hit not in (None,0) else None
    diff=((cand_hit-ctrl_hit)*100) if cand_hit is not None and ctrl_hit is not None else None
    if lift is not None and diff is not None and lift>=MIN_HIT10_LIFT and diff>=MIN_HIT10_DIFF_PP:
        passes.append(f"+10 kontrol üstünlüğü var ({lift:.2f}x, +{diff:.1f} puan)")
    else: blockers.append("+10 kontrol üstünlüğü henüz yeterli değil")
    if regime_pass>=MIN_REGIMES: passes.append(f"en az {MIN_REGIMES} piyasa rejiminde tekrarlandı")
    else: blockers.append(f"rejim dayanıklılığı yetersiz ({regime_pass}/{MIN_REGIMES})")
    if security_ok: passes.append("kritik güvenlik/doğrulama engeli yok")
    else: blockers.append("kritik güvenlik/doğrulama engeli var")
    if brake_ok: passes.append("son dönem performans freni devrede değil")
    else: blockers.append("son dönem performans freni devrede")
    status="OPEN" if not blockers else "CLOSED"
    return status,passes,blockers,ce,be,p,lift

def exact_label_check(c,source):
    if not table(c,"trader_label_ledger"):
        return False,"GÜÇLÜ etiketi için 72s sonuç defteri henüz yok"
    rows=c.execute("""SELECT final_return_pct FROM trader_label_ledger
      WHERE source=? AND label='GÜÇLÜ' AND status='CLOSED'
        AND final_return_pct IS NOT NULL ORDER BY closed_at_utc""",(source,)).fetchall()
    vals=[float(r[0]) for r in rows]
    if len(vals)<MIN_STRONG_LABEL_N:
        return False,f"GÜÇLÜ etiketi kapanmış örnek yetersiz ({len(vals)}/{MIN_STRONG_LABEL_N})"
    e=mean(vals); p=pf(vals)
    if e is None or e<MIN_EXPECTANCY_PCT:
        return False,f"GÜÇLÜ etiketi beklentisi yetersiz (%{(e or 0):.2f})"
    if p is None or p<MIN_PROFIT_FACTOR:
        return False,f"GÜÇLÜ etiketi profit factor yetersiz ({(p or 0):.2f})"
    return True,f"GÜÇLÜ etiketi ayrı doğrulandı (n={len(vals)}, beklenti %{e:.2f}, PF {p:.2f})"

def binance(c):
    cand=[]; ctrl=[]; hit_c=[]; hit_k=[]; regimes=defaultdict(list)
    cand_cluster_keys=[]; ctrl_cluster_keys=[]
    if table(c,"signal_events") and table(c,"outcome_labels"):
        rows=c.execute("""SELECT s.event_class,s.btc_regime,s.signal_time_utc,o.net_return_pct,
            o.reach_json,o.barrier_results_json
          FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
          WHERE o.label_status='CLOSED'
            AND s.event_class IN ('CANDIDATE','RANDOM_CONTROL','NEAR_MISS')""").fetchall()
        for r in rows:
            net=r["net_return_pct"]
            try: net=float(net) if net is not None else None
            except Exception: net=None
            try:
                reach=json.loads(r["reach_json"] or "{}")
                hit=int(bool(reach.get("10") or reach.get("10.0")))
            except Exception:
                hit=0
            day=str(r["signal_time_utc"] or "")[:10] or "UNKNOWN"
            cluster_key=f"{day}|{r['btc_regime'] or 'UNKNOWN'}"
            if r["event_class"]=="CANDIDATE":
                if net is not None:
                    cand.append(net); regimes[r["btc_regime"] or "UNKNOWN"].append(net)
                    cand_cluster_keys.append(cluster_key)
                hit_c.append(hit)
            else:
                if net is not None:
                    ctrl.append(net); ctrl_cluster_keys.append(cluster_key)
                hit_k.append(hit)
    regime_pass=sum(1 for xs in regimes.values() if len(xs)>=MIN_REGIME_N and (mean(xs) or -999)>0)
    ch=mean(hit_c); kh=mean(hit_k)
    brake_ok=True
    if table(c,"performance_brake"):
        br=c.execute("SELECT status FROM performance_brake WHERE source='BINANCE' LIMIT 1").fetchone()
        if br and br[0]=="ENGAGED": brake_ok=False
    status,passes,blockers,ce,be,p,lift=classify(len(cand),len(ctrl),effective_n(cand_cluster_keys),effective_n(ctrl_cluster_keys),cand,ctrl,ch,kh,regime_pass,True,brake_ok)
    label_ok,label_note=exact_label_check(c,"BINANCE")
    (passes if label_ok else blockers).append(label_note)
    status="OPEN" if not blockers else "CLOSED"
    return dict(status=status,candidate_n=len(cand),control_n=len(ctrl),candidate_expectancy=ce,
                control_expectancy=be,profit_factor=p,candidate_hit10=ch,control_hit10=kh,
                hit10_lift=lift,regime_pass_count=regime_pass,security_status="N/A",
                passes=passes,blockers=blockers)

def gate(c,v):
    cand=[]; ctrl=[]; hit_c=[]; hit_k=[]; regimes=defaultdict(list)
    cand_cluster_keys=[]; ctrl_cluster_keys=[]
    if table(v,"validation_events"):
        rows=v.execute("""SELECT group_type,btc_regime,signal_iso,net_final_pct,result_10,status,rulesets
          FROM validation_events
          WHERE status='CLOSED_72H'
            AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','RANDOM_CONTROL','NEAR_MISS')
            AND NOT (group_type='CANDIDATE' AND COALESCE(rulesets,'')='')""").fetchall()
        for r in rows:
            try: net=float(r["net_final_pct"]) if r["net_final_pct"] is not None else None
            except Exception: net=None
            hit=int(str(r["result_10"] or "").upper()=="TARGET_FIRST")
            day=str(r["signal_iso"] or "")[:10] or "UNKNOWN"
            cluster_key=f"{day}|{r['btc_regime'] or 'UNKNOWN'}"
            if r["group_type"] in ("CANDIDATE","EXPANDED_CANDIDATE"):
                if net is not None:
                    cand.append(net); regimes[r["btc_regime"] or "UNKNOWN"].append(net)
                    cand_cluster_keys.append(cluster_key)
                hit_c.append(hit)
            else:
                if net is not None:
                    ctrl.append(net); ctrl_cluster_keys.append(cluster_key)
                hit_k.append(hit)

    security_ok=False
    sec_status="INSUFFICIENT"
    if table(c,"security_model_decay_history"):
        s=c.execute("""SELECT * FROM security_model_decay_history
          WHERE source='GATE' ORDER BY run_date DESC LIMIT 1""").fetchone()
        if s:
            sec_status=s["status"]
            security_ok=(sec_status=="OK")
    # Absence of validated security evidence is a capital blocker.
    regime_pass=sum(1 for xs in regimes.values() if len(xs)>=MIN_REGIME_N and (mean(xs) or -999)>0)
    ch=mean(hit_c); kh=mean(hit_k)
    brake_ok=True
    if table(c,"performance_brake"):
        br=c.execute("SELECT status FROM performance_brake WHERE source='GATE' LIMIT 1").fetchone()
        if br and br[0]=="ENGAGED": brake_ok=False
    status,passes,blockers,ce,be,p,lift=classify(len(cand),len(ctrl),effective_n(cand_cluster_keys),effective_n(ctrl_cluster_keys),cand,ctrl,ch,kh,regime_pass,security_ok,brake_ok)
    label_ok,label_note=exact_label_check(c,"GATE")
    (passes if label_ok else blockers).append(label_note)
    status="OPEN" if not blockers else "CLOSED"
    return dict(status=status,candidate_n=len(cand),control_n=len(ctrl),candidate_expectancy=ce,
                control_expectancy=be,profit_factor=p,candidate_hit10=ch,control_hit10=kh,
                hit10_lift=lift,regime_pass_count=regime_pass,security_status=sec_status,
                passes=passes,blockers=blockers)

def save(c,source,r):
    c.execute("""INSERT OR REPLACE INTO capital_trust_status VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (source,r["status"],VERSION,r["candidate_n"],r["control_n"],r["candidate_expectancy"],
       r["control_expectancy"],r["profit_factor"],r["candidate_hit10"],r["control_hit10"],
       r["hit10_lift"],r["regime_pass_count"],r["security_status"],
       json.dumps(r["blockers"],ensure_ascii=False),json.dumps(r["passes"],ensure_ascii=False),now()))
    c.commit()
    print(f"{source} CAPITAL_TRUST={r['status']} | blockers={len(r['blockers'])}")

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if mode=="binance":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db): return
        with sqlite3.connect(db,timeout=60) as c:
            c.row_factory=sqlite3.Row; init(c); save(c,"BINANCE",binance(c))
    elif mode=="gate":
        db=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not os.path.exists(db) or not os.path.exists(vdb): return
        with sqlite3.connect(db,timeout=60) as c, sqlite3.connect(vdb,timeout=60) as v:
            c.row_factory=v.row_factory=sqlite3.Row; init(c); save(c,"GATE",gate(c,v))
    else:
        raise SystemExit("usage: capital_trust_gate.py binance|gate")

if __name__=="__main__": main()
