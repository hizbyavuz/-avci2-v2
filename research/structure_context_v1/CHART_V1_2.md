# Çizim sürümü 1.2

23 Ekim 2026. Kullanıcının Telegram çizimlerini açıklayıcı teknik analizle
zenginleştirme isteği için hazırlanmıştır. Bu bir strateji başarısı iyileştirme
iddiası değildir. Karar eşikleri, stop veya hedef hesabı değiştirilmedi.

Eklenenler: formasyon/kurulum adı ve çift tepe/dip dayanakları; HH/HL/LH/LL;
önceden teyitli seviyenin ilk kapanış kırılımında BOS/CHoCH veya belirsiz
bağlamda BREAK; fitil geçişi ve geri kapanıştan türetilen süpürme işareti;
dokunulmamış üç mum FVG alanları; bir önceki ATR'den büyük gövdeli yapı
kırılımından önceki beş mum içindeki son ters renkli mumun tam aralığıyla
sınırlı OB adayı; EMA20/50, Wilder RSI14, MACD12/26/9 ve USDT hacmi.

BOS/CHoCH önceki teyitli dip/tepe yapısına göre sınıflanır. Bunlar evrensel
SMC tanımları değildir; kesin mekanik tanımlar annotations.py RULES alanında
kaydedilir. Süpürme gerçek bekleyen emirleri kanıtlamaz; OB adayı kurumsal
emir kanıtı değildir. FVG, sonraki fitil ilk dokunduğunda aktif listeden çıkar;
OB, uzak kenarının aksi yönde kapanışla geçilmesinde listeden çıkar.

Bu etiketler kapalı mumlardan karar anında hesaplanır, anlık görüntüye
kaydedilir ve grafikte aynı kayıttan çizilir. Yeni etiketlerin karara etkisi
NONE'dır. Fibonacci mevcut kararın aynı A/B noktalarını kullanır. Grafikte
okunabilirlik için en yakın dört FVG/OB ve son on ayrı yapı işareti bulunur;
tam liste kayıtta saklanır. Olay geçmişi son 60 mumla sınırlıdır.

Geçerli plan varsa sağda giriş referansı / stop / hedef renkli alanı vardır.
İzleme bantları giriş alanı diye gösterilmez. Uygun plan bulunmadığında grafik
ve mesaj başlığı İŞLEM PLANI YOK der. Sağdaki kutu varsayımsal referanstır,
gerçek gerçekleşmiş emir değildir.

Üçgen, kama, bayrak, omuz-baş-omuz, harmonik, Elliott, IFVG tespiti bu sürümde
yoktur. Tüm teknik analiz yöntemlerinin eklendiği iddia edilmez. Önceki
performans raporları eski sürüme aittir; bu ek etiketlerin ayrıca kârlılık
validasyonu yapılmamıştır. Eski kayıtlar korunur, yeni sürüm ayrı durum
dizinine yazar. Yeni sürümde olay kimliği değiştiğinden eski izleme fikirleri
bir kez yeniden bildirilebilir; mevcut günlük kota yeni veritabanıyla başlar.

## Kaynak ve uygulama tercihi ayrımı

- MACD12/26/9 hesabı: Fidelity teknik gösterge rehberi,
  https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd
- RSI14 Wilder ortalama yaklaşımı: Fidelity RSI rehberi,
  https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI
- Klasik çift tepe; önceki yükseliş, iki tepe, aradaki destek kırılımı: StockCharts ChartSchool,
  https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-patterns/double-top-reversal
- SMC ürün uygulaması örneği (evrensel standart değildir):
  https://www.luxalgo.com/library/indicator/smart-money-concepts-smc/

Bu motordaki 2-sol/2-sağ pivot, 1.5 ATR anlamlı hareket, 0.35 ATR benzer
tepe/dip toleransı, beş mum OB araması ve 1 ATR gövde koşulu bizim
araştırma tercihlerimizdir. Bu kaynakların dayattığı standartlar değildir.
15 dakikalık benzer dip/tepe etiketi klasik orta vadeli formasyonun trend,
hacim ve süre koşullarının tümünün sağlandığını iddia etmez; görsel bu yüzden
“yerel geometri adayı” der. İndikatör hesaplarının başlangıç tohumu ve
yeterli geçmişi platformlar arası küçük farklar yaratabilir.
