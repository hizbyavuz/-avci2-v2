# Gate Avcı 2: erken gözlem ve Telegram

## Gate Spot için ayrı erken kağıt izleme

`gate_spot_observer.py` Gate'in tradable, normal, ST işareti olmayan USDT
paritelerini ve Gate'in resmi zincir adreslerini okur. En az $30.000 günlük
hacmi olanların fiyatı tarama bazında saklanır; güncel taramada 24 saatlik
+%10/+%20 hareketlerin kaçının on-chain gözleme girdiği Actions logunda
sayılır. Bu sayı 24 saatlik geçmişi anlatır; erken alım sinyali değildir.
Bağımsız `gate-spot-watch.yml` işi aynı gözlem kodunu ayrı bir veritabanında,
uzun on-chain taramayı beklemeden yaklaşık 10 dakikalık UTC planıyla çalıştırır.
GitHub zamanlanmış işleri geciktirebildiği için gerçek tarama aralıkları ayrıca
kontrol edilmelidir. Arşiv ve önbellek bu ayrı kağıt izleme verisini korur.

`gate_spot_watch.py` ayrı bir `gate-spot-watch-v0` araştırma kohortudur.
En az 30 günlük Gate işlem geçmişi, $300.000 günlük hacim, dar alış-satış
farkı, en az 18 dakika aralıklı iki gerçek fiyat ölçümü, erken fiyat aralığı,
resmi kontrat eşleşmesi ve Gate emir defterinde $1.000 alıp tekrar satabilme
koşullarını arar. Sonucu `gate_spot_watch` tablosuna ve Actions özetine yazar.
Bu sonuçlar **yalnızca kağıt izleme** içindir; alış emri veya Telegram alım
adayı üretilmez. On-chain V5 seçim, güvenlik ve bildirim kuralları değişmez.
Bir parite için 24 saat yeni kayıt açılmaz. Sonraki taramalardaki örneklenmiş
fiyat değişimi `gate_spot_watch_path` tablosuna 72 saate kadar kaydedilir;
bu örnekler gerçekleşebilir net getiri veya tam 1 dakikalık fiyat yolu değildir.
Ardışık snapshot henüz yoksa temiz izleme olmaması beklenir. Takip edilen
olayların sonradan gerçekleşen getirisi ve kaçırılan yükselişler ölçülmeden
bu ayrı kolun başarısı hakkında hüküm verilmez.

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

Ek risk gözlemi: zenginleştirilmiş adaylarda deployer adresiyle eşleşen
kilitsiz LP payı, düzeltilmiş ilk 10 holder payının en az 10 dakika arayla
değişimi, son işlem örneğindeki benzersiz alıcı ve çift yönlü işlem yapan
cüzdan sayısı ayrı kaydedilir. Bilinen deployer kilitsiz LP payı %10 veya
üstündeyse Telegram adayı bekletilir; V5 sinyal eşiği değişmez. Örneklenen
işlemler bütün ağın alıcı sayısı veya kesin wash-trade kanıtı değildir.
Aynı deployer'ın botun gözlediği farklı coin sayısı geçmiş rug kanıtı
sayılmaz. LP kilidi yüzdesi tek başına güvenlik garantisi değildir.

V5 sonrası araştırma katmanı: havuz yanıtı sağlıyorsa 5 dakikalık ve 1 saatlik
benzersiz alıcı sayılarını, coin'in önceki ölçümlerine göre alıcı hızını ayrı
tutar. Son işlem örneğinde aynı cüzdanın aynı blokta benzer tutarla hem alıp
hem satması iki veya daha fazla kez görülürse Telegram adayı bekletir;
bu kural sahte işlem kanıtı değil, temkinli bir örneklem vekilidir.
GoPlus kötü niyetli adres API'sinin EVM creator adresinde doğruladığı risk veya
önceki kötü niyetli kontrat sayısı >0 ise Telegram adayı bekletilir.
Negatif GoPlus sonucu "güvenli" demek değildir. Solana creator geçmişi aynı
kapsamda harici doğrulanamadığından yalnızca kendi kaydımızdaki tekrar sayısı
gösterilir; yanlış bir kara liste etiketi üretilmez.

GoPlus kilit detayında `end_time` varsa bitiş zamanı saklanır; kilidi 24 saat
içinde açılacak aday bekletilir, geçmişte bitmiş kilit korumaya dahil edilmez.
Kilidin bitişi dönmüyorsa süre bilinmiyor diye raporlanır. Multi-pool ve
NFT tabanlı likidite farklı sözleşmelerde ayrıca doğrulama gerektirebilir;
birden fazla LP holder seti varsa ağırlıkları doğrulanmadığı için alarm
bekletilir.

Sosyal veri opsiyonel: GitHub Secrets içine `X_API_BEARER_TOKEN` konursa,
X'in resmi `/2/tweets/counts/recent` API'sinde yalnızca **tam kontrat adresini**
içeren herkese açık gönderilerin son 15/önceki 45 dakikalık sayısı gözlenir.
İsim/ticker araması, otomatik sosyal puan veya Telegram kanallarında küresel
mention taraması yoktur. Anahtar yok, API yetkisi yok veya sorgu boşsa durum
`UNAVAILABLE` kalır; V5 puanı ve aday seçimi değişmez. Kaynak ücret ve
kota koşullarını X hesabında ayrıca kontrol etmek gerekir.

`gate_research_report.py`, son 30 gün kapanmış aday, near-miss ve rastgele
kontrollerin +%10 ilk hedef oranlarını, belirsiz sonuç sayısını, maliyet sonrası
ölçülebilen örnekleri ve önceden sabitlenmiş iki güvenlik alt grubunu GitHub
Actions özetine yazar. 100 kapanmış aday ve 100 kontrol olmadan üstünlük
iddiası kurmaz; bu sayı bile istatistiksel anlamlılık garantisi değildir.
Sinyalden önceki yükseliş sonuçlara eklenmez. Donmuş V5 kuralları değiştirilmez.
