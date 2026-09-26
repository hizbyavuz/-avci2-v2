#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import independent public security ground-truth into Avci corpus.

Sources are external to Avci and retain provenance. This script never invents
labels and never changes scanner/security thresholds.

EVM labelled source:
- al-Jurjani/Honeypot-Detector ground_truth.csv (HoneyBadger + legitimate controls)

Additional BAD-only source:
- dianxiang-sun/rug_pull_dataset (validated ETH/BSC rug-pull incidents)

Solana research source:
- DeFiLabX/SolRPDS public CSV files. These are archived separately as raw
  research evidence because the paper's inactivity-based labels are not treated
  as equivalent to independently confirmed rug/healthy ground truth.
"""
from __future__ import annotations
import csv, io, json, os, re
from datetime import datetime, timezone
import requests

OUT=os.getenv("SECURITY_CORPUS_OUT","security_adversarial_corpus.csv")
SOL_RAW=os.getenv("SOLRPDS_RAW_OUT","solrpds_external_raw.csv")
REPORT=os.getenv("SECURITY_IMPORT_REPORT","security_external_import_report.json")
UA={"User-Agent":"avci-research-groundtruth/1.0"}

HONEY="https://raw.githubusercontent.com/al-Jurjani/Honeypot-Detector/main/data/labels/ground_truth.csv"
RUG="https://raw.githubusercontent.com/dianxiang-sun/rug_pull_dataset/main/rugpull_full_dataset_new.csv"
SOL=[
 "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset/CSV/2021.csv",
 "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset/CSV/2022.csv",
 "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset/CSV/2023.csv",
 "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset/CSV/Jan_2024-Nov_2024.csv",
]
FIELDS=["case_id","network_id","contract","label","incident_type","incident_time_utc",
        "validation_event_id","source_url","notes"]

def get(url,timeout=90):
    r=requests.get(url,headers=UA,timeout=timeout)
    r.raise_for_status()
    return r.text

def evm_address(x):
    x=(x or "").strip()
    return x.lower() if re.fullmatch(r"0x[a-fA-F0-9]{40}",x) else None

def load_existing():
    out={}
    if os.path.exists(OUT):
        try:
            with open(OUT,newline="",encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    key=(r.get("network_id",""),(r.get("contract") or "").lower(),r.get("label",""))
                    out[key]={k:r.get(k,"") for k in FIELDS}
        except Exception:
            pass
    return out

def import_honey(rows,stats):
    text=get(HONEY)
    for i,r in enumerate(csv.DictReader(io.StringIO(text)),1):
        a=evm_address(r.get("contract_id"))
        lab=(r.get("label") or "").lower()
        if not a or lab not in ("honeypot","legitimate"): continue
        label="BAD" if lab=="honeypot" else "GOOD"
        subtype=(r.get("honeypot_type") or ("LEGITIMATE_CONTROL" if label=="GOOD" else "HONEYPOT"))
        key=("1",a,label)
        rows[key]={
          "case_id":f"HONEYBADGER-{i:05d}","network_id":"1","contract":a,"label":label,
          "incident_type":subtype.upper(),"incident_time_utc":"","validation_event_id":"",
          "source_url":"https://github.com/al-Jurjani/Honeypot-Detector",
          "notes":f"Independent labelled dataset; original source={r.get('source') or 'unknown'}"
        }
        stats["honeybadger_"+label.lower()]+=1

def import_rugs(rows,stats):
    text=get(RUG)
    rd=csv.DictReader(io.StringIO(text))
    for i,r in enumerate(rd,1):
        low={str(k).strip().lower():v for k,v in r.items()}
        a=evm_address(low.get("address") or low.get("contract") or low.get("contract_address"))
        if not a: continue
        chain=(low.get("chain") or "").strip().upper()
        network="56" if chain in ("BSC","BNB","BINANCE SMART CHAIN") else "1" if chain in ("ETH","ETHEREUM") else ""
        if not network: continue
        typ=(low.get("type") or low.get("root_causes") or "RUG_PULL").strip()
        src=(low.get("url") or low.get("sources") or "https://github.com/dianxiang-sun/rug_pull_dataset").strip()
        key=(network,a,"BAD")
        if key not in rows:
            rows[key]={
              "case_id":f"RUGDATA-{i:05d}","network_id":network,"contract":a,"label":"BAD",
              "incident_type":typ[:120],"incident_time_utc":"","validation_event_id":"",
              "source_url":src[:500],
              "notes":"Validated public rug-pull dataset; external to Avci."
            }
        stats["rug_dataset_bad"]+=1

def archive_solrpds(stats):
    total=0
    headers=set()
    with open(SOL_RAW,"w",newline="",encoding="utf-8") as out:
        writer=None
        for url in SOL:
            try:
                text=get(url,180)
                rd=csv.DictReader(io.StringIO(text))
                if not rd.fieldnames: continue
                if writer is None:
                    fields=["source_file"]+list(rd.fieldnames)
                    writer=csv.DictWriter(out,fieldnames=fields,extrasaction="ignore")
                    writer.writeheader()
                elif list(rd.fieldnames)!=fields[1:]:
                    stats["solrpds_schema_mismatch"]+=1
                    continue
                headers.update(rd.fieldnames)
                for r in rd:
                    writer.writerow({"source_file":url.rsplit("/",1)[-1],**r})
                    total+=1
            except Exception as e:
                stats.setdefault("solrpds_errors",[]).append(f"{url}:{type(e).__name__}:{str(e)[:120]}")
    stats["solrpds_raw_rows"]=total
    stats["solrpds_headers"]=sorted(headers)

def main():
    from collections import defaultdict
    stats=defaultdict(int)
    rows=load_existing()
    try: import_honey(rows,stats)
    except Exception as e: stats["honeybadger_error"]=f"{type(e).__name__}:{e}"
    try: import_rugs(rows,stats)
    except Exception as e: stats["rug_error"]=f"{type(e).__name__}:{e}"
    try: archive_solrpds(stats)
    except Exception as e: stats["solrpds_error"]=f"{type(e).__name__}:{e}"

    vals=sorted(rows.values(),key=lambda r:(r["label"],r["network_id"],r["contract"],r["case_id"]))
    with open(OUT,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(vals)
    report={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "corpus_rows":len(vals),
      "bad":sum(r["label"]=="BAD" for r in vals),
      "good":sum(r["label"]=="GOOD" for r in vals),
      "evm_sources":["HoneyBadger-derived ground truth","validated ETH/BSC rug-pull dataset"],
      "solana_source":"SolRPDS archived as raw evidence; not silently promoted to confirmed GOOD/BAD labels.",
      "stats":dict(stats)
    }
    with open(REPORT,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
