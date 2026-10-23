# Veri tamamlandıktan sonra ilk tam plan sonucu

22 Ekim 2026. Ağustos adaylarının dakika verisi artık tam: 479/479 arşiv,
656.642.156 bayt, resmi SHA-256 kontrolleri başarılı. Önceden eksik olan 471
dosya alındı. Aynı 625 adaya aynı kurallar uygulandı; eşikler değiştirilmedi.

**Sonuç:** LONG'un Ağustos plan sonucu olumlu; fakat Eylül ham büyük hareket
taramasında kontrol grubuna üstünlük tekrarlanmadı. SHORT'un Ağustos plan sonucu
eksi. İki yönü de doğrulanmış bir canlı sinyal sistemi olarak sunmak için destek
yok. Funding eksik; evren hisse ve diğer kripto dışı sözleşmeleri de içeriyor.

## Ağustos: dakika verisiyle giriş ve çıkış

625 adayın 556'sı giriş koşullarını korudu; 69'unda giriş geçersizdi. Geçersiz
girişlerde işlem açıldığı varsayılmadı. Geçerli 556 planın tamamında 168 saatlik
dakika yolu tam; eksik giriş veya fiyat yolu kalmadı. Önceki 12 tamamlanmış
plan ve iki geçersiz giriş sonucu birebir aynı kaldı.

Aşağıdaki oranlar ilk işlem büyüklüğüne göre, kaldıraçsız varsayımsal plan
getirisidir. Toplam %0,30 ücret/kayma düşülmüştür; funding düşülmemiştir.

| Ölçü | LONG | SHORT |
| --- | ---: | ---: |
| Aday | 436 | 189 |
| Giriş geçersiz | 37 | 32 |
| Tam ölçülen plan | 399 | 157 |
| Artı / eksi sonuç | 217 / 182 | 44 / 113 |
| Artı sonuç oranı | %54,39 | %28,03 |
| Plan başına aritmetik ortalama | +%4,02 | −%1,35 |
| Medyan plan sonucu | +%1,61 | −%3,29 |
| Başlangıç riskinin katı olarak ortalama | +0,85 R | −0,44 R |
| Toplam artı / toplam eksi büyüklüğü | 2,47 | 0,55 |
| Ortanca tutma süresi | 41,35 saat | 62,90 saat |
| Ortanca başlangıç stop mesafesi | %5,90 | %3,95 |

Ücret/kayma varsayımı toplam %1,00'e çıkarıldığında ortalama LONG +%3,32,
SHORT −%2,05. Bu yalnız maliyet hassasiyetidir; gerçek gerçekleşebilir kayma
ölçülmüş değildir. Funding API'si HTTP 451 bölge kısıtı verdi; tam net sonuçlar
bilinmiyor olarak kalır.

### Stop sonrası hareketin ayrılması

399 LONG planının 87'sinde fiyat yedi günlük ham yolda en az %30 lehte ilerledi.
Bunların 47'sinde bu seviye plan açıkken kesin görüldü; **40'ında ilk %30 erişimi
çıkıştan sonraydı**. Bu 40 olay plan içinde %30 başarı sayılmadı. Bu oranların
hiçbiri doğrudan kazanma oranı değildir; bazı planlar daha düşük kârla kapanabilir.

24 saatlik ortalama (açık ve kapanmış planlar birlikte) LONG +%2,07,
SHORT −%0,74; 72 saatte LONG +%4,09, SHORT −%0,78. Sadece erken kapanan
işlemlere bakılarak payda daraltılmadı.

## Bu sonuç hangi sınırlar içinde geçerli?

- Ağustos daha önce görülmüş keşif dönemidir. Kurallar bu veri tamamlama sırasında
  değiştirilmedi; buna rağmen geçmiş dönem sonucu bağımsız ileri test değildir.
- Tarihsel katalog henüz coin/hisse/diğer olarak doğrulanmış değil. Sonuç yalnız
  geniş USDT sözleşme kataloğu içindir; canlı COIN evreninin performansı sayılmaz.
- 399 LONG, 23 sinyal gününde oluştu; bir günde 80 geçerli LONG var. 399 bağımsız
  deney değildir. SHORT 24 sinyal gününe yayılıyor. Rejim/genelleme hükmü yok.
- Bütün araştırma adayları ölçüldü. Günlük altı mesaj bütçesi, Telegram gecikmesi,
  aynı anda açık işlemler ve portföy riski bu getirilerde simüle edilmedi.
- Kontrol ve momentum için önceki karşılaştırma ham fiyat hareketiydi; aynı
  işlem planıyla tam maliyet sonrası kıyas henüz tamamlanmadı.

Canlıya deployment yapılmadı. Telegram kapalı; otomatik emir yok. Önceki kısmi
raporlar silinmedi: `MINUTE_FINDINGS.md` ve `MINUTE_AUDIT.json` eski sekiz arşivlik
örneği, bu dosya ise bütün Ağustos adaylarının tamamlanmış dakika ölçümünü anlatır.

Tam sonuç: `evidence/2026-08-full-minute-audit.json.gz`.
Okunabilir sayısal özet: `FULL_MINUTE_AUDIT.json`.
İndirme ve bütünlük kanıtları `evidence/` içindedir.

## Eylül: aynı kuralların ikinci ay taraması

900 sembollük aynı katalogda 869 saatlik arşiv resmi checksum ile doğrulandı.
31 eksikte arşiv ZIP adresine ayrıca HEAD isteği yapıldı; hepsi HTTP 404 verdi.
Ağ hatası veya durumu bilinmeyen dosya kalmadı. 467.748 sözleşme-saati
değerlendirildi; bunların 241.394'ü geçmiş/hacim açısından uygun paneldeydi.

Bu bölüm **saatlik ham fiyat yolu** ölçümüdür; stop/çıkış, ücret ve funding
sonrası plan getirisi değildir. Ayın ilk 169 saati ön kayda göre ısınmadır.

| Ölçü | LONG | SHORT |
| --- | ---: | ---: |
| Aday | 384 | 231 |
| Yedi günlük ham yolu tamamlanan | 319 | 175 |
| Ay içinde yolu tamamlanmayan / boşluklu | 65 | 56 |
| Ham lehte %30 erişimi | 38/319 (%11,91) | 3/175 (%1,71) |
| Eşleştirilmiş kontrolün ham %30 erişimi | 40/319 (%12,54) | 0/175 (%0) |
| Ortanca en büyük lehte ham hareket | %10,06 | %5,17 |
| Ortanca en büyük ters ham hareket | %6,31 | %9,88 |

Kontrol, aynı zamanda benzer hacim ve oynaklıktaki tetiklenmemiş sözleşmeden
sabit hash ile seçildi; gelecekte iyi giden sözleşme seçilmedi. Eylül LONG'unda
büyük hareket seçme üstünlüğü görülmedi. Bu, Eylül planlarının zarar ettiği
anlamına gelmez: o ayın tam dakika giriş/stop/çıkış ölçümü henüz yapılmadı.
SHORT tarafındaki üç büyük ham olay da tek başına işlem avantajı göstermez.

Bu sonuçlar görüldükten sonra eşik ayarlanmadı. İlerlemek için Eylül'ün tam
dakika planı, tarihsel coin sınıflandırması, funding ve aynı işlem ölçüsüyle
kontrol/momentum kıyası hâlâ gerekiyor. Üretim veri akışı ve eski 166 sembollük
kolektör sınırı da bu arşiv çalışmasıyla doğrulanmış veya düzeltilmiş olmadı.

Eylül özeti: `SEPTEMBER_SCREEN.json`.
Tam aday ve kontrol kayıtları: `evidence/2026-09-screen-complete.json.gz`.
