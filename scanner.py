import os
import requests
import time
from datetime import datetime, timezone

from snapshot_deposu import snapshot_kaydet, son_snapshot, snapshot_sayisi

# ============================================================
# AVCI 2 V3 — COMPLETE CORE
# ============================================================
# Ana kaynak: GeckoTerminal
# Solana exit quote: Jupiter
# Solana on-chain risk: Solana RPC (+ varsa Helius)
# EVM token security: GoPlus (varsa/erisilebilirse)
#
# Not:
# - Wallet funding graph / gercek Sybil tespiti, yalnızca holder sayısı ile
#   dürüstçe yapılamaz. Helius anahtarı varsa holder örneklemi alınır;
#   funding graph "hazır değil" olarak açıkça raporlanır.
# - 24–72 saat outcome/triple-barrier için kalıcı snapshot geçmişi gerekir.
#   Mevcut snapshot_deposu aynen korunur; bu scanner tüm yeni alanları
#   candidate sözlüğüne koyup snapshot_kaydet'e gönderir.
# ============================================================

BASE_URL = "https://api.geckoterminal.com/api/v2"

NETWORKS = {
    "solana": "Solana",
    "base": "Base",
    "bsc": "BSC",
    "eth": "Ethereum",
    "arbitrum": "Arbitrum",
}

EVM_CHAIN_IDS = {
    "eth": "1",
    "bsc": "56",
    "arbitrum": "42161",
    "base": "8453",
}

JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
SOLANA_PUBLIC_RPC = os.getenv(
    "SOLANA_RPC_URL",
    "https://api.mainnet-beta.solana.com"
)

GOPLUS_ACCESS_TOKEN = os.getenv("GOPLUS_ACCESS_TOKEN", "")

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6

CONFIG_VERSION = "v3.0-complete-core-20260921"

# ------------------------------------------------------------
# TEMEL EVREN / TRADABILITY
# ------------------------------------------------------------

MIN_LIQUIDITY = 15000
MAX_LIQUIDITY = 500000

MIN_VOLUME_24H = 30000

MIN_CHANGE_24H = 5
MAX_CHANGE_24H = 40

MIN_VOLUME_1H = 1000
MIN_VOLUME_5M = 100

MIN_TX_1H = 15
MIN_TX_5M = 3

VETO_CHANGE_1H = -12
VETO_CHANGE_6H = -25
VETO_CHANGE_5M = -8

NEW_LAUNCH_MINUTES = 60
MAX_POOL_AGE_MINUTES = 43200  # 30 gun

MAX_OUTPUT_CANDIDATES = 5
SECURITY_ENRICH_LIMIT = 10

# ------------------------------------------------------------
# CLIMAX / TRAP / EXIT
# Bunlar v3 icin sabit, deterministik esiklerdir.
# ------------------------------------------------------------

CLIMAX_CHANGE_24H = 32
CLIMAX_TURNOVER = 6.0
CLIMAX_SELL_DOMINANCE_5M = 1.50

SUSPICIOUS_TURNOVER = 10.0
SUSPICIOUS_BUY_RATIO = 4.0

MAX_EXIT_LOSS_1K_PCT = 7.0
MAX_EXIT_LOSS_5K_PCT = 12.0

SOL_TOP1_WARN_PCT = 40.0
SOL_TOP5_WARN_PCT = 70.0
SOL_TOP10_WARN_PCT = 85.0

REQUEST_TIMEOUT = 25

HEADERS = {
    "accept": "application/json;version=20230203"
}


# ============================================================
# GENEL YARDIMCILAR
# ============================================================

def num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


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


def pct(part, whole):
    whole = num(whole)

    if whole <= 0:
        return None

    return (num(part) / whole) * 100.0


def bool01(value):
    if value in (True, 1, "1", "true", "True"):
        return True

    if value in (False, 0, "0", "false", "False"):
        return False

    return None


def api_get(path):
    url = f"{BASE_URL}{path}"

    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
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


def json_rpc(url, method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": "avci2",
        "method": method,
        "params": params,
    }

    for attempt in range(2):
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )

            if r.status_code == 429:
                if attempt == 0:
                    time.sleep(3)
                    continue

            r.raise_for_status()
            data = r.json()

            if data.get("error"):
                return {
                    "ok": False,
                    "error": str(data["error"]),
                    "result": None,
                }

            return {
                "ok": True,
                "error": None,
                "result": data.get("result"),
            }

        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue

            return {
                "ok": False,
                "error": str(e),
                "result": None,
            }

    return {
        "ok": False,
        "error": "RPC bilinmeyen hata",
        "result": None,
    }


# ============================================================
# JUPITER — GERCEK CIKIS MOTORU
# ============================================================

def jupiter_quote(input_mint, output_mint, amount):
    if not JUPITER_API_KEY:
        return {
            "ok": False,
            "error": "JUPITER_API_KEY yok"
        }

    if int_or_zero(amount) <= 0:
        return {
            "ok": False,
            "error": "Jupiter amount <= 0"
        }

    try:
        r = requests.get(
            JUPITER_QUOTE_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(int(amount)),
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


def quote_exit_metrics(quote, intended_usd):
    if not quote.get("ok"):
        return {
            "ok": False,
            "route": False,
            "out_usd": None,
            "price_impact_pct": None,
            "loss_pct": None,
            "error": quote.get("error"),
        }

    out_amount = num(quote.get("out_amount"))
    out_usd = out_amount / (10 ** USDC_DECIMALS)

    loss_pct = None

    if intended_usd > 0:
        loss_pct = (
            (intended_usd - out_usd)
            / intended_usd
        ) * 100.0

    return {
        "ok": True,
        "route": bool(quote.get("route_plan")),
        "out_usd": out_usd,
        "price_impact_pct": num(
            quote.get("price_impact_pct")
        ),
        "loss_pct": loss_pct,
        "error": None,
    }


# ============================================================
# HELIUS — OPSIYONEL SOLANA ZENGINLESTIRME
# ============================================================

def helius_rpc_url():
    if not HELIUS_API_KEY:
        return None

    return (
        "https://mainnet.helius-rpc.com/"
        f"?api-key={HELIUS_API_KEY}"
    )


def helius_get_asset(mint):
    url = helius_rpc_url()

    if not url:
        return {
            "ok": False,
            "error": "HELIUS_API_KEY yok",
        }

    res = json_rpc(
        url,
        "getAsset",
        {
            "id": mint,
            "displayOptions": {
                "showFungible": True
            }
        }
    )

    if not res["ok"]:
        return res

    asset = res.get("result") or {}
    token_info = asset.get("token_info") or {}
    price_info = token_info.get("price_info") or {}

    return {
        "ok": True,
        "error": None,
        "decimals": int_or_zero(
            token_info.get("decimals")
        ),
        "supply": int_or_zero(
            token_info.get("supply")
        ),
        "price_usd": num(
            price_info.get("price_per_token")
        ),
        "token_program": token_info.get("token_program"),
        "interface": asset.get("interface"),
    }


def helius_holder_sample(mint):
    url = helius_rpc_url()

    if not url:
        return {
            "ok": False,
            "error": "HELIUS_API_KEY yok",
            "accounts": None,
            "unique_owners": None,
            "truncated": None,
        }

    res = json_rpc(
        url,
        "getTokenAccounts",
        {
            "page": 1,
            "limit": 1000,
            "displayOptions": {},
            "mint": mint,
        }
    )

    if not res["ok"]:
        return {
            "ok": False,
            "error": res.get("error"),
            "accounts": None,
            "unique_owners": None,
            "truncated": None,
        }

    result = res.get("result") or {}
    accounts = result.get("token_accounts") or []

    owners = {
        item.get("owner")
        for item in accounts
        if item.get("owner")
        and int_or_zero(item.get("amount")) > 0
    }

    return {
        "ok": True,
        "error": None,
        "accounts": len(accounts),
        "unique_owners": len(owners),
        "truncated": len(accounts) >= 1000,
    }


# ============================================================
# SOLANA RPC — CONTRACT / HOLDER CONCENTRATION RISK
# ============================================================

def solana_rpc_url():
    return helius_rpc_url() or SOLANA_PUBLIC_RPC


def solana_mint_profile(mint):
    result = {
        "ok": False,
        "error": None,
        "decimals": 0,
        "supply_raw": 0,
        "mint_authority_active": None,
        "freeze_authority_active": None,
        "top1_pct": None,
        "top5_pct": None,
        "top10_pct": None,
        "largest_accounts_ok": False,
        "helius_holder_sample_ok": False,
        "holder_sample_unique": None,
        "holder_sample_accounts": None,
        "holder_sample_truncated": None,
    }

    rpc_url = solana_rpc_url()

    account_res = json_rpc(
        rpc_url,
        "getAccountInfo",
        [
            mint,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
            }
        ]
    )

    if account_res["ok"]:
        value = (account_res.get("result") or {}).get("value")
        data = (value or {}).get("data") or {}

        if isinstance(data, dict):
            parsed = data.get("parsed") or {}
            info = parsed.get("info") or {}

            result["decimals"] = int_or_zero(
                info.get("decimals")
            )

            result["supply_raw"] = int_or_zero(
                info.get("supply")
            )

            result["mint_authority_active"] = (
                info.get("mintAuthority") is not None
            )

            result["freeze_authority_active"] = (
                info.get("freezeAuthority") is not None
            )

            result["ok"] = True

    else:
        result["error"] = account_res.get("error")

    largest_res = json_rpc(
        rpc_url,
        "getTokenLargestAccounts",
        [
            mint,
            {
                "commitment": "confirmed"
            }
        ]
    )

    if largest_res["ok"]:
        rows = (
            (largest_res.get("result") or {})
            .get("value")
            or []
        )

        amounts = [
            int_or_zero(row.get("amount"))
            for row in rows
        ]

        supply = result["supply_raw"]

        if supply > 0 and amounts:
            result["top1_pct"] = pct(
                sum(amounts[:1]),
                supply
            )
            result["top5_pct"] = pct(
                sum(amounts[:5]),
                supply
            )
            result["top10_pct"] = pct(
                sum(amounts[:10]),
                supply
            )
            result["largest_accounts_ok"] = True

    # Helius varsa metadata + holder sample ile zenginlestir.
    if HELIUS_API_KEY:
        asset = helius_get_asset(mint)

        if asset.get("ok"):
            if result["decimals"] <= 0:
                result["decimals"] = int_or_zero(
                    asset.get("decimals")
                )

            if result["supply_raw"] <= 0:
                result["supply_raw"] = int_or_zero(
                    asset.get("supply")
                )

        holders = helius_holder_sample(mint)

        if holders.get("ok"):
            result["helius_holder_sample_ok"] = True
            result["holder_sample_unique"] = (
                holders.get("unique_owners")
            )
            result["holder_sample_accounts"] = (
                holders.get("accounts")
            )
            result["holder_sample_truncated"] = (
                holders.get("truncated")
            )

    return result


# ============================================================
# GOPLUS — EVM CONTRACT SECURITY
# ============================================================

def goplus_security(network_id, contract):
    chain_id = EVM_CHAIN_IDS.get(network_id)

    if not chain_id:
        return {
            "ok": False,
            "error": "Desteklenmeyen EVM chain",
        }

    url = (
        "https://api.gopluslabs.io/api/v1/"
        f"token_security/{chain_id}"
    )

    headers = {
        "accept": "application/json"
    }

    if GOPLUS_ACCESS_TOKEN:
        headers["Authorization"] = (
            f"Bearer {GOPLUS_ACCESS_TOKEN}"
        )

    try:
        r = requests.get(
            url,
            params={
                "contract_addresses": contract
            },
            headers=headers,
            timeout=20,
        )

        if r.status_code in (401, 403):
            return {
                "ok": False,
                "error": (
                    "GoPlus auth gerekli veya yetki yok"
                ),
            }

        r.raise_for_status()
        data = r.json()

        result = data.get("result") or {}

        token = (
            result.get(contract.lower())
            or result.get(contract)
            or {}
        )

        if not token:
            return {
                "ok": False,
                "error": "GoPlus token sonucu bos",
            }

        fields = {
            "is_honeypot": bool01(
                token.get("is_honeypot")
            ),
            "is_open_source": bool01(
                token.get("is_open_source")
            ),
            "is_proxy": bool01(
                token.get("is_proxy")
            ),
            "hidden_owner": bool01(
                token.get("hidden_owner")
            ),
            "can_take_back_ownership": bool01(
                token.get("can_take_back_ownership")
            ),
            "owner_change_balance": bool01(
                token.get("owner_change_balance")
            ),
            "selfdestruct": bool01(
                token.get("selfdestruct")
            ),
            "external_call": bool01(
                token.get("external_call")
            ),
            "is_blacklisted": bool01(
                token.get("is_blacklisted")
            ),
            "transfer_pausable": bool01(
                token.get("transfer_pausable")
            ),
            "slippage_modifiable": bool01(
                token.get("slippage_modifiable")
            ),
            "cannot_sell_all": bool01(
                token.get("cannot_sell_all")
            ),
            "buy_tax": num(
                token.get("buy_tax")
            ),
            "sell_tax": num(
                token.get("sell_tax")
            ),
        }

        return {
            "ok": True,
            "error": None,
            **fields,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }


# ============================================================
# GECKOTERMINAL PARSING
# ============================================================

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
        "decimals": int_or_zero(
            attrs.get("decimals")
        ),
    }


# ============================================================
# STAGE / ENGINE / MOTORLAR
# ============================================================

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


def classify_engine(c):
    if c["is_new_launch"]:
        return "NEW_LAUNCH_FLOW"

    if (
        c["acceleration_1h"] is not None
        and c["acceleration_1h"] >= 1.5
        and c["buy_sell_ratio_1h"] >= 1.2
    ):
        if (
            c["acceleration_5m"] is not None
            and c["acceleration_5m"] >= 1.3
            and c["buy_sell_ratio_5m"] >= 1.2
        ):
            return "RE_IGNITION_FLOW"

        return "VOLUME_WAKE_UP"

    if (
        c["buy_sell_ratio_24h"] >= 1.4
        and c["buy_sell_ratio_1h"] >= 1.1
    ):
        return "BUY_PRESSURE"

    if (
        c["volume_liquidity_ratio"] >= 4
        and c["liquidity"] <= 60000
    ):
        return "LIQUIDITY_VACUUM_RISK"

    return "MOMENTUM_CONTINUATION"


def compute_climax(c):
    reasons = []

    if c["change_24h"] >= CLIMAX_CHANGE_24H:
        reasons.append("24H_MOVE_HIGH")

    if (
        c["volume_liquidity_ratio"]
        >= CLIMAX_TURNOVER
    ):
        reasons.append("TURNOVER_EXTREME")

    if (
        c["sells_5m"] >
        c["buys_5m"] * CLIMAX_SELL_DOMINANCE_5M
        and c["change_5m"] < 0
    ):
        reasons.append("5M_SELL_DOMINANCE")

    return {
        "risk": len(reasons) >= 2,
        "points": len(reasons),
        "reasons": reasons,
    }


def compute_trap_proxy(c):
    reasons = []

    # Bu bir "wash trade kaniti" degildir.
    # Sadece anormal turnover / akış örüntüsü proxy'sidir.
    if (
        c["volume_liquidity_ratio"]
        >= SUSPICIOUS_TURNOVER
    ):
        reasons.append("EXTREME_TURNOVER")

    if (
        c["buy_sell_ratio_24h"]
        >= SUSPICIOUS_BUY_RATIO
        and c["volume_24h"] >= 100000
    ):
        reasons.append("BUY_RATIO_EXTREME")

    if (
        c["tx_count_5m"] > 0
        and c["volume_5m"] > 0
    ):
        avg_trade_5m = (
            c["volume_5m"]
            / c["tx_count_5m"]
        )

        if avg_trade_5m < 1:
            reasons.append("MICRO_TX_PATTERN")

    return {
        "risk": len(reasons) >= 2,
        "points": len(reasons),
        "reasons": reasons,
    }


def compute_models(c):
    is_new = c["is_new_launch"]

    wake_up = (
        not is_new
        and c["acceleration_1h"] is not None
        and c["acceleration_1h"] >= 1.5
        and c["buy_sell_ratio_1h"] > 1
    )

    persistence = (
        c["change_1h"] >= 0
        and c["buy_sell_ratio_1h"] > 1
        and c["volume_1h"] >= MIN_VOLUME_1H
    )

    re_ignition = (
        not is_new
        and c["acceleration_5m"] is not None
        and c["acceleration_5m"] >= 1.3
        and c["buy_sell_ratio_5m"] >= 1.2
        and c["change_5m"] >= 0
    )

    trigger = (
        c["buy_sell_ratio_5m"] >= 1.2
        and c["change_5m"] >= 0
        and c["tx_count_5m"] >= MIN_TX_5M
    )

    # Gercek retention icin ilk hareketin tepe/dip path'i gerekir.
    # Burada yalnizca mevcut yapı proxy olarak etiketlenir.
    retention_proxy = (
        c["change_24h"] > 0
        and c["change_6h"] > -5
        and c["change_1h"] > -3
    )

    return {
        "wake_up": wake_up,
        "persistence": persistence,
        "re_ignition": re_ignition,
        "trigger": trigger,
        "retention_proxy": retention_proxy,
    }


def compute_15_motors(c):
    motors = {
        "M01_AGE_FIT": (
            c["age_minutes"] is None
            or c["age_minutes"] <= MAX_POOL_AGE_MINUTES
        ),
        "M02_LIQUIDITY_FIT": (
            MIN_LIQUIDITY
            <= c["liquidity"]
            <= MAX_LIQUIDITY
        ),
        "M03_VOLUME_24H": (
            c["volume_24h"] >= MIN_VOLUME_24H
        ),
        "M04_EARLY_MOVE_ZONE": (
            MIN_CHANGE_24H
            <= c["change_24h"]
            <= MAX_CHANGE_24H
        ),
        "M05_BUY_PRESSURE_24H": (
            c["buy_sell_ratio_24h"] > 1
        ),
        "M06_BUY_PRESSURE_1H": (
            c["buy_sell_ratio_1h"] > 1
        ),
        "M07_BUY_PRESSURE_5M": (
            c["buy_sell_ratio_5m"] > 1
        ),
        "M08_LIVE_VOLUME_1H": (
            c["is_new_launch"]
            or c["volume_1h"] >= MIN_VOLUME_1H
        ),
        "M09_LIVE_VOLUME_5M": (
            c["volume_5m"] >= MIN_VOLUME_5M
        ),
        "M10_TX_ACTIVITY_1H": (
            c["is_new_launch"]
            or c["tx_count_1h"] >= MIN_TX_1H
        ),
        "M11_TX_ACTIVITY_5M": (
            c["tx_count_5m"] >= MIN_TX_5M
        ),
        "M12_TURNOVER": (
            c["volume_liquidity_ratio"] >= 0.5
        ),
        "M13_ACCELERATION_1H": (
            None
            if c["is_new_launch"]
            else (
                c["acceleration_1h"] is not None
                and c["acceleration_1h"] >= 1.5
            )
        ),
        "M14_ACCELERATION_5M": (
            None
            if c["is_new_launch"]
            else (
                c["acceleration_5m"] is not None
                and c["acceleration_5m"] >= 1.3
            )
        ),
        "M15_MOMENTUM_STRUCTURE": (
            c["change_1h"] >= 0
            and c["change_5m"] >= -1
        ),
    }

    available = [
        value
        for value in motors.values()
        if value is not None
    ]

    passed = sum(
        1
        for value in available
        if value is True
    )

    return {
        "flags": motors,
        "passed": passed,
        "available": len(available),
    }


# ============================================================
# ANA PAYLOAD TARAMA
# ============================================================

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

        if not token_contract:
            continue

        # ----------------------------------------------------
        # TEMEL FILTRELER
        # ----------------------------------------------------

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

        if (
            age_minutes is not None
            and age_minutes > MAX_POOL_AGE_MINUTES
        ):
            continue

        # ----------------------------------------------------
        # SHORT-TERM CRASH VETO
        # ----------------------------------------------------

        veto_reason = None

        if change_1h <= VETO_CHANGE_1H:
            veto_reason = "1H_CRASH"

        if change_6h <= VETO_CHANGE_6H:
            veto_reason = "6H_CRASH"

        if change_5m <= VETO_CHANGE_5M:
            veto_reason = "5M_CRASH"

        if veto_reason:
            continue

        # ----------------------------------------------------
        # YENI POOL / CANLI AKTIVITE
        # ----------------------------------------------------

        is_new_launch = (
            age_minutes is not None
            and age_minutes
            < NEW_LAUNCH_MINUTES
        )

        if is_new_launch:
            if volume_5m < MIN_VOLUME_5M:
                continue

            if tx_count_5m < MIN_TX_5M:
                continue

            if buys_5m <= sells_5m:
                continue

        else:
            if volume_1h < MIN_VOLUME_1H:
                continue

            if tx_count_1h < MIN_TX_1H:
                continue

            if volume_5m < MIN_VOLUME_5M:
                continue

            if tx_count_5m < MIN_TX_5M:
                continue

        # ----------------------------------------------------
        # RATIOS / ACCELERATION
        # ----------------------------------------------------

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

        if is_new_launch:
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

        # ----------------------------------------------------
        # AVCI SCORE / STAGE
        # ----------------------------------------------------

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
            if buys_5m > sells_5m:
                score += 1

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

        candidate = {
            "network": network_name,
            "network_id": network_id,
            "source": source,
            "name": (
                base_token.get("name")
                or pool_name
            ),
            "symbol": base_token.get(
                "symbol",
                ""
            ),
            "token_contract": token_contract,
            "pool": pool_address,
            "quote_symbol": quote_token.get(
                "symbol",
                ""
            ),
            "created_at": created_at,
            "age_minutes": age_minutes,
            "stage": stage,
            "score": score,
            "liquidity": liquidity,
            "price_usd": price_usd,
            "decimals": base_token.get(
                "decimals",
                0
            ),
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
            "tx_count_1h": tx_count_1h,
            "tx_count_5m": tx_count_5m,
            "buy_sell_ratio_24h":
                buy_sell_ratio_24h,
            "buy_sell_ratio_1h":
                buy_sell_ratio_1h,
            "buy_sell_ratio_5m":
                buy_sell_ratio_5m,
            "volume_liquidity_ratio":
                volume_liquidity_ratio,
            "acceleration_1h":
                acceleration_1h,
            "acceleration_5m":
                acceleration_5m,
            "is_new_launch":
                is_new_launch,
        }

        candidate["models"] = compute_models(
            candidate
        )

        candidate["climax"] = compute_climax(
            candidate
        )

        candidate["trap_proxy"] = (
            compute_trap_proxy(candidate)
        )

        candidate["engine"] = classify_engine(
            candidate
        )

        motor_info = compute_15_motors(
            candidate
        )

        candidate["motors"] = motor_info["flags"]
        candidate["motor_passed"] = (
            motor_info["passed"]
        )
        candidate["motor_available"] = (
            motor_info["available"]
        )

        found.append(candidate)

    return found


# ============================================================
# DEDUP
# ============================================================

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

        new_rank = (
            c["score"],
            c["motor_passed"],
            c["liquidity"]
        )

        old_rank = (
            old["score"],
            old["motor_passed"],
            old["liquidity"]
        )

        if new_rank > old_rank:
            result[key] = c

    return list(
        result.values()
    )


# ============================================================
# NETWORK-SPECIFIC SECURITY + EXIT
# ============================================================

def enrich_solana_candidate(c):
    mint = c["token_contract"]

    profile = solana_mint_profile(mint)
    c["solana_security"] = profile

    if int_or_zero(c.get("decimals")) <= 0:
        c["decimals"] = int_or_zero(
            profile.get("decimals")
        )

    # Helius price varsa yalnızca Gecko fiyatı yoksa fallback.
    if num(c.get("price_usd")) <= 0 and HELIUS_API_KEY:
        asset = helius_get_asset(mint)

        if asset.get("ok"):
            c["price_usd"] = num(
                asset.get("price_usd")
            )

            if int_or_zero(c.get("decimals")) <= 0:
                c["decimals"] = int_or_zero(
                    asset.get("decimals")
                )

    price_usd = num(c.get("price_usd"))
    decimals = int_or_zero(
        c.get("decimals")
    )

    c["exit_1k"] = {
        "ok": False,
        "error": "fiyat veya decimals yok",
    }

    c["exit_5k"] = {
        "ok": False,
        "error": "fiyat veya decimals yok",
    }

    if price_usd > 0 and decimals > 0:
        amount_1000 = int(
            (1000 / price_usd)
            * (10 ** decimals)
        )

        amount_5000 = int(
            (5000 / price_usd)
            * (10 ** decimals)
        )

        quote_1k = jupiter_quote(
            mint,
            USDC_MINT,
            amount_1000
        )

        quote_5k = jupiter_quote(
            mint,
            USDC_MINT,
            amount_5000
        )

        c["exit_1k"] = quote_exit_metrics(
            quote_1k,
            1000
        )

        c["exit_5k"] = quote_exit_metrics(
            quote_5k,
            5000
        )

    risk_reasons = []

    if profile.get("mint_authority_active"):
        risk_reasons.append(
            "MINT_AUTHORITY_ACTIVE"
        )

    if profile.get("freeze_authority_active"):
        risk_reasons.append(
            "FREEZE_AUTHORITY_ACTIVE"
        )

    top1 = profile.get("top1_pct")
    top5 = profile.get("top5_pct")
    top10 = profile.get("top10_pct")

    if top1 is not None and top1 >= SOL_TOP1_WARN_PCT:
        risk_reasons.append(
            "TOP1_CONCENTRATION"
        )

    if top5 is not None and top5 >= SOL_TOP5_WARN_PCT:
        risk_reasons.append(
            "TOP5_CONCENTRATION"
        )

    if (
        top10 is not None
        and top10 >= SOL_TOP10_WARN_PCT
    ):
        risk_reasons.append(
            "TOP10_CONCENTRATION"
        )

    exit1 = c.get("exit_1k") or {}
    exit5 = c.get("exit_5k") or {}

    if exit1.get("ok"):
        loss = exit1.get("loss_pct")

        if (
            loss is not None
            and loss > MAX_EXIT_LOSS_1K_PCT
        ):
            risk_reasons.append(
                "EXIT_1K_LOSS_HIGH"
            )

    if exit5.get("ok"):
        loss = exit5.get("loss_pct")

        if (
            loss is not None
            and loss > MAX_EXIT_LOSS_5K_PCT
        ):
            risk_reasons.append(
                "EXIT_5K_LOSS_HIGH"
            )

    c["security_risk_reasons"] = risk_reasons

    c["wallet_quality"] = {
        "status": (
            "HOLDER_SAMPLE_ONLY"
            if profile.get("helius_holder_sample_ok")
            else "TX_GRAPH_NOT_AVAILABLE"
        ),
        "holder_sample_unique":
            profile.get("holder_sample_unique"),
        "holder_sample_accounts":
            profile.get("holder_sample_accounts"),
        "holder_sample_truncated":
            profile.get("holder_sample_truncated"),
        "note": (
            "Holder sayisi Sybil/funding graph "
            "tespiti degildir."
        ),
    }


def enrich_evm_candidate(c):
    sec = goplus_security(
        c["network_id"],
        c["token_contract"]
    )

    c["evm_security"] = sec
    c["security_risk_reasons"] = []

    if not sec.get("ok"):
        c["security_risk_reasons"].append(
            "SECURITY_API_UNAVAILABLE"
        )
        return

    hard_fields = [
        "is_honeypot",
        "hidden_owner",
        "can_take_back_ownership",
        "owner_change_balance",
        "selfdestruct",
        "is_blacklisted",
    ]

    for field in hard_fields:
        if sec.get(field) is True:
            c["security_risk_reasons"].append(
                field.upper()
            )

    if sec.get("is_open_source") is False:
        c["security_risk_reasons"].append(
            "NOT_OPEN_SOURCE"
        )

    if num(sec.get("sell_tax")) >= 0.10:
        c["security_risk_reasons"].append(
            "SELL_TAX_HIGH"
        )

    if num(sec.get("buy_tax")) >= 0.10:
        c["security_risk_reasons"].append(
            "BUY_TAX_HIGH"
        )


def enrich_candidate(c):
    if c["network_id"] == "solana":
        enrich_solana_candidate(c)

    elif c["network_id"] in EVM_CHAIN_IDS:
        enrich_evm_candidate(c)

    else:
        c["security_risk_reasons"] = [
            "NO_SECURITY_ENGINE"
        ]

    # Nihai kalite etiketi; yatırım kararı degildir.
    risk_points = 0

    risk_points += len(
        c.get("security_risk_reasons") or []
    )

    risk_points += int(
        (c.get("climax") or {}).get(
            "points",
            0
        )
    )

    risk_points += int(
        (c.get("trap_proxy") or {}).get(
            "points",
            0
        )
    )

    if risk_points == 0:
        c["risk_band"] = "LOW_FLAGS"
    elif risk_points <= 2:
        c["risk_band"] = "MEDIUM_FLAGS"
    else:
        c["risk_band"] = "HIGH_FLAGS"

    return c


# ============================================================
# OUTPUT
# ============================================================

def yesno(value):
    if value is True:
        return "EVET"

    if value is False:
        return "HAYIR"

    return "BILINMIYOR"


def fmt_pct(value):
    if value is None:
        return "N/A"

    return f"%{value:.2f}"


def print_exit(label, data):
    print(f"   {label}:")

    if not data or not data.get("ok"):
        print(
            "      Durum: HATA/PASIF -",
            (data or {}).get(
                "error",
                "bilinmeyen"
            )
        )
        return

    print(
        "      Route:",
        "VAR" if data.get("route")
        else "YOK"
    )

    print(
        "      Cikis USD:",
        (
            f"${data['out_usd']:,.2f}"
            if data.get("out_usd") is not None
            else "N/A"
        )
    )

    print(
        "      Price impact:",
        fmt_pct(
            data.get("price_impact_pct")
        )
    )

    print(
        "      Tahmini kayip:",
        fmt_pct(
            data.get("loss_pct")
        )
    )


def print_candidate(i, c):
    print()
    print(
        f"{i}. {c['name']} ({c['symbol']})"
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
        "   Engine:",
        c["engine"]
    )

    print(
        "   Source:",
        c["source"]
    )

    print(
        f"   Avci score: {c['score']}/8"
    )

    print(
        "   15 motor:",
        f"{c['motor_passed']}/"
        f"{c['motor_available']}"
    )

    models = c.get("models") or {}

    print(
        "   Wake-up:",
        yesno(models.get("wake_up"))
    )

    print(
        "   Persistence:",
        yesno(models.get("persistence"))
    )

    print(
        "   Re-ignition:",
        yesno(models.get("re_ignition"))
    )

    print(
        "   Trigger:",
        yesno(models.get("trigger"))
    )

    print(
        "   Retention proxy:",
        yesno(
            models.get("retention_proxy")
        )
    )

    print(
        "   Climax risk:",
        yesno(
            (c.get("climax") or {})
            .get("risk")
        )
    )

    print(
        "   Trap/wash proxy:",
        yesno(
            (c.get("trap_proxy") or {})
            .get("risk")
        )
    )

    print(
        "   Risk band:",
        c.get("risk_band")
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

    risk_reasons = (
        c.get("security_risk_reasons")
        or []
    )

    print(
        "   Security flags:",
        (
            ", ".join(risk_reasons)
            if risk_reasons
            else "YOK"
        )
    )

    if c["network_id"] == "solana":
        sol = c.get("solana_security") or {}

        print(
            "   Mint authority:",
            yesno(
                sol.get(
                    "mint_authority_active"
                )
            )
        )

        print(
            "   Freeze authority:",
            yesno(
                sol.get(
                    "freeze_authority_active"
                )
            )
        )

        print(
            "   Top1/Top5/Top10:",
            fmt_pct(sol.get("top1_pct")),
            "/",
            fmt_pct(sol.get("top5_pct")),
            "/",
            fmt_pct(sol.get("top10_pct")),
        )

        wallet = c.get("wallet_quality") or {}

        print(
            "   Wallet-quality:",
            wallet.get(
                "status",
                "N/A"
            )
        )

        if (
            wallet.get(
                "holder_sample_unique"
            )
            is not None
        ):
            suffix = (
                "+"
                if wallet.get(
                    "holder_sample_truncated"
                )
                else ""
            )

            print(
                "   Holder sample unique:",
                f"{wallet['holder_sample_unique']}"
                f"{suffix}"
            )

        print_exit(
            "Jupiter $1K",
            c.get("exit_1k")
        )

        print_exit(
            "Jupiter $5K",
            c.get("exit_5k")
        )

    elif c["network_id"] in EVM_CHAIN_IDS:
        sec = c.get("evm_security") or {}

        print(
            "   EVM security API:",
            (
                "OK"
                if sec.get("ok")
                else (
                    "PASIF/HATA - "
                    + str(
                        sec.get("error")
                    )
                )
            )
        )

        if sec.get("ok"):
            print(
                "   Honeypot:",
                yesno(
                    sec.get("is_honeypot")
                )
            )

            print(
                "   Buy/Sell tax:",
                fmt_pct(
                    num(sec.get("buy_tax"))
                    * 100
                ),
                "/",
                fmt_pct(
                    num(sec.get("sell_tax"))
                    * 100
                ),
            )

    print(
        "   Token kontrati:"
    )

    print(
        "  ",
        c["token_contract"]
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


# ============================================================
# MAIN
# ============================================================

print("=" * 72)
print("AVCI 2 V3 — COMPLETE CORE")
print("CONFIG:", CONFIG_VERSION)

print(
    "UTC:",
    datetime.now(
        timezone.utc
    ).isoformat()
)

print(
    "Jupiter:",
    "AKTIF" if JUPITER_API_KEY
    else "PASIF"
)

print(
    "Helius:",
    "AKTIF" if HELIUS_API_KEY
    else "PASIF (opsiyonel)"
)

print(
    "GoPlus token:",
    (
        "AUTH VAR"
        if GOPLUS_ACCESS_TOKEN
        else "AUTH YOK - public denenecek"
    )
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

# Once base-filtered, zenginlestirme pahali API'leri sadece ilk grup icin.
all_candidates.sort(
    key=lambda x: (
        x["score"],
        x["motor_passed"],
        x["volume_liquidity_ratio"]
    ),
    reverse=True
)

for c in all_candidates[:SECURITY_ENRICH_LIMIT]:
    enrich_candidate(c)

# Zenginlestirilmeyenler de snapshotta kaybolmasin.
for c in all_candidates[SECURITY_ENRICH_LIMIT:]:
    c["security_risk_reasons"] = [
        "NOT_ENRICHED_LIMIT"
    ]
    c["risk_band"] = "UNKNOWN"

# Tüm adaylar snapshot'a yazilir.
for candidate in all_candidates:
    snapshot_kaydet(
        candidate,
        CONFIG_VERSION
    )

print(
    "Snapshot toplam:",
    snapshot_sayisi()
)

# Output sıralaması: önce skor/motor, sonra daha az risk flag.
all_candidates.sort(
    key=lambda x: (
        x["score"],
        x["motor_passed"],
        -len(
            x.get(
                "security_risk_reasons"
            )
            or []
        ),
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
        "FILTRELENMIS ADAY BULUNDU"
    )

    print(
        f"Ilk {min(MAX_OUTPUT_CANDIDATES, len(all_candidates))} "
        "aday detayli gosteriliyor."
    )

    print("=" * 72)

    for i, c in enumerate(
        all_candidates[
            :MAX_OUTPUT_CANDIDATES
        ],
        1
    ):
        print_candidate(i, c)

print()
print("=" * 72)
print("AVCI 2 V3 TAMAMLANDI")
print(
    "Not: Retention gercek path metriği, "
    "funding-graph Sybil ve 24-72h triple-barrier "
    "kalici tarihsel veri gerektirir; "
    "scanner bunlari uydurmaz."
)
print("=" * 72)
