#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""History Miner Twin Test v0.1.

Matched-pair / case-control research layer:
- Start from frozen V4 P2/P3 first-entry signals.
- Define winners only from future 7d outcomes.
- Match each winner to a near-miss that looked similar BEFORE the outcome:
  same P2/P3 pattern, same BTC regime when known, within +/-45 days, and close
  on dist_high_90d, realized_vol_30d and drawdown_30d.
- Compare only pre-signal "change" features. No future data enters features.
- Discovery/validation split stays frozen at 2025-09-01.
- Separate version/tables; V0-V5 remain untouched.
"""
from __future__ import annotations
import math, os, sqlite3, statistics, time, json
from datetime import datetime, timezone
from urllib import request, parse

import history_validation_v4 as v4
import history_validation as hv

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-twin-v0.1-20260925"
V2_VERSION="history-v2-outcomes-matched-v0.1-20260924"
CUTOFF=os.getenv("HISTORY_VALIDATION_CUTOFF","2025-09-01")
WINDOW_DAYS=45
PATTERNS=("P2_FAR_PLUS_VOL","P3_FAR_VOL_DRAWDOWN")
TARGET_SPECS=((100,50),(50,20))  # winner target, near-miss must fail lower target
PROGRESS_INTERVAL_SEC=int(os.getenv("TWIN_PROGRESS_INTERVAL_SEC","1200"))

def send_progress(done,total):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat or total<=0:
        return
    pct_done=max(0.0,min(100.0,100.0*done/total))
    pct_left=max(0.0,100.0-pct_done)
    text=f"🧬 Twin Test | %{pct_done:.0f} tamamlandı | %{pct_left:.0f} kaldı"
    try:
        data=parse.urlencode({"chat_id":chat,"text":text,"disable_web_page_preview":"true"}).encode()
        req=request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=data,method="POST")
        with request.urlopen(req,timeout=20) as resp:
            resp.read()
    except Exception as e:
        print(f"Twin progress Telegram error: {type(e).__name__}: {str(e)[:120]}")

FEATURES=(
 ("ret_1d","1 günlük fiyat değişimi"),
 ("ret_3d","3 günlük fiyat değişimi"),
 ("vol_accel_3v7","son 3g hacim / önceki 7g"),
 ("range_accel_3v7","son 3g hareket genişliği / önceki 7g"),
 ("green_ratio_3d","son 3 gün yeşil gün oranı"),
 ("dist_low_7d","7 günlük dipten uzaklaşma"),
)

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory=sqlite3.Row
    return c

def pct(a,b):
    return 100.0*(b/a-1.0) if a and b and a>0 else None

def med(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return statistics.median(xs) if xs else None

def mad(xs,center=None):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not xs:return None
    center=med(xs) if center is None else center
    return med([abs(x-center) for x in xs])

def init_db(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS twin_matches(
      split TEXT NOT NULL,
      pattern_name TEXT NOT NULL,
      target_pct INTEGER NOT NULL,
      winner_pair TEXT NOT NULL,
      winner_ts INTEGER NOT NULL,
      near_pair TEXT NOT NULL,
      near_ts INTEGER NOT NULL,
      btc_regime TEXT NOT NULL,
      match_distance REAL NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,pattern_name,target_pct,winner_pair,winner_ts,version)
    );
    CREATE TABLE IF NOT EXISTS twin_results(
      split TEXT NOT NULL,
      pattern_name TEXT NOT NULL,
      target_pct INTEGER NOT NULL,
      feature TEXT NOT NULL,
      pair_n INTEGER NOT NULL,
      winner_median REAL,
      near_median REAL,
      paired_effect REAL,
      same_direction INTEGER NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(split,pattern_name,target_pct,feature,version)
    );
    CREATE TABLE IF NOT EXISTS twin_runs(
      run_id TEXT PRIMARY KEY,
      started_utc TEXT NOT NULL,
      finished_utc TEXT,
      matches INTEGER NOT NULL DEFAULT 0,
      result_rows INTEGER NOT NULL DEFAULT 0,
      notes TEXT,
      version TEXT NOT NULL
    );
    """)

def day_features(c,pair,ts_):
    rows=c.execute("""SELECT ts,open,high,low,close,volume_quote
      FROM daily_bars WHERE pair=? AND ts<=? ORDER BY ts DESC LIMIT 16""",
      (pair,int(ts_))).fetchall()
    if len(rows)<11:return None
    r=list(reversed(rows))
    close=[float(x["close"]) for x in r]
    high=[float(x["high"]) for x in r]
    low=[float(x["low"]) for x in r]
    op=[float(x["open"]) for x in r]
    vol=[float(x["volume_quote"] or 0.0) for x in r]
    if min(close)<=0:return None
    ret1=pct(close[-2],close[-1])
    ret3=pct(close[-4],close[-1])
    last3v=sum(vol[-3:])/3
    prev7v=sum(vol[-10:-3])/7
    vr=last3v/prev7v if prev7v>0 else None
    ranges=[100*(h/l-1) if l>0 else None for h,l in zip(high,low)]
    a=med(ranges[-3:]); b=med(ranges[-10:-3])
    rr=a/b if a is not None and b not in (None,0) else None
    gr=sum(1 for o_,cl in zip(op[-3:],close[-3:]) if cl>o_)/3
    lo7=min(low[-7:])
    dl=pct(lo7,close[-1])
    vals=(ret1,ret3,vr,rr,gr,dl)
    if any(x is None or not math.isfinite(float(x)) for x in vals):return None
    return dict(zip([x[0] for x in FEATURES],map(float,vals)))

def static_features(c):
    return v4.build_anchor_features(c)

def candidate_signals(c):
    feats=static_features(c)
    spec=v4.freeze_spec(c,feats)
    out={}
    for split in ("DISCOVERY","VALIDATION"):
        raws=v4.raw_candidates(feats,spec,split)
        out[split]={}
        for p in PATTERNS:
            out[split][p]=v4.first_entry_signals(raws.get(p,[]),30)
    return feats,out

def outcome_maps(c):
    rows=c.execute("""SELECT pair,anchor_ts,hit20,hit50,hit100
      FROM v2_outcomes WHERE version=? AND horizon_days=7""",(V2_VERSION,)).fetchall()
    return {(r["pair"],int(r["anchor_ts"])):{
      20:int(r["hit20"]),50:int(r["hit50"]),100:int(r["hit100"])
    } for r in rows}

def regime(c,pair,ts_):
    return hv.btc_regime(c,ts_)

def distance(a,b):
    # Frozen, simple robust scales from the already-established V4 profile.
    return (
      abs(a["dist_high_90d"]-b["dist_high_90d"])/20.0 +
      abs(a["realized_vol_30d"]-b["realized_vol_30d"])/5.0 +
      abs(a["drawdown_30d"]-b["drawdown_30d"])/20.0
    )

def build_matches(c,feats,signals,outs):
    c.execute("DELETE FROM twin_matches WHERE version=?",(VERSION,))
    all_rows=[]
    prepared=[]
    total=0
    for split in ("DISCOVERY","VALIDATION"):
      for p in PATTERNS:
        sig=[x for x in signals[split][p] if x in outs and x in feats]
        regs={x:regime(c,*x) for x in sig}
        for target,near_fail in TARGET_SPECS:
          winners=[x for x in sig if outs[x][target]==1]
          nears=[x for x in sig if outs[x][near_fail]==0]
          prepared.append((split,p,target,near_fail,winners,nears,regs))
          total+=len(winners)

    done=0
    last_notice=time.monotonic()
    for split,p,target,near_fail,winners,nears,regs in prepared:
          used=set()
          for w in winners:
            wr=regs[w]
            choices=[]
            for n in nears:
                if n in used or n[0]==w[0]:continue
                if abs(n[1]-w[1])>WINDOW_DAYS*86400:continue
                nr=regs[n]
                if wr!="UNKNOWN" and nr!="UNKNOWN" and wr!=nr:continue
                d=distance(feats[w],feats[n])
                choices.append((d,n,nr))
            if choices:
                choices.sort(key=lambda x:x[0])
                d,n,nr=choices[0]
                used.add(n)
                c.execute("""INSERT OR REPLACE INTO twin_matches
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (split,p,target,w[0],w[1],n[0],n[1],wr,float(d),VERSION))
                all_rows.append((split,p,target,w,n))
            done+=1
            now=time.monotonic()
            if now-last_notice>=PROGRESS_INTERVAL_SEC:
                send_progress(done,total)
                last_notice=now
    c.commit()
    if total:
        send_progress(total,total)
    return all_rows

def summarize(c,matches):
    c.execute("DELETE FROM twin_results WHERE version=?",(VERSION,))
    by={}
    for split,p,target,w,n in matches:
        wf=day_features(c,*w); nf=day_features(c,*n)
        if not wf or not nf:continue
        by.setdefault((split,p,target),[]).append((wf,nf))
    rows=[]
    for key,pairs in by.items():
        split,p,target=key
        for feat,label in FEATURES:
            wp=[a[feat] for a,b in pairs]; np=[b[feat] for a,b in pairs]
            dif=[a-b for a,b in zip(wp,np)]
            scale=mad(np)
            if scale in (None,0): scale=max(abs(med(np) or 0),1.0)
            eff=(med(dif)/scale) if dif else None
            same=sum(1 for x in dif if (x>0 and (med(dif) or 0)>0) or (x<0 and (med(dif) or 0)<0))
            same_dir=int(round(100*same/len(dif))) if dif else 0
            c.execute("""INSERT OR REPLACE INTO twin_results VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (split,p,target,feat,len(dif),med(wp),med(np),eff,same_dir,VERSION))
            rows.append((split,p,target,feat,len(dif),med(wp),med(np),eff,same_dir))
    c.commit()
    return rows

def main():
    if not os.path.exists(DB):
        print("Twin Test: DB yok");return
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with con() as c:
        init_db(c)
        feats,signals=candidate_signals(c)
        outs=outcome_maps(c)
        matches=build_matches(c,feats,signals,outs)
        rows=summarize(c,matches)
        note=("Matched-pair case-control: same V4 pattern, +/-45d, same BTC regime when known, "
              "static-profile distance frozen; features strictly pre-signal; 7d +100 vs fail +50 "
              "and +50 vs fail +20. Survivorship/wash-history limitations remain.")
        c.execute("""INSERT OR REPLACE INTO twin_runs VALUES(?,?,?,?,?,?,?)""",
          (run_id,datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat(),
           len(matches),len(rows),note,VERSION))
        c.commit()
        print(f"Twin Test: matches={len(matches)} result_rows={len(rows)}")
        for r in c.execute("""SELECT pattern_name,target_pct,feature,pair_n,winner_median,
          near_median,paired_effect,same_direction FROM twin_results
          WHERE version=? AND split='VALIDATION' AND pair_n>=20
          ORDER BY ABS(paired_effect) DESC LIMIT 12""",(VERSION,)):
            print(dict(r))

if __name__=="__main__":
    main()
