import os
import requests
import time
import sqlite3
from collections import Counter, defaultdict
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

ZEROX_API_KEY = os.getenv("ZEROX_API_KEY", "")

# 0x cikis testinde alinacak stabil token.
EVM_STABLES = {
    "eth": {
        "address": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "symbol": "USDC",
        "decimals": 6,
    },
    "base": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "symbol": "USDC",
        "decimals": 6,
    },
    "arbitrum": {
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "symbol": "USDC",
        "decimals": 6,
    },
    "bsc": {
        "address": "0x55d398326f99059fF775485246999027B3197955",
        "symbol": "USDT",
        "decimals": 18,
    },
}

OUTCOME_DB_PATH = os.getenv(
    "AVCI_OUTCOME_DB",
    "avci_outcomes.db"
)

OUTCOME_SIGNAL_COOLDOWN_HOURS = 24
OUTCOME_MAX_UPDATES_PER_RUN = 2
OUTCOME_TARGETS = (3, 5, 7, 10, 15)
OUTCOME_CLOSE_HOURS = 72

# Top-holder hesabinda sistem/LP/burn/borsa benzeri etiketleri disla.
EXCLUDED_HOLDER_TAG_WORDS = (
    "burn",
    "dead",
    "null",
    "locker",
    "locked",
    "liquidity",
    "pool",
    "raydium",
    "orca",
    "meteora",
    "pump",
    "program",
    "dex",
    "exchange",
    "cex",
    "bridge",
)

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6

CONFIG_VERSION = "v4.0-full-risk-outcome-20260921"

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
# V4 EK MOTORLAR
# LP LOCK/BURN, DUZELTILMIS HOLDER, EVM EXIT, TRADE CLUSTER,
# QUOTE TIMESTAMP, OUTCOME/MFE/MAE
# ============================================================

def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def is_excluded_holder_tag(tag):
    text = str(tag or "").strip().lower()

    if not text:
        return False

    return any(
        word in text
        for word in EXCLUDED_HOLDER_TAG_WORDS
    )


def goplus_headers():
    headers = {
        "accept": "application/json"
    }

    if GOPLUS_ACCESS_TOKEN:
        headers["Authorization"] = (
            f"Bearer {GOPLUS_ACCESS_TOKEN}"
        )

    return headers


def goplus_raw_evm(network_id, contract):
    chain_id = EVM_CHAIN_IDS.get(network_id)

    if not chain_id:
        return {
            "ok": False,
            "error": "Desteklenmeyen EVM chain",
            "data": None,
        }

    try:
        r = requests.get(
            (
                "https://api.gopluslabs.io/api/v1/"
                f"token_security/{chain_id}"
            ),
            params={
                "contract_addresses": contract
            },
            headers=goplus_headers(),
            timeout=20,
        )

        if r.status_code in (401, 403):
            return {
                "ok": False,
                "error": "GoPlus auth/yetki yok",
                "data": None,
            }

        r.raise_for_status()
        payload = r.json()
        result = payload.get("result") or {}

        token = (
            result.get(contract.lower())
            or result.get(contract)
            or {}
        )

        if not token:
            return {
                "ok": False,
                "error": "GoPlus DATA_MISSING",
                "data": None,
            }

        return {
            "ok": True,
            "error": None,
            "data": token,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "data": None,
        }


def goplus_raw_solana(contract):
    try:
        r = requests.get(
            (
                "https://api.gopluslabs.io/api/v1/"
                "solana/token_security"
            ),
            params={
                "contract_addresses": contract
            },
            headers=goplus_headers(),
            timeout=20,
        )

        if r.status_code in (401, 403):
            return {
                "ok": False,
                "error": "GoPlus Solana auth/yetki yok",
                "data": None,
            }

        r.raise_for_status()
        payload = r.json()
        result = payload.get("result") or {}

        token = (
            result.get(contract)
            or result.get(contract.lower())
            or {}
        )

        # Bazı sürümlerde result doğrudan token objesi olabilir.
        if not token and any(
            k in result
            for k in (
                "metadata",
                "holders",
                "dex",
                "dex_info",
                "total_supply",
            )
        ):
            token = result

        if not token:
            return {
                "ok": False,
                "error": "GoPlus Solana DATA_MISSING",
                "data": None,
            }

        return {
            "ok": True,
            "error": None,
            "data": token,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "data": None,
        }


def adjusted_holder_concentration(holders):
    rows = []

    for h in holders or []:
        tag = h.get("tag")
        percent = num(h.get("percent")) * 100.0

        if percent <= 0:
            continue

        excluded = (
            is_excluded_holder_tag(tag)
            or bool01(h.get("is_locked")) is True
        )

        if excluded:
            continue

        rows.append(percent)

    rows.sort(reverse=True)

    if not rows:
        return {
            "ok": False,
            "top1_pct": None,
            "top5_pct": None,
            "top10_pct": None,
            "counted_rows": 0,
        }

    return {
        "ok": True,
        "top1_pct": sum(rows[:1]),
        "top5_pct": sum(rows[:5]),
        "top10_pct": sum(rows[:10]),
        "counted_rows": len(rows),
    }


def recursively_find_lists(obj, key_name):
    found = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == key_name and isinstance(value, list):
                found.append(value)

            found.extend(
                recursively_find_lists(
                    value,
                    key_name
                )
            )

    elif isinstance(obj, list):
        for item in obj:
            found.extend(
                recursively_find_lists(
                    item,
                    key_name
                )
            )

    return found


def lp_protection_summary(raw_token):
    lp_lists = recursively_find_lists(
        raw_token or {},
        "lp_holders"
    )

    if not lp_lists:
        return {
            "status": "DATA_MISSING",
            "protected_pct": None,
            "locked_pct": None,
            "burned_pct": None,
            "unknown_unlocked_pct": None,
            "holders_seen": 0,
        }

    # En fazla holder bilgisi olan LP setini kullan.
    holders = max(
        lp_lists,
        key=len
    )

    locked = 0.0
    burned = 0.0
    unknown_unlocked = 0.0

    for h in holders:
        p = num(h.get("percent")) * 100.0
        tag = str(h.get("tag") or "").lower()
        is_locked = bool01(
            h.get("is_locked")
        )

        is_burn = any(
            x in tag
            for x in (
                "burn",
                "dead",
                "null",
                "black hole",
            )
        )

        if is_burn:
            burned += p
        elif is_locked is True:
            locked += p
        else:
            unknown_unlocked += p

    protected = locked + burned

    if protected >= 80:
        status = "STRONGLY_PROTECTED"
    elif protected >= 50:
        status = "PARTLY_PROTECTED"
    else:
        status = "LOW_OR_UNKNOWN_PROTECTION"

    return {
        "status": status,
        "protected_pct": protected,
        "locked_pct": locked,
        "burned_pct": burned,
        "unknown_unlocked_pct": unknown_unlocked,
        "holders_seen": len(holders),
    }


def gecko_recent_trade_cluster(
    network_id,
    pool_address,
    creator=None,
):
    path = (
        f"/networks/{network_id}/pools/"
        f"{pool_address}/trades"
    )

    payload = api_get(path)
    rows = payload.get("data") or []

    parsed = []

    for item in rows:
        a = item.get("attributes") or {}
        wallet = a.get("tx_from_address")
        block = a.get("block_number")
        kind = str(a.get("kind") or "").lower()
        ts = a.get("block_timestamp")
        volume = num(a.get("volume_in_usd"))

        if not wallet:
            continue

        parsed.append({
            "wallet": wallet,
            "block": block,
            "kind": kind,
            "timestamp": ts,
            "volume": volume,
        })

    if not parsed:
        return {
            "ok": False,
            "error": "TRADE_DATA_MISSING",
        }

    wallet_counts = Counter(
        x["wallet"]
        for x in parsed
    )

    unique_wallets = len(wallet_counts)
    total = len(parsed)
    top_wallet_count = (
        wallet_counts.most_common(1)[0][1]
        if wallet_counts
        else 0
    )

    top_wallet_trade_share = (
        top_wallet_count / total
        if total
        else 0
    )

    same_block_buys = Counter()

    for x in parsed:
        if x["kind"] == "buy" and x["block"] is not None:
            same_block_buys[x["block"]] += 1

    max_same_block_buys = (
        max(same_block_buys.values())
        if same_block_buys
        else 0
    )

    creator_trades = 0

    if creator:
        creator_norm = str(creator).lower()

        creator_trades = sum(
            1
            for x in parsed
            if str(x["wallet"]).lower()
            == creator_norm
        )

    bundle_proxy = (
        max_same_block_buys >= 4
        or top_wallet_trade_share >= 0.25
    )

    return {
        "ok": True,
        "error": None,
        "trades_seen": total,
        "unique_wallets": unique_wallets,
        "top_wallet_trade_share":
            top_wallet_trade_share,
        "max_same_block_buys":
            max_same_block_buys,
        "bundle_sniper_proxy":
            bundle_proxy,
        "creator_trades_in_pool":
            creator_trades,
    }


def helius_creator_transfer_activity(
    creator,
    mint,
):
    if not HELIUS_API_KEY or not creator:
        return {
            "ok": False,
            "error": "HELIUS_OR_CREATOR_MISSING",
        }

    url = helius_rpc_url()

    since = int(
        time.time() - 24 * 3600
    )

    res = json_rpc(
        url,
        "getTransfersByAddress",
        [
            creator,
            {
                "mint": mint,
                "filters": {
                    "blockTime": {
                        "gte": since
                    },
                    "status": "succeeded",
                },
                "limit": 100,
            }
        ]
    )

    if not res.get("ok"):
        return {
            "ok": False,
            "error": res.get("error"),
        }

    result = res.get("result") or {}
    rows = (
        result.get("data")
        or result.get("transfers")
        or result.get("items")
        or (
            result
            if isinstance(result, list)
            else []
        )
    )

    if not isinstance(rows, list):
        rows = []

    return {
        "ok": True,
        "error": None,
        "transfers_24h": len(rows),
    }


def zerox_exit_price(
    network_id,
    sell_token,
    sell_amount,
):
    if not ZEROX_API_KEY:
        return {
            "ok": False,
            "error": "ZEROX_API_KEY yok",
            "timestamp": utc_iso(),
        }

    chain_id = EVM_CHAIN_IDS.get(
        network_id
    )

    stable = EVM_STABLES.get(
        network_id
    )

    if not chain_id or not stable:
        return {
            "ok": False,
            "error": "0x chain/stable desteklenmiyor",
            "timestamp": utc_iso(),
        }

    try:
        r = requests.get(
            (
                "https://api.0x.org/"
                "swap/allowance-holder/price"
            ),
            params={
                "chainId": chain_id,
                "sellToken": sell_token,
                "buyToken": stable["address"],
                "sellAmount": str(
                    int(sell_amount)
                ),
            },
            headers={
                "0x-api-key": ZEROX_API_KEY,
                "0x-version": "v2",
                "accept": "application/json",
            },
            timeout=20,
        )

        r.raise_for_status()
        data = r.json()

        liquidity_available = data.get(
            "liquidityAvailable"
        )

        if liquidity_available is False:
            return {
                "ok": False,
                "error": "0x liquidityAvailable=false",
                "timestamp": utc_iso(),
            }

        buy_amount = num(
            data.get("buyAmount")
        )

        out_usd = (
            buy_amount
            / (
                10
                ** stable["decimals"]
            )
        )

        return {
            "ok": True,
            "error": None,
            "timestamp": utc_iso(),
            "out_usd": out_usd,
            "buy_amount": data.get(
                "buyAmount"
            ),
            "liquidity_available":
                liquidity_available,
            "stable_symbol":
                stable["symbol"],
            "block_number":
                data.get("blockNumber"),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "timestamp": utc_iso(),
        }


def evm_exit_metrics(
    network_id,
    token,
    decimals,
    price_usd,
    intended_usd,
):
    if decimals <= 0 or price_usd <= 0:
        return {
            "ok": False,
            "error": "fiyat veya decimals yok",
            "timestamp": utc_iso(),
        }

    sell_amount = int(
        (intended_usd / price_usd)
        * (10 ** decimals)
    )

    q = zerox_exit_price(
        network_id,
        token,
        sell_amount,
    )

    if not q.get("ok"):
        return q

    out_usd = num(
        q.get("out_usd")
    )

    loss_pct = (
        (
            intended_usd
            - out_usd
        )
        / intended_usd
    ) * 100.0

    q["loss_pct"] = loss_pct
    q["intended_usd"] = (
        intended_usd
    )

    return q


# ------------------------------------------------------------
# OUTCOME / MFE / MAE — 1 DAKIKALIK OHLCV
# ------------------------------------------------------------

def outcome_db():
    con = sqlite3.connect(
        OUTCOME_DB_PATH
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_version TEXT NOT NULL,
            network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL,
            pool TEXT NOT NULL,
            signal_ts INTEGER NOT NULL,
            signal_iso TEXT NOT NULL,
            entry_price REAL NOT NULL,
            last_candle_ts INTEGER,
            mfe_pct REAL NOT NULL DEFAULT 0,
            mae_pct REAL NOT NULL DEFAULT 0,
            hit_3_ts INTEGER,
            hit_5_ts INTEGER,
            hit_7_ts INTEGER,
            hit_10_ts INTEGER,
            hit_15_ts INTEGER,
            observation_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN'
        )
        """
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_signals_open
        ON signals(status, signal_ts)
        """
    )

    con.commit()
    return con


def record_signal_for_outcome(c):
    entry_price = num(
        c.get("price_usd")
    )

    if entry_price <= 0:
        return None

    now_ts = int(time.time())
    cutoff = (
        now_ts
        - OUTCOME_SIGNAL_COOLDOWN_HOURS
        * 3600
    )

    con = outcome_db()

    row = con.execute(
        """
        SELECT id
        FROM signals
        WHERE network_id = ?
          AND token_contract = ?
          AND signal_ts >= ?
        ORDER BY signal_ts DESC
        LIMIT 1
        """,
        (
            c["network_id"],
            c["token_contract"],
            cutoff,
        )
    ).fetchone()

    if row:
        con.close()
        return row[0]

    cur = con.execute(
        """
        INSERT INTO signals (
            config_version,
            network_id,
            token_contract,
            pool,
            signal_ts,
            signal_iso,
            entry_price,
            last_candle_ts,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            CONFIG_VERSION,
            c["network_id"],
            c["token_contract"],
            c["pool"],
            now_ts,
            utc_iso(),
            entry_price,
            now_ts - 60,
        )
    )

    signal_id = cur.lastrowid
    con.commit()
    con.close()
    return signal_id


def fetch_minute_candles(
    network_id,
    pool,
    token_contract,
    before_ts=None,
    limit=120,
):
    path = (
        f"/networks/{network_id}/pools/"
        f"{pool}/ohlcv/minute"
        f"?aggregate=1"
        f"&limit={int(limit)}"
        f"&currency=usd"
        f"&token={token_contract}"
    )

    if before_ts:
        path += (
            f"&before_timestamp="
            f"{int(before_ts)}"
        )

    payload = api_get(path)
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}
    rows = attrs.get(
        "ohlcv_list"
    ) or []

    parsed = []

    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) < 6
        ):
            continue

        parsed.append({
            "ts": int_or_zero(row[0]),
            "open": num(row[1]),
            "high": num(row[2]),
            "low": num(row[3]),
            "close": num(row[4]),
            "volume": num(row[5]),
        })

    parsed.sort(
        key=lambda x: x["ts"]
    )

    return parsed


def update_one_outcome(
    con,
    signal_row,
):
    (
        signal_id,
        network_id,
        token_contract,
        pool,
        signal_ts,
        entry_price,
        last_candle_ts,
        mfe_pct,
        mae_pct,
        hit3,
        hit5,
        hit7,
        hit10,
        hit15,
        obs_count,
    ) = signal_row

    now_ts = int(time.time())

    # Her run'da son ~3 saati cekmek,
    # 10 dk scheduler icin yeterli tampon verir.
    candles = fetch_minute_candles(
        network_id,
        pool,
        token_contract,
        before_ts=now_ts + 60,
        limit=180,
    )

    candles = [
        x
        for x in candles
        if x["ts"] > (
            last_candle_ts
            or signal_ts - 60
        )
        and x["ts"] >= signal_ts
    ]

    if not candles:
        if (
            now_ts - signal_ts
            >= OUTCOME_CLOSE_HOURS
            * 3600
        ):
            con.execute(
                """
                UPDATE signals
                SET status = 'CLOSED_72H'
                WHERE id = ?
                """,
                (signal_id,)
            )
        return

    target_hits = {
        3: hit3,
        5: hit5,
        7: hit7,
        10: hit10,
        15: hit15,
    }

    current_mfe = num(mfe_pct)
    current_mae = num(mae_pct)

    for candle in candles:
        high_ret = (
            (
                candle["high"]
                / entry_price
            )
            - 1
        ) * 100.0

        low_ret = (
            (
                candle["low"]
                / entry_price
            )
            - 1
        ) * 100.0

        current_mfe = max(
            current_mfe,
            high_ret
        )

        current_mae = min(
            current_mae,
            low_ret
        )

        for target in OUTCOME_TARGETS:
            if (
                target_hits[target]
                is None
                and high_ret >= target
            ):
                target_hits[target] = (
                    candle["ts"]
                )

    last_ts = candles[-1]["ts"]
    new_obs = (
        int_or_zero(obs_count)
        + len(candles)
    )

    status = "OPEN"

    if (
        now_ts - signal_ts
        >= OUTCOME_CLOSE_HOURS
        * 3600
    ):
        status = "CLOSED_72H"

    con.execute(
        """
        UPDATE signals
        SET last_candle_ts = ?,
            mfe_pct = ?,
            mae_pct = ?,
            hit_3_ts = ?,
            hit_5_ts = ?,
            hit_7_ts = ?,
            hit_10_ts = ?,
            hit_15_ts = ?,
            observation_count = ?,
            status = ?
        WHERE id = ?
        """,
        (
            last_ts,
            current_mfe,
            current_mae,
            target_hits[3],
            target_hits[5],
            target_hits[7],
            target_hits[10],
            target_hits[15],
            new_obs,
            status,
            signal_id,
        )
    )


def update_outcomes():
    con = outcome_db()

    rows = con.execute(
        """
        SELECT
            id,
            network_id,
            token_contract,
            pool,
            signal_ts,
            entry_price,
            last_candle_ts,
            mfe_pct,
            mae_pct,
            hit_3_ts,
            hit_5_ts,
            hit_7_ts,
            hit_10_ts,
            hit_15_ts,
            observation_count
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_ts ASC
        LIMIT ?
        """,
        (
            OUTCOME_MAX_UPDATES_PER_RUN,
        )
    ).fetchall()

    for row in rows:
        try:
            update_one_outcome(
                con,
                row
            )
            con.commit()
        except Exception as e:
            print(
                "Outcome update hata:",
                row[0],
                e
            )

        time.sleep(7)

    con.close()


def outcome_summary():
    con = outcome_db()

    total = con.execute(
        "SELECT COUNT(*) FROM signals"
    ).fetchone()[0]

    closed = con.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status != 'OPEN'
        """
    ).fetchone()[0]

    hits = {}

    for target in OUTCOME_TARGETS:
        col = f"hit_{target}_ts"

        hits[target] = con.execute(
            f"""
            SELECT COUNT(*)
            FROM signals
            WHERE {col} IS NOT NULL
            """
        ).fetchone()[0]

    con.close()

    return {
        "total": total,
        "closed": closed,
        "hits": hits,
    }


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
# V4 OVERRIDES — ESKI CALISAN V3 MOTORLARINI KORUR,
# USTUNE EKSIK RISK KATMANLARINI EKLER.
# ============================================================

_enrich_solana_candidate_v3 = enrich_solana_candidate
_enrich_evm_candidate_v3 = enrich_evm_candidate
_print_candidate_v3 = print_candidate


def enrich_solana_candidate(c):
    _enrich_solana_candidate_v3(c)

    c["exit_quote_timestamp"] = utc_iso()

    gp = goplus_raw_solana(
        c["token_contract"]
    )

    c["goplus_solana"] = {
        "ok": gp.get("ok"),
        "error": gp.get("error"),
    }

    if not gp.get("ok"):
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "GOPLUS_SOLANA_DATA_MISSING"
        )

        c["lp_protection"] = {
            "status": "DATA_MISSING"
        }

        c["adjusted_holder"] = {
            "ok": False
        }

        creator = None

    else:
        raw = gp.get("data") or {}

        holders = raw.get(
            "holders"
        ) or []

        c["adjusted_holder"] = (
            adjusted_holder_concentration(
                holders
            )
        )

        c["lp_protection"] = (
            lp_protection_summary(raw)
        )

        creator = (
            raw.get("creator")
            or raw.get("creator_address")
        )

        c["creator_address"] = creator

        lp = c["lp_protection"]

        if lp.get("status") == "DATA_MISSING":
            c.setdefault(
                "security_risk_reasons",
                []
            ).append(
                "LP_DATA_MISSING"
            )

        elif num(
            lp.get("protected_pct")
        ) < 50:
            c.setdefault(
                "security_risk_reasons",
                []
            ).append(
                "LP_LOW_PROTECTION"
            )

        adj = c["adjusted_holder"]

        if adj.get("ok"):
            if num(
                adj.get("top1_pct")
            ) >= SOL_TOP1_WARN_PCT:
                c.setdefault(
                    "security_risk_reasons",
                    []
                ).append(
                    "ADJ_TOP1_CONCENTRATION"
                )

            if num(
                adj.get("top5_pct")
            ) >= SOL_TOP5_WARN_PCT:
                c.setdefault(
                    "security_risk_reasons",
                    []
                ).append(
                    "ADJ_TOP5_CONCENTRATION"
                )

    cluster = gecko_recent_trade_cluster(
        c["network_id"],
        c["pool"],
        creator=creator,
    )

    c["trade_cluster"] = cluster

    if cluster.get(
        "bundle_sniper_proxy"
    ) is True:
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "BUNDLE_SNIPER_PROXY"
        )

    if int_or_zero(
        cluster.get(
            "creator_trades_in_pool"
        )
    ) > 0:
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "DEV_ACTIVE_IN_POOL"
        )

    dev_transfer = (
        helius_creator_transfer_activity(
            creator,
            c["token_contract"],
        )
    )

    c["dev_transfer_activity"] = (
        dev_transfer
    )

    if (
        dev_transfer.get("ok")
        and int_or_zero(
            dev_transfer.get(
                "transfers_24h"
            )
        ) > 0
    ):
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "DEV_TOKEN_TRANSFERS_24H"
        )


def enrich_evm_candidate(c):
    _enrich_evm_candidate_v3(c)

    raw_res = goplus_raw_evm(
        c["network_id"],
        c["token_contract"]
    )

    c["goplus_raw"] = {
        "ok": raw_res.get("ok"),
        "error": raw_res.get("error"),
    }

    if not raw_res.get("ok"):
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "GOPLUS_DATA_MISSING"
        )

        c["lp_protection"] = {
            "status": "DATA_MISSING"
        }

        c["adjusted_holder"] = {
            "ok": False
        }

    else:
        raw = raw_res.get("data") or {}

        c["lp_protection"] = (
            lp_protection_summary(raw)
        )

        c["adjusted_holder"] = (
            adjusted_holder_concentration(
                raw.get("holders") or []
            )
        )

        lp = c["lp_protection"]

        if lp.get("status") == "DATA_MISSING":
            c.setdefault(
                "security_risk_reasons",
                []
            ).append(
                "LP_DATA_MISSING"
            )

        elif num(
            lp.get("protected_pct")
        ) < 50:
            c.setdefault(
                "security_risk_reasons",
                []
            ).append(
                "LP_LOW_PROTECTION"
            )

        creator = raw.get(
            "creator_address"
        )

        if creator:
            c["creator_address"] = creator

    decimals = int_or_zero(
        c.get("decimals")
    )

    price_usd = num(
        c.get("price_usd")
    )

    c["evm_exit_1k"] = (
        evm_exit_metrics(
            c["network_id"],
            c["token_contract"],
            decimals,
            price_usd,
            1000,
        )
    )

    c["evm_exit_5k"] = (
        evm_exit_metrics(
            c["network_id"],
            c["token_contract"],
            decimals,
            price_usd,
            5000,
        )
    )

    c["exit_quote_timestamp"] = utc_iso()

    if not ZEROX_API_KEY:
        c.setdefault(
            "security_risk_reasons",
            []
        ).append(
            "EVM_EXIT_DATA_MISSING"
        )

    for label, data, threshold in (
        (
            "EVM_EXIT_1K_LOSS_HIGH",
            c["evm_exit_1k"],
            MAX_EXIT_LOSS_1K_PCT,
        ),
        (
            "EVM_EXIT_5K_LOSS_HIGH",
            c["evm_exit_5k"],
            MAX_EXIT_LOSS_5K_PCT,
        ),
    ):
        if data.get("ok"):
            if num(
                data.get("loss_pct")
            ) > threshold:
                c.setdefault(
                    "security_risk_reasons",
                    []
                ).append(label)


def print_candidate(i, c):
    _print_candidate_v3(i, c)

    print(
        "   [V4 ek risk katmanlari]"
    )

    print(
        "   Quote timestamp:",
        c.get(
            "exit_quote_timestamp",
            "N/A"
        )
    )

    lp = c.get(
        "lp_protection"
    ) or {}

    print(
        "   LP koruma:",
        lp.get(
            "status",
            "DATA_MISSING"
        )
    )

    if lp.get(
        "protected_pct"
    ) is not None:
        print(
            "      Kilit+burn:",
            fmt_pct(
                lp.get(
                    "protected_pct"
                )
            )
        )

        print(
            "      Kilitli:",
            fmt_pct(
                lp.get(
                    "locked_pct"
                )
            )
        )

        print(
            "      Burn:",
            fmt_pct(
                lp.get(
                    "burned_pct"
                )
            )
        )

    adj = c.get(
        "adjusted_holder"
    ) or {}

    print(
        "   Adjusted holder "
        "(LP/burn/program/exchange haric):",
        (
            (
                fmt_pct(
                    adj.get(
                        "top1_pct"
                    )
                )
                + " / "
                + fmt_pct(
                    adj.get(
                        "top5_pct"
                    )
                )
                + " / "
                + fmt_pct(
                    adj.get(
                        "top10_pct"
                    )
                )
            )
            if adj.get("ok")
            else "DATA_MISSING"
        )
    )

    cluster = c.get(
        "trade_cluster"
    ) or {}

    if c.get(
        "network_id"
    ) == "solana":
        print(
            "   Bundle/sniper proxy:",
            (
                yesno(
                    cluster.get(
                        "bundle_sniper_proxy"
                    )
                )
                if cluster.get("ok")
                else "DATA_MISSING"
            )
        )

        if cluster.get("ok"):
            print(
                "      Unique recent wallets:",
                cluster.get(
                    "unique_wallets"
                )
            )

            print(
                "      Max same-block buys:",
                cluster.get(
                    "max_same_block_buys"
                )
            )

            print(
                "      Top-wallet trade share:",
                fmt_pct(
                    num(
                        cluster.get(
                            "top_wallet_trade_share"
                        )
                    )
                    * 100
                )
            )

    if c.get(
        "network_id"
    ) in EVM_CHAIN_IDS:
        for name, data in (
            (
                "0x $1K exit",
                c.get(
                    "evm_exit_1k"
                )
            ),
            (
                "0x $5K exit",
                c.get(
                    "evm_exit_5k"
                )
            ),
        ):
            data = data or {}

            print(
                f"   {name}:",
                (
                    (
                        f"out ${num(data.get('out_usd')):,.2f}, "
                        f"kayip {fmt_pct(data.get('loss_pct'))}"
                    )
                    if data.get("ok")
                    else (
                        "DATA_MISSING/HATA - "
                        + str(
                            data.get(
                                "error"
                            )
                        )
                    )
                )
            )

    print(
        "   V4 security flags:",
        (
            ", ".join(
                c.get(
                    "security_risk_reasons"
                )
                or []
            )
            or "YOK"
        )
    )

    print(
        "=" * 72
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
    "0x EVM exit:",
    "AKTIF" if ZEROX_API_KEY
    else "PASIF (ZEROX_API_KEY yok)"
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

# Onceki sinyallerin 1 dakikalik OHLCV outcome verisini guncelle.
update_outcomes()

# Yeni sinyalleri 24 saat cooldown ile outcome DB'ye kaydet.
for candidate in all_candidates:
    try:
        candidate["outcome_signal_id"] = (
            record_signal_for_outcome(
                candidate
            )
        )
    except Exception as e:
        candidate["outcome_signal_id"] = None
        print(
            "Outcome signal kayit hata:",
            e
        )

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

try:
    _out = outcome_summary()

    print()
    print(
        "Outcome DB:",
        _out["total"],
        "sinyal /",
        _out["closed"],
        "72h kapanmis"
    )

    print(
        "Outcome hits:",
        " | ".join(
            f"+%{t}: {_out['hits'][t]}"
            for t in OUTCOME_TARGETS
        )
    )

except Exception as e:
    print(
        "Outcome summary hata:",
        e
    )

print()
print("=" * 72)
print("AVCI 2 V4 TAMAMLANDI")
print(
    "Not: Outcome MFE/MAE ve +%3/+%5/+%7/+%10/+%15 "
    "1 dakikalik OHLCV ile izlenir. Funding-graph Sybil "
    "icin Helius transfer gecmisi gerekir; bundle/sniper "
    "etiketi proxy olup kanit degildir."
)
print("=" * 72)
