# Gate Avcı 2: erken gözlem ve Telegram

Bu katman mevcut V5 taramasının seçim eşiklerini değiştirmez. Her 10 dakikalık
taramanın trending/new-pools kaynaklarında görünen geniş gözlem havuzunu ve
kaynak hatalarını `avci2.db` içine kaydeder. Gözlem için en az $5.000
likidite ve $1.000 24 saatlik hacim yeterlidir; bu coinler V5 adayı sayılmaz.
En az üç önceki gözlem ve 20 dakikalık geçmiş varsa 5 dakikalık hacmi tokenin
kendi medyan hacmiyle karşılaştırır. İlk anomali zamanı ve sinyal öncesi getiri
yalnızca gözlem alanıdır; V5 başarı ölçümüne dahil edilmez.

Telegram alarmı yalnızca yeni bir `validation_events` kaydının `CANDIDATE`
sınıfında olup donmuş R1/R2/R3 kurallarından en az birini geçmesiyle
değerlendirilir. Ağ ve tam kontrat eşleşir; isim veya ticker tek başına anahtar
değildir. Yakın kontrol ve rastgele kontrol grupları alarma dönüşmez.

Alarm için kaynak taramasının eksiksiz olması, güvenlik verisinin ve LP/holder
incelemesinin tamamlanması, tehlikeli yetkilerin bulunmaması ve $1.000/$5.000
pozisyonlarda satış teklifi alınması gerekir. Token-2022 uzantıları bu sürümde
manuel inceleme gerektirdiğinden temiz alarm verilmez. EVM için GoPlus ile
0x çıkış verisi gerekir. Doğrulanamayan durumlar `gate_alert_audit` tablosunda
`WITHHELD` ve gerekçesiyle saklanır; sıfır alarm geçerli bir sonuçtur.

İlk kez kabul edilen uyarılar `PENDING` olarak saklanır ve Telegram başarılı
gönderimi onayladıktan sonra `SENT` olur. Geçici Telegram hatasında bir sonraki
tarama yeniden dener. İşlem emri gönderilmez. Mevcut günlük Avcı raporu Gate
adaylarının 24 saat sonraki fiyatını ayrı izler; bu gerçekleşmiş kâr değildir.

Gereken GitHub Secrets: `TELEGRAM_BOT_TOKEN` ve `JUPITER_API_KEY`.
`TELEGRAM_CHAT_ID` verilmezse Binance Avcı 2 ile aynı botun son özel
sohbeti Telegram `getUpdates` üzerinden bulunur; sohbet bulunamazsa
adaylar gönderilmeden bekler. `HELIUS_API_KEY`, `GOPLUS_ACCESS_TOKEN` ve `ZEROX_API_KEY`
mevcutsa zenginleştirme için okunur; EVM çıkış doğrulaması olmadan EVM alarmı
verilmez. Hiçbir API anahtarı kaynak koduna veya Telegram mesajına yazılmaz.
Bot anahtarı eksikse veya sohbet bulunamazsa GitHub Actions uyarı üretir. Başarılı workflow
tek başına Telegram mesajının ulaştığını kanıtlamaz; gönderilen aday sayısı
ve bot ayarı ayrıca kontrol edilmelidir.
