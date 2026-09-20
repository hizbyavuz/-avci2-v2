from dataclasses import dataclass, asdict
import os
import requests

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
JUPITER_BASE_URL = "https://api.jup.ag/swap/v2"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def jupiter_sell_quote(
    token_mint,
    amount_atomic,
):
    """
    Solana tokenini USDC'ye satmak için gerçek Jupiter quote'u alır.

    amount_atomic:
    Token miktarının en küçük birimdeki hali.
    Örn. decimals=6 ise 1 token = 1_000_000.
    """

    api_key = os.getenv("JUPITER_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "reason": "JUPITER_API_KEY bulunamadi"
        }

    try:
        response = requests.get(
            f"{JUPITER_BASE_URL}/order",
            params={
                "inputMint": token_mint,
                "outputMint": USDC_MINT,
                "amount": str(int(amount_atomic)),
            },
            headers={
                "x-api-key": api_key
            },
            timeout=20,
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "reason": f"Jupiter HTTP {response.status_code}",
                "body": response.text[:300],
            }

        data = response.json()

        out_amount = data.get("outAmount")

        if not out_amount:
            return {
                "ok": False,
                "reason": data.get(
                    "errorMessage",
                    "Jupiter quote yok"
                ),
                "router": data.get("router"),
            }

        # USDC 6 decimal
        expected_usd = float(out_amount) / 1_000_000

        return {
            "ok": True,
            "expected_usd": expected_usd,
            "router": data.get("router"),
            "out_amount": out_amount,
            "request_id": data.get("requestId"),
        }

    except Exception as e:
        return {
            "ok": False,
            "reason": str(e),
        }
