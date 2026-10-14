import ast
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import long_short_qualified_chain as q
import long_short_breakout_observer as observer
import long_short_message_card as card

ROOT=Path(__file__).resolve().parent
if ROOT.name=="tests":ROOT=ROOT.parent

def function(filename,name,namespace):
    tree=ast.parse((ROOT/filename).read_text())
    node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),filename,"exec"),namespace)
    return namespace[name]

class QualifiedChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=observer.initialize_state(str(self.root/"long_short_breakout_observer.db"))
        self.db=self.root/"analyst.db"
        with sqlite3.connect(self.db) as c:c.execute("CREATE TABLE analyses(symbol,scan_time_utc)")
        self.now=1791935169000
    def tearDown(self):
        self.state.close();self.tmp.cleanup()
    def episode(self,symbol,age=60000,fresh=1000,state="EARLY",ended=None):
        self.state.execute("""INSERT INTO episodes(symbol,direction,state,started_ms,early_ms,ended_ms,last_features_json)
            VALUES(?,'LONG',?,?,?,?,?)""",(symbol,state,self.now-age,self.now-age,ended,json.dumps({"price_event_ms":self.now-fresh})))
        self.state.commit()
    def analyzed(self,symbol,age):
        stamp=datetime.fromtimestamp((self.now-age)/1000,timezone.utc).isoformat()
        with sqlite3.connect(self.db) as c:c.execute("INSERT INTO analyses VALUES(?,?)",(symbol,stamp))
    def test_candidate_retained_no_transport_even_for_failure_retry(self):
        for status in ("PENDING","FAILED:timeout","ACK"):
            self.state.execute("INSERT INTO transitions(delivery_status) VALUES(?)",(status,))
        self.state.commit()
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"1"}):
            self.assertEqual(observer.deliver_pending(self.state,"","",self.now,publisher=lambda *a,**k:self.fail("sent")),2)
        self.assertEqual([r[0] for r in self.state.execute("SELECT delivery_status FROM transitions")],["CANDIDATE_ONLY_V1","CANDIDATE_ONLY_V1","ACK"])
        self.assertEqual(q.retain_candidates(self.state),0)
    def test_publisher_blocks_early_before_database_or_network(self):
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"1"}),patch.object(card,"connect",side_effect=AssertionError("db")):
            r=card.publish("","", "XUSDT","EARLY","text","LONG",post=lambda *a,**k:self.fail("network"))
        self.assertEqual(r["status"],"CANDIDATE_ONLY_V1")
    def test_recent_analysis_waits_unseen_goes_first(self):
        self.episode("NEW");self.episode("DONE");self.analyzed("DONE",1000)
        self.assertEqual(q.read_pending(self.root,self.db,self.now),["NEW"])
    def test_old_active_candidate_revisited_after_five_minutes(self):
        self.episode("OLD",age=1200000);self.analyzed("OLD",400000)
        self.assertEqual(q.read_pending(self.root,self.db,self.now),["OLD"])
    def test_stale_future_terminal_excluded(self):
        self.episode("STALE",fresh=31000);self.episode("FUTURE",fresh=-1)
        self.episode("BROKEN",state="BROKEN",ended=self.now)
        self.assertEqual(q.read_pending(self.root,self.db,self.now),[])
    def test_priority_dedup_cap_and_not_in_universe(self):
        rows=[{"symbol":str(i)} for i in range(10)]
        selected=q.reserve(rows,rows,["9","9","MISSING","8"],3,lambda x:x["symbol"])
        self.assertEqual([x["symbol"] for x in selected],["9","8","0"])
        self.assertEqual(q.reserve(rows,rows,["9"],0,lambda x:x["symbol"]),[])
    def test_six_slots_max_and_normal_capacity_preserved(self):
        rows=list(range(30))
        out=q.reserve(rows,rows,list(range(29,19,-1)),24,lambda x:x)
        self.assertEqual(out[:6],[29,28,27,26,25,24]);self.assertEqual(len(out),24)
        self.assertEqual(len(set(out)),24)
    def test_deep_handoff_cannot_promote_low_liquidity(self):
        ns={"PRESELECT_MAX":24,"MIN_24H_QUOTE_VOL":25,"os":os,"json":json,"DB":str(self.db)}
        f=function("long_short_analyst.py","select_deep_shortlist",ns)
        def row(sym,vol,rank):return {"symbol":sym,"quote_volume":vol,"rank":rank,"discovery_meta":{"binance_spot_member":True}}
        rows=[row("HIGH",30,100),row("EARLY",30,1),row("LOW",5,200)]
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"1"}),patch.object(q,"priority",return_value=["LOW","EARLY"]):
            out=f(rows,1)
        self.assertEqual([r["symbol"] for r in out],["EARLY"])
    def test_watch_close_retest_are_silent_trigger_still_formats(self):
        ns={"TELEGRAM_TRADE_ONLY":True,"fmtp":str,"json":json,
            "classify_move":lambda *a:"STABLE","_telegram_data_brief":lambda *a:""}
        f=function("long_short_live_pool.py","message_for",ns)
        row=dict(symbol="XUSDT",direction="LONG",trigger_level=100,retest_low=99,retest_high=100,invalidation=98,target1=104,target2=0,data_mode="BINANCE_FUTURES")
        for stage in ("WATCH","APPROACHING","CLOSE_CONFIRMED","RETESTING","INVALIDATED"):
            self.assertIsNone(f(row,stage,100,100))
        self.assertIn("LONG SİNYALİ",f(row,"TRIGGERED",100,100))
    def test_partial_data_transport_branch_respects_chain(self):
        tree=ast.parse((ROOT/"long_short_live_pool.py").read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and "PARTIAL_3_OF_4" in ast.unparse(n.test))
        expr=compile(ast.Expression(node.test),"<partial-policy>","eval")
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"1"}):
            self.assertFalse(eval(expr,{"four_factor_check":{"tier":"PARTIAL_3_OF_4"},"qualified_chain_enabled":q.enabled}))
    def test_core_card_removes_old_early_section(self):
        import long_short_display
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"0","LS_MESSAGE_CARD_DB":str(self.root/"cards.db")}):
            def post(url,json,timeout):
                class R:
                    def json(self):return {"ok":True,"result":{"message_id":42,"chat":{"id":"7"}}}
                return R()
            card.publish("123:test","7","XUSDT","EARLY","🟢 LONG İÇİN İZLE | XUSDT","LONG",post=post)
            with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"1"}):
                card.publish("123:test","7","XUSDT","CORE","🟢 LONG SİNYALİ | XUSDT","LONG",post=post)
            with sqlite3.connect(self.root/"cards.db") as c:
                sections=json.loads(c.execute("SELECT sections_json FROM cards").fetchone()[0])
            self.assertEqual(set(sections),{"CORE"})
    def test_flag_off_preserves_default(self):
        with patch.dict(os.environ,{"LS_QUALIFIED_SIGNAL_CHAIN_V1":"0"}):self.assertFalse(q.enabled())

if __name__=="__main__":unittest.main()

