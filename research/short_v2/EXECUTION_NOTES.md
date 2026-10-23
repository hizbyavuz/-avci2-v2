# Integrity clarification before the new replay

The preregistration's phrase "officially verified cached August and September"
overstates the August hourly evidence. Inspection before evaluating V2 found:
August's 833 hourly ZIPs have preserved download URLs and local SHA256 hashes,
but that coverage report does not contain official CHECKSUM verification.
September's 869 hourly ZIPs and the 479 cached minute ZIPs do have official
CHECKSUM verification. The replay checks the preserved hashes in all cases and
reports these provenance classes separately. No network fetch is attempted.

This clarification changes no candidate, threshold, outcome or selection order.
The unclassified historical universe and already-viewed months remain development
data. Missing minutes remain in the denominator. These limits prevent promotion.

Before this run, 8 targeted tests passed for the two structures, future-data
invariance, missing/stale data, reclaim invalidation, flow sign and cost/chase
rejection. The existing LONG, V1 decision, and V1 outcome sources are unchanged.
