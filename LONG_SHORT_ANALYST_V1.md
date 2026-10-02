# Long / Short Analyst V1

Ayrı, deterministik Binance USDT perpetual analiz motorudur. Mevcut Avcı eşiklerine veya Gate motoruna dokunmaz.

## Ne okur?
- 5m / 15m / 1h / 4h fiyat yapısı ve EMA trendi
- RSI, ATR ve hacim genişlemesi
- 20-bar breakout / breakdown
- Open Interest değişimi
- Funding
- Taker buy/sell ratio
- Global long/short ratio
- Futures order-book imbalance
- BTC rejimi

## Çıktı
- 🟢 LONG SETUP
- 🔴 SHORT SETUP
- 🟡 TEYİT BEKLE
- ⚪ İŞLEM YOK

Setup çıktısı giriş bölgesi, invalidation/stop, TP1/TP2 ve yaklaşık R/R verir. Sistem hiçbir zaman emir göndermez.

## Paper test
Her LONG/SHORT setup ayrı SQLite veritabanında saklanır. Sonraki taramalarda 5m fiyat yolu ile stop / TP1 / TP2 gözlenir. Aynı mumda hem stop hem hedef görülürse kötümser biçimde stop-first sayılır.

## Çalışma
GitHub Actions workflow: `.github/workflows/long-short-analyst.yml`
- Her 15 dakikada bir çalışır.
- Manuel tetikleme de açıktır.
- Telegram secrets mevcutsa mesaj yollar.
