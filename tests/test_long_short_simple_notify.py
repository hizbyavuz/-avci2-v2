import unittest

from long_short_simple_notify import format_alert


class TestLongShortSimpleNotify(unittest.TestCase):
    def test_long_user_facing_format(self):
        msg = format_alert("ETHUSDT", "LONG", 2.718, "STABLE", 0.0, 2.714)
        self.assertEqual(
            msg,
            "🟢 LONG İÇİN İZLE | ETHUSDT\n"
            "5 dk mum 2.718 üstünde kapanırsa LONG güçlenir.\n"
            "Şu an fiyat: 2.714\n"
            "Beklenen: 2.718 üstü kapanış → ardından seviyeyi koruması.\n"
            "Tip: 🟦 Güçlü/Stabil",
        )

    def test_short_user_facing_format(self):
        msg = format_alert("XRPUSDT", "SHORT", 1.5153, "STABLE", 0.0, 1.517)
        self.assertEqual(
            msg,
            "🔴 SHORT İÇİN İZLE | XRPUSDT\n"
            "5 dk mum 1.5153 altında kapanırsa SHORT güçlenir.\n"
            "Şu an fiyat: 1.517\n"
            "Beklenen: 1.5153 altı kapanış → ardından seviyenin altında kalması.\n"
            "Tip: 🟦 Güçlü/Stabil",
        )

    def test_fast_type_is_kept(self):
        msg = format_alert("SOMEUSDT", "LONG", 10.0, "FAST_FRESH", 7.2, 9.95)
        self.assertIn("Tip: ⚡ Hızlı/Taze", msg)


if __name__ == "__main__":
    unittest.main()
