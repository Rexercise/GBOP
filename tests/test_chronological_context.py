"""Synthetic range/context regressions. No private journal or market fixtures."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.chronological_context import build_context_graph
from gbop_voice_web.market_data import session_review
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS

DAY = '2026-09-17'


def ny(clock):
    return DAY + 'T' + clock + ':00-04:00'


def synthetic():
    hours = [(100, 150, 50, 100), (100, 120, 80, 100),
             (100, 110, 90, 100), (95, 110, 85, 95), (105, 110, 100, 105)]
    start = parse_time(ny('07:00'))
    bars = [dict(time=start+h*3600+m*60, open=o, high=hi, low=lo, close=c)
            for h, (o, hi, lo, c) in enumerate(hours) for m in range(60)]
    def change(clock, **values):
        next(b for b in bars if b['time'] == parse_time(ny(clock))).update(values)
    change('09:01', open=110, high=125, low=105, close=122)
    change('09:40', open=90, high=92, low=75, close=78)
    change('09:41', open=78, high=92, low=76, close=90)
    change('10:01', open=80, high=82, low=70, close=72)
    change('10:02', open=72, high=88, low=71, close=85)
    change('10:10', open=95, high=115, low=90, close=100)
    change('10:59', close=100)
    change('11:00', open=110, high=118, low=109, close=117)
    change('11:01', open=117, high=119, low=106, close=110)
    # Complete body outside 10's high across the assigned 11:00 M5.
    change('11:04', open=110, high=118, low=109, close=117)
    change('11:09', low=98, close=99)
    change('11:45', open=110, high=117, low=109, close=116)
    change('11:49', open=110, high=117, low=109, close=116)
    return bars


def review(bars=None):
    return session_review(synthetic() if bars is None else bars, DAY, 'day', 60)


class ChronologicalContextTests(unittest.TestCase):
    def test_completion_hands_off_on_completion_candle_closure_without_crt_promotion(self):
        result = review()
        story = result['shift_story']
        first = story['range_transitions'][0]
        self.assertEqual(first['from_anchor_ny'], ny('08:00'))
        self.assertEqual(first['to_anchor_ny'], ny('09:00'))
        self.assertEqual(first['confirmed_at_ny'], ny('10:00'))
        self.assertEqual(first['reason'], 'opposing_objective_completed')
        self.assertEqual(first['next_status'], 'range_under_review')
        self.assertFalse(first['crt_established_by_handoff'])
        self.assertEqual(story['active_anchor_ny'], ny('09:00'))
        text = result['shift_synopsis']['spoken_summary']
        self.assertIn('next range under review on the closure of the 9:00 AM H1 candle', text)
        self.assertIn('not automatic CRT confirmation', text)
        self.assertIn('end of the GTOP day shift', text)
        self.assertIn('9:00 AM to 12:00 PM New York', text)

    def test_two_earlier_bullish_ranges_and_one_countertrend_range_count_once_each(self):
        result = review()
        graph = build_context_graph(result['shift_story'])
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 3)
        self.assertEqual(graph['at_shift_end']['bullish_ranges'], 2)
        self.assertEqual(graph['at_shift_end']['bearish_ranges'], 1)
        relation = next(r for r in graph['relationships'] if r['later_context_id'] == ny('10:00')+'/original')
        self.assertEqual(relation['earlier_opposing_distinct_ranges'], 2)
        self.assertEqual(relation['countertrend_to'], [ny('08:00')+'/double_purge', ny('09:00')+'/original'])
        text = result['shift_synopsis']['spoken_summary']
        self.assertIn('2 earlier intact distinct ranges', text)
        self.assertIn('the 8:00 AM H1 double-purge range', text)
        self.assertIn('the 9:00 AM H1 range', text)
        self.assertIn('probability', graph['response_contract'])

    def test_source_return_and_h1_confirmation_are_distinct_from_named_eligibility(self):
        bars = synthetic()
        truncated = [b for b in bars if b['time'] < parse_time(ny('10:30'))]
        nine = review(truncated)['shift_story']['ranges'][1]
        qualifier = nine['context_qualification']
        self.assertTrue(qualifier['source_return_observed'])
        self.assertFalse(qualifier['selected_tf_return_confirmed'])
        self.assertEqual(qualifier['crt_status'], 'developing_source_return_only')
        self.assertEqual(qualifier['named_play_eligibility'], 'unchanged_separate_assessment')
        graph = build_context_graph(review(truncated)['shift_story'])
        self.assertNotIn(ny('09:00')+'/original', [c['context_id'] for c in graph['contexts']])

    def test_midpoint_only_does_not_retire_full_dol(self):
        graph = build_context_graph(review()['shift_story'])
        double = next(c for c in graph['contexts'] if c['phase'] == 'double_purge')
        self.assertEqual(double['midpoint_status'], 'observed_after_confirmation')
        self.assertEqual(double['status'], 'active_full_DOL_pending')
        self.assertIsNone(double['retired_at_ny'])
        self.assertIn(double['context_id'], graph['at_shift_end']['active_context_ids'])

    def test_full_completion_and_invalidation_retire_at_event_time_not_final_votes(self):
        story = review()['shift_story']
        eight, nine = story['ranges'][:2]
        eight['double_purge']['reversal_thesis']['objectives']['original_side'].update(
            status='observed_after_confirmation', evidence={'bar_open_ny': ny('11:15'), 'bar_close_ny': ny('11:16')})
        nine['invalidated_at_ny'] = ny('12:00')
        graph = build_context_graph(story)
        later = next((r for r in graph['relationships'] if r['later_context_id'] == ny('10:00')+'/original'), None)
        self.assertIsNone(later)
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 1)
        self.assertEqual(graph['at_shift_end']['bearish_ranges'], 1)

    def test_incomplete_prefix_and_unknown_objective_order_are_not_intact_votes(self):
        story = review()['shift_story']
        story['ranges'][0]['context_qualification']['double_purge_evidence_through_ny'] = ny('10:30')
        full = story['ranges'][1]['directional_outcome']['opposing_liquidity']
        full['status'] = 'same_bar_order_unknown'
        graph = build_context_graph(story)
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 1)
        self.assertFalse(any(r['countertrend_to'] for r in graph['relationships']))

    def test_later_gap_does_not_erase_earlier_concurrent_relationship(self):
        bars = [b for b in synthetic() if b['time'] != parse_time(ny('11:30'))]
        graph = build_context_graph(review(bars)['shift_story'])
        relation = next(r for r in graph['relationships'] if r['later_context_id'] == ny('09:00')+'/original')
        self.assertEqual(relation['aligned_with'], [ny('08:00')+'/double_purge'])
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 0)

    def test_native_confirmation_survives_missing_pre_confirmation_source_minute(self):
        from test_native_anchor_resolution import native
        bars = synthetic()
        higher = native(bars, parse_time(ny('09:00')))
        higher['provenance']['captured_at'] = parse_time(ny('12:00'))
        bars = [b for b in bars if b['time'] != parse_time(ny('09:59'))]
        result = session_review(bars, DAY, 'day', 60, native_h1=[higher])
        graph = build_context_graph(result['shift_story'])
        self.assertTrue(result['shift_story']['ranges'][0]['double_purge']['observed'])
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 3)
        later = next(r for r in graph['relationships'] if r['later_context_id'] == ny('10:00')+'/original')
        self.assertEqual(later['earlier_opposing_distinct_ranges'], 2)

    def test_boundary_order_uncertainty_starts_at_boundary_not_establishment(self):
        bars = synthetic()
        bars[-1].update(high=123, close=122)
        graph = build_context_graph(review(bars)['shift_story'])
        earlier = next(r for r in graph['relationships'] if r['later_context_id'] == ny('09:00')+'/original')
        self.assertEqual(earlier['aligned_with'], [ny('08:00')+'/double_purge'])

    def test_target_in_invalidating_bar_does_not_retire_as_completed(self):
        bars = synthetic()
        bars[-1].update(high=131, close=130)
        graph = build_context_graph(review(bars)['shift_story'])
        nine = next(c for c in graph['contexts'] if c['context_id'] == ny('09:00')+'/original')
        self.assertEqual(nine['retirement_reason'], 'range_invalidated')
        self.assertEqual(nine['remaining_DOL']['status'], 'touch_in_invalidating_bar_order_unresolved')

    def test_lifecycle_identity_page_cannot_hide_later_primary_body(self):
        bars = synthetic()
        for bar in bars:
            if bar['time'] >= parse_time(ny('09:00')):
                bar.update(open=100, high=121, low=99, close=100)
        next(b for b in bars if b['time'] == parse_time(ny('11:49'))).update(high=123, close=122)
        fact = review(bars)['shift_synopsis']['ranges'][0]['pending_range']['primary_body_model1']
        self.assertEqual(fact['status'], 'present')
        self.assertEqual(fact['bar_open_ny'], ny('11:45'))
        self.assertEqual(fact['own_CRT_status'], 'unverified')

    def test_one_sided_young_range_remains_a_separate_context(self):
        bars = synthetic()
        for bar in bars:
            if bar['time'] < parse_time(ny('08:00')):
                bar.update(high=130, low=60)
            elif bar['time'] < parse_time(ny('09:00')):
                bar['low'] = 50
        result = review(bars)
        young = next(o['evidence'] for o in result['observations'] if o['play'] == 'Young Lefty')
        graph = build_context_graph(result['shift_story'], young)
        self.assertIn(ny('07:00')+'/original', [c['context_id'] for c in graph['contexts']])
        later = next(r for r in graph['relationships'] if r['later_context_id'] == ny('10:00')+'/original')
        self.assertIn(ny('07:00')+'/original', later['countertrend_to'])

    def test_young_double_purge_uses_its_own_post_confirmation_coverage(self):
        bars = synthetic()
        for bar in bars:
            if bar['time'] < parse_time(ny('08:00')):
                bar.update(high=130, low=60)
            elif bar['time'] < parse_time(ny('09:00')):
                bar['low'] = 50
        next(b for b in bars if b['time'] == parse_time(ny('09:01'))).update(high=140, close=132)
        result = review(bars)
        young = next(o['evidence'] for o in result['observations'] if o['play'] == 'Young Lefty')
        self.assertTrue(young['double_purge']['observed'])
        graph = build_context_graph(result['shift_story'], young)
        young_dp = next(c for c in graph['contexts'] if c['context_id'] == ny('07:00')+'/double_purge')
        self.assertEqual(young_dp['status'], 'active_full_DOL_pending')
        self.assertIn(young_dp['context_id'], graph['at_shift_end']['active_context_ids'])

    def test_pending_reversal_owns_opposite_primary_without_replacing_original(self):
        bars = synthetic()
        next(b for b in bars if b['time'] == parse_time(ny('09:04'))).update(high=125, close=122)
        next(b for b in bars if b['time'] == parse_time(ny('09:44'))).update(open=78, high=92, low=76, close=78)
        result = review(bars)
        lead = result['shift_synopsis']['ranges'][0]
        reverse = lead['pending_reversal']
        self.assertEqual(reverse['direction'], 'bullish')
        self.assertEqual(reverse['remaining_DOL_side'], 'buy')
        self.assertEqual(reverse['primary_body_model1']['bar_open_ny'], ny('09:40'))
        self.assertEqual(reverse['primary_body_model1']['status'], 'present')
        self.assertEqual(lead['direction'], 'bearish')
        original = result['shift_story']['ranges'][0]['directional_outcome']['initiating_identity']
        self.assertEqual(original['bar_open_ny'], ny('09:00'))
        self.assertIn('8:00 AM bullish double-purge reversal, primary body Model 1 is the 9:40 AM M5 candle',
                      result['shift_synopsis']['spoken_summary'])
        self.assertIn('own_CRT_status', reverse['primary_body_model1'])
        self.assertIn('strict_CISD_status', reverse['primary_body_model1'])

    def test_missing_reversal_body_does_not_inherit_original_body(self):
        bars = synthetic()
        next(b for b in bars if b['time'] == parse_time(ny('09:04'))).update(high=125, close=122)
        result = review(bars)
        primary = result['shift_synopsis']['ranges'][0]['pending_reversal']['primary_body_model1']
        self.assertNotEqual(primary['status'], 'present')
        self.assertNotIn('bar_open_ny', primary)
        self.assertEqual(result['shift_story']['ranges'][0]['directional_outcome']['initiating_identity']['bar_open_ny'], ny('09:00'))

    def test_completed_super_soup_is_preserved_before_outside_close_narration(self):
        from gbop_voice_web.chronological_context import pending_range_facts, pending_sentence
        result = review()
        row = result['shift_story']['ranges'][2]
        primary = next(p for p in row['candle_lifecycle']['purge_candles'] if p['bar_open_ny'] == ny('11:00'))
        # Forward-compatible synthetic classifier contract; no historical fixture edit.
        primary['super_soup_structure'] = {'variant_status': 'distribution_observed',
            'variants': [{'code': 'V2', 'name': 'Pattern Trader’s Kryptonite'}],
            'completion_known_at_ny': ny('11:10'), 'completion_preserved_after_outside_close': True}
        fact = next(f for f in result['shift_synopsis']['ranges'] if f['anchor_start_ny'] == ny('10:00'))
        pending = pending_range_facts(row, fact)
        self.assertEqual(pending['primary_body_model1']['own_CRT_status'], 'completed')
        self.assertEqual(pending['primary_body_model1']['own_invalidating_candle_ny'], ny('11:05'))
        text = pending_sentence(pending)
        self.assertIn('own Super Soup V2 — Pattern Trader’s Kryptonite completed', text)
        self.assertIn('completion preserved despite its outside close', text)
        self.assertNotIn('own CRT invalidated', text)
        self.assertLess(text.index('Kryptonite completed'), text.index('strict CISD'))

    def test_primary_body_identity_is_original_and_not_latest_repurge(self):
        result = review()
        fact = next(r for r in result['shift_synopsis']['ranges'] if r['anchor_start_ny'] == ny('10:00'))
        primary = fact['pending_range']['primary_body_model1']
        self.assertEqual(primary['status'], 'present')
        self.assertEqual(primary['bar_open_ny'], ny('11:00'))
        self.assertEqual(primary['timeframe'], 'M5')
        self.assertGreaterEqual(primary['later_body_repurges'], 1)
        self.assertEqual(primary['own_CRT_status'], 'invalidated')
        self.assertIsNone(fact['invalidated_at_ny'])
        self.assertIn('strict_CISD', primary)
        text = result['shift_synopsis']['spoken_summary']
        self.assertIn('Primary body Model 1 is the 11:00 AM M5 candle', text)
        self.assertNotIn('Primary body Model 1 is the 11:45 AM', text)

    def test_voice_retains_context_pending_primary_and_variant_under_budget(self):
        raw = {'ok': True, 'asset': 'NAS100', 'review': review()}
        before = deepcopy(raw)
        wire = voice_tool_payload('review_market_session', raw)
        self.assertTrue(wire['ok'], wire)
        self.assertLess(len(json.dumps(wire, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)
        facts = wire['review']['shift_synopsis']
        self.assertEqual(facts['chronological_context']['at_shift_end']['bullish_ranges'], 2)
        self.assertIn('V1 Textbook/V3 extended distribution candidate because', facts['spoken_summary'])
        self.assertEqual(raw, before)
        from gbop_voice_web.market_conversation import MarketConversation
        context = MarketConversation()
        context.begin_turn()
        scoped = context.run('review_market_session', dict(asset='NAS100', date_ny=DAY, shift='day'), lambda n, a: raw)
        self.assertTrue(voice_tool_payload('review_market_session', scoped)['ok'])

    def test_append_after_cutoff_is_inert(self):
        bars = synthetic()
        before = review(bars)
        bars += [dict(time=parse_time(ny('12:00'))+i*60, open=100, high=1000, low=1, close=900) for i in range(60)]
        self.assertEqual(before, review(bars))


if __name__ == '__main__':
    unittest.main()
