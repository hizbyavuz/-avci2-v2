# Coin seçimi ve çıkış kuralı karşılaştırması

23 Ekim 2026, Türkiye saati. Bu çalışma önceki sonuçları görülen geliştirme
verisi üzerindedir. Yeni bir doğrulama dönemi, kârlılık belgesi veya canlı yayın
onayı değildir. Mevcut motor, seçim eşikleri, stoplar ve bildirim ayarları
değiştirilmedi; otomatik emir yok.

**Sonuç:** Sabit 2R hedefi Ağustos'taki üç SHORT modelinin hiçbirini pozitif
yapmadı. LONG'da daha fazla artıda sonuç, daha düşük ortalama R ile birlikte
geldi. Mevcut LONG çıkışını değiştirmek veya elenen SHORT modellerini açmak için
bu karşılaştırma gerekçe sağlamıyor.

## Nasıl karşılaştırıldı?

Önce yöntem ve kod ayrı commit'lerle kaydedildi. Ardından 2.148 adayın her biri
için aynı saatte hacim ve oynaklık bakımından yakın beş coin arasından sabit
hash ile bir karşılaştırma coini seçildi. Seçim, sonraki dakika verisine veya
sonuca bakılmadan kaydedildi. Verisi eksik çıkan coin başka coinle değiştirilmedi.
Diğer sinyal coinleri de kontrol olabilir; bu bir eşleştirilmiş rastgele giriş
kıyasıdır, "hiç sinyal yok" grubu değildir.

Karşılaştırma girişi sinyalle aynı dakikada ve aynı yöndedir. Başlangıç stop
mesafesi yüzde olarak aynıdır. Kontrolün stopu yapısal bir destek/direnç iddiası
taşımaz. İki tarafa aynı kurallar uygulanır:

- Mevcut çıkış: 2R görülünce kapanmış dört saatlik yapıyla izleyen stop, en çok
  yedi gün. Bir R başlangıç fiyatıyla stop arasındaki risktir.
- Tek yeni çıkış deneyi: aynı ilk stop, sabit 2R kâr hedefi, en çok yedi gün.

Aynı dakikada stop ve hedef görülürse stop önce sayılır. Stop üzerinden fiyat
boşluğunda daha kötü açılış kullanılır; olumlu hedef boşluğunda daha iyi dolum
iddia edilmez. Toplam %0,30 ücret/kayma varsayımı vardır. Funding eksik olduğundan
tablolar bütün maliyetler sonrası net getiri değildir.

## Aynı sinyallerde çıkış değişince ne oldu?

Burada her satır iki çıkışta da **aynı tam-yollu sinyal kümesini** kullanır.
Kontrol verisinin bulunması bu tabloya dahil olma koşulu değildir.

| Model | Dönem | Tam yol | Mevcut çıkış: artı sonuç / ortalama R | Sabit 2R: artı sonuç / ortalama R |
|---|---|---:|---:|---:|
| Taban kırılımı LONG | Ağustos | 399 | 217 / +0,846 | 223 / +0,516 |
| Eski SHORT | Ağustos | 157 | 44 / −0,441 | 44 / −0,415 |
| Başarısız yukarı kırılım SHORT | Ağustos | 156 | 31 / −0,582 | 31 / −0,633 |
| Destek geri testi SHORT | Ağustos | 192 | 43 / −0,533 | 47 / −0,462 |
| Başarısız yukarı kırılım SHORT | Eylül | 27 | 10 / −0,139 | 12 / +0,183 |
| Destek geri testi SHORT | Eylül | 43 | 12 / −0,337 | 15 / −0,190 |

LONG'da artıda sonuç oranı %54,39'dan %55,89'a yükseldiği halde ortalama R
yaklaşık %39 azaldı. Bu örnek, kazanma oranını tek başına hedeflemenin neden
yanıltıcı olabileceğini gösterir. Eylül'deki 27 kayıtlık pozitif SHORT altkümesi
saklandı; aynı modelin Ağustos'taki negatif sonucu yok sayılarak seçilmedi.

## Aynı çıkışla başka coin seçmekten daha iyi mi?

Aşağıdaki tablo mevcut izleyen stopla, iki tarafın da tam dakika yolu bulunan
**aynı eşleşmiş çiftlerini** gösterir. Bu altküme yukarıdaki bütün sinyallerden
farklıdır; farklı örneklemleri karşılaştırıp iyileşme iddia edilmez.

| Model / dönem | Çift | Gerçek sinyal günleri | Sinyal ortalama R | Kontrol ortalama R | Sinyal−kontrol |
|---|---:|---:|---:|---:|---:|
| LONG / Ağustos | 269 | 20 | +1,081 | +0,753 | +0,328 |
| Eski SHORT / Ağustos | 102 | 19 | −0,393 | −0,554 | +0,161 |
| Başarısız yukarı kırılım / Ağustos | 100 | 17 | −0,594 | −0,678 | +0,084 |
| Destek geri testi / Ağustos | 136 | 20 | −0,568 | −0,252 | −0,317 |
| Başarısız yukarı kırılım / Eylül | 6 | 5 | +0,331 | −0,417 | +0,748 |
| Destek geri testi / Eylül | 12 | 6 | −0,490 | −0,310 | −0,180 |

LONG farkının yedi günlük bloklarla hesaplanan betimleyici %90 aralığı
−0,021R ile +0,451R arasında; sıfırı içeriyor. Kontrol girişleri de pozitif.
Bu nedenle LONG'un pozitif ortalaması tek başına özel seçim avantajını kanıtlamaz.
Eski SHORT'un kontrolünden daha az kaybetmesi de pozitif işlem beklentisi değildir.

Destek geri testi Ağustos'ta kontrolünden daha kötü: betimleyici fark aralığı
−0,475R ile −0,192R. Eylül'ün altı ve 12 çifti güçlü hüküm vermeye yetmez.
Yedi günlük yollar örtüşür, coinler tekrar eder ve az sayıda takvim bloğu vardır;
bu aralıklar bağımsız istatistiksel doğrulama olarak kullanılamaz.

## Kapsama ve sınırlar

2.148 adayın tamamı tutuldu: 922 giriş dakikası eksik, 146 giriş geçersiz,
1.080 ölçüm başladı. Bunların 974'ünde tam yol, 106'sında veri boşluğu var;
84 plan boşluktan önce kapanmış. Bu 84 kayıt tam-yollu örnekleme katılmadı.

1.080 geçerli sinyal girişinin kontrol tarafında 733 ölçüm başladı; 347 giriş
dakikası eksik. Kontrollerin 638'inde tam yol, 95'inde boşluk var; 71 plan
boşluktan önce kapandı. İki tarafın da tam yolu olan **625 çift** var. Başlamayan
1.068 sinyalin önceden seçilmiş kontrolü korunur ama işlem çifti oluşturulmaz.
Tüm kısmi/kapalı sonuçlar OUTCOMES dosyasında ayrı durumlarıyla bulunur.

Arşiv kapsamı önceki adaylar için toplanmıştı; bu nedenle eksikler rastgele
sayılmaz. Evren tarihsel olarak kripto dışı sözleşmeler içeriyor. Eşleştirme
yaklaşıktır: model bazında medyan log uzaklığı 0,12–0,17, en uç uzaklık 5,32.
Uç durumlar sonradan elenmedi. Fonlama, emir defteri yürütmesi, tüm evren kapsamı,
bağımsız dönem ve ileri test olmadan canlı başarı sonucu çıkarılamaz.

## Kod ve kanıt kontrolü

- Önceki 2.148 giriş durumu ve başlayan 1.080 planın mevcut çıkış sonucu yeniden
  üretildi; eski sonuçlar değişmedi. Eski karar/çıkış/çalışma kaynak hash'leri aynı.
- Sekiz yeni test geçti: iki yönde stop-önce, fiyat boşluğu, hedef dolumu,
  süre sonu, eksik veri, geleceği kullanmayan seçim, değiştirilmeyen eksik kontrol,
  açık takvim günlerinin yanlışlıkla sinyal günü sayılmaması.
- İlk raporda, boş takvim günlerini okuyan defaultdict nedeniyle gerçek sinyal
  günü sayısı fazla yazılmıştı. Yalnız bu rapor alanı düzeltildi. Önceki rapor
  korundu; tüm işlem sonuçları, ortalamalar ve aralık çekilişleri aynı kaldı.
- Ağ isteği veya üretim değişikliği yok. Ağustos saatlik kaynakları korunmuş
  yerel hash ile, Eylül ve kullanılan dakika kaynakları önceki resmi CHECKSUM
  kanıtıyla kontrol edildi.
- Ek dönem için erişilebilir dosyalarda yedek arandı; iki aramada kullanılabilir
  sonuç dönmedi. Bu, yedeğin hiçbir yerde olmadığı anlamına gelmez.

## Karar ve dosyalar

Mevcut LONG araştırma çıkışı korunur. SHORT modelleri yayın açısından kapalı
kalır. Tek başına sabit hedef eklemekle sorun çözülmedi; bu çalışma yeni ve
başarılı bir SHORT motoru teslim etmiş sayılmaz. Yeni bir eşik deneyi başlatılmadı.

PROTOCOL yöntemi, MATCH_MANIFEST sonuçtan önce sabitlenen seçimin hash'ini,
MATCHES bütün seçimleri, OUTCOMES bütün yolları/sonuçları, SUMMARY özet ve kaynak
kapsamasını taşır. İlk rapor SUMMARY_BEFORE_DAY_COUNT_FIX içinde; düzeltmenin
hash bağı REPORT_CORRECTION içinde korunur. VALIDATION test/karar kaydıdır.

İlk eşleştirme ve replay, GitHub commit `aea4eb5add24413267c233759c4eb3f02cacdacf`
içindeki compare.py ile yeniden üretilebilir. Güncel kod yalnız rapor sayım
düzeltmesini ekler. Mevcut MATCHES/OUTCOMES dosyaları ezilmez; rapor düzeltmesi
bu dosyaların hash'lerini doğrular ve işlem sayılarını değiştiremez.
