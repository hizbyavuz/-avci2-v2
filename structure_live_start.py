"""Explicit live entry point; reuse the bot's existing stored destination only."""
import os
from pathlib import Path
import sqlite3
import time
import requests
from long_short_pattern_runtime import destination
from structure_engine.daemon import main


def start():
    if os.getenv('LS_RUNTIME_PROFILE') != 'STRUCTURE_CONTEXT_V1' or os.getenv('STRUCTURE_NOTIFICATIONS') != '1':
        raise SystemExit('EXPLICIT_LIVE_OPT_IN_REQUIRED')
    root = Path(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
    token, chat = destination(directory=root)
    os.environ['TELEGRAM_CHAT_ID'] = chat
    # Validate the already recorded destination, never discover a new recipient.
    r = requests.post('https://api.telegram.org/bot'+token+'/getChat', json={'chat_id':chat}, timeout=15)
    data = r.json()
    if not data.get('ok') or str(data.get('result', {}).get('id')) != chat:
        raise SystemExit('EXISTING_CHAT_VERIFICATION_FAILED')
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root/'structure_startup_receipts.db')
    c.execute('CREATE TABLE IF NOT EXISTS deliveries(release TEXT PRIMARY KEY,state TEXT,receipt INTEGER,created_ms INTEGER)')
    release = 'structure-live-20261023-1'
    if not c.execute('SELECT 1 FROM deliveries WHERE release=?',(release,)).fetchone():
        c.execute('INSERT INTO deliveries VALUES(?,?,NULL,?)',(release,'PENDING',int(time.time()*1000))); c.commit()
        text = ('Çizimli araştırma motoru başlatılıyor.\n4 saat + 1 saat bağlam, 15 dakika senaryo. '
                'İZLE / KOŞUL OLUŞTU bildirimleri ve çizimleri bu sohbete gelecek.\n'
                'Bu mesaj bağlantı kontrolüdür; veri hazırlığının tamamlandığı veya sinyal bulunduğu anlamına gelmez. '
                'Geçmiş test kârlılığı doğrulamadı. Gerçek alım-satım emri yok.')
        try:
            response = requests.post('https://api.telegram.org/bot'+token+'/sendMessage',json={'chat_id':chat,'text':text},timeout=15).json()
            receipt = response.get('result',{}).get('message_id')
            ok = bool(response.get('ok') and receipt)
            c.execute('UPDATE deliveries SET state=?,receipt=? WHERE release=?',('ACK' if ok else 'FAILED',receipt,release)); c.commit()
            print('STRUCTURE_STARTUP_DELIVERY '+('ACK' if ok else 'FAILED'),flush=True)
            if not ok: raise SystemExit('STARTUP_DELIVERY_FAILED')
        except requests.RequestException:
            c.execute('UPDATE deliveries SET state=? WHERE release=?',('UNCERTAIN',release)); c.commit()
            raise SystemExit('STARTUP_DELIVERY_UNCERTAIN_NO_BLIND_RETRY')
    else:
        print('STRUCTURE_STARTUP_DELIVERY_ALREADY_ATTEMPTED',flush=True)
    c.close()
    main()

if __name__ == '__main__':
    start()
