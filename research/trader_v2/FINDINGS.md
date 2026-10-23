# Mevcut motor üzerinde türev verisi deneyi — 22 Ekim 2026

**Sonuç: Bu değişiklikler güvenilir, iki yönlü bir canlı motoru henüz doğrulamıyor.**
OI, funding ve akışı eklemek mevcut SHORT seçiminin negatif sonucunu düzeltmedi.
LONG'da ek koşulların risk başına getiriyi artırdığı gösterilmedi. Hiçbir eşik
istenen %60, %70 veya %100 başarı oranına ulaşmak için değiştirilmedi.

## Ne yapıldı?

Mevcut SWING V1 adayları, saatlik yapı stopu ve çıkış kuralları korundu.
MAJOR'ın eski puanlama zinciri tekrar etkinleştirilmedi. Yeni araştırma bileşeni,
aynı adayları dört önceden sabitlenmiş koşul grubundan geçiriyor:

- BASE: mevcut V1 adayı.
- STRUCTURE: fiyat yapısı ve BTC'ye göre beta düzeltilmiş güç uyumu.
- PARTICIPATION: bunlara gerçekleşmiş alış/satış hacmi ve 1/4 saatlik OI katılımı.
- CONTEXT: bunlara funding maliyet senaryosu ve hesap yoğunluğu koşulu.

OI tek başına LONG/SHORT yönünü söylemez. Hesap oranı pozisyon büyüklüğü oranı
değildir. Funding tahmini ile gerçekleşmiş ödeme ayrı tutulur. Eksik veri olumlu
oy veya sıfır maliyet sayılmaz. Kurallar `PROTOCOL.md` ile veri birleşiminden önce
kaydedildi; başarısız varyantlar da raporda duruyor.

Resmi Binance arşivinden 3.176 dosya SHA-256 ile doğrulandı. Ağustos ve Eylül'deki
1.240 adayın tamamına yalnız karar anında erişilebilir olduğu varsayılan geçmiş
veriler eklendi. Arşivden varsayılan yayın gecikmesi, gerçek canlı gecikmenin
ölçümü değildir. Bu aylar daha önce incelendiği için bağımsız doğrulama sayılmaz.

## Tam Ağustos işlem ölçümü: funding hariç

625 adayın 556'sında geçerli giriş ve tam dakika yolu var; 69 giriş geçersiz.
Aşağıdaki getiri toplam %0,30 ücret/kayma varsayımı içerir, funding içermez.
Artıda kapanan plan oranı gösterilir; yön tahmini veya hedefe ulaşma oranı değildir.
Bir R, giriş anındaki fiyat-stop mesafesidir. Satırlar farklı seçilmiş altkümelerdir.

| Koşul grubu | LONG plan | Artıda | Ortalama R | SHORT plan | Artıda | Ortalama R |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 399 | %54,39 | +0,846 | 157 | %28,03 | −0,441 |
| STRUCTURE | 333 | %52,55 | +0,713 | 130 | %28,46 | −0,442 |
| PARTICIPATION | 142 | %50,00 | +0,735 | 40 | %25,00 | −0,764 |
| CONTEXT | 99 | %55,56 | +0,793 | 37 | %27,03 | −0,508 |

CONTEXT, LONG'da artıda kapanma oranını biraz yükseltirken ortalama R'yi yükseltmedi.
Bu farkların istatistiksel üstünlük olduğu ileri sürülmüyor. SHORT bütün gruplarda
negatif. Daha fazla veri kullanmak, yanlış işlem seçimini kendiliğinden düzeltmiyor.

Eylül'de 615 aday var, fakat bu deneyde tam dakika çıkışlı plan sonucu yok.
Ham %30 erişimi LONG için BASE 38/319, CONTEXT 12/96; SHORT için BASE 3/175,
CONTEXT 2/50. Bunlar stop sonrası hareketi de içerebilen ham gözlemlerdir;
işlem kârı veya bu tablonun ikinci dönem doğrulaması olarak kullanılmaz.

## Gerçekleşmiş funding: kapsamı açık maliyet aralıkları

556 geçerli planın gerçek elde tutma aralığı için 816 funding/mark-price arşivi
istendi. 666 doğrulandı; bir dosyanın CHECKSUM adresi 404 verdi, ZIP yokluğu
ayrıca doğrulanamadı. Son 149 istek ağ geçidinin `403 Forbidden` hatasıyla bitti.
Başka alan adı veya proxy denenmedi. Eski indiricide sarılmış CONNECT hatasının
durdurma bayrağını tetiklemediği fark edildi ve düzeltildi; yeni deneme yapılmadı.

Daha önce doğrulanmış 59 funding dosyası yerel önbellekten, URL ve hash eşleşmesiyle
geri kazanıldı; ağ isteği yapılmadı. Böylece 725 doğrulanmış dosya, 90 erişim hatası
ve bir CHECKSUM 404 kaydı kaldı. İlk indirme raporu değiştirilmedi.

Eldeki dosyalarla çevrimdışı, açıkça `--allow-partial` modunda hesaplandı:
495 planın funding dahil sonucu aralık olarak ölçüldü; 61 plan bilinmiyor kaldı
(59 kaynak eksik, 2 ödeme aralığı boşluğu/değişikliği çözülemedi). 69 geçersiz
giriş ayrıca korundu. Eksikler paydadan silinmedi; altküme ortalamaları koşulludur.

| Grup | Funding ölçülen | Bilinmeyen geçerli plan | Artıda | Ortalama R aralığı |
|---|---:|---:|---:|---:|
| LONG BASE | 363 | 36 | 207 | +0,935453…+0,935515 |
| LONG CONTEXT | 89 | 10 | 52 | +0,890389…+0,890437 |
| SHORT BASE | 132 | 25 | 41 | −0,298814…−0,298776 |
| SHORT CONTEXT | 34 | 3 | 9 | −0,515234…−0,515188 |

Ödeme anının tam mark fiyatı yok: o dakikanın düşük/yüksek mark fiyatından
maliyet aralığı hesaplandı. Stop dakikasındaki ödeme ve eşzamanlı giriş belirsizliği
aralığa katıldı. Bu, kesin gerçekleşmiş net getiri değildir. Önceki tablodan farklı
altküme olduğu için daha yüksek görünen oranlar funding'in iyileştirdiği anlamına
gelmez. Eksik maliyetlere rağmen SHORT'un iyileştiği iddia edilemez.

## Kullanılabilir teknik parçalar

- `long_short_trader_context.py`: zamana uygun fiyat/akış/OI/funding kanıtı,
  ayrı LONG/SHORT koşulları ve açık ret nedenleri.
- `long_short_trade_plan.py`: taze emir defterinde belirtilen büyüklük için
  gerçekleşebilir giriş hesabı; spread, derinlik, ücret, kayma, funding ve engel
  sonrası risk/ödül kontrolü. Stop başarı oranı için genişletilmez.
- `long_short_funding_bounds.py`: gerçekleşmiş ödemeler ve dakika mark aralıkları.
- 21 yeni test geçişi; gelecekteki verinin geçmiş kararı değiştirmemesi, eksik veri,
  maliyet işaretleri, gerçek alış/satış tarafı ve kaynak bütünlüğü kontrol edildi.

Bu parçalar araştırmada çalışıyor. Yeni bağlam ve emir defteri parçaları canlı
runtime'a bağlanmadı; `actionable=false`. Emir defteriyle hesaplanan 2R planı bu
tarihsel tablonun çıkış kuralı değildir; tabloda eski V1 izleyen stopu kullanıldı.
Railway deployment, Telegram bildirimi veya otomatik emir açılmadı.

## Açık işler ve karar

Tarihsel evren kripto dışı sözleşmeler içeriyor; yalnız kripto sonucu olduğu
söylenemez. Tam funding kapsamı, Eylül plan sonuçları, aynı giriş/çıkışla basit
kıyaslar, portföy ve gerçek yürütme kısıtları, bağımsız dönem ve ileri kağıt testi
henüz tamamlanmadı. Eski kolektörün 166 sembol sınırı da canlı entegrasyon işidir.

Bu dört koşul grubundan hiçbiri iki yönlü canlı strateji olarak seçilmedi.
Yeni SHORT hipotezi ayrı tanımlanmalı ve yeni, ayrılmış dönemde sınanmalı;
mevcut kaybeden örnekleri eleyecek eşikler seçmek doğrulama olmaz. Kullanıcının
isteği mümkün olan en kaliteli fırsatları bulmak; bir başarı oranı kotasını
doldurmak veya zayıf sinyalleri mesaj üretmek için açmak değil.

Sayısal kayıtlar: `COMPARISON.json`, `FUNDED_COMPARISON.json`.
Bütün aday kayıtları: `EPISODES.json.gz`, `FUNDING_EPISODES.json.gz`.
Kaynak manifestleri ve indirmenin tüm durumları: `evidence/`.
