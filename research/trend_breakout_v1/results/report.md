# Trend Kırılım Motoru — geçmiş test

**Sınırlı portföy: BAŞARISIZ · Sınırsız: BAŞARISIZ**

Dönem: 2023-10-23T00:00:00+00:00 — 2026-10-23T00:00:00+00:00. Ayrım: 2025-11-28T04:00:00+00:00.
Orijinal kurallar; 0 değişiklik. Kilitli test tek çalıştırma. Canlı workflow KAPALI.

| Portföy / dönem / yön | İşlem | Ort. net R | Toplam R | Maks. düşüş R | Kayıp serisi | Hedef önce % | Yön isabet % | %0,60 ort. R | En iyi 3 hariç ort. R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unlimited/development/ALL | 9051 | -0.1368 | -1238.0728 | 1276.6444 | 52 | 33.2008 | 49.9282 | -0.2704 | -0.1469 |
| unlimited/development/LONG | 4430 | -0.1864 | -825.8495 | 842.5040 | 33 | 32.0316 | 49.2099 | -0.3310 | -0.2005 |
| unlimited/development/SHORT | 4621 | -0.0892 | -412.2233 | 498.7415 | 53 | 34.3216 | 50.6167 | -0.2124 | -0.1035 |
| unlimited/locked_test/ALL | 3561 | -0.1406 | -500.7212 | 551.7227 | 37 | 34.5128 | 49.0593 | -0.3145 | -0.1582 |
| unlimited/locked_test/LONG | 1501 | -0.1666 | -250.0248 | 260.3916 | 25 | 33.7775 | 50.4997 | -0.3502 | -0.2023 |
| unlimited/locked_test/SHORT | 2060 | -0.1217 | -250.6964 | 330.0237 | 34 | 35.0485 | 48.0097 | -0.2885 | -0.1336 |
| limited/development/ALL | 3065 | -0.1298 | -397.6882 | 406.1327 | 21 | 33.5400 | 50.6362 | -0.2667 | -0.1536 |
| limited/development/LONG | 1823 | -0.1476 | -269.0333 | 278.2628 | 21 | 32.9128 | 49.5337 | -0.2818 | -0.1772 |
| limited/development/SHORT | 1242 | -0.1036 | -128.6550 | 131.7681 | 19 | 34.4605 | 52.2544 | -0.2444 | -0.1280 |
| limited/locked_test/ALL | 1703 | -0.1350 | -229.8330 | 249.4279 | 16 | 35.1145 | 50.3817 | -0.3170 | -0.1546 |
| limited/locked_test/LONG | 842 | -0.1404 | -118.1867 | 134.4504 | 13 | 34.6793 | 52.9691 | -0.3257 | -0.1693 |
| limited/locked_test/SHORT | 861 | -0.1297 | -111.6464 | 147.5427 | 19 | 35.5401 | 47.8513 | -0.3085 | -0.1606 |

## Veri ve uygulama sınırları
- Futures arşivi ve resmi SHA256 doğrulaması; fallback kullanılan parite kaynakları summary.json ve CSV’de.
- Evren: tüm arşiv sembolleri, önceki 30 günlük USDT hacmi; 60 günlük geçmiş. Stablecoin/endeks dışlama listesi veri modülünde açıkça kayıtlı; tarihsel ürün sınıflandırması bağımsız exchangeInfo arşiviyle doğrulanmadı.
- Funding yüzdesi: elde tutma aralığındaki gerçek oranların yön işaretli toplamı. Eksik aylar iki yön için de %0,01 / 8 saat gider varsayımı. Sabit giriş nominali üzerinden yüzde muhasebesi; mark-price ile değişen nominal tutar ayrıca modellenmedi.
- Stop/TP aynı saatte: tam 1m veriyle sıra; aynı dakikada ikisi veya eksik/tutarsız 1m: STOP. Sayılar aşağıda.
- Geliştirme/test sınırı ve dönem sonundaki son 168 sinyal saati sonuçtan bağımsız dışlandı.
- Düşüş: kapanan işlemlerden oluşan R eğrisi; açık işlemlerin anlık toplam zararını ölçmez.
- Test varsayımsal dolumdur. Günlük kâr veya günlük sinyal garantisi değildir.
- Veri kapsamı: tam indirme kontrolü geçti. Ürün sınıflandırması ve funding fiyatı sınırlamaları yukarıda.

## Sonuç dağılımı ve 1m çözümü
- unlimited/development/ALL: {'HEDEF': 3005, 'STOP': 6026, 'TIMEOUT': 20}; çözüm {'1H_SINGLE': 9022, '168H': 20, '1M_RESOLVED': 9}; funding varsayımlı 0; en iyi 3 ['WIFUSDT', 'ENAUSDT', 'MYXUSDT'].
- unlimited/locked_test/ALL: {'STOP': 2317, 'HEDEF': 1229, 'TIMEOUT': 15}; çözüm {'1H_SINGLE': 3538, '168H': 15, '1M_RESOLVED': 8}; funding varsayımlı 182; en iyi 3 ['FARTCOINUSDT', 'PENGUUSDT', '1000PEPEUSDT'].
- limited/development/ALL: {'HEDEF': 1028, 'STOP': 2030, 'TIMEOUT': 7}; çözüm {'1H_SINGLE': 3053, '168H': 7, '1M_RESOLVED': 5}; funding varsayımlı 0; en iyi 3 ['MATICUSDT', 'ETHUSDT', 'RUNEUSDT'].
- limited/locked_test/ALL: {'STOP': 1100, 'HEDEF': 598, 'TIMEOUT': 5}; çözüm {'1H_SINGLE': 1691, '168H': 5, '1M_RESOLVED': 7}; funding varsayımlı 74; en iyi 3 ['PENGUUSDT', 'SNDKUSDT', 'BEATUSDT'].

## Ay ay toplam net R
| Portföy | Dönem | Ay | İşlem | Toplam R |
|---|---|---|---:|---:|
| limited | development | 2023-10 | 36 | 1.0267 |
| limited | development | 2023-11 | 138 | -51.7485 |
| limited | development | 2023-12 | 150 | 0.1410 |
| limited | development | 2024-01 | 102 | -4.8769 |
| limited | development | 2024-02 | 142 | -22.6905 |
| limited | development | 2024-03 | 138 | -23.4759 |
| limited | development | 2024-04 | 96 | 5.5337 |
| limited | development | 2024-05 | 117 | -6.5982 |
| limited | development | 2024-06 | 127 | -7.9853 |
| limited | development | 2024-07 | 133 | -2.5226 |
| limited | development | 2024-08 | 117 | -21.3842 |
| limited | development | 2024-09 | 134 | -0.7133 |
| limited | development | 2024-10 | 136 | -38.0241 |
| limited | development | 2024-11 | 139 | -35.7126 |
| limited | development | 2024-12 | 111 | -24.8885 |
| limited | development | 2025-01 | 131 | 14.4320 |
| limited | development | 2025-02 | 108 | -3.2643 |
| limited | development | 2025-03 | 130 | -32.9223 |
| limited | development | 2025-04 | 127 | -22.8426 |
| limited | development | 2025-05 | 123 | -31.0107 |
| limited | development | 2025-06 | 83 | -9.0073 |
| limited | development | 2025-07 | 130 | -15.9932 |
| limited | development | 2025-08 | 94 | -34.1776 |
| limited | development | 2025-09 | 130 | 1.5528 |
| limited | development | 2025-10 | 113 | -8.8119 |
| limited | development | 2025-11 | 80 | -21.7238 |
| limited | locked_test | 2025-11 | 16 | -5.5266 |
| limited | locked_test | 2025-12 | 157 | -25.5631 |
| limited | locked_test | 2026-01 | 160 | 24.4452 |
| limited | locked_test | 2026-02 | 127 | -20.1719 |
| limited | locked_test | 2026-03 | 160 | -21.7594 |
| limited | locked_test | 2026-04 | 155 | -67.9118 |
| limited | locked_test | 2026-05 | 172 | -47.1337 |
| limited | locked_test | 2026-06 | 181 | -3.1339 |
| limited | locked_test | 2026-07 | 151 | -13.3454 |
| limited | locked_test | 2026-08 | 166 | -14.3781 |
| limited | locked_test | 2026-09 | 186 | -32.3547 |
| limited | locked_test | 2026-10 | 72 | -2.9998 |
| unlimited | development | 2023-10 | 149 | -41.4002 |
| unlimited | development | 2023-11 | 313 | -114.0436 |
| unlimited | development | 2023-12 | 357 | -45.6267 |
| unlimited | development | 2024-01 | 299 | -21.7582 |
| unlimited | development | 2024-02 | 348 | -86.5075 |
| unlimited | development | 2024-03 | 305 | -63.4400 |
| unlimited | development | 2024-04 | 371 | 36.2241 |
| unlimited | development | 2024-05 | 307 | -147.7013 |
| unlimited | development | 2024-06 | 260 | -31.8193 |
| unlimited | development | 2024-07 | 407 | 77.2444 |
| unlimited | development | 2024-08 | 477 | 56.5692 |
| unlimited | development | 2024-09 | 390 | -101.5292 |
| unlimited | development | 2024-10 | 360 | -76.9218 |
| unlimited | development | 2024-11 | 413 | -92.5378 |
| unlimited | development | 2024-12 | 338 | -42.6826 |
| unlimited | development | 2025-01 | 461 | 33.0321 |
| unlimited | development | 2025-02 | 344 | 47.9493 |
| unlimited | development | 2025-03 | 366 | -48.6084 |
| unlimited | development | 2025-04 | 400 | -25.6948 |
| unlimited | development | 2025-05 | 380 | -81.5590 |
| unlimited | development | 2025-06 | 333 | -97.9028 |
| unlimited | development | 2025-07 | 409 | -22.6287 |
| unlimited | development | 2025-08 | 329 | -190.7140 |
| unlimited | development | 2025-09 | 343 | -25.4500 |
| unlimited | development | 2025-10 | 332 | -79.3291 |
| unlimited | development | 2025-11 | 260 | -51.2368 |
| unlimited | locked_test | 2025-11 | 17 | -6.6543 |
| unlimited | locked_test | 2025-12 | 335 | -88.7929 |
| unlimited | locked_test | 2026-01 | 405 | 65.0899 |
| unlimited | locked_test | 2026-02 | 333 | -9.0062 |
| unlimited | locked_test | 2026-03 | 331 | -83.3172 |
| unlimited | locked_test | 2026-04 | 256 | -132.5275 |
| unlimited | locked_test | 2026-05 | 348 | -70.8786 |
| unlimited | locked_test | 2026-06 | 387 | 3.0922 |
| unlimited | locked_test | 2026-07 | 315 | -80.6965 |
| unlimited | locked_test | 2026-08 | 297 | -4.9987 |
| unlimited | locked_test | 2026-09 | 357 | -61.7121 |
| unlimited | locked_test | 2026-10 | 180 | -30.3194 |

## Atlananlar
```json
{
  "evaluations": {
    "NO_BREAKOUT": 1140446,
    "TREND_CHANNEL_VOLUME": 24612,
    "NO_TREND": 112412,
    "LOW_VOLUME": 10184,
    "BOUNDARY_PURGE_168H": 16800,
    "DATA_GAP": 9984
  },
  "plans": {
    "OBSTACLE_BEFORE_TARGET": 3200,
    "LATE_BREAKOUT": 2900,
    "INVALID_TARGET": 10
  },
  "limits": {
    "SAME_SYMBOL_OPEN": 8213,
    "LİMİT NEDENİYLE ATLANDI": 11411
  }
}
```

Tam işlemler: trades.csv; reddedilenler: skipped_and_rejected.csv; ayrıntı: summary.json; değişmez protokol: locked_test_seal.json.
Kaynaklar: https://github.com/binance/binance-public-data · https://data.binance.vision/
