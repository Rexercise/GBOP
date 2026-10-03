"""Retained Oct 2 bars: preserve each directional thesis and its own outcome."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_context import enrich_smt
from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.smt_reference import reconcile_paired_recap
from gbop_voice_web.shift_narrative import directional_outcome
from test_smt_reference import fixture, START, END, bearish


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


class DirectionalDeliveryRegressionTests(unittest.TestCase):
    def setUp(self):
        fixture_path = Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json'
        retained = json.loads(fixture_path.read_text())
        self.bars = {item['asset']: [dict(zip(('time','open','high','low','close'), row))
                                   for row in item['candles']] for item in retained['instruments']}
        p = patch.object(market, 'read_feed', side_effect=lambda db, asset: dict(
            ok=True, asset=market.asset_name(asset), symbol=market.asset_name(asset), is_live=False))
        p.start(); self.addCleanup(p.stop)
        p = patch.object(market, 'history_bars', side_effect=lambda db, feed, *args: (self.bars[feed['asset']], 60))
        p.start(); self.addCleanup(p.stop)

    def review(self, anchor='07:00', through='12:00'):
        result = market.market_tool(None, 'review_market_crt', dict(asset='gold',
            anchor_start_ny=ny(anchor), anchor_timeframe='H1', through_ny=ny(through)))
        self.assertTrue(result['ok'], result)
        return result['review']

    def test_gold_seven_delivered_v2_is_not_erased_by_later_h1_close(self):
        review = self.review()
        out = review['directional_outcome']
        self.assertEqual((out['direction'], out['status']), ('bullish', 'opposing_liquidity_delivered'))
        self.assertEqual(out['play_context'], 'Young Lefty')
        origin = out['initiating_identity']
        self.assertEqual((origin['identity'], origin['bar_open_ny'], origin['qualification']),
                         ('Turtle Wick Soup', ny('08:10'), 'wick_only'))
        self.assertEqual(origin['source_purge']['bar_open_ny'], ny('08:11'))
        self.assertEqual(out['first_source_return_inside']['bar_close_ny'], ny('08:12'))
        self.assertEqual(out['midpoint']['evidence']['bar_open_ny'], ny('08:19'))
        self.assertEqual(out['opposing_liquidity']['evidence']['bar_open_ny'], ny('08:30'))
        self.assertEqual(out['range_invalidated_at_ny'], ny('09:00'))
        self.assertTrue(out['delivery_before_later_invalidation'])
        variants = review['variant_evidence']
        self.assertEqual(variants['status'], 'distribution_observed')
        self.assertEqual([v['code'] for v in variants['labels']], ['V2'])
        self.assertTrue(variants['manipulation_closed_outside'])
        self.assertEqual(out['separate_opposite_identities'][0], dict(
            identity='Model 1 candle', bar_open_ny=ny('08:30'), timeframe='M5', direction='bearish'))
        wick = review['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(wick['csd']['status'], 'not_a_model1_body_candle')
        self.assertNotIn('super_soup', wick)
        self.assertTrue(review['recap']['headline'].startswith('The 7:00 AM H1 range (Young Lefty) completed its bullish buy-side objective'))
        self.assertLess(review['recap']['headline'].index('completed'), review['recap']['headline'].index('invalidated'))

    def test_intrahour_custom_anchor_is_not_a_named_clock_play(self):
        for clock in ('07:30', '08:30'):
            with self.subTest(anchor=clock):
                review = self.review(clock)
                self.assertIsNone(review['directional_outcome']['play_context'])
                self.assertNotIn('Young Lefty', review['directional_outcome']['spoken_summary'])
                self.assertNotIn('9ate8', review['directional_outcome']['spoken_summary'])
                self.assertEqual(review['variant_evidence']['labels'], [])
                self.assertIn('not assessed', review['variant_evidence']['reason'])
                self.assertEqual(review['directional_outcome']['range_start_ny'], ny(clock))
                for event in review['paired_smt'].get('events', []):
                    self.assertNotEqual(event['play_context'], '9ate8')
                self.assertNotIn('9ate8', review['recap']['spoken_summary'])

    def test_retained_eleven_anchor_at_noon_cutoff_remains_reviewable(self):
        for asset in ('NAS100', 'XAUUSD'):
            with self.subTest(asset=asset):
                result = market.market_tool(None, 'review_market_crt', dict(asset=asset,
                    anchor_start_ny=ny('11:00'), anchor_timeframe='H1', through_ny=ny('12:00')))
                self.assertTrue(result['ok'], result)
                review = result['review']
                self.assertTrue(review['anchor']['complete'])
                self.assertEqual(review['events'], [])
                self.assertEqual(review['model1']['candles'], [])
                self.assertEqual(review['directional_outcome']['status'], 'unverified')
                self.assertEqual(review['paired_smt']['status'], 'no_subsequent_evidence_window')
                self.assertEqual(review['paired_smt']['assessment_status'], 'not_assessed')
                self.assertEqual(review['paired_smt']['events'], [])
                self.assertFalse(review['paired_smt']['divergence_confirmed'])
                self.assertNotIn('failed', review['recap']['headline'])

    def test_midpoint_only_and_full_delivery_have_separate_closed_bar_cutoffs(self):
        before_mid = self.review(through='08:19')['directional_outcome']
        self.assertNotEqual(before_mid['status'], 'midpoint_only')
        mid = self.review(through='08:20')['directional_outcome']
        self.assertEqual(mid['status'], 'midpoint_only')
        self.assertNotEqual(mid['opposing_liquidity']['status'], 'observed_after_purge')
        before_full = self.review(through='08:30')['directional_outcome']
        self.assertEqual(before_full['status'], 'midpoint_only')
        full = self.review(through='08:31')['directional_outcome']
        self.assertEqual(full['status'], 'opposing_liquidity_delivered')
        self.assertIsNone(full['range_invalidated_at_ny'])

    def test_gold_eight_paired_bearish_delivery_and_local_bullish_failure_are_distinct(self):
        review = self.review('08:00')
        local = review['directional_outcome']
        self.assertEqual((local['direction'], local['status']), ('bullish', 'failed_before_objectives'))
        self.assertEqual(local['initiating_identity']['source_purge']['bar_open_ny'], ny('10:28'))
        pair = next(r for r in review['recap']['paired_interpretation'] if r['direction'] == 'bearish')
        self.assertEqual(pair['asset_role'], 'boneless leg')
        self.assertEqual(pair['boneless_status'], 'completed_delivery')
        self.assertEqual(pair['objective_status']['opposing_liquidity']['touch_bar_open_ny'], ny('10:28'))
        headline = review['recap']['headline']
        self.assertTrue(headline.startswith("The 8:00 AM H1 range (9ate8) delivered XAUUSD's bearish sell-side objective"))
        self.assertLess(headline.index('delivered'), headline.index('boneless'))
        self.assertIn('bullish', review['recap']['local_only_spoken_summary'])
        self.assertNotIn('boneless failed', review['recap']['spoken_summary'])

    def test_nonpurging_gold_can_be_potential_then_pending_then_complete(self):
        for cutoff, expected, full_status in [('09:03', 'potential_setup', 'pending_at_review_cutoff'),
                                               ('09:13', 'potential_setup', 'pending_at_review_cutoff'),
                                               ('10:00', 'delivery_pending', 'pending_at_review_cutoff'),
                                               ('10:29', 'completed_delivery', 'objective_complete_while_range_valid')]:
            with self.subTest(cutoff=cutoff):
                review = self.review('08:00', cutoff)
                event = next(e for e in review['paired_smt']['events'] if e['direction'] == 'bearish')
                self.assertEqual(event['boneless_status'], expected)
                self.assertEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'], full_status)
                self.assertNotEqual(event['paired_model1']['status'], 'identified')  # Partner is wick-only.
                self.assertNotEqual(event['paired_model1']['partner_csd']['status'], 'confirmed')
                record = next(r for r in review['recap']['paired_interpretation'] if r['direction'] == 'bearish')
                if expected == 'potential_setup':
                    self.assertFalse(event['setup_interval']['qualified_smt'])
                    self.assertTrue(event['setup_interval']['potential_smt'])
                    self.assertEqual(event['potential_boneless_asset'], 'XAUUSD')
                    self.assertEqual(record['asset_role'], 'potential boneless leg')
                self.assertNotIn('boneless failed', record['spoken_summary'])

    def test_same_hour_dual_purges_forbid_all_boneless_labels(self):
        gold = next(b for b in self.bars['XAUUSD'] if b['time'] == parse_time(ny('09:45')))
        gold['high'] = 4228  # Explicit synthetic fault injection into retained data.
        review = self.review('08:00')
        event = next(e for e in review['paired_smt']['events'] if e['direction'] == 'bearish')
        self.assertEqual(event['setup_interval']['status'], 'both_assets_purged_same_setup_interval')
        self.assertFalse(event['setup_interval']['potential_smt'])
        self.assertIsNone(event['boneless_asset'])
        self.assertIsNone(event['potential_boneless_asset'])
        self.assertEqual(event['boneless_status'], 'disqualified')
        self.assertFalse(any(r['direction'] == 'bearish' for r in review['recap'].get('paired_interpretation', [])))

    def test_later_catchup_does_not_claim_nonpurge_through_end_of_review(self):
        gold = next(b for b in self.bars['XAUUSD'] if b['time'] == parse_time(ny('10:05')))
        gold['high'] = 4228
        review = self.review('08:00')
        event = next(e for e in review['paired_smt']['events'] if e['direction'] == 'bearish')
        self.assertTrue(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['setup_interval']['observed_through_ny'], ny('10:00'))
        self.assertNotIn('matching level through ' + ny('12:00'), review['paired_smt']['spoken_summary'])

    def test_purging_asset_is_never_the_boneless_leg(self):
        review = self.review('08:00')
        paired = review['paired_smt']
        silver = {'paired_smt': paired, 'recap': {'spoken_summary': ''}}
        reconcile_paired_recap(silver, 'XAGUSD')
        bearish_record = next(r for r in silver['recap']['paired_interpretation'] if r['direction'] == 'bearish')
        self.assertEqual(bearish_record['asset_role'], 'visible-purge leg')

    def test_missing_paired_minute_keeps_only_provisional_observed_nonpurge(self):
        self.bars['XAUUSD'] = [b for b in self.bars['XAUUSD'] if b['time'] != parse_time(ny('09:04'))]
        review = self.review('08:00')
        event = next(e for e in review['paired_smt']['events'] if e['direction'] == 'bearish')
        self.assertTrue(event['setup_interval']['potential_smt'])
        self.assertFalse(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['setup_interval']['observed_through_ny'], ny('09:04'))
        self.assertEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'],
                         'unverified_incomplete_paired_coverage')
        self.assertNotEqual(event['paired_model1']['status'], 'identified')
        self.assertIn('qualification remains unverified', review['recap']['headline'])

    def test_target_in_invalidating_source_bar_is_unresolved_not_prior_delivery(self):
        review = self.review()
        # Explicit counterfactual: force both objectives into the invalidating
        # source minute; no intrabar touch/close ordering is then established.
        objectives = []
        for name in ('midpoint', 'opposing_liquidity'):
            fact = dict(review['directional_outcome'][name])
            fact['evidence'] = {**fact['evidence'], 'bar_open_ny': ny('08:59'), 'bar_close_ny': ny('09:00')}
            objectives.append(fact)
        review['objectives'] = objectives
        self.assertEqual(directional_outcome(review)['status'], 'unverified')


class PartnerConfirmationRegressionTests(unittest.TestCase):
    def test_qualified_intrahour_pair_is_not_nineate8(self):
        pair = fixture()
        for market_data in pair:
            for candle in market_data['bars']:
                candle['time'] += 1800
        paired = enrich_smt(compare_ranges(*pair, START+1800, START+5400, END+1800))
        event = bearish(paired)
        self.assertTrue(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['play_context'], 'selected_range_SMT')
        review = {'paired_smt': paired, 'recap': {'spoken_summary': ''}}
        reconcile_paired_recap(review, 'XAUUSD')
        self.assertNotIn('9ate8', review['recap']['headline'])

    def test_actual_partner_model1_and_csd_are_distinct_from_purge_only(self):
        pair = fixture()
        event = bearish(enrich_smt(compare_ranges(*pair, START, START+3600, END)))
        identity = event['paired_model1']
        self.assertEqual(identity['status'], 'identified')
        self.assertEqual(identity['partner_csd']['status'], 'confirmed')
        self.assertEqual(identity['thesis_support_status'], 'partner_model1_and_csd_confirmed')
        self.assertEqual(identity['boneless_reference']['csd_status'], 'not_assessed')
        pair[0]['bars'][15]['close'] = 99  # Wick-only partner never becomes Model 1/CSD.
        event = bearish(enrich_smt(compare_ranges(*pair, START, START+3600, END)))
        self.assertNotEqual(event['paired_model1']['status'], 'identified')
        self.assertNotEqual(event['paired_model1']['partner_csd']['status'], 'confirmed')
        self.assertEqual(event['paired_model1']['thesis_support_status'], 'purge_only_csd_unverified')


if __name__ == '__main__':
    unittest.main()
