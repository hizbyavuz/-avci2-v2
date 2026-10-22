# Evidence and reproduction

`MANIFEST.json` identifies every compressed source report and its uncompressed
SHA256. The full context and funding episode outputs retain every candidate;
the `initial_partial` output preserves the result before offline source reuse.
The original failed download report is not rewritten as a successful download.

To restore the small JSON/JSONL inputs from this directory, from repository root:

```python
import gzip, hashlib, json
from pathlib import Path
p = Path('research/trader_v2')
for item in json.loads((p/'evidence/MANIFEST.json').read_text())['entries']:
    if 'restore_path' not in item:
        continue
    source = (p/item['path']).read_bytes()
    assert hashlib.sha256(source).hexdigest() == item['compressed_sha256']
    raw = gzip.decompress(source)
    assert hashlib.sha256(raw).hexdigest() == item['sha256']
    destination = p/item['restore_path']
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        assert destination.read_bytes() == raw
    else:
        destination.write_bytes(raw)
```

The earlier V1 source episodes/outcomes are in `research/swing_v1/evidence/`.
Restore them to the paths named by the requests, verifying that manifest too.
Large exchange ZIPs are not embedded in this repository; source URLs and official
checksums are retained. Replay requires those verified local cache files. Never
replace a missing archive with an empty/zero-cost record.

Run `compare.py` with `--cache` and `--hourly-cache`. Run `funding_audit.py` with
`--cache`, `--allow-partial`, and
`--download-report research/trader_v2/FUNDING_COST_OFFLINE_RECOVERY.json`.
Both tools refuse to overwrite existing output files. Use a separate working copy
for reruns. `recover_verified_costs.py` uses already verified local context files
only; it performs no network request.

No strategy threshold, selection order or exit policy changed after the four
variant results. Later code changes concern cost coverage, source integrity,
simultaneous settlement uncertainty, and aborting wrapped network denials.
