# 15 / 30 / 60 dakika LONG giriş karşılaştırması

**Sonuç:** Bu geliştirme örneğinde 15 veya 30 dakikalık girişe geçmenin genel üstünlüğü görülmedi. Saatlik referans hem pozitif kapanış oranında hem işlem başına ortalama ücret varsayımı sonrası sonuçta daha yüksek. Bu, saatliğin gelecekte kesin üstün olduğunun kanıtı değildir. Ana motor değiştirilmedi.

392 yerel, resmi checksum ile doğrulanmış sözleşme arşivi; giriş mumları 8–23 Ağustos 2026 UTC. Üç varyant aynı saatlik yapı/stop ve dört saatlik takip çıkışını kullanır. Seçim yalnız bilinen eski kazananlardan yapılmadı. Bütün uygun zamanlar tarandı. Aşağıdakiler Telegram mesajları veya gerçek hesap işlemleri değildir.

## Bütün varyantlar

| Giriş kapanışı | Aday | Geçersiz giriş | Tam ölçülen | Pozitif kapanış | Ortalama sonuç | Ortalama R | İşlem içi +%10 | İşlem içi +%20 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 15 dk | 507 | 71 | 436 | 241/436 (%55.28) | %+4.368 | +0.915R | 223 (%51.15) | 119 (%27.29) |
| 30 dk | 429 | 54 | 375 | 216/375 (%57.60) | %+4.129 | +0.825R | 188 (%50.13) | 101 (%26.93) |
| 60 dk | 349 | 31 | 318 | 190/318 (%59.75) | %+4.782 | +0.973R | 169 (%53.14) | 92 (%28.93) |

Sonuçlar başlangıç nominali üzerindedir; kaldıraç yoktur. %0,30 sabit toplam ücret/kayma varsayımı düşüldü; funding dahil değildir, tam net getiri bilinmiyor. R başlangıç stop mesafesine bölünmüş sonuçtur. Eşik erişimi kâr realizasyonu değildir. Geçersiz girişlerin oranı başarı paydasına dahil edilmedi, yukarıda ayrıca gösterildi. Başlayan 1.129 planın tamamında yedi günlük dakika yolu vardı; 156 aday girişte elendi.

15 dk: 44 kırılım korunmadı, 25 kovalama, iki aşırı uzama. 30 dk: 31 kırılım korunmadı, 23 kovalama. 60 dk: 13 kırılım korunmadı, 15 kovalama ve birer risk/uzama/engel reddi. Bunlar gönderilmiş işlemlerin kayıpları değil, giriş öncesi retlerdir.

## Küçük mum gerçekten ne kadar erkendi?

Aynı coin ve aynı teyit saatinde iki varyantın da geçerli olayı olduğu altküme:

| Kıyas | Eşleşen çift | Medyan erkenlik | Ortalama giriş fiyatı farkı | Ortalama R farkı |
|---|---:|---:|---:|---:|
| 15 dk − 60 dk | 161 | 15 dk | %-0.0627 | +0.0311R |
| 30 dk − 60 dk | 191 | 0 dk | %-0.0129 | +0.0345R |

Negatif fiyat farkı daha düşük LONG girişi demektir. Ortak saat altkümesinde küçük bir ortalama avantaj olsa da bu eşleme yalnız her iki varyantta sinyal olan örnekleri içerir. 15 dk grubundaki 275 ve 30 dk grubundaki 184 tam plan bu altkümeye girmez. Ortak örneklerdeki iyileşme bütün sinyal akışına taşınamaz. 30 dk medyan erkenliğinin sıfır olması, eşleşmelerin en az yarısının saat sonunda da oluşmasıyla uyumludur.

## Hareket ve tutuş

| Giriş | Medyan tutuş | Medyan ilk stop mesafesi | +%10 görüp zararla kapanan | +%20 görüp zararla kapanan | Bilinen üst engel |
|---|---:|---:|---:|---:|---:|
| 15 dk | 40.08 saat | %5.42 | 21 | 0 | 16/436 |
| 30 dk | 40.48 saat | %5.56 | 16 | 0 | 11/375 |
| 60 dk | 38.63 saat | %5.62 | 16 | 0 | 7/318 |

15 dakika giriş, 15 dakika tutuş anlamına gelmedi. Stop küçük muma göre daraltılmadı. Üst engelin bilinmediği örnekler çok fazla; UNKNOWN, direnç yok demek değildir. Daha uzun yapısal engel geçmişi ihtiyacı devam ediyor.

## Sınırlar ve karar

- Ağustos daha önce görülmüş geliştirme verisidir; yeni bağımsız doğrulama değildir. 16 günlük pencere ve birlikte hareket eden coinler bağımsız büyük örnek sayılmaz. Üç varyantın aday kümeleri farklıdır; oran farkı tek başına istatistiksel üstünlük değildir.
- Dakika arşivi kapsamı önceki araştırmalara bağlı seçilmiş bir altkümedir. Tarihsel kripto dışı sözleşmeler ayrıştırılamadı. Günlük yayın kotası, eşzamanlı portföy riski ve fon kapasitesi uygulanmadı. Bu sonuçlar kullanıcıya ulaşacak altı günlük mesajın oranı değildir.
- Eksik veya bozuk dakika içeren toplama mumları kullanılmadı; kaynak bazında ret sayıları SUMMARY.json içinde korunur. Tam sonuç için eksik kayıt sessizce yok sayılmaz.
- Ortak 60 dk referansı eski dondurulmuş V1 ile aynı değildir: ATR/24 saat hacim/engel teyit saatinden önce sabitlenmiştir. Bu yüzden eski 399 planın %54,4 oranıyla doğrudan karşılaştırılmaz.
- Eşikler sonuç görüldükten sonra değiştirilmedi. Sekiz yazılım testi ve 1.129 kapanışta getiri/R yeniden hesaplama kontrolleri geçti; bunlar strateji başarısı testi değildir.
- **Karar: mevcut saatlik ana kurallar korunur; 15/30 dk otomatik olarak devreye alınmaz.** Bu sınırlı deneyde daha iyi bir sinyal akışı üretmediler. SHORT değişmedi. Yeni bir giriş fikri ayrı sürüm ve ileri gözlem gerektirir.
- Bu çalışma yalnız çevrimdışı araştırmadır. Railway veya Telegram başlatılmadı; önceki iptal edilen deployment tekrar denenmedi.

Yöntem sonuçtan önce `1fe98504804a03d1386a0fc25e96cd4b176e1579` commit’inde sabitlendi. FROZEN.json kaynakları ve kod hash’lerini; OUTCOMES.json.gz bütün aday ve sonuçları; SUMMARY.json bütün özet ve retleri; VALIDATION.json doğrulamayı taşır.
