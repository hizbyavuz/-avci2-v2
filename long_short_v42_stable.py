"""Pinned V4.2: quality-gated entries, chart-only notification owner."""
import json
import os

EXPECTED_STRATEGY = 'a1825cfcee14edd1ff6150d080a75ed36a1c3b99f99e28d18fbf8b40faaa967c'


def main():
    # Explicit ownership prevents inherited experiment flags from starting other motors.
    os.environ.update(LS_LEAN_RUNTIME='1', LS_NATIVE_CHAIN_V2='1',
                      LS_CHART_FIRST_V1='1', LS_CHART_FIRST_NOTIFY_V1='1',
                      LS_CHART_FIRST_PREVIEW='0', LS_MAJOR_MOVE_V1='0',
                      LS_EARLY_BREAKOUT_V1_ENABLED='0', LS_DATA_OPPORTUNITY_V2='0')
    import long_short_chart_first as chart
    if chart.STRATEGY_ID != EXPECTED_STRATEGY:
        raise RuntimeError('V4.2 decision identity changed; refusing to start')
    import long_short_daemon as daemon

    def maintenance():
        # Keep state and archives intact across the version change.
        # Only the existing non-destructive database backup runs; no migration,
        # archive compaction or retention deletion is part of a restart.
        while not daemon.STOP.is_set():
            if os.getenv('LS_BACKUP_BUCKET'):
                daemon.run_child('daily_backup', 'long_short_daily_backup.py')
            if daemon.STOP.wait(daemon.BACKUP_INTERVAL):
                return

    daemon.lean_maintenance = maintenance
    print('V42_STABLE_BOOT '+json.dumps(dict(
        version=chart.VERSION, strategy_id=chart.STRATEGY_ID,
        config_hash=chart.CONFIG_HASH, presentation='QUALITY_WATCH_READY_20261024',
        automatic_orders=False)), flush=True)
    return daemon.main()


if __name__ == '__main__':
    raise SystemExit(main())
