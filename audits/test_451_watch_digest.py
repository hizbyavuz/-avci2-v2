import unittest
from types import SimpleNamespace
from long_short_451_watch_digest import format_watch_digest


def item(direction="LONG", eligible=False, observed=True, price=100):
    return SimpleNamespace(symbol="ETHUSDT", price=price, payload={
        "direction_engine": {
            "direction": direction,
            "eligible": eligible,
            "observation_only": observed,
        }
    })


class WatchDigestTests(unittest.TestCase):
    def test_observation_label(self):
        text = format_watch_digest([item()])
        self.assertIn("İZLEME", text)
        self.assertIn("İşlem teyidi DEĞİL", text)
        self.assertIn("ETHUSDT", text)

    def test_trade_eligible_never_relabelled(self):
        self.assertIsNone(format_watch_digest([item(eligible=True)]))

    def test_unobserved_not_reported(self):
        self.assertIsNone(format_watch_digest([item(observed=False)]))

    def test_invalid_price_rejected(self):
        self.assertIsNone(format_watch_digest([item(price=0)]))

    def test_directionless_rejected(self):
        self.assertIsNone(format_watch_digest([item(direction="NONE")]))


if __name__ == "__main__":
    unittest.main()
