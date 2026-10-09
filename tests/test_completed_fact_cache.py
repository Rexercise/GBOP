"""Completed fact reuse and bounded read presentation; synthetic public data only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from gbop_voice_web import market_watch as watch
from gbop_voice_web.market_watch_runtime import prepare_next_shift, _completed_input_token
from gbop_voice_web.voice_payload import voice_tool_payload, _encoded_size
import test_tab_preparation as preparation
from test_tab_preparation import T


class CompletedFactCacheTests(unittest.TestCase):
    setUp = preparation.PreparationTests.setUp
    tearDown = preparation.PreparationTests.tearDown

    def run_preparation(self, cache=None, now=T+8*3600):
        return prepare_next_shift(self.db, {} if cache is None else cache, now)

    def saved(self, now=T+8*3600):
        return watch.watch_tool(self.db, 1, 42, 42, 'get_prepared_market_brief',
                                {'asset': 'NAS', 'shift': 'day'}, now)

    def test_completed_reuse_survives_time_bucket_and_process_restart(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.assertIsNotNone(self.run_preparation(cache))
            initial=self.saved()
            for offset in (30, 301, 601):
                self.assertIsNone(self.run_preparation(cache,T+8*3600+offset))
            self.assertIsNone(self.run_preparation({},T+9*3600))
            self.assertEqual(analysis.call_count,1)
        later=self.saved(T+9*3600)
        self.assertEqual(later['review'],initial['review'])
        self.assertEqual(later['prepared_at_epoch'],initial['prepared_at_epoch'])
        self.assertEqual(later['age_seconds'],3600)
        self.assertNotIn('analysis_cache',later['review'])

    def test_source_correction_and_canon_change_rebuild(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            rows=json.loads(self.conn.execute('SELECT payload FROM gbop_market_history').fetchone()[0])
            rows[0]['close']+=.25
            self.conn.execute('UPDATE gbop_market_history SET payload=?',(json.dumps(rows),))
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+30))
            with patch('gbop_voice_web.gtop_protocol.CANONICAL_KNOWLEDGE','changed synthetic canon'):
                self.assertFalse(self.saved()['ok'])
                self.assertIsNotNone(self.run_preparation(cache,T+8*3600+60))
                self.assertTrue(self.saved()['ok'])
            self.assertEqual(analysis.call_count,3)

    def test_postshift_new_candles_do_not_change_frozen_shift_facts(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            self.feed['bars'].append(dict(time=T+3*3600,open=100,high=111,low=90,close=105))
            self.assertIsNone(self.run_preparation(cache,T+8*3600+600))
            self.assertEqual(analysis.call_count,1)
        # This cache is only in the preparation runtime; post-shift detail tools
        # continue to read their own new data. Existing post-shift suites verify it.
        self.assertEqual(self.saved()['review']['as_of_ny'],self.result['available_through_ny'])

    def test_active_shift_is_never_prepared(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.assertIsNone(self.run_preparation(now=T+1800))
            analysis.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_prepared_shifts').fetchone()[0],0)

    def test_deleted_or_old_version_row_rebuilds_despite_warm_fingerprint(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            self.conn.execute('DELETE FROM gbop_prepared_shifts')
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+30))
            self.conn.execute("UPDATE gbop_prepared_shifts SET version='obsolete'")
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+60))
            self.assertEqual(analysis.call_count,3)

    def test_malformed_persisted_cache_is_not_trusted(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result):
            for bad in ('not JSON','null','[]','{"analysis_cache":null}',
                        '{"analysis_cache":{},"availability":[]}'):
                self.run_preparation()
                self.conn.execute('UPDATE gbop_prepared_shifts SET payload=?',(bad,))
                self.assertFalse(self.saved()['ok'],bad)
                self.assertIsNotNone(self.run_preparation({},T+8*3600+30),bad)

    def test_warm_corrupt_payload_and_cold_missing_synopsis_rebuild(self):
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            self.conn.execute("UPDATE gbop_prepared_shifts SET payload='null'")
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+30))
            row=json.loads(self.conn.execute('SELECT payload FROM gbop_prepared_shifts').fetchone()[0])
            row.pop('shift_synopsis')
            self.conn.execute('UPDATE gbop_prepared_shifts SET payload=?',(json.dumps(row),))
            self.assertFalse(self.saved()['ok'])
            self.assertIsNotNone(self.run_preparation({},T+8*3600+60))
            self.assertEqual(analysis.call_count,3)

    def test_private_result_fields_never_enter_global_prepared_payload(self):
        self.result['member_context']={'user_id':99,'journal':'private sentinel'}
        self.result['review']['member_journal']='private sentinel'
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result):
            self.run_preparation()
        payload=self.conn.execute('SELECT payload FROM gbop_prepared_shifts').fetchone()[0]
        self.assertNotIn('private sentinel',payload)
        self.assertNotIn('user_id',payload)
        with patch('gbop_voice_web.market_watch.member_access_error',return_value='Access denied'):
            self.assertFalse(self.saved()['ok'])

    def test_bounded_paired_inputs_and_missing_dependency_changes(self):
        feed={'asset':'NAS100','symbol':'NASm','ok':True}
        peer={'asset':'SPX','symbol':'SPXm','ok':True}
        sets={60:[{'time':100,'close':1}],300:[]}
        args=(self.db,feed,'NAS100','2026-06-11','day',[],60,[],0,18000,20000)
        with patch('gbop_voice_web.market_data.read_feed',return_value=peer) as reader, \
             patch('gbop_voice_web.market_data._history_sets',return_value=sets) as history:
            first=_completed_input_token(*args)
            self.assertEqual(history.call_count,2)
            self.assertTrue(all(c.args[-2:]==(3600,18000) for c in history.call_args_list))
            sets[300].append({'time':120,'close':2})
            self.assertNotEqual(_completed_input_token(*args),first)
            reader.return_value={'ok':False}
            missing=_completed_input_token(*args)
            self.assertNotEqual(missing,first)
            reader.side_effect=RuntimeError('temporarily unreadable')
            self.assertIsNone(_completed_input_token(*args))

    def test_native_capture_only_reuses_but_ohlc_and_provenance_changes_rebuild(self):
        cache={}
        bar={'time':T-7200,'open':100,'high':110,'low':90,'close':100,
             'provenance':{'captured_at':T+8*3600,'source':'MT5','timeframe':'H1'}}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.history_native_h1',return_value=[bar]), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            bar['provenance']['captured_at']+=600
            self.assertIsNone(self.run_preparation(cache,T+8*3600+600))
            bar['high']+=1
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+630))
            bar['provenance']['source']='changed source'
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+660))
            self.assertEqual(analysis.call_count,3)

    def test_input_change_during_analysis_does_not_persist_wrong_revision(self):
        def corrected(*args,**kwargs):
            self.conn.execute('UPDATE gbop_market_history SET payload=?',
                (json.dumps([dict(time=T,open=100,high=103,low=98,close=101)]),))
            return self.result
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',side_effect=corrected):
            self.assertIsNone(self.run_preparation())
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_prepared_shifts').fetchone()[0],0)

    def test_cache_hit_rotates_one_eligible_context_per_tick(self):
        from gbop_voice_web.market_data import ASSETS
        self.conn.executemany('INSERT INTO gbop_market_feed VALUES (?)',
                              [(asset,) for asset in ASSETS if asset!='NAS100'])
        cache={}
        def available(bars,day,shift,*args):
            return {'reviewable':True,'temporal_status':'completed','date_ny':day,'shift':shift}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.latest_available_shift_date',return_value='2026-10-02'), \
             patch('gbop_voice_web.market_data.history_bars',return_value=([],60)), \
             patch('gbop_voice_web.market_data.history_native_h1',return_value=[]), \
             patch('gbop_voice_web.shift_availability.assess_shift',side_effect=available), \
             patch('gbop_voice_web.market_watch_runtime._completed_input_token',return_value=('test',)), \
             patch('gbop_voice_web.market_watch_runtime._prepared_matches',return_value=True) as match, \
             patch('gbop_voice_web.market_data.market_tool') as analysis:
            for i in range(18):
                self.assertIsNone(self.run_preparation(cache,T+8*3600+30*i))
                self.assertEqual(match.call_count,i+1)
            self.assertEqual(len({c.args[1] for c in match.call_args_list}),18)
            analysis.assert_not_called()

    def test_postgres_conditional_payload_check_repairs_in_place_changes(self):
        from contextlib import contextmanager
        import hashlib
        self.conn.create_function('md5',1,lambda text:hashlib.md5(text.encode()).hexdigest())
        connection=self.conn; transferred=[]
        class Adapter:
            _conn=connection
            def execute(_,sql,params=()):
                cursor=connection.execute(sql,params)
                if 'AS payload_fingerprint' not in sql:
                    return cursor
                class Cursor:
                    def fetchone(_):
                        row=cursor.fetchone()
                        transferred.append(len(row['payload'] or '') if row else 0)
                        return row
                return Cursor()
        @contextmanager
        def db():
            with connection:
                yield Adapter()
        self.db=db
        cache={}
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.feed), \
             patch('gbop_voice_web.market_data.market_tool',return_value=self.result) as analysis:
            self.run_preparation(cache)
            self.assertIsNone(self.run_preparation(cache,T+8*3600+301))
            self.assertEqual(transferred[-1],0)
            self.assertIsNone(self.run_preparation({},T+8*3600+601))
            self.assertGreater(transferred[-1],0)
            self.conn.execute("UPDATE gbop_prepared_shifts SET payload='null'")
            self.assertIsNotNone(self.run_preparation(cache,T+8*3600+901))
            self.assertEqual(analysis.call_count,2)

    def test_incomplete_prepared_synopsis_fails_closed_instead_of_crashing(self):
        for synopsis in ({},{'range_index':None},
                         {'spoken_summary':'a'*20000,'ranges':[],'range_index':[{}]},
                         {'spoken_summary':'a'*20000,'ranges':[],
                          'range_index':[{'label':'9ate8','anchor_start_ny':'x','detail_request':[]}]}):
            value={'ok':True,'asset':'NAS100','review':{'shift_synopsis':synopsis}}
            wire=voice_tool_payload('get_prepared_market_brief',value)
            self.assertFalse(wire['ok'])
            self.assertLessEqual(_encoded_size(wire),12000)


class PreparedPresentationTests(unittest.TestCase):
    def test_all_assets_day_night_cached_synopsis_lossless_and_bounded(self):
        from gbop_voice_web.market_conversation import MarketConversation
        from gbop_voice_web.market_data import ASSETS, market_tool
        from gbop_voice_web.market_watch import init_watches
        from test_current_market import CurrentMarketTests
        from test_young_lefty_delivery import START, sequence
        from test_chronological_transport import expand
        for asset in ASSETS:
            for hours,shift in ((0,'day'),(12,'night')):
                with self.subTest(asset=asset,shift=shift):
                    fixture=CurrentMarketTests(); fixture.setUp()
                    try:
                        init_watches(fixture.db)
                        start=START+hours*3600; now=start+18000
                        fixture.seed(now,sequence(start),asset=asset)
                        self.assertIsNotNone(prepare_next_shift(fixture.db,{},now))
                        args={'asset':asset,'date_ny':'2026-06-11','shift':shift}
                        def run(name,args):
                            if name=='get_prepared_market_brief':
                                return watch.watch_tool(fixture.db,1,42,42,name,args,now+600)
                            return market_tool(fixture.db,name,args,now=now+600)
                        context=MarketConversation(); context.begin_turn()
                        raw=context.run('get_prepared_market_brief',args,run)
                        self.assertTrue(raw['ok'],raw)
                        before=deepcopy(raw)
                        with patch('gbop_voice_web.shift_synopsis.build_shift_synopsis',
                                   side_effect=AssertionError('must reuse analyzed synopsis')):
                            wire=voice_tool_payload('get_prepared_market_brief',raw)
                        self.assertTrue(wire['ok'],wire)
                        self.assertLessEqual(_encoded_size(wire),12000)
                        self.assertEqual(raw,before)
                        actual=expand(wire)['review']['shift_synopsis']
                        for key in ('ranges','chronological_context','active_range_context','shift_end'):
                            self.assertEqual(actual[key],raw['review']['shift_synopsis'][key],key)
                        appendix=raw['review'].get('post_shift_outcomes')
                        self.assertEqual(actual['spoken_summary'],raw['review']['shift_synopsis']['spoken_summary']
                            + (' '+appendix['summary'] if appendix else ''))
                        if appendix:
                            expected_appendix=deepcopy(appendix)
                            expected_appendix.pop('summary',None)
                            for row in expected_appendix.get('ranges',[]): row.pop('summary',None)
                            self.assertEqual(actual['post_shift_outcomes'],expected_appendix)
                        self.assertEqual(wire['age_seconds'],600)
                        self.assertEqual(wire['prepared_at_epoch'],now)
                        self.assertEqual(wire['review']['as_of_ny'],raw['review']['as_of_ny'])
                        self.assertEqual(wire['market_context']['selection'],raw['market_context']['selection'])
                    finally:
                        fixture.tearDown()


if __name__=='__main__': unittest.main()
