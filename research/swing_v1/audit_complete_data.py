"""Repeat the frozen minute audit after a documented archive download.

Write separate files; keep the earlier convenience-cache evidence unchanged.
No thresholds, fills, exits, costs or funding assumptions are changed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import minute_audit as audit
from long_short_swing import CONFIG_HASH
from long_short_swing_outcomes import VERSION as OUTCOME_VERSION


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--screen',type=Path,required=True)
    p.add_argument('--download',type=Path,required=True)
    p.add_argument('--previous-audit',type=Path)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
    screen=json.loads(args.screen.read_text());download=json.loads(args.download.read_text())
    if screen['config_hash']!=CONFIG_HASH:raise ValueError('CONFIG_MISMATCH')
    if download['aborted']:raise ValueError('DOWNLOAD_ABORTED')
    expected={(r['symbol'],r['month']):r['sha256'] for r in download['archives'] if r['status']=='OK'}
    records,archives,requests=audit.run(screen['records'],args.cache,expected)
    prior_count=0
    if args.previous_audit:
        previous=json.loads(args.previous_audit.read_text())
        key=lambda r:(r['symbol'],r['direction'],r['confirmation_ms'])
        current={key(r):r for r in records}
        for old in previous['records']:
            if old['status']=='MEASURED' and old['outcome']['status']=='COMPLETE' or old['status']=='ENTRY_INVALID':
                if old!=current[key(old)]:raise ValueError('PREVIOUS_COMPLETE_RESULT_CHANGED:'+str(key(old)))
                prior_count+=1
    statuses=dict(Counter(a['status'] for a in archives))
    all_available=all(a['status']=='OK' for a in archives)
    manifest=dict(version='MINUTE_FULL_MANIFEST_REPLAY_V1',created_at=datetime.now(timezone.utc).isoformat(),
        config_hash=CONFIG_HASH,outcome_version=OUTCOME_VERSION,
        classification='ALL_FROZEN_CANDIDATES_REPLAYED' if all_available else 'PARTIAL_ARCHIVE_REPLAY',
        universe='UNCLASSIFIED_USDT_ARCHIVE_V1',crypto_only=False,
        funding_status='UNKNOWN_API_HTTP_451',network_used_during_replay=False,
        screen_sha256=audit.sha(args.screen),download_evidence_sha256=audit.sha(args.download),
        runner_sha256=audit.sha(Path(__file__)),audit_code_sha256=audit.sha(Path(audit.__file__)),
        outcome_code_sha256=audit.sha(Path(__file__).resolve().parents[2]/'long_short_swing_outcomes.py'),
        total_candidates=len(records),archive_status_counts=statuses,
        previously_measured_complete_or_invalid_results_unchanged=prior_count,
        interpretation='Hypothetical per-signal plans; not a portfolio, delivery-budget simulation or proof of profitability.')
    report=dict(manifest=manifest,summary=audit.summarize(records),records=records,archives=archives)
    body=json.dumps(report,ensure_ascii=False,indent=2).encode()
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_bytes(body)
    args.out.with_suffix('.json.gz').write_bytes(gzip.compress(body,mtime=0))
    args.out.with_name(args.out.stem+'-requests.json').write_text(json.dumps(dict(manifest=manifest,symbol_requests=requests),ensure_ascii=False,indent=2))
    print(json.dumps(dict(manifest=manifest,summary=report['summary']),ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
