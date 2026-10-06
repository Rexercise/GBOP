"""Synthetic post-delivery shift inventory; no private fixtures or network calls."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import session_review
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS


DAY = '2026-10-02'


def ny(clock, shift='day'):
    value = datetime.fromisoformat(f'{DAY}T{clock}:00-04:00')
    return (value + timedelta(hours=12 if shift == 'night' else 0)).isoformat()


def fixture(outcome='pending', shift='day'):
    start = parse_time(ny('07:00', shift))
    hours = [(100, 150, 50, 100), (100, 120, 80, 100),
             (100, 110, 90, 100), (100, 110, 90, 100), (110, 115, 105, 110)]
    bars = [dict(time=start + hour * 3600 + minute * 60, open=o, high=h, low=l, close=c)
            for hour, (o, h, l, c) in enumerate(hours) for minute in range(60)]
    # Eight delivers by 9:41. Ten only becomes relevant at the 11:00 purge,
    # while eight remains selected, so completion never changes the anchor.
    bars[121].update(open=115, high=125, low=110, close=115)
    bars[160].update(open=100, high=105, low=75, close=100)
    if outcome == 'failed':
        bars[-1]['close'] = 114
    elif outcome == 'delivered':
        bars[260].update(open=105, low=85, close=100)
    elif outcome == 'unverified':
        del bars[250]
    elif outcome == 'unordered':
        bars[240]['low'] = 85
    elif outcome == 'cutoff_selection':
        bars[-1].update(high=130, close=130)
    elif outcome == 'untouched':
        for bar in bars[240:]:
            bar.update(open=100, high=105, low=95, close=100)
    return bars


class LaterShiftRangeTests(unittest.TestCase):
    def synopsis(self, bars, shift='day'):
        review = session_review(bars, DAY, shift, 60)
        before = deepcopy(review)
        synopsis = build_shift_synopsis(review, 'NAS100')
        self.assertEqual(review, before)
        return review, synopsis

    def test_after_first_delivery_later_ranges_keep_all_four_outcomes(self):
        statuses = {'pending': 'pending_at_review_cutoff', 'failed': 'failed_before_objectives',
                    'delivered': 'opposing_liquidity_delivered', 'unverified': 'unverified'}
        for shift in ('day', 'night'):
            for outcome, expected in statuses.items():
                with self.subTest(shift=shift, outcome=outcome):
                    review, synopsis = self.synopsis(fixture(outcome, shift), shift)
                    lead, later = synopsis['ranges']
                    self.assertEqual(lead['outcome'], 'opposing_liquidity_delivered')
                    self.assertEqual(lead['opposing_liquidity']['source_interval']['bar_close_ny'], ny('09:41', shift))
                    self.assertEqual(later['anchor_start_ny'], ny('10:00', shift))
                    self.assertEqual(later['role'], 'independent_range_context')
                    self.assertEqual(later['outcome'], expected)
                    self.assertEqual(later['first_purge_interval']['bar_open_ny'], ny('11:00', shift))
                    self.assertEqual(later['relevance'], {
                        'basis': 'post_close_purge', 'known_at_ny': ny('11:01', shift),
                        'purged_side': 'buy', 'selected_anchor_ny': ny('08:00', shift)})
                    text = synopsis['spoken_summary']
                    self.assertIn('Independent ' + ('10:00 AM' if shift == 'day' else '10:00 PM') + ' H1 range', text)
                    self.assertIn('buy-side purge', text)
                    self.assertIn({'pending': '50%/sell-side pending', 'failed': 'failed before 50%/sell-side',
                        'delivered': '50% and sell-side delivered', 'unverified': '50%/sell-side unverified'}[outcome], text)
                    if outcome == 'unverified':
                        self.assertIsNone(synopsis['active_range_context'])
                    else:
                        self.assertEqual(synopsis['active_range_context']['anchor_start_ny'], ny('08:00', shift))
                    self.assertEqual(review['shift_story']['range_transitions'], [])
                    self.assertEqual(synopsis['through_ny'], ny('12:00', shift))
                    wire = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100', 'review': review})
                    self.assertTrue(wire['ok'])
                    self.assertLess(len(json.dumps(wire, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)

    def test_untouched_hours_and_unobserved_final_anchor_are_not_automatic_theses(self):
        _, synopsis = self.synopsis(fixture('untouched'))
        self.assertEqual([row['anchor_start_ny'] for row in synopsis['ranges']], [ny('08:00')])
        self.assertEqual(len(synopsis['range_index']), 5)
        self.assertNotIn('Independent', synopsis['spoken_summary'])
        self.assertNotIn('11:00 AM H1 range', synopsis['spoken_summary'])

    def test_incomplete_untouched_final_anchor_is_not_a_relevant_setup(self):
        _, synopsis = self.synopsis(fixture('unverified'))
        self.assertNotIn(ny('11:00'), [row['anchor_start_ny'] for row in synopsis['ranges']])
        self.assertEqual(synopsis['ranges'][-1]['outcome'], 'unverified')

    def test_same_source_bar_two_sides_preserves_unknown_direction_and_order(self):
        _, synopsis = self.synopsis(fixture('unordered'))
        later = synopsis['ranges'][-1]
        self.assertEqual(later['outcome'], 'unverified')
        self.assertIsNone(later['direction'])
        self.assertEqual(later['relevance']['purged_side'], 'both')
        self.assertIn('two-sided purge', synopsis['spoken_summary'])
        self.assertNotIn('after completion', synopsis['spoken_summary'])

    def test_real_cutoff_only_selection_is_relevant_but_remains_unknown(self):
        _, synopsis = self.synopsis(fixture('cutoff_selection'))
        self.assertEqual(synopsis['ranges'][0]['outcome'], 'opposing_liquidity_delivered')
        last = synopsis['ranges'][-1]
        self.assertEqual(last['anchor_start_ny'], ny('11:00'))
        self.assertEqual(last['role'], 'selected_range')
        self.assertEqual((last['verdict'], last['outcome']), ('unverified', 'unverified'))
        self.assertEqual(last['observation_status'], 'no_post_close_evidence_at_cutoff')
        self.assertEqual(last['relevance'], {'basis': 'selected_range_transition', 'known_at_ny': ny('12:00')})
        self.assertIn('11:00 AM H1 range became selected', synopsis['spoken_summary'])
        self.assertIn('later setup/delivery unknown', synopsis['spoken_summary'])

    def test_appended_post_cutoff_bars_cannot_create_a_later_range_or_outcome(self):
        bars = fixture()
        _, before = self.synopsis(bars)
        bars += [dict(time=parse_time(ny('12:00')) + minute * 60,
                      open=100, high=1000, low=1, close=1000) for minute in range(60)]
        _, after = self.synopsis(bars)
        self.assertEqual(after, before)

    def test_evidenced_boneless_pending_range_has_its_own_setup_and_outcome(self):
        review, _ = self.synopsis(fixture('untouched'))
        review['shift_story']['recap']['paired_interpretation'] = [{
            'anchor_start_ny': ny('09:00'), 'direction': 'bullish', 'asset_role': 'boneless leg',
            'setup_interval': {'start_ny': ny('10:00'), 'end_ny': ny('11:00'),
                               'qualified_smt': True, 'potential_smt': False},
            'objective_status': {'midpoint': {'status': 'pending_at_review_cutoff', 'level': 100},
                                 'opposing_liquidity': {'status': 'pending_at_review_cutoff', 'level': 125}}}]
        synopsis = build_shift_synopsis(review, 'NAS100')
        lead, later = synopsis['ranges']
        self.assertEqual(lead['outcome'], 'opposing_liquidity_delivered')
        self.assertEqual((later['verdict'], later['outcome']), ('boneless_pending', 'pending_at_review_cutoff'))
        self.assertEqual(later['relevance']['basis'], 'paired_setup')
        self.assertNotEqual(later['direction'], lead['direction'])
        self.assertEqual(later['relevance']['selected_anchor_ny'], lead['anchor_start_ny'])
        self.assertIn('bullish boneless from paired setup by 11:00 AM', synopsis['spoken_summary'])
        self.assertIn('50%/buy-side pending', synopsis['spoken_summary'])
        review['shift_story']['recap']['paired_interpretation'][0]['setup_interval']['end_ny'] = ny('12:01')
        bounded = build_shift_synopsis(review, 'NAS100')
        self.assertEqual([row['anchor_start_ny'] for row in bounded['ranges']], [ny('08:00')])

    def test_independent_later_range_renders_legacy_v2_with_canonical_full_name(self):
        review, _ = self.synopsis(fixture('delivered'))
        later = next(row for row in review['shift_story']['ranges'] if row['anchor_start_ny'] == ny('10:00'))
        later['variant_evidence'] = {'labels': [{'code': 'V2', 'name': 'Kryptonite'}]}
        before = deepcopy(review)
        synopsis = build_shift_synopsis(review, 'NAS100')
        clause = synopsis['spoken_summary'].split('Independent 10:00 AM H1 range:', 1)[1]
        self.assertIn('V2 — Pattern Trader’s Kryptonite', clause)
        self.assertNotIn('V2 — Kryptonite', clause)
        self.assertEqual(review, before)


if __name__ == '__main__':
    unittest.main()
