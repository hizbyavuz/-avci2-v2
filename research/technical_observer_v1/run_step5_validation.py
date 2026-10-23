"""Validate integration, while preserving the separate MAGMA unverified result."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from research.technical_observer_v1.run_step4_validation import MODULES, Result
from technical_observer_v1.config import DEFAULTS
from technical_observer_v1.shadow_config import OPS, OPS_HASH


def run(output):
    root=Path(__file__).resolve().parents[2]
    names=MODULES+('tests.test_technical_observer_shadow',)
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in names)
    result=unittest.TextTestRunner(verbosity=1,resultclass=Result).run(suite)
    changed=subprocess.check_output(['git','diff','--name-only',
             'c39383597fc8ac68849fd80ee7a0a932e053d3d7','--','structure_engine',
             'structure_live_start.py','long_short_*.py','Dockerfile.structure','requirements.txt'],cwd=root,text=True).splitlines()
    paths=sorted((root/'technical_observer_v1').glob('*.py'))
    paths += [root/'structure_shadow_start.py',Path(__file__).resolve()]
    paths += [root/(name.replace('.','/')+'.py') for name in names]
    success=result.wasSuccessful() and not changed
    payload=dict(status='ENGINEERING_PASSED_REAL_MAGMA_UNVERIFIED' if success else 'FAILED',
       tested_base_commit='bebb21d3b4c7191d58ebdd1d6da5aca3ed24ac77',
       config_hash=DEFAULTS.fingerprint(),ops=OPS,ops_hash=OPS_HASH,
       tests_run=result.testsRun,passed=len(result.passed_ids),failed=len(result.failures),errors=len(result.errors),
       skipped=[dict(test=t.id(),reason=reason) for t,reason in result.skipped],
       passed_tests=result.passed_ids,production_files_changed=changed,
       failures=[dict(test=t.id(),traceback=trace) for t,trace in result.failures+result.errors],
       source_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
       local_test_market_samples=0,profitability_verified=False,live_status='NOT_ASSERTED_BY_OFFLINE_TESTS')
    Path(output).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status=payload['status'],tests_run=result.testsRun,passed=len(result.passed_ids),
                         skipped=len(result.skipped),production_files_changed=changed)))
    return 0 if success else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    raise SystemExit(run(p.parse_args().output))
