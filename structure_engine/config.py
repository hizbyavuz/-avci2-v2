"""Declared hypotheses, not optimized thresholds or a profitability claim."""
import hashlib
import json

VERSION = 'STRUCTURE_CONTEXT_V1_2026_10_23'
MINUTE = 60_000
HOUR = 60 * MINUTE
PERIODS = {'15m': 15*MINUTE, '30m': 30*MINUTE, '1h': HOUR, '4h': 4*HOUR}
CONFIG = dict(
    primary_interval='15m', alternative_interval='30m', context_intervals=['1h', '4h'],
    maximum_bars=220, minimum_bars={'15m': 120, '30m': 120, '1h': 48, '4h': 32},
    pivot_left=2, pivot_right=2, significant_leg_min_bars=3,
    significant_leg_min_atr=1.5, atr_period=14, zone_half_width_atr=.15,
    break_buffer_atr=.05, approach_atr=1., stop_buffer_atr=.2,
    max_chase_atr=.75, retest_bars=6, double_extreme_tolerance_atr=.35,
    min_quote_volume_24h=2_000_000., assumed_round_trip_cost_pct=.30,
    minimum_plan_net_r=1.5, horizon_hours=3, entry_delay_minutes=1,
    notification_cooldown_minutes=60, max_notifications_per_cycle=3,
    max_notifications_24h=20, disk_free_floor_mb=512,
    state_directory_limit_mb=4096, memory_rss_limit_mb=768,
    status='UNVALIDATED_RESEARCH', funding_included=False,
)
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, allow_nan=False).encode()).hexdigest()
