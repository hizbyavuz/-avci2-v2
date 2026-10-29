# Retired replay output archival, 29 October 2026

The live inventory showed only 111 MB free on the persistent volume. Eight
retired historical replay databases occupied about 1.22 GB. Equal file sizes
do not establish duplicate contents: each file is archived independently.

`--archive-retired-replays` runs at startup after acquiring the existing worker
locks, before opening live stores. It also acquires the research job lock and
refuses to run with `--historical-research`. Only the eight exact run-directory
identities in `ta_paper/storage.py` are eligible. Missing/nonterminal job status,
SQLite sidecars, symlinks, bad integrity, active locks, or insufficient free
space retain the raw file. Live databases, candles, messages, reports, chunk
files, and all unlisted files are outside the allowlist.

For each terminal research run, an exclusive SQLite transaction and quick-check
precede gzip compression. The archive is flushed to disk, reopened and hashed,
and its byte count and SHA256 must match the raw file. The raw source is hashed
again and its inode, size and modification time checked. A durable manifest
record is written before replacing the raw representation with its verified
compressed representation. Only this attempt's incomplete temporary output may
be discarded on compression failure. Every run keeps a separate archive.

The manifest is `research/storage_archive_20261029.jsonl`; each record contains
the original relative path, archive path, SHA256, byte counts, outcome row count,
and historical job status. Historical failures remain failures. Restoration
requires enough disk space: stop historical jobs, decompress the recorded gzip
to a temporary file, verify its SHA256/size against the manifest, then rename it
to the recorded original path. Never overwrite an existing raw file.

The signal configuration, core, risk limits, exchange request gate and retry
deadline remain unchanged. No live orders or new research jobs are enabled.
The former inventory-only start wrapper is replaced with the normal runner
command plus the explicit archival flag. Archival failures are logged and the
existing worker continues. The flag is idempotent on subsequent starts.
