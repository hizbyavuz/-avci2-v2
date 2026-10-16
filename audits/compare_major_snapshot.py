"""Read-only paired research comparison on identical closed Binance candles.

Independent screen specified BEFORE inspecting the resulting ranked cases:
  4h and 12h direction, 4h BTC relative strength, position vs 24h VWAP,
  rolling 1h quote volume vs median 24 previous hourly blocks.
  A candidate needs all these in its direction. Entry WATCH requires proximity
  within 1 ATR(15m) of prior 4h breakout level and a local invalidation <=8%.
This is an unvalidated research screen, not a live trade or a fitted strategy.
Production is replayed unchanged with 15m close as a hypothetical reference.
No historical WebSocket delivery or contemporary universe membership is implied.
"""
import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import statistics
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import long_short_major_move as motor

ROOT=pathlib.Path(os.environ.get('MAJOR_AUDIT_DATA_DIR', str(pathlib.Path(__file__).resolve().parent/'major_move_20261016'/'data')))


def load(symbol,cutoff,directory=None):
    data={};raw={}
    for tf,n in [('15m',120),('1h',120),('4h',220)]:
        x=json.loads(((directory or ROOT/'market')/(symbol+'_'+tf+'.json')).read_text())
        rows=[b for b in x['bars'] if int(b[6])<cutoff]
        raw[tf]=rows[-n:]
        data[tf]=[[int(b[0]),*[float(b[i]) for i in (1,2,3,4,5)],int(b[6])] for b in rows[-n:]]
    return data,raw


def clauses(f,h):
    hh,ll=f['confirmed_hour_highs'],f['confirmed_hour_lows']
    return {
      'WAKE_UP':{
        'compression_le_0.8':f['compression_ratio']<=.8,
        'hour_volume_ge_1.5':f['volume_1h_multiple']>=1.5,
        'hour_close_above_box':f['price']>f['box_high'],
        'rs4h_ge_2pp':f['relative_strength_4h_pp']>=2,
        'day_gain_lt_10':f['return_24h_pct']<10},
      'MAJOR_CONTINUATION':{
        'prior_leg_ge_10':f['leg_gain_pct']>=10,
        'retained_ge_half':f['retention']>=.5,
        'higher_hour_low':len(ll)>=2 and ll[-1][1]>ll[-2][1],
        'rs4h_ge_2pp':f['relative_strength_4h_pp']>=2,
        'last_hour_up':f['price']>h[-2][4],
        'peak_old_enough':f['peak_ms']<h[-2][0],
        'hour_close_below_peak':f['price']<f['leg_peak']},
      'TOP_BREAKDOWN':{
        'prior_leg_ge_10':f['leg_gain_pct']>=10,
        'lower_hour_high':len(hh)>=2 and hh[-1][1]<hh[-2][1],
        'hour_close_below_low':bool(ll) and f['price']<ll[-1][1],
        'rs4h_le_minus2pp':f['relative_strength_4h_pp']<=-2}}


def independent(data,raw,btc):
    m,h=data['15m'],data['1h'];q=raw['15m'];bm=btc['15m'];price=m[-1][4]
    ret=lambda a,n:(a[-1][4]/a[-1-n][4]-1)*100
    r4,r12=ret(m,16),ret(h,12);rs4=r4-ret(bm,16)
    vwap=sum(float(b[7]) for b in q[-96:])/sum(float(b[5]) for b in q[-96:])
    baseline=[sum(float(b[7]) for b in q[i:i+4]) for i in range(len(q)-100,len(q)-4,4)]
    volume=sum(float(b[7]) for b in q[-4:])/statistics.median(baseline)
    side='LONG' if r4>0 and r12>0 and rs4>0 and price>vwap else 'SHORT' if r4<0 and r12<0 and rs4<0 and price<vwap else None
    out=dict(price=price,return_4h_pct=r4,return_12h_pct=r12,rs4h_pp=rs4,
             rolling_1h_volume_multiple=volume,vwap24h=vwap,side=side,status='NO_ALIGNED_TREND')
    if not side:return out
    sign=1 if side=='LONG' else -1;a=motor.atr(m)
    level=max(b[2] for b in m[-17:-1]) if sign==1 else min(b[3] for b in m[-17:-1])
    piv=motor.pivots(m[-48:],high=sign==-1)
    structural=piv[-1][1] if piv else min(b[3] for b in m[-9:-1]) if sign==1 else max(b[2] for b in m[-9:-1])
    stop=structural-sign*.15*a;risk=sign*(price-stop)/price*100;distance=sign*(price-level)/a
    obstacles=[v for _,v in motor.pivots(data['4h'],high=sign==1) if sign*(v-price)>0]
    obstacle=(min(obstacles) if sign==1 else max(obstacles)) if obstacles else None
    status='VOLUME_NOT_ELEVATED' if volume<1 else 'INVALID_LOCAL_STOP' if not 0<risk<=8 else 'LATE' if distance>1 else 'AWAY_FROM_TRIGGER' if distance<-1 else 'BREAKOUT_RESEARCH' if distance>0 else 'WATCH'
    out.update(status=status,trigger=level,stop=stop,risk_pct=risk,trigger_distance_atr=distance,atr15=a,
               obstacle_4h=obstacle,structural_room_pct=sign*(obstacle/price-1)*100 if obstacle else None,
               buy_share_1h=sum(float(b[10]) for b in q[-4:])/sum(float(b[7]) for b in q[-4:]))
    return out


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cutoff',default='2026-10-16T17:30:00+00:00');args=parser.parse_args()
    cutoff=int(dt.datetime.fromisoformat(args.cutoff).timestamp()*1000)
    btc,_=load('BTCUSDT',cutoff);answer=[];errors=[]
    for t in json.loads((ROOT/'universe.json').read_text()):
        s=t['symbol']
        try:
            data,raw=load(s,cutoff);price=data['15m'][-1][4]
            result=motor.assess(s,data,price,float(t['quoteVolume']),cutoff+500,btc)
            f=result['features']
            answer.append(dict(symbol=s,quote_volume=float(t['quoteVolume']),cutoff_ms=cutoff,
              reconstructed_motor=result,clauses=clauses(f,data['1h']) if f else {},
              independent=independent(data,raw,btc) if len(data['1h'])>=72 and len(data['15m'])>=100 else {},
              daily_ticker_gain_pct=float(t['priceChangePercent'])))
        except Exception as exc:errors.append(dict(symbol=s,error=str(exc)))
    output=dict(cutoff=args.cutoff,production_strategy_id=motor.strategy_id(),
                caveat='RECONSTRUCTED_CLOSED_PRICE_NOT_EXECUTABLE_SIGNAL; later download; current liquidity cohort',
                rows=answer,errors=errors)
    (ROOT/'comparison.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps({'complete':len(answer),'pending_or_errors':len(errors),
        'motor_reasons':dict(collections.Counter(r['reconstructed_motor']['reason'] for r in answer)),
        'independent_status':dict(collections.Counter(r['independent'].get('status','NO_DATA') for r in answer))},ensure_ascii=False))
    for r in sorted(answer,key=lambda r:abs(r['independent'].get('rs4h_pp',0)),reverse=True):
        i=r['independent']
        if i.get('side') and i.get('rolling_1h_volume_multiple',0)>=1:
            print(json.dumps({'symbol':r['symbol'],'motor':r['reconstructed_motor']['reason'],
              'model':r['reconstructed_motor'].get('model'),'independent':i,
              'failed':{k:[c for c,v in flags.items() if not v] for k,flags in r['clauses'].items()}},ensure_ascii=False))


if __name__=='__main__':main()
