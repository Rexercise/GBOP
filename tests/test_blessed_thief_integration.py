"""Tool, voice and prepared-summary retention using synthetic broker candles."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import stamp
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_watch import watch_tool
from gbop_voice_web.market_watch_runtime import prepare_next_shift
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_blessed_thief import fixture, review, T, STEP
import test_tab_preparation as preparation
PREP_T = preparation.T


class BlessedThiefIntegrationTests(unittest.TestCase):
    def tool(self, name='review_market_crt', **extra):
        bars=fixture(5)
        args=dict(asset='EURUSD',anchor_start_ny=stamp(T),anchor_timeframe='H1',through_ny=stamp(T+21600))
        args.update(extra)
        feed=dict(ok=True,asset='EURUSD',symbol='EURUSDtest',bars=bars,bars_m1=[])
        with patch.object(market,'read_feed',return_value=feed),patch.object(market,'history_bars',return_value=(bars,STEP)):
            return market.market_tool(None,name,args)

    def test_tool_default_and_explicit_tf_page_parameters(self):
        a=self.tool(blessed_thief_timeframe='M5')['review']['blessed_thief']
        b=self.tool(blessed_thief_timeframe='M5',blessed_thief_from_ny=a['next_candle_start_ny'])['review']['blessed_thief']
        self.assertEqual(a['manipulation'],b['manipulation'])
        self.assertEqual(b['candles'][0]['bar_open_ny'],a['next_candle_start_ny'])
        self.assertEqual(self.tool()['review']['blessed_thief']['timeframe'],'H1')

    def test_tool_schema_exposes_nullable_independent_timeframe_and_cursor(self):
        schema=next(t for t in market.MARKET_TOOLS if t['name']=='review_market_crt')['parameters']
        for key in ('blessed_thief_timeframe','blessed_thief_from_ny'):
            self.assertEqual(schema['properties'][key]['type'],['string','null'])
            self.assertIn(key,schema['required'])

    def test_bad_page_returns_error_without_losing_anchor_silently(self):
        self.assertFalse(self.tool(blessed_thief_from_ny=stamp(T+3900))['ok'])

    def test_voice_keeps_every_bounded_level_and_correct_cursor(self):
        result=self.tool(blessed_thief_timeframe='M5');before=deepcopy(result)
        compact=compact_voice_tool_result('review_market_crt',result)
        self.assertEqual(compact['review']['blessed_thief'],result['review']['blessed_thief'])
        self.assertEqual(result,before)
        self.assertNotIn('next_start_ny',compact['review']['blessed_thief'])

    def test_shift_and_compact_voice_preserve_sequences(self):
        # Move the fixture anchor from 9 to the selected 8 o'clock range.
        bars=[{**b,'time':b['time']-3600} for b in fixture()]
        r=market.session_review(bars,'2026-10-02','day',STEP)
        first=r['shift_story']['ranges'][0]['blessed_thief']
        self.assertEqual(first['total_candle_count'],3)
        self.assertEqual(first['candles'][1]['bar_open_ny'],stamp(T+3600))
        c=compact_voice_tool_result('review_market_session',{'review':r})
        self.assertEqual(c['review']['shift_story']['ranges'][0]['blessed_thief'],first)

    def test_night_candle_time_keeps_explicit_ny_offset(self):
        bars=[{**b,'time':b['time']+11*3600} for b in fixture()]
        r=market.session_review(bars,'2026-10-02','night',STEP)
        self.assertEqual(r['shift_story']['ranges'][0]['blessed_thief']['candles'][0]['bar_open_ny'],
                         '2026-10-02T21:00:00-04:00')

    def test_shared_canonical_routing_and_fifth_tier3_entry(self):
        for phrase in ('FIFTH CRT entry, Tier 3','blessed_thief.candles','blessed_thief_timeframe',
                       'blessed_thief_from_ny','never proves a member fill, stop or realized R'):
            self.assertIn(phrase,CANONICAL_KNOWLEDGE)


class BlessedThiefPreparedTests(unittest.TestCase):
    def setUp(self):
        self.h=preparation.PreparationTests();self.h.setUp()
    def tearDown(self):
        self.h.tearDown()
    def save(self, huge=False):
        sequence=review()
        if huge:
            sequence['synthetic_oversized_details']='x'*140000
        self.h.result['review']['shift_story']['ranges']=[dict(role='selected_range',
            anchor_start_ny=stamp(T),blessed_thief=sequence)]
        with patch('gbop_voice_web.market_data.read_feed',return_value=self.h.feed),patch('gbop_voice_web.market_data.market_tool',return_value=self.h.result):
            self.assertIsNotNone(prepare_next_shift(self.h.db,{},PREP_T+8*3600))
        return sequence,watch_tool(self.h.db,1,42,42,'get_prepared_market_brief',
                                  {'asset':'NAS','shift':'day'},PREP_T+8*3600)['review']
    def test_saved_brief_preserves_each_candle_and_summary(self):
        sequence,r=self.save()
        self.assertEqual(r['selected_ranges'][0]['blessed_thief'],sequence)
        self.assertEqual(r['selected_ranges'][0]['blessed_thief_summary'],sequence['summary'])
    def test_payload_limit_keeps_summary_and_explicit_detail_omission(self):
        sequence,r=self.save(True)
        self.assertNotIn('blessed_thief',r['selected_ranges'][0])
        self.assertEqual(r['selected_ranges'][0]['blessed_thief_summary'],sequence['summary'])
        self.assertIn('detail_omitted',r)

if __name__=='__main__':
    unittest.main()
