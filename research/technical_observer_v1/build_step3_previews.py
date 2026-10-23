"""Offline historical illustration. No live scan, notification, trade or backtest.

Use two pre-existing fixed snapshots, including every new setup they produce.
Do not select examples by later price performance. Verify their actual candles
against the checksum-verified archive; never synthesize missing market data.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from structure_engine.archive import archive, aggregate
from technical_observer_v1 import observe
from technical_observer_v1.records import build_record, read_record, write_record
from technical_observer_v1.presentation import message, render


def build(cache, output):
    root = Path(__file__).resolve().parents[2]
    source_dir = root/'research/structure_context_v1/evidence-release/examples'
    manifest, minutes = archive(cache, 'BTCUSDT', '2026-08', offline=True)
    index = {tf: {b['open_ms']: b for b in aggregate(minutes, tf)} for tf in ('15m', '1h', '4h')}
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    items = []
    for name in ('BTCUSDT-CONDITION_MET-LONG', 'BTCUSDT-CONDITION_MET-SHORT'):
        source_path = source_dir/(name+'.json.gz')
        old = json.loads(gzip.decompress(source_path.read_bytes()))
        frames = {tf: old['frames'][tf] for tf in index}
        for tf, rows in frames.items():
            for row in rows:
                expected = index[tf][row['open_ms']]
                if any(row[key] != expected[key] for key in expected):
                    raise ValueError('SNAPSHOT_DOES_NOT_MATCH_ARCHIVE:'+tf)
        provenance = dict(kind='ARCHIVE_PREVIEW', archive=manifest,
                          snapshot=str(source_path.relative_to(root)),
                          snapshot_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                          all_input_candles_match_archive=True,
                          limitation='Fixed finite input history; not a rolling-prefix invariance proof.',
                          historical_tick_metadata='DOĞRULANMADI')
        o = observe('BTCUSDT', frames, old['assessment']['asof_ms'], provenance=provenance)
        for number, setup in enumerate(o['setups']):
            r = build_record(o, setup['id'], frames, tick_snapshot=None)
            path = write_record(r, output/'records')
            saved = read_record(path)
            prefix = output/(name+f'-{number}')
            prefix.with_suffix('.txt').write_text(message(saved)+'\n')
            render(saved, prefix.with_suffix('.png'))
            items.append(dict(record=str(path.relative_to(output)), chart=prefix.name+'.png',
                              message=prefix.name+'.txt', record_hash=saved['record_hash'],
                              state=saved['display']['state']))
    (output/'manifest.json').write_text(json.dumps(dict(kind='HISTORICAL_PRESENTATION_PREVIEW_NOT_BACKTEST',
        examples=items, source=manifest, future_prices_used=False), ensure_ascii=False, indent=2)+'\n')
    return items


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.cache, args.output), ensure_ascii=False, indent=2))
