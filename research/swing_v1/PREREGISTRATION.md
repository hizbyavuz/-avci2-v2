# Swing Structure V1 — ön kayıt, 22 Ekim 2026

Bu sürüm kârlı olduğu gösterilmiş bir strateji değildir. Amaç saatler–günler
ölçeğinde iki açık hipotezi ve doğru işlem ölçümünü sınamaktır. Otomatik emir yok.
Eski MAJOR ve dondurulmuş V1.1 kuralları/sonuçları değiştirilmez.

## Dondurulmuş hipotezler

Sayısal sözleşme `long_short_swing.CONFIG` içindedir. JSON SHA-256 özeti her
değerlendirmeye, manifest dosyasına ve araştırma raporuna yazılır. Sonuca bakarak
eşik değiştirmek yeni sürüm ve yeni değerlendirme gerektirir.

* LONG: kapanmış 1 saatlik fiyat önceki 72 saatin tepesini aşar; saatlik quote
  hacmi aynı UTC saatin önceki yedi günlük medyanının en az 3 katıdır; agresif
  alış payı >%50; fiyat önceki 168 saatin dibinden en fazla %25 uzaktadır.
* SHORT: kapanmış 1 saatlik fiyat önceki 72 saatin dibini kırar; aynı aralıkta
  iki teyitli tepenin sonuncusu daha düşüktür; hacim en az 2 kat; agresif alış
  payı <%45; fiyat önceki 168 saatin tepesinden en fazla %25 düşmüştür.
* En az 169 kesintisiz kapanmış saat ve son 24 saatte 2M USDT hacim gerekir.
  Bu, listelenme yaşının kesin ölçüsü değildir; eldeki geçmiş uzunluğu kapısıdır.
  Taker veya hacim eksikse aday üretilmez. Düşük hacimli dönemler de arşivde tutulur.
* LONG stopu son teyitli saatlik dip (yoksa 72 saatlik dip) eksi 0,25 ATR1h;
  SHORT stopu son daha düşük tepe artı 0,25 ATR1h. En fazla %12 risk mesafesi;
  kırılan seviyeden en fazla 1 ATR uzaklık. Bu sınırlar kalibre edilmiş değildir.
* Kapanmış saatlerden üretilmiş 4 saatlik teyitli, henüz yeniden dokunulmamış
  ilk engel varsa en az 2R alan gerekir. Engel yoksa ödül/risk bilinmiyor denir;
  görünmeyen bir direnç olmadığı iddia edilmez. Yedi günlük veri dışı engeller
  kapsam dışıdır; bunun ayrıca daha uzun bağlamla sınanması gerekir.
* Aynı yöndeki yeni zirveler yeni fikir sayılmaz. Eski kırılımın içinde art arda
  altı saat kapanış olmadan aynı yönde ikinci episode açılmaz. Ters yön ayrı.
* Araştırma adaylarının tamamı kaydedilir. Bildirim sırası hacim katı, sonra
  stop mesafesi, sonra semboldür; başarı olasılığı değildir. Günlük kayan 24 saatte
  en fazla altı yeni araştırma bildirimi. İletimi belirsiz mesaj da bütçe tüketir.

## İşlem ölçüm sözleşmesi

Canlı araştırma referansı karar anından, teslim edilmiş sinyal referansı Telegram
kabulünden **sonraki ilk tam dakikanın açılışıdır**. Bunlar ayrı kohortlardır;
gerçek fill iddiası yoktur. Açılışta kırılım/risk/geç kalma koşulları bozulmuşsa
ENTRY_INVALID; kayıt silinmez. Kısmi dakika geriye dönük girişe dönüştürülmez.

Stop gerçek bir yapı seviyesidir. +2R görülmesi kâr alımı değildir: o andan sonra
tamamlanan UTC 4 saatlik bloğun dibi/tepesi ve ilk ATR tamponu ile stop yalnız
lehte taşınır. Yeni stop sonraki dakikadan itibaren geçerlidir. Maksimum süre 168
saat. 24/72/168 saatte kapanmış ve açık planların birlikte mark-to-horizon ölçüsü
vardır. Bağımsızlık birimi mesaj değil; coin/episode ve piyasa günleri kümeleridir.

Stop dakikasında sıra bilinmez: stop önce varsayılır, gap açılışı daha kötüyse o
fiyat kullanılır. İşlem içi lehte hareketin alt/üst sınırları ayrı; stop sonrası
ham hareket ayrı. Böylece 'sonra %50 yükseldi' zarar eden planı başarılı yapmaz.
Veri boşluğunda imleç bekler, hayali mum üretilmez. Eksik sonuçların sayısı raporda
kalır. 'Eksik = başlangıç stopu' yalnız hassasiyet senaryosudur; gap riskinin gerçek
en kötü sınırı olduğu söylenmez. Stop sonrası eksik ham yol, önceden kapanmış
planın gerçekleşmiş kağıt sonucunu değiştirmez.

%0,30 toplam ücret/kayma varsayımı ayrı adla raporlanır. Funding tahakkuku gerçek
settlement zamanı, oran, mark fiyatı ve eksiksiz kapsam belgesi gerektirir.
Bunlar yoksa full_net_pct=NULL; funding sıfır sayılmaz. İlk sürüm canlı worker'ı
funding tahakkuku toplamaz; yalnız bağlamı kaydeder. Dolayısıyla bu sürümden tek
başına 'tüm maliyetler sonrası kârlı' sonucu çıkarılamaz.

## İlk gerçek veri denemesi (eşik ayarı yok)

1. Resmi Binance USD-M aylık arşiv kataloğunun bütün sayfalarından USDT sembolleri.
   Bugünkü exchangeInfo listesini geçmişe uygulama. HTTP 404 ile ağ hatasını ayır.
2. Ağustos ve Eylül 2026 saatlik arşivleri; her ayın ilk 169 saati ısınma için.
   Her sinyalin sonrasında 168 saat tam olanları olgun, diğerlerini bekleyen say.
   Ayın sonundaki adaylar silinmez. Bu ilk deneme tasarım kontrolüdür; iki ayı da
   gördükten sonra ikisini yeni eşiklerin bağımsız testi olarak adlandırma.
3. Aynı anda benzer hacim/oynaklıkta, aynı yönde tetiklenmemiş bir coinden sabit
   tohumlu eşleştirilmiş kontrol. Kontrol yoksa eksik say; farklı güne taşınmaz.
   Saatlik ham hareket taraması işlem getirisi değildir. Top-5 momentum ve eski
   MAJOR karşılaştırması tamamlanmadan karmaşıklığın yararı gösterilmiş sayılmaz.
4. Tüm adayların kaydı, ret nedenleri, kapsama ve günlük yoğunluk. Gelecek hareketle
   seçilmeyen sabit örnek üzerinde gerçek 1 dakikalık plan replay'i. Küçük örnek
   sadece uygulama doğrulamasıdır; kârlılık kanıtı değildir.

## İlerleme / durdurma kapıları

İlk sürüm `SWING_NOTIFICATIONS=0` ile kağıt çalışır. Test edilen bütün varyantlar
raporlanır. OI, funding ve hesap oranı önceden tanımlanmış ek katkı testi olmadan
oy veya veto değildir. Sonraki sürümde ancak kapsamı ve ilave faydası ölçülürse
karara alınır. Belirsiz veri sessizce iyi kabul edilmez.

İşlem avantajı iddiası için: dakika verili tam replay, funding ve makul maliyet
stresi, basit momentum ve eşleştirilmiş kontrol, farklı piyasa dönemlerinde
dondurulmuş kuralların sınanması gerekir. Gün bloklu güven aralıkları raporlanır.
100 mesaj otomatik olarak 100 bağımsız örnek değildir. Dört hafta kağıt izleme
asgari operasyon denemesidir; tek başına istatistiksel yeterlilik garantisi yoktur.

## Çalıştırma / geri dönüş

`python -m unittest discover -s tests -p 'test_swing_structure.py'`

Mevcut daemon için tek seçim: `LS_RUNTIME_PROFILE=SWING_STRUCTURE_V1`.
Worker, kolektörleri ve mevcut private history relay'i kullanır. Veriler
`swing_structure_v1.db` içinde; eski DB'lere sonuç yazılmaz. Kapsama raporu eksik
coinleri/retleri gösterir; 166 sembollük eski feed kapasitesi aşılırsa tam evren
izlendiği iddia edilmez. Kolektör kapasitesini artırmak ayrı operasyon çalışmasıdır.
Bildirimler açıkça `SWING_NOTIFICATIONS=1` ile açılır ve 'ARAŞTIRMA SİNYALİ' der.
Eski profil ayarlarına dönmek karar motorunu geri alır; yeni araştırma DB'si silinmez.

Canlı hizmetler inceleme sırasında kapalıydı. Kaynak kod hazırlanması deployment
veya sağlıklı veri akışı kanıtı değildir; relay ve hacim/kapasite kontrolü gerekir.
