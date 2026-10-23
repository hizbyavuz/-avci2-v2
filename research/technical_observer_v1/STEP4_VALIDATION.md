# 4. adım — test ve regresyon raporu

23.10.2026. Taban commit: `dfa6e75b1de4a9d8acdcba9242dfdbcb630abfcf`.

**Sonuç: 45 kontrol geçti, 0 başarısız/hata, MAGMA'nın 3 gerçek-vaka kontrolü
doğrulanamadı. 4. adımın tam kabulü verilmedi.** Makine tarafından okunabilir
sonuç: `STEP4_RESULTS.json`, durum `INCOMPLETE_REAL_CASE_UNVERIFIED`.

Canlı kod, engine/config, eşikler, güvenlik kapıları, Telegram ve üretim
DB'si değiştirilmedi. Yalnız testler, doğrulama çalıştırıcısı ve bu rapor eklendi.
Gözlemsel etiketler işleme engel yapılmadı. Deploy veya gerçek emir yok.

## Gerçekten sınananlar

| Kontrol | Kanıt ve sonuç |
|---|---|
| Bilinen swing'ler | Elle tanımlı sentetik yolun beklenen tepeleri 24/40/56/72, dipleri 32/48/64. Fiyat uçları ve sağdaki üçüncü mumun kapanış zamanı eşleşti. Daha erken onay oluşmadı. |
| Geçmişin değişmezliği | 8 sabit rastgele tohum × 3 TF × 180 geçmiş uzunluğu = **4.320 geçmiş durumu**. Her yeni mumda önceki onaylı swing'lerin bütün alanları aynı kaldı. |
| Sınır durumları | Eşit tepe, aynı mumda hem tepe hem dip, 1 ATR mesafesinin tam sınırı, ATR başlangıcı, sıfır ATR ve yetersiz yapı geçmişi kontrol edildi. |
| Kapanmış veri | Gelecekte ulaşmış veri, açık mum, tekrar eden mum, NaN fiyat ve negatif hacim yeni setup üretmedi. |
| Kırılım kalitesi | Tam %50 gövde ve 1,5 hacim katsayısı kabul edildi; hemen altı zayıf kırılım oldu. Medyan, mevcut mum hariç önceki 20 mumdan geldi. Zayıf kırılım gözlemden silinmedi. |
| Fitil / tekrar | Yalnız fitille geçen, kenara eşit kapanan ve daha önce zaten kırılmış seviyeler yeni ilk kırılım sayılmadı. |
| Karar zamanı | 30 sabit sentetik veri setindeki setup'larda bölge ve stop/target dayanakları tetik mumundan önce onaylıydı. Sonraki aşırı mumlar saklanmış gözlemi değiştirmedi. |
| Stop | Sentetik eski range tepesi yerine en yakın onaylı karşı swing seçildi. Tam 3 ATR genişlik etiketi almadı; 3 ATR üzeri gözlemsel etiket aldı. |
| Hedef | Kırılan bölgenin kendisi, çakışan/kenarı eşit bölge ve 1 ATR'den yakın engel hedef seçilmedi. Tam 1 ATR mesafesi kabul edildi. Uygun bölge yoksa hedef üretilmedi. |
| LONG/SHORT aynası | **30 sabit sentetik veri seti ve üç TF** üzerinde swing zamanları/türleri, ATR, yapı yönü, bölge sınırları/rolleri/güçleri, kırılım kalitesi, giriş/stop/hedef ve GEÇ nedenleri aynalandı. Test boş setup/boş hedefle geçemez; gerçekten oluşan setup ve hedefler var. |
| Tick aynası | **20 sentetik veri setinde**, ayna ekseniyle uyumlu 0,125 fiyat adımı üzerinde giriş/SL/TP ve bandın konservatif yuvarlaması karşılıklı eşleşti. |
| Gerçek arşiv tekrar üretimi | 3. adımdaki iki checksum-kontrollü BTCUSDT arşiv kaydı kendi kapanmış girdilerinden yeniden üretildi. Tüm JSON nesnesi ve hash birebir eşleşti. Tick metadata yokluğu korunuyor. |
| Önceki kontroller | Önceki 15 tanım + 13 sunum/kayıt kontrolü de yeniden geçti. Yeni dosyada 17 geçen kontrol ve 3 doğrulanamayan MAGMA kontrolü var. Toplam 48 test / 45 geçen. |

Sentetik veriler açıkça test girdisidir; piyasa verisi veya MAGMA yerine
kullanılmadı. 4.320 geçmiş durumu ve 30 aynalama seti **bağımsız işlem örneği
değildir**. 5. adımın 100 bağımsız örneğine sayılan kayıt: **0**.
Bu kontroller matematik/uygulama tutarlılığını sınar; kârlılık veya sinyal
kalitesini kanıtlamaz.

Yüzde maliyetli R, giriş fiyatına bölündüğü için fiyat eksenini aynalayınca
sayısal olarak eşit olmak zorunda değildir. Her yönün R'si kendi girişine göre
`(ödül yüzdesi − 0,30) / (risk yüzdesi + 0,30)` formülüyle ayrıca kontrol edildi.

## MAGMA: ne bulundu, ne eksik?

Kullanıcının paylaştığı 23/10 17:00 Türkiye vakasında eski referanslar:
SHORT, kırılım **0.41855407**, stop **0.51757414**, ilk engel **0.41696471**,
son kapanış **0.41885**. Bunlar ekran/metin referanslarıdır; ham mum verisi değildir.

Salt okunur arama:

1. Çalışma dosyaları/JSON ve mevcut snapshot'larda bu olayın özgün kararı yok.
   Repo içindeki yerel SQLite veritabanında da MAGMA için `raw_klines` ve
   `signal_events` sayısı **0**. DB içeriğinin Git blob hash'i değişmedi.
2. Railway `long-short-motor`, production ortamının **13:55–14:05 UTC**
   loglarında MAGMA'nın veri toplama kapsamına girdiği ve 14:03 civarında
   1m/5m/15m/30m/1h/4h geçmiş kontrolü yapıldığı görüldü. `rows_added=0`,
   verinin içeride var olup olmadığını tek başına kanıtlamaz; ham mumlar logda yok.
3. 14:01:48 UTC sağlık kaydı `STRUCTURE_CONTEXT_V1_3_2026_10_23` sürümünde
   290 sembolün tarandığını gösteriyor; MAGMA'nın seviye nesnesini içermiyor.
4. Bu servisin dışarı açık domain'i yok. Kullanılabilir Railway MCP araçları
   log okuyabiliyor fakat volume'daki snapshot dosyasını indirme/SQL çalıştırma
   yeteneği sunmuyor. Yeni domain/servis/deploy açılmadı.

Okunan deployment: `ce5cb546-a5d3-47f0-a36b-0c4246ddd56c`.
[Railway proje bağlantısı](https://railway.com/project/ba2b84a7-22c6-4703-aefc-c6f95393c1b6?environmentId=5fc78d8b-4f54-4b50-867d-c544cbd74aaa)

**Bu yüzden “yeni motor MAGMA'da stopu şu fiyata koyardı” sonucu verilmedi.**
Stopun 0,5176'dan seçilmediği, hedefin bölge dışında kaldığı ve bandın kırılımı
içerdiği üç gerçek-vaka testi `skipped / DOĞRULANMADI` durumunda.
Genel sentetik kontrollerin geçmesi bu üç gerçek-vaka testini geçmiş saydırmaz.

Gerekli dosya, olayın özgün `assessment + frames + plan` JSON snapshot'ıdır.
Mevcut canlı sürümün kodunda beklenen konum:
`/app/.long-short-state/structure_context_v1_3/snapshots/<event_id>.json.gz`.
Bu yol koddan çıkarıldı; dosyanın gerçekten bulunduğu uzaktan doğrulanmadı.
Olay zamanı/stop/sembol/yön yanlışsa regresyon testi hata verir; başka bir olayı
MAGMA vakası yerine kabul etmez. Verilen vaka WATCH aşamasında olduğu için yeni
kurallarda o anda hiç SHORT kırılımı oluşmayabilir; böyle bir sonuç da seviye
kontrollerini otomatik geçmiş saydırmaz.

Özgün kayıt sağlandığında çalıştırma:

```bash
MAGMA_REGRESSION_SNAPSHOT=/path/to/original-event.json.gz \
  python -B -m research.technical_observer_v1.run_step4_validation \
  --output /path/to/new-validation-result.json
```

## Tekrar çalıştırma ve kabul sınırı

```bash
python -B -m research.technical_observer_v1.run_step4_validation \
  --output /path/to/new-validation-result.json
```

Çalıştırıcı çıkış kodları: **0** bütün istenen testler gerçekten geçti;
**1** başarısız test/hata; **2** en az bir doğrulanamayan/atlanmış test.
Bu çalışmanın çıkışı **2**: eksik gerçek-vaka verisi yeşil kabul sayılmıyor.
Sonuç JSON'unda test kimlikleri, atlanma nedenleri ve sınanan kodların SHA256
değerleri var. Eşik optimizasyonu veya yeni kural eklenmedi.

Swing değişmezliği hâlâ aynı geçmişin sonuna mum eklemek için geçerlidir.
Başlangıcı değişen hareketli pencereyi sıfırdan çalıştırmak aynı garanti değildir;
canlı bağlantı öncesi artımlı durum/geçmiş başlangıcı 5. adımda ele alınmalıdır.

Kullanıcının adım sonu durma kuralı gereği **5. adım başlatılmadı**.
Önce MAGMA'nın özgün kaydıyla bu üç doğrulamayı tamamlamak veya eksik gerçek-vaka
testini açık bir sınırlılık olarak kabul edip yalnız gölge ölçüme geçmek için
ayrı karar gerekir. Bu rapor üretime geçiş onayı değildir.
