"""Executable-price geometry for a human-reviewed signal; never sends orders.

Order book is used for spread, depth and entry cost, not a directional vote.
Risk policy values are supplied explicitly and hashed into the result. No win
probability or approval is inferred from a high reward/risk ratio.
"""
import hashlib
import json

from long_short_swing import finite

VERSION='TRADE_PLAN_GEOMETRY_V1_2026_10_22'


def plan_at_book(candidate, book, now_ms, *, notional_usdt, round_trip_fee_bps,
                 exit_slippage_reserve_bps, max_spread_bps, min_net_rr,
                 paid_carry_scenario_pct):
    policy=dict(notional_usdt=notional_usdt,round_trip_fee_bps=round_trip_fee_bps,
                exit_slippage_reserve_bps=exit_slippage_reserve_bps,
                max_spread_bps=max_spread_bps,min_net_rr=min_net_rr,
                paid_carry_scenario_pct=paid_carry_scenario_pct)
    out=dict(version=VERSION,status='REJECTED',reason=None,actionable=False,
             direction=candidate.get('direction'),win_probability=None,policy=policy)
    try:
        if not all(finite(x) and x>=0 for x in policy.values()):raise ValueError('INVALID_OR_MISSING_COST_POLICY')
        if notional_usdt<=0 or min_net_rr<=0 or max_spread_bps<=0:raise ValueError('INVALID_SIZE_OR_POLICY')
        out['policy_hash']=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
        if candidate.get('direction') not in ('LONG','SHORT'):raise ValueError('INVALID_DIRECTION')
        if candidate.get('stage')!='READY':raise ValueError('CANDIDATE_NOT_READY')
        if book.get('source')!='BINANCE_UM_FUTURES_REST' or book.get('symbol')!=candidate['symbol']:
            raise ValueError('BOOK_PROVENANCE_MISMATCH')
        if not all(isinstance(book.get(k),int) and not isinstance(book[k],bool) for k in ('event_ms','received_ms')):
            raise ValueError('BOOK_TIMESTAMP_MISSING')
        if not 0<=now_ms-book['event_ms']<=5000 or not book['event_ms']<=book['received_ms']<=now_ms:
            raise ValueError('BOOK_STALE_OR_FUTURE')
        if now_ms>=candidate['entry_valid_until_ms']:raise ValueError('ENTRY_EXPIRED')
        bids=book.get('bids',[]);asks=book.get('asks',[])
        for levels,sign in ((bids,-1),(asks,1)):
            if not levels:raise ValueError('EMPTY_BOOK')
            for i,level in enumerate(levels):
                if len(level)!=2 or not all(finite(x) and x>0 for x in level):raise ValueError('INVALID_BOOK_LEVEL')
                if i and sign*(level[0]-levels[i-1][0])<=0:raise ValueError('BOOK_NOT_SORTED_OR_DUPLICATE')
        bid,ask=bids[0][0],asks[0][0]
        if bid>=ask:raise ValueError('CROSSED_BOOK')
        mid=(bid+ask)/2;spread=(ask-bid)/mid*10000
        if spread>max_spread_bps:raise ValueError('SPREAD_EXCEEDS_POLICY')
        sign=1 if candidate['direction']=='LONG' else -1
        levels=asks if sign==1 else bids;remaining=notional_usdt;qty=0.
        for price,size in levels:
            take=min(remaining,price*size);qty+=take/price;remaining-=take
            if remaining<=1e-8:break
        if remaining>1e-8:raise ValueError('INSUFFICIENT_VISIBLE_DEPTH')
        entry=notional_usdt/qty
        stop,trigger,atr=(candidate.get(k) for k in ('stop','trigger','atr_1h'))
        if not all(finite(x) and x>0 for x in (stop,trigger,atr)):raise ValueError('INVALID_PLAN_LEVELS')
        risk=sign*(entry-stop)
        if risk<=0 or risk/entry>.12:raise ValueError('INVALID_RISK')
        if sign*(entry-trigger)<0:raise ValueError('BREAK_NOT_HELD')
        if sign*(entry-trigger)>atr:raise ValueError('LATE_CHASE')
        target=entry+sign*2*risk
        if target<=0:raise ValueError('INVALID_TARGET')
        obstacle=candidate.get('obstacle')
        if obstacle is not None:
            if not finite(obstacle) or sign*(obstacle-entry)<2*risk:raise ValueError('INSUFFICIENT_KNOWN_ROOM')
        # Entry book impact is ALREADY in VWAP; do not subtract it twice.
        costs=(round_trip_fee_bps+exit_slippage_reserve_bps)/100+paid_carry_scenario_pct
        risk_pct=100*risk/entry;gross_reward_pct=2*risk_pct
        net_rr=(gross_reward_pct-costs)/(risk_pct+costs)
        out.update(entry_vwap=entry,base_quantity=qty,stop=stop,target=target,
                   risk_pct=risk_pct,spread_bps=spread,
                   entry_impact_from_mid_bps=sign*(entry/mid-1)*10000,
                   assumed_cost_pct=costs,net_reward_risk_scenario=net_rr,
                   maximum_loss_guaranteed=False,obstacle_known=obstacle is not None)
        if net_rr<min_net_rr:raise ValueError('NET_REWARD_RISK_BELOW_POLICY')
        out.update(status='GEOMETRY_READY',reason='STRATEGY_VALIDATION_STILL_REQUIRED')
    except (ValueError,KeyError,TypeError) as exc:out.update(reason=str(exc))
    return out
