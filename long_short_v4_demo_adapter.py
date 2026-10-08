#!/usr/bin/env python3
"""Testnet-only Binance USD-M order adapter, with NO production exchange URL.

Does NOT place real-money orders. A testnet order is transmitted only when
LS_V4_TESTNET_SEND=YES, testnet credentials exist, and a separate risk decision
has approved the immutable plan. This file contains no trading strategy.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from typing import Any
import requests

from long_short_v4_execution_gate import Decision, quantity_round_down

DEMO_BASE="https://demo-fapi.binance.com"
ALLOWED_PATHS=frozenset((
    "/fapi/v1/time","/fapi/v1/ping","/fapi/v1/exchangeInfo",
    "/fapi/v1/order","/fapi/v1/order/test","/fapi/v1/algoOrder",
    "/fapi/v1/openAlgoOrders","/fapi/v1/openOrders",
    "/fapi/v1/positionSide/dual","/fapi/v2/positionRisk",
))
SAFE_READ=frozenset(("GET",))
ALLOW_WRITE=frozenset(("POST",))
LIMITED_METHODS={"GET","POST"}
UNRESOLVED=("NEW","PARTIALLY_FILLED","EXPIRED_IN_MATCH")
TERMINAL=("FILLED","CANCELED","EXPIRED","REJECTED")

class DemoRejected(RuntimeError):pass
class DemoOrderUnknown(RuntimeError):pass

class FuturesDemoClient:
    def __init__(self,api_key=None,secret=None,session=None,base=DEMO_BASE,clock=None):
        if base != DEMO_BASE:
            raise ValueError("Only the hard-pinned demo Binance host is accepted")
        self.base=DEMO_BASE
        self.api_key=api_key
        self.secret=secret
        self.session=session or requests.Session()
        self.clock=clock or (lambda:int(time.time()*1000))
    def signed_request(self,method,path,params=None,*,send=False):
        if method not in LIMITED_METHODS or path not in ALLOWED_PATHS:
            raise ValueError("Host, path or method not on fixed allowlist")
        if method=="POST":
            if not send or os.getenv("LS_V4_TESTNET_SEND")!="YES":
                raise DemoRejected("Demo writing disabled: explicit testnet send switch required")
            if path not in ("/fapi/v1/order","/fapi/v1/order/test","/fapi/v1/algoOrder"):
                raise DemoRejected("Writes to this path not allowed")
        if not self.api_key or not self.secret:
            raise DemoRejected("Missing DEMO-specific credentials")
        data={k:str(v) for k,v in (params or {}).items() if v is not None}
        data["timestamp"]=str(self.clock())
        data["recvWindow"]="5000"
        payload=urlencode(data)
        signature=hmac.new(self.secret.encode(),payload.encode(),hashlib.sha256).hexdigest()
        target=self.base+path
        headers={"X-MBX-APIKEY":self.api_key}
        try:
            if method=="GET":
                resp=self.session.get(target,params=payload+"&signature="+signature,
                    headers=headers,timeout=7)
            else:
                resp=self.session.post(target,data=payload+"&signature="+signature,
                    headers=headers,timeout=7)
            resp.raise_for_status()
            out=resp.json()
            if not isinstance(out,dict) and path not in ("/fapi/v1/openAlgoOrders","/fapi/v1/openOrders","/fapi/v2/positionRisk"):
                raise DemoOrderUnknown("Unexpected Binance demo response")
            return out
        except (requests.RequestException,ValueError,TypeError) as exc:
            # Do not blindly retry an order; it might have reached exchange.
            raise DemoOrderUnknown("Transport or response state UNKNOWN: "+type(exc).__name__) from exc

def demo_intents(plan:dict[str,Any],decision:Decision) -> dict:
    """Return entry/stop/TP requests; NEVER route to production."""
    if not decision.approved or not decision.planned_qty or float(decision.planned_qty)<=0:
        raise DemoRejected("Risk gate has not authorized this plan")
    direction=plan["direction"]
    symbol=plan["symbol"]
    side="BUY" if direction=="LONG" else "SELL"
    exit_side="SELL" if direction=="LONG" else "BUY"
    key=decision.intent_id
    # MARKET is a normal order. STOP_MARKET and TAKE_PROFIT_MARKET have moved
    # to algoOrder, where the key is *triggerPrice*, not stopPrice.
    entry={
        "symbol":symbol,"side":side,"positionSide":"BOTH","type":"MARKET",
        "quantity":decision.planned_qty,
        "newOrderRespType":"RESULT","newClientOrderId":key+"e",
    }
    stop={
        "algoType":"CONDITIONAL","symbol":symbol,"side":exit_side,
        "positionSide":"BOTH","type":"STOP_MARKET",
        "triggerPrice":str(plan["stop"]),"closePosition":"true",
        "workingType":"MARK_PRICE","clientAlgoId":key+"s",
    }
    tp={
        "algoType":"CONDITIONAL","symbol":symbol,"side":exit_side,
        "positionSide":"BOTH","type":"TAKE_PROFIT_MARKET",
        "triggerPrice":str(plan["tp1"]),"closePosition":"true",
        "workingType":"MARK_PRICE","clientAlgoId":key+"t",
    }
    # TP2 is research metadata; no two simultaneous close-all TP orders.
    return {"entry_path":"/fapi/v1/order","entry":entry,
            "stop_path":"/fapi/v1/algoOrder","stop":stop,
            "tp_path":"/fapi/v1/algoOrder","tp":tp,
            "testnet_only":True,"tp2_observational":str(plan["tp2"])}

def execute_demo_with_rollback(client:FuturesDemoClient,decision:Decision,plan,*,send=False):
    """Submit only to DEMO. Verify exact fill, protective stop; fail closed.

    If order ACK is ambiguous, refuse further orders and require reconciliation.
    If protective stop fails after a known fill, attempt reduceOnly MARKET exit
    on DEMO and mark status unknown if an error occurs; NEVER retry entry.
    A live user-data-stream monitor is still required for full production parity.
    """
    req=demo_intents(plan,decision)
    if not send:return {"status":"DRY_RUN","requests":req,"orders_placed":0}
    if os.getenv("LS_V4_TESTNET_SEND")!="YES":
        raise DemoRejected("Testnet switch is off")
    # This adapter does NOT override existing positions, margin mode, or leverage.
    # Pre-execution account reconciliation is an independent mandatory gate.
    entry=client.signed_request("POST",req["entry_path"],req["entry"],send=True)
    if not isinstance(entry,dict) or entry.get("status")!="FILLED":
        raise DemoOrderUnknown("Entry status not positively FILLED, reconcile by clientOrderId")
    amount=str(entry.get("executedQty") or "")
    try:
        if float(amount)<=0 or float(amount)>float(decision.planned_qty)+1e-12:
            raise DemoOrderUnknown("Unexpected executed quantity")
    except (ValueError,TypeError) as exc:
        raise DemoOrderUnknown("Invalid fill quantity") from exc
    exit_side=req["stop"]["side"]
    try:
        stop=client.signed_request("POST",req["stop_path"],req["stop"],send=True)
        if not isinstance(stop,dict) or not stop.get("algoId"):
            raise DemoOrderUnknown("STOP response does not confirm an algoId")
    except (DemoRejected,DemoOrderUnknown) as stop_exc:
        # Emergency DEMO close, no retry of unknown entry/stop:
        emergency={"symbol":plan["symbol"],"side":exit_side,"type":"MARKET",
                   "positionSide":"BOTH","reduceOnly":"true",
                   "quantity":amount,"newClientOrderId":decision.intent_id+"x"}
        try:
            close=client.signed_request("POST","/fapi/v1/order",emergency,send=True)
            return {"status":"EMERGENCY_DEMO_CLOSE","fill":entry,
                    "close":close,"stop_error":type(stop_exc).__name__}
        except (DemoRejected,DemoOrderUnknown) as close_exc:
            raise DemoOrderUnknown("UNPROTECTED DEMO POSITION POSSIBLE; manual intervention required") from close_exc
    # For a production-class bracket, TP and stop need OCO/reconciliation.
    # This is a DEMO experiment only; if TP fails the already placed stop remains.
    try:
        tp=client.signed_request("POST",req["tp_path"],req["tp"],send=True)
        tp_ok=isinstance(tp,dict) and bool(tp.get("algoId"))
    except (DemoRejected,DemoOrderUnknown):
        tp_ok=False
        tp=None
    return {"status":"DEMO_PROTECTED_WITH_TP" if tp_ok else "DEMO_STOP_ONLY",
            "fill":entry,"stop":stop,"tp":tp,
            "warning":"DEMO bracket is not atomic; always reconcile both algo orders and position."}
