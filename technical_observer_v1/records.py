"""Append-only, offline presentation snapshots. No Telegram, DB or order API.

Raw analytical evidence is preserved. Tick rounding is a separate recorded view;
it never feeds back into the observer or the production safety gates.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import tempfile

from structure_engine.config import CONFIG as EXISTING_CONFIG, CONFIG_HASH as EXISTING_CONFIG_HASH
from .engine import _id

SCHEMA = 'TECHNICAL_OBSERVER_PRESENTATION_V1_2026_10_23'
EXCHANGE_INFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('BOOLEAN_PRICE')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('INVALID_DECIMAL') from exc
    if not result.is_finite():
        raise ValueError('NONFINITE_DECIMAL')
    return result


def capture_price_filter(response, symbol, observed_ms):
    """Extract a supplied exchangeInfo response; does NOT authenticate its source.

    The collector must retain actual response provenance. Do not substitute
    pricePrecision, a guessed tick, or today's metadata for historical metadata.
    """
    markets = [m for m in response['symbols'] if m['symbol'] == symbol]
    if len(markets) != 1:
        raise ValueError('SYMBOL_NOT_UNIQUE_IN_EXCHANGE_INFO')
    filters = [f for f in markets[0]['filters'] if f['filterType'] == 'PRICE_FILTER']
    if len(filters) != 1:
        raise ValueError('PRICE_FILTER_NOT_UNIQUE')
    return dict(symbol=symbol, observed_ms=observed_ms, source_url=EXCHANGE_INFO_URL,
                source_verification='CALLER_SUPPLIED_RESPONSE_NOT_AUTHENTICATED_BY_THIS_MODULE',
                response_sha256=digest(response), price_filter=deepcopy(filters[0]))


def price_grid(snapshot, symbol, asof_ms):
    result = dict(status='DOĞRULANMADI', snapshot=deepcopy(snapshot), reason='PRICE_FILTER_MISSING')
    if snapshot is None:
        return result
    try:
        if snapshot['symbol'] != symbol:
            raise ValueError('PRICE_FILTER_SYMBOL_MISMATCH')
        if snapshot['source_url'] != EXCHANGE_INFO_URL:
            raise ValueError('PRICE_FILTER_SOURCE_MISMATCH')
        observed = snapshot['observed_ms']
        if type(observed) is not int or observed <= 0 or observed > asof_ms:
            raise ValueError('PRICE_FILTER_TIME_NOT_VALID_FOR_DECISION')
        f = snapshot['price_filter']
        if f['filterType'] != 'PRICE_FILTER':
            raise ValueError('WRONG_FILTER_TYPE')
        lo, hi, tick = [decimal(f[k]) for k in ('minPrice', 'maxPrice', 'tickSize')]
        if lo < 0 or hi < 0 or tick <= 0 or (hi and hi < lo):
            raise ValueError('PRICE_GRID_INVALID_OR_DISABLED')
        result.update(status='SUPPLIED_PRICE_FILTER', reason='GRID_CHECKED_SOURCE_IS_CALLER_RESPONSIBILITY',
                      tick=str(tick), origin=str(lo), maximum=str(hi), age_ms=asof_ms-observed,
                      freshness_verified=False)
    except (KeyError, TypeError, ValueError) as exc:
        result['reason'] = str(exc)
    return result


def rounded_price(value, grid, mode):
    if value is None:
        return None
    price = decimal(value)
    if grid['status'] != 'SUPPLIED_PRICE_FILTER':
        return format(price, 'f')
    tick, origin, maximum = [decimal(grid[k]) for k in ('tick', 'origin', 'maximum')]
    rounding = {'UP': ROUND_CEILING, 'DOWN': ROUND_FLOOR, 'NEAREST': ROUND_HALF_UP}[mode]
    count = ((price-origin)/tick).to_integral_value(rounding=rounding)
    result = origin + count*tick
    if result <= 0 or result < origin or (maximum and result > maximum):
        raise ValueError('PRICE_OUTSIDE_FILTER_BOUNDS')
    places = min(tick.as_tuple().exponent, origin.as_tuple().exponent, 0)
    return format(result.quantize(Decimal(1).scaleb(places)), 'f')


def _r(direction, levels, cost):
    if any(levels[k] is None for k in ('entry', 'stop', 'target')):
        return dict(value=None, reason='MISSING_STOP_OR_TARGET')
    entry, stop, target = [decimal(levels[k]) for k in ('entry', 'stop', 'target')]
    sign = Decimal(1 if direction == 'LONG' else -1)
    if min(entry, stop, target) <= 0:
        return dict(value=None, reason='NONPOSITIVE_PRICE')
    risk, reward = 100*sign*(entry-stop)/entry, 100*sign*(target-entry)/entry
    if min(risk, reward) <= 0:
        return dict(value=None, reason='INVALID_DIRECTIONAL_GEOMETRY')
    fee = decimal(cost)
    return dict(value=str((reward-fee)/(risk+fee)), risk_pct=str(risk), reward_pct=str(reward),
                cost_pct=str(fee), reason='REFERENCE_PRICES_NOT_FILLS', funding_included=False)


def _label(value, grid):
    if value is None:
        return 'YOK · doğrulanmadı'
    return value if grid['status'] == 'SUPPLIED_PRICE_FILTER' else '≈'+format(decimal(value), '.8g')


def build_record(observation, setup_id, frames, tick_snapshot=None):
    """Build one immutable evidence/view record; no market analysis or I/O here."""
    if observation.get('mode') != 'OBSERVATION_ONLY' or observation.get('decision_effect') != 'NONE':
        raise ValueError('NOT_AN_OBSERVATION')
    if observation.get('input_hash') != _id(frames):
        raise ValueError('FRAMES_DO_NOT_MATCH_OBSERVATION')
    matches = [s for s in observation['setups'] if s['id'] == setup_id]
    if len(matches) != 1:
        raise ValueError('SETUP_NOT_UNIQUE')
    s = matches[0]
    grid = price_grid(tick_snapshot, observation['symbol'], s['asof_ms'])
    side = s['direction']
    zone = s['broken_region']
    raw = dict(band_low=zone['low'], band_high=zone['high'], entry=s['entry_reference'],
               stop=s['stop']['price'], target=s['target']['price'], last=s['breakout_bar']['close'])
    edge = 'band_high' if side == 'LONG' else 'band_low'
    if raw['entry'] != raw[edge]:
        raise ValueError('ENTRY_NOT_THE_BROKEN_REGION_EDGE')
    modes = dict(band_low='DOWN', band_high='UP', entry='UP' if side == 'LONG' else 'DOWN',
                 stop='DOWN' if side == 'LONG' else 'UP', target='DOWN' if side == 'LONG' else 'UP', last='NEAREST')
    levels, issues = {}, []
    for name, value in raw.items():
        try:
            levels[name] = rounded_price(value, grid, modes[name])
        except ValueError as exc:
            levels[name] = None
            issues.append(name.upper()+':'+str(exc))
    # The region and the entry line literally share the same edge value.
    levels['entry'] = levels[edge]
    target_region = s['target']['region']
    shown_target = None
    if target_region:
        # Round the target region conservatively: the near edge is the TP line.
        try:
            shown_target = dict(id=target_region['id'], timeframe=target_region['timeframe'],
                known_ms=target_region['known_ms'],
                low=rounded_price(target_region['low'], grid, 'DOWN'),
                high=rounded_price(target_region['high'], grid, 'UP'))
            near = 'low' if side == 'LONG' else 'high'
            if levels['target'] != shown_target[near]:
                raise ValueError('TARGET_NOT_OWN_REGION_EDGE')
        except ValueError as exc:
            issues.append(str(exc)); levels['target'] = None; shown_target = None
    if shown_target and levels['entry'] is not None:
        beyond = (decimal(shown_target['low']) > decimal(levels['band_high']) if side == 'LONG'
                  else decimal(shown_target['high']) < decimal(levels['band_low']))
        distance = abs(decimal(levels['target'])-decimal(levels['entry']))/decimal(s['atr'])
        if not beyond or distance < decimal(observation['config']['target_min_distance_atr']):
            issues.append('ROUNDING_INVALIDATED_TARGET_REGION_OR_MINIMUM_DISTANCE')
            levels['target'] = None
            shown_target = None
    risk = _r(side, levels, observation['config']['round_trip_cost_pct'])
    minimum = decimal(EXISTING_CONFIG['minimum_plan_net_r'])
    reasons = []
    if risk['value'] is None:
        state = 'SEVİYELER EKSİK / DOĞRULANMADI'
        reasons.append('Giriş, stop ve hedef geometrisi birlikte doğrulanmadı.')
    elif decimal(risk['value']) < minimum:
        state = 'R/R YETERSİZ'
        reasons.append('Kırılsa da R/R yetersiz: masraf sonrası '+format(decimal(risk['value']), '.2f')+
                       ' R, mevcut '+format(minimum, '.2f')+' R sınırının altında.')
    else:
        state = 'SEVİYE SENARYOSU · İŞLEM ONAYI DEĞİL'
        reasons.append('Referans R hesabı mevcut sınırı karşılıyor; işlem uygunluğu henüz doğrulanmadı.')
    if s['stop']['price'] is None:
        reasons.append('Stop için onaylı swing yok.')
    if s['target']['price'] is None:
        reasons.append('Onaylı hedef yok; TP üretilmedi.')
    if s['breakout']['reasons']:
        names = {'BODY_TOO_SMALL': 'mum gövdesi yetersiz', 'VOLUME_TOO_LOW': 'hacim yetersiz',
                 'VOLUME_BASELINE_UNDEFINED': 'hacim karşılaştırması doğrulanmadı'}
        reasons.append('Zayıf kırılım: '+', '.join(names.get(r, r) for r in s['breakout']['reasons'])+'.')
    if 'STOP_TOO_WIDE' in s['stop']['labels']:
        reasons.append('Stop çok geniş (>3 ATR); yalnız gözlemsel etiket.')
    if any('NEAREST_SWING_NOT_CONFIRMED' in r for r in s['stop']['labels']):
        reasons.append('En yakın stop swing’i beklenen LH/HL yapısını doğrulamıyor.')
    if s['late']['label'] == 'GEÇ':
        reasons.append('GEÇ: fiyat uzaklığı / son hareket eşiği aşıldı; yalnız gözlemsel etiket.')
    nearer = [z for z in s['target']['rejected_regions']
              if z['reason'] in ('TARGET_CLOSER_THAN_MINIMUM_ATR', 'NOT_ENTIRELY_BEYOND_BROKEN_ZONE')]
    if nearer:
        reasons.append(f'{len(nearer)} yakın/çakışan karşı bölge kayıtta var; hedefe kadar yol boş sayılmaz.')
    if grid['status'] != 'SUPPLIED_PRICE_FILTER':
        reasons.append('Tick size doğrulanmadı; ≈ fiyatlar ham analiz referansıdır.')
    else:
        reasons.append('Fiyatlar sağlanan PRICE_FILTER adımına yuvarlandı; kaynak ve geçerlilik toplayıcı sorumluluğunda.')
        if levels['entry'] is not None:
            sign = 1 if side == 'LONG' else -1
            if sign*(decimal(raw['last'])-decimal(levels['entry'])) <= 0:
                reasons.append('Yuvarlanmış bölge kenarının dışında kapanış doğrulanmadı; ham kırılım ayrı kayıtta.')
    reasons.extend('Çizim doğrulaması: '+r for r in issues)
    captions = {k: _label(v, grid) for k, v in levels.items()}
    rows = frames['15m'][-64:]
    dots = []
    for p in observation['decision_basis']['contexts']['15m']['swings']['confirmed']:
        if p['open_ms'] >= rows[0]['open_ms']:
            # Candle extrema are observed exchange prices, not interpolated levels.
            dots.append(dict(id=p['id'], open_ms=p['open_ms'], price=str(p['price']), kind=p['kind']))
    anchor = s['stop'].get('anchor')
    view = dict(state=state, reasons=reasons, levels=levels, price_labels=captions, net_r=risk,
                trend_labels=deepcopy(s['trend_labels']), candles=deepcopy(rows), swing_dots=dots,
                broken_region=dict(id=zone['id'], snapshot_id=zone['snapshot_id'], timeframe=zone['timeframe'],
                                   low=levels['band_low'], high=levels['band_high'], known_ms=zone['known_ms']),
                target_region=shown_target, stop_anchor_id=anchor['id'] if anchor else None,
                stop_anchor_in_view=bool(anchor and anchor['open_ms'] >= rows[0]['open_ms']),
                entry_kind='REGION_EDGE_REFERENCE_NOT_EXECUTABLE_ENTRY',
                level_layout_valid=risk['value'] is not None, issues=issues)
    # Full source evidence: both pre-break and current TF structures, all regions,
    # all accepted/rejected swings, reasons and every input candle. No recompute.
    evidence = deepcopy({k: v for k, v in observation.items() if k != 'setups'})
    evidence['setup'] = deepcopy(s)
    result = dict(schema=SCHEMA, symbol=observation['symbol'], direction=side, timeframe='15m',
                  asof_ms=s['asof_ms'], setup_id=setup_id, decision_effect='NONE',
                  order_authorized=False, evidence=evidence, frames=deepcopy(frames), grid=grid, display=view,
                  presentation_policy=dict(minimum_net_r=str(minimum), source='structure_engine.config.CONFIG',
                                           source_config_hash=EXISTING_CONFIG_HASH, new_filter=False))
    # Normalize tuples/config containers now, so in-memory and reloaded JSON agree.
    result = json.loads(canonical(result))
    result['record_hash'] = digest(result)
    return result


def validate_record(record):
    body = {k: v for k, v in record.items() if k != 'record_hash'}
    if record.get('schema') != SCHEMA or record.get('record_hash') != digest(body):
        raise ValueError('RECORD_INTEGRITY_MISMATCH')
    if record['decision_effect'] != 'NONE' or record['order_authorized'] is not False:
        raise ValueError('OBSERVATION_CANNOT_AUTHORIZE_ORDER')
    return record


def write_record(record, directory):
    """Atomic exclusive append; identical retry is safe, an existing file never overwritten."""
    validate_record(record)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/(record['record_hash']+'.json')
    data = (canonical(record)+'\n').encode()
    fd, temp = tempfile.mkstemp(prefix='.record-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError('EXISTING_RECORD_DIFFERS_NO_OVERWRITE')
    finally:
        os.unlink(temp)
    return path


def read_record(path):
    return validate_record(json.loads(Path(path).read_text()))


def record_observation(observation, frames, directory, tick_snapshot=None):
    """Explicit offline caller API: save EVERY setup, including inadequate/weak ones.

    No scan scheduler or automatic publishing is introduced. Live collection,
    rejected scan candidates and independent-sample grouping belong to step 5.
    """
    return [write_record(build_record(observation, s['id'], frames, tick_snapshot), directory)
            for s in observation['setups']]
