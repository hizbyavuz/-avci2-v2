#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prospective genesis holdout integrity guard.

The baseline commit in genesis_freeze_manifest.json is the canonical byte-level
reference for contamination-sensitive files. This tool never rewrites research
state and never changes scanner behavior.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone

MANIFEST=os.getenv("GENESIS_FREEZE_MANIFEST","genesis_freeze_manifest.json")

def now():
    return datetime.now(timezone.utc)

def dt(s):
    return datetime.fromisoformat(str(s).replace("Z","+00:00"))

def run(*args):
    return subprocess.run(args,check=False,text=True,capture_output=True)

def git_bytes(ref,path):
    p=run("git","show",f"{ref}:{path}")
    if p.returncode!=0:
        return None,p.stderr.strip()
    return p.stdout.encode("utf-8"),None

def worktree_bytes(path):
    try:
        with open(path,"rb") as f:return f.read(),None
    except OSError as e:
        return None,str(e)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--strict",action="store_true",
                    help="after holdout start, exit nonzero on contamination")
    ap.add_argument("--json-out",default="genesis_freeze_status.json")
    args=ap.parse_args()
    with open(MANIFEST,encoding="utf-8") as f:
        m=json.load(f)
    baseline=m["baseline_git_commit"]
    holdout=dt(m["genesis_holdout_start_utc"])
    changes=[]
    errors=[]
    for path in m["contamination_sensitive_files"]:
        base,be=git_bytes(baseline,path)
        cur,ce=worktree_bytes(path)
        if be or ce:
            errors.append({"path":path,"baseline_error":be,"current_error":ce})
            continue
        if base!=cur:
            changes.append(path)

    ts=now()
    phase="PRE_HOLDOUT" if ts<holdout else "HOLDOUT"
    contaminated=bool(changes) and phase=="HOLDOUT"
    status={
      "version":m["version"],
      "checked_at_utc":ts.isoformat(),
      "baseline_git_commit":baseline,
      "holdout_start_utc":m["genesis_holdout_start_utc"],
      "phase":phase,
      "changed_sensitive_files":changes,
      "comparison_errors":errors,
      "contaminated":contaminated,
      "status":(
        "CONTAMINATED_NEW_VERSION_REQUIRED" if contaminated else
        "PRE_HOLDOUT_BASELINE_DRIFT_RESEAL_OR_REVERT_BEFORE_START" if changes else
        "CLEAN"
      )
    }
    with open(args.json_out,"w",encoding="utf-8") as f:
        json.dump(status,f,ensure_ascii=False,indent=2)
    print(json.dumps(status,ensure_ascii=False,indent=2))
    if errors:
        return 2
    if args.strict and contaminated:
        return 3
    return 0

if __name__=="__main__":
    sys.exit(main())
