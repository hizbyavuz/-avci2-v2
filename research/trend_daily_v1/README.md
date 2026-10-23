# Günlük Trend Motoru

Sabit günlük 20/10 kanal + ATR20 sistemi. Başlangıç stopu 2N; hedef ve süre
sınırı yok. Parametre varyantı yok. Aynı evaluate, make_plan, update_stop,
stop_exit, choose ve result fonksiyonları geçmiş testte ve canlıda kullanılır.

Eski motorlar, frozen config'ler ve veritabanları değiştirilmez. Yalnız resmi
Binance arşiv indirme sınıfı ve mevcut Telegram/GitHub durum taşıma kodu import
edilir. Yeni SQLite ayrı `trend-daily-paper-state` dalında tutulacaktır.

```bash
pip install -r requirements.trend-breakout.txt
python -m unittest test_trend_daily -v
python trend_daily_data.py --archive-cache /path/to/archive --data /path/to/daily-data
python trend_daily_backtest.py --archive-cache /path/to/archive --data /path/to/daily-data
```

Arşiv cache'i önceki motorla paylaşılabilir; bu bir motor veritabanı değildir.
Günlük hazırlanmış veri, rapor, protokol ve paper defter ayrıdır. Ham arşivler
resmi SHA256 ile doğrulanır. Geçmiş evren güncel TRADING listesiyle filtrelenmez;
arşivdeki delist olmuş ürünler de geçmişteki verileriyle değerlendirilir.

`sealed_run.json` kod/veri/protokol hash'lerini ilk sonuçtan önce sabitler.
Tamamlanan test tekrar çalıştırılamaz. Teknik kesintide `--resume` sadece
aynı kod/veri/protokol ile, son kaydedilmiş ayın devamından aynı testi sürdürür.
Bitmiş rapor varken `--resume` de reddedilir. Kural değiştirme veya iyileştirme
arayüzü yoktur. Sentetik mumlar sadece birim test dosyasındadır.

Funding için arşivdeki gerçek oranlar kullanılır. Yayımlanmamış aylar aynı
Binance futures REST verisiyle tamamlanır; sabit oran varsayılmaz. Hesaplama
sabit giriş nominalinde yapılır; değişen mark-price nominali ayrıca modellenmez.
Günlük mumun içinde stop saatini bulmak için 1m verisi yalnız ölçüm amacıyla
kullanılır. Dakika içi sıra bilinmediğinden çıkış dakikasının ekstremi
MFE/MAE'ye katılmaz; gerçekleştiği varsayılan stop/gap fiyatı katılır.

Test sonunda açık pozisyon zorla kapatılmaz. Delist olmuş veya verisi bitmiş
bir pozisyona sahte kapanış/funding atanmaz; sorun ayrıca kaydedilir. Böyle
bir veri eksiği varsa olumlu test sonucu doğrulanmış sayılmaz. Sermaye eğrisinde
stale son fiyat kullanılmışsa eğri açıkça doğrulanmadı olarak etiketlenir.

Canlı workflow schedulesiz ve `TREND_DAILY_LIVE_ENABLED` kapısının arkasındadır.
Kod, rapor GEÇTİ değilse veya kullanıcı onayı/rapor SHA256 eşleşmiyorsa gönderim
yapmayı reddeder. Saatlik eski motor yeniden açılmaz. Onaylı yeni program
`7 0 * * *` olacaktır: her gün 00:07 UTC / 03:07 Türkiye.

Canlı ATR başlangıcı geçmiş testte kullanılan arşivin başlangıcına bağlanır.
Futures erişiminde 451 alınırsa aynı resmi www.binance.com futures yolu denenir;
bu da kullanılamazsa mevcut piyasa istemcisinin spot fallback'i mesajda belirtilir.
Evren sıralaması spot hacmiyle yapılamaz. Spot verisi futures fiyatıyla eşdeğer
iddia edilmez. Paper, emir gerçekleşmesi değildir; otomatik emir kodu yoktur.

Telegram girişimleri önce kalıcı SQLite kaydına yazılır. Yanıtı belirsiz
gönderim otomatik tekrarlanmaz. Günlük kaçan kapanışlar sırayla işlenir;
eski kapanış için giriş yine yeni mesaj öncesi alınan fiyattır.

## Açık uygulama varsayımları

`trend_daily_protocol.json` tüm yorumları sonuçtan önce sabitler. 10 günlük
çıkış kanalı, az önce kapanan günü kapsayan son 10 kapalı gündür. Stop yalnız
sonraki gün geçerlidir. İlk veri ön hazırlığında bu indeksleme düzeltildi.
Tamamlanmamış teknik çalışmalar `engineering_abort_preload` ve
`engineering_incomplete_replay` klasörlerinde saklandı. Eksik günlü stop
geçmişi ilk yürütmeyi durdurdu; hiçbir tamamlanmış performans raporu veya
ortalama getiri incelenmeden veri hatası yönetimi düzeltildi. Parametre
denemesi yapılmadı. Tamamlanan tek performans raporu sonuç klasöründedir.
İsabet = net R > 0. Yıllık R ve son 12 ay sonuçları çıkış tarihine göre;
portföy getirisi günlük NAV üzerinden. BTC karşılaştırması aynı futures mumlarından
çıkarılan brüt fiyat al-tut serisidir, funding ödenen sürekli kontrat hesabı değildir.
Tarihsel stablecoin/endeks sınıflandırma listesi önceki veri modülündeki açık
listedir; bağımsız tarihsel exchangeInfo arşiviyle tam doğrulama yapılmamıştır.
