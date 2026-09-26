"""Adaptive paginated pool observations, separate from frozen V5 selection.

Coverage priority follows the actual Gate Web3 opportunity surface:
Solana is scanned deepest, Base/BSC next, ETH/Arbitrum lighter.
This module is observation-only and cannot alter frozen V5 membership.
"""

FEEDS = (("TRENDING", "trending_pools"), ("NEW", "new_pools"))

# Page 1 is collected by scanner.py. These are additional research pages.
PAGE_PLAN = {
    "solana": {
        "trending_pools": tuple(range(2, 7)),
        "new_pools": tuple(range(2, 11)),
    },
    "base": {
        "trending_pools": tuple(range(2, 5)),
        "new_pools": tuple(range(2, 7)),
    },
    "bsc": {
        "trending_pools": tuple(range(2, 5)),
        "new_pools": tuple(range(2, 7)),
    },
    "eth": {
        "trending_pools": (2, 3),
        "new_pools": (2, 3, 4),
    },
    "arbitrum": {
        "trending_pools": (2, 3),
        "new_pools": (2, 3, 4),
    },
}

def collect_extra_observations(networks, fetch, parse, pause,
                               pages=None, on_payload=None):
    """Collect a wider discovery universe without changing frozen candidates.

    `pages` is retained for backwards-compatible tests. When omitted, the
    adaptive PAGE_PLAN is used.
    """
    observed, errors, fetched = [], [], 0
    for network_id, network_name in networks.items():
        for source, endpoint in FEEDS:
            wanted = tuple(pages) if pages is not None else PAGE_PLAN.get(
                network_id, {}
            ).get(endpoint, (2, 3))
            for page in wanted:
                payload = fetch(
                    f"/networks/{network_id}/{endpoint}"
                    f"?include=base_token,quote_token&page={page}"
                )
                if (not isinstance(payload, dict)
                        or payload.get("_avci_data_error")
                        or not isinstance(payload.get("data"), list)):
                    errors.append(f"{network_id}:{endpoint}:page{page}")
                    break
                fetched += 1
                observed.extend(parse(
                    payload, network_id, network_name, source, observation=True
                ))
                if on_payload is not None:
                    on_payload(payload, network_id, network_name, source)
                # Caller controls rate-limit pacing. Stop once the API says
                # there are no more rows rather than wasting requests.
                pause(3)
                if not payload["data"]:
                    break
    return observed, errors, fetched
