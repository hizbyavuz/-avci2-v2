# Teknik gözlem modülü — 2. adım

23.10.2026. Kullanıcının onayladığı kapsam: **ayrı modülde tanımlar**.
Taban commit: `c39383597fc8ac68849fd80ee7a0a932e053d3d7`.

Bu sürüm `technical_observer_v1` adlı saf Python paketidir. Üretim motoruna
import edilmez; zamanlayıcı, DB yazarı, Telegram göndericisi, grafik veya emir
arayüzü içermez. `observe()` JSON'a çevrilebilen gözlem döndürür. Hiçbir yeni
etiket mevcut seçimleri engellemez; her sonuç `decision_effect=NONE` taşır.
Mevcut 42/12, veri güvenliği, bildirim ve R eşikleri değiştirilmemiştir.

## Arayüz ve kanıt

```python
from technical_observer_v1 import observe

observation = observe(symbol, frames, asof_ms, provenance=source_reference)
```

`frames`: `15m`, `1h`, `4h` anahtarları; her mumda `open_ms`, `close_ms`,
`open`, `high`, `low`, `close`, `quote_volume`, varsa `received_ms`.
Mumlar zaman sıralı ve bitişik olmalıdır. Açık mum, gelecekte ulaşmış veri,
eksik mum, geçersiz OHLC/hacim ve bayat son mum açık hata üretir. Bu hatalar
yalnız bu gözlemi geçersiz yapar; canlı motorun kapıları üzerinde etkisi yoktur.
Kaynak doğruluğu bu modül tarafından ağdan doğrulanmaz. Kaynak belirtilmezse
`source_verification=DOĞRULANMADI` döner; sağlanan kaynak açıklaması da tek
başına borsa doğrulaması değildir.

Girdinin özeti (`input_hash`), sürüm, config ve hash, mevcut bağlamlar,
kırılım öncesi bölge/swing bağlamları, nedenler ve tüm bulunan kırılım adayları
döner. Aynı kırılımın farklı TF bölgelerine karşı görülmesi ayrı gözlemdir;
**bağımsız örnek sayılmaz**. Bağımsız olay gruplaması 5. adımın konusudur.

## Tanımlar ve açık uygulama tercihleri

Bütün sayısal başlangıç değerleri `technical_observer_v1/config.py` içindeki
değişmez `Settings` nesnesinden okunur. Veriye göre ayarlama/optimizasyon yapılmadı.

| Konu | Kesin tanım |
|---|---|
| ATR(14) | Yeni modülde **Wilder** yöntemi: ilk 14 TR ortalaması, ardından `(önceki ATR × 13 + TR) / 14`. İlk TR'de önceki fiyat olarak ilk mum açılışı alınır. İlk 13 değer yoktur. Eski motorun basit ATR ortalaması değişmedi. |
| Swing | Soldaki 3 ve sağdaki 3 mumun hepsinden kesin yüksek/alçak. Eşit tepe/dip pivot sayılmaz. Sağdaki üçüncü mum kapanmadan onaylanmaz. |
| Küçük swing | Önceki kabul edilmiş zıt swing'e mutlak mesafe, aday swing mumundaki ATR'nin 1 katından küçükse gerekçesiyle dışarıda tutulur. İlk seed için zıt swing yokluğu ayrıca kaydedilir. |
| Değişmezlik | Aynı türde daha uç swing geldiğinde önceki silinmez/değiştirilmez. Aynı mum hem tepe hem dip ise sıralama bilinmediğinden `AMBIGUOUS_OUTSIDE_BAR` olarak ayrılır. |
| Yapı | Son iki kabul edilmiş tepe/dip: HH+HL yükseliş, LH+LL düşüş, diğerleri yatay. İki çift yoksa **DOĞRULANMADI**; yatay diye sunulmaz. Her TF ayrı. |
| Bölgeler | Her TF'de son 200 mum içindeki onaylı swing'ler. O TF'nin bölge hesap anındaki ATR'si kullanılır. Ardışık fiyat uzaklığı en fazla 0,5 ATR olan noktalar bağlı kümeye alınır. Zincirleme yakınlık geniş bir bölge üretebilir; bu gizlenmez. TF'ler birbirine karıştırılıp tek ATR ile birleştirilmez. |
| Bölge sınırı | Üye fiyatlarının min/max'ı; genişlik 0,25 ATR'den küçükse orta noktanın iki yanına eşit genişletilir. Ham min/max ayrıca saklanır. |
| Bölge rolü | İlk rol, kümedeki en son onaylanmış swing'in türünden gelir. Üst kenarın üzerinde kapanış → destek; alt kenarın altında kapanış → direnç; içeride kapanış rolü korur. Fitil tek başına rol değiştirmez. Her değişim zamanıyla saklanır. |
| Kırılım | 15m son kapanış, önceden oluşturulmuş bölgenin dışına ilk kez geçer. Önceki 15m kapanışı o kenarın dışına zaten geçmişse yeni kırılım sayılmaz. Kırılan bölge **15m, 1h veya 4h** kaynaklı olabilir. |
| Kırılım kalitesi | Mutlak gövde/mum boyu ≥ 0,50 ve son mumun USDT işlem hacmi / önceki 20 mumun USDT hacim medyanı ≥ 1,5. Sıfır medyan doğrulanamaz. Sağlanmayan koşullar `ZAYIF_KIRILIM` nedenleridir; gözlem silinmez. Mum gövdesinin yönü ayrıca kaydedilir, yeni engelleyici şart yapılmaz. |
| Giriş referansı | LONG üst kenar, SHORT alt kenar: **bandın tam kenarı**, tampon yok. Kapanış fiyatı veya gerçekleşmiş emir fiyatı değildir. Tick yuvarlaması 3. adımda ele alınacaktır. |
| Stop | Kırılım mumundan önce onaylanan **en son 15m karşı swing**: SHORT tepe + 0,3 güncel 15m ATR; LONG dip − 0,3 ATR. Daha eski swing'e geri gidilmez; sonraki fitillerle genişletilmez. |
| Stop açıklaması | En son tepe LH / dip HL değilse bunu açıkça etiketler, eski bir LH/HL aramaya dönmez. Hiç karşı swing yoksa stop yok. Stop yanlış taraftaysa veya pozitif değilse geçersiz geometri açıklaması. Mesafe > 3 ATR ise `STOP_TOO_WIDE`, yalnız gözlemsel. |
| Hedef | Bütün TF bölgelerinde yönün karşı engeli aranır. Bölgenin **tamamı** kırılan bölgenin ötesinde olmalı ve girişten yakın kenarına uzaklık ≥ 1 güncel 15m ATR olmalı. En yakın uygun bölge seçilir; eşit mesafede yüksek TF önceliklidir. |
| Yakın engeller | Mesafesi 1 ATR'den küçük ya da kırılan bantla çakışan bölgeler gerekçeleriyle `rejected_regions` içinde tutulur. Uzak hedef seçilmesi, aradaki yakın engellerin yok olduğu anlamına gelmez. |
| Hedef yokluğu | Uygun bölge yoksa `target.price=None`, `NO_CONFIRMED_TARGET`. Fibonacci projeksiyonu veya sabit R ile hedef uydurulmaz. |
| Net R | Yüzde birimleriyle `(brüt ödül − 0,30) / (brüt risk + 0,30)`. Giriş, bölge kenarıdır. Eksik/ters/geçersiz fiyatlarda R yok. Bu modülde minimum R filtresi veya `plan_eligible` yoktur. |
| GEÇ | Mutlak fiyat–EMA20 uzaklığı > 3 ATR **veya** son 12 kapanış aralığındaki mutlak net fiyat hareketi > 4 ATR. EMA ilk 20 kapanış ortalamasıyla başlar. Yalnız gözlemsel; hareketlerin toplam yolu kullanılmaz. |
| İsimli formasyonlar | OBO, üçgen, bayrak ve diğer isimli formasyon dedektörleri yok; `named_pattern=None`. |

### Bölge gücündeki başlangıç tercihleri

Kullanıcının puan bileşenlerine şu açık ilk tanımlar verildi; veriye göre seçilmedi:

- Art arda bandı kesen mumlar tek dokunma olayıdır. Yeni dokunma için arada
  bandı kesmeyen en az bir mum gerekir. Yalnız bölgenin tüm üyeleri onaylandıktan
  sonraki dokunmalar sayılır; geçmişe dönük yeni bölge icat edilmez.
- Tepki: dokunma olayının bitişinden sonraki **3 kapanmış mum** içindeki, rolün
  beklenen yönündeki en büyük uzaklığın dokunma ATR'sine oranı. Yalnız tamamlanmış
  pencerelerin ortalaması alınır; tamamlanmamış tepkiler `None` kalır. Bu ileri
  pencere ancak gerçekten kapanmışsa mevcut gözleme eklenir; geçmiş sinyale
  önceden biliniyormuş gibi taşınmaz.
- TF ağırlıkları: 15m=1, 1h=2, 4h=3.
- Yenilik: `max(0, 1 − son üye swing yaşı / 200)`.
- Toplam: dokunma sayısı + ortalama tepki/ATR + TF ağırlığı + yenilik.
  Bileşenler ve katsayıları config/kayıtta açık. Güç, aday engellemez.

## Zaman ve değişmezlik sınırı

Swing değişmezliği **aynı geçmişin sonuna mum eklenmesi** için geçerlidir.
Fonksiyon geçmişi yeniden daraltan hareketli bir pencereye bağımsız olarak
başlatılırsa ilk ATR/seed değişebilir. Canlı bağlantı kurulurken tam geçmişin
eşdeğerini koruyan artımlı durum/başlangıç noktası gerekir; 220 mumluk pencereyi
her seferinde sıfırdan başlatmakla değişmezlik garanti edilmiş sayılmayacaktır.

Kırılım bölgesi/stop dayanakları kırılım mumundan **önce kapanan** mumlardan
hesaplanır. Bütün verinin gözlem anında ulaşmış olması kontrol edilir. Bu,
verinin kırılım mumunun açıldığı milisaniyede toplayıcıya ulaşmış olduğu iddiası
değildir. `candle_cutoff_ms` ile `observed_ms` ayrı kaydedilir.
Karar anındaki güncel 1h/4h etiketleri ayrıca tutulur.

## Kontroller ve henüz tamamlanmayan işler

Geliştirme kontrolü: `python -B -m unittest -v tests.test_technical_observer_definitions`.
15 geliştirme kontrolü geçti. Test girdileri açıkça sentetiktir; piyasa verisi gibi sunulmaz. Formül,
teyit zamanı, prefix değişmezliği, bölge/rol, en yakın stop, hedef dışlamaları,
gözlemsel zayıf kırılım, veri zamanı ve ayna geometrisi kontrol edilir.
Fiyatı sabit bir sayı etrafında aynalamak giriş fiyatını da değiştirir. Geometri
ayna olmalıdır; fiyat yüzdesi üzerinden tanımlanan %0,30 maliyetli R'nin farklı
giriş fiyatlarında aynı çıkması beklenmez. R her iki yönde kendi formülüyle ölçülür.

MAGMAUSDT 23/10 17:00 gerçek karar görüntüsü ve ham mumları bu çalışmada
edinilmedi. **MAGMA regresyonu doğrulanmadı.** Yeni kuralların o olayda hangi
stopu seçtiği uydurulmadı. En yakın onaylı swing hâlâ uzaksa modül yakın stop
icat etmek yerine genişlik etiketini döndürür.

Bu, 4. adımın gerçek-vaka kabul raporu değildir. 3. adım (Telegram/grafik,
tick size, kalıcı setup JSON), 4. adımın tam regresyonu ve 5. adım (eski–yeni
canlı gölge kayıt/100 bağımsız örnek) **başlatılmadı**. Deploy yapılmadı.
