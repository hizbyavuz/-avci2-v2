from dataclasses import dataclass, asdict


# Avci 2 - Gercekci Cikis Motoru
# Amaç:
# Bir token yükseldiğinde, kağıt üzerindeki fiyat yerine
# tanımlı pozisyon büyüklüğünün gerçekten satılabilirliğini ölçmek.


@dataclass
class ExitResult:
    network: str
    token_contract: str
    position_usd: float

    quote_available: bool
    expected_usd: float | None
    slippage_pct: float | None
    price_impact_pct: float | None

    liquidity_usd: float | None
    position_liquidity_pct: float | None

    status: str
    reason: str

    def to_dict(self):
        return asdict(self)


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def liquidity_pressure(position_usd, liquidity_usd):
    """
    Pozisyonun havuz likiditesine oranı.
    Bu GERCEK DEX quote değildir.
    Sadece ön risk filtresidir.
    """

    position_usd = _num(position_usd)
    liquidity_usd = _num(liquidity_usd)

    if liquidity_usd <= 0:
        return None

    return (position_usd / liquidity_usd) * 100


def realistic_exit(candidate, position_usd=500):
    """
    Ortak multi-chain çıkış arayüzü.

    Şimdilik:
    - Likidite baskısını hesaplar.
    - Gerçek DEX quote yoksa PASS vermez.
    - Sonraki adımda Solana/Jupiter ve EVM adapterleri
      bu fonksiyona bağlanacak.
    """

    network = str(
        candidate.get("network")
        or candidate.get("network_name")
        or ""
    )

    token_contract = str(
        candidate.get("token_contract")
        or candidate.get("address")
        or ""
    )

    liquidity = _num(
        candidate.get("liquidity")
        or candidate.get("liquidity_usd")
    )

    pressure = liquidity_pressure(
        position_usd,
        liquidity
    )

    # Güvenlik kapısı:
    # Gerçek satış quote'u olmadan coin "satılabilir" kabul edilmiyor.
    return ExitResult(
        network=network,
        token_contract=token_contract,
        position_usd=float(position_usd),

        quote_available=False,
        expected_usd=None,
        slippage_pct=None,
        price_impact_pct=None,

        liquidity_usd=liquidity,
        position_liquidity_pct=pressure,

        status="UNKNOWN",
        reason="Gercek DEX sell quote henuz alinmadi."
    )


def exit_summary(candidate):
    """
    $100 / $500 / $1000 için çıkış testi.
    """

    results = {}

    for amount in (100, 500, 1000):
        results[str(amount)] = realistic_exit(
            candidate,
            position_usd=amount
        ).to_dict()

    return results
