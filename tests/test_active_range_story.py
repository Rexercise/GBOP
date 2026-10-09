"""Retained OHLC continuity, cutoff and transport contracts; no production calls."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.active_range_story import selected_range_story
from gbop_voice_web.market_data import session_review
from gbop_voice_web.shift_narrative import classify_structure
from gbop_voice_web.shift_synopsis import build_other_ranges, _local_fact
from gbop_voice_web.voice_payload import voice_tool_payload
from test_other_market_ranges import BARS, DAY, ny, parse_time, provider


class ActiveRangeStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = provider('review_market_session', DAY)['review']

    def test_exact_nine_development_ends_with_eleven_under_review_at_noon(self):
        result = build_other_ranges(self.review, 'NAS100', [ny('07:00'), ny('08:00')])
        context = result['active_range_context']
        self.assertEqual(context['anchor_start_ny'], ny('09:00'))
        self.assertEqual(context['selected_at_ny'], ny('10:00'))
        self.assertEqual(context['selected_through_ny'], ny('12:00'))
        self.assertFalse(context['still_selected_at_cutoff'])
        self.assertEqual(context['next_selected_range']['to_anchor_ny'], ny('11:00'))
        self.assertEqual(context['next_selected_range']['confirmed_at_ny'], ny('12:00'))
        self.assertEqual(context['next_selected_range']['reason'], 'opposing_objective_completed')
        self.assertFalse(context['next_selected_range']['crt_established_by_handoff'])
        ten, eleven = context['hourly_development']
        self.assertEqual(ten['candle_start_ny'], ny('10:00'))
        self.assertEqual(ten['candle_body_direction'], 'bearish')
        self.assertEqual(ten['candle_science'], 'wick_above')
        self.assertEqual(ten['purge']['bar_open_ny'], ny('10:02'))
        self.assertEqual(ten['purge']['bar_close_ny'], ny('10:03'))
        self.assertEqual(ten['return_inside']['bar_open_ny'], ny('10:07'))
        self.assertEqual(ten['return_inside']['bar_close_ny'], ny('10:08'))
        self.assertEqual(eleven['midpoint']['bar_open_ny'], ny('11:03'))
        self.assertEqual(eleven['opposing_liquidity']['bar_open_ny'], ny('11:12'))
        self.assertEqual(context['conclusion']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(context['conclusion']['known_at_ny'], ny('11:13'))
        self.assertEqual(context['variant_known_at_ny'], ny('12:00'))
        self.assertEqual([v['code'] for v in context['variant']['labels']], ['V1'])

    def test_independent_ten_thesis_is_not_the_ten_candle_body_or_next_selection(self):
        result = build_other_ranges(self.review, 'NAS100', [ny('07:00'), ny('08:00')])
        ten = next(r for r in result['ranges'] if r['anchor_start_ny'] == ny('10:00'))
        self.assertEqual((ten['role'], ten['direction'], ten['outcome']),
                         ('independent_range_context', 'bullish', 'failed_before_objectives'))
        self.assertEqual(ten['invalidated_at_ny'], ny('12:00'))
        self.assertEqual(result['active_range_context']['hourly_development'][0]['candle_body_direction'], 'bearish')
        text = result['spoken_summary']
        self.assertLess(text.index('11:12 AM M1'), text.index('Separately, treating this candle as an independent range'))
        self.assertNotIn('started bullish', text)

    def test_discussed_anchor_keeps_completion_bridge_without_new_opportunity(self):
        result = build_other_ranges(self.review, 'NAS100', [ny('07:00'), ny('08:00'), ny('09:00')])
        self.assertEqual([r['anchor_start_ny'] for r in result['ranges']], [ny('10:00'), ny('11:00')])
        self.assertEqual(result['active_range_context']['anchor_start_ny'], ny('09:00'))
        self.assertIn('11:03 AM M1', result['spoken_summary'])
        self.assertIn('11:12 AM M1', result['spoken_summary'])
        self.assertNotIn('9ate8', result['spoken_summary'])
        self.assertNotIn('Young Lefty', result['spoken_summary'])

    def test_explicit_continuation_is_only_active_story_and_actual_later_transitions(self):
        result = build_other_ranges(self.review, 'NAS100', [ny('09:00')], continue_active=True)
        self.assertEqual(result['mode'], 'continue_active_range')
        self.assertEqual(result['ranges'], [])
        self.assertEqual(result['next_selected_context'], [])
        self.assertNotIn('independent range', result['spoken_summary'])
        older = build_other_ranges(self.review, 'NAS100', continue_active=True, anchor_start_ny=ny('08:00'))
        self.assertEqual(older['active_range_context']['next_selected_range']['to_anchor_ny'], ny('09:00'))
        self.assertEqual([r['anchor_start_ny'] for r in older['next_selected_context']], [ny('09:00'), ny('11:00')])
        with self.assertRaisesRegex(ValueError, 'independent context'):
            build_other_ranges(self.review, 'NAS100', continue_active=True, anchor_start_ny=ny('10:00'))

    def test_noon_end_state_preserves_closed_eleven_with_no_later_setup(self):
        result = build_other_ranges(self.review, 'NAS100')
        ending = result['shift_end']
        self.assertEqual(ending['through_ny'], ny('12:00'))
        self.assertEqual(ending['active_anchor_ny'], ny('11:00'))
        self.assertEqual(ending['final_hour']['candle_start_ny'], ny('11:00'))
        self.assertEqual(ending['final_hour']['close'], 30875.09)
        self.assertEqual(ending['final_hour']['candle_science'], 'wick_below')
        self.assertEqual(ending['newly_closed_range_observation'], 'no_post_close_evidence_at_cutoff')
        eleven = next(r for r in result['ranges'] if r['anchor_start_ny'] == ny('11:00'))
        self.assertEqual(eleven['observation_status'], 'no_post_close_evidence_at_cutoff')
        self.assertNotEqual(eleven['verdict'], 'failed')
        self.assertIsNone(eleven['invalidated_at_ny'])

    def test_truncated_source_never_promotes_v1_early_or_uses_future_objectives(self):
        row = next(r for r in self.review['shift_story']['ranges'] if r['anchor_start_ny'] == ny('09:00'))
        for cutoff in ('11:00', '11:13'):
            result = classify_structure(row, [b for b in BARS if b['time'] + 60 <= parse_time(ny(cutoff))],
                                        parse_time(ny(cutoff)), 60)
            self.assertEqual(result['labels'], [])
        self.assertEqual([v['code'] for v in classify_structure(row, BARS, parse_time(ny('12:00')), 60)['labels']], ['V1'])
        incomplete = session_review([b for b in BARS if b['time'] < parse_time(ny('11:00'))], DAY['date_ny'], 'day', 60)
        synopsis = incomplete['shift_synopsis']
        self.assertIsNone(synopsis['active_range_context'])
        self.assertNotIn('11:12', synopsis['spoken_summary'])
        self.assertNotIn('supports V1', synopsis['spoken_summary'])
        self.assertFalse(synopsis['shift_end']['progression_complete'])

    def test_post_cutoff_candles_cannot_change_synopsis_or_active_story(self):
        extra = [{**BARS[-1], 'time': parse_time(ny('12:00')) + i * 60,
                  'high': 100000, 'low': 1, 'close': 1} for i in range(60)]
        future = session_review(BARS + extra, DAY['date_ny'], 'day', 60)
        self.assertEqual(build_other_ranges(future, 'NAS100'), build_other_ranges(self.review, 'NAS100'))

    def test_actual_next_selected_at_cutoff_is_unassessed_not_failed(self):
        # Alter one last close to add outside-close invalidation to the already
        # completed range; this is a labelled synthetic edge case.
        bars = deepcopy(BARS)
        bars[-1].update(close=30770, low=min(bars[-1]['low'], 30770))
        review = session_review(bars, DAY['date_ny'], 'day', 60)
        result = build_other_ranges(review, 'NAS100', continue_active=True, anchor_start_ny=ny('09:00'))
        context = result['active_range_context']
        self.assertEqual(context['next_selected_range']['to_anchor_ny'], ny('11:00'))
        self.assertEqual(context['next_selected_range']['confirmed_at_ny'], ny('12:00'))
        nxt = result['next_selected_context'][0]
        self.assertEqual(nxt['selected_at_ny'], ny('12:00'))
        self.assertEqual(nxt['hourly_development'], [])
        self.assertEqual(nxt['conclusion']['status'], 'pending_at_review_cutoff')
        self.assertIn('no later setup or delivery evidence', nxt['spoken_summary'])

    def test_default_and_followups_keep_immutable_raw_evidence_and_payload_budget(self):
        before = deepcopy(self.review)
        synopsis = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100', 'review': self.review})
        self.assertLess(len(json.dumps(synopsis, separators=(',', ':'))), 9000)
        from test_chronological_transport import expand
        active = expand(synopsis)['review']['shift_synopsis']['active_range_context']
        self.assertEqual(active['anchor_start_ny'], ny('11:00'))
        self.assertEqual(active['selected_at_ny'], ny('12:00'))
        self.assertIsNone(active['next_selected_range'])
        self.assertIsNone(active['direction'])
        self.assertIsNone(active['variant_known_at_ny'])
        self.assertEqual(active['conclusion']['status'], 'pending_at_review_cutoff')
        self.assertEqual(active['hourly_development'], [])
        for mode in (False, True):
            result = build_other_ranges(self.review, 'NAS100', [ny('07:00'), ny('08:00'), ny('09:00')], continue_active=mode)
            payload = voice_tool_payload('review_other_market_ranges', {'ok': True, 'asset': 'NAS100', 'review': {'other_range_followup': result}})
            self.assertTrue(payload['ok'])
            self.assertLess(len(json.dumps(payload, separators=(',', ':'))), 12000)
        self.assertEqual(self.review, before)

    def test_short_synopsis_preserves_delivery_before_later_invalidation_and_transition(self):
        bars = deepcopy(BARS)
        bars[-1].update(close=31000, high=max(bars[-1]['high'], 31000))
        review = session_review(bars, DAY['date_ny'], 'day', 60)
        text = review['shift_synopsis']['spoken_summary']
        self.assertIn('11:12 AM M1', text)
        self.assertIn('The 9:00 AM H1 range:', text)
        self.assertIn('invalidated on the closure of the 11:00 AM H1 candle after recorded delivery', text)
        self.assertIn('11:00 AM H1 became the next range under review on the closure of the 11:00 AM H1 candle', text)
        self.assertIn('this is not automatic CRT confirmation', text)
        self.assertIn('No later evidence before the end of the GTOP shift', text)
        active = build_other_ranges(review, 'NAS100', continue_active=True, anchor_start_ny=ny('09:00'))
        self.assertEqual(active['active_range_context']['invalidated_at_ny'], ny('12:00'))
        self.assertTrue(active['active_range_context']['next_selected_range']['at_review_cutoff'])
        other = build_other_ranges(review, 'NAS100', [ny('07:00'), ny('08:00'), ny('09:00')])
        self.assertEqual(other['active_range_context']['anchor_start_ny'], ny('09:00'))
        self.assertEqual(other['shift_end']['active_anchor_ny'], ny('11:00'))
        self.assertNotIn(ny('09:00'), [r['anchor_start_ny'] for r in other['ranges']])
        self.assertIn('invalidated on the closure of the 11:00 AM H1 candle after recorded delivery', other['spoken_summary'])
        self.assertIn('11:00 AM H1 became the next range under review on the closure of the 11:00 AM H1 candle', other['spoken_summary'])
        self.assertLess(other['spoken_summary'].index('11:12 AM M1'),
                        other['spoken_summary'].index('independent range'))


if __name__ == '__main__':
    unittest.main()
