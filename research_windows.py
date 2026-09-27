from datetime import datetime, timezone, timedelta

DISCOVERY_END_UTC="2026-09-25T21:00:00+00:00"
CALIBRATION_END_UTC="2026-10-03T00:00:00+00:00"
PURGE_HOURS=72
EMBARGO_HOURS=24
LABEL_HORIZON_HOURS=72

def _dt(text):
    return datetime.fromisoformat(text.replace("Z","+00:00")).astimezone(timezone.utc)

def computed_holdout_start():
    return (_dt(CALIBRATION_END_UTC)+timedelta(hours=PURGE_HOURS+EMBARGO_HOURS)).isoformat()

HOLDOUT_START_UTC=computed_holdout_start()
