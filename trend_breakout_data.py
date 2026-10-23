"""Official Binance archive ingestion with checksums and point-in-time universes.

Daily OHLCV is used ONLY to sum the monthly universe's trailing 30-day quote
volume and establish 60-day age. Strategy calculations use original 1h/4h data.
No current TRADING list is used to select historical symbols.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import requests
from trend_breakout_engine import Candle, Series, HOUR, DAY

ARCHIVE = 'https://data.binance.vision/'
CATALOG = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
# Product identity, not a price/return filter. Historical baskets included.
EXCLUDED = {'USDCUSDT', 'BUSDUSDT', 'TUSDUSDT', 'FDUSDUSDT', 'USDPUSDT',
            'USDEUSDT', 'USDDUSDT', 'DAIUSDT', 'USTUSDT', 'USTCUSDT',
            'DEFIUSDT', 'BTCDOMUSDT', 'BLUEBIRDUSDT',
            'QQQUSDT', 'SPYUSDT', 'TQQQUSDT', 'SQQQUSDT', 'SOXLUSDT', 'SOXSUSDT',
            'KORUUSDT', 'EWYUSDT', 'EWJUSDT', 'TZAUSDT', 'XBIUSDT', 'XLEUSDT',
            'UVXYUSDT', 'URNMUSDT', 'SMHUSDT', 'IWMUSDT', 'DIAUSDT', 'TNAUSDT',
            'FDUSDUSDT', 'USD1USDT', 'RLUSDUSDT', 'XUSDUSDT', 'UUSDT', 'EURIUSDT', 'AEURUSDT'}
_local = threading.local()


def ms(text):
    return int(datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=timezone.utc).timestamp()*1000)


def iso(stamp):
    return datetime.fromtimestamp(stamp/1000, timezone.utc).isoformat()


def month(stamp):
    return iso(stamp)[:7]


def months(start, end):
    d = datetime.fromtimestamp(start/1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(d.timestamp()*1000) < end:
        yield d.strftime('%Y-%m')
        d = (d.replace(day=28)+timedelta(days=4)).replace(day=1)


def get(url, params=None):
    if not hasattr(_local, 'session'):
        _local.session = requests.Session()
    for attempt in range(3):
        try:
            r = _local.session.get(url, params=params, timeout=(10, 30))
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(.25*(attempt+1))


class Archive:
    def __init__(self, root, offline=False):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.lock = threading.Lock()

    def note(self, row):
        with self.lock, (self.root/'downloads.jsonl').open('a') as f:
            f.write(json.dumps(row, ensure_ascii=False)+'\n')

    def listing(self, prefix, delimiter='/', refresh=False):
        cache = self.root/'catalog'/hashlib.sha256((prefix+delimiter).encode()).hexdigest()
        if cache.exists() and not refresh:
            return json.loads(cache.read_text())
        if self.offline:
            raise RuntimeError('CATALOG_NOT_CACHED: '+prefix)
        prefixes, keys, marker = [], [], ''
        ns = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        while True:
            raw = get(CATALOG, dict(prefix=prefix, delimiter=delimiter, marker=marker))
            if raw is None:
                raise RuntimeError('CATALOG_MISSING')
            x = ET.fromstring(raw)
            ps = [n.text for n in x.findall('s:CommonPrefixes/s:Prefix', ns)]
            ks = [n.text for n in x.findall('s:Contents/s:Key', ns)]
            prefixes.extend(ps); keys.extend(ks)
            if x.findtext('s:IsTruncated', namespaces=ns) != 'true':
                break
            new = x.findtext('s:NextMarker', namespaces=ns) or (ks+ps)[-1]
            if new == marker:
                raise RuntimeError('CATALOG_PAGINATION_STALLED')
            marker = new
        result = dict(prefixes=prefixes, keys=keys)
        cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text(json.dumps(result))
        return result

    def zip_rows(self, key):
        path = self.root/key
        if path.exists() and path.with_suffix('.sha256').exists():
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != path.with_suffix('.sha256').read_text().strip():
                raise ValueError('CACHE_CHECKSUM_FAILED: '+key)
        elif self.offline:
            raise FileNotFoundError('ARCHIVE_NOT_CACHED: '+key)
        else:
            try:
                body = get(ARCHIVE+requests.utils.quote(key, safe='/'))
                if body is None:
                    self.note(dict(key=key, status='NOT_PUBLISHED')); return []
                check = get(ARCHIVE+requests.utils.quote(key+'.CHECKSUM', safe='/'))
                digest = hashlib.sha256(body).hexdigest()
                if check is None or digest != check.decode().split()[0]:
                    raise ValueError('OFFICIAL_CHECKSUM_FAILED: '+key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body); path.with_suffix('.sha256').write_text(digest)
                self.note(dict(key=key, status='OK', sha256=digest, bytes=len(body)))
            except Exception as e:
                self.note(dict(key=key, status='ERROR', error=type(e).__name__)); raise
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            files = z.namelist()
            if len(files) != 1 or not files[0].endswith('.csv'):
                raise ValueError('INVALID_ZIP_CONTENTS')
            return list(csv.reader(io.StringIO(z.read(files[0]).decode())))

    def candles(self, symbol, interval, period, source='futures', daily=False):
        root = 'futures/um' if source == 'futures' else 'spot'
        key = f'data/{root}/{"daily" if daily else "monthly"}/klines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip'
        out = []
        for r in self.zip_rows(key):
            if not r or not r[0].isdigit():
                continue
            factor = 1000 if int(r[0]) > 100_000_000_000_000 else 1
            out.append(Candle(int(r[0])//factor, (int(r[6])+1)//factor,
                              *map(float, r[1:6]), float(r[7])))
        return out

    def funding(self, symbol, period):
        key = f'data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{period}.zip'
        rows = self.zip_rows(key)
        return [dict(time=int(r[0]), interval_hours=float(r[1]), rate=float(r[2]))
                for r in rows if r and r[0].isdigit()]

    def range(self, symbol, interval, start, end, source='futures'):
        out = []
        for m in months(start, end):
            rows = self.candles(symbol, interval, m, source)
            # The current month may not yet have a monthly bundle. Use published daily files.
            if not rows and m == month(end-1):
                d = ms(m+'-01')
                while d < end:
                    if d+DAY > start:
                        out.extend(self.candles(symbol, interval, iso(d)[:10], source, daily=True))
                    d += DAY
            out.extend(rows)
        by_time = {}
        for b in out:
            if start <= b.open_ms and b.close_ms <= end:
                if b.open_ms in by_time and b != by_time[b.open_ms]:
                    raise ValueError('CONFLICTING_DUPLICATE_CANDLE')
                by_time[b.open_ms] = b
        return [by_time[t] for t in sorted(by_time)]


def select_universe(daily_by_symbol, at, n=50):
    """No future row, listing date, future delisting or future liquidity is consulted."""
    ranking = []
    for symbol, bars in daily_by_symbol.items():
        if symbol in EXCLUDED or not symbol.endswith('USDT') or '_' in symbol:
            continue
        closed = [b for b in bars if b.close_ms <= at]
        if len(closed) < 60 or closed[0].open_ms > at-60*DAY:
            continue
        recent = [b for b in closed if at-30*DAY <= b.open_ms < at]
        if len(recent) != 30 or any(b.open_ms != at-30*DAY+i*DAY for i,b in enumerate(recent)):
            continue
        ranking.append(dict(symbol=symbol, quote_volume_30d=sum(b.quote_volume for b in recent),
                            first_observed_ms=closed[0].open_ms, latest_used_ms=recent[-1].close_ms,
                            selection_ms=at))
    return sorted(ranking, key=lambda x: (-x['quote_volume_30d'], x['symbol']))[:n]


def collect_universe(archive, start, end, workers=16):
    prefixes = archive.listing('data/futures/um/monthly/klines/', refresh=True)['prefixes']
    symbols = sorted(p.split('/')[-2] for p in prefixes if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2])
    begin = ms(month(start)+'-01')-60*DAY
    daily, failures = {}, []
    def one(symbol):
        if symbol in EXCLUDED:
            return symbol, []
        prefix = f'data/futures/um/monthly/klines/{symbol}/1d/'
        keys = archive.listing(prefix, refresh=True)['keys']
        allowed = set(months(begin, end))
        rows = []
        for key in keys:
            if key.endswith('.zip') and key[-11:-4] in allowed:
                rows.extend(archive.candles(symbol, '1d', key[-11:-4]))
        # At a new month boundary the preceding month's bundle may not yet
        # exist. Its published daily archives carry the same original volume.
        last_selection = ms(list(months(start,end))[-1]+'-01')
        required_month = month(last_selection-1)
        if not any(month(b.open_ms)==required_month for b in rows):
            daily_prefix=f'data/futures/um/daily/klines/{symbol}/1d/'
            daily_keys=archive.listing(daily_prefix, refresh=True)['keys']
            for key in daily_keys:
                if key.endswith('.zip') and key[-14:-7]==required_month:
                    rows.extend(archive.candles(symbol,'1d',key[-14:-4],daily=True))
        return symbol, sorted(rows, key=lambda b: b.open_ms)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        jobs = {ex.submit(one, s): s for s in symbols}
        for i,f in enumerate(as_completed(jobs), 1):
            try:
                s, rows = f.result(); daily[s] = rows
            except Exception as e:
                failures.append(dict(symbol=jobs[f], error=type(e).__name__, detail=str(e)[:200]))
            if i % 25 == 0 or i == len(jobs):
                print(json.dumps(dict(stage='universe', processed=i, total=len(jobs), errors=len(failures))), flush=True)
    selections = {}
    for m in months(start, end):
        selections[m] = select_universe(daily, ms(m+'-01'))
    result = dict(source='futures', start=start, end=end, catalog_symbols=len(symbols),
                  excluded=sorted(EXCLUDED), errors=failures, months=selections,
                  ranking_source='official futures 1d quote_volume, exact trailing 30 complete UTC days',
                  classification='USDT suffix/no expiry suffix; explicit stablecoin/index exclusion registry')
    (archive.root/'universe.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--cache', required=True)
    p.add_argument('--start', default='2023-10-23'); p.add_argument('--end', default='2026-10-23')
    p.add_argument('--workers', type=int, default=16)
    a = p.parse_args(); collect_universe(Archive(a.cache), ms(a.start), ms(a.end), a.workers)
