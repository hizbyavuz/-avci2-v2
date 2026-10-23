# Dakika planı kapsam denetimi — 22 Ekim 2026

Swing V1'in 625 Ağustos olayının tamamı paydada tutulur. Elimizde sekiz
sözleşmenin dakika arşivi vardır; bunlar daha önceki sekiz olaylık hash örneği
için indirilmiştir. O örneğin yedi işlem sonucu zaten görülmüştür. Bu çalışma
bağımsız test veya tüm piyasayı temsil eden rastgele işlem örneği değildir.

Karar/stop/çıkış kuralları değiştirilmez. Her olay için karar zamanından sonraki
ilk TAM dakikanın açılışı giriş referansıdır. Fiyatın girişte koşulları koruması
gerekir. Açılış verisi yoksa sonraki mumdan giriş uydurulmaz. Koşullar bozulmuşsa
ENTRY_INVALID kaydı tutulur; bu işlem açılmamıştır, kayıp sayılmaz.

Mevcut `long_short_swing_outcomes` ile stop önce, gap açılışı, +2R sonrası dört
saatlik takip ve 168 saatlik süre aynı şekilde uygulanır. Ay sınırında diğer ayın
dosyası istenir; eksik dosya başarılı veya başarısız işlem yerine geçmez. İlk
veri boşluğunda yol durur; boşluktan sonra gerçekleşmiş gibi çıkış hesaplanmaz.
Stopla kapanmış planın daha sonraki ham yolunda boşluk varsa plan sonucu korunur.

Birincil özet yalnız giriş geçerli ve 168 saat yolu tam olan küçük teknik
örneği içerir. Bütün adayların durum sayıları bunun yanında gösterilir. Daha
erken kapanmış fakat ham yolu eksik kayıtlar varsa ayrıca sayılır; kısa vadeli
kapanışlardan bütün stratejinin başarı oranı çıkarılmaz. 24/72/168 saat özetleri
açık ve kapanmış planları birlikte içerir; kapsam her ufuk için ayrıca sayılır.

Sonuç birimleri: kaldıraçsız ilk işlem büyüklüğüne göre yüzde ve R (başlangıçtaki
stop mesafesinin katı). Varsayılan toplam ücret/kayma %0,30; %0,60 ve %1,00
yalnız ek maliyet hassasiyetidir, gerçek gerçekleşme tahmini değildir. Funding
(vadeli pozisyon taşıma ödemesi) kaydı ve doğrulanmış kapsamı yoksa tam net
sonuç bilinmiyor kalır. Ücret/kayma varsayımı düşülmüş sonuç tam net diye sunulmaz.

Ham %10/%30 erişimi, işlem içindeki kesin erişim ve stop sonrası ilk erişim
ayrı gösterilir. Aynı stop dakikasındaki belirsizlik `AMBIGUOUS_EXIT_MINUTE` olur;
stop sonrası kesin erişim sayılmaz. Önceki ham yol ölçümü işlem kazancına katılmaz.

611 gibi veri eksikliği sayıları girdi kapsamından gelir, kötü sonuçlar gizlenmez.
Tamlık koşulu örneği yanlı yapabilir; tam ve eksik sonuçlar karıştırılmaz. Eksik
girişte gözlenen fiyat ve başlangıç stopuna göre varsayımsal stop kaybı ayrıca
kaydedilebilir; bu gerçek giriş, gerçekleşmiş kayıp veya matematiksel en kötü
sonuç değildir. Birleşik ana başarı oranına dönüştürülmez.

Çalışma yalnız yerel arşivleri okur. Her gerekli sembol/ay dosyası, kaynak adresi,
varlık durumu ve SHA-256 özeti; her adayın dakika ve funding zaman aralığı;
birleştirilmiş eksik veri istekleri kaydedilir. Ağ isteği, Telegram ve otomatik
emir yok. Tarihsel coin üyeliği doğrulanana kadar kapsam sınıflandırılmamış USDT
olarak kalır. Mevcut sekiz sözleşme üzerinden bütün motora kârlılık hükmü yoktur.
