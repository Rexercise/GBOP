"""Synthetic same-parent post-shift variants; no live feed or execution claims."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.post_shift_followthrough import (
    build_followthrough, continuation_candidates, delivery_variant_clause,
)
from gbop_voice_web.shift_narrative import attach_directional_outcome
from test_chronological_context import synthetic, review, ny
from test_post_shift_followthrough import later, change


class PostShiftVariantTests(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic()
        self.frozen = review(self.bars)

    def follow(self, rows, anchor='09:00', phase='original', through='14:00', before=None):
        source = self.frozen if before is None else before
        selected = next(c for c in continuation_candidates(source, 'NAS100', 'NAS100m')
                        if c['anchor_start_ny'] == ny(anchor) and c['phase'] == phase)
        return build_followthrough(source, rows, asset='NAS100', symbol='NAS100m',
            anchor_start_ny=selected['anchor_start_ny'], phase=phase,
            expected_scope_id=selected['source_scope_id'], through=parse_time(ny(through)),
            as_of=parse_time(ny(through)))['review']

    def test_original_delivery_reports_final_variant_and_frozen_midpoint(self):
        rows = self.bars + later()
        change(rows, '12:15', high=130)
        before = deepcopy((self.frozen, rows))
        result = self.follow(rows)
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['status'], 'distribution_observed')
        self.assertEqual([v['code'] for v in variant['labels']], ['V3'])
        self.assertEqual(variant['evidence_through_ny'], ny('13:00'))
        mid = variant['delivery_milestones']['midpoint']
        original = self.frozen['shift_story']['ranges'][1]['variant_evidence']['delivery_milestones']['midpoint']
        self.assertEqual(mid, original)
        self.assertEqual(mid['manner']['primary_code'], 'V2')
        self.assertIn('Full delivery: V3 extended distribution.', result['spoken_summary'])
        self.assertNotIn('Full delivery: V2', result['spoken_summary'])
        self.assertEqual((self.frozen, rows), before)
        self.assertFalse(variant['entry_confirmed'])
        self.assertEqual(result['frozen_cutoff']['tradeability_at_cutoff'], 'not_assessed')

    def test_immediate_delivery_manner_does_not_borrow_future_h1_closure(self):
        rows = self.bars + later()
        change(rows, '12:15', low=60)
        early = self.follow(rows, anchor='10:00', through='12:16')
        variant = early['appendix']['variant_evidence']
        self.assertNotEqual(variant['status'], 'distribution_observed')
        self.assertEqual(variant['labels'], [])
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V1')
        self.assertIn('V1 Textbook manner', early['spoken_summary'])
        self.assertIn('completed H1 variant classification remains unverified', early['spoken_summary'])
        late = self.follow(rows, anchor='10:00')
        self.assertIn('Full delivery: V1 Textbook.', late['spoken_summary'])
        self.assertEqual(early['appendix']['terminal'], late['appendix']['terminal'])

    def test_later_resoup_changes_full_variant_never_midpoint_snapshot(self):
        rows = self.bars + later(end='15:00')
        change(rows, '12:05', low=65)
        change(rows, '13:15', high=130)
        result = self.follow(rows)
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['delivery_milestones']['midpoint']['manner']['primary_code'], 'V2')
        full = variant['delivery_milestones']['opposing_liquidity']
        self.assertEqual(full['manner']['primary_code'], 'V6')
        self.assertEqual(full['known_at_ny'], ny('13:16'))
        self.assertEqual({v['code'] for v in variant['labels']}, {'V3', 'V6'})
        self.assertIn('Full delivery: V6 re-soup.', result['spoken_summary'])
        change(rows, '14:05', low=50)
        change(rows, '14:59', low=50, close=50)
        later_result = self.follow(rows, through='15:00')
        self.assertEqual(later_result['appendix']['variant_evidence'], variant)
        self.assertEqual(later_result['appendix']['terminal'], result['appendix']['terminal'])

    def test_gap_before_touch_cannot_establish_full_delivery_or_variant(self):
        rows = self.bars + [b for b in later() if b['time'] != parse_time(ny('12:02'))]
        change(rows, '12:15', high=130)
        result = self.follow(rows)
        variant = result['appendix']['variant_evidence']
        self.assertEqual(result['appendix']['status'], 'unverified_later_evidence')
        self.assertNotEqual(variant['status'], 'distribution_observed')
        self.assertNotEqual(variant['delivery_milestones']['opposing_liquidity']['status'], 'observed')
        self.assertEqual(delivery_variant_clause(variant), 'variant unverified')
        self.assertNotIn('Full delivery:', result['spoken_summary'])

    def test_gap_after_touch_retains_manner_without_inventing_structural_close(self):
        rows = self.bars + [b for b in later() if b['time'] != parse_time(ny('12:30'))]
        change(rows, '12:15', high=130)
        result = self.follow(rows)
        variant = result['appendix']['variant_evidence']
        self.assertEqual(result['appendix']['status'], 'full_objective_delivered_after_cutoff')
        self.assertNotEqual(variant['status'], 'distribution_observed')
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V3')
        self.assertIn('V3 extended distribution manner', result['spoken_summary'])

    def test_post_invalidation_and_boundary_touches_never_get_full_variant(self):
        for hit in ('12:59', '13:15'):
            with self.subTest(hit=hit):
                rows = self.bars + later()
                change(rows, '12:59', low=60, close=60)
                change(rows, hit, high=130)
                result = self.follow(rows)
                variant = result['appendix']['variant_evidence']
                self.assertEqual(result['appendix']['terminal']['kind'], 'range_invalidated')
                self.assertNotEqual(variant['status'], 'distribution_observed')
                self.assertNotEqual(variant['delivery_milestones']['opposing_liquidity']['status'], 'observed')
                self.assertEqual(delivery_variant_clause(variant), 'variant unverified')

    def test_no_later_source_history_leaves_variant_unverified_without_erasing_delivery(self):
        rows = later()
        change(rows, '12:15', high=130)
        result = self.follow(rows)
        self.assertEqual(result['appendix']['status'], 'full_objective_delivered_after_cutoff')
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['labels'], [])
        self.assertIn('Full delivery: variant unverified.', result['spoken_summary'])
        self.assertEqual(variant['delivery_milestones']['midpoint']['manner']['primary_code'], 'V2')

    def test_future_touch_is_absent_at_cutoff_and_partial_source_close(self):
        rows = self.bars + later()
        change(rows, '12:15', high=130)
        for through in ('12:00', '12:15'):
            result = self.follow(rows, through=through)
            variant = result['appendix']['variant_evidence']
            self.assertNotEqual(variant['status'], 'distribution_observed')
            self.assertNotIn('12:16', json.dumps(variant))
            self.assertIsNone(result['appendix']['terminal'])

    def test_double_purge_uses_its_own_v6_without_borrowing_original_v2(self):
        rows = self.bars + later()
        change(rows, '12:15', high=120)
        result = self.follow(rows, anchor='08:00', phase='double_purge')
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['phase'], 'double_purge')
        self.assertEqual([v['code'] for v in variant['labels']], ['V6'])
        self.assertEqual(variant['delivery_milestones']['midpoint']['manner']['status'], 'unverified')
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V6')
        self.assertIn('Full delivery: V6 re-soup.', result['spoken_summary'])

    def test_double_purge_without_resoup_has_no_invented_timing_variant(self):
        original = synthetic()
        change(original, '10:01', low=77, close=78)
        change(original, '10:02', open=78, low=77)
        rows = original + later()
        change(rows, '12:15', high=120)
        result = self.follow(rows, anchor='08:00', phase='double_purge', before=review(original))
        variant = result['appendix']['variant_evidence']
        self.assertEqual(result['appendix']['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(variant['labels'], [])
        self.assertIn('Full delivery: variant unverified.', result['spoken_summary'])

    def test_subsequent_phase_preserves_frozen_midpoint_and_own_v6(self):
        from test_subsequent_post_shift import SubsequentPostShiftTests

        case = SubsequentPostShiftTests()
        case.setUp()
        result = case.follow()['review']
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['phase'], 'purge_3')
        self.assertEqual([v['code'] for v in variant['labels']], ['V6'])
        mid = variant['delivery_milestones']['midpoint']
        self.assertEqual(mid['manner'], result['frozen_cutoff']['objectives']['midpoint']['delivery_manner'])
        self.assertEqual(mid['manner']['status'], 'no_confirmed_resoup_at_milestone')
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V6')
        self.assertIn('Full delivery: V6 re-soup.', result['spoken_summary'])

    def test_native_conflict_does_not_create_variant_from_source_only_touch(self):
        rows = self.bars + later()
        change(rows, '12:15', high=130)
        native = [dict(time=parse_time(ny('12:00')), open=100, high=140, low=90, close=100,
            provenance={'source': 'MT5', 'timeframe': 'H1', 'method': 'copy_rates_from_pos',
                        'asset': 'NAS100', 'symbol': 'NAS100m', 'captured_at': parse_time(ny('14:00'))})]
        selected = next(c for c in continuation_candidates(self.frozen, 'NAS100', 'NAS100m')
                        if c['anchor_start_ny'] == ny('09:00'))
        result = build_followthrough(self.frozen, rows, asset='NAS100', symbol='NAS100m',
            anchor_start_ny=selected['anchor_start_ny'], phase='original',
            expected_scope_id=selected['source_scope_id'], through=parse_time(ny('14:00')),
            as_of=parse_time(ny('14:00')), native_h1=native)['review']
        variant = result['appendix']['variant_evidence']
        self.assertEqual(variant['evidence_through_ny'], ny('12:00'))
        self.assertNotEqual(variant['status'], 'distribution_observed')
        self.assertNotEqual(variant['delivery_milestones']['opposing_liquidity']['status'], 'observed')
        self.assertIsNone(result['appendix']['terminal'])

    def test_immediate_next_h1_v2_manner_before_return_confirmation(self):
        # The reusable summary helper must cover later-development consumers
        # without promoting this unqualified-at-cutoff range to a candidate.
        rows = later(start='11:00')
        for b in rows:
            b.update(open=106, high=109, low=104, close=106)
        for b in rows[:60]:
            b.update(open=100, high=110, low=90, close=100)
        change(rows, '12:01', high=112, low=111, open=111, close=111)
        change(rows, '12:02', high=111, low=89, open=111, close=89)
        end = parse_time(ny('12:03'))
        row = crt_review(rows, parse_time(ny('11:00')), end, 'H1', 60)
        attach_directional_outcome(row, rows, end, 60)
        variant = row['variant_evidence']
        self.assertEqual(variant['labels'], [])
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V2')
        self.assertEqual(variant['delivery_milestones']['opposing_liquidity']['known_at_ny'], ny('12:03'))
        self.assertIn('V2', delivery_variant_clause(variant))
        self.assertIn('manner', delivery_variant_clause(variant))
        self.assertNotIn(ny('11:00'), [c['anchor_start_ny'] for c in
            continuation_candidates(self.frozen, 'NAS100', 'NAS100m')])


if __name__ == '__main__':
    unittest.main()
