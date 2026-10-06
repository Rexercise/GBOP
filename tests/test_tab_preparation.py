"""Exercise the actual prepared-brief storage path using synthetic broker history."""
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch
from gbop_voice_web.market_watch import init_watches, watch_tool
from gbop_voice_web.market_watch_runtime import prepare_next_shift

T=int(datetime.fromisoformat('2026-10-02T10:00:00-04:00').timestamp())

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:'); self.conn.row_factory=sqlite3.Row
        self.db=lambda:self.conn
        init_watches(self.db)
        self.conn.execute('CREATE TABLE gbop_market_feed(asset TEXT)')
        self.conn.execute('INSERT INTO gbop_market_feed VALUES (?)',('NAS100',))
        self.conn.execute('CREATE TABLE gbop_market_history(asset TEXT,symbol TEXT,step INTEGER,day_utc INTEGER,payload TEXT)')
        bar={'time':T,'open':100,'high':103,'low':98,'close':102}
        self.conn.execute('INSERT INTO gbop_market_history VALUES (?,?,?,?,?)',('NAS100','USTECm',300,T//86400*86400,json.dumps([bar])))
        self.conn.commit()
        self.feed={'ok':True,'asset':'NAS100','symbol':'USTECm','bars':[dict(bar,time=T+7*3600)],'bars_m1':[]}
        self.result={'ok':True,'asset':'NAS100','available_through_ny':'2026-10-02T10:05:00-04:00',
            'review':{'shift_story':{'recap':{'classification':'synthetic test only'},'ranges':[]},
                      'paired_smt':{'status':'unavailable','reason':'SPX not connected'}}}
    def tearDown(self):
        self.conn.close()
    def test_preparation_uses_retained_shift_not_current_two_hour_snapshot(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as tool:
            r=prepare_next_shift(self.db,{},T+8*3600)
        self.assertEqual((r['asset'],r['date_ny'],r['shift']),('NAS100','2026-10-02','day'))
        self.assertEqual(tool.call_count,1)
        saved=watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600)
        self.assertTrue(saved['ok'])
        self.assertEqual(saved['review']['as_of_ny'],'2026-10-02T10:05:00-04:00')
        self.assertEqual(saved['review']['paired_smt']['status'],'unavailable')
    def test_no_fabricated_preparation_when_market_analysis_fails(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value={'ok':False}):
            self.assertIsNone(prepare_next_shift(self.db,{},T+8*3600))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_prepared_shifts').fetchone()[0],0)
    def test_unchanged_brief_not_recomputed_each_tick(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as tool:
            prepare_next_shift(self.db,cache,T+8*3600)
            prepare_next_shift(self.db,cache,T+8*3600+30)
        self.assertEqual(tool.call_count,1)
    def test_prior_version_is_not_served_and_is_rebuilt_with_same_candles(self):
        from gbop_voice_web.market_watch import VERSION
        old='tab-watch-corrected-chronology-2026-10-03'
        self.assertNotEqual(VERSION,old)
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as tool:
            with patch('gbop_voice_web.market_watch.VERSION',old),patch('gbop_voice_web.market_watch_runtime.VERSION',old):
                self.assertIsNotNone(prepare_next_shift(self.db,cache,T+8*3600))
            payload=self.conn.execute('SELECT payload FROM gbop_prepared_shifts').fetchone()[0]
            saved=watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600)
            self.assertFalse(saved['ok'])
            self.assertEqual(saved['status'],'not_prepared')
            # Filtering old derived state does not delete it or any member data.
            self.assertEqual(self.conn.execute('SELECT payload FROM gbop_prepared_shifts').fetchone()[0],payload)
            self.assertIsNotNone(prepare_next_shift(self.db,cache,T+8*3600))
            self.assertEqual(tool.call_count,2)
        self.assertEqual(self.conn.execute('SELECT version FROM gbop_prepared_shifts').fetchone()[0],VERSION)
        self.assertEqual(cache[('NAS100','2026-10-02','day')][0],VERSION)
        self.assertTrue(watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600)['ok'])
    def test_prepared_brief_preserves_post_invalidation_local_function(self):
        from test_super_soup_function import review, first, MODEL, INVALID, LOCAL_TARGET
        actual=review([MODEL,INVALID,LOCAL_TARGET],authoritative=True)
        self.result['review']['shift_story']['ranges']=[dict(actual,role='selected_range')]
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.result):
            prepare_next_shift(self.db,{},T+8*3600)
        saved=watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600)
        selected=saved['review']['selected_ranges'][0]
        self.assertEqual(first(selected)['super_soup_structure'],first(actual)['super_soup_structure'])
        self.assertEqual(selected['candle_lifecycle_summary'],actual['candle_lifecycle']['spoken_summary'])
    def test_native_evidence_change_refreshes_same_source_bars_and_bucket(self):
        cache={}
        self.result['review']['shift_synopsis']={'young_lefty_status':'context_dependent',
            'young_lefty_coverage':{'ohlc_basis':'native_broker_H1','source_coverage_complete':False}}
        higher={'time':T-7200,'open':100,'high':110,'low':90,'close':100,
            'provenance':{'source':'MT5','timeframe':'H1','method':'copy_rates_from_pos',
                          'asset':'NAS100','symbol':'USTECm','captured_at':T+8*3600}}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as tool, \
             patch('gbop_voice_web.market_data.history_native_h1',return_value=[]) as native:
            self.assertIsNotNone(prepare_next_shift(self.db,cache,T+8*3600))
            native.return_value=[higher]
            self.assertIsNotNone(prepare_next_shift(self.db,cache,T+8*3600+30))
            self.assertEqual(tool.call_count,2)
        saved=watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600+30)
        self.assertEqual(saved['review']['shift_synopsis'],self.result['review']['shift_synopsis'])
    def test_oversized_prepared_brief_keeps_function_summary_when_details_are_paged_out(self):
        from test_super_soup_function import review, MODEL, INVALID, LOCAL_TARGET
        actual=review([MODEL,INVALID,LOCAL_TARGET],authoritative=True)
        actual['candle_lifecycle']['extra_test_details']='x'*140000
        self.result['review']['shift_story']['ranges']=[dict(actual,role='selected_range')]
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.result):
            self.assertIsNotNone(prepare_next_shift(self.db,{},T+8*3600))
        saved=watch_tool(self.db,1,42,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T+8*3600)
        selected=saved['review']['selected_ranges'][0]
        self.assertNotIn('candle_lifecycle',selected)
        self.assertIn('after the closure of the 10:05 AM M5 candle invalidated the Model 1 CRT',selected['candle_lifecycle_summary'])
        self.assertIn('does not restore CRT validity',selected['candle_lifecycle_summary'])
        self.assertIn('detail_omitted',saved['review'])
    def test_collector_accepts_explicit_spx_among_nine_assets(self):
        from market_bridge.bridge import load_config, ASSETS
        config={'endpoint':'https://gbop.onrender.com/api/market/ingest','token':'fixture-only-token-0000000000000000',
                'symbols':{asset:asset+'m' for asset in ASSETS}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'; path.write_text(json.dumps(config))
            self.assertIn('SPX',load_config(path)['symbols'])
            self.assertEqual(len(load_config(path)['symbols']),9)

if __name__=='__main__': unittest.main()
