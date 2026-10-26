# TA PAPER V1.1 — Ölçüm ve arşiv araştırması

Bu, ayrı bir araştırma sürümüdür. Eski Avcı/V4.2/V4.3 karar kodunu değiştirmez
ve eski daemon'ları başlatmaz. Otomatik borsa emri, hesap erişimi, para transferi
ve gerçek kaldıraç ayarlama kodu yoktur. Telegram yalnız önceden yapılandırılmış
kullanıcı adresine, `--telegram` açıkken gönderilir.

**Durum: V1.1 kodu test ediliyor; dağıtım teyidi bu dosyada varsayılmaz.
Son doğrulanan canlı sürüm 138b545, N=0 ve Binance HTTP 418 beklemesindedir.
V1.1 kârlılık iddiası taşımaz.**

V1.1 strateji eşiklerini değiştirmez. Canlı bid/ask referansındaki ikinci yarım
spread eklemesi kaldırıldı. Yapılandırma kimliği sürüm nedeniyle değişti;
her plan/değerlendirme ayrıca git commit ve Python kaynak SHA-256 taşır.
Yeni sürüm ayrı veritabanı kullanır, eski bekleme süresini kopyalar ve eski
veritabanında çözülmemiş plan varsa başlamayı reddeder. Anahtarlar okunmaz
ve mevcut Telegram alıcısı korunur.

## Sabit araştırma hipotezi

- Evren: Binance USD-M, USDT marjinli, işlem gören perpetual sözleşmeler.
  Metadata'da COIN olmalı; TradFi/hisse/emtia/endeks/forex alt türleri ve
  bilinen emtia/stablecoin bazları dışarıda. Listeleme yaşı en az 30 gün;
  güncel 24 saatlik işlem tutarı en az 2 milyon USDT. İlk 50 sınırı yoktur.
- Yön: kapanmış 4 saatlik mumda LONG için fiyat > EMA20 > EMA50 ve EMA20
  üç mum öncesinden yukarıda. SHORT bunun simetriğidir.
- Giriş: kapanmış 30 dakikalık önceki mum EMA20'nin altında/eşitken son mum
  EMA20 ve önceki mumun tepesinin üzerinde kapanır. SHORT simetriktir.
- Stop: son altı 30 dakikalık mumun en düşük seviyesi eksi 0,1 ATR14;
  SHORT için en yüksek seviye artı tampon. ATR burada son 14 gerçek aralığın
  basit ortalamasıdır. Stop uzaklığı %0,4–5 aralığında olmalı.
- Tetik mumunun tüm aralığı 1,5 ATR'yi aşarsa giriş yoktur. Fiyat sinyal
  kapanışından işlem yönünde 0,25 ATR uzaklaşırsa kovalanmaz.
- Tek hedef: sinyal kapanışındaki stop uzaklığının 2 katı. İz süren stop veya
  kademeli hedef yoktur. Gerçekleşme fiyatı değişince hedef büyütülmez.
- Masraf sonrası ödül/risk en az 1,25; spread en fazla %0,10. Güncel ilk
  100 kademe, örnek büyüklüğü öngörülen etki sınırı içinde karşılamalıdır.
- Hacim oranı kaydedilir; yön filtresi değildir. OI/funding yön puanı üretmez.
  Funding yalnız gerçekleşmiş taşıma maliyetini hesaplamak için kullanılır.

Bu eşikler en iyi sonucu verdiği için seçilmedi. Parametre optimizasyonu ve
geçmiş sonuçlara göre eşik seçimi yapılmadı. Arşiv keşif testi aşağıda açıklanır. `Config.fingerprint`, tüm sayısal kuralları
SHA-256 ile tanımlar; bir veritabanında farklı config açılması reddedilir.

## Paper hesap ve kaldıraç

Kullanıcının gerçek sermayesi veya risk tercihi alınmadı. Bu nedenle **1.000
USDT yalnız örnek sanal başlangıç hesabıdır**. İşlem başına bütçe %0,5;
toplam planlanan açık risk %1; UTC gün başlangıcı özsermayesine göre günlük
zarar bütçesi %2. Açık zararlar yeni büyüklük hesabını düşürür; açık kârlar
büyüklüğü artırmaz. En fazla iki açık/bekleyen plan, aynı yönde en fazla bir
plan vardır. Coin başına 24 saat bekleme uygulanır. Aynı anda gelen uygun
adaylarda likidite sırası kullanılır; bu bir başarı puanı değildir.

Miktar = risk bütçesi / (birim stop zararı + giriş/çıkış komisyonu + stop kayması).
Miktar borsa `stepSize` değerine aşağı yuvarlanır. Stop/target `tickSize`
değerine yuvarlanır. Minimum tutar, minimum/maksimum miktar doğrulanır.
Notional en fazla özsermaye; örnek teminat en fazla özsermayenin %25'idir.
Gösterilen **3x yalnız teminat örneğidir**. Emir/hesap API'si kullanılmadığı
için gerçek marjin kademeleri ve likidasyon hesabı doğrulanmaz; sonuçlar
likidasyonu modellenmiş kaldıraçlı hesap performansı diye sunulamaz.

Varsayımlar: her yönde komisyon %0,05; giriş kayması %0,02 ve gözlenen yarım
spread; hedef çıkışı kayması %0,02; normal stop kayması %0,10; stres stop
kayması %1. Bunlar gerçek hesap komisyonu veya piyasada ölçülmüş kayma
dağılımı değildir. Gap oluşursa stop fiyatı yerine daha kötü mum açılışı
kullanılır; stres kaybı bile garanti edilmiş en kötü kayıp sınırı değildir.

## Zamanlama ve sonuçlar

Plan üretildikten en az iki saniye sonraki ilk tam dakikanın açılışı sanal
giriş referansıdır. O dakikanın kapanmasından sonra giriş doğrulanır; Telegram
giriş bildirimi bu nedenle yaklaşık bir dakika gecikebilir. Bu bildirim gerçek
piyasa emri gerçekleştirildiği veya o fiyatın hâlâ alınabildiği anlamına gelmez.
Önceden gönderilen TEST PLANI ile sonradan doğrulanan SANAL GİRİŞ ayrıdır.

Giriş öncesi dakikalarda stop/target görülürse plan iptal edilir. Sinyalin kısmi
dakikasında önce/sonra sırası belirsizse yine iptal; kazanç yazılmaz. Girişte
fiyat kaçmışsa, geometri bozulmuşsa, risk veya emir büyüklüğü sınırı aşılmışsa
iptal edilir. Gönderilmiş planın miktarı girişte artırılamaz.

İşlem yolu yalnız girişten itibaren 1 dakikalık mumlarla ölçülür. Aynı dakikada
iki bariyer varsa stop önce kabul edilir. Target'a yalnız değmek doldurma
kanıtı sayılmaz; aşılması gerekir, olumlu gap kazancı eklenmez. MFE/MAE yalnız
çıkıştan önce tamamen tamamlanmış mumlardan hesaplanır; çıkış mumunda belirsiz
tepe/dip kazancı eklenmez. Saat sınırına göre zorunlu 1–3 saatlik çıkış yoktur.

Funding (perpetual taşıma ödemesi), pozisyonun açık kaldığı ödeme zamanlarının
resmi geçmişinden miktar × o anki mark price × oran ile hesaplanır. Ödeme
aralığı 8 saat varsayılmaz. Çıkış dakikasındaki sıra belirsizse borç eklenir,
belirsiz alacak eklenmez ve bayrak tutulur. Funding doğrulanmadan sonuç
`FUNDING_PENDING` kalır. Eksik yol `DATA_HOLD` olur; bunlar galibiyet/mağlubiyet
sayılmaz. Eksik sonuçlar varken yeni portföy riski açılmaz.

## Kayıt, bildirim ve sınırlamalar

`ta_paper_v1_1_20261026/research.sqlite3` yeni ve ayrı WAL veritabanıdır. Eski
veritabanları değiştirilmez. Tarama evreni, metadata/ticker anlık görüntüsü,
karar verilen mumlar, bütün değerlendirmeler ve ret gerekçeleri, plan geçişleri,
giriş emir defteri ve funding yanıtı korunur. Geçişler olay tablosuna eklenir;
plan tablosu son durumu gösterir. Kapanmış mum kaynağı değişirse hata kaydı
oluşturulur ve ilgili karar durdurulur. Otomatik kayıt silme yoktur.

Telegram gönderim kuyruğu kalıcıdır. Ağ zaman aşımında teslim belirsizse
kör tekrar yapılmaz. 10 dakikadan eski gönderilmemiş kartlar gönderilmez.
Hedef adres yalnız ortam değişkeni veya eski motorun başarılı adres önbelleğidir;
`getUpdates` içinden rastgele/son kullanıcı seçilmez. Pasif bekleme spam'i yoktur.

Rapor LONG/SHORT ayrı kapanmış sayı, galibiyet sayısı, ortalama net/stres R,
giriş günü sayısı ve gün ortalamalarının standart hatasını verir. **Bu ölçüler
tahmin olasılığı veya istatistiksel başarı kanıtı değildir.** Günlerin de
bağımlı olabileceği belirtilir. N=100 veya belirli süre dolunca otomatik
“doğrulandı” durumuna geçmez. Rastgele ve basit strateji kontrolleri, görülmemiş
dönem karşılaştırması, kalibrasyon ve güven aralığı çalışması henüz yapılmadı;
rapor bunları yapılmış gibi göstermemelidir.

İlk sürüm limit/market dolumunun her ayrıntısını, gerçek likidasyonu, bakım
marjini değişimlerini, ADL'yi veya vadeli sözleşme delistinde zorunlu uzlaşmayı
modellemez. Delist/eksik yol çözümsüz kayıt olarak korunur. Portföy risk sınırı
bu tür kuyruk risklerini garanti olarak sınırlandırmaz. Tarihsel evren iddiası
yoktur; yalnız o taramada alınan evren kaydedilir.

## Çalıştırma ve doğrulama

Linux / Python 3.12, grafik için Pillow 12.3.0 ve DejaVu Sans yazı tipi gerekir.
Dockerfile bunları kurar. Windows'ta WSL kullanılabilir
(`fcntl` dosya kilidi Linux içindir).

```bash
python3 -m pip install -r requirements.ta-paper.txt
python3 -m unittest discover -s tests -p 'test_ta_paper*.py' -v
python3 -m ta_paper.benchmark --symbols 300
python3 -m ta_paper.runner --once --state-root /tmp/ta-paper-smoke
python3 -m ta_paper.runner --report --state-root /app/.long-short-state
python3 -u -m ta_paper.runner --telegram
```

Railway Dockerfile: `Dockerfile.ta-paper`. Kalıcı volume mevcut
`/app/.long-short-state` yolunda kalır; eski içeriğe yazılmaz. Tek motor
süreci çalışır, dört sınırlı REST işçisi vardır. Resmi hosta yalnız allowlist
GET çağrıları yapılır. 451/403 hatasında beklenir ve sinyal verilmez; farklı
ülke, proxy, başka borsa veya alternatif domain ile kısıtlama aşılmaz.

26 Ekim V1.1 yazılım doğrulaması: 54 kontrollü test başarılı. Grafiklerin LONG/SHORT
seviyeleri, gelecekteki mumun dışlanması, mevcut kuyruğun kayıpsız güncellenmesi,
fotoğraf teslim teyidi, belirsiz teslimin tekrarlanmaması, 429 beklemesi ve
418 sırasında Binance’e istek yapılmadan sistem bildirimlerinin gönderilmesi
kapsandı. Bunlar kontrollü testlerdir; gerçek Telegram teslimi değildir.

Önceki ağsız 300 sembol × iki zaman dilimi × 120 mum yük denemesinde ~33,1 MiB
tepe RSS, ~1 saniye, ~8,1 MiB SQLite ana dosyası görüldü. Bu, grafik eklenmiş
üretim sürümünün RAM ölçümü değildir. Canlı temel sürüm ilk veri isteğinde
`BINANCE_HTTP_418` aldı; başarılı tarama veya piyasa kaynaklı test sonucu yoktur.

## Grafikli Telegram bildirimleri

`CHARTS_20261026_1` sürümü açılışta bir kez, belirgin şekilde YAPAY VERİ ve
GERÇEK SİNYAL DEĞİL yazan örnek PNG gönderir. Bu kontrol Binance erişimine
bağlı değildir ve sanal işlem kaydı oluşturmaz. Teslim ancak Telegram’ın
`ok=true` yanıtı ve beklenen sohbet adresi eşleşince `TA_PAPER_TELEGRAM_ACK`
olarak kaydedilir. Anahtar veya sohbet adresi günlüğe yazılmaz.

Plan/durum kartında son 60 kayıtlı ve kapanmış 30 dakikalık mum; LONG/SHORT,
giriş, SL, TP çizgileri ve fiyatları; pozisyon miktarı/tutarı; örnek 3x teminat;
net ödül/risk; planlanan ve stres stop kaybı; hedefte net kazanç; zaman,
geçerlilik ve doğrulanmamış test sayısı gösterilir. Kart, plan anındaki grafiktir;
canlı fiyat veya fiyat tahmini değildir. Kapanışta sanal sonuç ayrıca yazılır.
Fotoğraf üretilemezse durum mesajı metin olarak gönderilir.

Kuyruk tablosuna `photo` ve `retry_at_ms` alanları eklenir; mevcut kayıtlar
korunur. Telegram 429 yanıtında belirtilen süre dolana kadar beklenir. Veri
sağlıksızken yalnız sistem/durum mesajları gönderilir, yeni işlem kartı verilmez.

## HTTP 418 ve istek bütçesi

Binance 418, IP seviyesinde engeldir. Kod engeli kaldırdığını iddia etmez.
`Retry-After` veya yanıttaki `banned until` zamanı okunur; daha geç olan sınır
artı iki saniye `binance_cooldown.json` içine kalıcı yazılır. Yeniden başlatma
beklemeyi silmez. Bu süre içinde ağ isteği yapılmaz. Tüm işçiler ortak hız
sınırını kullanır; yaklaşık 600 istek ağırlığı/dakika hedeflenir. Paylaşılan
IP’nin yanıt başlığındaki kullanımı 1.000’e ulaşırsa sonraki dakika beklenir.
4 saatlik mumlar kapanış değişene kadar önbellekte kullanılır.

İlk sürümün 418 olayında kesin bitiş saati kaydedilmemişti. Böyle bir kayıt
aktarılırken, son 418 olayından itibaren belgelenmiş azami 72 saat ihtiyatlı
sınır kabul edilir (`LEGACY_UNKNOWN_UPPER_BOUND`). **Bu Binance’in verdiği
kesin süre değildir; gerçek engelden daha uzun bekleyebilir.** Yeni yanıtta
zaman bulunamaması da aynı ihtiyatlı beklemeyi kullanır. Bilinen sunucu zamanı
varsa bu zaman esas alınır ve önceki varsayımdan daha kısaysa varsayılan süreyi
kısaltabilir. Aktif, doğrulanmış sunucu süresi varsayımla kısaltılamaz veya silinemez. Süre dolduğunda aynı resmi bağlantıda yeniden
denenir; yeni hata yeniden bekletir. Proxy, farklı IP/bölge veya alternatif
host ile engel aşılmaz. Sağlık mesajı, kesin sunucu zamanı ile ihtiyatlı
sınırı açıkça ayırır. Engelin kalkması ancak geçerli piyasa verisiyle anlaşılır.

Kullanıcının açık yeniden deneme talebi için `--recheck-unknown-cooldown`
seçeneği eklendi. `USER_RECHECK_20261026_1` kontrolü yalnız kaynağı varsayım olan
418 beklemesinde, aynı resmi `/fapi/v1/time` uç noktasına bir GET yapar. Bilinen
sunucu süresini geçersiz kılmaz. Kontrol hakkı istekten önce kalıcı tüketilir;
yeniden başlatma veya belirsiz teslim tekrar denemeye yol açmaz. İzin başka uç
noktalarda veya ikinci defa kullanılamaz. Geçerli saat yanıtı varsayılan beklemeyi
kaldırır; bunun ardından piyasa mumları ayrıca doğrulanır. Yeni 418 yanıtının
`Retry-After`/`banned until` zamanı kaydedilir. Sıkıştırılmış hata yanıtı da
çözümlenir. Zaman aşımı veya bozuk yanıt beklemeyi kaldırmaz. Sonuç Telegram'a
durum mesajı olarak gider. Başka IP/bölge, vekil sunucu veya veri kaynağına
geçiş bu kontrolde yapılmaz. Bu seçenek yeni sürümün başlatma komutuna açıkça
eklenmedikçe kontrol gerçekleşmez.

Resmi kaynak: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info

## Kurulum durumu

26 Ekim TSİ kontrolünde temel sürüm `dd887aa1c3da138168a321eeb9240c5e96cc6502`,
Railway `long-short-motor` hizmetinde SUCCESS / 1 çalışan kopya olarak doğrulandı.
Eski yardımcı veri hizmetinin çalışan kopya sayısı 0, diğer üç hizmet Offline.
ChatGPT’deki dokuz otomasyon kapalı. Eski motor workflow’ları pasif; kullanıcı
`hizbyavuz/Avci2-` tarayıcısını da GitHub arayüzünde devre dışı bıraktı.

Grafik/bekleme sürümü 26 Ekim 02:23 TSİ’de SUCCESS olarak açıldı; sendPhoto ve
sendMessage teslim teyitleri alındı. Tek seferlik yeniden kontrol düzeltmesi
aynı release dalı, Dockerfile ve kalıcı volume ile hazırlanır; başlatma komutuna
`--recheck-unknown-cooldown` eklenir. Strateji kuralları ve config
hash değişmez. Yeniden kontrolün yapıldığı ve piyasa verisinin erişilebilir olduğu ancak yeni
dağıtımın kontrol sonucu ve geçerli tarama kayıtlarıyla söylenebilir. Eski kaynak/volume/kayıtlar silinmez.
Eski kaynak yapılandırmasının anahtarsız kopyası:
`audits/ta_paper_previous_runtime_20261026.json`.


## V1.1 ölçüm değişiklikleri

- `reference_is_quote=True`, güncel ask/bid kontrolünde spread'i ikinci kez eklemez.
  Mum açılışındaki sanal giriş yarım spread + kayma modelini korur.
- Açık yolun eksik başlangıcı bir kez hedefli istekle aranır. Yalnız kesintisiz
  doğrulanmış önek işlenir; boşluğun sonrasına atlanmaz. 30 dakika sonra
  `UNRESOLVED_DATA`, 15 dakika arayla yeniden onarım ve bir durum mesajı vardır.
  Bilinmeyen sermaye çözülene kadar portföy girişi durur; bağımsız aday gözlemi
  devam eder. Eksik mumdan stop, kapanış veya −1R uydurulmaz.
- Geçerli bütün SETUP'lar, kapasite/cooldown nedeniyle elenenler dahil, amaçlanan
  girişten 72 saat sonraya kadar gözlenir. Kabul/iptal/gerçek giriş ayrıdır.
  Dakikada en fazla altı 1000-mum isteği ortak hız bütçesini kullanır. Ortak
  sembol/dakika verisi tekrar indirilmez. Boşluklara en fazla üç ek onarım;
  sonrasında PARTIAL/INVALID_DATA. 72 saat canlı pozisyonu kapatma kuralı değildir.
- Huni: değerlendirilen → setup → kabul → giriş → kapanış; iptal, açık veri
  belirsizliği, funding beklemesi ve gözlem tamamlığı ayrı raporlanır.
  Tarama kapsaması %95 altındaysa işaretlenir; bu yeni bir giriş filtresi değildir.
- 4h EMA uzaklığı/ATR, önceki 60 mumdaki alan/R ve BTC yön uyumu gözlem alanıdır.
  Eşik veya işlem puanı olarak kullanılmaz.

4h/30m karar mumlarında süreklilik şartı korunur. Borsa kaynaklı uzun bir boşluk
bu sembolü yeterli kesintisiz geçmiş oluşana kadar geçersiz bırakabilir; bu
sürüm eksik fiyatları doldurmaz veya bu sınırlamayı çözülmüş saymaz.

## İlk arşiv araştırması

Protokol: `research/ta_paper_v1_1/protocol.json`. Dönem Ağustos 2026,
ısınma Temmuz, 72 saatlik sonuç takibi için Eylül başlangıcı. Bu dönem eski
çalışmalarda görülmüştür: **EXPLORATORY_NOT_HOLDOUT**. Son 12 aylık görülmemiş
veri doğrulamasının yerine geçmez. Eşik optimizasyonu yapılmaz.

Resmi `data.binance.vision` arşivinden SHA-256 doğrulamalı ZIP'ler okunur;
canlı `fapi` engelini kaldırma, IP/ülke değişimi veya hesap API'si yoktur.
Arşiv dizinindeki USDT sembolleri taranır; eksik/delist dosyaları ve hatalar
ayrı kalır. İsim tabanlı emtia/stablecoin dışlaması tarihsel COIN metadata'sını
birebir yeniden kurmaz; hayatta kalan yanlılığının tamamen çözüldüğü iddia edilmez.

Canlı `assess`, `sizing`, `exit_on_bar`, `funding_cost` kullanılır. Sabit karar
gecikmesi 60 saniye, spread varsayımı %0,10'dur. Tarihsel order book/tick/lot
bilgisi yoktur: miktar/fiyat adımları yaklaşık sürekli, minimum tutar 5 USDT.
Funding oranı arşivden, mark fiyatı ödeme zamanındaki 30m mark mumunun açılışından
alınır (en fazla 1 saniye zaman farkı). Eksik oran, fiyat veya ödeme aralığı
belirsizliktir; sıfır funding varsayılmaz.

İki karşılaştırma: aynı setup zamanında sabit hash ile rastgele yön ve 4h yönü
korunarak %5 sabit seçim olasılıklı rastgele 30m giriş zamanı. Normal, 1,5× ve
2× komisyon/kayma/spread; ayrıca %1 stop kayması senaryosu vardır. Tam 0,60%
gidiş-dönüş maliyet senaryosu bu ilk protokole eklenmemiştir. BTC karşılaştırması
aynı süreye ait yönlü brüt getiridir; beta/oynaklık eşlenmiş alfa ölçümü değildir.

Bütün setup'lar ile kronolojik kapasite/risk sınırlarını uygulayan portföy
**yaklaşımı** ayrı raporlanır. Portföy geçmiş lot/defter ve gerçek ağ gecikmesini
birebir canlandırmaz. Kapanmayan 72 saatlik işlem OPEN_AT_HORIZON'dur; stop veya
sıfır kazanç yazılmaz ve portföy slotu serbest bırakılmaz. Kapananların ortalama
R'si yalnız tamamlanmış işlemlere koşulludur; eksik sonuç sayısı yanında görünür.
Gün/hafta blok bootstrap, LONG/SHORT ayrımı, ilk üç coin/en iyi beş gün çıkarma
kontrolleri vardır. Bunlar görülmemiş veri kanıtı veya garanti başarı eşiği değildir.

```bash
python3 -u -m ta_paper.research_job --root /data/ta-paper-research
# Ana motorla kalıcı, kaynakları sınırlı araştırma işi:
python3 -u -m ta_paper.runner --telegram --historical-research
```

Araştırma işi düşük CPU önceliği ve 650 MiB sanal adres alanı sınırıyla ayrı
alt süreçte çalışır; canlı motor bu bellek sınırını miras almaz. Arşivde dört,
replay'de iki işçi; raporlamada bir grup belleğe alınır. Tamamlanmış sembol
sonuçları ve checksum listeleri kalıcıdır; yeniden üretilebilen 1m ZIP önbelleği
her sembolden sonra temizlenir. Kaynak kimliği değişirse farklı çıktı dizini
kullanılır. En az 500 MiB boş alan aranır. İş bitince süreç durur; ana motor
Telegram'a bir araştırma özeti yollar. Hata veya eksik veri başarı diye sunulmaz.

Tek sembollü yazılım denemesi, tüm evren doğrulaması değildir. Dağıtım/log
teyidi olmadan bu arka plan işinin sunucuda çalıştığı söylenemez.
