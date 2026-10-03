"""Backend integration and voice-retention tests with synthetic, closed candles."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES

START = parse_time('2026-10-02T07:00:00-04:00')

def bars():
    out = [dict(time=START+i*300,open=100,high=110,low=90,close=100) for i in range(60)]
    for i, values in enumerate(((108,115,107,112),(112,116,110,111),(111,112,106,107),
                              (107,109,103,104),(104,105,99,100),(100,101,89,91),(91,114,90,113))):
        out[24+i].update(zip(('open','high','low','close'),values))
    return out

class LifecycleIntegrationTests(unittest.TestCase):
    def run_tool(self, asset='NAS100', name='review_market_session', extra=None):
        source=bars()
        def feed(db, value, now=None):
            asset=market.asset_name(value)
            if asset=='SPX': return {'ok':False,'asset':asset,'status':'not_connected'}
            return {'ok':True,'asset':asset,'symbol':asset+'test','bid':100,'ask':101,'bars':source,'bars_m1':[]}
        args={'asset':asset,'date_ny':'2026-10-02','shift':'day'}
        args.update(extra or {})
        with patch.object(market,'read_feed',side_effect=feed), patch.object(market,'history_bars',return_value=(source,300)):
            return market.market_tool(None,name,args)
    def test_session_exposes_candle_lifecycle_even_with_false_execution_flag(self):
        r=self.run_tool();self.assertTrue(r['ok'])
        row=r['review']['shift_story']['ranges'][0]
        self.assertFalse(row['entry_confirmed'])
        facts=row['candle_lifecycle']['purge_candles']
        body=next(f for f in facts if f['identity']=='Model 1 candle')
        self.assertEqual(body['csd']['status'],'confirmed')
    def test_missing_spx_is_disclosed_without_substituting_us30(self):
        r=self.run_tool()['review']
        self.assertEqual(r['paired_smt']['missing_asset'],'SPX')
        self.assertEqual(r['paired_context']['comparison_asset'],'SPX')
        self.assertEqual(r['paired_context']['status'],'insufficient_paired_evidence')
    def test_crypto_pairing_is_automatic(self):
        r=self.run_tool('BTCUSD')['review']
        self.assertEqual(r['paired_context']['comparison_asset'],'ETHUSD')
        self.assertTrue(r['paired_smt']['ok'])
    def test_metals_pairing_is_automatic(self):
        r=self.run_tool('XAUUSD')['review']
        self.assertEqual(r['paired_context']['comparison_asset'],'XAGUSD')
        self.assertTrue(r['paired_context']['ranges'])
    def test_unconfigured_asset_has_no_invented_pair(self):
        r=self.run_tool('EURUSD')['review']
        self.assertEqual(r['paired_context']['status'],'no_configured_comparison_pair')
    def test_generic_crt_exposes_lifecycle(self):
        r=self.run_tool(name='review_market_crt',extra={'anchor_start_ny':'2026-10-02T08:00:00-04:00',
            'anchor_timeframe':'H1','through_ny':'2026-10-02T12:00:00-04:00'})
        self.assertTrue(r['ok']);self.assertIn('candle_lifecycle',r['review'])
        self.assertEqual(r['review']['candle_lifecycle']['assigned_timeframe'],'M5')
    def test_voice_keeps_lifecycle_and_paired_context(self):
        raw=self.run_tool('XAUUSD');result=compact_voice_tool_result('review_market_session',raw)
        self.assertIn('paired_context',result['review'])
        self.assertIn('candle_timeline',result['review']['shift_recap'])
        row=result['review']['shift_story']['ranges'][0]
        self.assertTrue(row['candle_lifecycle']['purge_candles'])
        self.assertIn('recap',raw['review']['shift_story'])
    def test_spx_alias_is_data_support_not_assertion_of_live_feed(self):
        self.assertEqual(market.asset_name('US500'),'SPX')
        self.assertEqual(market.asset_name('S&P 500'),'SPX')
    def test_shared_knowledge_contains_lifecycle_contract(self):
        self.assertIn('gtop_lifecycle.txt',KNOWLEDGE_FILES)
        self.assertIn('initial visible purge; assigned-timeframe wick/body classification',CANONICAL_KNOWLEDGE)
        self.assertIn('candle_lifecycle',market.MARKET_PROMPT)
        self.assertIn('candle_lifecycle',market.LIVE_MARKET_PROMPT)
    def test_quotes_absent_from_structural_review(self):
        r=self.run_tool();self.assertNotIn('bid',r);self.assertNotIn('ask',r)
    def test_night_shift_has_lifecycle_and_correct_date(self):
        source=[{**b,'time':b['time']+12*3600} for b in bars()]
        r=market.session_review(source,'2026-10-02','night',300)
        first=r['shift_story']['ranges'][0]['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(first['bar_open_ny'],'2026-10-02T21:00:00-04:00')
    def test_cutoff_excludes_later_confirmation(self):
        r=self.run_tool(name='review_market_crt',extra={'anchor_start_ny':'2026-10-02T08:00:00-04:00',
            'anchor_timeframe':'H1','through_ny':'2026-10-02T09:10:00-04:00'})
        f=next(f for f in r['review']['candle_lifecycle']['purge_candles'] if f['identity']=='Model 1 candle')
        self.assertNotEqual(f['csd']['status'],'confirmed')

if __name__=='__main__': unittest.main()
