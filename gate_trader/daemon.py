"""Gate Futures scan worker; paper signals only."""
import time
import traceback
from . import config as C
from . import engine
from . import ledger as LG
from . import telegram as TG
from . import early_outcomes as EO

def main():
    with LG.connect() as db:
        first = LG.get_meta(db, "started_version") != C.VERSION
        LG.set_meta(db, "started_version", C.VERSION)
    print("GATE_TRADER_BASLADI", C.VERSION, "PERSISTENT_SQLITE", flush=True)
    # Startup details stay in Railway logs, never spam Telegram.
    last_scan_bar = None
    last_track = 0.0
    while True:
        now = time.time()
        bar = int(now // 900)
        try:
            if bar != last_scan_bar and now - bar * 900 >= C.SCAN_DELAY_SEC:
                last_scan_bar = bar
                engine.scan(now)
            if now - last_track >= C.TRACK_EVERY_SEC:
                last_track = now
                engine.track(now)
                EO.track(now)
            # Daily summary disabled by request; only actionable alerts are sent.
            # engine.daily_summary()
        except Exception:
            traceback.print_exc()
        time.sleep(5)

if __name__ == "__main__":
    main()
