"""Gate Futures scan worker; paper signals only."""
import time
import traceback
from . import config as C
from . import engine
from . import ledger as LG
from . import telegram as TG

def main():
    with LG.connect() as db:
        first = LG.get_meta(db, "started_version") != C.VERSION
        LG.set_meta(db, "started_version", C.VERSION)
    print("GATE_TRADER_BASLADI", C.VERSION, "RAM_ONLY", flush=True)
    if first:
        TG.send("\n".join([
            "🚀 Gate Trader başladı",
            f"Sürüm: {C.VERSION}",
            f"Tarama: en hacimli {C.MAX_COINS} Gate vadeli coin, her 15 dakikada",
            f"Risk: işlem başı {C.RISK_USD:.0f}$, hedef en az {C.MIN_RR:.0f}R",
            "Kapılar: coin trendi → seviye → teyit mumu → risk/ödül; BTC yalnızca bilgi",
            "Depolama: yalnızca geçici RAM, yeniden başlatmada silinir.",
            "Otomatik emir YOK.",
        ]))
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
            engine.daily_summary()
        except Exception:
            traceback.print_exc()
        time.sleep(5)

if __name__ == "__main__":
    main()
