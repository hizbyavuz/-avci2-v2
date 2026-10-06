import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import long_short_simple_notify as sn
import long_short_outcome_tracker_v21 as ot


class WatchAlertTrackingTests(unittest.TestCase):
    def test_queue_claim_ack_preserves_payload(self):
        old=sn.DB
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                sn.DB=tmp.name
                payload={
                    "stage":"WATCH_ALERT","price":100.0,"trigger_level":101.0,
                    "invalidation":98.0,"target1":103.0,"target2":105.0,
                    "data_cohort":"BINANCE_FUTURES_NATIVE","early_state":"PENDING",
                }
                ok=sn.queue_alert("TESTUSDT","LONG",101.0,"test",priority=3,payload=payload)
                self.assertTrue(ok)
                item=sn.claim_ready_alert()
                self.assertIsNotNone(item)
                self.assertEqual(item["payload"]["stage"],"WATCH_ALERT")
                sn.ack_claimed_alert(item)
                with sqlite3.connect(tmp.name) as con:
                    row=con.execute("SELECT payload_json FROM sent_alerts WHERE symbol='TESTUSDT'").fetchone()
                    self.assertIsNotNone(row)
                    saved=json.loads(row[0])
                    self.assertEqual(saved["target1"],103.0)
        finally:
            sn.DB=old

    def test_watch_alert_outcome_is_separate_and_cost_adjusted(self):
        old_db,old_notify,old_rows=ot.DB,ot.NOTIFY_DB,ot._rows_1m
        old_sn=sn.DB
        try:
            with tempfile.TemporaryDirectory() as td:
                live=str(Path(td)/"live.db")
                notify=str(Path(td)/"notify.db")
                ot.DB=live; ot.NOTIFY_DB=notify; sn.DB=notify
                sn.init_db()
                payload={
                    "stage":"WATCH_ALERT","early_state":"PENDING",
                    "trigger_level":100.5,"invalidation":99.0,
                    "target1":101.5,"target2":103.0,
                    "data_cohort":"BINANCE_FUTURES_NATIVE",
                }
                sn.mark_sent("TESTUSDT","LONG",100.5,payload)
                old_epoch=time.time()-5*3600
                with sqlite3.connect(notify) as con:
                    con.execute("UPDATE sent_alerts SET sent_at_epoch=? WHERE symbol='TESTUSDT'",(old_epoch,))
                    con.commit()

                sent=datetime.fromtimestamp(old_epoch,tz=timezone.utc)
                execute=sent+timedelta(seconds=ot.HUMAN_DELAY_SECONDS)
                rows=[]
                for i in range(250):
                    ts=int((execute+timedelta(minutes=i)).timestamp()*1000)
                    if i==0:
                        o,h,l,c=100.0,100.4,99.8,100.2
                    elif i==1:
                        o,h,l,c=100.2,101.7,100.1,101.5
                    else:
                        o,h,l,c=101.5,102.2,101.1,102.0
                    rows.append([ts,str(o),str(h),str(l),str(c),"0",ts+59999])
                ot._rows_1m=lambda symbol,start,end: rows

                with sqlite3.connect(live) as con:
                    ot.init_db(con)
                    added=ot.evaluate_watch_alerts(con)
                    con.commit()
                    self.assertEqual(added,3)
                    vals=con.execute("""SELECT horizon_min,net_return_pct,first_barrier,direction_correct
                                        FROM delivered_watch_outcomes ORDER BY horizon_min""").fetchall()
                    self.assertEqual([r[0] for r in vals],[15,60,240])
                    self.assertTrue(all(r[1]>0 for r in vals))
                    self.assertTrue(all(r[2]=="TP1" for r in vals))
                    self.assertTrue(all(r[3]==1 for r in vals))
        finally:
            ot.DB,ot.NOTIFY_DB,ot._rows_1m=old_db,old_notify,old_rows
            sn.DB=old_sn


if __name__=="__main__":
    unittest.main()
