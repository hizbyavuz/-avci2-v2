import os
import requests
import time
from datetime import datetime, timezone
from snapshot_deposu import snapshot_kaydet, son_snapshot, snapshot_sayisi
BASE_URL = "https://api.geckoterminal.com/api/v2"

NETWORKS = {
    "solana": "Solana",
    "base": "Base",
    "bsc": "BSC",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
}
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# -------------------------------------------------
# AVCI 2 V2.1 CONFIG
# -------------------------------------------------

CONFIG_VERSION = "v2.1-age-veto-live"

MIN_LIQUIDITY = 15000
MAX_LIQUIDITY = 500000

MIN_VOLUME_24H = 30000

MIN_CHANGE_24H = 5
MAX_CHANGE_24H = 40

# Canli aktivite
MIN_VOLUME_1H = 1000
MIN_VOLUME_5M = 100

MIN_TX_1H = 15
MIN_TX_5M = 3

# Sert dusus veto
VETO_CHANGE_1H = -12
VETO_CHANGE_6H = -25
VETO_CHANGE_5M = -8

# Pool yasi
NEW_LAUNCH_MINUTES = 60

HEADERS = {
    "accept": "application/json;version=20230203"
}


def num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except Exception:
        return None


def pool_age_minutes(created_at):
    dt = parse_time(created_at)

    if not dt:
        return None

    now = datetime.now(timezone.utc)

    return max(
        0,
        (now - dt).total_seconds() / 60
    )


def api_get(path):
    url = f"{BASE_URL}{path}"

    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=25
            )

            if r.status_code == 429:
                print("Rate limit -> 15 saniye bekleniyor...")
                time.sleep(15)
                continue

            r.raise_for_status()

            return r.json()

        except Exception as e:
            print("API HATA:", url)
            print(e)

            if attempt < 2:
                time.sleep(5)

    return {
        "data": [],
        "included": []
    }
def jupiter_quote(input_mint, output_mint, amount):
    if not JUPITER_API_KEY:
        return {
            "ok": False,
            "error": "JUPITER_API_KEY yok"
        }

    try:
        r = requests.get(
            JUPITER_QUOTE_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
            },
            headers={
                "x-api-key": JUPITER_API_KEY,
                "accept": "application/json",
            },
            timeout=20,
        )

        r.raise_for_status()
        data = r.json()

        return {
            "ok": True,
            "out_amount": data.get("outAmount"),
            "price_impact_pct": data.get("priceImpactPct"),
            "route_plan": data.get("routePlan", []),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }

def included_map(payload):
    result = {}

    for obj in payload.get("included", []):
        key = (
            obj.get("type"),
            obj.get("id")
        )

        result[key] = obj

    return result


def relation_id(pool, relation):
    try:
        return (
            pool["relationships"]
            [relation]
            ["data"]
            ["id"]
        )
    except Exception:
        return None


def token_from_included(pool, inc, relation):
    rid = relation_id(
        pool,
        relation
    )

    if not rid:
        return {}

    obj = inc.get(
        ("token", rid)
    )

    if not obj:
        return {}

    attrs = obj.get(
        "attributes",
        {}
    )

    return {
        "id": rid,
        "address": attrs.get(
            "address",
            ""
        ),
        "name": attrs.get(
            "name",
            ""
        ),
        "symbol": attrs.get(
            "symbol",
            ""
        ),
 "decimals": int(attrs.get("decimals") or 0),
    }

def classify_stage(
    age_minutes,
    change_1h,
    change_5m,
    acceleration_1h,
    acceleration_5m,
    buys_1h,
    sells_1h,
    buys_5m,
    sells_5m,
):
    if (
        age_minutes is not None
        and age_minutes < NEW_LAUNCH_MINUTES
    ):
        return "NEW_LAUNCH"

    if (
        acceleration_1h >= 1.5
        and buys_1h > sells_1h
    ):
        if (
            acceleration_5m >= 1.3
            and buys_5m > sells_5m
            and change_5m >= 0
        ):
            return "RE_IGNITION"

        return "WAKE_UP"

    if (
        buys_1h > sells_1h
        and change_1h >= 0
    ):
        return "PERSISTENCE"

    return "WATCH"


def scan_payload(
    payload,
    network_id,
    network_name,
    source
):
    inc = included_map(payload)

    found = []

    for pool in payload.get("data", []):
        a = pool.get(
            "attributes",
            {}
        )

        pool_address = a.get(
            "address",
            ""
        )

        pool_name = a.get(
            "name",
            "Unknown"
        )

        created_at = a.get(
            "pool_created_at"
        )

        age_minutes = pool_age_minutes(
            created_at
        )

        liquidity = num(
            a.get("reserve_in_usd")
        )
price_usd = num(
    a.get("base_token_price_usd")
)
        volume = a.get(
            "volume_usd",
            {}
        )

        volume_24h = num(
            volume.get("h24")
        )

        volume_6h = num(
            volume.get("h6")
        )

        volume_1h = num(
            volume.get("h1")
        )

        volume_5m = num(
            volume.get("m5")
        )

        changes = a.get(
            "price_change_percentage",
            {}
        )

        change_24h = num(
            changes.get("h24")
        )

        change_6h = num(
            changes.get("h6")
        )

        change_1h = num(
            changes.get("h1")
        )

        change_5m = num(
            changes.get("m5")
        )

        tx = a.get(
            "transactions",
            {}
        )

        tx24 = tx.get(
            "h24",
            {}
        )

        tx1 = tx.get(
            "h1",
            {}
        )

        tx5 = tx.get(
            "m5",
            {}
        )

        buys_24h = num(
            tx24.get("buys")
        )

        sells_24h = num(
            tx24.get("sells")
        )

        buys_1h = num(
            tx1.get("buys")
        )

        sells_1h = num(
            tx1.get("sells")
        )

        buys_5m = num(
            tx5.get("buys")
        )

        sells_5m = num(
            tx5.get("sells")
        )

        tx_count_1h = (
            buys_1h
            + sells_1h
        )

        tx_count_5m = (
            buys_5m
            + sells_5m
        )

        base_token = token_from_included(
            pool,
            inc,
            "base_token"
        )

        quote_token = token_from_included(
            pool,
            inc,
            "quote_token"
        )

        token_contract = (
            base_token.get(
                "address",
                ""
            )
        )

        # ---------------------------------------
        # TEMEL FILTRELER
        # ---------------------------------------

        if liquidity < MIN_LIQUIDITY:
            continue

        if liquidity > MAX_LIQUIDITY:
            continue

        if volume_24h < MIN_VOLUME_24H:
            continue

        if not (
            MIN_CHANGE_24H
            <= change_24h
            <= MAX_CHANGE_24H
        ):
            continue

        if buys_24h <= sells_24h:
            continue

        # ---------------------------------------
        # SHORT-TERM CRASH VETO
        # DONATED tipi coinleri engeller
        # ---------------------------------------

        veto_reason = None

        if change_1h <= VETO_CHANGE_1H:
            veto_reason = "1H_CRASH"

        if change_6h <= VETO_CHANGE_6H:
            veto_reason = "6H_CRASH"

        if change_5m <= VETO_CHANGE_5M:
            veto_reason = "5M_CRASH"

        if veto_reason:
            continue

        # ---------------------------------------
        # YENI POOL AYRIMI
        # MONA tipi sahte acceleration engeli
        # ---------------------------------------

        is_new_launch = (
            age_minutes is not None
            and age_minutes
            < NEW_LAUNCH_MINUTES
        )

        # ---------------------------------------
        # CANLI AKTIVITE
        # ---------------------------------------

        if is_new_launch:

            # Yeni coinlerde 24h / 1h karsilastirmasi
            # yapmiyoruz.
            # Gercek 5m aktivitesine bakiyoruz.

            if volume_5m < MIN_VOLUME_5M:
                continue

            if tx_count_5m < MIN_TX_5M:
                continue

            if buys_5m <= sells_5m:
                continue

        else:

            # Eski poollarda son saat hala yasiyor mu?

            if volume_1h < MIN_VOLUME_1H:
                continue

            if tx_count_1h < MIN_TX_1H:
                continue

            if volume_5m < MIN_VOLUME_5M:
                continue

            if tx_count_5m < MIN_TX_5M:
                continue

        # ---------------------------------------
        # RATIOS
        # ---------------------------------------

        volume_liquidity_ratio = (
            volume_24h
            / max(liquidity, 1)
        )

        buy_sell_ratio_24h = (
            buys_24h
            / max(sells_24h, 1)
        )

        buy_sell_ratio_1h = (
            buys_1h
            / max(sells_1h, 1)
        )

        buy_sell_ratio_5m = (
            buys_5m
            / max(sells_5m, 1)
        )

        # ---------------------------------------
        # ACCELERATION
        # ---------------------------------------

        if is_new_launch:

            # Yeterli gecmis olmadigi icin
            # acceleration hesaplamiyoruz.

            acceleration_1h = None
            acceleration_5m = None

        else:

            expected_hour = max(
                volume_24h / 24,
                1
            )

            acceleration_1h = (
                volume_1h
                / expected_hour
            )

            expected_5m = max(
                volume_1h / 12,
                1
            )

            acceleration_5m = (
                volume_5m
                / expected_5m
            )

        # ---------------------------------------
        # SCORE
        # ---------------------------------------

        score = 0

        if buy_sell_ratio_24h >= 1.2:
            score += 1

        if buy_sell_ratio_1h >= 1.2:
            score += 1

        if buy_sell_ratio_5m >= 1.2:
            score += 1

        if volume_liquidity_ratio >= 1:
            score += 1

        if not is_new_launch:

            if (
                acceleration_1h is not None
                and acceleration_1h >= 1.5
            ):
                score += 1

            if (
                acceleration_5m is not None
                and acceleration_5m >= 1.3
            ):
                score += 1

            if change_1h >= 0:
                score += 1

            if change_5m >= 0:
                score += 1

        else:

            # Yeni launch icin skor ust siniri
            # bilerek dusuk tutuluyor.

            if buys_5m > sells_5m:
                score += 1

        # ---------------------------------------
        # STAGE
        # ---------------------------------------

        stage = classify_stage(
            age_minutes,
            change_1h,
            change_5m,
            acceleration_1h or 0,
            acceleration_5m or 0,
            buys_1h,
            sells_1h,
            buys_5m,
            sells_5m,
        )

        found.append({
            "network": network_name,
            "network_id": network_id,
            "source": source,

            "name": (
                base_token.get(
                    "name"
                )
                or pool_name
            ),

            "symbol": base_token.get(
                "symbol",
                ""
            ),

            "token_contract":
                token_contract,

            "pool":
                pool_address,

            "quote_symbol":
                quote_token.get(
                    "symbol",
                    ""
                ),

            "created_at":
                created_at,

            "age_minutes":
                age_minutes,

            "stage":
                stage,

            "score":
                score,

            "liquidity":
                liquidity,
            "price_usd":
                price_usd,
            "volume_24h":
                volume_24h,

            "volume_6h":
                volume_6h,

            "volume_1h":
                volume_1h,

            "volume_5m":
                volume_5m,

            "change_24h":
                change_24h,

            "change_6h":
                change_6h,

            "change_1h":
                change_1h,

            "change_5m":
                change_5m,

            "buys_24h":
                buys_24h,

            "sells_24h":
                sells_24h,

            "buys_1h":
                buys_1h,

            "sells_1h":
                sells_1h,

            "buys_5m":
                buys_5m,

            "sells_5m":
                sells_5m,

            "volume_liquidity_ratio":
                volume_liquidity_ratio,

            "acceleration_1h":
                acceleration_1h,

            "acceleration_5m":
                acceleration_5m,

            "is_new_launch":
                is_new_launch,
        })

    return found


def deduplicate(candidates):
    result = {}

    for c in candidates:

        key = (
            c["network_id"],
            c["token_contract"]
            or c["pool"]
        )

        old = result.get(key)

        if old is None:
            result[key] = c
            continue

        if (
            c["score"]
            > old["score"]
        ):
            result[key] = c

        elif (
            c["score"]
            == old["score"]
            and c["liquidity"]
            > old["liquidity"]
        ):
            result[key] = c

    return list(
        result.values()
    )


print("=" * 72)
print("AVCI 2 V2.1 — MULTI-CHAIN")
print("CONFIG:", CONFIG_VERSION)

print(
    "UTC:",
    datetime.now(
        timezone.utc
    ).isoformat()
)

print("=" * 72)

all_candidates = []


for network_id, network_name in NETWORKS.items():

    print()
    print(
        f"[{network_name}] taraniyor..."
    )

    trending = api_get(
        f"/networks/"
        f"{network_id}/"
        f"trending_pools"
        f"?include="
        f"base_token,quote_token"
    )

    all_candidates.extend(
        scan_payload(
            trending,
            network_id,
            network_name,
            "TRENDING"
        )
    )

    time.sleep(7)

    new_pools = api_get(
        f"/networks/"
        f"{network_id}/"
        f"new_pools"
        f"?include="
        f"base_token,quote_token"
    )

    all_candidates.extend(
        scan_payload(
            new_pools,
            network_id,
            network_name,
            "NEW"
        )
    )

    time.sleep(7)


all_candidates = deduplicate(
    all_candidates
)
for candidate in all_candidates:
    snapshot_kaydet(
        candidate,
        CONFIG_VERSION
    )

print(
    "Snapshot toplam:",
    snapshot_sayisi()
)
all_candidates.sort(
    key=lambda x: (
        x["score"],
        x["volume_liquidity_ratio"]
    ),
    reverse=True
)


print()
print("=" * 72)


if not all_candidates:

    print("0 TEMIZ AVCI 2 ADAYI")
    print(
        "0 aday da dogru bir sonuctur."
    )

else:

    print(
        f"{len(all_candidates)} "
        "ADAY BULUNDU"
    )

    print("=" * 72)

    for i, c in enumerate(
        all_candidates[:20],
        1
    ):

        print()

        print(
            f"{i}. "
            f"{c['name']} "
            f"({c['symbol']})"
        )

        print(
            "   Network:",
            c["network"]
        )

        print(
            "   Stage:",
            c["stage"]
        )

        print(
            "   Source:",
            c["source"]
        )

        print(
            f"   Avci score: "
            f"{c['score']}/8"
        )

        if c["age_minutes"] is not None:

            print(
                f"   Pool age: "
                f"{c['age_minutes']:.1f} dk"
            )

        print(
            f"   24H fiyat: "
            f"%{c['change_24h']:.2f}"
        )

        print(
            f"   6H fiyat: "
            f"%{c['change_6h']:.2f}"
        )

        print(
            f"   1H fiyat: "
            f"%{c['change_1h']:.2f}"
        )

        print(
            f"   5M fiyat: "
            f"%{c['change_5m']:.2f}"
        )

        print(
            f"   Likidite: "
            f"${c['liquidity']:,.0f}"
        )

        print(
            f"   24H hacim: "
            f"${c['volume_24h']:,.0f}"
        )

        print(
            f"   1H hacim: "
            f"${c['volume_1h']:,.0f}"
        )

        print(
            f"   5M hacim: "
            f"${c['volume_5m']:,.0f}"
        )

        print(
            "   Buy/Sell 24H:",
            int(c["buys_24h"]),
            "/",
            int(c["sells_24h"])
        )

        print(
            "   Buy/Sell 1H:",
            int(c["buys_1h"]),
            "/",
            int(c["sells_1h"])
        )

        print(
            "   Buy/Sell 5M:",
            int(c["buys_5m"]),
            "/",
            int(c["sells_5m"])
        )

        print(
            f"   Hacim/Likidite: "
            f"{c['volume_liquidity_ratio']:.2f}x"
        )

        if c["is_new_launch"]:

            print(
                "   Hacim ivmesi: "
                "YENI POOL - hesaplanmadi"
            )

        else:

            print(
                f"   1H hacim ivmesi: "
                f"{c['acceleration_1h']:.2f}x"
            )

            print(
                f"   5M hacim ivmesi: "
                f"{c['acceleration_5m']:.2f}x"
            )

        print(
            "   Token kontrati:"
        )

        print(
            "  ",
            c["token_contract"]
        )
            if c.get("network_id") == "solana":
                quote = jupiter_quote(
                    c["token_contract"],
                    USDC_MINT,
                    1_000_000
                )

                print("   Jupiter:")

                if quote["ok"]:
                    print(
                        "   Route:",
                        "VAR" if quote["route_plan"] else "YOK"
                    )
                    print(
                        "   Price impact:",
                        quote["price_impact_pct"]
                    )
                    print(
                        "   Out amount:",
                        quote["out_amount"]
                    )
                else:
                    print(
                        "   Jupiter hata:",
                        quote["error"]
                    )
        print(
            "   Pool:"
        )

        print(
            "  ",
            c["pool"]
        )

        print(
            "-" * 72
        )
