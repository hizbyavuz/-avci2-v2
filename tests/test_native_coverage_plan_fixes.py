import unittest
from unittest.mock import patch
from types import SimpleNamespace
import long_short_analyst as a


class NativeCoveragePlanTests(unittest.TestCase):
    def test_complete_cycle_and_no_duplicate_window_symbols(self):
        rows=[(f'COIN{i:03d}USDT',10000000+i,1,0) for i in range(190)]
        seen=set()
        for slot in range(7):
            window=a.native_rotating_window(rows,30,slot)
            self.assertEqual(len({x[0] for x in window}),30)
            seen.update(x[0] for x in window)
        self.assertEqual(seen,{x[0] for x in rows})

    def test_volume_rank_churn_does_not_change_window(self):
        rows=[(f'COIN{i:03d}USDT',i,1,0) for i in range(61)]
        self.assertEqual(a.native_rotating_window(rows,30,2),
                         a.native_rotating_window(list(reversed(rows)),30,2))

    def test_small_universe_not_repeated(self):
        rows=[('AUSDT',1,1,0),('BUSDT',1,1,0)]
        self.assertEqual(a.native_rotating_window(rows,30,0),rows)

    def test_repeated_pivot_is_not_target_and_missing_target_rejected(self):
        k={'high':[11]*48,'low':[9]*48,'close':[10]*48}
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}):
            levels=a.swing_levels(k)
        self.assertEqual(levels['resistance'],11)
        self.assertEqual(levels['support'],9)
        self.assertEqual(levels['resistance2'],0)
        self.assertEqual(levels['support2'],0)

    def test_duplicate_touches_use_next_distinct_observed_pivot(self):
        k={'high':[10,10,11,10,10,11,10,10,12,10,10,10],
           'low':[8]*12,'close':[10]*12}
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}):
            levels=a.swing_levels(k)
        self.assertEqual(levels['resistance'],11)
        self.assertEqual(levels['resistance2'],12)

    def test_invalid_wait_is_not_presented_as_telegram_promise(self):
        payload={'setup_plan':{'direction':'LONG','trigger_level':11,
                 'invalidation':9,'target1':11,'target2':0},
                 'structure_gate':{'level_ok':True,'room_ok':False,'rr_ok':False}}
        item=SimpleNamespace(status='WAIT',symbol='TESTUSDT',payload=payload,
                confidence=50,long_score=50,short_score=0,price=10,reasons=[],risks=[])
        with patch.dict('os.environ',{'LS_NATIVE_CHAIN_V2':'1'}):
            msg=a.build_message('', 'RANGE',[item],0)
        self.assertIn('PLAN ELENDİ',msg)
        self.assertNotIn('Bu şartlar oluşursa',msg)
        self.assertNotIn('canlı havuz giriş şartını bekliyor',msg)


if __name__=='__main__':
    unittest.main()
