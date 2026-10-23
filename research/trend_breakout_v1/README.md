# Trend Kırılım Motoru v1

Yeni ve izole motor. Eski motor, frozen config ve veritabanı import edilmez.
Otomatik emir kodu, Binance hesap anahtarı veya emir endpoint'i yoktur.
Canlı kapalıdır: teslim edilen workflow'da cron yok, onay dosyası `false`,
ayrıca `TREND_BREAKOUT_LIVE_ENABLED` değişkeninin `true` olması gerekir.

## Tek sinyal kaynağı

`trend_breakout_engine.evaluate(symbol, mumlar_1h, mumlar_4h, zaman)` hem
backtest hem canlı tarafından doğrudan çağrılır. `make_plan` giriş fiyatı
bilindiğinde stop/2R/2,5 ATR/engel kontrollerini ortak uygular. Hiçbir parametre
başarı oranına göre ayarlanmaz. Geliştirme varyantı sayısı: **0**.

Mum zamanları UTC milisaniyedir. `close_ms` kapanış sınırıdır; Binance'in
dahil kapanış zamanına 1 ms eklenir. Açık mum sinyale giremez. EMA50 SMA
tohumuyla, ATR14 Wilder yöntemiyle başlar. Sinyal hacmi base volume;
evren ve öncelik hacmi USDT quote volume kullanır.
Backtest başlangıcı her paritenin ilk seçildiği aydan 60 gün öncesidir.
Canlı ilk açıldığı ayın 60 gün öncesinden başlar ve bu başlangıcı saklar.
Sonlu EMA/ATR başlangıç noktası farklı olabilir; aynı geçmiş veriler verildiğinde
iki yol aynı sinyali üretir. Bu nedenle paper karşılaştırması ayrıca gereklidir.

## Çalıştırma

```bash
pip install -r requirements.trend-breakout.txt
python -m unittest test_trend_breakout -v
python trend_breakout_data.py --cache /path/to/separate-cache
python trend_breakout_backtest.py --prepare --cache /path/to/separate-cache
python trend_breakout_backtest.py --run --cache /path/to/separate-cache
```

`--run`, sonuçtan önce `locked_test_seal.json` dosyasını yalnızca bir kez
oluşturur. Bu dosya varken yeniden çalışmayı reddeder. Silinerek yeniden test
yapılması protokol ihlalidir. Veri hazırlama sonuç hesaplamaz ve tekrarlanabilir.
Çalışma teknik olarak kesilirse `--resume`, yalnızca kod/protokol/veri hash'leri
aynıyken, tamamlanmamış tek testi kaldığı sembolden sürdürür. Tamamlanan semboller
tekrar hesaplanmaz. Sonuç dosyası oluşmuş test `--resume` ile de tekrarlanamaz.
Arşiv dosyaları resmi SHA256 dosyalarıyla doğrulanır. Sentetik veriler yalnızca
birim testlerindedir; rapor verisine karışmaz.

## Sonuç varsayımları

İlk %70 geliştirme, son %30 kilitli test. Sınırın son 168 sinyal saati,
sonuca bakılmadan dışlanır. Sinyal sonrası saat açılışı giriş olur. Stopta
aleyhe açılış boşluğu açılıştan, hedefte boşluk hedef fiyatından sayılır.
Aynı saatte iki seviyeye temas: tam 1m verisi; aynı dakikada ikisi veya
eksik/tutarsız 1m: STOP. Eksik 1h veri uydurulmaz, `UNVERIFIED` olur.
MFE/MAE, işlem erken bitse de takip eden tam 24 saati kapsar.

Funding yüzde muhasebesi, gerçek oranların yön işaretli toplamıdır; eksik ayda
iki yön için de %0,01/8 saat süreyle orantılı gider varsayılır. Sabit giriş
nominali kullanılır; değişen mark-price nominali ayrıca modellenmez.
R eğrisi kapanan işlemlerdendir; açık pozisyonların anlık düşüşü değildir.

Evren için 1d arşivi yalnızca 30 günlük quote-volume toplamı ve 60 günlük
veri yaşı hesabında kullanılır. Sinyallerde 1h ve 4h dışı analiz yoktur.
Arşiv kataloğu delist olmuş ürünleri içerir. Dışlama listesi data modülündedir.
Endekse bağlı ETF kontratları da endeks kapsamındadır; tek-hisse ve emtia
perpetual'ları USDT-M kapsamındadır ve ayrıca filtrelenmez.

## Canlı onayından sonraki kurulum

1. Raporun kendisi ve kural hash'i kullanıcıya sunulur; açık onay kaydedilir.
2. `live_approval.json` onay ve SHA256 ile doldurulur.
3. İncelenen canlı workflow'a `schedule: [{cron: '3 * * * *'}]` eklenir;
   `TREND_BREAKOUT_LIVE_ENABLED=true` ayarlanır. Bu adımlar henüz yapılmadı.
4. Mevcut Telegram botu için repo secret adları `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID` ile doğrulanır; değerler loglanmaz. İlk manuel çalışmada
   `initialize_state=true` seçilir.

SQLite paper defteri `trend-breakout-paper-state` dalındaki ayrı dosyaya
her Telegram girişiminden **önce** yazılır. Mum arşivleri bu dala yazılmaz.
GitHub SHA kontrolü ve workflow concurrency çakışmayı önler. Telegram cevap
vermeden bağlantı koparsa kayıt `UNKNOWN` olur; otomatik tekrar gönderilmez.
Bu durumda mesaj ulaşmış olabilir; gönderim garantisi iddia edilmez.

Her atlanan saat işlenir. Veri alınamayan sembol/saat `pending` olarak kalır
ve sonraki çalışmada tekrar denenir; diğer doğrulanmış adaylar hacimle seçilir.
Geçmiş kapanıştan gelen canlı öneride giriş yine yeni mesajın fiyatıdır.
Stop/engel/geç kalınma kontrolü yeni fiyattan tekrar yapılır.

Paper takipte giriş anının içinde kaldığı 1m mum, giriş öncesi hareket
barındırabilir. Bu mumda stop/TP teması varsa sonuç `UNVERIFIED_ENTRY_MINUTE`
olur; gerçekleşmiş emir gibi gösterilmez. Telegram ve emir gerçekleşmesi
aynı şey değildir. Haftalık özet ölçülebilen 24h yön örneği sayısını da yazar.

## Kaynaklar

- https://github.com/binance/binance-public-data
- https://data.binance.vision/
- QQQ/SPY endeks kontratları: https://www.binance.com/en/support/announcement/detail/4cde981dc32d4268b7645e9d16e8d63a
- SOXL: https://www.binance.com/en/support/announcement/detail/a2bce2686fc64a61b44c4335facafe7f
- SOXS/TZA: https://www.binance.com/en/support/announcement/detail/0a613aed15cc4cf78898594d7c767661
- TQQQ/SQQQ: https://www.binance.com/en/support/announcement/detail/ba04ce1272df4bdf8d2595ccb0e1954b
- KORU endeks dayanağı: https://www.binance.com/ja/academy/articles/leveraged-etfs-you-can-trade-on-binance-futures

Tam tarihsel exchangeInfo ürün-sınıflandırma arşivi doğrulanmadı; rapor bu
sınırlamayı açıkça taşır. ETF ve hisse adları birbiriyle karıştırılmaz
(örneğin SPXUSDT isimli kripto token, S&P 500 endeks kontratı değildir).
