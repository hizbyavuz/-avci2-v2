"""Separate archive feature reader. Never changes the frozen replay's candles."""
import csv
from datetime import datetime, timezone
import hashlib
import io
import re
import zipfile

from .archive import Archive, BASE
from .evidence import number


def candle_features(row):
    if len(row)<11:raise ValueError('TAKER_COLUMNS_MISSING')
    at,end=int(row[0]),int(row[6])
    volume,quote,buy_base,buy_quote=map(number,(row[5],row[7],row[9],row[10]))
    if (at<0 or end<at or at>100_000_000_000_000 or min(volume,quote,buy_base,buy_quote)<0 or
            buy_base>volume+1e-8 or buy_quote>quote+1e-6):raise ValueError('INVALID_TAKER_COLUMNS')
    return dict(open_ms=at,close_ms=end,quote_volume=quote,taker_buy_quote=buy_quote,
                taker_sell_quote=quote-buy_quote,taker_buy_base=buy_base,
                taker_buy_share=buy_quote/quote if quote else None,trades=int(row[8]))


class FeatureArchive(Archive):
    def metrics_file(self,symbol,day):
        if not re.fullmatch(r'[A-Z0-9]+USDT',symbol) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
            raise ValueError('INVALID_METRICS_KEY')
        key=f'data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip'
        path=self.cache/key;checksum=path.with_suffix('.sha256')
        if path.exists() and checksum.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=checksum.read_text().strip():
                raise ValueError('CACHED_METRICS_CHECKSUM_MISMATCH')
            return path
        expected=self.request(BASE+key+'.CHECKSUM').decode().split()[0]
        if not re.fullmatch('[a-fA-F0-9]{64}',expected):raise ValueError('INVALID_METRICS_CHECKSUM')
        data=self.request(BASE+key)
        if hashlib.sha256(data).hexdigest()!=expected.lower():raise ValueError('METRICS_CHECKSUM_MISMATCH')
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_bytes(data);temp.replace(path)
        checksum.write_text(expected.lower()+'\n')
        return path

    @staticmethod
    def csv_rows(path,dictionaries=False):
        with zipfile.ZipFile(path) as z:
            names=z.namelist()
            if len(names)!=1 or z.getinfo(names[0]).file_size>300_000_000:raise ValueError('INVALID_FEATURE_ARCHIVE')
            with z.open(names[0]) as stream:
                reader=csv.DictReader if dictionaries else csv.reader
                yield from reader(io.TextIOWrapper(stream,encoding='utf-8-sig'))

    def taker_rows(self,symbol,interval,months):
        rows=[]
        for month in months:
            for row in self.csv_rows(self.file(symbol,interval,month)):
                if row and row[0].isdigit():rows.append(candle_features(row))
        rows.sort(key=lambda r:r['open_ms'])
        if len({r['open_ms'] for r in rows})!=len(rows):raise ValueError('DUPLICATE_FEATURE_CANDLE')
        return rows

    def metrics_rows(self,symbol,day):
        rows=[];seen=set()
        for raw in self.csv_rows(self.metrics_file(symbol,day),True):
            at=int(datetime.strptime(raw['create_time'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()*1000)
            if raw['symbol']!=symbol or at in seen:raise ValueError('INVALID_METRICS_SYMBOL_OR_DUPLICATE')
            seen.add(at)
            row={key:number(value) if value not in ('',None) else None for key,value in raw.items()
                 if key not in ('symbol','create_time')}
            row.update(symbol=symbol,event_ms=at,available_ms_assumption=at+6*60_000,
                       publication_time_verified=False)
            rows.append(row)
        return sorted(rows,key=lambda r:r['event_ms'])


def hypothesis_results(features,direction):
    """Independent research hypotheses, NEVER imported by signal selection."""
    sign=1 if direction=='LONG' else -1
    share=features.get('taker_buy_share_30m')
    oi=features.get('oi_change_4h');move=features.get('return_4h')
    funding=features.get('funding_percentile_30d');relative=features.get('relative_return_3d')
    return dict(
        H1=None if share is None else share>.55 if sign==1 else share<.45,
        H2=None if oi is None or move is None else oi>0 and sign*move>0,
        H3=None if funding is None else funding<.90 if sign==1 else funding>.10,
        H4=None if relative is None else sign*relative>0)
