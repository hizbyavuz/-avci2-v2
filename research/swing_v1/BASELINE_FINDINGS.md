# Basit strateji kıyası: ek kurallar ne sağladı?

22 Ekim 2026. Mevcut altyapı üzerindeki Swing araştırma sürümünün Ağustos
denemesi tamamlandı. **LONG için araştırmaya devam etmek anlamlı; işlem
avantajı gösterilmedi. SHORT için bu dönemde büyük düşüş keşfini destekleyen
sonuç yok. İki yön de canlı sinyal için onaylanmış sayılmıyor.**

Karar eşikleri değiştirilmedi. Momentum kıyasının sözleşmesi hesaplamadan önce
`e0eb98a` commit'iyle kaydedildi. Ağustos Swing sonuçları zaten görülmüştü;
bu nedenle çalışma bağımsız doğrulama değildir.

## Ne karşılaştırıldı?

Aynı saatte üç seçim: Swing'in adayları; yalnız son 24 saat getirisine göre
LONG için en güçlü / SHORT için en zayıf beş sözleşme; önceki denemede seçilmiş
benzer hacim ve oynaklıktaki kontrol adayları. Tüm seçimler o anda bilinen veriye
dayanır. Sonraki 168 saat, sonraki saat açılışından başlayarak incelenir.

833 arşivin hash'i önceki kapsama manifestiyle doğrulandı. Aynı 223.052 uygun
sözleşme-saatinden seçim yapıldı; eksik gelecek verisi sıralamayı değiştirmedi.
Bu evren kripto dışı USDT sözleşmeleri de içerir. Sonuçlar yalnız coinlerden
oluşan canlı evrenin doğrulaması değildir.

**Aşağıdaki ölçüler stop, ücret, kayma, funding veya kaldıraç içermez.** Ham
yoldaki yükseliş/düşüş, elde edilmiş kazanç veya kazanma oranı değildir.
Her sinyal saatine eşit ağırlık verilir; yoğun saatlerdeki çok sayıda aday
sonucu orantısız büyütmez. Aday sayısı ve olay ağırlıklı görünüm ayrıca JSON'da.

## LONG: 161 eşleşmiş saat, 17 sinyal günü

| Ham fiyat yolu ölçüsü | Swing | En güçlü 5 | Eşleştirilmiş kontrol |
|---|---:|---:|---:|
| Yedi gün içinde en az %30 yukarı hareket gören pay | %20,29 | %40,37 | %10,00 |
| Seçim anında son 7 günlük dipten uzaklık, ortalama | %14,23 | %96,75 | Hesaplanmadı |
| Sonraki 7 günde en büyük ters hareket, ortalama | %8,47 | %31,18 | %9,50 |
| Yedinci gün sonundaki yönlü fiyat değişimi, ortalama | +%3,65 | −%12,02 | +%2,04 |

Swing bu örnekte daha az uzamış adaylar ve daha küçük ters fiyat hareketleri
üretti. Basit sıralama ise daha çok %30 hareket gördü; fakat çok daha ilerlemiş
adaylar seçti ve yedi günlük kapanış değişimi ortalaması negatife döndü.
Bu fark, yalnız 'sonradan çok yükseldi' ölçüsünün işlem kalitesini neden tek
başına anlatmadığını gösteriyor. Dipten tam erkenlik/olay yakalama oranı henüz
ölçülmedi; düşük başlangıç uzaklığının bir kısmı zaten %25 kuralının sonucudur.

Olay ağırlıklı önceki %23,71 değeri yanlış veya silinmiş değildir: 388 olgun
LONG olayının 92'si ham %30'a erişti. Buradaki %20,29, aynı 388 olayı 161 saate
eşit ağırlıkla özetler. Basit tarafta 805 seçim, 126 farklı sözleşme vardır;
tekrar seçilen sözleşmeler bağımsız işlem sayılmaz.

## SHORT: 89 eşleşmiş saat, 17 sinyal günü

| Ham fiyat yolu ölçüsü | Swing | En zayıf 5 | Eşleştirilmiş kontrol |
|---|---:|---:|---:|
| Yedi gün içinde en az %30 aşağı hareket gören pay | %0,00 | %35,73 | %0,80 |
| Seçim anında son 7 günlük tepeden düşüş, ortalama | %12,48 | %49,05 | Hesaplanmadı |
| Sonraki 7 günde en büyük ters hareket, ortalama | %15,19 | %31,52 | %19,70 |
| Yedinci gün sonundaki yönlü fiyat değişimi, ortalama | −%6,83 | +%11,65 | −%8,70 |

148 olgun Swing SHORT olayından hiçbiri ham %30 düşüş görmedi. Bu, küçük hedefli
her SHORT işleminin zararlı olduğunu kanıtlamaz; fakat kullanıcının istediği
büyük hareket keşfi için bu adayın kabulünü desteklemez. Basit en zayıf beş
seçimi de çoğunlukla düşüşün büyük kısmından sonra oluşur; erken giriş ihtiyacına
çözüm diye sunulamaz. LONG kurallarını ters çevirmekle iyi SHORT elde edildiği
iddiası yapılmıyor. Yeni bir SHORT hipotezi farklı ve görülmemiş veri gerektirir.

## Eksikler, bağımlılık ve ayrı takvim

* Toplam 198 LONG saatinin 37'si, 107 SHORT saatinin 18'i tam sonraki yedi güne
  sahip değildi. Kayıtlar korunur; paydadan çıktıkları açıkça raporlanır. Eksik
  basit adaylar sıralamadaki başka adaylarla değiştirilmedi.
* Yedi günlük bloklarla 2.000 tekrarlı betimleyici %90 fark aralığı, Swing eksi
  momentum ham erişimi için LONG'da [−25,54; −6,02], SHORT'ta [−40,52; −32,41]
  yüzde puandır. Yalnız yaklaşık 2,5 adet örtüşmeyen yedi günlük blok vardır;
  aralıklar bağımsız doğrulama veya istatistiksel başarı/başarısızlık kapısı değildir.
* BTC'nin geçmiş 24 saati pozitif/negatif olarak ayrıldı. Bu ayda LONG Swing'in
  ham %30 erişimi sırasıyla %23,41/%13,56; SHORT'ta iki grupta da sıfır. Rejim
  filtresi sonuca bakılarak eklenmedi. Gelecek BTC hareketi etiket olarak kullanılmadı.
* Swing saatlerinden bağımsız, her UTC dört saatte ilk beş ayrıca seçildi.
  Yön başına 720 seçimden 510'u olgundu. Ham %30 erişimi LONG %37,45; SHORT
  %34,12. Farklı takvim ve tekrar sıklığı nedeniyle bunlar eşit işlem sayılı
  portföy kıyası değildir. Tam kayıtlar sıkıştırılmış kanıtta bulunur.

## Uygulama kararı

1. Swing karar eşikleri ve ilk sonuçlar korunuyor; kötü görünen sonucu silen
   veya bu aya uyduran değişiklik yapılmadı. Motor hâlâ aynı projenin ayrı
   araştırma sürümüdür. Canlı/Telegram açılmadı.
2. LONG, bir araştırma adayı olarak kalıyor. Kabul için doğru coin evreninde,
   tüm adayların dakika verili sabit giriş/stop/çıkış planı ve gerçek maliyetleri
   gerekir. Ham erişim ve küçük teknik örnekler bunun yerine geçmez.
3. SHORT, mevcut kanıtla büyük hareket sinyali olarak kabul edilmedi. Ölçümde
   tutulabilir; başarılı LONG ile tek bir başarı yüzdesinde birleştirilmemeli.
4. Eylül eksikliği sürüyor: 322/900 dosya önbellekte, kalanlar bilinmiyor.
   Ortamın arşiv alan adına erişim engeli aşılmaya çalışılmadı. Kısmi evrenden
   doğrulama sonucu çıkarılmadı.
5. Eski MAJOR kıyası için gerekli tüm evrene ait 15 dakika mumları ve yeterli
   4 saat geçmişi burada yok. Saatlik mumlardan 15 dakikalık yol üretilmedi.

Yeni kıyas kodundaki beş test geçti: geçmişe gelecek fiyat sızmaması, veri boşluğu,
yalnız geçmişle sıralama, eksik ilk beşi değiştirmeme, saat/blok ağırlıkları.
Önceki 114 testle birlikte 119 testin geçiş kaydı var; kârlılık testi değildir.

Kaynaklar: `BASELINE_PROTOCOL.md`, `baseline.py`, `MOMENTUM_COMPARISON.json`,
`evidence/2026-08-momentum-comparison.json.gz`. Yeni kıyas tamamen çevrimdışı
çalıştı. Tüm girdi ve kod hash'leri sonuç manifestindedir.
