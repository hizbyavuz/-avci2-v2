import copy
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import long_short_chart_first as chart
from long_short_chart_quality import entry_quality, candidate_priority, POLICY
from long_short_chart_first_worker import live_stage, advance, connect
from long_short_chart_dispatch_audit import admission_reason, stage_reason
from long_short_chart_setups import geometry
from long_short_chart_reliability import summarize
from test_chart_first import sample, aligned_flow, NOW
import test_chart_lifecycle as lifecycle


class EntryQualityTests(unittest.TestCase):
    def plan(self, short=False):
        f, price = sample(short)
        return chart.assess('XUSDT', f, price, 50e6, NOW, flow=aligned_flow(short))

    def test_missing_stale_opposite_and_nonfinite_flow_cannot_confirm(self):
        for short in (False, True):
            f, price = sample(short)
            good = aligned_flow(short)
            bad_flows = [None, dict(good, end_ms=good['end_ms']-300000),
                         dict(good, buy_share=1-good['buy_share']),
                         dict(good, price_return_pct=-good['price_return_pct']),
                         dict(good, buy_share=float('nan')), dict(good, buy_share=.5)]
            for bad in bad_flows:
                p = chart.assess('XUSDT', f, price, 50e6, NOW, flow=bad)
                self.assertEqual(p['stage'], 'WATCH')
                self.assertTrue(p['ready_blockers'])
                probe = dict(p, created_ms=NOW)
                self.assertEqual(live_stage(probe, price, f['5m'][-1][4],
                                            f['5m'][-1][6], NOW, f['5m'][-2][4]), 'WATCH')
                self.assertNotIn('GİRİŞ KOŞULU OLUŞTU', chart.message(p))

    def test_deteriorating_price_rechecks_payoff_without_moving_levels(self):
        for short in (False, True):
            p = self.plan(short); sign = -1 if short else 1
            p.update(stage='WATCH', created_ms=NOW)
            # A valid chart can have an insufficient payoff before the chase limit.
            p['obstacle'] = p['price']+sign*.90
            before = copy.deepcopy(p)
            self.assertFalse(entry_quality(p, p['price'], NOW)['blockers'])
            self.assertIn('READY_REWARD_BELOW_RISK',
                          entry_quality(p, p['price']+sign*.10, NOW)['blockers'])
            self.assertEqual(p, before)

    def test_every_price_in_advertised_ready_range_meets_one_net_r(self):
        for short in (False, True):
            p = self.plan(short)
            self.assertEqual(p['stage'], 'READY')
            for fraction in (0, .25, .5, .75, 1):
                price = p['trigger']+fraction*(p['ready_entry_limit']-p['trigger'])
                g = geometry(p['direction'], price, p['stop'], p['obstacle'], .3)
                self.assertGreaterEqual(g['net_reward_risk']+1e-10, 1)

    def test_existing_continuation_uses_current_flow_before_sending(self):
        for short in (False, True):
            for allow in (False, True):
                with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'LS_STATE_DIR':root}), patch('time.time', return_value=NOW/1000):
                    c=connect();k,p=lifecycle.LifecycleTests().db(short)
                    p.update(stage='WATCH')
                    original=(p['trigger'],p['stop'],p['obstacle'])
                    c.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?)',
                              ('e','XUSDT',p['direction'],p['created_ms'],'WATCH','WATCH',None,json.dumps(p)))
                    c.commit();decision=copy.deepcopy(p)
                    if not allow:decision['hourly_flow']=aligned_flow(not short)
                    publisher=Mock(return_value={'status':'ACK'})
                    advance(c,k,{'XUSDT':decision},NOW,publisher,'token','123')
                    saved=json.loads(c.execute('SELECT payload FROM episodes').fetchone()[0])
                    self.assertEqual(saved['stage'],'READY' if allow else 'WATCH')
                    self.assertEqual(publisher.call_count,int(allow))
                    self.assertEqual((saved['trigger'],saved['stop'],saved['obstacle']),original)
                    if not allow:self.assertTrue(saved['ready_blockers'])
                    c.close();k.close()

    def test_already_delivered_ready_is_not_revoked_by_later_flow(self):
        p=self.plan();p.update(stage='READY',created_ms=NOW,hourly_flow=aligned_flow(True))
        self.assertEqual(live_stage(p,p['price'],p['price'],NOW-8000,NOW),'READY')
        self.assertEqual(live_stage(p,p['stop']-.01,p['price'],NOW-8000,NOW),'BROKEN')

    def test_quality_beats_proximity_and_cap_never_hides_existing_episode(self):
        good=self.plan();good.update(stage='WATCH',symbol='GOOD')
        weak=copy.deepcopy(good);weak.update(symbol='WEAK',price=weak['trigger'])
        weak['entry_quality']['blockers']=['READY_REWARD_BELOW_RISK']
        self.assertEqual(sorted([weak,good],key=candidate_priority)[0]['symbol'],'GOOD')
        recent=dict(ended_ms=None,created_ms=NOW,payload=json.dumps(good))
        self.assertEqual(admission_reason(recent,good,NOW,3,chart.CONFIG),'EXISTING_ACTIVE_EPISODE')
        self.assertEqual(admission_reason(None,good,NOW,3,chart.CONFIG),'CYCLE_NOTICE_CAP')

    def test_blocker_reason_and_sides_are_not_lost_in_reporting(self):
        p=self.plan();p.update(stage='WATCH',created_ms=NOW,hourly_flow=aligned_flow(True))
        self.assertEqual(stage_reason(p,'WATCH',p['price'],p['price'],p['trigger']-.01,
                                      NOW-8000,NOW,chart.CONFIG),'READY_FLOW_NOT_ALIGNED')
        rows=[]
        for side,net in [('LONG',1),('SHORT',-2)]:
            rows.append(dict(payload=json.dumps(dict(strategy_id='v42',episode_id=side)),horizon=180,
                stage='DELIVERED_READY',symbol=side,event_key=side,observed_ms=60000,
                direction=side,reason='TELEGRAM_ACK',state='RESOLVED',
                outcome_payload=json.dumps(dict(model_net_return_pct=net))))
        groups={g['side']:g for g in summarize(rows,NOW)['groups']}
        self.assertEqual(groups['LONG']['reference_net']['mean_pct'],1)
        self.assertEqual(groups['SHORT']['reference_net']['mean_pct'],-2)


if __name__ == '__main__':
    unittest.main()
