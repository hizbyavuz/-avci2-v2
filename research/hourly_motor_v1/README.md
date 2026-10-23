# Saatlik Motor v1

Bağımsız, tek seferlik sabit kural deneyi. Diğer motor dosyaları, frozen config ve veritabanları değişmez. Otomatik emir kodu yoktur.

- `hourly_motor_engine.py`: günlük yön, saatlik sinyal ve plan; canlı ve geçmiş aynı fonksiyonları çağırır.
- `hourly_motor_data.py`: tarihsel aylık ilk30, günlük/saatlik futures arşivleri, gerçek funding. Arşiv ZIPleri resmi SHA256 ile kontrol edilir.
- `hourly_motor_backtest.py`: iki kontrol grubu, 1m çözüm, masraf, tek koşu mührü ve rapor.
- `hourly_motor_live.py`: paper SQLite, gönderim öncesi kalıcı kayıt, Telegram grafik/giriş/çıkış/haftalık özet. Bildirimi belirsiz kalan gönderim otomatik tekrar edilmez.

Kesin yorumlar `hourly_motor_protocol.json` içinde sonuç hesaplanmadan sabitlendi. Parametre araması yok. Bir tamamlanmış testin mührünü silip yeniden çalıştırmayın. Kesintide yalnız aynı kaynak/veri mührüyle kaydedilmiş sonuçlardan devam edilir.

```sh
python -m unittest -v test_hourly_motor
python hourly_motor_data.py --data hourly-motor-data --archive-cache hourly-motor-data/archive
python hourly_motor_backtest.py --run --data hourly-motor-data --archive-cache hourly-motor-data/archive --out research/hourly_motor_v1/results
```

Canlı **kapalıdır**. Etkin workflow'da cron yok; `HOURLY_MOTOR_LIVE_ENABLED` kapısı ve rapor SHA256'sına bağlı kullanıcı onayı gerekir. Test BAŞARISIZ ise açılmaz. GEÇTİ + sonraki kullanıcı onayından sonra aynı workflow'a `schedule: [{cron: '3 * * * *'}]` eklenebilir. GitHub zamanlayıcısı tam saniyeyi garanti etmez; mesajda gerçek quote zamanı/deadline kaydedilir.

Canlı geçmiş önbelleği tüm geçmişten aynı EMA/ATR başlangıcını korur. Yeni sinyaller için kapanan tüm saatler sırayla incelenir. İki saatlik penceresi geçmiş eski sinyal `MISSED` olarak kaydedilir; kullanıcıya süresi geçmiş giriş önerilmez. İlk çalıştırma yalnız en son kapanışı değerlendirir. Paper çıkışlarında tam giriş dakikasının bir kısmı public trades, devamı kapanmış1m ile izlenir. Funding'in sonraki gerçek kaydı henüz yayımlanmadıysa net sonuç `FUNDING_PENDING`; sıfır sayılmaz. Gerçek emir/gerçekleşme garantisi yoktur.

SQLite ayrı `hourly-motor-paper-state` dalında `hourly-motor.sqlite` olarak tutulur. Eski ledger dosyaları kullanılmaz. Mumlar ayrı Actions önbelleğindedir. Network/API hatası kaydedilir; spot/sentetik fallback yapılmaz.
