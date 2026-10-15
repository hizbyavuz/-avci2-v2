"""Chart-only process ownership and collector demand; no signal-rule changes."""
import json
import os
import sqlite3
import shutil
import time
from pathlib import Path

PROFILE = 'CHART_ONLY_2026_10_15'
WORKERS = (
    ('binance_futures_universe', 'long_short_futures_universe_worker.py'),
    ('binance_futures_kline_shadow', 'long_short_futures_kline_worker.py'),
    ('binance_native_derivatives', 'long_short_native_derivatives.py'),
    ('binance_futures_history', 'long_short_futures_history_worker.py'),
    ('binance_taker_shadow', 'long_short_taker_shadow_worker.py'),
    ('chart_first', 'long_short_chart_first_worker.py'),
)
RETIRED = ('analyst', 'continuation', 'reversal', 'early_breakout_observer',
           'data_opportunity_research', 'v2_shadow', 'research_validation',
           'independent_paper', 'context_scorecard', 'operational_audit',
           'coinalyze_probes', 'gate_bybit_probes', 'v4_native_probe')


def enabled():
    return all(os.getenv(k, '0') == '1' for k in
               ('LS_LEAN_RUNTIME', 'LS_NATIVE_CHAIN_V2', 'LS_CHART_FIRST_V1'))


def demand(limit=90, full_chart=False):
    """Reserve chart ideas/outcomes, then supply verified market demand.

    No analyst/live-pool database reads. Catalogue failure is not permission
    to silently treat an obsolete legacy shortlist as current demand.
    Candle/price consumers cover the full chart liquidity universe; bounded
    auxiliary consumers retain their existing rotating request list.
    """
    from long_short_chart_first_worker import active_symbols, root
    from long_short_analyst import native_futures_websocket_universe
    active = active_symbols()
    pending = []
    path = Path(root()) / 'chart_first_v1.db'
    if path.is_file():
        c = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True, timeout=3)
        try:
            pending = [r[0] for r in c.execute("""SELECT DISTINCT symbol FROM assessments
                WHERE observed_ms>=? AND stage IN ('DELIVERED_READY','DELIVERED_WATCH')""",
                (int(time.time()*1000)-181*60000,))]
        finally:
            c.close()
    if full_chart:
        from long_short_chart_first import CONFIG
        forecast = [r[0] for r in native_futures_websocket_universe(
            all_eligible=True, quiet=True) if r[1] >= CONFIG['min_quote_volume']]
    else:
        forecast = [r[0] for r in native_futures_websocket_universe(
            slot_offsets=(0, 1), quiet=True)]
    urgent = list(dict.fromkeys(active + pending))
    requested = list(dict.fromkeys(urgent + forecast + ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']))
    if full_chart:
        print('CHART_FEED_DEMAND '+json.dumps(dict(eligible=len(forecast),
            urgent=len(urgent), requested=len(requested), capacity=limit,
            deferred=requested[limit:])), flush=True)
    return requested[:limit], urgent[:limit]


def health(root):
    """Report the current worker's records, never the retired analyst's clock."""
    path = Path(root) / 'chart_first_v1.db'
    now = int(time.time()*1000)
    out = dict(profile=PROFILE, observed_ms=now, available=path.is_file())
    usage = shutil.disk_usage(root)
    out['disk_used_pct'] = round(100*usage.used/usage.total, 2)
    out['storage_pressure'] = usage.used/usage.total >= .85
    if not path.is_file():
        return out
    c = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True, timeout=5)
    try:
        latest = c.execute('SELECT MAX(observed_ms) FROM assessments').fetchone()[0]
        out['latest_assessment_ms'] = latest
        out['assessment_age_seconds'] = round((now-latest)/1000) if latest else None
        out['active_ideas'] = c.execute('SELECT COUNT(*) FROM episodes WHERE ended_ms IS NULL').fetchone()[0]
        out['stages_last_24h'] = dict(c.execute('SELECT stage,COUNT(*) FROM assessments WHERE observed_ms>=? GROUP BY stage', (now-86400000,)))
        out['outcomes_last_24h'] = dict(c.execute('SELECT state,COUNT(*) FROM outcomes WHERE checked_ms>=? GROUP BY state', (now-86400000,)))
    finally:
        c.close()
    return out
