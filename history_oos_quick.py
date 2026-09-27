#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast representative chronological OOS validation.

Uses the same point-in-time feature logic as history_oos_validation.py but tests
only a deterministic, stratified subsample:
- chronological 70/30 split computed on the full archive
- up to 1500 RISE + 1500 CONTROL from TRAIN
- up to 1500 RISE + 1500 CONTROL from TEST
- sampling is stratified by BTC regime and calendar quarter
- TRAIN is fully attempted before thresholds are frozen
- TEST outcomes are evaluated only after the TRAIN-only spec is frozen
"""
from __future__ import annotations
import hashlib, json, math, os, random, sqlite3
from datetime import datetime, timezone

import history_oos_validation as base
from binance_notify import resolve_chat_id, send_telegram

DB=os.getenv("HISTORY_DB","history_miner.db")
VERSION="history-oos-quick-sample-v1-20260927"
TARGET_PER_CLASS=int(os.getenv("HISTORY_OOS_QUICK_TARGET","1500"))
BUDGET=int(os.getenv("HISTORY_OOS_QUICK_BUDGET","900"))
SEED=27092026

base.VERSION=VERSION

def con():
    return base.con()

def quarter(ts):
    d=datetime.fromtimestamp(int(ts),timezone.utc)
    return f"{d.year:04d}-Q{(d.month-1)//3+1}"

def stable_seed(*parts):
    h=hashlib.sha256(("|".join(map(str,parts))).encode()).hexdigest()
    return int(h[:16],16)

def stratified_sample(c,rows,cut,split,label,target):
    pool=[r for r in rows if r["label"]==label and (("TRAIN" if int(r["event_ts"])<=cut else "TEST")==split)]
    if len(pool)<=target:
        return list(pool)
    strata={}
    for r in pool:
        reg=base.btc_regime(c,r["event_ts"])
        key=(reg,quarter(r["event_ts"]))
        strata.setdefault(key,[]).append(r)
    total=len(pool)
    quotas={}
    used=0
    rema=[]
    for k,g in strata.items():
        exact=target*len(g)/total
        q=min(len(g),int(math.floor(exact)))
        quotas[k]=q; used+=q
        rema.append((exact-q,k))
    for _frac,k in sorted(rema,reverse=True):
        if used>=target: break
        if quotas[k]<len(strata[k]):
            quotas[k]+=1; used+=1
    # If rounding/caps left room, fill from remaining strata deterministically.
    keys=sorted(strata)
    i=0
    while used<target and keys:
        k=keys[i%len(keys)]
        if quotas[k]<len(strata[k]):
            quotas[k]+=1; used+=1
        i+=1
        if i>target*len(keys)*2: break
    out=[]
    for k in sorted(strata):
        g=sorted(strata[k],key=lambda r:(int(r["event_ts"]),r["pair"]))
        rng=random.Random(stable_seed(SEED,split,label,k))
        idx=list(range(len(g))); rng.shuffle(idx)
        out.extend(g[i] for i in idx[:quotas[k]])
    return sorted(out,key=lambda r:(int(r["event_ts"]),r["pair"],r["label"]))

def populate_sample_split(c,sampled,cut):
    c.execute("DELETE FROM oos_split WHERE version=?",(VERSION,))
    for r in sampled:
        split="TRAIN" if int(r["event_ts"])<=cut else "TEST"
        c.execute("INSERT OR REPLACE INTO oos_split VALUES(?,?,?,?,?,?)",
          (r["pair"],int(r["event_ts"]),r["label"],split,base.btc_regime(c,r["event_ts"]),VERSION))
    c.commit()

def attempted_keys(c):
    return {(r["pair"],int(r["anchor_ts"]),r["label"]) for r in c.execute(
      "SELECT pair,anchor_ts,label FROM oos_point_features WHERE version=?",(VERSION,))}

def fill_once(c,rows,budget):
    seen=attempted_keys(c)
    due=[r for r in rows if (r["pair"],int(r["event_ts"]),r["label"]) not in seen]
    attempted=0
    for r in due[:budget]:
        attempted+=1
        try:
            sig,price,d24,d1,acc,ret,per,r3,vr,src=base.point_features(r["pair"],r["event_ts"])
            c.execute("""INSERT OR REPLACE INTO oos_point_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (r["pair"],int(r["event_ts"]),r["label"],sig,price,
               base.f(r["dist_high_90d"]),base.f(r["drawdown_30d"]),d24,d1,acc,ret,per,r3,vr,
               "DONE","SOURCE="+src,VERSION))
        except Exception as e:
            c.execute("""INSERT OR REPLACE INTO oos_point_features VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (r["pair"],int(r["event_ts"]),r["label"],int(r["event_ts"])+82800,
               float(r["pre_price"]),base.f(r["dist_high_90d"]),base.f(r["drawdown_30d"]),
               None,None,None,None,None,None,None,"ERROR",str(e)[:220],VERSION))
        if attempted%25==0:c.commit()
    c.commit()
    return attempted

def class_counts(c,split):
    rs=c.execute("""SELECT label,COUNT(*) n FROM oos_split
      WHERE version=? AND split=? GROUP BY label""",(VERSION,split)).fetchall()
    d={r["label"]:int(r["n"]) for r in rs}
    return d.get("RISE",0),d.get("CONTROL",0)

def attempted_count(c,split):
    return c.execute("""SELECT COUNT(*) FROM oos_point_features p JOIN oos_split s
      ON s.pair=p.pair AND s.anchor_ts=p.anchor_ts AND s.label=p.label AND s.version=p.version
      WHERE p.version=? AND s.split=?""",(VERSION,split)).fetchone()[0]

def done_count(c,split):
    return c.execute("""SELECT COUNT(*) FROM oos_point_features p JOIN oos_split s
      ON s.pair=p.pair AND s.anchor_ts=p.anchor_ts AND s.label=p.label AND s.version=p.version
      WHERE p.version=? AND s.split=? AND p.feature_status='DONE'""",(VERSION,split)).fetchone()[0]

def custom_report(c,cut,spec):
    m=base.metrics(c)
    rows=[dict(r) for r in c.execute("""SELECT * FROM oos_metrics WHERE version=?
      ORDER BY split,regime,group_name,target_pct""",(VERSION,))]
    def mr(split,regime,group,t):
        return next((r for r in rows if r["split"]==split and r["regime"]==regime
                     and r["group_name"]==group and r["target_pct"]==t),None)
    tr10=mr("TRAIN","ALL","SIGNAL",10); te10=mr("TEST","ALL","SIGNAL",10)
    ra10=mr("TEST","ALL","RANDOM_CONTROL",10); nm10=mr("TEST","ALL","NEAR_MISS",10)
    primary_p=primary_diff=None; verdict="INSUFFICIENT"
    if te10 and ra10 and te10["n"]>=20 and ra10["n"]>=20:
        primary_diff=(te10["precision"] or 0)-(ra10["precision"] or 0)
        primary_p=base.fisher_greater(int(te10["hits"]),int(te10["n"]-te10["hits"]),
                                      int(ra10["hits"]),int(ra10["n"]-ra10["hits"]))
        verdict="OOS_EDGE_PRESENT" if primary_diff>0 and primary_p is not None and primary_p<0.05 else "NO_CONFIRMED_OOS_EDGE"
    ranges={}
    for split in ("TRAIN","TEST"):
        r=c.execute("SELECT MIN(anchor_ts),MAX(anchor_ts) FROM oos_split WHERE version=? AND split=?",
                    (VERSION,split)).fetchone()
        ranges[split]=(base.utc(r[0]) if r and r[0] else None,base.utc(r[1]) if r and r[1] else None)
    tw,tc=class_counts(c,"TRAIN"); vw,vc=class_counts(c,"TEST")
    data={"version":VERSION,"sample_target_per_class":TARGET_PER_CLASS,"cutoff_date":base.utc(cut),
      "train":{"date_range":ranges["TRAIN"],"winners":tw,"controls":tc,"done":done_count(c,"TRAIN")},
      "test":{"date_range":ranges["TEST"],"winners":vw,"controls":vc,"done":done_count(c,"TEST")},
      "spec":{k:{"operator":v[0],"threshold":v[1]} for k,v in spec.items()},
      "train_precision_10":None if not tr10 else tr10["precision"],
      "test_precision_10":None if not te10 else te10["precision"],
      "test_random_10":None if not ra10 else ra10["precision"],
      "test_near_miss_10":None if not nm10 else nm10["precision"],
      "test_recall":None if not te10 else te10["recall"],
      "primary_test":{"precision_diff_vs_random":primary_diff,"fisher_one_sided_p":primary_p},
      "metrics":rows,"verdict":verdict,
      "sampling":"fixed-seed stratified by BTC regime + calendar quarter; outcomes never used for sampling",
      "caveat":"Active-pair historical archive still has incomplete delisted-market coverage."}
    with open("history_oos_quick_report.json","w",encoding="utf-8") as fp:
        json.dump(data,fp,ensure_ascii=False,indent=2)
    return data

def notify(c,data):
    token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    configured=(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:return
    fp=hashlib.sha256(json.dumps({
      "v":data["version"],"verdict":data["verdict"],
      "tr":data["train_precision_10"],"te":data["test_precision_10"],
      "ra":data["test_random_10"],"rec":data["test_recall"]
    },sort_keys=True).encode()).hexdigest()
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_status_state(
      channel TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,sent_utc TEXT NOT NULL)""")
    prev=c.execute("SELECT fingerprint FROM telegram_status_state WHERE channel='history-oos-quick'").fetchone()
    if prev and prev[0]==fp:return
    def pc(v):
        return "—" if v is None else f"%{100*float(v):.1f}"
    verdict={"OOS_EDGE_PRESENT":"EDGE DOĞRULANDI",
             "NO_CONFIRMED_OOS_EDGE":"EDGE DOĞRULANMADI",
             "INSUFFICIENT":"VERİ YETERSİZ"}.get(data["verdict"],data["verdict"])
    msg="\n".join([
      "🧪 AVCI | HIZLI OOS SONUCU",
      f"• Train +%10: {pc(data['train_precision_10'])}",
      f"• Test +%10: {pc(data['test_precision_10'])}",
      f"• Random control: {pc(data['test_random_10'])}",
      f"• Near-miss: {pc(data['test_near_miss_10'])}",
      f"• Recall: {pc(data['test_recall'])}",
      f"• Sonuç: {verdict}",
    ])
    chat=resolve_chat_id(token,configured,DB,"History OOS Quick")
    send_telegram(token,chat,msg)
    c.execute("""INSERT INTO telegram_status_state(channel,fingerprint,sent_utc)
      VALUES('history-oos-quick',?,?)
      ON CONFLICT(channel) DO UPDATE SET fingerprint=excluded.fingerprint,sent_utc=excluded.sent_utc""",
      (fp,datetime.now(timezone.utc).isoformat()))
    c.commit()

def main():
    if not os.path.exists(DB):
        print("QUICK OOS: history_miner.db yok"); return
    with con() as c:
        base.init_db(c)
        rows=base.all_cases(c); cut=base.split_cutoff(rows)
        sampled=[]
        for split in ("TRAIN","TEST"):
            for label in ("RISE","CONTROL"):
                sampled += stratified_sample(c,rows,cut,split,label,TARGET_PER_CLASS)
        populate_sample_split(c,sampled,cut)
        train=[r for r in sampled if int(r["event_ts"])<=cut]
        test=[r for r in sampled if int(r["event_ts"])>cut]
        ta=attempted_count(c,"TRAIN"); total_train=len(train)
        used=0
        if ta<total_train:
            used=fill_once(c,train,min(BUDGET,total_train-ta))
        ta=attempted_count(c,"TRAIN")
        spec={}; frozen=False
        if ta>=total_train:
            spec,frozen=base.freeze_spec(c)
        if frozen and used<BUDGET:
            xa=attempted_count(c,"TEST")
            if xa<len(test):
                fill_once(c,test,min(BUDGET-used,len(test)-xa))
        xa=attempted_count(c,"TEST")
        verdict="WAITING_TRAIN"
        if frozen and xa>=len(test):
            base.build_results(c,spec)
            data=custom_report(c,cut,spec)
            verdict=data["verdict"]; notify(c,data)
        tw,tc=class_counts(c,"TRAIN"); vw,vc=class_counts(c,"TEST")
        print("QUICK OOS",{
          "cutoff":base.utc(cut),"target_per_class":TARGET_PER_CLASS,
          "train_sample":[tw,tc],"test_sample":[vw,vc],
          "train_attempted":attempted_count(c,"TRAIN"),"train_done":done_count(c,"TRAIN"),
          "test_attempted":attempted_count(c,"TEST"),"test_done":done_count(c,"TEST"),
          "spec_frozen":frozen,"verdict":verdict,"budget":BUDGET})

if __name__=="__main__":main()
