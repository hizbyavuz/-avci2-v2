# TA PAPER V1 — 26 Ekim 2026

Bu, ayrı bir araştırma sürümüdür. Eski Avcı/V4.2/V4.3 karar kodunu değiştirmez
ve eski daemon'ları başlatmaz. Otomatik borsa emri, hesap erişimi, para transferi
ve gerçek kaldıraç ayarlama kodu yoktur. Telegram yalnız önceden yapılandırılmış
kullanıcı adresine, `--telegram` açıkken gönderilir.

**Durum: temel paper motor Railway’de çalışıyor; veri erişimi HTTP 418 nedeniyle bekliyor.
Grafik ve kalıcı bekleme yükseltmesinin yerel testleri geçti. Kârlılık DOĞRULANMADI.**

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
geçmiş kârlılık testi yapılmadı. `Config.fingerprint`, tüm sayısal kuralları
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

`ta_paper_v1_20261026/research.sqlite3` yeni ve ayrı WAL veritabanıdır. Eski
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

26 Ekim yerel yazılım doğrulaması: 34 test başarılı. Grafiklerin LONG/SHORT
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
varsa bu zaman esas alınır. Süre dolduğunda aynı resmi bağlantıda yeniden
denenir; yeni hata yeniden bekletir. Proxy, farklı IP/bölge veya alternatif
host ile engel aşılmaz. Sağlık mesajı, kesin sunucu zamanı ile ihtiyatlı
sınırı açıkça ayırır. Engelin kalkması ancak geçerli piyasa verisiyle anlaşılır.

Resmi kaynak: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info

## Kurulum durumu

26 Ekim TSİ kontrolünde temel sürüm `dd887aa1c3da138168a321eeb9240c5e96cc6502`,
Railway `long-short-motor` hizmetinde SUCCESS / 1 çalışan kopya olarak doğrulandı.
Eski yardımcı veri hizmetinin çalışan kopya sayısı 0, diğer üç hizmet Offline.
ChatGPT’deki dokuz otomasyon kapalı. Eski motor workflow’ları pasif; kullanıcı
`hizbyavuz/Avci2-` tarayıcısını da GitHub arayüzünde devre dışı bıraktı.

Grafik/bekleme yükseltmesi aynı release dalında, aynı Dockerfile/başlatma
komutu ve mevcut kalıcı volume ile uygulanır. Strateji kuralları ve config
hash değişmez. Yükseltmenin yayına ve Telegram’a ulaştığı ancak dağıtım
kayıtları ve teslim teyidiyle söylenebilir. Eski kaynak/volume/kayıtlar silinmez.
Eski kaynak yapılandırmasının anahtarsız kopyası:
`audits/ta_paper_previous_runtime_20261026.json`.
