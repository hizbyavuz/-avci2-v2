# Planlanan giriş düzeltmesi ve kapsam araştırması — 16 Ekim 2026

Üretim tabanı: `43da26dd9978242c0fe081954ca86b1a90528cb6`.
Yeni sürüm: `MAJOR_MOVE_V1_1_PLANNED_ENTRY_2026_10_16`.
Yeni karar ayar özeti: `69229c2480fed1928edc86b071260b4f65b7fa738b1af3f20f458c8174c8111c`.

## Canlıya hazırlanmış düzeltme

WATCH (bekle) aşamasında stop ve tarihsel engel mesafesi artık planlanan giriş fiyatından da hesaplanır. LONG için güncel fiyat ile tetikleyicinin büyüğü; SHORT için küçüğü kullanılır. Bu, sabit stopla hâlâ mümkün olan en iyi giriş referansıdır. Sonraki daha kötü fiyat ayrıca gönderim kontrolünden geçer.

- Girişte stop mesafesi %8'i aşıyorsa `PLANNED_STOP_TOO_DISTANT`.
- Girişte en yakın 4 saatlik engel %10'dan yakınsa `PLANNED_ROOM_BLOCKED`.
- Bilinmeyen engel mesafesi bilinmeyen olarak kalır; garantili hedefe çevrilmez.
- Mevcut giriş/risk eşikleri, yön senaryoları, türev verisinin gözlemsel rolü, sonuç takibi ve Telegram gönderim akışı korunur.
- Yeni süreç, servis veya veri kaynağı eklenmez. Veri şeması değişmez; planlanan geometri mevcut değerlendirme JSON'una yazılır.
- Sürüm/config/strateji kimliği değişir. Eski sinyal kayıtları silinmez.

Bu düzeltme daha çok sinyal üretme iddiası taşımaz. Önceden uygulanabilir görünen fakat girişte kendi kurallarını karşılamayan beklemeleri doğru sınıflandırır.

## Aynı veride eski/yeni karşılaştırması

15 Ekim 20:30–16 Ekim 20:30 Türkiye; 154 coin, 97 kapanış, 14.938 değerlendirme. Güncel likidite listesi geçmişe uygulanmıştır; bu bir gerçek işlem veya kârlılık testi değildir.

| Geçiş | Adet |
|---|---:|
| Reddedildi → reddedildi | 14.908 |
| Bekle → reddedildi: girişte stop fazla uzak | 19 |
| Bekle → reddedildi: girişte engel mesafesi yetersiz | 7 |
| Bekle → bekle | 4 |

Her iki sürümde de bu örneklemde READY yoktur. Dolayısıyla “geçmiş gerçek hazır sinyallerin tamamını korudu” şeklinde bir istatistiksel iddia kurulamaz. READY durumunda planlanan girişin güncel fiyata eşit kaldığı davranış ayrı regresyon testiyle doğrulandı.

İlgili karar/bağlam/sonuç/kaynak testleri geçti: 61 testlik çalışma ve ardından eklenen SHORT regresyonunu içeren 8 planlanan giriş testi (aynı 7 test ikinci sayıya dahildir; toplam 62 farklı test).

## Ayrı, çevrimdışı kapsam araştırması

`audits/compare_planned_entry_versions.py` canlı çalışan tarafından çağrılmaz. Önceki bağımsız taramanın iki yönünü ayrı etiketler: son dört saatlik tepeyi aşan LONG ve son dört saatlik dibi kıran düşüş-devamı SHORT. 4/12 saat yönü, BTC'ye göre güç, hacim ağırlıklı fiyatın tarafı, hacim artışı, en çok %8 yerel stop ve en çok yarım ATR geç kalma koşulları kullanılır. %10 engel şartının etkisi ayrıca raporlanır.

Araştırma sürümü `COVERAGE_RESEARCH_R1_2026_10_16`; ayar özeti `f3707c25f4ac8710745a0fe76d00292bc4a08bc06f93bf2c7e7476cf0481402d`.

Coin başına bu dönemdeki yalnız ilk araştırma olayı alındığında 88 LONG ve 36 SHORT örneği oluştu. Bunların 119'unda tarihsel engel %10'dan yakındı, 2'sinde engel bilinmiyordu, 3'ünde bilinen engel en az %10 uzaktaydı. Bunlar gönderilmiş öneriler veya başarılı işlemler değildir; yalnız kapsam karşılaştırmasıdır. Bu gün, model tasarımından önce görüldüğünden sonuçlar bağımsız doğrulama sayılamaz.

Bu bulgu, sadece yeni yön senaryoları eklemenin yeterli olmadığını gösteriyor: “büyük hareket potansiyeli” ile “önünde hiç tarihsel engel olmaması” ayrımını da araştırmak gerekiyor. Mevcut %10 engel sınırını kaldırmanın daha kârlı olduğu bu çalışmayla gösterilmedi.

Bir sonraki strateji kararının kanıtı; yeni bir zaman döneminde zaman damgalı adaylar, aynı coinlerde basit karşılaştırma, stop öncesi/sonrası hareket ve maliyet sonrası sonuçlar olmalı. Burada bu araştırma sürümü Telegram kararlarına bağlanmış veya kârlı ilan edilmiş değildir.

## Yeniden çalıştırma

Önceki denetimin kanıt paketindeki `data` dizini kullanılır:

```bash
python audits/compare_planned_entry_versions.py --data-dir /absolute/path/to/data --output /tmp/major-comparison.json
PYTHONPATH=.:tests python -m unittest tests.test_major_planned_entry tests.test_major_move tests.test_major_context tests.test_runtime_cleanup -q
```

Temel sürümün kaynak SHA-256 özeti ve bütün geçişler `comparison.json` dosyasındadır. Başka güne ait verilerle çalıştırılırsa betiğin sabit tarih kesiti ve tasarım kaydı ayrı sürümle güncellenmelidir.
