"""Resumable, bounded archive job. Public files only; no exchange/account calls."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

from .core import CFG
from .provenance import identity


def atomic_json(path,payload):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def job(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    source=identity();output=root/source['source_sha256'][:16]
    output.mkdir(exist_ok=True)
    state=output/'job_status.json'
    with open(root/'job.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if state.exists() and json.loads(state.read_text()).get('status') in ('COMPLETE','COMPLETE_WITH_GAPS'):
            return 0
        if shutil.disk_usage(root).free<500*1024*1024:
            atomic_json(state,{'status':'FAILED','reason':'LESS_THAN_500_MIB_FREE','provenance':source});return 2
        status=dict(status='RUNNING',version=CFG.version,provenance=source,started_ms=int(time.time()*1000))
        cache=root/'archive_cache'
        commands=[('ARCHIVE',[sys.executable,'-u','-m','ta_paper.archive','--cache',str(cache),'--workers','4']),
                  ('REPLAY',[sys.executable,'-u','-m','ta_paper.replay','--cache',str(cache),'--output',str(output),'--workers','2','--trim-minute-cache'])]
        for phase,cmd in commands:
            status['phase']=phase;atomic_json(state,status)
            print('TA_RESEARCH_PHASE '+json.dumps(status),flush=True)
            result=subprocess.run(cmd,check=False)
            if result.returncode:
                status.update(status='FAILED',returncode=result.returncode,reason=phase+'_PROCESS_FAILED')
                atomic_json(state,status);return 2
        report=json.loads((output/'report.json').read_text())
        m=report['universe_manifest']
        incomplete=bool(report['errors'] or m['errors'] or not report['full_coarse_discovery_complete'])
        status.update(status='COMPLETE_WITH_GAPS' if incomplete else 'COMPLETE',finished_ms=int(time.time()*1000),
            classification=report['classification'],completed_symbols=report['completed_symbols'],
            archive_missing_symbols=len(m['missing']),error_symbols=len(report['errors'])+len(m['errors']))
        atomic_json(state,status)
        for profile,arms in report['results'].items():
            for arm,modes in arms.items():
                if arm=='control_comparisons_all_setups':
                    print('TA_RESEARCH_CONTROLS '+json.dumps(dict(profile=profile,comparisons=modes)),flush=True)
                    continue
                for mode in ('all_setups','portfolio_approximation'):
                    print('TA_RESEARCH_RESULT '+json.dumps(dict(profile=profile,arm=arm,mode=mode,results=modes[mode]),allow_nan=False),flush=True)
        print('TA_RESEARCH_COMPLETE '+json.dumps(status),flush=True)
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True)
    args=parser.parse_args()
    os.nice(15)
    # Hard limit on the research child's address space. The live worker is not
    # inside this limit and does not inherit it. Bounded parallel downloads.
    resource.setrlimit(resource.RLIMIT_AS,(650*1024*1024,650*1024*1024))
    return job(args.root)

if __name__=='__main__':raise SystemExit(main())
