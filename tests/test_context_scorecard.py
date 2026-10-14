import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from long_short_context_scorecard import context_state, context_line, summarize, report
from long_short_v4_storage_reclaim import close_idle_database, file_hash

class ContextScorecardTests(unittest.TestCase):
    def test_symmetric_direction_and_unknown(self):
        c={'timeframes':{h:{'status':'OK','ema20_slope_pct':1} for h in ('1h','4h')}}
        self.assertEqual(context_state(c,'LONG'),'ALIGNED')
        self.assertEqual(context_state(c,'SHORT'),'COUNTER_TREND')
        self.assertEqual(context_state({},'LONG'),'UNKNOWN')
        self.assertIn('eksik',context_line({},'LONG'))
    def test_same_endpoint_baselines_and_costs(self):
        r=summarize([{'symbol':'A','direction':'SHORT','return_pct':1},
                     {'symbol':'B','direction':'LONG','return_pct':-1}])
        self.assertEqual(r['gross_mean_pct'],0)
        self.assertEqual(r['always_long_mean_pct'],-1)
        self.assertEqual(r['always_short_mean_pct'],1)
        self.assertEqual(r['cost_sensitivity']['0.2']['mean_endpoint_less_cost_pct'],-.2)
    def test_duplicate_updates_not_new_episodes_and_no_hindsight(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'long_short_breakout_observer.db'
            c=sqlite3.connect(p)
            c.executescript('''CREATE TABLE episodes(id,symbol,direction,config_hash,early_ms);
                CREATE TABLE transitions(id,episode_id,new_state,features_json,delivery_status);
                CREATE TABLE outcomes(episode_id,horizon_seconds,state,return_pct);
                INSERT INTO episodes VALUES(1,'A','LONG','v1',1);
                INSERT INTO transitions VALUES(1,1,'EARLY','{}','ACK');
                INSERT INTO transitions VALUES(2,1,'EARLY','{}','ACK');
                INSERT INTO outcomes VALUES(1,900,'RESOLVED',1);
                INSERT INTO outcomes VALUES(1,3600,'DATA_PENDING',NULL);''')
            c.commit();c.close()
            r=report(tmp)
            self.assertFalse(r['issues'])
            self.assertEqual(r['groups']['v1:DELIVERED:900:ALL']['n'],1)
            self.assertEqual(r['groups']['v1:DELIVERED:900:UNKNOWN']['n'],1)
            self.assertEqual(r['outcome_states']['3600:DATA_PENDING'],1)
    def test_idle_wal_is_checkpointed_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'db'
            c=sqlite3.connect(p);c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE data(x)');c.execute('INSERT INTO data VALUES(123)');c.commit();c.close()
            close_idle_database(p)
            self.assertFalse(Path(str(p)+'-wal').exists())
            c=sqlite3.connect(p)
            self.assertEqual(c.execute('SELECT x FROM data').fetchone()[0],123)
            c.close();self.assertEqual(len(file_hash(p)),64)
    def test_reader_blocks_wal_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'db'
            c=sqlite3.connect(p);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE data(x)');c.commit()
            c.execute('BEGIN');c.execute('SELECT * FROM data').fetchall()
            with self.assertRaises((sqlite3.OperationalError,RuntimeError)):
                close_idle_database(p)
            c.close()

class EpisodeDeliveryTests(unittest.TestCase):
    def test_new_episode_sends_but_update_edits(self):
        import os
        from unittest.mock import patch
        import long_short_message_card as card
        calls=[]
        def post(url,json,timeout):
            calls.append(url.rsplit('/',1)[-1])
            class R:
                def json(self):return {'ok':True,'result':{'message_id':len(calls),'chat':{'id':'7'}}}
            return R()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{
            'LS_MESSAGE_CARD_DB':str(Path(tmp)/'cards.db'),'LS_QUALIFIED_SIGNAL_CHAIN_V1':'0'}):
            card.publish('123:test','7','XUSDT','EARLY','first','LONG',post=post,episode_key='1')
            card.publish('123:test','7','XUSDT','EARLY','update','LONG',post=post,episode_key='1')
            card.publish('123:test','7','XUSDT','EARLY','next','LONG',post=post,episode_key='2')
        self.assertEqual(calls,['sendMessage','editMessageText','sendMessage'])

class RetentionTests(unittest.TestCase):
    def test_keep_verified_set_and_recent_partial_upload(self):
        from datetime import datetime,timezone,timedelta
        from long_short_backup_retention import expired_objects
        now=datetime.now(timezone.utc)
        old='daily/2026-10-14/010000/manifest.json'
        new='daily/2026-10-14/020000/manifest.json'
        partial='daily/2026-10-14/030000/db'
        objects=[{'Key':k,'LastModified':now} for k in (old,old.replace('manifest.json','db'),new,partial)]
        deleted=expired_objects(objects,[old,new],new,{new},now-timedelta(hours=24))
        self.assertEqual({x['Key'] for x in deleted},{old,old.replace('manifest.json','db')})
