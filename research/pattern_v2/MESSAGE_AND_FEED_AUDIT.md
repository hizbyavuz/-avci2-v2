# V2.1 — mesaj ve veri hazırlama denetimi (23 Ekim 2026)

V2 karar fonksiyonları ve CONFIG değişmedi. Kaynak karşılaştırması, yalnız sürüm etiketi ve message() dışında long_short_patterns.py içeriğinin 270b9b6 ile aynı olduğunu doğruladı. Yeni kod manifesti için V2.1 etiketi kullanılır; aynı V2 veritabanındaki eski kayıtlar ve açık planlar korunur. Başarı oranı iddiası yok.

- Kart: giriş referansı, gerçekleşmiş kırılım/geri test teyidi, fiyatın koruması gereken seviye, kovalama sınırı, SL, TP, masraf sonrası R, UTC son geçerlilik; altta fiyat yapısı, ATR, hacim, taker alış/satış ve Fib ölçümleri. Eksik/eski türev verisi açıkça yazılır.
- Veri: PATTERN_STRUCTURE_V2 profilinde her coin için 1m/15m/30m/4h tamamlanır; eski 5m öncelikli tüm-evren geçişi kullanılmaz. Diğer profiller değişmez. 400 yalnız sembolü eler; kaynak hatası/429 geçişi durdurur ve mevcut bekleme uygulanır.
- Açılış: beklenen eksik/eski evren verisi worker'ı öldürmek yerine 60 saniye bekletir. Beklenmeyen ValueError hataları yutulmaz.

Doğrulama: test_pattern_engine, test_futures_history_worker, test_futures_window_prewarm, test_history_symbol_isolation, test_pattern_delivery_operations — 53 test başarılı. LONG/SHORT kart alanları ve teyit anlamı, veri tamamlamada ilk coin bağlamının önceliği, kaynak hatasında bekleme, sembol izolasyonu ve başlangıç beklemesi sınandı.

Açık sınırlamalar: tek websocket bağlantısı 166 coin kapasitesinde, son gözlenen evren 316 coin; dönüşümlü kapsama tam anlık kapsama değildir. Küçük pivotların önem ayrımı ve hedefi gereğinden yakın bırakması ayrı araştırma konusudur; hedef veya R filtresi gevşetilmedi. Gerçekleşmiş funding tam net sonuca henüz dahil değildir. Son kontrol anında eski dağıtımda sıfır başarılı tarama ve eski/eksik geçmiş vardır; yeni kodun canlı sonucu ayrıca doğrulanmalıdır.
