"""Persistent read-only native Binance Futures closed-candle worker."""
from long_short_futures_kline_shadow import continuous_main

if __name__ == "__main__":
    raise SystemExit(continuous_main())
