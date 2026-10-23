# Swing V1 bağımsız doğrulama — ön kayıt, 23 Ekim 2026

Karar kuralları ve parametreler değişmez. Kaynak commit: 04f6e96c02590ff77f49703ee4037c6d81763e50.
`long_short_swing.py` SHA256 c757e6f34bcd3c5549bfb02f31466f9288721cc77fc8c8697d9ba0bb80f6b5fe;
`long_short_swing_outcomes.py` SHA256 7798d1a2d6ffe66c367e517b4a7125a15e0d5faf7c2d868292fba136e3411feb.
CONFIG hash d899d56bf0dea0bc564c7907e4e79ad30b81e464de9021b6ecfd6d44e4577938.

## Ölçüm sözleşmesi

- 2020-01-01 00:00 UTC–2026-10-23 00:00 UTC. Son tamamlanmış UTC güne kadar yayımlanmış futures arşivi. Son yedi gündeki henüz kapanmayan planlar PENDING olarak korunur; zorla kapatılmaz.
- O saatte işlem gören tüm kripto USDT-M perpetual sözleşmeleri. Aylık ilk50 veya başka sıralama filtresi yok. Likiditeyi orijinal24h≥2M koşulu belirler. Arşiv kataloğu delist sembolleri içerir. Mevcut TRADING durumu seçimde kullanılmaz. Ürün kimliği COIN/PERPETUAL, endeks/hisse/emtia dışı; mevcut ürün metadata'sı yalnız kimlik için kullanılır, geçmiş varlığı arşiv kanıtından gelir. Tanımlanamayan ürünler ve seçime etkileri raporlanır.
- Sinyal fonksiyonu orijinal `assess`; yalnız LONG READY. Orijinal 6 saatlik episode reset korunur. Kripto dışı ürünlerden yeni sinyal oluşturulmaz; reset için geçmiş kapanışlar izlenir. Orijinal episode kuralı korunur; ayrı bir aynı-sembolde-tek-pozisyon filtresi eklenmez. Her yeni episode bir risk yuvası tüketir.
- Kullanıcının düzeltmesiyle orijinal giriş korunur: saatlik sınırdan sonraki ilk tam dakikanın açılışı (+1 dakika). Orijinal `geometry` girişte tekrar uygulanır. Reddedilen girişler kayıtta kalır. Stop/ATR/+2R/4h takip/168h kuralları aynıdır. ATR Wilder değildir: orijinal kodun son 14 true range aritmetik ortalamasıdır.
- +2R kâr hedefi değildir. Yeni stop yalnız tamamlanan UTC 4h bloğundan sonra, sonraki mumda geçerlidir. İşlemden önce başlayan kısmi 4h blok kullanılmaz. İlk ATR tamponu sabittir.
- Saatlik replay, orijinal dakika replay'inin eşdeğer toplulaştırmasıdır. Stopa değen saat 1m ile çözülür; dakika eksikse mevcut stop önce, gap daha kötüyse gap kabul edilir. Gelecek blok dibiyle aynı blok içi çıkış yapılamaz. Funding zaman belirsizliğinde maliyet alınır, belirsiz kredi verilmez. Eksik 1h yolu ölçülmüş sonuç sayılmaz.
- Sınırsız ve en fazla 5 pozisyon ayrı simüle edilir. Aynı saatte adaylar sinyalde bilinen son 24h USDT hacmiyle sıralanır; eşitlik sembolle çözülür. Sinyal seçimi gelecekteki çıkış/getiriye bakmaz. Günlük bildirim bütçesi uygulanmaz: bu talep edilen portföy deneyidir, Telegram teslim simülasyonu değildir.
- Masraf %0,30 ve aynı işlemlerde %0,60 ile %1,00. Gerçek funding oranı arşivden, yayımlanmamış günlerde resmi REST'ten. Sabit adet için funding/getiri yüzdesi: 100 × rate × settlement mark / giriş. REST settlement mark varsa önceliklidir; yoksa resmi markPriceKlines'ın funding saatindeki açılışı kullanılır ve kaynak sayısı açıklanır. Eksik rate/mark sıfır sayılmaz. Funding anındaki belirsiz giriş/stop için olumlu kredi varsayılmaz.

## Kontroller ve istatistik

Sabit tohum `SWING_V1_INDEPENDENT_20261023`. Her sinyalin aynı saatinde, dolayısıyla aynı gününde:
K1, o saatte işlem gören kripto evreninden kendi sembolü ve aynı anda READY LONG olanlar hariç eşit olasılıklı sabit SHA256 çekilişidir. Sonuç/veri bulunabilirliğine göre başka coin seçilmez.
K2 BTCUSDT'dir. İkisine sinyalin yüzde stop uzaklığı ve yüzde ATR tamponu taşınır; kendi coinlerinin 4h blok dipleriyle aynı çıkış uygulanır. Kontrol için kırılım/giriş geometry filtresi konmaz, çünkü sinyalsiz LONG istenir.
Kontroller eşleştirilmiş varsayımsal işlemlerdir; aynı BTC/coin pozisyonları çakışabilir. Aynı strateji seçilmiş kümesine karşı kıyaslanırlar; bağımsız kontrol portföyü diye sunulmazlar.

Birincil karşılaştırma eşleşmiş net R farkıdır. Aynı gün ve örtüşen yedi günlük yollar bağımsız işlem sayılmaz. Önce her UTC sinyal günü içindeki eşleşmiş farkların ortalaması alınır; günler eşit ağırlıktadır. Gün bazlı standart hata günlük ortalamaların örnek standart sapması / sqrt(gün sayısı). Ayrı günler de yedi günlük tutma nedeniyle bağımlı olabilir; ek olarak 7 gecikmeli Newey-West standart hata raporlanır, başarı kararında kullanıcı tarafından istenen gün bazlı SE kullanılır. Anlamlılık: ortalama fark > 2×SE. Ayrıca net getiri yüzdesi farkı ve SE raporlanır. Kayıp eşler açıklanır; eksik finansal veri varken kesin GEÇTİ denmez.

Başarı, limitli ana kümede: n≥200; ortalama net R>0; %0,60'da>0; net R toplamına göre en iyi üç coin çıkarılınca>0; K1/K2 farkı>2SE; 2020–2026 kapsanan takvim yıllarının yarısından fazlasında toplam R>0. Herhangi biri sağlanmazsa BAŞARISIZ. Tam veri olmadan sağlandı iddiası YOK. En iyi 10 işlem çıkarılması ek rapordur. Ağustos eski sınıflandırılmamış900katalog deneyi ayrıca birebir yeniden üretilir; kripto evreniyle tekrar ve5limitli tekrar ayrı verilir. Ağustos 2026 geliştirme ayı olarak açıkça işaretlenir; bu ayın tekrarı bağımsız kanıt değildir. Bu ay hariç sonuç ayrıca verilir.

Parametre denemesi: 0. Canlı, Telegram ve otomatik emir: kapalı. Eski dosyalar/DB değişmez. Sonuç tamamlandıktan sonra kaynak/evren mührüyle tekrar finansal koşu engellenir.

23 Ekim kullanıcı düzeltmeleri sonuç öncesi uygulanmıştır: tüm kripto evreni, +1dakika giriş, gün bazlı çıkarım, limit nedeniyle atlananların ayrıca sonucu, eski Ağustos kohortunun birebir replikasyonu, %1 maliyet stresi. Eski evren/giriş artık yeni protokolle karşılaştırmada tek başına hata sayılmaz; tam net başarı iddiasının eksikleri (funding/portföy/bağımsız üstünlük) korunur.
