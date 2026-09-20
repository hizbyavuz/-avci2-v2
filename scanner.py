import requests
import time
from datetime import datetime, timezone

BASE_URL = "https://api.geckoterminal.com/api/v2"

NETWORKS = {
    "solana": "Solana",
    "base": "Base",
    "bsc": "BSC",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
}

MIN_LIQUIDITY = 15000
MAX_LIQUIDITY = 500000

MIN_VOLUME_24H = 30000

MIN_CHANGE_24H = 5
MAX_CHANGE_24H = 40

MAX_RESULTS_PER_NETWORK = 20

HEADERS = {
    "accept": "application/json;version=20230203"
}


def num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
                print("Rate limit -> bekleniyor...")
                time.sleep(15)
                continue

            r.raise_for_status()
            return r.json()

        except Exception as e:
            print(f"API hata: {url}")
            print(e)

            if attempt < 2:
                time.sleep(5)

    return {"data": [], "included": []}


def included_map(payload):
    result = {}

    for obj in payload.get("included", []):
        key = (obj.get("type"), obj.get("id"))
        result[key] = obj

    return result


def relation_id(pool, relation):
    try:
        return (
            pool["relationships"][relation]
            ["data"]["id"]
        )
    except Exception:
        return None


def token_from_included(pool, inc, relation):
    rid = relation_id(pool, relation)

    if not rid:
        return {}

    obj = inc.get(("token", rid))

    if not obj:
        return {}

    attrs = obj.get("attributes", {})

    return {
        "id": rid,
        "address": attrs.get("address", ""),
        "name": attrs.get("name", ""),
        "symbol": attrs.get("symbol", ""),
    }


def scan_payload(payload, network_id, network_name, source):
    inc = included_map(payload)
    found = []

    for pool in payload.get("data", []):
        a = pool.get("attributes", {})

        pool_address = a.get("address", "")

        pool_name = a.get("name", "Unknown")

        liquidity = num(a.get("reserve_in_usd"))

        volume = a.get("volume_usd", {})
        volume_24h = num(volume.get("h24"))
        volume_6h = num(volume.get("h6"))
        volume_1h = num(volume.get("h1"))
        volume_5m = num(volume.get("m5"))

        changes = a.get("price_change_percentage", {})
        change_24h = num(changes.get("h24"))
        change_6h = num(changes.get("h6"))
        change_1h = num(changes.get("h1"))
        change_5m = num(changes.get("m5"))

        tx = a.get("transactions", {})

        tx24 = tx.get("h24", {})
        tx1 = tx.get("h1", {})
        tx5 = tx.get("m5", {})

        buys_24h = num(tx24.get("buys"))
        sells_24h = num(tx24.get("sells"))

        buys_1h = num(tx1.get("buys"))
        sells_1h = num(tx1.get("sells"))

        buys_5m = num(tx5.get("buys"))
        sells_5m = num(tx5.get("sells"))

        created_at = a.get("pool_created_at")

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

        volume_liquidity_ratio = (
            volume_24h / liquidity
            if liquidity > 0
            else 0
        )

        buy_sell_ratio = (
            buys_24h / max(sells_24h, 1)
        )

        acceleration_1h = (
            volume_1h /
            max(volume_24h / 24, 1)
        )

        acceleration_5m = (
            volume_5m /
            max(volume_1h / 12, 1)
        )

        score = 0

        if buy_sell_ratio >= 1.2:
            score += 1

        if buy_sell_ratio >= 2:
            score += 1

        if volume_liquidity_ratio >= 1:
            score += 1

        if volume_liquidity_ratio >= 3:
            score += 1

        if acceleration_1h >= 1.5:
            score += 1

        if acceleration_5m >= 1.5:
            score += 1

        if buys_1h > sells_1h:
            score += 1

        if buys_5m > sells_5m:
            score += 1

        found.append({
            "network": network_name,
            "network_id": network_id,
            "source": source,

            "name": pool_name,

            "pool": pool_address,

            "base_symbol": base_token.get(
                "symbol", ""
            ),

            "base_name": base_token.get(
                "name", ""
            ),

            "token_contract": base_token.get(
                "address", ""
            ),

            "quote_symbol": quote_token.get(
                "symbol", ""
            ),

            "created_at": created_at,

            "liquidity": liquidity,

            "volume_24h": volume_24h,
            "volume_6h": volume_6h,
            "volume_1h": volume_1h,
            "volume_5m": volume_5m,

            "change_24h": change_24h,
            "change_6h": change_6h,
            "change_1h": change_1h,
            "change_5m": change_5m,

            "buys_24h": buys_24h,
            "sells_24h": sells_24h,

            "buys_1h": buys_1h,
            "sells_1h": sells_1h,

            "buys_5m": buys_5m,
            "sells_5m": sells_5m,

            "buy_sell_ratio": buy_sell_ratio,

            "volume_liquidity_ratio":
                volume_liquidity_ratio,

            "acceleration_1h":
                acceleration_1h,

            "acceleration_5m":
                acceleration_5m,

            "score": score,
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

        existing = result.get(key)

        if existing is None:
            result[key] = c
            continue

        if c["liquidity"] > existing["liquidity"]:
            result[key] = c

    return list(result.values())


print("=" * 70)
print("AVCI 2 V2 — MULTI-CHAIN SCAN")
print(
    "UTC:",
    datetime.now(timezone.utc).isoformat()
)
print("=" * 70)

all_candidates = []

for network_id, network_name in NETWORKS.items():

    print(f"\n[{network_name}] taraniyor...")

    trending = api_get(
        f"/networks/{network_id}/trending_pools"
        "?include=base_token,quote_token"
    )

    candidates = scan_payload(
        trending,
        network_id,
        network_name,
        "TRENDING"
    )

    all_candidates.extend(candidates)

    time.sleep(7)

    new_pools = api_get(
        f"/networks/{network_id}/new_pools"
        "?include=base_token,quote_token"
    )

    candidates = scan_payload(
        new_pools,
        network_id,
        network_name,
        "NEW"
    )

    all_candidates.extend(candidates)

    time.sleep(7)


all_candidates = deduplicate(
    all_candidates
)

all_candidates.sort(
    key=lambda x: (
        x["score"],
        x["acceleration_1h"],
        x["volume_liquidity_ratio"]
    ),
    reverse=True
)


print("\n" + "=" * 70)

if not all_candidates:

    print("0 TEMIZ AVCI 2 ADAYI")
    print(
        "0 aday da dogru bir sonuctur."
    )

else:

    print(
        f"{len(all_candidates)} ADAY BULUNDU"
    )

    print("=" * 70)

    for i, c in enumerate(
        all_candidates[:20],
        1
    ):

        print()
        print(
            f"{i}. "
            f"{c['base_name']} "
            f"({c['base_symbol']})"
        )

        print(
            f"   Network: {c['network']}"
        )

        print(
            f"   Kaynak: {c['source']}"
        )

        print(
            f"   Avci score: "
            f"{c['score']}/8"
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
            f"   Buy/Sell 24H: "
            f"{int(c['buys_24h'])}/"
            f"{int(c['sells_24h'])}"
        )

        print(
            f"   Buy/Sell 1H: "
            f"{int(c['buys_1h'])}/"
            f"{int(c['sells_1h'])}"
        )

        print(
            f"   Buy/Sell 5M: "
            f"{int(c['buys_5m'])}/"
            f"{int(c['sells_5m'])}"
        )

        print(
            f"   Hacim/Likidite: "
            f"{c['volume_liquidity_ratio']:.2f}x"
        )

        print(
            f"   1H hacim ivmesi: "
            f"{c['acceleration_1h']:.2f}x"
        )

        print(
            f"   5M hacim ivmesi: "
            f"{c['acceleration_5m']:.2f}x"
        )

        print(
            f"   Token kontrati:"
        )

        print(
            f"   {c['token_contract']}"
        )

        print(
            f"   Pool:"
        )

        print(
            f"   {c['pool']}"
        )

        print(
            f"   Pool created:"
        )

        print(
            f"   {c['created_at']}"
        )

        print("-" * 70)
