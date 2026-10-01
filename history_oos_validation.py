#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chronological 70/30 out-of-sample validation for the historical Avci pattern.

Pattern tested (all point-in-time):
  crushed history + dip departure + retention + persistence + re-ignition

Rules:
- chronological 70/30 split by anchor timestamp; never random split
- thresholds learned ONLY from TRAIN winner/control distributions
- TEST outcomes are not read until the frozen spec exists
- signal price is the anchor-day close; pre-signal gains never count
- primary target horizon is the existing broad-miner 60d horizon
- winner label is the existing +20%/60d RISE definition
- hourly snapshots are aligned to anchor day-end, never future onset
"""
from __future__ import annotations
import hashlib, json, math, os, random, sqlite3, statistics
from datetime import datetime, timezone
import history_validation_v5 as hv5

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-oos-70-30-v1-20260927"
HORIZON_DAYS=60
TARGETS=(5,10,15)
SEED=27092026
MIN_TRAIN_PER_CLASS=30

def con():
    c=sqlite3.connect(DB,timeout=60)
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=60000"); c.row_factory=sqlite3.Row
    return c

def f(x):
    try:
        x=float(x); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def median(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None

def quantile(xs,q):
    xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not xs:return None
    if len(xs)==1:return xs[0]
    p=(len(xs)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p))
    if lo==hi:return xs[lo]
    w=p-lo
    return xs[lo]*(1-w)+xs[hi]*w

def utc(ts):
    return datetime.fromtimestamp(int(ts),timezone.utc).date().isoformat()

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS oos_point_features(
      pair TEXT NOT NULL,anchor_ts INTEGER NOT NULL,label TEXT NOT NULL,
      signal_ts INTEGER NOT NULL,signal_price REAL NOT NULL,
      dist_high_90d REAL,drawdown_30d REAL,
      dist_low_24h_t24 REAL,dist_low_24h_t1 REAL,dip_departure_accel REAL,
      retention_pct REAL,persistence_ratio REAL,
      reignition_ret_3h REAL,reignition_vol_ratio REAL,
      feature_status TEXT NOT NULL,feature_error TEXT,
      version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS oos_split(
      pair TEXT NOT NULL,anchor_ts INTEGER NOT NULL,label TEXT NOT NULL,
      split TEXT NOT NULL,btc_regime TEXT NOT NULL,version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS oos_spec(
      feature TEXT NOT NULL,operator TEXT NOT NULL,threshold REAL NOT NULL,
      train_winner_n INTEGER NOT NULL,train_control_n INTEGER NOT NULL,
      train_winner_median REAL,train_control_median REAL,
      frozen_utc TEXT NOT NULL,version TEXT NOT NULL,
      PRIMARY KEY(feature,version)
    );
    CREATE TABLE IF NOT EXISTS oos_case_results(
      pair TEXT NOT NULL,anchor_ts INTEGER NOT NULL,label TEXT NOT NULL,
      split TEXT NOT NULL,btc_regime TEXT NOT NULL,
      signal INTEGER NOT NULL,near_miss INTEGER NOT NULL,random_control INTEGER NOT NULL,
      signal_price REAL NOT NULL,
      hit5 INTEGER NOT NULL,hit10 INTEGER NOT NULL,hit15 INTEGER NOT NULL,
      max_return_pct REAL,version TEXT NOT NULL,
      PRIMARY KEY(pair,anchor_ts,label,version)
    );
    CREATE TABLE IF NOT EXISTS oos_metrics(
      split TEXT NOT NULL,regime TEXT NOT NULL,group_name TEXT NOT NULL,
      target_pct INTEGER NOT NULL,n INTEGER NOT NULL,hits INTEGER NOT NULL,
      precision REAL,recall REAL,baseline_rate REAL,lift_vs_random REAL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,regime,group_name,target_pct,version)
    );
    CREATE TABLE IF NOT EXISTS oos_runs(
      run_id TEXT PRIMARY KEY,created_utc TEXT NOT NULL,cutoff_ts INTEGER,
      train_start INTEGER,train_end INTEGER,test_start INTEGER,test_end INTEGER,
      train_winners INTEGER,train_controls INTEGER,test_winners INTEGER,test_controls INTEGER,
      feature_done INTEGER,feature_missing INTEGER,spec_frozen INTEGER,
      verdict TEXT,notes TEXT,version TEXT NOT NULL
    );
    """)

def all_cases(c):
    return c.execute("""SELECT pair,event_ts,label,pre_price,dist_high_90d,drawdown_30d
      FROM event_features WHERE label IN ('RISE','CONTROL') AND pre_price IS NOT NULL
      ORDER BY event_ts,pair,label""").fetchall()

def split_cutoff(rows):
    ts=sorted({int(r["event_ts"]) for r in rows})
    if len(ts)<10: raise RuntimeError("not_enough_distinct_dates")
    idx=max(1,min(len(ts)-1,int(math.ceil(len(ts)*0.70))-1))
    return ts[idx]

def btc_regime(c,ts):
    rows=c.execute("""SELECT close FROM daily_bars WHERE pair='BTC_USDT' AND ts<=?
      ORDER BY ts DESC LIMIT 31""",(int(ts),)).fetchall()
    if len(rows)<31:return "UNKNOWN"
    now=f(rows[0][0]); old=f(rows[-1][0])
    if not now or not old:return "UNKNOWN"
    r=pct(old,now)
    return "UP" if r>=10 else ("DOWN" if r<=-10 else "SIDEWAYS")

def populate_split(c,rows,cut):
    c.execute("DELETE FROM oos_split WHERE version=?",(VERSION,))
    for r in rows:
        split="TRAIN" if int(r["event_ts"])<=cut else "TEST"
        c.execute("INSERT OR REPLACE INTO oos_split VALUES(?,?,?,?,?,?)",
          (r["pair"],int(r["event_ts"]),r["label"],split,btc_regime(c,r["event_ts"]),VERSION))

def point_features(pair,anchor_ts):
    signal_end=int(anchor_ts)+86400
    hourly,source=hv5.fetch_hourly(pair,signal_end-76*3600,signal_end)
    h=[x for x in hourly if int(x["ts"])<signal_end]
    if len(h)<72: raise ValueError("short_hourly:"+str(len(h)))
    # use the last closed hourly candle as T-1/point-in-time signal price
    cur=h[-1]; t24=h[-24]
    def dist_low_at(idx):
        lo=min(float(x["low"]) for x in h[max(0,idx-23):idx+1])
        return pct(lo,float(h[idx]["close"]))
    d24=dist_low_at(len(h)-24); d1=dist_low_at(len(h)-1)
    accel=(d1-d24) if d1 is not None and d24 is not None else None

    # retention: fraction of the strongest low->peak impulse in last 24h still held at signal.
    w=h[-24:]; lows=[float(x["low"]) for x in w]; highs=[float(x["high"]) for x in w]
    best=None
    for i in range(len(w)-2):
        lo=lows[i]
        peak=max(highs[i+1:])
        amp=peak-lo
        if amp>0 and (best is None or amp>best[0]): best=(amp,lo,peak)
    retention=None
    if best:
        amp,lo,peak=best
        retention=100.0*(float(cur["close"])-lo)/amp
        retention=max(-200.0,min(200.0,retention))

    # persistence: after the first half's strongest volume burst, does volume stay elevated?
    pre=h[-48:-24]
    base=median([x.get("qv") for x in pre])
    first=w[:12]
    spike_i=max(range(len(first)),key=lambda i:(f(first[i].get("qv")) or -1))
    after=w[spike_i+1:]
    persist=median([x.get("qv") for x in after]) / base if after and base and base>0 else None

    # re-ignition: latest 3h price acceleration + latest 3h volume vs prior-24h median.
    r3=pct(float(h[-4]["close"]),float(cur["close"])) if len(h)>=4 else None
    last3=[f(x.get("qv")) for x in h[-3:] if f(x.get("qv")) is not None]
    vr=(sum(last3)/len(last3)/base) if last3 and base and base>0 else None
    if None in (d24,d1,accel,retention,persist,r3,vr):
        raise ValueError("missing_point_feature")
    return signal_end-3600,float(cur["close"]),d24,d1,accel,retention,persist,r3,vr,source

def fill_features(c,rows,budget):
    existing={(r["pair"],int(r["anchor_ts"]),r["label"]) for r in c.execute(
      "SELECT pair,anchor_ts,label FROM oos_point_features WHERE version=? AND feature_status='DONE'",(VERSION,))}
    due=[r for r in rows if (r["pair"],int(r["event_ts"]),r["label"]) not in existing]
    for r in due[:budget]:
        try:
            sig,price,d24,d1,acc,ret,per,r3,vr,src=point_features(r["pair"],r["event_ts"])
            c.execute("""INSERT OR REPLACE INTO oos_point_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (r["pair"],int(r["event_ts"]),r["label"],sig,price,
               f(r["dist_high_90d"]),f(r["drawdown_30d"]),d24,d1,acc,ret,per,r3,vr,
               "DONE","SOURCE="+src,VERSION))
        except Exception as e:
            c.execute("""INSERT OR REPLACE INTO oos_point_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (r["pair"],int(r["event_ts"]),r["label"],int(r["event_ts"])+82800,
               float(r["pre_price"]),f(r["dist_high_90d"]),f(r["drawdown_30d"]),
               None,None,None,None,None,None,None,"ERROR",str(e)[:220],VERSION))
        c.commit()

def train_rows(c):
    return c.execute("""SELECT p.* FROM oos_point_features p JOIN oos_split s
      ON s.pair=p.pair AND s.anchor_ts=p.anchor_ts AND s.label=p.label AND s.version=p.version
      WHERE p.version=? AND s.split='TRAIN' AND p.feature_status='DONE'""",(VERSION,)).fetchall()

def choose_threshold(winners,controls,feature,op):
    w=[f(r[feature]) for r in winners if f(r[feature]) is not None]
    z=[f(r[feature]) for r in controls if f(r[feature]) is not None]
    if len(w)<MIN_TRAIN_PER_CLASS or len(z)<MIN_TRAIN_PER_CLASS:
        return None
    vals=sorted(set(w+z))
    candidates=[quantile(vals,q) for q in [i/20 for i in range(2,19)]]
    best=None
    for th in candidates:
        if th is None:continue
        if op==">=":
            tpr=sum(x>=th for x in w)/len(w); fpr=sum(x>=th for x in z)/len(z)
        else:
            tpr=sum(x<=th for x in w)/len(w); fpr=sum(x<=th for x in z)/len(z)
        j=tpr-fpr
        score=(j,-abs(tpr-0.65))
        if best is None or score>best[0]: best=(score,float(th))
    return best[1] if best else None

FEATURES=(
 ("dist_high_90d","<="),("drawdown_30d","<="),
 ("dist_low_24h_t1",">="),("dip_departure_accel",">="),
 ("retention_pct",">="),("persistence_ratio",">="),
 ("reignition_ret_3h",">="),("reignition_vol_ratio",">="),
)

def freeze_spec(c):
    old=c.execute("SELECT * FROM oos_spec WHERE version=?",(VERSION,)).fetchall()
    if old:return {r["feature"]:(r["operator"],float(r["threshold"])) for r in old},True
    tr=train_rows(c); w=[r for r in tr if r["label"]=="RISE"]; z=[r for r in tr if r["label"]=="CONTROL"]
    if len(w)<MIN_TRAIN_PER_CLASS or len(z)<MIN_TRAIN_PER_CLASS:return {},False
    spec={}
    now=datetime.now(timezone.utc).isoformat()
    for feat,op in FEATURES:
        th=choose_threshold(w,z,feat,op)
        if th is None:return {},False
        c.execute("INSERT INTO oos_spec VALUES(?,?,?,?,?,?,?,?,?)",
          (feat,op,th,len(w),len(z),median([r[feat] for r in w]),median([r[feat] for r in z]),now,VERSION))
        spec[feat]=(op,th)
    c.commit(); return spec,True

def passes(r,spec,include_reignition=True):
    names=[x[0] for x in FEATURES]
    if not include_reignition:names=[n for n in names if not n.startswith("reignition_")]
    for n in names:
        v=f(r[n]); op,th=spec[n]
        if v is None:return False
        if op==">=" and v<th:return False
        if op=="<=" and v>th:return False
    return True

def outcome(c,pair,anchor_ts,signal_price):
    rows=c.execute("""SELECT high FROM daily_bars WHERE pair=? AND ts>? AND ts<=?
      ORDER BY ts""",(pair,int(anchor_ts),int(anchor_ts)+HORIZON_DAYS*86400)).fetchall()
    if not rows or signal_price<=0:return None
    mx=max(float(r["high"]) for r in rows)
    ret=pct(signal_price,mx)
    return ret,{t:int(ret>=t) for t in TARGETS}

def build_results(c,spec):
    c.execute("DELETE FROM oos_case_results WHERE version=?",(VERSION,))
    rows=c.execute("""SELECT p.*,s.split,s.btc_regime FROM oos_point_features p JOIN oos_split s
      ON s.pair=p.pair AND s.anchor_ts=p.anchor_ts AND s.label=p.label AND s.version=p.version
      WHERE p.version=? AND p.feature_status='DONE' ORDER BY p.anchor_ts,p.pair""",(VERSION,)).fetchall()
    staged=[]
    for r in rows:
        o=outcome(c,r["pair"],r["anchor_ts"],float(r["signal_price"]))
        if o is None:continue
        ret,hits=o
        sig=int(passes(r,spec,True))
        near=int((not sig) and passes(r,spec,False) and not (
          f(r["reignition_ret_3h"])>=spec["reignition_ret_3h"][1] and
          f(r["reignition_vol_ratio"])>=spec["reignition_vol_ratio"][1]))
        staged.append([r,sig,near,0,ret,hits])

    # Fixed-seed random controls; selection uses labels/split only, never outcomes.
    rng=random.Random(SEED)
    for split in ("TRAIN","TEST"):
        eligible=[i for i,x in enumerate(staged) if x[0]["split"]==split and x[0]["label"]=="CONTROL" and not x[2]]
        n_sig=sum(x[1] for x in staged if x[0]["split"]==split)
        n=min(len(eligible),max(1,n_sig))
        for i in rng.sample(eligible,n) if eligible and n else []: staged[i][3]=1

    for r,sig,near,rand,ret,hits in staged:
        c.execute("""INSERT OR REPLACE INTO oos_case_results VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (r["pair"],int(r["anchor_ts"]),r["label"],r["split"],r["btc_regime"],
           sig,near,rand,float(r["signal_price"]),hits[5],hits[10],hits[15],ret,VERSION))
    c.commit()

def metrics(c):
    c.execute("DELETE FROM oos_metrics WHERE version=?",(VERSION,))
    out=[]
    for split in ("TRAIN","TEST"):
      for regime in ("ALL","UP","SIDEWAYS","DOWN"):
        baseq="""FROM oos_case_results WHERE version=? AND split=?"""
        args=[VERSION,split]
        if regime!="ALL":baseq+=" AND btc_regime=?"; args.append(regime)
        rows=c.execute("SELECT * "+baseq,args).fetchall()
        winners=[r for r in rows if r["label"]=="RISE"]
        for group,flt in (
          ("SIGNAL",lambda r:r["signal"]==1),
          ("NEAR_MISS",lambda r:r["near_miss"]==1),
          ("RANDOM_CONTROL",lambda r:r["random_control"]==1),
        ):
          g=[r for r in rows if flt(r)]
          for t in TARGETS:
            key=f"hit{t}"; hits=sum(int(r[key]) for r in g); n=len(g)
            prec=hits/n if n else None
            recall=(sum(int(r["signal"]==1) for r in winners)/len(winners)) if group=="SIGNAL" and winners else None
            rand=[r for r in rows if r["random_control"]==1]
            br=sum(int(r[key]) for r in rand)/len(rand) if rand else None
            lift=prec/br if prec is not None and br not in (None,0) else None
            c.execute("INSERT OR REPLACE INTO oos_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (split,regime,group,t,n,hits,prec,recall,br,lift,VERSION))
            out.append((split,regime,group,t,n,hits,prec,recall,br,lift))
    c.commit(); return out

def fisher_greater(a,b,c,d):
    n1=a+b; n2=c+d; K=a+c; N=n1+n2
    if n1==0 or n2==0:return None
    lo=max(0,n1-(N-K)); hi=min(n1,K); den=math.comb(N,n1)
    return min(1.0,sum((math.comb(K,x)*math.comb(N-K,n1-x))/den for x in range(max(a,lo),hi+1)))

def report(c,cut,spec):
    def counts(split):
        rr=c.execute("""SELECT label,COUNT(*) n FROM oos_split WHERE version=? AND split=?
          GROUP BY label""",(VERSION,split)).fetchall()
        d={r["label"]:int(r["n"]) for r in rr}
        return d.get("RISE",0),d.get("CONTROL",0)
    tw,tc=counts("TRAIN"); vw,vc=counts("TEST")
    ranges={}
    for split in ("TRAIN","TEST"):
        r=c.execute("""SELECT MIN(anchor_ts),MAX(anchor_ts) FROM oos_split
          WHERE version=? AND split=?""",(VERSION,split)).fetchone()
        ranges[split]=(utc(r[0]) if r and r[0] else None,utc(r[1]) if r and r[1] else None)
    m=[dict(r) for r in c.execute("""SELECT * FROM oos_metrics WHERE version=?
      ORDER BY split,regime,group_name,target_pct""",(VERSION,))]
    def mrow(split,regime,group,t):
        return next((r for r in m if r["split"]==split and r["regime"]==regime and r["group_name"]==group and r["target_pct"]==t),None)
    test10=mrow("TEST","ALL","SIGNAL",10); rand10=mrow("TEST","ALL","RANDOM_CONTROL",10)
    verdict="INSUFFICIENT"; primary_p=None; primary_diff=None
    if test10 and test10["n"]>=20 and rand10 and rand10["n"]>=20:
        sp=float(test10["precision"] or 0); rp=float(rand10["precision"] or 0)
        primary_diff=sp-rp
        primary_p=fisher_greater(int(test10["hits"]),int(test10["n"]-test10["hits"]),
                                 int(rand10["hits"]),int(rand10["n"]-rand10["hits"]))
        verdict="OOS_EDGE_PRESENT" if primary_diff>0 and primary_p is not None and primary_p<0.05 else "NO_CONFIRMED_OOS_EDGE"
    data={"version":VERSION,"horizon_days":HORIZON_DAYS,"cutoff_date":utc(cut),
      "train":{"date_range":ranges["TRAIN"],"winners":tw,"controls":tc},
      "test":{"date_range":ranges["TEST"],"winners":vw,"controls":vc},
      "spec":{k:{"operator":v[0],"threshold":v[1]} for k,v in spec.items()},
      "metrics":m,"primary_test":{"target_pct":10,"precision_diff_vs_random":primary_diff,"fisher_one_sided_p":primary_p},"verdict":verdict,
      "caveat":"Historical archive is still active-pair seeded; delisted-market survivorship coverage remains incomplete."}
    with open("history_oos_report.json","w",encoding="utf-8") as fp: json.dump(data,fp,ensure_ascii=False,indent=2)
    lines=["# Avcı Historical OOS 70/30","",f"- Version: {VERSION}",f"- Horizon: {HORIZON_DAYS}d",
      f"- TRAIN: {ranges['TRAIN'][0]} → {ranges['TRAIN'][1]} | winner={tw} control={tc}",
      f"- TEST: {ranges['TEST'][0]} → {ranges['TEST'][1]} | winner={vw} control={vc}","",
      "## Frozen TRAIN-only thresholds"]
    for k,(op,th) in spec.items():lines.append(f"- {k} {op} {th:.6g}")
    lines+=["","## Train vs Test / random / near-miss"]
    for split in ("TRAIN","TEST"):
      for regime in ("ALL","UP","SIDEWAYS","DOWN"):
        for group in ("SIGNAL","NEAR_MISS","RANDOM_CONTROL"):
          r=mrow(split,regime,group,10)
          if r: lines.append(f"- {split}/{regime}/{group} +10: n={r['n']} precision={r['precision']} recall={r['recall']} random={r['baseline_rate']} lift={r['lift_vs_random']}")
    lines+=["",f"## Verdict: {verdict}","","Survivorship caveat: active-pair archive only until delisted backfill is complete."]
    with open("history_oos_report.md","w",encoding="utf-8") as fp:fp.write("\n".join(lines)+"\n")
    return data

def main():
    if not os.path.exists(DB):
        print("OOS: history_miner.db yok"); return
    budget=int(os.getenv("HISTORY_OOS_FEATURE_BUDGET","120"))
    run=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c); rows=all_cases(c)
        cut=split_cutoff(rows); populate_split(c,rows,cut); c.commit()
        # Important: feature mining is label-agnostic and outcome-blind.
        fill_features(c,rows,budget)
        spec,frozen=freeze_spec(c)
        done=c.execute("SELECT COUNT(*) FROM oos_point_features WHERE version=? AND feature_status='DONE'",(VERSION,)).fetchone()[0]
        missing=len(rows)-done
        verdict="WAITING_FEATURE_COVERAGE"
        if frozen:
            build_results(c,spec); metrics(c); data=report(c,cut,spec); verdict=data["verdict"]
        tw=c.execute("SELECT COUNT(*) FROM oos_split WHERE version=? AND split='TRAIN' AND label='RISE'",(VERSION,)).fetchone()[0]
        tc=c.execute("SELECT COUNT(*) FROM oos_split WHERE version=? AND split='TRAIN' AND label='CONTROL'",(VERSION,)).fetchone()[0]
        vw=c.execute("SELECT COUNT(*) FROM oos_split WHERE version=? AND split='TEST' AND label='RISE'",(VERSION,)).fetchone()[0]
        vc=c.execute("SELECT COUNT(*) FROM oos_split WHERE version=? AND split='TEST' AND label='CONTROL'",(VERSION,)).fetchone()[0]
        rr=c.execute("SELECT MIN(anchor_ts),MAX(anchor_ts) FROM oos_split WHERE version=? AND split='TRAIN'",(VERSION,)).fetchone()
        ss=c.execute("SELECT MIN(anchor_ts),MAX(anchor_ts) FROM oos_split WHERE version=? AND split='TEST'",(VERSION,)).fetchone()
        notes=json.dumps({"no_lookahead":True,"split":"chronological_70_30","signal_price":"last_closed_hour_of_anchor_day",
          "outcomes_start_after_signal":True,"hourly_alignment":"anchor_day_end_not_future_onset","random_seed":SEED},separators=(",",":"))
        c.execute("INSERT OR REPLACE INTO oos_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (run,datetime.now(timezone.utc).isoformat(),cut,rr[0],rr[1],ss[0],ss[1],tw,tc,vw,vc,done,missing,int(frozen),verdict,notes,VERSION))
        c.commit()
        print("HISTORY OOS",{"cutoff":utc(cut),"train":[tw,tc],"test":[vw,vc],
          "feature_done":done,"feature_missing":missing,"spec_frozen":frozen,"verdict":verdict})
        if frozen:
            for r in c.execute("""SELECT split,regime,group_name,target_pct,n,precision,recall,baseline_rate,lift_vs_random
              FROM oos_metrics WHERE version=? AND target_pct=10 ORDER BY split,regime,group_name""",(VERSION,)):
                print(dict(r))

if __name__=="__main__":main()
