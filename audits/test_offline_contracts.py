"""Offline acceptance tests for audit contracts."""
import unittest
from offline_contracts import gross_pct, net_pct, first_barrier, telegram_receipt

class ContractTests(unittest.TestCase):
    def test_long_short_mirror(self):
        self.assertAlmostEqual(gross_pct("LONG", 100, 105), 5)
        self.assertAlmostEqual(gross_pct("SHORT", 100, 95), 5)
        self.assertAlmostEqual(gross_pct("SHORT", 100, 105), -5)

    def test_costs(self):
        self.assertAlmostEqual(net_pct("LONG", 100, 105, 30), 4.7)

    def test_stop_first(self):
        self.assertEqual(first_barrier("LONG", 110, 90, 95, 105), "STOP")
        self.assertEqual(first_barrier("SHORT", 110, 90, 105, 95), "STOP")

    def test_telegram_receipt(self):
        response={"ok":True,"result":{"chat":{"id":"123"},"message_id":7,"date":123456}}
        self.assertEqual(telegram_receipt("123","123",response),"RECEIPT_OK")
        self.assertEqual(telegram_receipt("123","456",response),"CHAT_MISMATCH")
        self.assertEqual(telegram_receipt("123","123",{"ok":False}),"API_ERROR")

if __name__ == "__main__":
    unittest.main()
