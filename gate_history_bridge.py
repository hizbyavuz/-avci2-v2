#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge frozen Gate History Miner evidence into the live Gate research state.

Uses ONLY frozen V4/V5 history specifications/results:
- computes the current pair's V4 pattern features from archived Gate daily bars,
- checks P2/P3 membership using frozen discovery-only thresholds,
- computes current V5 VOL/DIP activation from closed Gate 1h candles,
- treats a matched V5 validation row as strong history support ONLY when BH-FDR passed.

Research-only. It never changes V5 scanner/security/selection thresholds.
"""
from __future__ import annotations

import json, math, os, sqlite3, statistics, time
from datetime import datetime, timezone
from urllib import parse, request, error

OBS_DB=os.getenv("AVCI_DB","avci2.db")
HISTORY_DB=os.getenv("HISTORY_DB",".history-state/history_miner.db")
VERSION="gate-history-bridge-v1-20260927"
V4_VERSION="history-v4-pattern-first-v0.1-20260925"
V5_VERSION="history-v5-activation-v0.4-20260925"
MAX_PAIRS=int(os.getenv("GATE_HISTORY_BRIDGE_MAX_PAIRS","12"))
BASE="https://api.gateio.ws/api/v4/spot/candlesticks"

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def realized_vol_30(closes):
    rs=[]
    for a,b in zip(closes,closes[1:]):
        if a>0 and b>0: rs.append(math.log(b/a))
    return statistics.pstdev(rs)*100 if len(rs)>=20 else None

def current_pairs(c,batch):
    wanted=[]
    seen=set()
    def add(pair,priority):
        if pair and pair not in seen:
            seen.add(pair); wanted.append((priority,pair))
    if table(c,"gate_candidate_evidence") and table(c,"gate_spot_contracts"):
        rows=c.execute("""SELECT DISTINCT s.pair
          FROM gate_candidate_evidence e
          JOIN gate_spot_contracts s
            ON s.network_id=e.network_id AND s.token_contract=e.token_contract
          WHERE e.batch_id=?""",(batch,)).fetchall()
        for r in rows:add(r["pair"],0)
    if table(c,"gate_spot_watch"):
        rows=c.execute("""SELECT pair FROM gate_spot_watch
          WHERE status='PAPER_WATCH' ORDER BY rowid DESC LIMIT 10""").fetchall()
        for r in rows:add(r["pair"],1)
    if table(c,"gate_opportunity_observations"):
        cols={r[1] for r in c.execute("PRAGMA table_info(gate_opportunity_observations)")}
        if "early_watch" in cols:
            rows=c.execute("""SELECT pair FROM gate_opportunity_observations
              WHERE batch_id=? AND early_watch=1
              ORDER BY COALESCE(volume_acceleration,0) DESC LIMIT 10""",(batch,)).fetchall()
            for r in rows:add(r["pair"],2)
    return [p for _,p in sorted(wanted)[:MAX_PAIRS]]

def latest_v4_features(h,pair):
    rows=h.execute("""SELECT ts,high,close FROM daily_bars
      WHERE pair=? ORDER BY ts DESC LIMIT 91""",(pair,)).fetchall()
    if len(rows)<91:return None
    rows=list(reversed(rows))
    highs=[float(r["high"]) for r in rows]
    closes=[float(r["close"]) for r in rows]
    cur=closes[-1]
    hi90=max(highs[-90:])
    hi30=max(highs[-30:])
    rv=realized_vol_30(closes[-31:])
    if rv is None:return None
    return {
      "anchor_ts":int(rows[-1]["ts"]),
      "dist_high_90d":pct(hi90,cur),
      "drawdown_30d":pct(hi30,cur),
      "realized_vol_30d":rv,
    }

def load_v4_spec(h):
    if not table(h,"v4_pattern_spec"):return {}
    rows=h.execute("""SELECT pattern_name,feature,operator,threshold
      FROM v4_pattern_spec WHERE version=?""",(V4_VERSION,)).fetchall()
    out={}
    for r in rows:
        if r["pattern_name"] in ("P2_FAR_PLUS_VOL","P3_FAR_VOL_DRAWDOWN"):
            out.setdefault(r["pattern_name"],[]).append(
              (r["feature"],r["operator"],float(r["threshold"])))
    return out

def matches(vals,conds):
    for feature,op,thr in conds:
        x=vals.get(feature)
        if x is None:return False
        if op=="<=" and not x<=thr:return False
        if op==">=" and not x>=thr:return False
    return True

def fetch_hourly(pair):
    now=int(time.time())
    start=now-53*3600
    params=parse.urlencode({"currency_pair":pair,"interval":"1h","from":start,"to":now})
    try:
        with request.urlopen(BASE+"?"+params,timeout=20) as r:
            data=json.load(r)
    except Exception:
        return []
    out=[]
    for row in data if isinstance(data,list) else []:
        try:
            ts=int(float(row[0])); qv=float(row[1]); close=float(row[2])
            high=float(row[3]); low=float(row[4])
            if close>0 and high>0 and low>0:
                out.append({"ts":ts,"qv":qv,"close":close,"high":high,"low":low})
        except Exception:
            continue
    out.sort(key=lambda x:x["ts"])
    # Current/open hour can be incomplete; only use bars whose hour is closed.
    closed_before=(now//3600)*3600
    return [x for x in out if x["ts"]<closed_before]

def activation_values(hourly):
    if len(hourly)<28:return None
    cur=hourly[-1]; idx=len(hourly)-1
    prev=hourly[max(0,idx-24):idx]
    prev_q=[x["qv"] for x in prev if x["qv"]>=0]
    base=statistics.median(prev_q) if prev_q else None
    last3=[x["qv"] for x in hourly[max(0,idx-2):idx+1] if x["qv"]>=0]
    vr=(sum(last3)/len(last3)/base) if last3 and base and base>0 else None
    last24=hourly[max(0,idx-23):idx+1]
    lo=min(x["low"] for x in last24) if last24 else None
    dip=pct(lo,cur["close"]) if lo else None
    if vr is None or dip is None:return None
    return {"vol_ratio_3_24":float(vr),"dist_low_24h":float(dip)}

def load_v5_spec(h):
    if not table(h,"v5_activation_spec"):return {}
    rows=h.execute("""SELECT feature,operator,threshold FROM v5_activation_spec
      WHERE version=?""",(V5_VERSION,)).fetchall()
    return {r["feature"]:(r["operator"],float(r["threshold"])) for r in rows}

def activation_name(vals,spec):
    if not vals or len(spec)<2:return None
    def hit(feat):
        if feat not in vals or feat not in spec:return False
        op,thr=spec[feat]; x=vals[feat]
        return x>=thr if op==">=" else x<=thr
    vol=hit("vol_ratio_3_24"); dip=hit("dist_low_24h")
    if vol and dip:return "VOL_DIP"
    if vol:return "VOL"
    if dip:return "DIP"
    return "BASE"

def validation_summary(h,patterns,act):
    best=None
    if patterns and act and act!="BASE" and table(h,"v5_activation_results"):
        qmarks=",".join("?" for _ in patterns)
        rows=h.execute(f"""SELECT pattern_name,activation_name,horizon_days,threshold_pct,
          signal_n,hit_n,precision,lift_vs_parent,q_value,fdr_pass
          FROM v5_activation_results
          WHERE version=? AND split='VALIDATION' AND pattern_name IN ({qmarks})
            AND activation_name=? AND fdr_pass=1
          ORDER BY q_value ASC,COALESCE(lift_vs_parent,0) DESC""",
          (V5_VERSION,*patterns,act)).fetchall()
        if rows:best=dict(rows[0])
    if best:
        return "HISTORY_FDR_SUPPORTED",best
    # Pattern-only historical match stays observational; it cannot be promoted
    # to strong evidence without the activation family passing FDR.
    if patterns and table(h,"v4_pattern_results"):
        qmarks=",".join("?" for _ in patterns)
        rows=h.execute(f"""SELECT pattern_name,horizon_days,threshold_pct,signal_n,hit_n,
          precision,raw_base_rate,lift_vs_raw_base
          FROM v4_pattern_results
          WHERE version=? AND split='VALIDATION' AND pattern_name IN ({qmarks})
          ORDER BY COALESCE(lift_vs_raw_base,0) DESC,signal_n DESC""",
          (V4_VERSION,*patterns)).fetchall()
        if rows:return "HISTORY_PATTERN_ONLY",dict(rows[0])
    return ("NO_HISTORY_MATCH" if not patterns else "HISTORY_UNVALIDATED"),None

def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS gate_history_bridge(
      batch_id TEXT NOT NULL,pair TEXT NOT NULL,network_id TEXT,token_contract TEXT,
      status TEXT NOT NULL,patterns_json TEXT NOT NULL,
      v4_features_json TEXT,activation_name TEXT,activation_json TEXT,
      validation_json TEXT,history_anchor_ts INTEGER,version TEXT NOT NULL,
      created_at_utc TEXT NOT NULL,
      PRIMARY KEY(batch_id,pair,version)
    )""")

def main():
    if not os.path.exists(OBS_DB):
        print("Gate history bridge: avci2.db yok");return
    if not os.path.exists(HISTORY_DB):
        print("Gate history bridge: history DB yok");return
    with sqlite3.connect(OBS_DB,timeout=30) as c, sqlite3.connect(HISTORY_DB,timeout=30) as h:
        c.row_factory=h.row_factory=sqlite3.Row;ensure(c)
        health=c.execute("""SELECT batch_id FROM gate_scan_health WHERE status='VALID'
          ORDER BY scan_ts DESC LIMIT 1""").fetchone()
        if not health:
            print("Gate history bridge: valid scan yok");return
        batch=health["batch_id"]; pairs=current_pairs(c,batch)
        if not table(h,"daily_bars"):
            print("Gate history bridge: daily_bars yok");return
        v4spec=load_v4_spec(h);v5spec=load_v5_spec(h)
        counts={}
        for pair in pairs:
            feat=latest_v4_features(h,pair)
            patterns=[name for name,conds in v4spec.items() if feat and matches(feat,conds)]
            av=activation_values(fetch_hourly(pair)) if patterns else None
            act=activation_name(av,v5spec)
            status,valid=validation_summary(h,patterns,act)
            mapped=c.execute("""SELECT network_id,token_contract FROM gate_spot_contracts
              WHERE pair=? ORDER BY network_id,token_contract LIMIT 1""",(pair,)).fetchone()
            if feat is None:
                status="INSUFFICIENT_HISTORY"
            counts[status]=counts.get(status,0)+1
            c.execute("""INSERT OR REPLACE INTO gate_history_bridge VALUES(
              ?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
              batch,pair,mapped["network_id"] if mapped else None,
              mapped["token_contract"] if mapped else None,status,
              json.dumps(patterns),json.dumps(feat) if feat else None,act,
              json.dumps(av) if av else None,json.dumps(valid) if valid else None,
              int(feat["anchor_ts"]) if feat else None,VERSION,
              datetime.now(timezone.utc).isoformat()))
            time.sleep(0.12)
        c.commit()
        print(f"Gate history bridge: pairs={len(pairs)} {counts}")

if __name__=="__main__":main()
