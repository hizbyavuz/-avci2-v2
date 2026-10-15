import json
import unittest
from unittest.mock import patch
import long_short_chart_first as chart
from long_short_chart_first_worker import live_stage
from long_short_chart_reliability import summarize
from test_chart_first import sample, NOW


class RewardRiskSwitchTests(unittest.TestCase):
    def test_sub_one_ratio_passes_scan_and_final_gate_both_sides(self):
        for short in (False,True):
            frames,price=sample(short);sign=-1 if short else 1
            with patch.object(chart,'first_obstacle',return_value=(price+sign*.7,'15m')):
                with patch.dict(chart.CONFIG,risk_reward_filter_enabled=True):
                    before=chart.assess('X',frames,price,50e6,NOW)
                after=chart.assess('X',frames,price,50e6,NOW)
            self.assertEqual(before['reason'],'NET_REWARD_BELOW_STRUCTURAL_RISK')
            self.assertEqual(after['stage'],'READY')
            self.assertFalse(after['risk_reward_filter_enabled'])
            self.assertGreater(after['geometry']['net_reward_risk'],0)
            self.assertLess(after['geometry']['net_reward_risk'],1)
            probe=dict(after,stage='WATCH',created_ms=NOW)
            last,previous=frames['5m'][-1],frames['5m'][-2]
            self.assertEqual(live_stage(probe,price,last[4],last[6],NOW,previous[4]),'READY')
            self.assertEqual(live_stage(dict(probe,risk_reward_filter_enabled=True),
                price,last[4],last[6],NOW,previous[4]),'EXPIRED')

    def test_no_net_room_still_blocks_with_switch_off(self):
        f,p=sample()
        with patch.object(chart,'first_obstacle',return_value=(p+.1,'15m')):
            result=chart.assess('X',f,p,50e6,NOW)
        self.assertEqual(result['stage'],'REJECTED')
        self.assertEqual(result['reason'],'OBSTACLE_WITHIN_COST_ALLOWANCE')

    def test_comparison_keeps_frozen_v3_filter_on(self):
        f,p=sample()
        with patch.object(chart,'first_obstacle',return_value=(p+.7,'15m')):
            result=chart.assess('X',f,p,50e6,NOW,comparison_v3=True)
        self.assertTrue(result['risk_reward_filter_enabled'])
        self.assertEqual(result['reason'],'NET_REWARD_BELOW_STRUCTURAL_RISK')

    def test_report_applies_recorded_policy_not_current_config(self):
        rows=[]
        for sid,enabled in (('old',True),('new',False)):
            p=dict(strategy_id=sid,version=sid,episode_id=sid,model='M',
                risk_reward_filter_enabled=enabled,min_net_reward_risk=1,
                geometry={'valid':True,'net_reward_risk':.5})
            rows.append(dict(payload=json.dumps(p),horizon=15,stage='DELIVERED_READY',
                event_key=sid,symbol='X',observed_ms=60000,direction='LONG',reason='OK',
                state='RESOLVED',outcome_payload=json.dumps({'model_net_return_pct':.2})))
        groups={g['strategy_id']:g for g in summarize(rows,NOW)['groups']}
        self.assertEqual(groups['old']['invalid_entry_geometry'],1)
        self.assertEqual(groups['new']['invalid_entry_geometry'],0)
