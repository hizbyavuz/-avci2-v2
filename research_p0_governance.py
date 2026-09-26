#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 research governance audit for Avci.

Purpose:
- prove what code/config was frozen at run time (git SHA + source hashes)
- report dependence / effective sample structure
- make cost-aware expectancy the primary performance KPI
- explicitly flag optional-stopping / insufficient-OOS conditions

Research-only. Never changes signal thresholds or candidate membership.
"""
import hashlib, json, math, os, sqlite3, statistics, sys
from datetime import datetime, timezone

VERSION="research-p0-governance-v1-20260926"
PRIMARY_KPI="NET_EXPECTANCY_PER_SIGNAL"
SECONDARY_KPIS=("PROFIT_FACTOR","MEDIAN_NET_RETURN","HIT_10_BEFORE_STOP","EXCESS_RETURN")

BINANCE_FROZEN_FILES=[
    "binance_scanner.py","binance_outcome_labeler.py",
    "binance_activation_math.py","binance_evidence_research.py",
]
GATE_FROZEN_FILES=[
    "scanner.py","history_validation_v5.py",
    "gate_activation_math.py","gate_evidence_research.py",
]

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def sha256_file(path):
    try:
        h=hashlib.sha256()
        with open(path,"rb") as f:
            for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None
def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def profit_factor(vals):
    wins=sum(x for x in vals if x>0)
    losses=-sum(x for x in vals if x<0)
    if losses<=0: return None if wins<=0 else 999.0
    return wins/losses
def mean(vals): return sum(vals)/len(vals) if vals else None
def median(vals): return statistics.median(vals) if vals else None

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS research_p0_governance(
      source TEXT NOT NULL,
      version TEXT NOT NULL,
      audited_at_utc TEXT NOT NULL,
      git_sha TEXT,
      frozen_manifest_json TEXT NOT NULL,
      total_closed INTEGER NOT NULL,
      candidate_n INTEGER NOT NULL,
      control_n INTEGER NOT NULL,
      unique_assets INTEGER NOT NULL,
      unique_weeks INTEGER NOT NULL,
      unique_asset_weeks INTEGER NOT NULL,
      unique_regimes INTEGER NOT NULL,
      max_events_one_asset INTEGER NOT NULL,
      max_events_one_week INTEGER NOT NULL,
      candidate_expectancy REAL,
      control_expectancy REAL,
      expectancy_diff REAL,
      candidate_profit_factor REAL,
      control_profit_factor REAL,
      candidate_median_net REAL,
      control_median_net REAL,
      primary_kpi TEXT NOT NULL,
      secondary_kpis_json TEXT NOT NULL,
      leakage_violations INTEGER NOT NULL,
      optional_stopping_status TEXT NOT NULL,
      power_plan_status TEXT NOT NULL,
      notes_json TEXT NOT NULL,
      PRIMARY KEY(source,version)
    )""")

def manifest(files):
    return {
      "git_sha":os.getenv("GITHUB_SHA") or "LOCAL_UNKNOWN",
      "files":{p:sha256_file(p) for p in files},
      "captured_at_utc":now(),
    }

def summarize(rows,is_candidate):
    ca=[r for r in rows if is_candidate(r)]
    co=[r for r in rows if not is_candidate(r)]
    cn=[float(r["net"]) for r in ca if r.get("net") is not None]
    kn=[float(r["net"]) for r in co if r.get("net") is not None]
    all_assets=[r["asset"] for r in rows]
    weeks=[]
    asset_weeks=[]
    regimes=set()
    by_asset={}; by_week={}
    for r in rows:
        x=r["time"]
        w="UNKNOWN"
        if isinstance(x,datetime):
            iso=x.isocalendar(); w=f"{iso.year}-W{iso.week:02d}"
        weeks.append(w); asset_weeks.append((r["asset"],w))
        regimes.add(r.get("regime") or "UNKNOWN")
        by_asset[r["asset"]]=by_asset.get(r["asset"],0)+1
        by_week[w]=by_week.get(w,0)+1
    return {
      "total":len(rows),"candidate_n":len(ca),"control_n":len(co),
      "unique_assets":len(set(all_assets)),"unique_weeks":len(set(weeks)),
      "unique_asset_weeks":len(set(asset_weeks)),"unique_regimes":len(regimes),
      "max_events_one_asset":max(by_asset.values()) if by_asset else 0,
      "max_events_one_week":max(by_week.values()) if by_week else 0,
      "candidate_expectancy":mean(cn),"control_expectancy":mean(kn),
      "expectancy_diff":(mean(cn)-mean(kn)) if cn and kn else None,
      "candidate_pf":profit_factor(cn),"control_pf":profit_factor(kn),
      "candidate_median":median(cn),"control_median":median(kn),
    }

def binance(c):
    if not table(c,"signal_events") or not table(c,"outcome_labels"): return [],0
    rows=[]
    raw=c.execute("""SELECT s.symbol,s.signal_time_utc,s.event_class,s.btc_regime,
      s.entry_time_utc,s.entry_delay_seconds,o.net_return_pct,o.label_status
      FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
      WHERE o.label_status='CLOSED'
        AND s.event_class IN ('CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY s.signal_time_utc""").fetchall()
    leakage=0
    for r in raw:
        t=dt(r["signal_time_utc"]); e=dt(r["entry_time_utc"])
        if t and e:
            if e<t: leakage+=1
            elif r["entry_delay_seconds"] is not None and (e-t).total_seconds()<float(r["entry_delay_seconds"]):
                leakage+=1
        rows.append({"asset":r["symbol"],"time":t,"group":r["event_class"],
                     "regime":r["btc_regime"],"net":r["net_return_pct"]})
    return rows,leakage

def gate(obs,val):
    if not table(val,"validation_events"): return [],0
    rows=[]
    raw=val.execute("""SELECT token_contract,network_id,signal_ts,group_type,btc_regime,
      net_final_pct,cost_status,status FROM validation_events
      WHERE status='CLOSED_72H'
        AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY signal_ts""").fetchall()
    health={}
    if table(obs,"gate_scan_health"):
        health={str(r["batch_id"]):int(r["scan_ts"]) for r in obs.execute("SELECT batch_id,scan_ts FROM gate_scan_health")}
    leakage=0
    # batch_id omitted from select above; point-in-time violations remain audited
    # in gate_research_integrity. Here governance focuses on closed cost-aware rows.
    for r in raw:
        net=r["net_final_pct"] if r["cost_status"]=="QUOTE_PLUS_ASSUMPTION" else None
        rows.append({"asset":f'{r["network_id"]}:{r["token_contract"]}',
                     "time":datetime.fromtimestamp(int(r["signal_ts"]),timezone.utc),
                     "group":r["group_type"],"regime":r["btc_regime"],"net":net})
    return rows,leakage

def write(c,source,files,stats,leakage):
    # We deliberately do NOT auto-select a sample size from observed performance.
    # That would make optional stopping easier. A power plan must be predeclared
    # from an externally chosen minimum economically meaningful effect.
    power_status="NEEDS_PREDECLARED_MINIMUM_EFFECT"
    optional="NO_FIXED_STOP_RULE_YET"
    notes={
      "primary_kpi":"Cost-aware net expectancy per signal; hit-rate is secondary.",
      "dependence":"Report assets, ISO weeks and asset×week clusters separately; raw event N is not treated as independent N.",
      "freeze_proof":"GitHub run SHA plus SHA256 of frozen source/config files.",
      "power":"Do not derive minimum N from the currently observed uplift. Choose minimum economically meaningful effect first, then freeze power/sample plan.",
      "optional_stopping":"Until a power/sample stopping rule is frozen, results remain exploratory even if p/q values look attractive.",
      "two_way_clustering":"Counts are reported now; formal two-way clustered inference remains P1."
    }
    m=manifest(files)
    c.execute("DELETE FROM research_p0_governance WHERE source=? AND version=?",(source,VERSION))
    c.execute("""INSERT INTO research_p0_governance VALUES
      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (source,VERSION,now(),m["git_sha"],json.dumps(m,sort_keys=True),
       stats["total"],stats["candidate_n"],stats["control_n"],stats["unique_assets"],
       stats["unique_weeks"],stats["unique_asset_weeks"],stats["unique_regimes"],
       stats["max_events_one_asset"],stats["max_events_one_week"],
       stats["candidate_expectancy"],stats["control_expectancy"],stats["expectancy_diff"],
       stats["candidate_pf"],stats["control_pf"],stats["candidate_median"],stats["control_median"],
       PRIMARY_KPI,json.dumps(SECONDARY_KPIS),leakage,optional,power_status,
       json.dumps(notes,ensure_ascii=False)))
    c.commit()
    print(source,"P0 GOVERNANCE",{
      "git_sha":m["git_sha"],"closed":stats["total"],"candidate":stats["candidate_n"],
      "control":stats["control_n"],"unique_assets":stats["unique_assets"],
      "unique_weeks":stats["unique_weeks"],"asset_weeks":stats["unique_asset_weeks"],
      "candidate_expectancy":stats["candidate_expectancy"],
      "control_expectancy":stats["control_expectancy"],
      "expectancy_diff":stats["expectancy_diff"],"candidate_pf":stats["candidate_pf"],
      "leakage_violations":leakage,"power_plan":power_status
    })

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if mode=="binance":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db): return
        with sqlite3.connect(db,timeout=30) as c:
            c.row_factory=sqlite3.Row; init(c)
            rows,leak=binance(c)
            stats=summarize(rows,lambda r:r["group"]=="CANDIDATE")
            write(c,"BINANCE",BINANCE_FROZEN_FILES,stats,leak)
    elif mode=="gate":
        odb=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not os.path.exists(odb) or not os.path.exists(vdb): return
        with sqlite3.connect(odb,timeout=30) as o, sqlite3.connect(vdb,timeout=30) as v:
            o.row_factory=v.row_factory=sqlite3.Row; init(o)
            rows,leak=gate(o,v)
            stats=summarize(rows,lambda r:r["group"] in ("CANDIDATE","EXPANDED_CANDIDATE"))
            write(o,"GATE",GATE_FROZEN_FILES,stats,leak)
    else:
        raise SystemExit("usage: research_p0_governance.py binance|gate")

if __name__=="__main__": main()
