#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adversarial/red-team validation for Gate security filters.

Uses only independently labelled corpus rows. If ground truth is not available,
reports NEED_GROUND_TRUTH rather than inventing labels.
"""
import csv,json,os,sqlite3
from datetime import datetime,timezone

VERSION="security-redteam-v1-20260926"
CORPUS=os.getenv("SECURITY_CORPUS","security_adversarial_corpus.csv")
OBS=os.getenv("AVCI_DB","avci2.db")
TARGET_BAD=30
TARGET_GOOD=30

def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t):return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()

def load():
    if not os.path.exists(CORPUS):return []
    with open(CORPUS,newline="",encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("label") or "").upper() in ("BAD","GOOD")]

def evidence(c,row):
    detected=False; reasons=[]; matched=False
    vid=(row.get("validation_event_id") or "").strip()
    contract=(row.get("contract") or "").strip()
    if vid and table(c,"gate_security_confidence_history"):
        cc=cols(c,"gate_security_confidence_history")
        if "validation_event_id" in cc:
            q=c.execute("""SELECT * FROM gate_security_confidence_history
              WHERE validation_event_id=? ORDER BY rowid DESC LIMIT 1""",(vid,)).fetchone()
            if q:
                matched=True;d=dict(q)
                if int(d.get("hard_veto") or 0)==1:
                    detected=True;reasons.append("HARD_VETO")
                if str(d.get("label") or "").upper()=="BLOCKED":
                    detected=True;reasons.append("BLOCKED_SECURITY_LABEL")
    if contract and table(c,"gate_deception_evidence"):
        cc=cols(c,"gate_deception_evidence")
        key=next((k for k in ("token_contract","contract","asset_key") if k in cc),None)
        if key:
            q=c.execute(f"""SELECT * FROM gate_deception_evidence
              WHERE {key}=? ORDER BY rowid DESC LIMIT 1""",(contract,)).fetchone()
            if q:
                matched=True;d=dict(q)
                if str(d.get("deception_risk") or "").upper()=="HIGH":
                    detected=True;reasons.append("DECEPTION_HIGH")
    return matched,detected,reasons

def main():
    rows=load()
    report={"version":VERSION,"generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "corpus_rows":len(rows),"target_bad":TARGET_BAD,"target_good":TARGET_GOOD}
    bad=[r for r in rows if r["label"].upper()=="BAD"];good=[r for r in rows if r["label"].upper()=="GOOD"]
    if not rows or not os.path.exists(OBS):
        report.update({"status":"NEED_GROUND_TRUTH" if not rows else "OBS_DB_MISSING",
          "missing_bad":max(0,TARGET_BAD-len(bad)),"missing_good":max(0,TARGET_GOOD-len(good)),
          "requirement":"Independent labelled historical rug/honeypot/manipulation and healthy controls."})
    else:
        with sqlite3.connect(OBS) as c:
            c.row_factory=sqlite3.Row
            results=[]
            for r in rows:
                matched,detected,reasons=evidence(c,r)
                results.append({**r,"matched":matched,"detected":detected,"reasons":reasons})
            badm=[r for r in results if r["label"].upper()=="BAD" and r["matched"]]
            goodm=[r for r in results if r["label"].upper()=="GOOD" and r["matched"]]
            tp=sum(r["detected"] for r in badm);fp=sum(r["detected"] for r in goodm)
            recall=tp/len(badm) if badm else None
            fpr=fp/len(goodm) if goodm else None
            if len(bad)<TARGET_BAD or len(good)<TARGET_GOOD:
                status="CORPUS_INCOMPLETE"
            elif len(badm)>=TARGET_BAD and len(goodm)>=TARGET_GOOD:
                status="READY_REPLAY_VALIDATED"
            else:
                status="CORPUS_READY_REPLAY_PENDING"
            report.update({"status":status,"bad_total":len(bad),"good_total":len(good),
              "bad_matched":len(badm),"good_matched":len(goodm),"security_recall":recall,
              "healthy_false_positive_rate":fpr,"missing_bad":max(0,TARGET_BAD-len(bad)),
              "missing_good":max(0,TARGET_GOOD-len(good)),"results":results})
    open("security_redteam_report.json","w",encoding="utf-8").write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
