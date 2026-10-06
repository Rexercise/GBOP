"""Short default presentation on retained evidence, with independent seven scope.

No paid/model requests. Synthetic bars below only exercise edge cases absent from
retained history; gold/NAS/crypto assertions use untouched broker replay fixtures.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import session_review, MARKET_PROMPT, LIVE_MARKET_PROMPT
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
import test_retained_market_replays as retained
import test_crypto_shift_replays as crypto


def synthetic(hours):
    start = parse_time('2026-10-02T07:00:00-04:00')
    return [dict(time=start + i * 3600 + n * 300, open=o, high=h, low=l, close=c)
            for i, (o, h, l, c) in enumerate(hours) for n in range(12)]


def young_rows(synopsis):
    return [r for r in synopsis['ranges'] if r.get('play') == 'Young Lefty']


class ShiftSynopsisTests(unittest.TestCase):
    def replay(self, cls):
        replay = cls()
        replay.setUp()
        self.addCleanup(replay.doCleanups)
        return replay

    def synopsis(self, bars):
        review = session_review(bars, '2026-10-02', 'day')
        before = deepcopy(review)
        result = build_shift_synopsis(review, 'NAS100')
        self.assertEqual(review, before)
        return result

    def test_real_gold_leads_boneless_delivery_then_opposed_early_young_lefty(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        source = replay.tool('XAUUSD')
        synopsis = source['review']['shift_synopsis']
        lead, young = synopsis['ranges'][:2]
        self.assertEqual((lead['play'], lead['direction'], lead['verdict']), ('9ate8', 'bearish', 'boneless_delivered'))
        self.assertEqual(lead['opposing_liquidity']['level'], 4174.379)
        self.assertEqual(lead['opposing_liquidity']['source_interval']['bar_open_ny'], retained.ny('10:28'))
        self.assertEqual((young['direction'], young['verdict']), ('bullish', 'delivered'))
        self.assertEqual(young['anchor_start_ny'], retained.ny('07:00'))
        self.assertEqual(young['anchor_timeframe'], 'H1')
        self.assertEqual(young['opposing_liquidity']['source_interval']['bar_open_ny'], retained.ny('08:30'))
        self.assertTrue(young['opposes_9ate8'])
        self.assertEqual([v['code'] for v in young['variant']['labels']], ['V2'])
        self.assertNotEqual(young['opposing_liquidity']['level'], lead['opposing_liquidity']['level'])
        text = synopsis['spoken_summary']
        self.assertTrue(text.startswith('9ate8'))
        self.assertIn('opposite 9ate8', text)
        self.assertNotIn('Model 1', text)
        # Required chronological context remains bounded even without a body Model 1.
        self.assertLess(len(text.split()), 240)

    def test_real_nas_failure_keeps_later_named_v1_delivery(self):
        synopsis = self.replay(retained.RetainedMarketReplayTests).tool('NAS100')['review']['shift_synopsis']
        self.assertEqual(synopsis['ranges'][0]['verdict'], 'failed')
        first = synopsis['spoken_summary'].split('.')[0]
        self.assertIn('failed bearish', first)
        self.assertIn('BUT induced 50%: 7.76 points short (~94% of the high-to-50% path)', synopsis['spoken_summary'])
        self.assertNotIn('inducement in GTOP terms', synopsis['spoken_summary'])
        self.assertEqual(synopsis['young_lefty_status'], 'failed')
        self.assertEqual(young_rows(synopsis)[0]['verdict'], 'failed')
        self.assertLess(synopsis['spoken_summary'].index('Young Lefty'),
                        synopsis['spoken_summary'].index('the 9:00 AM H1 became the next range under review'))
        self.assertEqual(synopsis['ranges'][0]['midpoint_approach']['gtop_context']['basis'],
                         'explicit_owner_characterization')
        later = next(r for r in synopsis['ranges'] if r['anchor_start_ny'] == retained.ny('09:00'))
        self.assertEqual((later['direction'], later['outcome']), ('bearish', 'opposing_liquidity_delivered'))
        self.assertEqual([v['code'] for v in later['variant']['labels']], ['V1'])
        self.assertLess(synopsis['spoken_summary'].find('9ate8'), synopsis['spoken_summary'].find('the 9:00 AM H1 became the next range under review'))
        self.assertIn('9:00 AM bullish double-purge reversal, primary body Model 1 is the 11:15 AM M5 candle', synopsis['spoken_summary'])
        self.assertEqual(later['pending_reversal']['primary_body_model1']['bar_open_ny'], retained.ny('11:15'))

    def test_retained_crypto_keeps_local_bones_and_earlier_opposed_young_lefty(self):
        replay = self.replay(crypto.CryptoShiftReplayTests)
        for asset in ('BTCUSD', 'ETHUSD'):
            with self.subTest(asset=asset):
                synopsis = replay.tool(asset)['review']['shift_synopsis']
                lead = synopsis['ranges'][0]
                self.assertFalse(lead['verdict'].startswith('boneless'))
                self.assertEqual(lead['anchor_start_ny'], crypto.ny('20:00'))
                young = young_rows(synopsis)[0]
                self.assertTrue(young['opposes_9ate8'])
                self.assertEqual(young['anchor_start_ny'], crypto.ny('19:00'))
                self.assertIn('PM', synopsis['spoken_summary'])
        btc = replay.tool('BTCUSD')['review']['shift_synopsis']['ranges'][0]
        self.assertEqual(btc['outcome'], 'midpoint_only')
        self.assertEqual(btc['verdict'], 'failed')

    def test_absent_young_lefty_status_is_explicit_and_exact_seven_detail_recoverable(self):
        data = synthetic([(100, 110, 90, 100)] + [(100, 105, 95, 100)] * 4)
        synopsis = self.synopsis(data)
        self.assertTrue(synopsis['young_lefty_evaluated'])
        self.assertFalse(synopsis['young_lefty_relevant'])
        self.assertEqual(young_rows(synopsis), [])
        self.assertEqual(synopsis['young_lefty_status'], 'absent')
        self.assertIn('Young Lefty: absent', synopsis['spoken_summary'])
        seven = next(r for r in synopsis['range_index'] if r['label'] == 'Young Lefty')
        self.assertEqual(seven['detail_request']['args']['anchor_start_ny'], retained.ny('07:00'))

    def test_late_ten_purge_is_not_early_young_lefty(self):
        data = synthetic([(100, 110, 90, 100)] + [(100, 105, 95, 100)] * 4)
        data[36]['high'] = 112
        synopsis = self.synopsis(data)
        self.assertFalse(synopsis['young_lefty_relevant'])
        self.assertEqual(synopsis['young_lefty_status'], 'absent')
        self.assertIn('Young Lefty: absent', synopsis['spoken_summary'])

    def test_eight_purge_pending_young_lefty_is_relevant_without_nineate8(self):
        data = synthetic([(100, 110, 90, 100)] + [(95, 99, 92, 95)] * 4)
        data[12]['low'] = 89
        synopsis = self.synopsis(data)
        young = young_rows(synopsis)[0]
        self.assertEqual((young['direction'], young['outcome']), ('bullish', 'pending_at_review_cutoff'))
        self.assertEqual(young['first_purge_interval']['bar_open_ny'], retained.ny('08:00'))
        self.assertIn('buy-side delivery pending', synopsis['spoken_summary'])

    def test_nine_purge_is_also_valid_early_young_lefty(self):
        data = synthetic([(100, 110, 90, 100)] + [(100, 108, 92, 100)] * 4)
        data[24].update(high=112, low=104, open=105, close=105)
        synopsis = self.synopsis(data)
        young = young_rows(synopsis)[0]
        self.assertEqual(young['first_purge_interval']['bar_open_ny'], retained.ny('09:00'))
        self.assertEqual(young['direction'], 'bearish')
        self.assertTrue(synopsis['young_lefty_relevant'])

    def test_same_source_bar_two_sides_cannot_invent_young_lefty_direction(self):
        data = synthetic([(100, 110, 90, 100)] + [(100, 105, 95, 100)] * 4)
        data[24].update(high=112, low=89)
        synopsis = self.synopsis(data)
        young = young_rows(synopsis)[0]
        self.assertIsNone(young['direction'])
        self.assertEqual(young['verdict'], 'unverified')
        self.assertFalse(young['opposes_9ate8'])
        self.assertEqual(young['variant']['labels'], [])

    def test_real_early_failed_young_lefty_is_one_clause_not_erased(self):
        data = synthetic([(100, 110, 90, 100)] + [(95, 99, 92, 95)] * 4)
        data[12]['low'] = 89
        data[23].update(low=88, close=88)
        synopsis = self.synopsis(data)
        young = young_rows(synopsis)[0]
        self.assertEqual((young['direction'], young['verdict']), ('bullish', 'failed'))
        self.assertEqual(synopsis['spoken_summary'].count('Young Lefty'), 1)

    def test_incomplete_seven_does_not_invent_a_setup(self):
        data = synthetic([(100, 110, 90, 100)] + [(95, 99, 92, 95)] * 4)
        data[12]['low'] = 89
        del data[0]
        synopsis = self.synopsis(data)
        self.assertFalse(synopsis['young_lefty_relevant'])
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertIn('Young Lefty: unverified', synopsis['spoken_summary'])
        self.assertNotIn('Young Lefty: absent', synopsis['spoken_summary'])

    def test_missing_eight_does_not_skip_sevens_independent_evidence(self):
        data = synthetic([(100, 110, 90, 100)] + [(95, 99, 92, 95)] * 4)
        data[24]['low'] = 89
        del data[12:24]
        review = session_review(data, '2026-10-02', 'day')
        seven = next(o for o in review['observations'] if o['play'] == 'Young Lefty')
        self.assertEqual(seven['status'], 'insufficient_closed_candles')
        self.assertIn('evidence', seven)
        self.assertEqual(seven['evidence']['anchor']['start_ny'], retained.ny('07:00'))
        self.assertFalse(seven['evidence']['observation_coverage']['complete'])

    def test_missing_delivery_evidence_stays_unverified_not_failed_or_complete(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        replay.store('XAUUSD', [b for b in replay.bars['XAUUSD'] if b['time'] != parse_time(retained.ny('10:28'))])
        synopsis = replay.tool('XAUUSD')['review']['shift_synopsis']
        lead = synopsis['ranges'][0]
        self.assertNotEqual(lead['verdict'], 'boneless_delivered')
        self.assertNotEqual(lead['opposing_liquidity']['status'], 'objective_complete_while_range_valid')
        self.assertIn('Missing or unfinished', synopsis['spoken_summary'])

    def test_provisional_boneless_never_becomes_qualified(self):
        review = self.replay(retained.RetainedMarketReplayTests).tool('XAUUSD')['review']
        row = next(r for r in review['shift_story']['recap']['paired_interpretation'] if r['anchor_start_ny'] == retained.ny('08:00'))
        row['asset_role'] = 'potential boneless leg'
        row['setup_interval'].update(qualified_smt=False, potential_smt=True)
        for target in row['objective_status'].values():
            target.update(status='unverified_incomplete_paired_coverage', touch_bar_open_ny=None, range_invalidated_at_ny=None)
        synopsis = build_shift_synopsis(review, 'XAUUSD')
        lead = synopsis['ranges'][0]
        self.assertEqual(lead['verdict'], 'boneless_potential')
        self.assertIn('setup qualification pending', synopsis['spoken_summary'])
        self.assertFalse(lead['paired_setup']['qualified_smt'])

    def test_pending_opposite_boneless_cannot_downgrade_completed_local_delivery(self):
        data = synthetic([(100, 130, 70, 100), (100, 110, 90, 100)] + [(105, 109, 101, 105)] * 3)
        data[24]['high'] = 112
        data[25]['low'] = 89
        review = session_review(data, '2026-10-02', 'day')
        row = review['shift_story']['ranges'][0]
        self.assertEqual(row['directional_outcome']['status'], 'opposing_liquidity_delivered')
        review['shift_story']['recap']['paired_interpretation'] = [{
            'anchor_start_ny': retained.ny('08:00'), 'direction': 'bullish', 'asset_role': 'boneless leg',
            'setup_interval': {'start_ny': retained.ny('09:00'), 'end_ny': retained.ny('10:00'),
                               'qualified_smt': True, 'potential_smt': False},
            'objective_status': {
                'midpoint': {'status': 'pending_at_review_cutoff', 'level': 100},
                'opposing_liquidity': {'status': 'pending_at_review_cutoff', 'level': 110}}}]
        synopsis = build_shift_synopsis(review, 'NAS100')
        lead = synopsis['ranges'][0]
        self.assertEqual((lead['direction'], lead['verdict']), ('bearish', 'delivered'))
        self.assertEqual(lead['paired_alternative']['direction'], 'bullish')
        self.assertEqual(lead['opposing_liquidity']['level'], 90)
        self.assertEqual(lead['paired_alternative']['opposing_liquidity']['level'], 110)
        self.assertIn('separate bullish boneless full delivery pending', synopsis['spoken_summary'])

    def test_no_untouched_later_range_candidate_list_or_cutoff_setup(self):
        data = synthetic([(100, 110, 90, 100)] + [(100, 105, 95, 100)] * 4)
        synopsis = self.synopsis(data)
        self.assertEqual([r['play'] for r in synopsis['ranges']], ['9ate8'])
        self.assertEqual(len(synopsis['range_index']), 5)

    def test_short_default_payload_preserves_metadata_raw_context_and_detail_routing(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        for asset in ('NAS100', 'XAUUSD'):
            with self.subTest(asset=asset):
                context = MarketConversation()
                context.begin_turn()
                source = context.run('review_market_session', dict(asset=asset, date_ny='2026-10-02', shift='day'),
                                     lambda name, args: replay.tool(asset))
                source['transport_metadata'] = 'x' * 300
                before, evidence = deepcopy(source), deepcopy(context.evidence)
                payload = voice_tool_payload('review_market_session', source)
                self.assertEqual(source, before)
                self.assertEqual(context.evidence, evidence)
                self.assertTrue(payload['ok'])
                self.assertEqual(payload['voice_view']['kind'], 'shift_synopsis')
                self.assertNotIn('shift_story', payload['review'])
                self.assertNotIn('shift_recap', payload['review'])
                self.assertLessEqual(len(json.dumps(payload, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)
                for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
                    self.assertEqual(payload['market_context'][key], source['market_context'][key])
                self.assertEqual(payload['market_context']['evidence_ref'], '#/review')
                self.assertEqual(payload['transport_metadata'], source['transport_metadata'])
                self.assertNotIn('range_outcomes', payload['market_context'])
                self.assertTrue(context._detail_index)
                if asset == 'NAS100':
                    context.begin_turn('For the 9 AM range, did the 10:00 AM Model 1 have a Super Soup?')
                    request = context.required_evidence_request()
                    self.assertEqual(request['args']['anchor_start_ny'], retained.ny('09:00'))
                    self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('10:00'))

    def test_all_retained_default_payloads_have_lower_hard_ceiling(self):
        for cls, assets in ((retained.RetainedMarketReplayTests, ('NAS100', 'SPX', 'XAUUSD', 'XAGUSD')),
                            (crypto.CryptoShiftReplayTests, ('BTCUSD', 'ETHUSD'))):
            replay = self.replay(cls)
            for asset in assets:
                source = replay.tool(asset)
                payload = voice_tool_payload('review_market_session', source)
                self.assertTrue(payload['ok'])
                # Verified two-sided chronology is now retained per selected
                # range, inside the unchanged 12k limit with metadata margin.
                self.assertLess(len(json.dumps(payload, separators=(',', ':'))),
                                SHIFT_SYNOPSIS_TARGET_CHARS - 300)
                # Official confirmation and pre-confirmation observations must remain distinct.
                # Handoffs, concurrent DOL and primary Model 1 are required in the default.
                self.assertLess(len(payload['review']['shift_synopsis']['spoken_summary'].split()), 360)

    def test_default_ignores_unbounded_legacy_prose_but_bounds_unknown_metadata(self):
        source = self.replay(retained.RetainedMarketReplayTests).tool('NAS100')
        source['review']['shift_story']['recap']['spoken_summary'] = 'legacy ' * 100000
        payload = voice_tool_payload('review_market_session', source)
        self.assertTrue(payload['ok'])
        source['transport_metadata'] = 'x' * 50000
        failure = voice_tool_payload('review_market_session', source)
        self.assertFalse(failure['ok'])
        self.assertEqual(failure['status'], 'voice_synopsis_budget_exceeded')
        self.assertLessEqual(len(json.dumps(failure, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)
        self.assertNotIn('review', failure)
        self.assertEqual(failure['detail_request']['args']['anchor_start_ny'], retained.ny('08:00'))

    def test_both_prompts_request_short_default_and_independent_young_lefty(self):
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            self.assertIn('SHORT', prompt)
            self.assertIn('shift_synopsis.spoken_summary', prompt)
            self.assertIn('Young Lefty', prompt)
            self.assertIn("always state Young Lefty's independent status", prompt)
            self.assertNotIn('Omit absent/uninitiated', prompt)
            self.assertIn('Recaps: verdict then BUT induced 50%', prompt)
            self.assertIn('No glossary, universal threshold, inferred intent/profit', prompt)
            self.assertNotIn('4-7 concise sentences', prompt)


if __name__ == '__main__':
    unittest.main()
