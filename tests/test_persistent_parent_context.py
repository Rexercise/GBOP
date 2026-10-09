"""Synthetic parent validity and event-time overlap, without market fixtures."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.chronological_context import (
    build_context_graph, compact_context_graph, context_sentence,
)
from gbop_voice_web.market_data import session_review


DAY = '2026-09-17'


def ny(clock):
    return DAY + 'T' + clock + ':00-04:00'


def parent(opening='07:00', established='09:00', completed='09:15', invalid=None):
    """A confirmed H1 parent and an explicitly ordered original objective."""
    full = {'objective': 'opposing_liquidity', 'level': 90,
            'status': 'observed_after_purge' if completed else 'not_observed_by_review_cutoff'}
    if completed:
        full['evidence'] = {'bar_close_ny': ny(completed)}
    return {'anchor': {'start_ny': ny(opening), 'timeframe': 'H1', 'complete': True},
            'label': 'Young Lefty' if opening == '07:00' else '9ate8',
            'direction_observed': 'bearish', 'invalidated_at_ny': ny(invalid) if invalid else None,
            'context_qualification': {'selected_tf_return_confirmed': True,
                'selected_tf_return_known_at_ny': ny(established),
                'evidence_through_ny': ny(invalid or '12:00'),
                'double_purge_evidence_through_ny': ny(invalid or '12:00')},
            'objectives': [full, {'objective': 'midpoint', 'status': 'observed_after_purge'}]}


def add_double(row, established='10:00', completed=None):
    full = {'level': 110, 'status': 'observed_after_confirmation' if completed else
            'not_observed_by_review_cutoff'}
    if completed:
        full['evidence'] = {'bar_close_ny': ny(completed)}
    row['double_purge'] = {'observed': True, 'confirmation_status': 'confirmed',
        'confirmed_at_ny': ny(established), 'reversal_thesis': {'direction': 'bullish',
            'objectives': {'original_side': full,
                           'midpoint': {'status': 'observed_after_confirmation'}}}}
    return row


def graph_for(*rows, young=None, cutoff='12:00'):
    return build_context_graph({'end_ny': ny(cutoff), 'ranges': list(rows)}, young)


def candles():
    """Ordered buy/sell purges, completed reversal, then continued inside closes."""
    start = parse_time(ny('07:00'))
    rows = [dict(time=start+i*60, open=100, high=105, low=95, close=100) for i in range(300)]
    rows[0].update(high=110, low=90)
    for minute, values in {
        65: dict(high=112, close=109),
        85: dict(low=88, close=94),
        125: dict(high=113, close=105),
        145: dict(low=87, close=95),
        185: dict(low=89, close=95),
    }.items():
        rows[minute].update(values)
    return rows


class PersistentParentContextTests(unittest.TestCase):
    def test_full_delivery_retires_objective_but_keeps_parent(self):
        graph = graph_for(parent())
        context = graph['contexts'][0]
        self.assertEqual(context['status'], 'completed')
        self.assertEqual(context['retired_at_ny'], ny('09:15'))
        self.assertEqual(context['retirement_reason'], 'full_DOL_delivered')
        self.assertEqual(context['parent_status'], 'intact_in_observed_closes')
        self.assertIsNone(context['parent_invalidated_at_ny'])
        self.assertEqual(graph['at_shift_end']['active_context_ids'], [])
        self.assertEqual(graph['at_shift_end']['parent_context_ids'], [context['context_id']])
        self.assertNotIn('Pending full DOL', context_sentence(graph))

    def test_completed_and_reversal_phases_coexist_without_double_counting_parent(self):
        graph = graph_for(add_double(parent()))
        overlap = graph['simultaneous_contexts'][0]
        self.assertEqual(overlap['known_at_ny'], ny('10:00'))
        self.assertEqual(set(overlap['context_ids']), {ny('07:00')+'/original', ny('07:00')+'/double_purge'})
        self.assertEqual(overlap['completed_context_ids'], [ny('07:00')+'/original'])
        self.assertEqual(overlap['distinct_ranges'], 1)
        self.assertEqual(graph['at_shift_end']['parent_distinct_ranges'], 1)
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 1)
        self.assertIn('simultaneous', context_sentence(graph))
        self.assertIn('objective completed', context_sentence(graph))

    def test_completed_parent_coexists_with_later_independent_parent(self):
        graph = graph_for(parent(), parent('08:00', '10:00', completed=None))
        overlap = graph['simultaneous_contexts'][0]
        self.assertEqual(overlap['distinct_ranges'], 2)
        self.assertEqual(graph['at_shift_end']['parent_distinct_ranges'], 2)
        self.assertEqual(graph['at_shift_end']['distinct_ranges'], 1)
        self.assertEqual(graph['relationships'], [])  # completed DOL is not another pending vote

    def test_own_timeframe_invalidation_ends_parent_but_preserves_earlier_overlap(self):
        graph = graph_for(add_double(parent(invalid='11:00')),
                          parent('08:00', '11:00', completed=None))
        original = next(c for c in graph['contexts'] if c['phase'] == 'original' and c['range_id'] == ny('07:00'))
        self.assertEqual(original['status'], 'completed')
        self.assertEqual(original['parent_status'], 'invalidated')
        self.assertEqual(original['parent_invalidated_at_ny'], ny('11:00'))
        self.assertNotIn(original['context_id'], graph['at_shift_end']['parent_context_ids'])
        self.assertEqual([r['known_at_ny'] for r in graph['simultaneous_contexts']], [ny('10:00')])

    def test_later_coverage_gap_does_not_erase_earlier_overlap(self):
        row = add_double(parent())
        row['context_qualification'].update(evidence_through_ny=ny('10:30'),
            double_purge_evidence_through_ny=ny('10:30'))
        graph = graph_for(row, parent('08:00', '11:00', completed=None))
        self.assertEqual([r['known_at_ny'] for r in graph['simultaneous_contexts']], [ny('10:00')])
        self.assertEqual(graph['at_shift_end']['parent_context_ids'], [ny('08:00')+'/original'])
        self.assertEqual(graph['contexts'][0]['parent_status'], 'unverified')

    def test_unknown_objective_order_does_not_gain_confirmed_overlap(self):
        row = parent()
        row['objectives'][0]['status'] = 'same_bar_order_unknown'
        graph = graph_for(row, parent('08:00', '10:00', completed=None))
        self.assertEqual(graph['simultaneous_contexts'], [])
        self.assertNotIn(ny('07:00')+'/original', graph['at_shift_end']['parent_context_ids'])

    def test_later_completion_does_not_leak_into_earlier_overlap(self):
        graph = graph_for(parent(completed='11:15'), parent('08:00', '10:00', completed=None))
        self.assertEqual(graph['simultaneous_contexts'][0]['completed_context_ids'], [])
        completed = next(r for r in graph['simultaneous_contexts'] if r['known_at_ny'] == ny('11:15'))
        self.assertEqual(completed['completed_context_ids'], [ny('07:00')+'/original'])
        self.assertIn('objective completed', context_sentence(graph))

    def test_context_dependent_young_needs_confirmed_delivery_recap(self):
        young = add_double(parent())
        young['young_lefty_context'] = {'selected_direction': None}
        self.assertEqual(graph_for(young=young)['contexts'], [])
        young['young_lefty_context']['delivery_recap'] = {'scope': 'observed_price_path_not_selected_thesis'}
        before = deepcopy(young)
        graph = graph_for(young=young)
        self.assertEqual(len(graph['contexts']), 2)
        self.assertTrue(all(c['direction_scope'] == 'observed_price_path_not_selected_thesis'
                            for c in graph['contexts']))
        self.assertIsNone(young['young_lefty_context']['selected_direction'])
        self.assertEqual(young, before)
        young['double_purge']['confirmation_status'] = 'developing'
        self.assertEqual(graph_for(young=young)['contexts'], [])

    def test_compact_view_keeps_completed_parent_and_resolvable_overlap_ids(self):
        graph = graph_for(add_double(parent()), parent('08:00', '11:00', completed=None))
        before = deepcopy(graph)
        compact = compact_context_graph(graph)
        ids = {c['id'] for c in compact['contexts']}
        overlap = compact['simultaneous_contexts'][0]
        self.assertTrue(set(overlap['context_ids']) <= ids)
        self.assertTrue(set(overlap['completed_context_ids']) <= ids)
        completed = next(c for c in compact['contexts'] if c['status'] == 'completed')
        self.assertIn(completed['id'], compact['at_shift_end']['parent_context_ids'])
        self.assertNotIn(completed['id'], compact['at_shift_end']['active_context_ids'])
        self.assertEqual(graph, before)

    def test_ordered_synthetic_delivery_keeps_young_parent_and_handoff_separate(self):
        result = session_review(candles(), DAY, 'day', 60)
        young = next(o['evidence'] for o in result['observations'] if o['play'] == 'Young Lefty')
        self.assertTrue(young['double_purge']['observed'])
        self.assertTrue(young['young_lefty_context']['delivery_recap'])
        graph = build_context_graph(result['shift_story'], young)
        original = next(c for c in graph['contexts'] if c['context_id'] == ny('07:00')+'/original')
        self.assertEqual(original['status'], 'completed')
        self.assertEqual(original['parent_status'], 'intact_in_observed_closes')
        self.assertIn(original['context_id'], graph['at_shift_end']['parent_context_ids'])
        self.assertIsNone(young['young_lefty_context']['selected_direction'])
        transition = result['shift_story']['range_transitions'][0]
        self.assertEqual(transition['reason'], 'opposing_objective_completed')
        self.assertEqual(transition['to_anchor_ny'], ny('09:00'))
        self.assertFalse(transition['crt_established_by_handoff'])
        eight = next(c for c in graph['contexts'] if c['context_id'] == ny('08:00')+'/original')
        self.assertEqual(eight['status'], 'completed')
        self.assertEqual(eight['parent_status'], 'intact_in_observed_closes')

    def test_synthetic_outside_parent_close_ends_validity_not_historical_completion(self):
        rows = candles()
        rows[-1].update(high=113, close=112)
        result = session_review(rows, DAY, 'day', 60)
        young = next(o['evidence'] for o in result['observations'] if o['play'] == 'Young Lefty')
        graph = build_context_graph(result['shift_story'], young)
        original = next(c for c in graph['contexts'] if c['context_id'] == ny('07:00')+'/original')
        self.assertEqual(original['status'], 'completed')
        self.assertEqual(original['parent_invalidated_at_ny'], ny('12:00'))
        self.assertNotIn(original['context_id'], graph['at_shift_end']['parent_context_ids'])
        self.assertTrue(any(original['context_id'] in r['context_ids'] for r in graph['simultaneous_contexts']))

    def test_confirmed_later_phases_use_post_double_coverage_after_native_recovery(self):
        from test_native_anchor_resolution import native
        from test_young_lefty_delivery import sequence, START
        from gbop_voice_web.candle_evidence import crt_review
        from gbop_voice_web.market_data import attach_lifecycle
        from gbop_voice_web.chronological_context import attach_qualification, attach_phase_coverage
        rows = sequence()
        higher = native(rows, START + 3600)
        # The original 08:00 H1 confirmation is authoritative native OHLC;
        # a missing 08:59 source minute cannot erase fully covered later legs.
        rows = [bar for bar in rows if bar['time'] != START + 119*60]
        for minutes, expected in ((210, 'active_full_DOL_pending'), (270, 'completed')):
            with self.subTest(minutes=minutes):
                end = START + minutes*60
                row = attach_lifecycle(crt_review(rows, START, end, 'H1', 60,
                    native_h1=[higher]), rows, end, 60)
                attach_qualification(row, rows, end, 60, [higher])
                attach_phase_coverage(row, rows, end, 60)
                graph = build_context_graph({'end_ny': row['double_purge']['review_cutoff_ny'], 'ranges': []}, row)
                third = next(c for c in graph['contexts'] if c['phase'] == 'purge_3')
                original = next(c for c in graph['contexts'] if c['phase'] == 'original')
                self.assertEqual(original['evidence_through_ny'], '2026-06-11T08:59:00-04:00')
                self.assertEqual(parse_time(third['evidence_through_ny']), end)
                self.assertEqual(third['status'], expected)
                self.assertIn(third['context_id'], graph['at_shift_end']['parent_context_ids'])
                self.assertIn('triple purge', third['name'])
                compact = compact_context_graph(graph)
                self.assertTrue(any(c['phase'] == 'purge_3' for c in compact['contexts']))
                if expected == 'active_full_DOL_pending':
                    self.assertEqual(graph['at_shift_end']['active_context_ids'], [third['context_id']])
                    self.assertIn('7:00 AM triple purge bearish sell-side', context_sentence(graph))
                else:
                    self.assertNotIn(third['context_id'], graph['at_shift_end']['active_context_ids'])

    def test_fourth_phase_stays_completed_without_extra_parent_votes_or_lookahead(self):
        from test_young_lefty_delivery import sequence, START
        from gbop_voice_web.candle_evidence import crt_review, stamp
        from gbop_voice_web.market_data import attach_lifecycle
        from gbop_voice_web.chronological_context import attach_qualification, attach_phase_coverage
        for offset in (1, 2, 13, 14):
            with self.subTest(anchor_hour=7 + offset):
                start = START + offset*3600
                rows = sequence(start)
                rows[-1].update(high=100, close=94)  # keep this parent inside for the fourth confirmation
                rows.extend(dict(time=start+i*60, open=100, high=111 if i == 305 else 105,
                                 low=95, close=100) for i in range(300, 330))
                end = start + 330*60
                row = attach_lifecycle(crt_review(rows, start, end, 'H1', 60), rows, end, 60)
                attach_qualification(row, rows, end, 60)
                attach_phase_coverage(row, rows, end, 60)
                graph = build_context_graph({'end_ny': stamp(end), 'ranges': [row]})
                self.assertEqual([(leg['leg_index'], leg['status'])
                    for leg in row['double_purge']['continuation']['legs']],
                    [(3, 'opposing_liquidity_delivered'), (4, 'opposing_liquidity_delivered')])
                self.assertEqual(graph['at_shift_end']['active_context_ids'], [])
                self.assertEqual(graph['at_shift_end']['parent_distinct_ranges'], 1)
                self.assertEqual(len(graph['at_shift_end']['parent_context_ids']), 4)
                compact = compact_context_graph(graph)
                fourth = next(c for c in compact['contexts'] if c['phase'] == 'purge_4')
                self.assertEqual(fourth['status'], 'completed')
                self.assertIn('purge 4', fourth['name'])
                bounded_end = start + 300*60
                bounded = attach_lifecycle(crt_review(rows, start, bounded_end, 'H1', 60),
                                           rows, bounded_end, 60)
                self.assertEqual(bounded['double_purge']['continuation']['legs'][-1]['status'],
                                 'pending_at_review_cutoff')


if __name__ == '__main__':
    unittest.main()
