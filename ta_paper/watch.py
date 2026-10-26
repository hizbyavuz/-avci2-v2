"""Technical watch candidates are observations, never paper fills or risk approval."""
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

from .core import CFG, finite
from .io import encode
from .observations import setup_id

WATCH_REVISION = 'TECHNICAL_WATCH_20261026_1'


def risk_note(reason, evidence=None):
    names = {
        'BOOK_IMPACT_EXCEEDS_MODEL':'Örnek işlem büyüklüğünde fiyat kayması sınırı aşılıyor.',
        'INSUFFICIENT_BOOK_DEPTH':'Örnek işlem büyüklüğü için emir defteri yetersiz.',
        'SPREAD_TOO_WIDE':'Alış-satış fiyat farkı sanal test sınırının üzerinde.',
        'STALE_BOOK':'Emir defteri güncelliği doğrulanamadı.',
        'EMPTY_BOOK':'Emir defteri doğrulanamadı.',
        'MAX_OPEN_POSITIONS':'Sanal portföyün açık işlem kapasitesi dolu.',
        'SAME_DIRECTION_LIMIT':'Sanal portföyün aynı yöndeki işlem sınırı dolu.',
        'MAX_SAME_DIRECTION':'Sanal portföyün aynı yöndeki işlem sınırı dolu.',
        'SAME_DIRECTION_CLUSTER_LIMIT':'Sanal portföyün aynı yöndeki işlem sınırı dolu.',
        'SYMBOL_COOLDOWN':'Sanal portföyde bu coin için tekrar işlem beklemesi var.',
        'UNRESOLVED_OPEN_RISK':'Sanal portföyde sonucu doğrulanmamış açık risk var.',
        'NO_RISK_ROOM':'Sanal portföyün risk bütçesi dolu.',
        'PORTFOLIO_OR_DAILY_RISK_LIMIT':'Sanal portföyün toplam veya günlük risk bütçesi dolu.',
        'DAILY_LOSS_LIMIT':'Sanal portföyün günlük zarar sınırına ulaşıldı.',
        'BELOW_EXCHANGE_MINIMUM':'Örnek risk bütçesiyle hesaplanan miktar borsa minimumunun altında.',
        'COST_ADJUSTED_REWARD_TOO_SMALL':'Masraflar sonrası ödül/risk sanal test için yetersiz.',
        'STOP_DISTANCE_OUTSIDE_LIMITS':'Güncel giriş hesabında stop uzaklığı test sınırlarını aşıyor.',
        'LATE_ENTRY':'Güncel fiyat teknik kovalama sınırını geçmiş; yeni giriş için geç olabilir.',
        'INVALID_PLAN_GEOMETRY':'Güncel giriş fiyatıyla stop/hedef düzeni uygun değil.',
        'BOOK_NOT_VERIFIED':'Güncel emir defteri ve işlem uygunluğu henüz doğrulanmadı.',
    }
    text=names.get(reason,'Sanal işlem kabulü doğrulanamadı; güncel likidite ve giriş ayrıca kontrol edilmeli.')
    if evidence and evidence.get('impact_fraction') is not None:
        text += (f" Ölçülen kayma %{100*evidence['impact_fraction']:.4f}; "
                 f"test sınırı %{100*evidence['impact_limit']:.4f}.")
    return text


def watch_message(p):
    long=p['direction']=='LONG'
    at=datetime.fromtimestamp(p['observed_ms']/1000,timezone.utc).astimezone(
        ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')
    return (f"{'🟢' if long else '🔴'} {'LONG' if long else 'SHORT'} İÇİN İZLE | {p['symbol']}\n"
            '30 dk dönüş koşulu oluştu · 4 saat yönü uyumlu.\n'
            f"Sinyal kapanışı: {p['reference_entry']:.8g}\n"
            f"Teknik stop: {p['stop']:.8g} · Teknik hedef: {p['target']:.8g}\n"
            f"Kovalama sınırı: {p['entry_limit']:.8g} {'üstü' if long else 'altı'}.\n"
            f"Sanal hesap uyarısı: {p['risk_note']}\n"
            'İzleme adayıdır; giriş/gerçekleşme onayı değildir.\n'
            'İşlem büyüklüğü ve giriş kararı sana ait. Gerçek emir yok.\n'
            f'{at} TSİ · Kapanış fiyatı canlı fiyat değildir. Başarı DOĞRULANMADI.')


def publish_watches(store, client, state, cfg=CFG):
    """Expose fresh SETUPs even if the example portfolio cannot accept them.

    No network calls, no threshold changes, no writes to plans/account results.
    Accepted plans keep their existing card; pending watch cards are superseded.
    """
    now=client.now(); queued=0
    for symbol,original in state.get('done',{}).items():
        if original.get('status')!='SETUP':continue
        key=setup_id(original);outbox_key='watch:'+key
        accepted=state.get('handled',{}).get(symbol)=='ACCEPTED'
        if accepted or store.db.execute('SELECT 1 FROM plans WHERE id=?',(key,)).fetchone():
            store.db.execute("UPDATE watch_candidates SET status='SUPERSEDED' WHERE id=?",(key,))
            store.db.execute("UPDATE outbox SET state='STALE' WHERE key=? AND state='PENDING'",(outbox_key,))
            store.db.commit();continue
        # Do not resend old/rejected examples after a deploy or a late retry.
        close=original['candle_ms']+cfg.entry_ms
        deadline=min(state['deadline_ms'],close+cfg.max_scan_delay_ms-1)
        if now>deadline or now<close+cfg.publication_delay_ms:continue
        old=store.db.execute('SELECT payload FROM watch_candidates WHERE id=?',(key,)).fetchone()
        note=state.get('admission_notes',{}).get(symbol,{})
        reason=state.get('handled',{}).get(symbol,note.get('reason','BOOK_NOT_VERIFIED'))
        if reason=='SCAN_FINISHED_TOO_LATE':continue
        p=dict(original,id=key,status='WATCH',entry=original['reference_entry'],
               watch_revision=WATCH_REVISION,valid_until_ms=deadline,
               risk_reason=reason,risk_note=risk_note(reason,note.get('book')),
               watch_git_commit=store.provenance['git_commit'],
               watch_source_sha256=store.provenance['source_sha256'])
        for field in ('entry','stop','target','entry_limit'):finite(p[field],True)
        if old and json.loads(old['payload']).get('risk_reason')==reason:continue
        photo=None
        try:
            from .chart import render_plan
            photo=render_plan(p,store.chart_bars(p))
        except Exception as exc:
            store.event('WATCH_CHART_ERROR',key,{'type':type(exc).__name__},now)
        store.db.execute('INSERT INTO watch_candidates VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                         (key,symbol,now,deadline,'ACTIVE',encode(p)))
        store.db.commit()
        store.queue(outbox_key,watch_message(p),now,photo=photo)
        # A retry can enrich an unsent watch with the actual rejection reason.
        store.db.execute("UPDATE outbox SET text=?,photo=? WHERE key=? AND state='PENDING'",
                         (watch_message(p),photo,outbox_key))
        store.db.commit()
        store.event('WATCH_CANDIDATE',key,p,now)
        print('TA_PAPER_WATCH '+encode(dict(symbol=symbol,direction=p['direction'],
              reason=reason,watch_revision=WATCH_REVISION,valid_until_ms=deadline)),flush=True)
        queued+=1
    return queued
