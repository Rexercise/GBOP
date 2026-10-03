"""Retained Oct 2 evidence: name each attempt without merging its direction."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.directional_evidence import (
    candidate_lifecycle_card, directional_candidate_evidence,
)


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


class DirectionalCandidateEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        bars = {item['asset']: [dict(zip(('time', 'open', 'high', 'low', 'close'), row))
                               for row in item['candles']] for item in fixture['instruments']}
        with patch.object(market, 'read_feed', side_effect=lambda db, asset, now=None: dict(
                ok=True, asset=market.asset_name(asset), symbol=market.asset_name(asset), is_live=False)), \
             patch.object(market, 'history_bars', side_effect=lambda db, feed, *args: (bars[feed['asset']], 60)):
            cls.review = market.market_tool(None, 'review_market_crt', dict(
                asset='NAS100', anchor_start_ny=ny('09:00'), anchor_timeframe='H1', through_ny=ny('12:00')))['review']

    def evidence(self, **kwargs):
        return directional_candidate_evidence(self.review, 'NAS100', **kwargs)

    def identity(self, evidence, clock):
        return next(row for row in evidence['identity_index'] if row['bar_open_ny'] == ny(clock))

    def test_scope_retains_exact_asset_range_prices_and_window(self):
        scope = self.evidence()['scope']
        self.assertEqual(scope, dict(asset='NAS100', anchor_start_ny=ny('09:00'),
            anchor_end_ny=ny('10:00'), anchor_timeframe='H1', assigned_timeframe='M5',
            high=30995.59, midpoint=30912.53, low=30829.47, through_ny=ny('12:00'),
            range_invalidated_at_ny=None))

    def test_both_early_bodies_keep_original_bearish_thesis(self):
        evidence = self.evidence()
        for opening, closing, role in [('10:00', '10:05', 'initiating_original_direction'),
                                      ('10:10', '10:15', 'same_as_original_direction')]:
            row = self.identity(evidence, opening)
            self.assertEqual((row['identity'], row['purge_type'], row['direction'], row['purged_side']),
                             ('Model 1 candle', 'body_soup', 'bearish', 'buy'))
            self.assertEqual((row['bar_close_ny'], row['identified_at_ny']), (ny(closing), ny(closing)))
            self.assertEqual(row['attempt_role'], role)
            self.assertEqual(row['source_vs_original_delivery'], 'before_delivery')
            self.assertEqual(row['formation_vs_original_delivery'], 'before_delivery')
        original = evidence['original_attempt']
        self.assertEqual((original['direction'], original['objective_side']), ('bearish', 'sell'))
        self.assertEqual(original['opposing_liquidity']['level'], 30829.47)
        self.assertEqual(original['opposing_liquidity']['source_interval']['bar_open_ny'], ny('11:12'))

    def test_1110_wick_is_separate_bullish_identity_with_same_bar_source_uncertainty(self):
        row = self.identity(self.evidence(), '11:10')
        self.assertEqual((row['identity'], row['purge_type'], row['direction'], row['purged_side']),
                         ('Turtle Wick Soup', 'wick_soup', 'bullish', 'sell'))
        self.assertEqual(row['identified_at_ny'], ny('11:15'))
        self.assertEqual(row['purge_source_interval']['bar_open_ny'], ny('11:12'))
        self.assertEqual(row['attempt_role'], 'separate_opposite_direction')
        self.assertEqual(row['source_vs_original_delivery'], 'same_source_bar_order_unknown')
        self.assertEqual(row['formation_vs_original_delivery'], 'at_or_after_delivery_source_close')

    def test_1115_body_is_separate_bullish_post_delivery_attempt(self):
        row = self.identity(self.evidence(), '11:15')
        self.assertEqual((row['identity'], row['direction'], row['purged_side']),
                         ('Model 1 candle', 'bullish', 'sell'))
        self.assertEqual(row['identified_at_ny'], ny('11:20'))
        self.assertEqual(row['purge_source_interval']['bar_open_ny'], ny('11:16'))
        self.assertEqual(row['attempt_role'], 'separate_opposite_direction')
        self.assertEqual(row['source_vs_original_delivery'], 'after_delivery')
        self.assertEqual(row['formation_vs_original_delivery'], 'at_or_after_delivery_source_close')

    def test_primary_card_explains_soup_before_csd_and_own_delivery_after_invalidation(self):
        card = self.evidence()['candidate_cards'][0]
        self.assertEqual(card['bar_open_ny'], ny('10:00'))
        csd, soup = card['csd'], card['super_soup']
        self.assertEqual((csd['candle']['bar_open_ny'], csd['confirmed_at_ny']), (ny('11:00'), ny('11:05')))
        self.assertEqual((soup['candle']['bar_open_ny'], soup['structure_known_at_ny']), (ny('10:05'), ny('10:10')))
        self.assertEqual(soup['pre_csd_status'], 'observed_before_csd')
        self.assertEqual(soup['structural_quality'], 'clean')
        self.assertEqual(soup['local_crt_outcome'], 'midpoint_delivered_then_invalidated')
        self.assertEqual(soup['local_crt_invalidated_at_ny'], ny('10:20'))
        own = soup['local_function_objectives']['opposing_liquidity']
        self.assertEqual(own['level'], 30930.59)
        self.assertEqual(own['source_interval']['bar_open_ny'], ny('10:59'))
        self.assertEqual(own['relative_to_model1_invalidation'], 'after_model1_invalidation')
        self.assertNotIn('parent_range_objectives', soup)  # Full detail retains these.

    def test_later_wicks_are_explicitly_omitted_but_all_model1s_remain_indexed(self):
        evidence = self.evidence()
        self.assertEqual(len(evidence['identity_index']), 4)
        self.assertEqual(sum(row['purge_type'] == 'body_soup' for row in evidence['identity_index']), 3)
        coverage = evidence['coverage']
        self.assertEqual(coverage['available_identity_count'], 9)
        self.assertEqual(coverage['omitted_available_identity_count'], 5)
        self.assertEqual(coverage['first_omitted_available_candle_open_ny'], ny('11:30'))
        full = self.evidence(include_later_wicks=True)
        self.assertEqual(len(full['identity_index']), 9)
        self.assertEqual(full['coverage']['omitted_available_identity_count'], 0)

    def test_focus_retains_omitted_wick_without_inventing_body_csd_or_soup(self):
        evidence = self.evidence(focus_open_ny=ny('11:35'))
        self.assertEqual(self.identity(evidence, '11:35')['purge_type'], 'wick_soup')
        self.assertEqual(evidence['candidate_cards'], [dict(bar_open_ny=ny('11:35'),
            purged_side='sell', purge_type='wick_soup', csd={'status': 'not_a_model1_body_candle'})])
        self.assertEqual(evidence['coverage']['omitted_available_identity_count'], 4)

    def test_focus_selects_opposite_body_without_relabeling_original_attempt(self):
        evidence = self.evidence(focus_open_ny=ny('11:15'))
        self.assertEqual(evidence['original_attempt']['direction'], 'bearish')
        card = evidence['candidate_cards'][0]
        self.assertEqual(card['bar_open_ny'], ny('11:15'))
        self.assertEqual(card['super_soup']['parent_function_outcome'], 'pending_at_cutoff')
        self.assertEqual(card['super_soup']['return_candle']['bar_open_ny'], ny('11:30'))
        self.assertEqual(card['super_soup']['structural_quality'], 'not_clean')
        self.assertEqual(card['csd']['confirmed_at_ny'], ny('12:00'))
        self.assertEqual(card['super_soup']['candle']['bar_open_ny'], ny('11:20'))

    def test_unavailable_focus_never_selects_a_different_identity(self):
        self.assertEqual(self.evidence(focus_open_ny=ny('10:20'))['candidate_cards'], [])
        self.assertEqual(self.evidence(focus_open_ny='invalid')['candidate_cards'], [])

    def test_pure_deterministic_and_timezone_equivalent_focus(self):
        before = deepcopy(self.review)
        first = self.evidence(focus_open_ny='2026-10-02T15:15:00+00:00')
        second = self.evidence(focus_open_ny=ny('11:15'))
        self.assertEqual(first, second)
        self.assertEqual(self.review, before)

    def test_missing_purge_source_is_unverified_not_before_or_after_delivery(self):
        review = deepcopy(self.review)
        review['candle_lifecycle']['purge_candles'][0].pop('purge_source_interval')
        row = directional_candidate_evidence(review, 'NAS100')['identity_index'][0]
        self.assertEqual(row['source_vs_original_delivery'], 'unverified_source_interval')
        self.assertEqual(row['attempt_role'], 'same_direction_attempt_order_unverified')

    def test_midpoint_only_never_makes_opposite_identity_post_full_delivery(self):
        review = deepcopy(self.review)
        review['directional_outcome']['status'] = 'midpoint_only'
        review['directional_outcome']['opposing_liquidity']['status'] = 'same_bar_order_unknown'
        row = self.identity(directional_candidate_evidence(review, 'NAS100'), '11:15')
        self.assertEqual(row['source_vs_original_delivery'], 'original_delivery_not_established')
        self.assertEqual(row['attempt_role'], 'separate_opposite_direction')

    def test_unresolved_initiating_direction_never_borrows_candidate_direction(self):
        review = deepcopy(self.review)
        review['directional_outcome']['direction'] = None
        for row in directional_candidate_evidence(review, 'NAS100')['identity_index']:
            self.assertEqual(row['attempt_role'], 'unverified_original_direction')

    def test_same_direction_after_delivery_is_a_separate_attempt(self):
        review = deepcopy(self.review)
        late = deepcopy(review['candle_lifecycle']['purge_candles'][0])
        late.update(bar_open_ny=ny('11:40'), bar_close_ny=ny('11:45'), identified_at_ny=ny('11:45'),
                    purge_source_interval={'bar_open_ny': ny('11:41'), 'bar_close_ny': ny('11:42')})
        review['candle_lifecycle']['purge_candles'] = [late]
        row = next(row for row in directional_candidate_evidence(review, 'NAS100')['identity_index']
                   if row['bar_open_ny'] == ny('11:40'))
        self.assertEqual(row['attempt_role'], 'separate_same_direction_after_delivery')

    def test_gaps_caps_and_missing_card_details_remain_explicit(self):
        review = deepcopy(self.review)
        review['range_observation_coverage']['complete'] = False
        review['range_observation_coverage']['missing_bar_count'] = 1
        lifecycle = review['candle_lifecycle']
        lifecycle.update(observation_complete=False, identified_count=40, next_identity_open_ny=ny('12:05'))
        lifecycle['purge_candles'][0]['sequence_gap_at_ny'] = ny('10:45')
        evidence = directional_candidate_evidence(review, 'NAS100')
        coverage = evidence['coverage']
        self.assertFalse(coverage['observation_complete'])
        self.assertFalse(coverage['range_observation_complete'])
        self.assertEqual(coverage['missing_bar_count'], 1)
        self.assertEqual(coverage['backend_identified_count'], 40)
        self.assertEqual(coverage['backend_remaining_from_ny'], ny('12:05'))
        self.assertEqual(evidence['candidate_cards'][0]['sequence_gap_at_ny'], ny('10:45'))
        self.assertNotIn('next_request', evidence)  # A backend cap is not a page cursor.

    def test_independent_model1_cap_never_hides_later_supplied_body_identity(self):
        review = deepcopy(self.review)
        review['candle_lifecycle']['purge_candles'] = review['candle_lifecycle']['purge_candles'][:2]
        review['candle_lifecycle']['next_identity_open_ny'] = ny('11:10')
        evidence = directional_candidate_evidence(review, 'NAS100', focus_open_ny=ny('11:15'))
        row = self.identity(evidence, '11:15')
        self.assertFalse(row['lifecycle_detail_available'])
        self.assertEqual(row['attempt_role'], 'separate_opposite_direction')
        self.assertEqual(evidence['candidate_cards'][0]['csd']['status'], 'not_assessed')
        self.assertFalse(evidence['candidate_cards'][0]['lifecycle_detail_available'])

    def test_dual_boundary_candle_keeps_both_exact_identities(self):
        review = deepcopy(self.review)
        first = deepcopy(review['candle_lifecycle']['purge_candles'][2])
        second = deepcopy(first)
        second.update(purged_side='buy', direction='bearish', purged_level=30995.59)
        for row in (first, second):
            row['both_boundaries_pierced_in_same_candle'] = True
        review['candle_lifecycle']['purge_candles'] = [first, second]
        review['model1']['candles'] = []
        evidence = directional_candidate_evidence(review, 'NAS100', max_cards=2, focus_open_ny=ny('11:10'))
        self.assertEqual(len(evidence['identity_index']), 2)
        self.assertEqual(len(evidence['candidate_cards']), 2)
        self.assertEqual({row['purged_side'] for row in evidence['identity_index']}, {'buy', 'sell'})
        self.assertTrue(all(row['both_boundaries_pierced_in_same_candle'] for row in evidence['identity_index']))

    def test_overlapping_coarse_source_bars_do_not_invent_tick_order(self):
        review = deepcopy(self.review)
        review['candle_lifecycle']['purge_candles'][2]['purge_source_interval'].update(
            bar_open_ny=ny('11:10'), bar_close_ny=ny('11:15'), precision_seconds=300)
        row = self.identity(directional_candidate_evidence(review, 'NAS100'), '11:10')
        self.assertEqual(row['source_vs_original_delivery'], 'overlapping_source_intervals_order_unknown')

    def test_wick_cannot_inherit_accidentally_attached_model1_sequel(self):
        fact = deepcopy(self.review['candle_lifecycle']['purge_candles'][0])
        fact['purge_type'] = 'wick_soup'
        card = candidate_lifecycle_card(fact)
        self.assertEqual(card['csd']['status'], 'not_a_model1_body_candle')
        self.assertNotIn('super_soup', card)

    def test_invalidation_bound_never_replaces_requested_review_cutoff(self):
        review = deepcopy(self.review)
        review['range_observation_coverage'] = deepcopy(review['range_observation_coverage'])
        review['range_observation_coverage']['end_ny'] = ny('11:00')
        review['invalidated_at_ny'] = ny('11:00')
        scope = directional_candidate_evidence(review, 'NAS100')['scope']
        self.assertEqual(scope['through_ny'], ny('12:00'))
        self.assertEqual(scope['range_observation_end_ny'], ny('11:00'))
        self.assertEqual(scope['range_invalidated_at_ny'], ny('11:00'))

    def test_card_bounds_and_small_named_payload(self):
        evidence = self.evidence(max_cards=0)
        self.assertEqual(evidence['candidate_cards'], [])
        self.assertEqual(len(self.evidence(max_cards=32)['candidate_cards']), 3)
        for bad in (-1, 33, 1.5, None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.evidence(max_cards=bad)
        default = self.evidence()
        self.assertLess(len(json.dumps(default['identity_index'], separators=(',', ':'))), 2500)
        self.assertLess(len(json.dumps(default['candidate_cards'], separators=(',', ':'))), 2100)
        self.assertNotIn('columns', json.dumps(default))


if __name__ == '__main__':
    unittest.main()
