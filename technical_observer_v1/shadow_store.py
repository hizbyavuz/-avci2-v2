"""Separate append-only shadow database and content-addressed evidence blobs."""
import gzip
import json
import os
from pathlib import Path
import resource
import sqlite3
import tempfile

from structure_engine.config import CONFIG as OLD_CONFIG, CONFIG_HASH as OLD_HASH
from .config import DEFAULTS
from .records import canonical, digest
from .shadow_config import VERSION, OPS, OPS_HASH


def pack(value):
    return gzip.compress(canonical(value).encode(), mtime=0)


def unpack(value):
    return json.loads(gzip.decompress(value))


def connect(path, started_ms):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA cache_size=-2048')
    c.executescript('''
      CREATE TABLE IF NOT EXISTS manifest(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS activation(id INTEGER PRIMARY KEY,started_ms INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS streams(symbol TEXT,tf TEXT,start_ms INTEGER,PRIMARY KEY(symbol,tf));
      CREATE TABLE IF NOT EXISTS quarantine(symbol TEXT,tf TEXT,reason TEXT,observed_ms INTEGER,payload TEXT,
        PRIMARY KEY(symbol,tf));
      CREATE TABLE IF NOT EXISTS bars(symbol TEXT,tf TEXT,open_ms INTEGER,close_ms INTEGER,
        value_hash TEXT,payload BLOB,PRIMARY KEY(symbol,tf,open_ms));
      CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY,symbol TEXT,bar_ms INTEGER,observed_ms INTEGER,
        cohort TEXT,pair_ready INTEGER,group_id TEXT,evidence TEXT,payload BLOB);
      CREATE INDEX IF NOT EXISTS shadow_scan_symbol ON scans(symbol,bar_ms);
      CREATE TABLE IF NOT EXISTS setups(id TEXT PRIMARY KEY,scan_id TEXT,symbol TEXT,side TEXT,
        observed_ms INTEGER,record_hash TEXT,evidence TEXT,payload BLOB);
      CREATE TABLE IF NOT EXISTS groups_v1(id TEXT PRIMARY KEY,symbol TEXT,observed_ms INTEGER,
        start_ms INTEGER,end_ms INTEGER,cohort TEXT,pair_ready INTEGER,scan_id TEXT);
      CREATE INDEX IF NOT EXISTS shadow_group_symbol ON groups_v1(symbol,end_ms);
      CREATE TABLE IF NOT EXISTS membership(scan_id TEXT PRIMARY KEY,group_id TEXT);
      CREATE TABLE IF NOT EXISTS outcomes(group_id TEXT PRIMARY KEY,measured_ms INTEGER,
        complete INTEGER,evidence TEXT,payload BLOB);
      CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY,observed_ms INTEGER,symbol TEXT,kind TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS health(id TEXT PRIMARY KEY,observed_ms INTEGER,payload TEXT);
    ''')
    code_files = list(Path(__file__).parent.glob('*.py'))
    code_files += [Path(__file__).resolve().parents[1]/'structure_engine/core.py',
                   Path(__file__).resolve().parents[1]/'structure_engine/config.py']
    base = Path(__file__).resolve().parents[1]
    code_hash = digest({str(p.relative_to(base)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in sorted(code_files)})
    manifest = canonical(dict(version=VERSION,ops=OPS,ops_hash=OPS_HASH,config=DEFAULTS.payload(),
        config_hash=DEFAULTS.fingerprint(),old_config_hash=OLD_HASH,code_hash=code_hash,
        no_automatic_promotion=True))
    existing = c.execute('SELECT payload FROM manifest WHERE id=?',(VERSION,)).fetchone()
    if existing and existing[0] != manifest:
        c.close(); raise ValueError('SHADOW_FROZEN_MANIFEST_CHANGED')
    c.execute('INSERT OR IGNORE INTO manifest VALUES(?,?)',(VERSION,manifest))
    c.execute('INSERT OR IGNORE INTO activation VALUES(1,?)',(started_ms,))
    c.commit()
    return c


def write_blob(directory, value):
    key = digest(value); directory = Path(directory)/'evidence'; directory.mkdir(parents=True,exist_ok=True)
    path = directory/(key+'.json.gz'); data = pack(value)
    fd, temp = tempfile.mkstemp(prefix='.blob-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        try:
            os.link(temp,path)
        except FileExistsError:
            if path.read_bytes()!=data: raise ValueError('SHADOW_EVIDENCE_COLLISION_NO_OVERWRITE')
    finally:
        os.unlink(temp)
    return str(path.relative_to(directory.parent))


def read_blob(directory, relative):
    path = Path(directory)/relative
    value = unpack(path.read_bytes())
    if path.name != digest(value)+'.json.gz': raise ValueError('SHADOW_EVIDENCE_CORRUPT')
    return value


def issue(c, now, symbol, kind, detail=None):
    # Once per minute/type/content. Preserve failures, avoid a busy-loop log flood.
    payload = dict(detail=detail or {},decision_effect='NONE')
    key = digest([symbol,now//60_000,kind,payload])
    c.execute('INSERT OR IGNORE INTO issues VALUES(?,?,?,?,?)',(key,now,symbol,kind,canonical(payload)))
    c.commit()


def limits(directory):
    directory = Path(directory)
    used = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())/1048576
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    reasons = []
    if used >= OPS['storage_limit_mb']: reasons.append('SHADOW_STORAGE_LIMIT')
    if rss >= OPS['rss_limit_mb']: reasons.append('SHADOW_RSS_LIMIT')
    # The existing global guard remains authoritative and unchanged.
    from structure_engine.store import resource_check
    global_limit = resource_check(directory.parent)
    if not global_limit['ok']: reasons += global_limit['reasons']
    return dict(ok=not reasons,reasons=reasons,shadow_mb=round(used,3),shadow_peak_rss_mb=round(rss,3))


def assign_group(c,symbol,now,bar_ms,scan_id,pair_ready):
    activation = c.execute('SELECT started_ms FROM activation WHERE id=1').fetchone()[0]
    cohort = 'FORWARD' if bar_ms >= activation else 'STARTUP_REPLAY'
    start = (now//60_000+OLD_CONFIG['entry_delay_minutes'])*60_000
    end = start+OLD_CONFIG['horizon_hours']*3_600_000
    old = c.execute('SELECT * FROM groups_v1 WHERE symbol=? AND end_ms>? ORDER BY observed_ms DESC LIMIT 1',
                    (symbol,start)).fetchone()
    if old:
        group_id = old['id']
    else:
        group_id = digest([VERSION,symbol,now,scan_id])
        c.execute('INSERT INTO groups_v1 VALUES(?,?,?,?,?,?,?,?)',
                  (group_id,symbol,now,start,end,cohort,int(pair_ready),scan_id))
    c.execute('INSERT OR IGNORE INTO membership VALUES(?,?)',(scan_id,group_id))
    return group_id,cohort


def counters(c):
    complete = c.execute("""SELECT COUNT(*) FROM groups_v1 g JOIN outcomes o ON o.group_id=g.id
        WHERE g.cohort='FORWARD' AND g.pair_ready=1 AND o.complete=1""").fetchone()[0]
    return dict(scans=c.execute('SELECT COUNT(*) FROM scans').fetchone()[0],
        new_setups=c.execute('SELECT COUNT(*) FROM setups').fetchone()[0],
        deduplicated_groups=c.execute('SELECT COUNT(*) FROM groups_v1').fetchone()[0],
        completed_forward_groups=complete,required_completed_groups=OPS['required_completed_groups'],
        review_ready=complete>=OPS['required_completed_groups'],automatic_promotion=False,
        unresolved=c.execute('SELECT COUNT(*) FROM outcomes WHERE complete=0').fetchone()[0])
