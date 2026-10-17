"""Read-only streaming comparison of frozen signal evidence and later price paths."""
import argparse
import json
import math
import time
from collections import Counter
from datetime import datetime,timezone

from long_short_major_move import finite
from long_short_major_move_store import EVALUATION_HOURS,LEGACY_EVALUATION_HOURS,db,root

LABELS={'SUPPORT':'Akış ve fiyat yönü destekliyor','CONFLICT':'Akış ve fiyat ters yönde',
        'MIXED':'Akış ile fiyat çelişiyor','NEUTRAL':'Nötr','MISSING':'Veri eksik',
        'LEGACY_NO_CONTEXT':'Bu sürümden önce; sinyal anı türev kaydı yok'}


class Stats:
    def __init__(self):
        self.counts=Counter();self.direction_sum=0.;self.mfe_sum=0.;self.mae_sum=0.
        self.reference_sum=0.;self.plan_sum=0.;self.gains=0.;self.losses=0.;self.symbols=set()

    def add(self,row,p,o,hours,now):
        n=self.counts;n['signals']+=1;self.symbols.add(row['symbol'])
        if now<row['delivered_ms']+hours*3600000:n['not_mature']+=1;return
        n['mature']+=1
        if row['state']=='UNRESOLVED_ENTRY':n['unresolved_entry']+=1;return
        if not o or row['outcome_state']=='DATA_PENDING':
            n['data_gap' if row['state'] in ('DATA_PENDING','DONE_DATA_GAP') or row['outcome_state']=='DATA_PENDING' else 'waiting_measurement']+=1
            return
        if row['outcome_state'] not in ('RESOLVED','UNRESOLVED_BOUNDARY'):
            n['other_unresolved']+=1;return
        value=o.get('directional_return_pct')
        if finite(value):
            n['direction_measured']+=1;self.direction_sum+=value
            n['aligned' if value>0 else 'opposed' if value<0 else 'flat']+=1
        if row['outcome_state']=='UNRESOLVED_BOUNDARY':n['boundary_ambiguous']+=1
        for key,attr in [('max_favorable_pct','mfe_sum'),('max_adverse_pct','mae_sum')]:
            if finite(o.get(key)):setattr(self,attr,getattr(self,attr)+o[key]);n[key+'_n']+=1
        for threshold in (10,20,30,50,100):
            if str(threshold) in o.get('milestones',{}):n['observed_reach_'+str(threshold)]+=1
            if str(threshold) in o.get('post_stop_milestones',{}):n['post_stop_reach_'+str(threshold)]+=1
        if row['outcome_state']=='RESOLVED' and finite(o.get('reference_net_return_pct')):
            n['reference_measured']+=1;self.reference_sum+=o['reference_net_return_pct']
        exit_=o.get('plan_exit')
        if row['outcome_state']=='RESOLVED' and exit_ and finite(exit_.get('net_return_pct')):
            value=exit_['net_return_pct'];n['plan_closed']+=1;self.plan_sum+=value
            n['plan_positive' if value>0 else 'plan_negative' if value<0 else 'plan_flat']+=1
            self.gains+=max(value,0);self.losses+=max(-value,0)
            n['exit_'+exit_['kind']]+=1

    def result(self):
        n=self.counts;measured=n['direction_measured'];closed=n['plan_closed']
        rate=n['aligned']/measured if measured else None
        if measured:
            z=1.96;den=1+z*z/measured
            mid=(rate+z*z/(2*measured))/den
            half=z*math.sqrt(rate*(1-rate)/measured+z*z/(4*measured*measured))/den
            ci=[100*(mid-half),100*(mid+half)]
        else:ci=None
        return dict(counts=dict(n),distinct_symbols=len(self.symbols),
            direction_correct_pct=100*rate if rate is not None else None,
            direction_wilson_95_pct=ci,mean_directional_return_pct=self.direction_sum/measured if measured else None,
            mean_mfe_pct=self.mfe_sum/n['max_favorable_pct_n'] if n['max_favorable_pct_n'] else None,
            mean_mae_pct=self.mae_sum/n['max_adverse_pct_n'] if n['max_adverse_pct_n'] else None,
            observed_reach_10_pct=100*n['observed_reach_10']/measured if measured else None,
            mean_reference_net_pct=self.reference_sum/n['reference_measured'] if n['reference_measured'] else None,
            closed_plan_positive_pct=100*n['plan_positive']/closed if closed else None,
            closed_plan_mean_net_pct=self.plan_sum/closed if closed else None,
            closed_plan_profit_factor=self.gains/self.losses if self.losses else None,
            evidence='DESCRIPTIVE_ONLY_SMALL_SAMPLE' if measured<30 else 'DESCRIPTIVE_ONLY_NOT_CAUSAL')


def groups(p):
    ctx=p.get('market_context') or {};g=ctx.get('groups') or {}
    missing='LEGACY_NO_CONTEXT' if not ctx else 'MISSING'
    yield 'overall','ALL'
    for axis in ('flow','oi_price','funding_cost','availability'):yield axis,g.get(axis,missing)
    yield 'flow_x_oi',g.get('flow',missing)+'|'+g.get('oi_price',missing)
    model=p.get('model','UNKNOWN')+'|'+p.get('direction','UNKNOWN')
    yield 'model_direction',model
    yield 'model_direction_flow',model+'|'+g.get('flow',missing)


def report(c,now_ms=None,since_ms=None,until_ms=None,horizons=EVALUATION_HOURS,paper=False):
    ideas='research_ideas' if paper else 'ideas'
    outcomes='research_outcomes' if paper else 'outcomes'
    reference='reference_ms' if paper else 'delivered_ms'
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    since_ms=now_ms-30*86400000 if since_ms is None else since_ms
    until_ms=now_ms+1 if until_ms is None else until_ms
    answer=dict(generated_ms=now_ms,signal_from_ms=since_ms,signal_until_ms=until_ms,
        selection_rules_changed=False,primary_horizon_hours=24,
        reference_kind='FORWARD_PAPER_QUOTE_NOT_DELIVERED' if paper else 'TELEGRAM_ACK',
        delivery_policy='VERSIONED_NOTIFICATION_ADMISSION' if not paper else 'NO_DELIVERY_LIMITS',
        notes=['Akış SUPPORT grubu alış/satış baskısı ve aynı saatlik fiyat hareketinin uyumudur; kâr tahmini değildir.',
               'OI artış/azalışı, funding maliyeti ve hesap oranı yön oyu sayılmaz.',
               'Yön doğruluğu, ham fiyat eşiğine erişim ve kapanan plan getirisi farklı ölçümlerdir.',
               'Reference net: plan daha önce kapandıysa o sonuç; açıksa vade sonunda kapatılsaydı oluşacak varsayımsal sonuç.',
               'Eksik/belirsiz veriler birincil kazanç-kayıp hesabına katılmaz; sayıları ayrıca gösterilir.',
               'Dakikalık mum kapanışıyla vade ölçümü; en fazla bir dakika zaman sapması vardır.',
               'Gruplar gözlemseldir; model/yön ve piyasa koşulları farkı nedensellik değildir. Wilson aralığı sinyal bağımsızlığı varsayar; kümelenme belirsizliği artırabilir.',
               'Negatif sonuçlar ve eski sürümün eksik bağlamı silinmez; geçmiş veri sonradan doldurulmaz.'],horizons=[])
    for h in horizons:
        accum={}
        rows=c.execute(f'''SELECT i.symbol,i.state,i.{reference} AS delivered_ms,i.payload,o.state AS outcome_state,o.payload AS outcome_payload
            FROM {ideas} i LEFT JOIN {outcomes} o ON o.idea_id=i.id AND o.horizon_hours=?
            WHERE i.{reference} IS NOT NULL AND i.{reference}>=? AND i.{reference}<? ORDER BY i.{reference}''',(h,since_ms,until_ms))
        for row in rows:
            p=json.loads(row['payload']);o=json.loads(row['outcome_payload']) if row['outcome_payload'] else None
            if h not in p.get('evaluation_hours',LEGACY_EVALUATION_HOURS):continue
            sid=p.get('strategy_id','UNKNOWN');oid=(p.get('market_context') or {}).get('observer_id','LEGACY_NO_CONTEXT')
            for axis,value in groups(p):
                key=(sid,oid,axis,value)
                if key not in accum:accum[key]=Stats()
                accum[key].add(row,p,o,h,now_ms)
        answer['horizons'].append(dict(hours=h,groups=[dict(strategy_id=k[0],observer_id=k[1],axis=k[2],group=k[3],
            label=LABELS.get(k[3],k[3]),**s.result()) for k,s in sorted(accum.items())]))
    return answer


def details(c,since_ms,until_ms):
    """One original signal and horizon per line; bounded streaming, no reclassification."""
    for r in c.execute('''SELECT i.id,i.symbol,i.delivered_ms,i.state,i.payload,
        o.horizon_hours,o.state AS outcome_state,o.payload AS outcome_payload
        FROM ideas i LEFT JOIN outcomes o ON o.idea_id=i.id
        WHERE i.delivered_ms>=? AND i.delivered_ms<? ORDER BY i.delivered_ms,o.horizon_hours''',(since_ms,until_ms)):
        p=json.loads(r['payload'])
        yield dict(idea_id=r['id'],symbol=r['symbol'],delivered_ms=r['delivered_ms'],
            direction=p['direction'],entry_price=p['price'],context=p.get('market_context'),
            idea_state=r['state'],horizon_hours=r['horizon_hours'],outcome_state=r['outcome_state'],
            outcome=json.loads(r['outcome_payload']) if r['outcome_payload'] else None)


def emit_report(c,now_ms,paper=False):
    result=report(c,now_ms,paper=paper)
    path=root()/('major_paired_comparison.json' if paper else 'major_context_comparison.json');temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');temp.replace(path)
    for horizon in result['horizons']:
        if horizon['hours'] not in (1,2,3,24,72):continue
        selected=[g for g in horizon['groups'] if g['axis'] in ('overall','flow','oi_price','model_direction')]
        print(('MAJOR_PAIRED_COMPARISON ' if paper else 'MAJOR_CONTEXT_COMPARISON ')+json.dumps(dict(reference_kind=result['reference_kind'],generated_ms=now_ms,horizon_hours=horizon['hours'],
              since_ms=result['signal_from_ms'],groups=selected)),flush=True)


def timestamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise argparse.ArgumentTypeError('UTC Z veya +03:00 gibi saat dilimi gerekli')
    return int(t.timestamp()*1000)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--since',type=timestamp);parser.add_argument('--until',type=timestamp)
    parser.add_argument('--hours',type=float,choices=EVALUATION_HOURS);parser.add_argument('--details',action='store_true');parser.add_argument('--research',action='store_true')
    args=parser.parse_args()
    if args.details and args.research:parser.error('--research currently supports grouped reports; raw rows remain in research_ideas/research_outcomes')
    c=db(root()/'major_move_v1.db',True);now=int(time.time()*1000)
    try:
        if args.details:
            for row in details(c,args.since if args.since is not None else now-30*86400000,args.until if args.until is not None else now+1):
                print(json.dumps(row,ensure_ascii=False))
        else:print(json.dumps(report(c,now,args.since,args.until,[args.hours] if args.hours else EVALUATION_HOURS,paper=args.research),ensure_ascii=False,indent=2))
    finally:c.close()


if __name__=='__main__':main()

