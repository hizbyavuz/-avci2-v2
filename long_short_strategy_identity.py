"""Effective code/settings identity, separate from historical frozen configs."""
import hashlib
import importlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

VERSION='LS_LOCAL_LEVEL_AND_BROAD_DISCOVERY_V5_2026_10_15'


@lru_cache(maxsize=1)
def source_hash():
    root=Path(__file__).resolve().parent
    paths=set(root.glob('long_short*.py'))|set(root.glob('binance_futures*.py'))
    paths.add(root/'infra/futures_history_relay.ts')
    files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def snapshot():
    # Effective numeric constants include environment overrides, not credentials
    # or private URLs. Source hash also captures hardcoded weights and defaults.
    settings={}
    for module in ('long_short_analyst','long_short_direction_engine','long_short_live_pool',
                   'long_short_trade_ledger','long_short_telegram_delivery_integrity'):
        values={k:v for k,v in vars(importlib.import_module(module)).items()
                if k.isupper() and not k.startswith('_') and isinstance(v,(int,float,bool))
                and math.isfinite(v)}
        settings[module]=values
    settings['modes']={k:os.getenv(k,'0') for k in (
        'LS_NATIVE_CHAIN_V2','LS_BINANCE_FUTURES_ONLY','LS_STRUCTURAL_PLAN_V3',
        'LS_QUALIFIED_SIGNAL_CHAIN_V1','LS_NATIVE_FUTURES_SNAPSHOT')}
    # Include additional numeric/boolean LS overrides used inside functions.
    # Explicit defaults may create a conservative new cohort; secrets/paths do not.
    settings['numeric_overrides']={}
    for key,value in os.environ.items():
        if not key.startswith('LS_') or any(x in key for x in
            ('TOKEN','SECRET','KEY','PASSWORD','URL','ENDPOINT','CHAT','BUCKET','_DB','PATH','DIR')):continue
        try:
            if math.isfinite(float(value)):settings['numeric_overrides'][key]=value
        except (ValueError,TypeError):
            if value.lower() in ('true','false','yes','no','on','off'):
                settings['numeric_overrides'][key]=value.lower()
    body={'version':VERSION,'source_sha256':source_hash(),'settings':settings}
    body['id']=hashlib.sha256(json.dumps(body,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return body


def event_identity(locked_gate):
    current=reference()
    plan=(locked_gate.get('_strategy_identity') or {}).get('id','LEGACY_UNVERSIONED')
    # Even a legacy locked plan executed by new code is a separate cohort.
    current['locked_plan_id']=plan
    current['cohort_id']=hashlib.sha256((current['id']+':'+plan).encode()).hexdigest()
    return current


def reference():
    body=snapshot()
    return {key:body[key] for key in ('version','id','source_sha256')}


def persist(con):
    body=snapshot()
    con.execute('CREATE TABLE IF NOT EXISTS strategy_manifests(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    con.execute('INSERT OR IGNORE INTO strategy_manifests VALUES(?,?)',
                (body['id'],json.dumps(body,sort_keys=True)))
    return reference()
