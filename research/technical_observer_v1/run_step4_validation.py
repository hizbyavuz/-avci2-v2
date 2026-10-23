"""Offline validation with an explicit INCOMPLETE result for missing real cases.

Exit 0: all requested tests actually passed; 1: a failure/error; 2: skipped tests.
No market fetch, Telegram, production DB access, threshold tuning or deployment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import unittest

from technical_observer_v1 import DEFAULTS

MODULES = ('tests.test_technical_observer_definitions',
           'tests.test_technical_observer_presentation',
           'tests.test_technical_observer_regression')


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


def run(output):
    root = Path(__file__).resolve().parents[2]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in MODULES)
    result = unittest.TextTestRunner(verbosity=1, resultclass=Result).run(suite)
    status = 'FAILED' if not result.wasSuccessful() else 'INCOMPLETE_REAL_CASE_UNVERIFIED' if result.skipped else 'PASSED'
    source_paths = [root/(name.replace('.','/')+'.py') for name in MODULES]
    source_paths += sorted((root/'technical_observer_v1').glob('*.py'))
    source_paths += [Path(__file__).resolve(),root/'structure_engine/config.py']
    payload = dict(status=status, tested_base_commit='dfa6e75b1de4a9d8acdcba9242dfdbcb630abfcf',
        config_hash=DEFAULTS.fingerprint(), tests_run=result.testsRun, passed=len(result.passed_ids),
        failed=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        passed_tests=result.passed_ids,
        skipped_tests=[dict(test=t.id(),reason=reason) for t,reason in result.skipped],
        failures=[dict(test=t.id(),traceback=detail) for t,detail in result.failures+result.errors],
        source_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        synthetic_prefix_states=4320, synthetic_mirror_frame_sets=30,
        synthetic_tick_mirror_frame_sets=20, real_archive_records_replayed=2,
        independent_market_samples_collected=0, performance_claim=False,
        production_modified=False, frozen_thresholds_modified=False,
        step5_started=False)
    destination = Path(output)
    # New validation evidence only. This does not address production state paths.
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:payload[k] for k in ('status','tests_run','passed','failed','errors','skipped')},ensure_ascii=False))
    return 1 if status=='FAILED' else 2 if result.skipped else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    raise SystemExit(run(parser.parse_args().output))
