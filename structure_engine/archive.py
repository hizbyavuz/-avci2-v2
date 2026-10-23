"""Checksum-verified Binance USD-M public monthly 1m archive reader."""
import csv
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

from .config import MINUTE, PERIODS


def archive(cache, symbol, month, offline=False):
    name = f'{symbol}-1m-{month}.zip'
    url = f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{name}'
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    path = cache/name
    checksum = cache/(name+'.CHECKSUM')
    for local, remote in [(path, url), (checksum, url+'.CHECKSUM')]:
        if not local.exists():
            if offline: raise ValueError('ARCHIVE_NOT_CACHED:'+name)
            with urllib.request.urlopen(remote, timeout=30) as response:
                body = response.read(16_000_000)
            tmp = local.with_suffix(local.suffix+'.tmp')
            tmp.write_bytes(body); tmp.replace(local)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != checksum.read_text().split()[0]:
        raise ValueError('ARCHIVE_CHECKSUM_MISMATCH:'+name)
    rows = []
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for r in csv.reader(io.TextIOWrapper(f)):
                if not r or not r[0].isdigit(): continue
                rows.append(dict(open_ms=int(r[0]), close_ms=int(r[6]),
                                 open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                                 quote_volume=float(r[7]), taker_buy_quote=float(r[10])))
    if any(b['close_ms'] != b['open_ms']+MINUTE-1 for b in rows):
        raise ValueError('ARCHIVE_MINUTE_CLOCK_INVALID')
    if any(b['open_ms'] != a['open_ms']+MINUTE for a, b in zip(rows, rows[1:])):
        raise ValueError('ARCHIVE_MINUTE_GAP')
    return dict(url=url, sha256=sha, checksum_verified=True, rows=len(rows), bytes=path.stat().st_size), rows


def aggregate(rows, interval):
    step = PERIODS[interval]
    buckets = {}
    for r in rows:
        buckets.setdefault(r['open_ms']//step*step, []).append(r)
    out = []
    for start, group in sorted(buckets.items()):
        if [r['open_ms'] for r in group] != list(range(start, start+step, MINUTE)):
            continue
        out.append(dict(open_ms=start, close_ms=start+step-1, open=group[0]['open'],
                        high=max(r['high'] for r in group), low=min(r['low'] for r in group),
                        close=group[-1]['close'], quote_volume=sum(r['quote_volume'] for r in group),
                        taker_buy_quote=sum(r['taker_buy_quote'] for r in group)))
    return out
