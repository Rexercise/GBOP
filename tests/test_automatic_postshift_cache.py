"""Fresh-input, bounded market-only appendix reuse; all candles are synthetic."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from gbop_voice_web import automatic_postshift as auto, market_data as market
import test_automatic_postshift as fixtures
import test_market_payload_cache as cache_fixtures


class AutomaticPostShiftCacheTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AutomaticPostShiftTests(); self.f.setUp()
        self.addCleanup(self.f.tearDown)
        with auto._cache_lock: auto._cache.clear()
        self.f.seed(self.f.cutoff,rows=[])
        from gbop_voice_web.market_watch_runtime import prepare_next_shift
        prepare_next_shift(self.f.db,{},self.f.cutoff)
        self.f.seed(self.f.cutoff+180)

    def test_cache_hit_cannot_hide_later_correction_deletion_or_read_failure(self):
        f=self.f; now=f.cutoff+180
        self.assertEqual(f.relevant(f.saved(now))['status'],'full_objective_delivered_after_cutoff')
        self.assertTrue(auto._cache)
        f.seed(now,rows=f.later(deliver=False))
        self.assertNotEqual(f.relevant(f.saved(now))['status'],'full_objective_delivered_after_cutoff')
        f.seed(now,rows=[])
        self.assertEqual(f.saved(now)['review']['post_shift_outcomes']['status'],'later_evidence_unavailable')
        f.seed(now)
        self.assertEqual(f.relevant(f.saved(now))['status'],'full_objective_delivered_after_cutoff')
        with patch.object(auto,'_source_sets',side_effect=RuntimeError('synthetic read failure')):
            failed=f.saved(now)['review']['post_shift_outcomes']
        self.assertEqual(failed['status'],'later_evidence_unverified')
        self.assertNotIn('ranges',failed)
        f.base[10]['close']+=.25; f.seed(now)
        self.assertEqual(f.saved(now)['review']['post_shift_outcomes']['status'],'frozen_source_changed')

    def test_native_recapture_reuses_facts_but_changed_ohlc_misses(self):
        f=self.f; now=f.cutoff+3600
        f.seed(now)
        native=[dict(time=f.cutoff,open=110,high=131,low=97,close=110,
                     provenance={'captured_at':now,'asset':f.asset,'symbol':f.asset+'m'})]
        with patch.object(market,'history_native_h1',return_value=native), \
                patch.object(market,'session_review',wraps=market.session_review) as analyze:
            first=f.saved(now)
            native[0]['provenance']['captured_at']+=60
            second=f.saved(now+60)
            self.assertEqual(analyze.call_count,1)
            self.assertEqual(first['review']['post_shift_outcomes']['ranges'],second['review']['post_shift_outcomes']['ranges'])
            native[0]['high']=140
            f.saved(now+60)
            self.assertEqual(analyze.call_count,2)

    def test_entry_and_byte_limits_evict_and_outputs_do_not_alias(self):
        with patch.object(auto,'MAX_CACHED_APPENDICES',2),patch.object(auto,'MAX_CACHED_BYTES',1000):
            value={'summary':'safe'}
            for key in ('a','b','c'): auto._remember(key,value)
            self.assertEqual(list(auto._cache),['b','c'])
            value['summary']='changed'
            self.assertEqual(auto._cache['c'][0]['summary'],'safe')
        with auto._cache_lock: auto._cache.clear()
        with patch.object(auto,'MAX_CACHED_BYTES',100):
            auto._remember('a',{'summary':'x'*60}); auto._remember('b',{'summary':'y'*60})
            self.assertEqual(list(auto._cache),['b'])
            auto._remember('huge',{'summary':'z'*200})
            self.assertNotIn('huge',auto._cache)
            self.assertLessEqual(sum(v[1] for v in auto._cache.values()),100)

    def test_existing_conditional_history_reader_reuses_bytes_and_rechecks_rows(self):
        f=cache_fixtures.MarketPayloadCacheTests(); f.setUp(); self.addCleanup(f.tearDown)
        bars=[dict(time=f.now-300,open=100,high=102,low=98,close=101)]
        payload=json.dumps(bars); f.insert(payload)
        feed={'asset':f.identity[0],'symbol':f.identity[1]}
        first=auto._source_sets(f.cached_db,feed,f.now-600,f.now)
        second=auto._source_sets(f.cached_db,feed,f.now-600,f.now)
        self.assertEqual(first,second)
        self.assertEqual(f.transferred,[len(payload),0])
        bars[0]['close']=102; corrected=json.dumps(bars)
        f.conn.execute('UPDATE gbop_market_history SET payload=?',(corrected,))
        self.assertEqual(auto._source_sets(f.cached_db,feed,f.now-600,f.now)[300],bars)
        self.assertEqual(f.transferred[-1],len(corrected))
        f.conn.execute('DELETE FROM gbop_market_history')
        self.assertEqual(auto._source_sets(f.cached_db,feed,f.now-600,f.now)[300],[])
