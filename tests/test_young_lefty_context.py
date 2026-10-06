"""Synthetic conditional checks never choose the user's HTF narrative."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review
from gbop_voice_web.market_data import attach_lifecycle, session_review
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.current_market import _range_fact
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from test_native_anchor_resolution import START, bars, native


def two_sided():
    rows = bars()
    rows[65].update(open=108, high=112, low=107, close=109)
    rows[85].update(open=95, high=97, low=88, close=94)
    rows[125].update(open=104, high=111, low=103, close=105)
    rows[179].update(high=112, close=111)
    return rows


def review(rows, cutoff=START+18000, native_h1=None):
    return attach_lifecycle(crt_review(rows, START, cutoff, 'H1', 60,
                                      native_h1=native_h1), rows, cutoff, 60)


class YoungLeftyContextTests(unittest.TestCase):
    def test_two_sided_hour_has_conditional_v1_v2_without_selecting_direction(self):
        result = review(two_sided())
        context = result['young_lefty_context']
        self.assertIsNone(context['selected_direction'])
        checks = {c['direction']: c for c in context['directional_checks']}
        self.assertEqual([v['code'] for v in checks['bullish']['variant_evidence']['labels']], ['V1'])
        self.assertEqual([v['code'] for v in checks['bearish']['variant_evidence']['labels']], ['V2'])
        self.assertEqual(result['observed_direction'], 'bearish')  # original price audit is preserved
        self.assertIsNone(result['directional_outcome']['play_context'])
        self.assertIsNone(result['double_purge']['original_outcome']['play_context'])
        self.assertNotIn('(Young Lefty)', result['double_purge']['original_outcome']['spoken_summary'])
        self.assertIn('physical_first_purge', result['variant_evidence']['scope'])
        self.assertIn('HTF/narrative-dependent', result['recap']['spoken_summary'])
        self.assertNotIn('Young Lefty) completed its bearish', result['recap']['spoken_summary'])
        self.assertEqual(result['double_purge']['named_play_applicability'],
                         'context_dependent_not_selected_young_lefty')

    def test_native_anchor_and_source_gaps_stay_distinct_in_conditional_view(self):
        rows = two_sided(); higher = native(rows); del rows[17:29]
        result = review(rows, native_h1=[higher])
        self.assertEqual(result['anchor']['missing_bar_count'], 12)
        self.assertFalse(result['anchor']['source_coverage_complete'])
        self.assertIsNone(result['young_lefty_context']['selected_direction'])

    def test_same_hour_body_model_and_csd_never_auto_select_direction(self):
        rows = two_sided()
        for i in range(85, 90):
            rows[i].update(open=95 if i == 85 else 89, high=96, low=88, close=89)
        for i in range(90, 95):
            rows[i].update(open=95, high=105, low=94, close=104)
        result = review(rows)
        self.assertTrue(any(c['direction'] == 'bullish' and c.get('csd', {}).get('status') == 'confirmed'
                            for c in result['candle_lifecycle']['purge_candles']))
        self.assertIsNone(result['young_lefty_context']['selected_direction'])

    def test_incomplete_distribution_hour_does_not_look_ahead_for_v1(self):
        result = review(two_sided(), START+9000)
        context = result['young_lefty_context']
        self.assertFalse(context['distribution']['complete'])
        bull = next(c for c in context['directional_checks'] if c['direction'] == 'bullish')
        self.assertEqual(bull['variant_evidence']['labels'], [])
        self.assertIsNone(context['selected_direction'])

    def test_missing_post_anchor_order_blocks_conditional_variant(self):
        rows = two_sided(); del rows[100]
        result = review(rows)
        self.assertNotIn('young_lefty_context', result)  # manipulation hour incomplete
        self.assertEqual(result['variant_evidence']['labels'], [])

    def test_one_sided_manipulation_does_not_gain_a_new_selection_policy(self):
        rows = bars(); rows[65].update(low=88); rows[125].update(high=111)
        result = review(rows)
        self.assertNotIn('young_lefty_context', result)
        self.assertEqual(result['observed_direction'], 'bullish')

    def test_session_and_voice_keep_neutral_selection_and_native_gap(self):
        rows = two_sided(); higher = native(rows); del rows[17:29]
        session = session_review(rows, '2026-06-11', 'day', 60, native_h1=[higher])
        before = deepcopy(session)
        packet = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100', 'review': session})
        self.assertEqual(session, before)
        synopsis = packet['review']['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'context_dependent')
        self.assertTrue(synopsis['young_lefty_coverage']['complete'])
        self.assertFalse(synopsis['young_lefty_coverage']['source_coverage_complete'])
        young = next(r for r in synopsis['ranges'] if r.get('play') == 'Young Lefty')
        self.assertIsNone(young['young_lefty_context']['selected_direction'])
        self.assertIsNone(young['direction'])
        self.assertIsNone(young['opposes_9ate8'])
        self.assertEqual(young['verdict'], 'context_dependent')
        self.assertEqual(young['outcome'], 'context_dependent')
        self.assertEqual(young['physical_path_audit']['direction'], 'bearish')
        self.assertEqual(young['physical_path_audit']['variant']['labels'][0]['code'], 'V2')
        self.assertIn('conditional direction checks', synopsis['spoken_summary'])
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, separators=(',', ':'))),
                             SHIFT_SYNOPSIS_TARGET_CHARS)

    def test_current_followup_retains_unselected_context_and_physical_audit_scope(self):
        result = review(two_sided())
        result['anchor'].update(forming=False, status='closed', observed_through_ny='2026-06-11T08:00:00-04:00')
        fact = _range_fact(result, 'selected_range', 'Young Lefty', 'NAS100', START+18000, 'H1', 'M5')
        retained = MarketConversation._evidence('review_current_market',
            {'review': {'ranges': [fact], 'mode': 'current_market'}},
            {'asset': 'NAS100', 'date_ny': '2026-06-11', 'shift': 'day'})
        saved = retained['range_outcomes'][0]
        self.assertIsNone(saved['young_lefty_context']['selected_direction'])
        self.assertEqual(saved['direction_scope'], 'context_dependent_no_selected_thesis')
        self.assertIsNone(saved['direction'])
        self.assertEqual(saved['physical_path_audit']['direction'], 'bearish')
        self.assertIn('HTF/narrative-dependent', saved['spoken_summary'])


if __name__ == '__main__':
    unittest.main()
