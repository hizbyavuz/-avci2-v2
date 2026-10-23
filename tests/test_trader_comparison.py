import copy
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from research.trader_v2.compare import ContextArchive,conditional_summary


class ComparisonTests(unittest.TestCase):
    def test_missing_invalid_and_unknown_never_disappear_from_denominator(self):
        decision=dict(variants={v:dict(pass_candidate=True,reasons=[]) for v in ('BASE','STRUCTURE','PARTICIPATION','CONTEXT')})
        base=dict(confirmation_ms=1786150799999,context_complete=True,decision=decision,raw=dict(status='IMMATURE_OR_GAP'),plan_outcome={})
        invalid=copy.deepcopy(base);invalid['plan_outcome']=dict(status='ENTRY_INVALID')
        missing=copy.deepcopy(base);missing['context_complete']=False
        for v in ('STRUCTURE','PARTICIPATION','CONTEXT'):
            missing['decision']['variants'][v]=dict(pass_candidate=False,reasons=['MISSING_DATA'])
        report=conditional_summary([base,invalid,missing])
        self.assertEqual(report['total_candidates'],3)
        self.assertEqual(report['missing_context'],1)
        self.assertEqual(report['variants']['BASE']['selected'],3)
        self.assertEqual(report['variants']['BASE']['invalid_entry'],1)
        self.assertEqual(report['variants']['BASE']['unmeasured_or_unknown'],2)
        self.assertIsNone(report['variants']['CONTEXT']['fee_only_positive_pct'])

    def test_csv_identity_and_checksum_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'TESTUSDT-metrics-2026-08-15.zip';body=io.BytesIO()
            with zipfile.ZipFile(body,'w') as z:
                z.writestr('data.csv','create_time,symbol,sum_open_interest,count_long_short_ratio\n2026-08-15 00:00:00,TESTUSDT,100,1.2\n')
            path.write_bytes(body.getvalue())
            record=dict(symbol='TESTUSDT',interval='metrics',month='2026-08-15',status='OK',sha256=hashlib.sha256(body.getvalue()).hexdigest())
            reader=ContextArchive(Path(tmp),[record]);rows=reader.read('TESTUSDT','metrics','2026-08-15')
            self.assertEqual(rows[0]['available_ms']-rows[0]['time_ms'],300000)
            reader.read.cache_clear();path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'HASH_CHANGED'):reader.read('TESTUSDT','metrics','2026-08-15')


if __name__=='__main__':unittest.main()
