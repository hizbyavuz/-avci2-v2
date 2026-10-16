# Canlı güncelleme doğrulaması ve ilk eşleştirme

16 Ekim 2026, yaklaşık 17:53 Türkiye saati.

**Ölçüm güncellemesi canlıya geçti. Dağıtım SUCCESS, yeni ölçüm çalışanı aktif. Süresi dolmuş ama sırada bekleyen ölçüm sayısı bütün sürelerde sıfıra indi. Veri eksikleri ve aynı dakika belirsizlikleri ayrı tutuluyor.**

Güncel fiyat taraması yeniden başladı: 153 sembol değerlendirildi, 10 izleme adayı bulundu. Sinyal ve ayar kimlikleri değişmedi. Bu, sinyal performansının arttığı anlamına gelmez.

## İlk mesaj bazında karşılaştırma

Aynı sürümün üç saatlik sonucu çözümlenmiş 115 giriş mesajı kullanıldı. Başarı sütunu, hedef/stop veya süre sonunda varsayılan maliyet sonrası artıda kalmayı gösterir; yalnızca yönün doğru çıkması değildir.

| Mesaj anındaki koşul | Ölçülen | Artıda sonuç | Ortalama net referans getiri |
|---|---:|---:|---:|
| Günlük yönle aynı | 51 | 26 | %-0.648 |
| Günlük yöne ters | 56 | 28 | %-0.502 |
| Günlük yön belirsiz | 8 | 4 | %-0.635 |
| Saatlik akış ve fiyat destekliyor | 81 | 40 | %-0.572 |
| Saatlik akış ve fiyat birlikte desteklemiyor | 34 | 18 | %-0.586 |
| Net hedef kazancı stop kaybından az değil | 8 | 4 | %-0.970 |
| Net hedef kazancı stop kaybından küçük | 107 | 54 | %-0.546 |

Bu gruplar birbirinin üzerine biner; satırlar toplanmaz. Sadece günlük yönü veya akış desteğini zorunlu tutmanın sorunu çözdüğüne dair bu örneklemde kanıt yok. Kazanç/kayıp oranı >=1 grubu da yalnızca 8 örnek içeriyor ve sonucu pozitif değil. Hiçbirini yeni bir kural olarak canlıya almadık.

## Kullanıcı için anlamı

Mevcut motorun kararları henüz kazandıran bir yöntemi göstermiyor. Birkaç büyük kaybın küçük kazançları silebildiğini artık tek tek mesajlarla inceleyebiliyoruz. İyileştirme için yalnızca daha fazla gösterge eklemek yeterli görünmüyor. Girişin zamanlaması, hedef/kayıp mesafesi ve süre birlikte değerlendirilmek zorunda.

## Kapsam

Gerçek emir veya hesap getirisi ölçülmedi; maliyet varsayımı %0,30. İlk dakikadaki olay sırası belirsiz olan 2 kayıt ve ilk açılış dışa aktarımına girmeyen 2 veri eksikliği bu ana karşılaştırmaya dahil değil. Bekleyen süreler başarısız sayılmadı. Aynı coin ve zaman çevresindeki kayıtlar bağımsız örnekler kabul edilmemeli.

Tekrarlama: `python audits/delivered_setup_review_20261016/review_features.py`. Canlı kanıtlar `live_verification.json`, mesaj kayıtları `matched_ready_180m.json`, karşılaştırma `matched_features.json` içinde.
