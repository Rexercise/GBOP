"""Answer named hourly CRT questions with retained range-specific evidence."""
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import MARKET_PROMPT, LIVE_MARKET_PROMPT
from gbop_voice_web.market_context import LIFECYCLE_PROMPT
from gbop_voice_web.market_watch_runtime import extract_events
from gbop_voice_web.shift_review import review_shift
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_shift_review import fixture


class NamedHourlyRecapTests(unittest.TestCase):
    def review(self, data=None):
        return review_shift(data or fixture('2026-10-01', True, 60), '2026-10-01', 'night', 60)

    def test_all_hours_named_in_order_without_promoting_independent_context(self):
        result = self.review()
        recap = result['recap']
        self.assertEqual([r['range_label'] for r in recap['range_summaries']],
                         [f'the {hour}:00 PM H1 range' for hour in (8,9,10,11)])
        rows = recap['range_summaries']
        self.assertEqual([r['role'] for r in rows], ['selected_range','selected_range',
                                                   'independent_range_context','independent_range_context'])
        self.assertIn('independent hourly context, not a selected range', rows[2]['text'])
        self.assertIn('no later shift candles', rows[3]['text'])
        text = recap['hourly_crt_summary']
        self.assertLess(text.index('The 8:00 PM'), text.index('The 9:00 PM H1 range'))
        self.assertNotIn('another', text)
        self.assertIn('2026-10-01', recap['shift_start_ny'])
        self.assertIn('2026-10-02T00:00', recap['shift_end_ny'])

    def test_failed_8_retains_purge_and_named_invalidation_before_9_delivery(self):
        result = self.review()
        recap = result['recap']
        eight, nine = recap['range_summaries'][:2]
        self.assertIn('sell-side purge of the 8:00 PM H1 range', eight['text'])
        self.assertIn('invalidated by the closure of the 9:00 PM H1 candle', eight['text'])
        self.assertIn('50% (midpoint) of the 9:00 PM H1 range', nine['text'])
        self.assertIn('buy-side of the 9:00 PM H1 range', nine['text'])
        self.assertIn('V1 Textbook', nine['text'])
        self.assertIn('sell-side purge of the 8:00 PM H1 range', recap['spoken_summary'])
        self.assertNotIn('not proof of an entry or profit', recap['spoken_summary'])
        self.assertFalse(result['ranges'][0]['entry_confirmed'])

    def test_assigned_source_return_and_h1_close_are_distinct(self):
        nine = self.review()['recap']['range_summaries'][1]['text']
        self.assertLess(nine.index('10:00 PM M5'), nine.index('10:00 PM M1'))
        self.assertIn('returned inside the 9:00 PM H1 range', nine)
        self.assertIn('The 10:00 PM H1 candle closed back inside the 9:00 PM H1 range', nine)

    def test_midpoint_only_is_not_full_delivery_or_a_completed_variant(self):
        data = fixture('2026-10-01', True, 60)
        later = parse_time('2026-10-01T23:00:00-04:00')
        for bar in data:
            if bar['time'] >= later:
                bar['high'] = 99
        result = self.review(data)
        row = result['recap']['range_summaries'][1]['text']
        self.assertIn('reached 50% (midpoint)', row)
        self.assertIn('did not reach buy-side of the 9:00 PM H1 range before the shift cutoff', row)
        self.assertNotIn('V1', row)
        self.assertNotIn('failed', row)

    def test_gap_is_scoped_to_affected_range_without_erasing_known_8_facts(self):
        data = fixture('2026-10-01', True, 60)
        data = [b for b in data if b['time'] != parse_time('2026-10-01T23:30:00-04:00')]
        result = self.review(data)
        eight = result['recap']['range_summaries'][0]['text']
        self.assertIn('sell-side purge of the 8:00 PM H1 range', eight)
        self.assertIn('invalidated by the closure of the 9:00 PM H1 candle', eight)
        self.assertNotIn('incomplete', eight)
        eleven = result['recap']['range_summaries'][3]['text']
        self.assertIn('11:00 PM H1 candle is incomplete', eleven)
        self.assertNotIn('failed', eleven)

    def test_gap_inside_range_window_still_blocks_a_complete_outcome(self):
        data = fixture('2026-10-01', True, 60)
        data = [b for b in data if b['time'] != parse_time('2026-10-01T21:30:00-04:00')]
        result = self.review(data)
        self.assertFalse(result['ranges'][0]['observation_coverage']['complete'])
        self.assertIsNone(result['ranges'][0]['invalidated_at_ny'])
        self.assertIn('unverified', result['recap']['range_summaries'][0]['text'])

    def test_voice_keeps_named_ranges_and_original_night_date(self):
        result = self.review()
        voice = compact_voice_tool_result('review_market_session', {'ok':True,'review':{'shift_story':result}})
        recap = voice['review']['shift_recap']
        self.assertEqual(recap['range_summaries'], result['recap']['range_summaries'])
        self.assertEqual(recap['shift_start_ny'], result['start_ny'])

    def test_prompt_contracts_answer_first_recheck_and_qualify_smt(self):
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            self.assertIn('shift_synopsis.spoken_summary', prompt)
            self.assertIn('SHORT', prompt)
            self.assertIn('recheck', prompt)
            self.assertIn('disputed', prompt)
            self.assertIn('setup_interval.qualified_smt', prompt)
            self.assertIn('both bones', prompt)
        self.assertIn('variant established/pending', MARKET_PROMPT)
        self.assertIn('Forming/missing is unverified', MARKET_PROMPT)
        self.assertIn('minute asynchrony', LIFECYCLE_PROMPT)

    def test_same_hour_or_forming_smt_is_not_alerted(self):
        event = dict(anchors_valid_at_event=True,bar_close_ny='2026-10-01T21:22:00-04:00',
                     side='sell_side',swept_asset='NAS100',nonconfirming_asset='SPX',direction='bullish',
                     setup_interval={'qualified_smt':False})
        result = dict(ok=True,asset='NAS100',review={'paired_smt':{'events':[event]}})
        self.assertEqual(extract_events(result), [])
        event['setup_interval']['qualified_smt'] = True
        self.assertEqual(len(extract_events(result)), 1)


if __name__ == '__main__':
    unittest.main()
