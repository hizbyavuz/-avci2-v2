import json

from long_short_live_pool import live_structure_confirmation, STRUCTURE_GATE_VERSION


def _row(direction="LONG", trigger=100.0):
    return {
        "direction": direction,
        "trigger_level": trigger,
        "structure_gate_json": json.dumps({
            "version": STRUCTURE_GATE_VERSION,
            "qualified_precheck": True,
            "room_pct": 0.8,
            "timeframe_confluence": 3,
            "level_touches": 2,
        }),
    }


def test_strong_long_breakout_passes():
    q=live_structure_confirmation(_row(),102.0,{
        "closed_5m_open":99.8,
        "closed_5m_high":102.3,
        "closed_5m_low":99.7,
        "closed_5m_close":102.0,
        "closed_5m_body_ratio":0.846,
        "closed_5m_close_location":0.885,
        "closed_5m_volume_mult":1.45,
    })
    assert q["qualified"] is True
    assert q["fake_breakout"] is False


def test_long_wick_fake_breakout_is_rejected():
    q=live_structure_confirmation(_row(),99.7,{
        "closed_5m_open":99.5,
        "closed_5m_high":100.8,
        "closed_5m_low":99.2,
        "closed_5m_close":99.7,
        "closed_5m_body_ratio":0.125,
        "closed_5m_close_location":0.3125,
        "closed_5m_volume_mult":1.80,
    })
    assert q["qualified"] is False
    assert q["fake_breakout"] is True


def test_weak_volume_breakout_is_rejected():
    q=live_structure_confirmation(_row(),101.2,{
        "closed_5m_open":99.7,
        "closed_5m_high":101.4,
        "closed_5m_low":99.6,
        "closed_5m_close":101.2,
        "closed_5m_body_ratio":0.833,
        "closed_5m_close_location":0.889,
        "closed_5m_volume_mult":0.82,
    })
    assert q["qualified"] is False
    assert q["volume_ok"] is False
