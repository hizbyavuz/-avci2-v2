"""PR-1 regression checks: persistent provider cache and fail-closed quota."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class CoinalyzeCacheTests(unittest.TestCase):
    def test_cache_reused_without_second_network_call(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"LS_COINALYZE_CACHE_DB": os.path.join(folder, "cache.db")}):
                import long_short_coinalyze_adapter as adapter
                importlib.reload(adapter)
                with patch.object(adapter, "_get", return_value=[{"symbol": "BTCUSDT_PERP.A"}]) as getter:
                    first = adapter._cached_request("/future-markets", ttl=3600)
                    second = adapter._cached_request("/future-markets", ttl=3600)
                self.assertEqual(first, second)
                self.assertEqual(getter.call_count, 1)

    def test_budget_exhaustion_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"LS_COINALYZE_CACHE_DB": os.path.join(folder, "cache.db")}):
                import long_short_coinalyze_adapter as adapter
                importlib.reload(adapter)
                with patch.object(adapter, "_get", return_value=[]):
                    for i in range(adapter._MAX_REQUESTS_PER_MINUTE):
                        adapter._cached_request("/open-interest", {"symbols": str(i)}, ttl=0)
                    with self.assertRaisesRegex(RuntimeError, "COINALYZE_RATE_BUDGET_EXHAUSTED"):
                        adapter._cached_request("/open-interest", {"symbols": "extra"}, ttl=0)


if __name__ == "__main__":
    unittest.main()
