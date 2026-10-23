"""Operational limits for the separate observer, not trading/filter thresholds."""
from .records import digest

VERSION = 'TA_SHADOW_V1_2026_10_23'
OPS = dict(seed_bars=220, minimum_seed_bars=200, max_prefix_bars=4096,
           fetch_batch_bars=512, overlap_bars=8, storage_limit_mb=256,
           rss_limit_mb=160, metadata_max_age_ms=3_600_000,
           retry_seconds=60, measurement_batch=50, measurement_grace_ms=3_600_000,
           required_completed_groups=100,
           independent_group_rule='SAME_SYMBOL_NON_OVERLAPPING_3H_OBSERVATION_WINDOWS_BOTH_DIRECTIONS',
           threshold_changes_allowed=False, notifications=False, orders=False)
OPS_HASH = digest(OPS)
