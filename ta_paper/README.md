# TA PAPER V1.1 — Ölçüm ve arşiv araştırması

Bu, ayrı bir araştırma sürümüdür. Eski Avcı/V4.2/V4.3 karar kodunu değiştirmez
ve eski daemon'ları başlatmaz. Otomatik borsa emri, hesap erişimi, para transferi
ve gerçek kaldıraç ayarlama kodu yoktur. Telegram yalnız önceden yapılandırılmış
kullanıcı adresine, `--telegram` açıkken gönderilir.

**Durum: `89f6256`, 26 Ekim 2026 21:13 TSİ'de
`ADMISSION_DELIVERY_20261026_4` kimliğiyle canlıda doğrulandı.
Aşağıdaki yeni teknik aday bildirimlerinin yayına alındığı bu dosyada
varsayılmaz. V1.1 kârlılık iddiası taşımaz.**

## Teknik aday bildirimleri — TECHNICAL_WATCH_20261026_1

Kullanıcının isteğiyle teknik aday görünürlüğü, örnek paper hesabın işlem
kabulünden ayrıldı. Mevcut ve değişmeyen `assess` fonksiyonunun SETUP ürettiği
coin; örnek hesap kapasitesi, risk bütçesi, aynı coin beklemesi, masraf veya
emir defteri etkisi nedeniyle portföye alınamasa da grafikli **LONG/SHORT
İÇİN İZLE** kartı üretir. Defter alınamamışsa doğrulanamadığı açıkça yazılır.
Kart sinyal kapanışını, teknik stop/hedefi, kovalama sınırını ve ret/bekleme
gerekçesini gösterir; işlem onayı, uygulanabilir fiyat veya gerçekleşmiş
giriş iddiası taşımaz. Giriş ve büyüklük kararı kullanıcıya aittir.

Bu gözlem katmanı `watch_candidates` tablosunda ayrı tutulur. Paper pozisyon,
kabul, kâr/zarar ve başarı istatistiği yaratmaz; çekirdek eşikleri değiştirmez.
Teknik koşulu oluşmayan veya karar mumları doğrulanamayan coin aday yapılmaz.
Kapanıştan sonraki değişmeyen beş dakikalık pencerede bir setup için tek
izleme bildirimi vardır. Süresi geçmiş eski adaylar deploy sonrası yollanmaz.
Aynı setup portföye kabul edilirse mevcut plan kartı kullanılır; bekleyen
izleme kartı iptal edilir. Daha önce teslim edilmiş izleme kartı tekrarlanmaz.
Sadece gözlem uyarısı için ek Binance isteği yapılmaz.

`TECHNICAL_WATCH_20261026_5` operasyon sürümü: 95 çevrimdışı test; defter reddi,
dolu portföy ve geçici veri beklemesindeki görünürlük, eksik mumda aday
üretmeme, restart/tekrar koruması, geçerlilik süresi, LONG/SHORT kartları ve
paper istatistiğinin değişmemesi kapsandı. Yayın ve canlı teslim ayrıca
doğrulanmalıdır.

## ADMISSION_DELIVERY_20261026_4 düzeltmesi

Emir defteri etkisi, yarım spread eklenmeden hesaplanmış geçici tutar yerine
nihai risk hesabındaki miktarla kontrol edilir. LONG ve SHORT için aynı
defterde geçici tutarın reddedilip nihai miktarın geçtiği regresyon örnekleri
vardır. %0,02 etki ve %0,10 spread sınırları korunur; geçebilmek için sonradan
miktar küçültülmez. Kabul ve retlerde kullanılan defter saklanır; sembol,
miktar, VWAP, ölçülen etki ve sınır loglara yazılır. Önceki iki canlı reddin
defterleri saklanmadığından bunların yanlış ret olduğu iddia edilmez.

Kalıcı kaydı ve kendi veri/defter/risk doğrulaması olan plan, giriş zamanı
gelmeden taramanın diğer sembollerini beklemeden Telegram'a gönderilir.
Sonraki Binance koruma beklemesi bu bildirimi engellemez. Plan bildirimi
sağlık mesajlarından önce işlenir; her gönderim öncesi saat yeniden kontrol
edilir. Süresi geçen, geçersizleşen veya kaydı olmayan plan gönderilmez.
Telegram 429 beklemesi ve belirsiz teslimi tekrar etmeme davranışı korunur.

Tamamlanmış tarama, arka plandaki gözlem toplama beklemesi nedeniyle tekrar
"veri bekleniyor/tarama tamamlandı" döngüsüne girmez. Her tamamlanan turda
teknik aday, kabul edilen plan ve eleme gerekçeleri bildirilir. Gerçek 418
beklemesinin kesin zamanı ayrıca bildirilir. Bu bölüm, aşağıdaki önceki
bildirim davranışına ilişkin tarihsel notların güncel karşılığıdır.

1 dakikalık gözlem ve pozisyon takibinde istek limiti gereken mum sayısına
indirilir. Örneğin üç mumluk istek ağırlığı 5 yerine 1 olur. Karar verisi,
gözlem ufku, risk/strateji eşikleri ve 418/429 korumaları değişmez. Yerel
dakikalık kullanım ve uç nokta toplamları IP başlığından ayrı raporlanır;
başka IP kullanıcılarının kimliği bu sayaçlardan çıkarılamaz.

Doğrulama: 87 çevrimdışı test. Çekirdek config hash
`f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd`
değişmez. Bu düzeltme Ağustos keşif testinin negatif sonucunu değiştirmez.

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



## Operations revision SCAN_RESUME_20261026_2

Historical revision: the all-universe admission gate below was found to be too restrictive in production and is superseded by SCAN_FLOW_20261026_3. Its incident evidence is retained.

Fixes the incident where a 21/314 scan was marked as processed and a later successful clock request produced a misleading recovery notification. Strategy Config and its fingerprint remain unchanged; the running source hash and an OPERATIONS_REVISION event identify this intervention. Existing paper state, observations, bans and archived failures are retained.

- Each 30-minute slot keeps a durable universe and per-symbol progress in `scan_progress_v2`. Successful symbols and the universe are reused across retry/restart. Pausing stops the work queue immediately; untouched symbols are pending, not hundreds of fictitious request failures.
- Requests are serial and paced by the same gate. A work slice is bounded by 64 symbols or 45 seconds (a request timeout may finish after that soft bound), so position management can run between slices.
- Failed symbols retry no sooner than one minute or the exchange deadline, whichever is later. Estimated next-request weight is checked against the existing protective 1000 threshold; the 600-weight/minute local pacing and all exchange deadlines remain in force. No alternate host, IP, region, proxy or trading endpoint is introduced.
- All universe assessments must be present before portfolio admission and READY. Book/data failures during admission remain retryable. This is an operational data-health gate; EMA, setup, sizing, stop, target and entry-delay parameters are unchanged.
- The existing five-minute entry window is preserved. Unfinished work expires explicitly, preserving partial evidence and observational setups; stale signals are never backfilled as live entries. A new candle starts a new universe. COMPLETE and EXPIRED are separate terminal results.
- SCAN_PROGRESS is append-only retry evidence; only terminal SCAN_HEALTH records count as new scan outcomes. Old historical partial records remain untouched. The old last_scan_slot field is no longer trusted as evidence of completeness.
- Telegram recovery requires a complete scan, not merely a successful time response. Waiting notifications include coverage and are coalesced to at most one per 15 minutes within a health class; genuine class changes still notify. Exact timers and causes remain in events/logs. Existing sample-chart delivery is not replayed.
- Logs distinguish reported IP usage from this process's own request totals. A high IP counter alone does not identify another tenant or prove who caused it.

Validation: 64 offline unit/integration tests, including interrupted scans, durable restart, untouched-symbol accounting, five-minute expiry, new-slot recovery, bounded slices, deferred book retry, no duplicate plans, no false READY, notification coalescing and pre-request quota protection. Existing 54 tests and frozen-parameter check remain included. Live exchange availability and profitability are not established by these tests.

## Operations revision SCAN_FLOW_20261026_3

The 19:30 Istanbul scan on 26 October assessed 217/318 symbols and found 55 observational setups, but expired at 19:35 with no admissions. The previous operations patch incorrectly coupled every plan to complete universe coverage. The original research protocol treats coverage as a reported metric, not an extra strategy threshold.

- Restore individual admission: each symbol still needs valid current closed 30m/4h bars, an in-window decision, a fresh executable order book, and unchanged sizing/risk/cooldown checks. Missing data on another symbol cannot veto it. Incomplete coverage remains PENDING/EXPIRED and is never called a complete scan or zero opportunities.
- Evaluate in descending snapshot quote-volume order, with symbol as tie-break, and handle setups promptly. Unavailable symbols remain explicit retryable data issues; a failed book cannot become a fabricated fill. Work slices are at most 32 symbols/15 seconds, with one-second yields during unblocked scans; all HTTP calls retain the same 600-weight/minute pacing and protective 1000 IP threshold.
- Reuse validated closed candles from the durable evidence database after restart. Current 4h context needs no new request. At the next close fetch the changed tail and one overlapping closed candle; reject changed overlaps, gaps and stale responses. Merge into the same latest-120 response window, excluding open candles from assessment. Keep only bounded per-symbol memory windows.
- Prepare missing candle history in small background slices outside the five-minute admission window. Pause warming before the next candle close and during exchange cooldowns. Observation collection yields before that close as well. No new host, proxy, region, API key, order endpoint or quota increase.
- Valid partial-scan plans can reach Telegram when the exchange cooldown is clear. Late PENDING and superseded PENDING/OPEN messages are marked STALE. Existing exchange-outage suppression remains. Expired-scan notices explicitly show the next fresh scan, rather than suggesting the old scan will resume after a minute-level retry timer.
- Config hash remains `f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd`. Operational revision/source identity distinguishes this intervention and changed admission timing; aggregate different revisions cautiously. Previous observations, failures, paper positions and exchange deadlines are retained.

Offline validation includes individual admission with a missing peer, liquidity ordering, deadline crossed during book retrieval, partial-scan notification eligibility without false READY, expiration notices, stale-notice suppression, restart reuse, 4h rollover, conflicting/gapped data rejection and a 318-symbol full-versus-incremental equality check. With warm 4h context that fixture uses 318 candle-request weight instead of 1272 for cold full histories; this excludes universe, position, book and observation requests and does not guarantee live completion or profitability.

## Operations revision MARKET_STREAM_20261026_6

Repeated shared-IP REST protection previously stopped both fresh candle collection
and technical WATCH cards. Enable `--market-streams` to maintain native Binance
USD-M 1m/30m/4h closed candles and 24-hour quote-volume updates in the existing
Railway service. No additional server or data subscription is provisioned.

```bash
python -u -m ta_paper.runner --telegram --historical-research --market-streams
```

- The official routed endpoint is `wss://fstream.binance.com/market/stream`.
  Market-eligible symbols are split into shards of 240 (720 candle streams;
  the first connection also carries all-market tickers and contract changes).
  There is no top-N universe cut or substitution of spot/another exchange.
- Connections use subscription acknowledgements, server ping handling, a
  30-second freshness limit, event/local-clock tolerance of 5 seconds, scheduled
  rotation before 24 hours, and bounded reconnect backoff. New connections wait
  for the existing exchange cooldown, including after deployment; this release
  cannot erase an active 418 deadline. An already-open healthy stream can keep
  collecting during a subsequent REST cooldown. Handshake blocks are persisted.
- Only final exchange candles enter a durable SQLite cache under the existing
  state volume. Readers still enforce the original 2-second publication delay,
  continuous 119-closed-bar decision window, and changed-overlap rejection.
  Existing engine evidence is reused after restart. Missing bars are never
  manufactured, interpolated, or treated as zero opportunities.
- A serial background worker fills missing completed UTC days from checksummed
  `data.binance.vision` daily files, including while new connections are waiting.
  Current-day gaps need normal guarded REST repair or subsequent live bars;
  an archive is not a real-time source. Files may be published late or absent.
  Cache writes retain original evidence; disk space remains an operational limit.
- WATCH scanning can use fresh stream tickers (at most 120 seconds old) and
  contract metadata cached for at most 6 hours. Contract-change events invalidate
  the affected symbol until metadata is fetched again. A restart never refreshes
  a cached timestamp. Initial/expired metadata and unrepaired gaps still depend
  on guarded REST access. No claim of complete REST independence is made.
- A valid technical SETUP can produce the existing **LONG/SHORT İÇİN İZLE** card
  even when the example paper account cannot verify an executable book. Such a
  card explicitly says the current book/entry is unverified, creates no paper
  position, and contributes no fabricated trade result. Incomplete peers cannot
  veto a valid candidate; expired cards and duplicate notifications remain blocked.
- Paper admission separately re-fetches current trading status/lot filters and
  the original 100-level REST book. No 20-level stream is presented as equivalent.
  Funding and gap repair keep their existing public REST gate. All cooldowns,
  rate limits, strategy thresholds, sizing and risk checks remain in force.
- `core.py` and `rate_limit.py` are unchanged. Config hash remains
  `f4485acc0340fb6352cde17558ada6f53459bafa486793423792c3b4e2ccdedd`.
  Source identity and the operations event identify the new data path; earlier
  evidence and research results are retained. No authenticated/order API is used.

Validation: 111 offline unit/integration tests, including a real local WebSocket
handshake with the pinned `websockets==17.3` dependency, subscription sharding,
closed-only ingestion, checksum failures, immutable-bar conflicts, restart
reuse, metadata expiry/invalidation, fresh paper-market revalidation, gap
isolation, archive prewarming and a technical WATCH during a durable REST ban
with zero HTTP calls. These tests do not prove live connectivity or profitability.

Official protocol references:
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
- https://github.com/binance/binance-public-data
