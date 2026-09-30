#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-holdout integrity inventory. Read-only to research state.

Compares the sealed baseline commit with the current worktree for every
contamination-sensitive file and emits an explicit review list. It does not
automatically bless semantic changes or reseal the genesis baseline.
"""
from __future__ import annotations
import json, os, subprocess
from datetime import datetime, timezone

MANIFEST=os.getenv("GENESIS_FREEZE_MANIFEST","genesis_freeze_manifest.json")
OUT=os.getenv("PREHOLDOUT_AUDIT_OUT","preholdout_integrity_status.json")

def run(*args):
    return subprocess.run(args,check=False,text=True,capture_output=True)

def main():
    m=json.load(open(MANIFEST,encoding="utf-8"))
    base=m["baseline_git_commit"]
    rows=[]
    for path in m["contamination_sensitive_files"]:
        p=run("git","diff","--numstat",base,"--",path)
        changed=bool(p.stdout.strip())
        status="UNCHANGED"
        add=dele=0
        if changed:
            status="REVIEW_REQUIRED"
            parts=p.stdout.strip().split()
            if len(parts)>=2:
                try:add=int(parts[0])
                except:pass
                try:dele=int(parts[1])
                except:pass
        rows.append({"path":path,"status":status,"additions":add,"deletions":dele})
    changed=[r for r in rows if r["status"]!="UNCHANGED"]
    out={
      "checked_at_utc":datetime.now(timezone.utc).isoformat(),
      "baseline_git_commit":base,
      "holdout_start_utc":m["genesis_holdout_start_utc"],
      "changed_sensitive_count":len(changed),
      "status":"BLOCK_HOLDOUT_RESEAL_UNTIL_REVIEWED" if changed else "CLEAN",
      "rule":"Do not automatically reseal. Each changed sensitive file must be classified as semantic or non-semantic before the holdout baseline is finalized.",
      "files":rows,
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
