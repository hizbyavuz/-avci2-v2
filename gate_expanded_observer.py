"""Extra paginated pool observations, separate from frozen V5 trade selection."""

EXTRA_PAGES = (2, 3)
FEEDS = (("TRENDING", "trending_pools"), ("NEW", "new_pools"))


def collect_extra_observations(networks, fetch, parse, pause, pages=EXTRA_PAGES,
                               on_payload=None):
    """Collect additional research rows without changing candidate selection.

    Failed optional pages are reported separately. They must not invalidate
    the first-page scan used by the existing safety-gated alert system.
    """
    observed, errors, fetched = [], [], 0
    for network_id, network_name in networks.items():
        for source, endpoint in FEEDS:
            for page in pages:
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
                pause(7)
                if not payload["data"]:
                    break
    return observed, errors, fetched
