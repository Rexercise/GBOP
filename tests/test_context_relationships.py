"""Synthetic raw-engine fixtures; no broker, provider, database or execution IO."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp
from gbop_voice_web.context_relationships import MAX_FINDINGS, summarize_relationships
from gbop_voice_web.fractal_lineage import review_fractal
from gbop_voice_web.shift_review import review_shift


START = parse_time('2026-10-02T08:00:00-04:00')
END = START + 4 * 3600


def bars():
    # 9 closes below 8. 10 purges 9; 11 subsequently delivers its high.
    values = [(100, 110, 90, 100), (95, 100, 80, 85),
              (85, 95, 82, 90), (90, 99, 85, 95)]
    result = [dict(time=START + hour*3600 + n*300, open=o, high=h, low=l, close=c)
              for hour, (o, h, l, c) in enumerate(values) for n in range(12)]
    result[24].update(open=85, high=87, low=78, close=82)
    result[25].update(open=82, high=89, low=81, close=88)
    result[37]['high'] = 101
    return result


def member(name, start=START, tf='H1', cutoff=END, review=None, asset='NAS100', symbol='USTEC'):
    return {'ok': True, 'context_id': 'ctx_' + name, 'evidence_id': 'evidence_' + name,
            'scope': {'asset': asset, 'anchor_start_ny': stamp(start),
                      'anchor_timeframe': tf, 'through_ny': stamp(cutoff)},
            'source': {'asset': asset, 'symbol': symbol, 'source_namespace': 'configured_market_bridge'},
            'review': crt_review(bars(), start, cutoff, tf, 300) if review is None else review}


def evidence(pair, kind):
    return next(row for row in pair['evidence'] if row['kind'] == kind)


class ContextRelationshipTests(unittest.TestCase):
    def pair(self, left=None, right=None):
        return summarize_relationships([left or member('left'), right or member('right', START+3600)])[0]

    def test_raw_crt_geometry_and_chronology_keep_range_references(self):
        pair = self.pair()
        self.assertEqual(pair['status'], 'observed')
        self.assertEqual(pair['context_ids'], ['ctx_left', 'ctx_right'])
        self.assertEqual(pair['evidence_ids'], ['evidence_left', 'evidence_right'])
        geometry = evidence(pair, 'price_geometry')
        self.assertEqual(geometry['status'], 'observed')
        self.assertEqual(geometry['evidence']['geometry'], 'overlap')
        self.assertEqual(geometry['evidence']['intersection'], {'low': 90, 'high': 100})
        self.assertEqual(geometry['evidence']['ranges'][1]['anchor_start_ny'], stamp(START+3600))
        self.assertEqual(evidence(pair, 'chronological_independent_ranges')['evidence']['earlier_context_id'], 'ctx_left')
        self.assertIn('broker continuity is unverified', ' '.join(pair['limits']))
        self.assertEqual(evidence(pair, 'engine_verified_lineage')['status'], 'unverified')

    def test_same_snapshot_requires_same_evidence_id_and_cutoff(self):
        left = member('left')
        right = deepcopy(left)
        right['context_id'] = 'ctx_right'
        self.assertEqual(evidence(self.pair(left, right), 'same_range_snapshot')['status'], 'observed')
        right['evidence_id'] = 'evidence_updated'
        self.assertEqual(evidence(self.pair(left, right), 'same_range_distinct_snapshots')['status'], 'observed')

    def test_geometry_contains_equal_and_touching_intervals(self):
        for low, high, expected in [(95, 105, 'containment'), (90, 110, 'equal_price_intervals'),
                                     (110, 120, 'boundary_touch')]:
            with self.subTest(expected=expected):
                right = member('right', START+3600)
                right['review']['anchor'].update(low=low, high=high)
                finding = evidence(self.pair(member('left'), right), 'price_geometry')
                self.assertEqual(finding['evidence']['geometry'], expected)

    def test_complete_disjoint_geometry_is_narrow_no_supported_relation(self):
        right = member('right', START+3600)
        right['review']['anchor'].update(low=120, high=130)
        pair = self.pair(member('left'), right)
        self.assertEqual(evidence(pair, 'price_geometry')['status'], 'no_supported_relation')
        self.assertEqual(evidence(pair, 'engine_verified_lineage')['status'], 'unverified')
        self.assertEqual(pair['status'], 'observed')  # independently known chronology

    def test_same_named_liquidity_is_exact_same_side_equality_only(self):
        left, right = member('left'), member('right', START+3600)
        right['review']['anchor'].update(low=90, high=110)
        shared = evidence(self.pair(left, right), 'same_named_liquidity_level')['evidence']
        self.assertEqual([x['liquidity_side'] for x in shared['levels']], ['buy-side', 'sell-side'])
        self.assertEqual(shared['levels'][0]['references'][1]['anchor_start_ny'], stamp(START+3600))
        self.assertIn('no common causal draw', shared['meaning'])
        right['review']['anchor'].update(low=90.0000001, high=110.0000001)
        self.assertNotIn('same_named_liquidity_level', [f['kind'] for f in self.pair(left, right)['evidence']])

    def test_cross_asset_does_not_emit_price_geometry_or_lineage(self):
        pair = self.pair(member('left'), member('right', asset='US30', symbol='DJ30'))
        self.assertEqual(pair['status'], 'no_supported_relation')
        self.assertEqual(pair['evidence'][0]['kind'], 'cross_asset_requires_paired_smt')
        self.assertNotIn('"high"', json.dumps(pair))
        self.assertIn('review_market_smt', ' '.join(pair['limits']))

    def test_source_unknown_mismatch_and_scope_asset_conflict_are_unverified(self):
        for change in ({'symbol': 'NAS100-other'}, {'symbol': None}, {'source_namespace': None},
                       {'broker_id': 'broker-A'}, {'asset': 'US30'}):
            with self.subTest(change=change):
                right = member('right', START+3600)
                right['source'].update(change)
                pair = self.pair(member('left'), right)
                self.assertEqual(pair['status'], 'unverified')
                self.assertNotIn('"high"', json.dumps(pair))

    def test_matching_reported_broker_is_scoped_but_different_brokers_are_not(self):
        left, right = member('left'), member('right', START+3600)
        left['source']['broker_id'] = right['source']['broker_id'] = 'broker-A'
        pair = self.pair(left, right)
        self.assertEqual(pair['status'], 'observed')
        self.assertNotIn('broker continuity is unverified', ' '.join(pair['limits']))
        right['source']['broker_id'] = 'broker-B'
        self.assertEqual(self.pair(left, right)['status'], 'unverified')

    def test_failed_missing_and_partial_members_are_not_absence(self):
        for change in ('failed', 'review_missing', 'partial', 'forming', 'scope_missing', 'wrong_anchor'):
            with self.subTest(change=change):
                left = member('left')
                if change == 'failed':
                    left['ok'] = False
                elif change == 'review_missing':
                    left['review'] = None
                elif change == 'scope_missing':
                    left['scope'].pop('anchor_start_ny')
                elif change == 'wrong_anchor':
                    left['review']['anchor']['start_ny'] = stamp(START-3600)
                else:
                    left['review']['anchor'].update({'complete': False} if change == 'partial' else {'forming': True})
                pair = self.pair(left, member('right', START+3600))
                self.assertEqual(pair['status'], 'unverified')
                self.assertNotIn('"high"', json.dumps(pair))

    def test_missing_observation_does_not_erase_complete_anchor_geometry(self):
        raw = crt_review(bars()[:12], START, END, 'H1', 300)
        self.assertFalse(raw['observation_coverage']['complete'])
        pair = self.pair(member('left', review=raw), member('right', START+3600))
        self.assertEqual(evidence(pair, 'price_geometry')['status'], 'observed')
        self.assertEqual(evidence(pair, 'shift_ledger_transition')['status'], 'unverified')

    def test_cutoff_mismatch_preserves_scoped_geometry_but_inhibits_events(self):
        left, right = member('left'), member('right', START+3600, cutoff=END-3600)
        pair = self.pair(left, right)
        self.assertEqual(evidence(pair, 'price_geometry')['status'], 'observed')
        finding = evidence(pair, 'event_comparison')
        self.assertEqual(finding['status'], 'unverified')
        self.assertEqual(finding['evidence']['through_ny'], [stamp(END), stamp(END-3600)])
        self.assertNotIn('shift_ledger_transition', [f['kind'] for f in pair['evidence']])

    def test_review_cutoff_mismatch_inhibits_events(self):
        left = member('left')
        left['scope']['through_ny'] = stamp(END+3600)
        right = member('right', START+3600)
        right['scope']['through_ny'] = stamp(END+3600)
        finding = evidence(self.pair(left, right), 'event_comparison')
        self.assertEqual(finding['evidence']['reason'], 'review_cutoff_does_not_match_member_scope')

    def test_shift_raw_shape_selects_exact_requested_range_and_real_ledger(self):
        story = review_shift(bars(), '2026-10-02', 'day')
        left = member('left', review={'shift_story': story})
        right = member('right', START+3600, review={'shift_story': deepcopy(story)})
        pair = self.pair(left, right)
        self.assertEqual(evidence(pair, 'price_geometry')['evidence']['ranges'][1]['low'], 80)
        transition = evidence(pair, 'shift_ledger_transition')
        self.assertEqual(transition['status'], 'observed')
        self.assertEqual(transition['evidence']['from_context_id'], 'ctx_left')
        self.assertEqual(transition['evidence']['to_context_id'], 'ctx_right')
        self.assertEqual(transition['evidence']['confirmed_at_ny'], stamp(START+7200))

    def test_shift_does_not_default_to_8_oclock_when_anchor_missing(self):
        review = {'shift_story': review_shift(bars(), '2026-10-02', 'day')}
        left = member('left', START-3600, review=review)
        self.assertEqual(self.pair(left, member('right'))['status'], 'unverified')

    def test_shift_transition_cannot_be_borrowed_by_unrelated_anchor(self):
        review = {'shift_story': review_shift(bars(), '2026-10-02', 'day')}
        pair = self.pair(member('left', review=review), member('right', START+7200, review=deepcopy(review)))
        self.assertEqual(evidence(pair, 'shift_ledger_transition')['status'], 'unverified')

    def test_transition_requires_complete_matching_ledger_and_exact_close(self):
        for corruption in ('incomplete', 'wrong_parent', 'wrong_close', 'missing_ledger'):
            with self.subTest(corruption=corruption):
                story = review_shift(bars(), '2026-10-02', 'day')
                if corruption == 'incomplete':
                    story['hourly_progression'][0]['complete'] = False
                elif corruption == 'wrong_parent':
                    story['hourly_progression'][0]['anchor_start_ny'] = stamp(START-3600)
                elif corruption == 'wrong_close':
                    story['range_transitions'][0]['close'] = 84
                else:
                    story['hourly_progression'] = []
                left = member('left', review={'shift_story': story})
                pair = self.pair(left, member('right', START+3600))
                self.assertEqual(evidence(pair, 'shift_ledger_transition')['status'], 'unverified')

    def test_shift_evidence_before_later_missing_hour_retains_verified_transition(self):
        story = review_shift(bars()[:30], '2026-10-02', 'day')
        self.assertFalse(story['progression_complete'])
        pair = self.pair(member('left', review={'shift_story': story}), member('right', START+3600))
        self.assertEqual(evidence(pair, 'shift_ledger_transition')['status'], 'observed')

    def fractal_members(self):
        rows = [dict(time=t, open=100, high=110, low=90, close=100) for t in range(START, END, 300)]
        rows[12].update(open=100, high=115, low=99, close=112)
        raw = review_fractal(rows, START, END, 'H1', 300, 'NAS100', 'USTEC', max_depth=1, page_size=1)
        self.assertEqual(len(raw['relations']), 1)
        return member('parent', review=raw), member('child', START+3600, 'M5', review=deepcopy(raw))

    def test_actual_fractal_engine_relation_is_preserved_without_identity_equivalence(self):
        left, right = self.fractal_members()
        pair = self.pair(left, right)
        fact = evidence(pair, 'engine_verified_lineage')
        self.assertEqual(fact['status'], 'observed')
        self.assertEqual(fact['evidence']['parent_context_id'], 'ctx_parent')
        self.assertEqual(fact['evidence']['child_context_id'], 'ctx_child')
        self.assertFalse(fact['evidence']['identity_equivalence_established'])
        self.assertFalse(fact['evidence']['parent_invalidation_propagates_to_child'])
        self.assertNotIn('higher_timeframe_super_soup_relation', json.dumps(pair))

    def test_nested_timeframes_without_engine_edge_do_not_imply_lineage(self):
        left, right = self.fractal_members()
        left['review']['relations'] = []
        right['review']['relations'] = []
        self.assertEqual(evidence(self.pair(left, right), 'engine_verified_lineage')['status'], 'unverified')

    def test_lineage_matching_wrong_source_or_endpoint_is_unverified(self):
        for corruption in ('source', 'parent_id', 'anchor', 'prices'):
            with self.subTest(corruption=corruption):
                left, right = self.fractal_members()
                for item in (left, right):
                    raw = item['review']
                    if corruption == 'source':
                        raw['source']['broker_symbol'] = 'OTHER'
                    elif corruption == 'parent_id':
                        raw['relations'][0]['parent_node_id'] = 'not_returned'
                    elif corruption == 'anchor':
                        raw['relations'][0]['child_node_id'] = raw['root_node_id']
                    else:
                        raw['nodes'][1]['anchor']['low'] = 10
                if corruption == 'prices':
                    # Select exact CRT evidence separately, avoiding a changed node as the selected range.
                    right['review'] = deepcopy(right['review']['nodes'][1])
                    right['review']['anchor']['low'] = 99
                pair = self.pair(left, right)
                if corruption == 'source':
                    self.assertEqual(pair['status'], 'unverified')
                    self.assertNotIn('"high"', json.dumps(pair))
                else:
                    self.assertEqual(evidence(pair, 'engine_verified_lineage')['status'], 'unverified')

    def test_fractal_cutoff_mismatch_cannot_compare_engine_events(self):
        left, right = self.fractal_members()
        right['scope']['through_ny'] = stamp(END+300)
        pair = self.pair(left, right)
        self.assertEqual(evidence(pair, 'event_comparison')['status'], 'unverified')
        self.assertNotIn('engine_verified_lineage', [f['kind'] for f in pair['evidence']])

    def test_invalid_numeric_and_temporal_anchor_evidence_never_leaks_prices(self):
        changes = ({'high': float('nan')}, {'low': float('inf')}, {'high': True},
                   {'high': 10**1000}, {'low': 500}, {'end_ny': stamp(END+3600)}, {'timeframe': 'H4'})
        for change in changes:
            with self.subTest(change=change):
                left = member('left')
                left['review']['anchor'].update(change)
                pair = self.pair(left, member('right', START+3600))
                self.assertEqual(pair['status'], 'unverified')
                self.assertNotIn('"high"', json.dumps(pair))

    def test_conflicting_duplicate_anchor_evidence_is_unverified(self):
        left = member('left')
        duplicate = deepcopy(left['review'])
        duplicate['anchor']['high'] = 1000
        left['review']['observations'] = [{'evidence': duplicate}]
        self.assertEqual(self.pair(left, member('right'))['status'], 'unverified')

    def test_same_snapshot_with_conflicting_prices_is_unverified(self):
        left = member('left')
        right = deepcopy(left)
        right['context_id'] = 'ctx_right'
        right['review']['anchor']['high'] = 120
        pair = self.pair(left, right)
        self.assertEqual(pair['status'], 'unverified')
        self.assertEqual(pair['evidence'][0]['evidence']['reason'], 'conflicting_prices_for_same_snapshot_identity')

    def test_transition_from_conflicting_parent_snapshot_is_unverified(self):
        story = review_shift(bars(), '2026-10-02', 'day')
        left = member('left')
        story['ranges'][0]['anchor']['high'] += 1
        right = member('right', START+3600, review={'shift_story': story})
        self.assertEqual(evidence(self.pair(left, right), 'shift_ledger_transition')['status'], 'unverified')

    def test_direct_shift_story_raw_shape_is_supported(self):
        story = review_shift(bars(), '2026-10-02', 'day')
        left = member('left', review=story)
        right = member('right', START+3600)
        finding = evidence(self.pair(left, right), 'shift_ledger_transition')
        self.assertEqual(finding['status'], 'observed')
        self.assertEqual(finding['evidence']['source_path'], 'review.range_transitions + hourly_progression')

    def test_equivalent_timestamp_offsets_match_exact_instants(self):
        left = member('left')
        left['scope']['anchor_start_ny'] = '2026-10-02T12:00:00+00:00'
        left['scope']['through_ny'] = '2026-10-02T16:00:00+00:00'
        self.assertEqual(self.pair(left, member('right', START+3600))['status'], 'observed')

    def test_actual_engine_edge_does_not_propagate_invalidation(self):
        left, right = self.fractal_members()
        left['review']['nodes'][0]['invalidated_at_ny'] = stamp(START+7200)
        right['review']['nodes'][0]['invalidated_at_ny'] = stamp(START+7200)
        before = deepcopy((left, right))
        fact = evidence(self.pair(left, right), 'engine_verified_lineage')
        self.assertEqual(fact['status'], 'observed')
        self.assertFalse(fact['evidence']['parent_invalidation_propagates_to_child'])
        self.assertEqual((left, right), before)

    def test_endpoint_source_mismatch_cannot_borrow_other_nodes_lineage(self):
        left, right = self.fractal_members()
        # The child member has clean CRT evidence, but the supplied engine edge
        # points to a child node whose source no longer matches its envelope.
        right['review'] = deepcopy(right['review']['nodes'][1])
        left['review']['nodes'][1]['source']['broker_symbol'] = 'OTHER'
        pair = self.pair(left, right)
        self.assertEqual(evidence(pair, 'engine_verified_lineage')['status'], 'unverified')

    def test_read_only_deterministic_and_bounded(self):
        members = [member(str(i), START+i*3600) for i in range(4)]
        original = deepcopy(members)
        result = summarize_relationships(members)
        self.assertEqual(len(result), 6)
        self.assertTrue(all(len(row['evidence']) <= MAX_FINDINGS for row in result))
        self.assertEqual(result, summarize_relationships(members))
        self.assertEqual(members, original)
        self.assertLess(len(json.dumps(result)), 30000)
        self.assertEqual(summarize_relationships([]), [])
        self.assertEqual(summarize_relationships([members[0]]), [])
        for invalid in (members + [members[0]], {}, [None]):
            with self.assertRaises(ValueError):
                summarize_relationships(invalid)


if __name__ == '__main__':
    unittest.main()
