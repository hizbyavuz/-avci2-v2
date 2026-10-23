import unittest
from structure_engine.presentation import card

class CardTests(unittest.TestCase):
    def plan(self,eligible=True):
        return dict(plan_eligible=eligible,payoff={'valid':eligible,'reason':'OK' if eligible else 'LOW_NET_REWARD_RISK'},stage='CONDITION_MET',direction='SHORT',condition='RETEST_HELD',interval='15m',entry_reference=100.,trigger=101.,stop=103.,target=94.,reference_price=100.)
    def test_eligible_card_keeps_exact_short_prices(self):
        c=card(self.plan())
        self.assertEqual((c['entry'],c['stop'],c['target']),(100.,103.,94.))
        self.assertEqual(c['stop_label'],'SL · STOP')
        self.assertEqual(c['target_label'],'TP · İLK HEDEF')
    def test_ineligible_card_never_invents_entry_or_confirmed_target(self):
        c=card(self.plan(False))
        self.assertIsNone(c['entry'])
        self.assertEqual(c['status'],'İŞLEM UYGUN DEĞİL')
        self.assertEqual(c['target_label'],'İLK ENGEL · TP ONAYLI DEĞİL')
    def test_watch_does_not_turn_valid_geometry_into_entry(self):
        p=self.plan(False);p.update(stage='WATCH',condition='APPROACHING_ZONE',payoff={'valid':True,'reason':'OK'})
        c=card(p)
        self.assertIsNone(c['entry']);self.assertEqual(c['status'],'BEKLE · GİRİŞ TEYİDİ YOK')
