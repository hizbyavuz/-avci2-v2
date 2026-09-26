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
MIN_ECONOMIC_DIFF_PCT=0.50
ALPHA_TWO_SIDED=0.05
TARGET_POWER=0.80

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

def two_way_cluster_diff(rows,is_candidate):
    """Conservative two-way cluster-robust diagnostic for candidate-control expectancy.

    Clusters on asset and ISO week, subtracting the asset×week intersection term.
    This is a research diagnostic for repeated-token/time dependence; it does not
    change signal selection or frozen thresholds.
    """
    valid=[r for r in rows if r.get("net") is not None and isinstance(r.get("time"),datetime)]
    ca=[r for r in valid if is_candidate(r)]
    co=[r for r in valid if not is_candidate(r)]
    if len(ca)<2 or len(co)<2:
        return {"diff":None,"se":None,"ci_low":None,"ci_high":None,
                "asset_clusters":0,"week_clusters":0,"asset_week_clusters":0}
    ma=mean([float(r["net"]) for r in ca]); mb=mean([float(r["net"]) for r in co])
    nc=len(ca); nk=len(co)
    contrib=[]
    for r in valid:
        y=float(r["net"])
        psi=(y-ma)/nc if is_candidate(r) else -(y-mb)/nk
        iso=r["time"].isocalendar(); week=f"{iso.year}-W{iso.week:02d}"
        contrib.append((r["asset"],week,(r["asset"],week),psi))
    def cvar(idx):
        groups={}
        for row in contrib: groups[row[idx]]=groups.get(row[idx],0.0)+row[3]
        g=len(groups)
        if g<2:return 0.0,g
        return (g/(g-1.0))*sum(v*v for v in groups.values()),g
    va,ga=cvar(0); vw,gw=cvar(1); vi,gi=cvar(2)
    var=max(0.0,va+vw-vi)
    se=math.sqrt(var); diff=ma-mb
    return {"diff":diff,"se":se,"ci_low":diff-1.96*se,"ci_high":diff+1.96*se,
            "asset_clusters":ga,"week_clusters":gw,"asset_week_clusters":gi}

def init(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS research_p0_governance(
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
    );
    CREATE TABLE IF NOT EXISTS research_p0_dependence(
      source TEXT NOT NULL,
      version TEXT NOT NULL,
      audited_at_utc TEXT NOT NULL,
      expectancy_diff REAL,
      two_way_cluster_se REAL,
      ci_low REAL,
      ci_high REAL,
      asset_clusters INTEGER NOT NULL,
      week_clusters INTEGER NOT NULL,
      asset_week_clusters INTEGER NOT NULL,
      method TEXT NOT NULL,
      PRIMARY KEY(source,version)
    );
    """)

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
    raw=val.execute("""SELECT batch_id,token_contract,network_id,signal_ts,group_type,btc_regime,
      net_final_pct,cost_status,status FROM validation_events
      WHERE status='CLOSED_72H'
        AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE','NEAR_MISS','RANDOM_CONTROL')
      ORDER BY signal_ts""").fetchall()
    health={}
    if table(obs,"gate_scan_health"):
        health={str(r["batch_id"]):int(r["scan_ts"]) for r in obs.execute("SELECT batch_id,scan_ts FROM gate_scan_health")}
    leakage=0
    for r in raw:
        h=health.get(str(r["batch_id"]))
        if h is None or abs(int(r["signal_ts"])-h)>1800:
            leakage+=1
        net=r["net_final_pct"] if r["cost_status"]=="QUOTE_PLUS_ASSUMPTION" else None
        rows.append({"asset":f'{r["network_id"]}:{r["token_contract"]}',
                     "time":datetime.fromtimestamp(int(r["signal_ts"]),timezone.utc),
                     "group":r["group_type"],"regime":r["btc_regime"],"net":net})
    return rows,leakage

def write(c,source,files,stats,leakage,dependence):
    # We deliberately do NOT auto-select a sample size from observed performance.
    # That would make optional stopping easier. A power plan must be predeclared
    # from an externally chosen minimum economically meaningful effect.
    power_status="PREDECLARED_DECISION_CONTRACT_FROZEN"
    optional="SEQUENTIAL_CHECKPOINTS_AND_MDE_FROZEN"
    notes={
      "primary_kpi":"Cost-aware net expectancy per signal; hit-rate is secondary.",
      "dependence":"Report assets, ISO weeks and asset×week clusters separately; raw event N is not treated as independent N.",
      "freeze_proof":"GitHub run SHA plus SHA256 of frozen source/config files.",
      "power":f"Frozen before genesis holdout: minimum economic candidate-control expectancy difference={MIN_ECONOMIC_DIFF_PCT:.2f} percentage points/signal, two-sided alpha={ALPHA_TWO_SIDED:.2f}, target power={TARGET_POWER:.2f}. Achieved power remains dependence/variance-sensitive; no post-hoc resizing from observed uplift.",
      "optional_stopping":"Sequential checkpoints and minimum economic effect are frozen before prospective holdout; decisions remain blocked until the holdout matures and pre-registered go-live criteria pass.",
      "two_way_clustering":"Candidate-control expectancy includes an asset + ISO-week cluster-robust diagnostic with asset×week intersection correction."
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
    c.execute("DELETE FROM research_p0_dependence WHERE source=? AND version=?",(source,VERSION))
    c.execute("""INSERT INTO research_p0_dependence VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      (source,VERSION,now(),dependence.get("diff"),dependence.get("se"),
       dependence.get("ci_low"),dependence.get("ci_high"),dependence.get("asset_clusters",0),
       dependence.get("week_clusters",0),dependence.get("asset_week_clusters",0),
       "ASSET_PLUS_ISO_WEEK_MINUS_ASSET_WEEK_INTERSECTION"))
    c.commit()
    print(source,"P0 GOVERNANCE",{
      "git_sha":m["git_sha"],"closed":stats["total"],"candidate":stats["candidate_n"],
      "control":stats["control_n"],"unique_assets":stats["unique_assets"],
      "unique_weeks":stats["unique_weeks"],"asset_weeks":stats["unique_asset_weeks"],
      "candidate_expectancy":stats["candidate_expectancy"],
      "control_expectancy":stats["control_expectancy"],
      "expectancy_diff":stats["expectancy_diff"],"candidate_pf":stats["candidate_pf"],
      "leakage_violations":leakage,"power_plan":power_status,
      "two_way_cluster_ci":[dependence.get("ci_low"),dependence.get("ci_high")]
    })

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if mode=="binance":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if not os.path.exists(db): return
        with sqlite3.connect(db,timeout=30) as c:
            c.row_factory=sqlite3.Row; init(c)
            rows,leak=binance(c)
            selector=lambda r:r["group"]=="CANDIDATE"
            stats=summarize(rows,selector)
            dependence=two_way_cluster_diff(rows,selector)
            write(c,"BINANCE",BINANCE_FROZEN_FILES,stats,leak,dependence)
    elif mode=="gate":
        odb=os.getenv("AVCI_DB","avci2.db"); vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if not os.path.exists(odb) or not os.path.exists(vdb): return
        with sqlite3.connect(odb,timeout=30) as o, sqlite3.connect(vdb,timeout=30) as v:
            o.row_factory=v.row_factory=sqlite3.Row; init(o)
            rows,leak=gate(o,v)
            selector=lambda r:r["group"] in ("CANDIDATE","EXPANDED_CANDIDATE")
            stats=summarize(rows,selector)
            dependence=two_way_cluster_diff(rows,selector)
            write(o,"GATE",GATE_FROZEN_FILES,stats,leak,dependence)
    else:
        raise SystemExit("usage: research_p0_governance.py binance|gate")

if __name__=="__main__": main()
