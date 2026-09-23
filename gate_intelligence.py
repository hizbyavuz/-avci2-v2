"""Optional, source-labelled Gate research enrichment; never places trades."""

import os
import re
from datetime import datetime, timedelta, timezone

import requests


EVM_CHAINS = {"eth": "1", "bsc": "56", "base": "8453", "arbitrum": "42161"}
MALICIOUS_FLAGS = (
    "honeypot_related_address", "phishing_activities", "stealing_attack",
    "cybercrime", "financial_crime", "blacklist_doubt", "sanctioned",
)


def creator_reputation(network, address, session=requests, token=None):
    """GoPlus malicious-address response for EVM creators only.

    A negative lookup means no finding, never proof of safety. An unsupported
    chain or failed request remains UNKNOWN rather than a clean verdict.
    """
    if network not in EVM_CHAINS or not re.fullmatch(r"0x[0-9a-fA-F]{40}",
                                                       address or ""):
        return {"status": "UNKNOWN", "reason": "UNSUPPORTED_OR_MISSING_CREATOR"}
    headers = {"accept": "application/json"}
    credential = token if token is not None else os.getenv("GOPLUS_ACCESS_TOKEN")
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        response = session.get(
            f"https://api.gopluslabs.io/api/v1/address_security/{address}",
            params={"chain_id": EVM_CHAINS[network]}, headers=headers, timeout=12)
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("result") or {}
        if not isinstance(raw, dict):
            raise ValueError("No address risk object")
        # Some provider versions wrap the result in an address key.
        data = raw.get(address.lower()) or raw.get(address) or raw
        if not isinstance(data, dict) or not any(
                flag in data for flag in MALICIOUS_FLAGS +
                ("number_of_malicious_contracts_created",)):
            raise ValueError("No recognized address risk fields")
        flags = [flag for flag in MALICIOUS_FLAGS
                 if str(data.get(flag)).lower() in ("1", "true")]
        count_raw = data.get("number_of_malicious_contracts_created")
        count = int(count_raw) if count_raw not in (None, "") else None
        if count is not None and count > 0:
            flags.append("number_of_malicious_contracts_created")
        return {"status": "FLAGGED" if flags else "NO_FINDING",
                "flags": flags, "malicious_contracts": count,
                "source": "GoPlus malicious-address API"}
    except (requests.RequestException, ValueError, TypeError):
        return {"status": "UNKNOWN", "reason": "ADDRESS_LOOKUP_UNAVAILABLE"}


def lp_lock_health(holder, now=None):
    """Use provider lock end times when present, preserving unknown expiry."""
    now = now or datetime.now(timezone.utc)
    details = holder.get("locked_detail") or []
    if not details:
        return {"status": "EXPIRY_UNKNOWN", "earliest_end": None}
    ends = []
    for detail in details:
        raw = detail.get("end_time") if isinstance(detail, dict) else None
        try:
            if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
                ts = int(raw)
                end = datetime.fromtimestamp(ts / 1000 if ts > 10**11 else ts,
                                             timezone.utc)
            else:
                end = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if end.tzinfo is None:
                    raise ValueError("Naive time")
            ends.append(end.astimezone(timezone.utc))
        except (TypeError, ValueError, OverflowError, OSError):
            return {"status": "EXPIRY_UNKNOWN", "earliest_end": None}
    earliest = min(ends)
    if any(end <= now for end in ends):
        return {"status": "EXPIRED_OR_PARTIAL", "earliest_end": earliest.isoformat()}
    return {"status": "EXPIRES_SOON" if earliest <= now + timedelta(hours=24)
            else "FUTURE", "earliest_end": earliest.isoformat()}


def x_contract_mentions(network, contract, session=requests, token=None,
                        now=None):
    """Exact-contract public X post counts; ticker/name matches are unsafe."""
    token = token if token is not None else os.getenv("X_API_BEARER_TOKEN")
    if not token:
        return {"status": "UNAVAILABLE", "reason": "X_API_BEARER_TOKEN_MISSING"}
    valid = (re.fullmatch(r"0x[0-9a-fA-F]{40}", contract or "")
             if network in EVM_CHAINS else
             re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", contract or ""))
    if not valid:
        return {"status": "UNAVAILABLE", "reason": "CONTRACT_INVALID"}
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = now - timedelta(minutes=1)
    start = end - timedelta(minutes=60)
    params = {"query": f'"{contract}" -is:retweet',
              "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "granularity": "minute"}
    try:
        response = session.get("https://api.x.com/2/tweets/counts/recent",
                               params=params,
                               headers={"Authorization": f"Bearer {token}"},
                               timeout=12)
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("No counts data")
        recent = previous = 0
        boundary = end - timedelta(minutes=15)
        for row in data:
            stamp = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
            count = int(row["tweet_count"])
            if count < 0:
                raise ValueError("Negative count")
            if boundary <= stamp < end:
                recent += count
            elif start <= stamp < boundary:
                previous += count
        return {"status": "OBSERVED", "last_15m": recent,
                "previous_45m": previous,
                "ratio": recent / (previous / 3) if previous else None,
                "source": "X exact-contract counts", "measured_at": end.isoformat()}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {"status": "UNAVAILABLE", "reason": "X_COUNTS_UNAVAILABLE"}
