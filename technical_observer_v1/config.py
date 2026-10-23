"""Initial declared definitions, not fitted values. Independent of frozen engines."""
from dataclasses import asdict, dataclass
import hashlib
import json

VERSION = 'TECHNICAL_OBSERVER_V1_DEFINITIONS_2026_10_23'


@dataclass(frozen=True)
class Settings:
    atr_period: int = 14
    atr_method: str = 'WILDER'
    swing_left: int = 3
    swing_right: int = 3
    swing_min_opposite_atr: float = 1.0
    zone_lookback: int = 200
    zone_cluster_atr: float = 0.5
    zone_min_width_atr: float = 0.25
    zone_tf_weights: tuple = (('15m', 1.0), ('1h', 2.0), ('4h', 3.0))
    strength_touch_weight: float = 1.0
    strength_reaction_weight: float = 1.0
    strength_recency_weight: float = 1.0
    reaction_bars: int = 3
    breakout_body_fraction: float = 0.50
    volume_lookback: int = 20
    breakout_volume_multiple: float = 1.5
    stop_buffer_atr: float = 0.3
    wide_stop_atr: float = 3.0
    target_min_distance_atr: float = 1.0
    round_trip_cost_pct: float = 0.30
    late_ema_period: int = 20
    late_ema_distance_atr: float = 3.0
    late_move_bars: int = 12
    late_move_atr: float = 4.0

    def __post_init__(self):
        integers = ('atr_period', 'swing_left', 'swing_right', 'zone_lookback',
                    'reaction_bars', 'volume_lookback', 'late_ema_period', 'late_move_bars')
        import math
        for name, value in asdict(self).items():
            if name in integers:
                if type(value) is not int or value < 1:
                    raise ValueError('INVALID_SETTING:' + name)
            elif name not in ('atr_method', 'zone_tf_weights'):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                    raise ValueError('INVALID_SETTING:' + name)
        if self.atr_method != 'WILDER':
            raise ValueError('UNSUPPORTED_ATR_METHOD')
        if not 0 < self.breakout_body_fraction <= 1:
            raise ValueError('INVALID_BODY_FRACTION')
        if (type(self.zone_tf_weights) is not tuple or
                any(type(pair) is not tuple or len(pair) != 2 for pair in self.zone_tf_weights)):
            raise ValueError('INVALID_TIMEFRAME_WEIGHTS')
        if dict(self.zone_tf_weights).keys() != {'15m', '1h', '4h'}:
            raise ValueError('INVALID_TIMEFRAME_WEIGHTS')
        weights = dict(self.zone_tf_weights)
        if len(self.zone_tf_weights) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                    and math.isfinite(v) for v in weights.values()):
            raise ValueError('INVALID_TIMEFRAME_WEIGHTS')
        if not weights['4h'] > weights['1h'] > weights['15m'] > 0:
            raise ValueError('TIMEFRAME_WEIGHTS_MUST_BE_ORDERED')

    def payload(self):
        return asdict(self)

    def fingerprint(self):
        raw = json.dumps(self.payload(), sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()


DEFAULTS = Settings()
