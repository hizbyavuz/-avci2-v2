import copy
import json
import sqlite3
import unittest
import tempfile
from unittest.mock import patch
from test_native_chain_v2 import fixture,NOW,SYMBOL
from long_short_native_derivatives import validate
from long_short_gate_experiment import variants,entry_probe,ingest,emit_report

class PartialNativeTests(unittest.TestCase):
    def test_each_bad_component_preserves_other_verified_fields(self):
        for field,key in [('long_short','ls_fresh'),('oi_history','oi_fresh'),('mark','funding_fresh')]:
            p=fixture();p[field]=[] if field!='mark' else {}
            e=validate(p,SYMBOL,NOW,allow_partial=True)
            self.assertFalse(e[key]);self.assertEqual(sum(e[k] for k in ('oi_fresh','ls_fresh','funding_fresh')),2)
            self.assertTrue(e['market_verified'])
            with self.assertRaises(ValueError):validate(p,SYMBOL,NOW)
    def test_identity_and_snapshot_age_never_become_partial_permission(self):
        for changes in ({'source':'WRONG'},{'symbol':'OTHER'},{'observed_ms':NOW-180001}):
            with self.assertRaises(ValueError):validate(dict(fixture(),**changes),SYMBOL,NOW,allow_partial=True)
    def test_partial_three_fields_is_recognized_without_neutral_imputation(self):
        from long_short_data_opportunity_v2 import partial_fields
        p=fixture();p['long_short']=[]
        e=validate(p,SYMBOL,NOW,allow_partial=True)
        e.update(taker_fresh=True,taker_ratio=1.2,taker_coverage_ready=True,taker_source='BINANCE_UM_FUTURES_KLINE')
        self.assertEqual(partial_fields(e),['long_short_ratio']);self.assertIsNone(e['long_short_ratio'])

class ExperimentTests(unittest.TestCase):
    def setup(self,side='LONG'):
        p=dict(direction=side,trigger_level=100,invalidation=96 if side=='LONG' else 104,
            target1=103 if side=='LONG' else 97,retest_low=99.8 if side=='LONG' else 100,retest_high=100 if side=='LONG' else 100.2)
        g=dict(qualified_precheck=False,level_ok=True,structural_plan_v3=dict(reason='INSUFFICIENT_GEOMETRY',cost_pct=.3,min_net_r=1,required_room_pct=.9))
        d=dict(direction=side,eligible=True,decision_reasons=[],hard_blockers=[])
        e=validate(fixture(),SYMBOL,NOW);e.update(ready=True)
        return p,g,d,e,dict(low=[99]*8,high=[101]*8)
    def test_only_stop_changes_and_both_directions_work(self):
        for side in ('LONG','SHORT'):
            p,g,d,e,k=self.setup(side);before=copy.deepcopy((p,g,d,e,k))
            arms={a['name']:a for a in variants(p,g,d,e,k,1)['arms']}
            self.assertEqual(before,(p,g,d,e,k));self.assertFalse(arms['BASELINE']['admitted'])
            self.assertTrue(arms['LOCAL_STOP_ONLY']['admitted'])
            self.assertTrue(arms['NET_R_ONLY_REMOVED']['admitted'])
            self.assertFalse(arms['ROOM_FLOOR_ONLY_REMOVED']['admitted'])
            q=arms['LOCAL_STOP_ONLY']['plan'];self.assertEqual(q['target1'],p['target1']);self.assertEqual(q['trigger_level'],100)
    def test_one_missing_is_not_blanket_bypass_of_other_gates(self):
        p,g,d,e,k=self.setup();p['invalidation']=99
        e.update(ready=False,ls_fresh=False,long_short_ratio=None,taker_ratio=1.2,taker_fresh=True,
            taker_coverage_ready=True,taker_source='BINANCE_UM_FUTURES_KLINE')
        d.update(eligible=False,hard_blockers=['derivatives_incomplete'],decision_reasons=['derivatives_incomplete'])
        arms={a['name']:a for a in variants(p,g,d,e,k,1)['arms']}
        self.assertFalse(arms['BASELINE']['admitted']);self.assertTrue(arms['ONE_MISSING_ONLY']['admitted'])
        d['hard_blockers'].append('liquidity_floor')
        self.assertFalse(any(a['admitted'] for a in variants(p,g,d,e,k,1)['arms']))
    def test_bad_levels_and_unknown_direction_never_admitted(self):
        p,g,d,e,k=self.setup();p['target1']=0
        self.assertFalse(any(a['admitted'] for a in variants(p,g,d,e,k,1)['arms']))
        p,g,d,e,k=self.setup();d['direction']='NONE'
        self.assertFalse(any(a['admitted'] for a in variants(p,g,d,e,k,1)['arms']))
    def bars(self):
        return [[t,100,101,99.9,100.5,1,t+59999] for t in range(300000,1800000,60000)]
    def test_confirmation_enters_after_closed_bar_not_inside_it(self):
        p,_,_,_,_=self.setup();bars=self.bars();observed=299999
        self.assertEqual(entry_probe(p,observed,bars,600000,'CLOSE_5M'),(600000,None))
        self.assertEqual(entry_probe(p,observed,bars,660000,'RETEST_CLOSE'),(660000,None))
        self.assertEqual(entry_probe(p,observed,bars,599999,'CLOSE_5M'),(None,'PENDING'))
    def test_confirmation_rejects_gaps_and_prior_stop(self):
        p,_,_,_,_=self.setup();bars=self.bars()
        self.assertEqual(entry_probe(p,299999,bars[1:],900000,'CLOSE_5M')[1],'DATA_UNRESOLVED')
        bars[0][3]=95
        self.assertEqual(entry_probe(p,299999,bars,900000,'CLOSE_5M')[1],'INVALIDATED_BEFORE_ENTRY')
    def test_pair_sampling_freezes_all_arms_once_and_reports_blocked_not_losses(self):
        p,g,d,e,k=self.setup();exp=variants(p,g,d,e,k,1)
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,symbol,observed_ms,cohort,direction,price,payload,version)')
        payload=dict(gate_experiment=exp,strategy_identity={'id':'v1'},setup_plan=p)
        for i in (1,2):c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)',(i,'X',NOW+i*60000,'FULL_4_OF_4','LONG',100,json.dumps(payload),'v'))
        ingest(c,NOW+3*60000);self.assertEqual(c.execute('SELECT COUNT(*) FROM gate_experiment_episodes').fetchone()[0],1)
        with patch('builtins.print') as output:
            emit_report(c,NOW+300000)
        rows=[json.loads(call.args[0].split(' ',1)[1]) for call in output.call_args_list]
        base=next(r for r in rows if r['arm']=='BASELINE')
        self.assertEqual(base['admitted'],0);self.assertEqual(base['outcomes']['NEXT_MINUTE']['net_losses'],0)
        c.close()

    def test_persist_resolve_and_report_use_frozen_plans_and_real_path_states(self):
        import long_short_gate_experiment as expmod
        from long_short_data_opportunity_v2 import observe,connect
        from long_short_native_derivatives import SOURCE
        p,g,d,e,k=self.setup();exp=variants(p,g,d,e,k,1)
        observed=NOW;start=(observed//60000+1)*60000
        def path(symbol,begin,count):
            return [[t,100.5,100.7,100.2,100.5,1,t+59999] for t in range(begin,begin+count*60000,60000)]
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'LS_STATE_DIR':root}), \
             patch('long_short_data_opportunity_v2.current_price',return_value=100), \
             patch('long_short_live_pool.native_futures_path_bars',side_effect=path), \
             patch('long_short_native_derivatives.relay_get',return_value={'source':SOURCE,'symbol':'X','rates':[]}), \
             patch('builtins.print'):
            observe('X','FULL_4_OF_4','LONG',dict(gate_experiment=exp,strategy_identity={'id':'v1'},setup_plan=p),observed)
            expmod.resolve(start+361*60000)
            c=connect()
            states=dict(c.execute('SELECT state,COUNT(*) FROM gate_experiment_results GROUP BY state'))
            self.assertEqual(states,{'NO_CONFIRMATION':3,'RESOLVED':6})
            frozen=json.loads(c.execute('SELECT payload FROM gate_experiment_episodes').fetchone()[0])
            self.assertEqual(frozen,exp)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM gate_experiment_results WHERE arm='BASELINE'").fetchone()[0],0)
            c.close()

if __name__=='__main__':unittest.main()
