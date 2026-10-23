"""Pinned V4.1 restart: unchanged decision identity, chart-only notification owner."""
import json
import os

EXPECTED_STRATEGY = 'b3154047e7fc7631de651a4431bd478e61b54098ebb63faed6bd216cf2bc762a'


def main():
    # Explicit ownership prevents inherited experiment flags from starting other motors.
    os.environ.update(LS_LEAN_RUNTIME='1', LS_NATIVE_CHAIN_V2='1',
                      LS_CHART_FIRST_V1='1', LS_CHART_FIRST_NOTIFY_V1='1',
                      LS_CHART_FIRST_PREVIEW='0', LS_MAJOR_MOVE_V1='0',
                      LS_EARLY_BREAKOUT_V1_ENABLED='0', LS_DATA_OPPORTUNITY_V2='0')
    import long_short_chart_first as chart
    if chart.STRATEGY_ID != EXPECTED_STRATEGY:
        raise RuntimeError('V4.1 decision identity changed; refusing to start')
    import long_short_daemon as daemon

    def maintenance():
        # Keep existing state and archives intact during this restoration.
        # Only the existing non-destructive database backup runs; no migration,
        # archive compaction or retention deletion is part of a restart.
        while not daemon.STOP.is_set():
            if os.getenv('LS_BACKUP_BUCKET'):
                daemon.run_child('daily_backup', 'long_short_daily_backup.py')
            if daemon.STOP.wait(daemon.BACKUP_INTERVAL):
                return

    daemon.lean_maintenance = maintenance
    print('V41_STABLE_BOOT '+json.dumps(dict(
        version=chart.VERSION, strategy_id=chart.STRATEGY_ID,
        config_hash=chart.CONFIG_HASH, presentation='PLAIN_SL_TP1_20261024',
        automatic_orders=False)), flush=True)
    return daemon.main()


if __name__ == '__main__':
    raise SystemExit(main())
