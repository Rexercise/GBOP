"""Presentation-only status changes; retained history plus labeled synthetic edges."""
from copy import deepcopy
from pathlib import Path
import unittest

from gbop_voice_web.active_range_story import _double_context, _double_sentence
from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import attach_lifecycle, session_review
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.target_approach import inducement_clause
import test_retained_market_replays as retained


def synthetic_cisd_outside_h1():
    """M5 CISD below H1 low; the completed H1 itself closes back inside."""
    start = parse_time(retained.ny('09:00'))
    bars = [dict(time=start + i * 300, open=100, high=110, low=90, close=100)
            for i in range(12)]
    sequence = [(108, 113, 107, 112), (112, 113, 88, 88), (88, 105, 88, 104)]
    sequence += [(104, 106, 100, 104)] * 9
    bars.extend(dict(time=start + (12 + i) * 300, open=o, high=h, low=l, close=c)
                for i, (o, h, l, c) in enumerate(sequence))
    return bars, start, start + 7200


class RecapStatusWordingTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)

    def test_retained_reversal_non_delivery_is_not_structural_failure(self):
        review = self.replay.tool('NAS100')['review']
        before = deepcopy(review)
        story = review['shift_story']
        row = next(r for r in story['ranges'] if r['anchor_start_ny'] == retained.ny('09:00'))
        value = _double_context(row, 'NAS100', story)
        self.assertEqual(value['reversal_outcome'], 'pending_at_review_cutoff')
        self.assertEqual(value['presentation_outcome'], 'failed_to_deliver_objectives_by_shift_end')
        self.assertIsNone(value['invalidated_at_ny'])
        text = _double_sentence(value)
        self.assertIn('failed to deliver objectives by shift end', text)
        self.assertIn('Neither 50% nor the original boundary was reached', text)
        self.assertIn('not structurally invalidated', text)
        self.assertEqual(value['original_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(value['objectives']['midpoint']['distance_price_points'], 33.2)
        self.assertEqual(value['objectives']['original_side']['distance_price_points'], 116.26)
        self.assertEqual(review, before)

    def test_live_earlier_cutoff_remains_pending_without_future_metrics(self):
        bars = self.replay.bars['NAS100']
        start, end = parse_time(retained.ny('09:00')), parse_time(retained.ny('11:30'))
        row = attach_lifecycle(crt_review(bars, start, end, 'H1', 60), bars, end, 60)
        value = _double_context(row, 'NAS100')
        self.assertEqual(value['presentation_outcome'], 'pending_at_review_cutoff')
        text = _double_sentence(value)
        self.assertIn('remained pending at the cutoff', text)
        self.assertNotIn('shift end', text)
        self.assertNotIn('33.20', text)
        self.assertNotIn('induced', text)

    def test_missing_reversal_coverage_does_not_become_shift_end_failure(self):
        bars = [b for b in self.replay.bars['NAS100'] if b['time'] != parse_time(retained.ny('11:40'))]
        review = session_review(bars, '2026-10-02', 'day', 60)
        story = review['shift_story']
        row = next(r for r in story['ranges'] if r['anchor_start_ny'] == retained.ny('09:00'))
        value = _double_context(row, 'NAS100', story)
        self.assertEqual(value['presentation_outcome'], 'unverified')
        self.assertIn('unverified outcome', _double_sentence(value))
        self.assertNotIn('failed to deliver objectives by shift end', review['shift_synopsis']['spoken_summary'])

    def test_synthetic_structural_failure_stays_distinct(self):
        bars = deepcopy(self.replay.bars['NAS100'])
        # Fault injection: final H1 closes below its selected nine-range low.
        bars[-1].update(close=30770, low=30770)
        review = session_review(bars, '2026-10-02', 'day', 60)
        story = review['shift_story']
        row = next(r for r in story['ranges'] if r['anchor_start_ny'] == retained.ny('09:00'))
        value = _double_context(row, 'NAS100', story)
        self.assertEqual(value['presentation_outcome'], 'failed_before_objectives')
        self.assertEqual(value['invalidated_at_ny'], retained.ny('12:00'))
        self.assertIn('on structural invalidation', _double_sentence(value))
        self.assertNotIn('not structurally invalidated', _double_sentence(value))

    def test_m5_cisd_outside_h1_does_not_invalidate_h1(self):
        bars, start, end = synthetic_cisd_outside_h1()
        review = attach_lifecycle(crt_review(bars, start, end, 'H1', 300), bars, end, 300)
        model = review['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(model['csd']['status'], 'confirmed')
        self.assertLess(model['csd']['evidence']['close'], review['anchor']['low'])
        self.assertEqual(model['csd']['evidence']['timeframe'], 'M5')
        self.assertIsNone(review.get('invalidated_at_ny'))
        self.assertEqual(model['model1_crt_invalidating_close']['bar_open_ny'], retained.ny('10:05'))

    def test_h1_own_close_still_invalidates_and_preserves_earlier_delivery(self):
        bars, start, end = synthetic_cisd_outside_h1()
        bars[-1].update(low=87, close=88)
        review = attach_lifecycle(crt_review(bars, start, end, 'H1', 300), bars, end, 300)
        self.assertEqual(review['invalidated_at_ny'], retained.ny('11:00'))
        self.assertEqual(review['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertTrue(review['directional_outcome']['delivery_before_later_invalidation'])

    def test_missing_shift_still_states_young_lefty_uncertainty(self):
        result = build_shift_synopsis({'shift_story': {'ranges': []}})
        self.assertEqual(result['young_lefty_status'], 'unverified')
        self.assertIn('Young Lefty: unverified', result['spoken_summary'])

    def test_unattributed_gap_or_touch_never_becomes_inducement(self):
        base = {'distance_price_points': 7.76,
                'boundary_to_target_reference': {'progress_percent': 94}}
        self.assertEqual(inducement_clause(base, 'bearish'), '')
        base.update(gtop_context={'basis': 'explicit_owner_characterization'}, distance_price_points=0)
        self.assertEqual(inducement_clause(base, 'bearish'), '')

    def test_both_canonical_invalidation_sections_name_own_timeframe(self):
        knowledge = (Path(__file__).resolve().parents[1] / 'gbop_voice_web/gtop_knowledge.txt').read_text()
        self.assertEqual(knowledge.count("selected range's OWN timeframe"), 2)
        self.assertIn('M5 close/CISD outside an H1 boundary alone does not invalidate', knowledge)
        self.assertIn('M5 close/CISD alone cannot invalidate the selected H1 range', knowledge)


if __name__ == '__main__':
    unittest.main()
