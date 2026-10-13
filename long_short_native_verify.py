"""One labelled Telegram transport check. No signal or trade events created."""
import json
import os
import sqlite3
import time
from long_short_native_derivatives import evidence,root,VERSION

def main():
    path=os.path.join(root(),'native_chain_checks_v2.db')
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE IF NOT EXISTS checks(version TEXT PRIMARY KEY,sent_at TEXT,payload TEXT)')
        if c.execute('SELECT 1 FROM checks WHERE version=?',(VERSION,)).fetchone():return 0
    from long_short_live_pool import send_telegram,trigger_execution_proxy
    for _ in range(48):
        ev=evidence('BTCUSDT')
        if ev['ready']:
            book=trigger_execution_proxy('BTCUSDT')
            if book.get('available') and book.get('source')=='BINANCE_FUTURES_BOOK':
                msg=('🔧 LONG/SHORT V2 · BAĞLANTI TESTİ\n'
                     'Binance Futures mum ve türev veri yolu bağlandı.\n'
                     'BTC veri kontrolü: 4/4 · Vadeli emir defteri: erişilebilir.\n'
                     'Bu mesaj yalnızca Telegram teslim testidir; işlem sinyali değildir.\n'
                     'Otomatik emir yok.')
                sent=send_telegram(msg)
                with sqlite3.connect(path) as c:c.execute('INSERT OR IGNORE INTO checks VALUES(?,?,?)',
                    (VERSION,sent,json.dumps({'source':ev['source'],'four_factor_ready':True,'book_source':book['source']})))
                print('V2_TELEGRAM_TRANSPORT_VERIFIED',json.dumps({'version':VERSION,'sent_at':sent}),flush=True)
                return 0
        time.sleep(5)
    print('V2_TELEGRAM_TRANSPORT_PENDING_DATA',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
