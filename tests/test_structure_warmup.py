import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from structure_engine.runtime import main

class WarmupTest(unittest.TestCase):
    def test_stale_catalogue_records_waiting_without_crashing_or_scanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source.db'
            sqlite3.connect(source).close()
            output = io.StringIO()
            with patch('sys.argv',['runtime','--source-db',str(source),'--state-dir',str(Path(tmp)/'state'),'--once']), patch('long_short_swing_runtime.universe',side_effect=ValueError('ASSET_CLASS_METADATA_STALE')), patch('structure_engine.runtime.scan') as scan, redirect_stdout(output):
                main()
            scan.assert_not_called()
            self.assertIn('WAITING_FOR_UNIVERSE',output.getvalue())
            db=sqlite3.connect(Path(tmp)/'state/structure_context_v1.db')
            self.assertEqual(db.execute('SELECT count(*) FROM health').fetchone()[0],1)
            db.close()
