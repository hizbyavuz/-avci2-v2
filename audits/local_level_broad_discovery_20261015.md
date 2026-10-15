# V7 local level identity and broad native discovery

User authorized repair after repeated late SHORT notices and silence, 2026-10-15.

Two bounded changes:

1. Local entry uses only the fixed-anchor 5m/15m region. Higher-timeframe observations with their center inside that local region are classified as observations of the same level, retained explicitly as context rather than widening entry or counting as a second obstacle. A distinct higher-frame center remains an obstacle, including when its band overlaps entry. This is a versioned modeling choice, not a proven profitability improvement. V6 is preserved and compared on identical zones and local stop on every live analysis.
2. Under verified Binance-native-only mode, cheap prefilter reads the entire eligible universe from existing local closed candles each scan instead of a rotating 30-coin subset. Deep analysis remains capped at 24, and existing readiness ranking, priority reservations, direction/data/entry gates remain. Missing or stale candles fail closed; the native fetch path does not make outbound fallback requests. Other modes retain their existing universe. Rollback broad discovery with LS_NATIVE_BROAD_PREFILTER_V1=0.

V6 local invalidation, observed-target requirements, cost assumptions, minimum room, net R, direction scores, anti-chase notice guard and confirmation lifecycle remain. Failed V7 plans clear target/room/R. No automated order execution.

Tests cover both directions, duplicated level observations, genuine overlapping higher-frame barriers, close targets, failed-plan cleanup, the captured PUMP fixture, and native-only universe routing. Live comparison must report actual candidate counts and runtime; passing tests is not evidence of trading expectancy.
