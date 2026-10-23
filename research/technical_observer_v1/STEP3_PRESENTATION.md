# 3. adım — aynı kayıttan mesaj, grafik ve fiyatlar

23.10.2026. Yalnız onaylanan mesaj/grafik/kayıt katmanı tamamlandı.
Taban: `3d6a099267fa1b0e64ab0349a0da6686aa53c57a`.

**Bu kod canlı motora bağlanmadı, deploy yapılmadı ve Telegram gönderilmedi.**
Üretim DB'si ve geçmiş kayıtlar değişmedi. `technical_observer_v1/engine.py`
ve `config.py` değiştirilmedi; mevcut 42/12, güvenlik ve bildirim kapıları aynı.
Yeni kayıtların `decision_effect=NONE`, `order_authorized=False` alanları sabit.

## Kullanıcıya görünen sonuç

- Tek 15m grafikte mavi kırılan bölge, onaylı swing noktaları, sarı stop
  dayanağı ve sağda büyük **GİRİŞ REFERANSI / STOP (SL) / HEDEF (TP)** etiketleri.
- Giriş çizgisi bandın LONG üst / SHORT alt kenarındaki **aynı fiyat alanı**.
  Son kapanış veya gerçekleşmiş giriş olarak sunulmaz.
- Stop ve hedef için kırmızı/yeşil senaryo alanları yalnız üç fiyatın yön
  geometrisi geçerliyse çizilir. R yetersiz olsa da araştırma referansları
  görülebilir; başlık açıkça **R/R YETERSİZ** olur.
- Mevcut `minimum_plan_net_r=1.5` yalnız açıklama için okunur. Yeni işlem
  filtresi eklenmedi. Düşük R mesajı: **“Kırılsa da R/R yetersiz: …”**.
  “Kapanış bekleniyor” ifadesi kullanılmaz.
- Yeterli hesaplanan R, işlem onayı sayılmaz; başlık bunu açıkça söyler.
- Onaylı hedef yoksa `TP: YOK · doğrulanmadı`; hedef veya formasyon üretilmez.
- 15m/1h/4h yapıları, zayıf kırılım, yakın engel, geniş stop ve GEÇ nedenleri
  aynı JSON ve metinde bulunur. Grafikte okunabilirlik için karar bölgesi,
  seçilmiş hedef bölgesi ve görünür swing'ler çizilir; tüm bölgeler kayıtta kalır.

## Tek kaynak ve geçmişi koruma

`technical_observer_v1/records.py`:

| Fonksiyon | İşlev |
|---|---|
| `build_record` | Girdi mumlarının hash'ini gözlemle karşılaştırır. Ham setup, config, önceki/güncel TF bağlamları, kullanılan/reddedilen swing ve bölgeler, nedenler, bütün mumlar ve çizim alanlarını tek JSON nesnesine alır. |
| `validate_record` | İçerik SHA256 kontrolü yapar. Değişmiş kayıt mesaj/grafik üretmez. Bu bir dijital imza veya kaynak doğruluğu kanıtı değildir. |
| `write_record` / `read_record` | Hash adlı JSON'u atomik ve üzerine yazmadan kaydeder/okur. Aynı kayıt tekrar çağrılırsa aynı dosya döner. Mevcut içerik farklıysa hata verir, geçmişi değiştirmez. |
| `record_observation` | Çağrıldığında gözlemdeki **bütün** setup'ları, düşük R/zayıf kırılım/hedefsiz olanlar dahil kaydeder. Kendi kendine tarama başlatmaz. |

`technical_observer_v1/presentation.py` içindeki `message(record)` ve
`render(record, path)` yalnız kayıt alanlarını okur. Swing, bölge, stop, hedef,
R veya trendi yeniden hesaplamaz. PNG metadata'sındaki `RecordHash`, JSON ve
mesajdaki kayıt kimliğiyle eşleşir. Fiyat etiketleri iki çıktıda aynı alandan gelir.
Mumlar, mumların gerçek OHLC değerleriyle çizilir; doji için yapay gövde eklenmez.
Bölge bantları üye swing'lerin teyit zamanından önce çizilmez.

## Tick size — doğrulanmış bilgi sınırı

`capture_price_filter`, toplayıcının sağladığı Binance USD-M `exchangeInfo`
yanıtından sembolün `PRICE_FILTER` alanını alır; `pricePrecision` kullanmaz.
Bu saf fonksiyon ağ isteği yapmaz ve kaynağın gerçekliğini kendisi doğrulamaz.
Kaynak URL, sembol, gözlenme zamanı, yanıt özeti ve özgün filtre kayıtta korunur.
Kaynağın doğruluğu/geçerliliği toplayıcının sorumluluğudur; metadata yaşı saklanır,
güncel olduğu varsayılmaz.

[Binance USD-M resmi PRICE_FILTER tanımı](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition)
uyarınca `Decimal` ile `(fiyat - minPrice) % tickSize = 0` ızgarası kullanılır.
Min/max fiyat sınırları ayrıca kontrol edilir. Sıfır/devre dışı tick, eksik veri,
yanlış sembol/kaynak veya karar anından sonra alınmış metadata doğrulanmış
fiyat adımı gibi sunulmaz. Fiyat adımı tahmin edilmez.

| Türetilmiş fiyat | Yuvarlama |
|---|---|
| Bölge alt / üst kenarı | Aşağı / yukarı; bant dışa genişler. |
| LONG giriş / SHORT giriş | Bölgenin yuvarlanmış üst / alt kenarı. |
| LONG stop / SHORT stop | Aşağı / yukarı; risk küçültülmez. |
| LONG hedef / SHORT hedef | Aşağı / yukarı; ödül büyütülmez. Hedef bölgesinin aynı yakın kenarı kullanılır. |

Yuvarlanmış fiyatlarla gösterim R'si yeniden hesaplanıp kayda alınır; ham R ve
fiyatlar delil olarak korunur. Yuvarlama hedefi kırılan bantla çakıştırır veya
1 ATR mesafe şartını bozarsa görünür TP doğrulanmaz; ham aday silinmez.
Grid sınırı dışındaki fiyatlar sınıra sıkıştırılarak uydurulmaz.
Yuvarlanmış kenarın dışına kapanış yoksa ayrıca açıklanır.

Bu ortamdan doğrudan `exchangeInfo` isteği **HTTP 451** döndürdü. Ağ engeli
aşılmadı. Tarihsel örneklerin karar anına ait PRICE_FILTER de elde yok.
Bu nedenle önizlemelerde **tick size doğrulanmadı**, fiyatlar `≈` işaretli
ham analiz referansıdır. Sekiz anlamlı hanelik metin yaklaşık gösterimdir;
JSON'da tam ham değer vardır. **Canlı tick entegrasyonu doğrulanmadı.**

## Gerçek veriyle önizleme

`step3-preview/manifest.json` iki önceden mevcut BTCUSDT arşiv snapshot'ını,
çıktılarını ve kayıt hash'lerini listeler. Uzun/kısa örneklerin tamamı kullanıldı;
sonraki fiyat sonucuna göre seçim yapılmadı.

- Kaynak: Binance USD-M Ağustos 2026, 1m aylık arşivi.
- ZIP'in SHA256 değeri arşiv CHECKSUM dosyasıyla doğrulandı.
- Örneklerdeki bütün 15m/1h/4h OHLC ve hacim mumları bu arşivden tekrar
  toplanan mumlarla birebir karşılaştırıldı.
- Yeni hesaplar yalnız snapshot'ın kapanmış mumlarını gördü. Sonraki fiyat
  kullanımı veya kârlılık testi yok.
- İki yeni hesap da **R/R YETERSİZ**. Grafikler canlı öneri değil, açıkça
  **ARŞİV ÖNİZLEMESİ** olarak etiketli.
- Sabit geçmiş pencerelerinden üretilen görsel örneklerdir; artımlı geçmiş
  değişmezliğini veya 100 bağımsız örneği kanıtlamaz. Kısa TF geçmişi olan
  örneklerde `zone_window_complete=False` delilde korunur.

Tekrar üretim (arşiv önceden cache'te olmalıdır; indirme yapılmaz):

```bash
python -B -m research.technical_observer_v1.build_step3_previews \
  --cache /path/to/context-archive \
  --output research/technical_observer_v1/step3-preview
```

## Kontroller ve durma noktası

`python -B -m unittest -q tests.test_technical_observer_definitions tests.test_technical_observer_presentation`

**28 geliştirme kontrolü geçti: 15 tanım + 13 sunum/kayıt kontrolü.**
0,05 gibi ondalık hane sayısıyla çözülemeyen tick ve minPrice başlangıcı,
LONG/SHORT kenar paylaşımı, eksik/gelecek/yanlış metadata, konservatif yuvarlama,
R hesabı, eksik hedef, bozuk geometri, veri eşleşmesi, geçmişin üzerine yazmama,
eksik/düşük R adaylarının tutulması ve JSON–metin–PNG eşleşmesi kontrol edildi.
İki gerçek arşiv PNG'si ayrıca görsel olarak incelendi.

Sentetik kontrol verileri açıkça test fixture'ıdır; gerçek piyasa diye sunulmadı.
**MAGMAUSDT 23/10 17:00 ham karar kaydı hâlâ yok; MAGMA regresyonu doğrulanmadı.**
Bu rapor 4. adımın kapsamlı regresyon kabulü değildir.

Kullanıcının adım sonu onay kuralı gereği burada duruldu. Sonraki onay 4. adım
test/regresyon çalışmasını açar. Canlı gölge toplama, bütün reddedilen tarama
adaylarını kaydetme, bağımsız örnek sayımı, depolama yönetimi ve üretim bağlantısı
5. adımın kapsamındadır; bu sürüm bunları başlatmaz.
