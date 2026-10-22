import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import long_short_signal_policy as policy
import long_short_swing_runtime as runtime
from long_short_swing import HOUR, MINUTE
from research.short_v2.diagnose import context, early_mark


class DiagnosisTests(unittest.TestCase):
    def test_btc_context_is_prefix_invariant_and_a_gap_is_unknown(self):
        rows=[dict(open_ms=i*HOUR,close_ms=(i+1)*HOUR-1,open=100+i,
                   high=102+i,low=99+i,close=101+i) for i in range(170)]
        decision=rows[168]['close_ms']; expected=context(rows[:169],decision)
        rows[-1]['close']=float('nan')
        self.assertEqual(context(rows,decision),expected)
        self.assertEqual(expected['24']['regime'],'UP')
        self.assertEqual(context(rows[:160]+rows[161:],decision)['24']['status'],'UNKNOWN')

    def test_raw_rally_after_exit_never_replaces_realized_short_loss(self):
        a=dict(reference_ms=0,direction='SHORT',entry_price=100.,initial_risk_pct=5.,
               outcome={'exit':dict(time_upper_ms=MINUTE-1,gross_pct=-5.)})
        b=dict(open_ms=4*HOUR-MINUTE,close_ms=4*HOUR-1,open=80.,high=81.,low=79.,close=80.)
        result=early_mark(a,b,4)
        self.assertAlmostEqual(result['raw_direction_pct'],20.)
        self.assertAlmostEqual(result['plan_fee_only_r'],-1.06)
        self.assertEqual(early_mark(a,None,4)['status'],'MISSING')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LS_STATE_DIR':self.tmp.name,'SWING_NOTIFICATIONS':'1',
                        'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'123'})
        self.env.start();self.c=runtime.connect()

    def tearDown(self):
        self.c.close();self.env.stop();self.tmp.cleanup()

    def candidate(self,name):
        version,hash_,direction,_=policy.MODELS[name]
        return dict(version=version,config_hash=hash_,direction=direction,model=name,
                    stage='READY',research_only=True,actionable=False)

    def test_all_failed_shorts_blocked_before_quote_or_sender_with_flag_on(self):
        sender=Mock()
        with patch.object(runtime,'quote',side_effect=AssertionError('must not fetch price')):
            for name in policy.MODELS:
                if name=='BASE_BREAKOUT_LONG':continue
                self.assertEqual(runtime.publish(self.c,None,name,self.candidate(name),0,sender),
                                 'SHORT_MODEL_FAILED_RESEARCH')
        sender.assert_not_called()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],0)

    def test_long_research_permission_is_not_trading_approval(self):
        p=self.candidate('BASE_BREAKOUT_LONG');d=policy.publication_decision(p)
        self.assertTrue(d['research_publish_allowed']);self.assertFalse(d['actionable'])
        self.assertIsNone(d['win_probability'])
        self.assertFalse(policy.publication_decision(dict(p,actionable=True))['research_publish_allowed'])
        self.assertFalse(policy.publication_decision(dict(p,direction='SHORT'))['research_publish_allowed'])
        self.assertFalse(policy.publication_decision(dict(p,config_hash='changed'))['research_publish_allowed'])

    def test_unknown_models_and_unready_candidates_are_not_published(self):
        self.assertFalse(policy.publication_decision({})['research_publish_allowed'])
        p=self.candidate('BASE_BREAKOUT_LONG')
        self.assertFalse(policy.publication_decision(dict(p,stage='REJECTED'))['research_publish_allowed'])

    def test_daemon_cannot_silently_restart_legacy_sender(self):
        import long_short_daemon as daemon
        with patch.dict(os.environ,{'LS_RUNTIME_PROFILE':'','LS_LEAN_RUNTIME':'1',
                        'LS_NATIVE_CHAIN_V2':'1','LS_CHART_FIRST_V1':'1','LS_MAJOR_MOVE_V1':'1'}), \
             patch.object(daemon,'configure_state_paths') as paths:
            with self.assertRaisesRegex(RuntimeError,'NO_LEGACY_FALLBACK'):daemon.main()
            paths.assert_not_called()
        policy.require_explicit_profile({'LS_RUNTIME_PROFILE':'SWING_STRUCTURE_V1'})

    def test_old_manifest_preserved_and_side_losses_are_not_hidden(self):
        self.c.execute('INSERT INTO manifest VALUES(?,?)',(runtime.VERSION,'unchanged'))
        for key,direction,fee in [('l','LONG',10.),('s','SHORT',-5.),('u','SHORT',None)]:
            p=json.dumps(dict(direction=direction));out={} if fee is None else dict(fee_slippage_only_pct=fee,full_net_pct=None)
            progress=dict(checkpoints={'24':dict(fee_slippage_only_pct=1. if fee is None else fee,
                                                 open_plan=fee is None)})
            self.c.execute('INSERT INTO references_v1 VALUES(?,?,?,?,?,?,?,?,?,?)',
                (key,key,'TESTUSDT','RESEARCH',0,'TRACKING' if fee is None else 'DONE',p,json.dumps(progress),json.dumps(out),0))
        self.c.commit();self.c.close();self.c=runtime.connect()
        self.assertEqual(self.c.execute('SELECT payload FROM manifest WHERE id=?',(runtime.VERSION,)).fetchone()[0],'unchanged')
        report=runtime.report(self.c)['cohorts']['RESEARCH']
        self.assertGreater(report['closed_plan_fee_only_conditional_mean_pct'],0)
        self.assertLess(report['by_direction']['SHORT']['closed_plan_fee_only_conditional_mean_pct'],0)
        self.assertEqual(report['by_direction']['SHORT']['unknown_or_open_count'],1)
        self.assertEqual(report['by_direction']['SHORT']['full_net_known_count'],0)
        point=report['by_direction']['SHORT']['checkpoints']['24']
        self.assertEqual(point['available'],2);self.assertEqual(point['open_plans_included'],1)
        self.assertEqual(point['mark_to_horizon_fee_only_mean_pct'],-2.)


if __name__=='__main__':unittest.main()
