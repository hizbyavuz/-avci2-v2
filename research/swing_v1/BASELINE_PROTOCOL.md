# Basit kıyas sözleşmesi — 22 Ekim 2026

Bu ek sözleşme Ağustos Swing ve eşleştirilmiş kontrol sonuçları görüldükten,
momentum kıyası hesaplanmadan önce kaydedildi. Bağımsız ön kayıt veya yeni bir
doğrulama dönemi değildir. İlk PREREGISTRATION.md ve Swing eşikleri değişmez.

## Kıyas

* Kaynak yalnız mevcut Ağustos 2026 saatlik dosyalar; ağ isteği yapılmaz. Önceki
  kapsama manifestindeki her OK dosyanın SHA-256 değeri doğrulanır. Eksik veya
  değişmiş dosya varsa çalışma durur. 833 dosyalık sınıflandırılmamış USDT evreni
  korunur; kripto dışı sözleşmeler içerdiği için canlı coin doğrulaması değildir.
* Adaylar o anda bilinen 169 kesintisiz, geçerli saat; eksiksiz quote/taker
  alanları ve son 24 saatte en az 2M USDT hacimle belirlenir. Gelecek veri aday
  seçimine girmez. Basit kıyasa kırılım, alış baskısı, stop veya geç kalma kapısı
  eklenmez. Sayısal eşikler sonuca bakılarak ayarlanmaz.
* Son kapanış / 24 saat önceki kapanış - 1 sıralanır. LONG en yüksek beş,
  SHORT en düşük beş; eşitlikte sembol adı. Aynı anda tüm sembollerden aynı BTC
  getirisini çıkarmak sıralamayı değiştirmez; beta düzeltmesi yapılmış sayılmaz.
* Birinci kıyas: Swing'in sinyal verdiği aynı saat/yön için bu beş adayın ham
  sonraki 168 saatlik yolunu hesapla. Swing adayı basit ilk beşteyse dışlama.
  Sonraki saatin açılışı ortak tarama referansıdır; gerçek giriş fiyatı değildir.
* %30 yönlü ham erişim birincil keşif ölçüsüdür. 7 gün sonundaki yönlü kapanış,
  en büyük lehte/ters hareket ve başlangıçtaki 7 günlük dip/tepe uzaklığı da
  gösterilir. Bunlar stop, ücret, kayma, funding veya kaldıraç içermez; kâr,
  kazanma oranı, uygulanabilir sepet getirisi veya portföy performansı değildir.
* Her saatte Swing olaylarının ortalama erişimi ile beş basit adayın ortalama
  erişimi karşılaştırılır; saatlere eşit ağırlık verilir. Yoğun saatler çok sayıda
  Swing mesajı var diye daha ağır sayılmaz. Olay ağırlıklı görünüm ayrıca verilir.
  Saatlik Swing ve kontrol tarafları aynı eşleştirilmiş kayıtlarla karşılaştırılır.
* Tam 168 saat sonrası olmayan Swing olayları ve tamamlanmamış basit bileşenler
  sayılır. Basit ilk beş yalnız geçmişle seçilir; geleceği eksik diye altıncıyla
  değiştirilmez. Beş bileşenden biri eksikse o saat birincil kıyasa giremez.
  Swing tarafında kısmi olgunluk olan saat de tam-saat kıyasından çıkarılır.
* İkinci, ayrı görünüm: kapanışı UTC 00/04/08/12/16/20'ye denk gelen saatlerde
  aynı ilk beşi seç. Her yeniden seçim kaydedilir; tekrarlar, farklı mesaj
  yoğunluğu ve farklı takvim açıkça sayılır. Bu görünüm Swing ile eşit işlem
  sayılı bir portföy kıyası değildir.

## Belirsizlik ve yorum

UTC sinyal günlerindeki eşleşmiş saatlerin toplamları üzerinde 7 günlük hareketli
bloklarla, 2.000 tekrar, sabit tohum 20261022 ile betimleyici %90 aralık hesapla.
7 günlük fiyat yolları örtüşür; aynı coin tekrar eder. Bir aylık veri ve az sayıda
blok güvenilir bağımsızlık sağlamaz. Bu aralık istatistiksel başarı kapısı değildir.
BTC'nin o andaki 24 saat getirisi pozitif / negatif / sıfır olarak ayrıca gösterilir;
gelecekteki piyasa rejimi kullanılmaz. Veri yoksa UNKNOWN sayılır.

Sinyal sayısı, farklı sembol/saat/gün, başlangıçtaki hareket ve erişim farkı ayrı
raporlanır. Sadece sonraki %30 erişimine bakarak en erken yakalama, doğru stop veya
kârlılık hükmü verilmez. Olay başlangıcı ve kaçırılan olayların etiketlenmesi ayrıca
gereklidir. Yeni eşik araması yapılmaz. Eski MAJOR kıyası için tam evren 15 dakika
ve yeterli 4 saat geçmişi gerekir; elde olmayan çözünürlük uydurulmaz.
