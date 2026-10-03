"""Recorded M1 evidence through conversation routing and the actual voice boundary.

No broker/provider/LLM calls. Feed metadata matches the captured live diagnosis;
all analytical prices come from the retained fixture, never synthetic prices.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_status import broker_session_status, feed_health
from gbop_voice_web.voice_payload import voice_tool_payload, shift_voice_overview
from gbop_voice_web.voice_runtime import compact_voice_tool_result
import test_retained_market_replays as retained
from test_voice_payload_budget import expanded, identities

ny = retained.ny


class VoiceContextPayloadTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)
        market.read_feed.side_effect = self.feed
        self.context = MarketConversation()

    def feed(self, db, asset, now=None):
        # Realistic live envelope matters: the old budget tests only attached
        # opaque padding, not the repeated verified evidence returned in use.
        bid, ask = {'NAS100': (30826.84, 30827.96), 'SPX': (7726.73, 7727.13),
                    'XAUUSD': (4140.309, 4140.569), 'XAGUSD': (60.369, 60.399)}[asset]
        return {**self.replay.feed(db, asset), 'source': 'MT5 broker feed',
                'status': 'stale', 'is_live': False, 'bid': bid, 'ask': ask,
                'feed_health': feed_health(27, 79382),
                'broker_session': broker_session_status(),
                'tick_time_utc': '2026-10-02T20:57:59+00:00',
                'tick_age_seconds': 79382, 'capture_age_seconds': 27,
                'received_at_utc': '2026-10-03T13:16:34+00:00'}

    def run_tool(self, name, args, *, text=None):
        self.context.begin_turn(text)
        raw = self.context.run(name, args,
            lambda tool, resolved: market.market_tool(self.replay.db, tool, resolved))
        self.assertTrue(raw['ok'], raw)
        return raw

    def voice(self, name, raw, *, success=True):
        saved = deepcopy(raw)
        retained = deepcopy(self.context.evidence)
        page = (shift_voice_overview(raw) if name == 'review_market_session'
                else voice_tool_payload(name, raw))
        self.assertEqual(raw, saved)
        self.assertEqual(self.context.evidence, retained)
        self.assertLessEqual(len(json.dumps(page, separators=(',', ':'))), 32000)
        self.assertEqual(page['ok'], success, page)
        for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
            self.assertEqual(page['market_context'][key], raw['market_context'][key])
        self.assertNotIn('range_outcomes', page['market_context'])
        self.assertNotIn('recap', page['market_context'])
        if success:
            self.assertEqual(page['market_context']['evidence_ref'], '#/review')
        else:
            self.assertNotIn('evidence_ref', page['market_context'])
        # Resolve every pointer, including nested/reused scalar policy prose;
        # missing targets or reference loops must fail this complete traversal.
        return page, expanded(page, page)

    def full(self, asset):
        raw = self.run_tool('review_market_session', {
            'asset': asset, 'date_ny': '2026-10-02', 'shift': 'day'})
        wire, page = self.voice('review_market_session', raw)
        return raw, wire, page

    def detail(self, asset, hour, **selection):
        raw = self.run_tool('review_market_crt', {
            'asset': asset, 'context_action': 'continue', 'anchor_start_ny': ny(hour),
            'anchor_timeframe': 'H1', 'through_ny': ny('12:00'), **selection})
        wire, page = self.voice('review_market_crt', raw)
        return raw, wire, page

    def test_full_nas_and_gold_context_fit_with_all_range_identities_and_direction(self):
        for asset in ('NAS100', 'XAUUSD'):
            with self.subTest(asset=asset):
                self.context = MarketConversation()
                raw, wire, page = self.full(asset)
                self.assertEqual(raw['market_context']['recap']['spoken_summary'],
                                 raw['review']['shift_synopsis']['spoken_summary'])
                self.assertTrue(raw['market_context']['range_outcomes'])
                for actual, source in zip(page['review']['shift_story']['ranges'],
                                          raw['review']['shift_story']['ranges']):
                    for key in ('anchor_start_ny', 'role', 'status', 'direction_observed', 'invalidated_at_ny'):
                        self.assertEqual(actual[key], source[key])
                    self.assertEqual(actual['directional_outcome']['status'], source['directional_outcome']['status'])
                    self.assertEqual(actual['directional_outcome']['initiating_identity'], source['directional_outcome']['initiating_identity'])
                    actual_ids = identities(wire, actual)
                    self.assertEqual([(c['bar_open_ny'], c['bar_close_ny'], c['direction']) for c in actual_ids],
                                     [(c['bar_open_ny'], c['bar_close_ny'], c['direction']) for c in source['model1']['candles']])
                    self.assertEqual(actual['detail_request']['args']['context_action'], 'continue')
                self.assertFalse(page['is_live'])
                self.assertEqual(page['broker_session']['status'], 'unknown')
                self.assertEqual(page['review']['shift_recap']['spoken_summary'], raw['review']['shift_story']['recap']['spoken_summary'])

    def test_generated_nas_nine_detail_retains_ten_model1_and_local_parent_chronology(self):
        _, _, full = self.full('NAS100')
        row = next(r for r in full['review']['shift_story']['ranges'] if r['anchor_start_ny'] == ny('09:00'))
        request = deepcopy(row['detail_request'])
        request['args']['detail_candle_start_ny'] = ny('10:00')
        raw = self.run_tool(request['tool'], request['args'])
        _, page = self.voice(request['tool'], raw)
        self.assertEqual(page['review']['anchor']['start_ny'], ny('09:00'))
        self.assertEqual(page['market_context']['selection']['anchor_start_ny'], ny('09:00'))
        self.assertEqual(page['voice_detail_page']['selected_candle_start_ny'], ny('10:00'))
        card = page['review']['candle_lifecycle']['purge_candles'][0]
        full_card = compact_voice_tool_result('review_market_crt', raw)['review']['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(card, full_card)
        self.assertEqual(card['bar_open_ny'], ny('10:00'))
        self.assertEqual(card['csd']['evidence']['bar_open_ny'], ny('11:00'))
        self.assertEqual(card['csd']['evidence']['bar_close_ny'], ny('11:05'))
        local = card['super_soup_structure']
        self.assertEqual(local['local_crt_invalidated_at_ny'], ny('10:20'))
        self.assertEqual(local['local_function_objectives']['midpoint']['evidence']['bar_open_ny'], ny('10:10'))
        own = local['local_function_objectives']['opposing_liquidity']
        self.assertEqual(own['evidence']['bar_open_ny'], ny('10:59'))
        self.assertEqual(own['relative_to_model1_invalidation'], 'after_model1_invalidation')
        outcome = page['review']['directional_outcome']
        self.assertEqual(outcome['initiating_identity']['source_purge']['bar_open_ny'], ny('10:02'))
        self.assertEqual(outcome['initiating_identity']['source_purge']['bar_close_ny'], ny('10:03'))
        self.assertEqual(outcome['midpoint']['evidence']['bar_open_ny'], ny('11:03'))
        self.assertEqual(outcome['opposing_liquidity']['evidence']['bar_open_ny'], ny('11:12'))
        self.assertEqual(outcome['direction'], 'bearish')

    def test_gold_seven_preserves_wick_delivery_before_invalidation_and_separate_body_page(self):
        self.full('XAUUSD')
        _, _, page = self.detail('XAUUSD', '07:00')
        outcome = page['review']['directional_outcome']
        self.assertEqual(outcome['play_context'], 'Young Lefty')
        self.assertEqual((outcome['direction'], outcome['status']), ('bullish', 'opposing_liquidity_delivered'))
        self.assertEqual(outcome['initiating_identity']['identity'], 'Turtle Wick Soup')
        self.assertEqual(outcome['initiating_identity']['source_purge']['bar_open_ny'], ny('08:11'))
        self.assertEqual(outcome['first_source_return_inside']['bar_close_ny'], ny('08:12'))
        self.assertEqual(outcome['midpoint']['evidence']['bar_open_ny'], ny('08:19'))
        self.assertEqual(outcome['opposing_liquidity']['evidence']['bar_open_ny'], ny('08:30'))
        self.assertTrue(outcome['delivery_before_later_invalidation'])
        self.assertEqual(outcome['range_invalidated_at_ny'], ny('09:00'))
        self.assertEqual(page['voice_detail_page']['selected_candle_start_ny'], ny('08:10'))
        _, _, focused = self.detail('XAUUSD', '07:00', detail_candle_start_ny=ny('08:30'))
        card = focused['review']['candle_lifecycle']['purge_candles'][0]
        self.assertEqual((card['identity'], card['direction']), ('Model 1 candle', 'bearish'))
        self.assertEqual(card['csd']['status'], 'not_observed_before_range_invalidation')
        self.assertEqual(focused['review']['directional_outcome'], outcome)
        self.assertEqual(focused['review']['variant_evidence'], page['review']['variant_evidence'])

    def test_gold_eight_keeps_paired_bearish_delivery_separate_from_later_local_bullish_failure(self):
        _, _, full = self.full('XAUUSD')
        _, _, page = self.detail('XAUUSD', '08:00')
        for review in (full['review'], page['review']):
            event = next(e for e in review['paired_smt']['events'] if e['side'] == 'buy_side')
            self.assertEqual((event['swept_asset'], event['boneless_asset'], event['direction']),
                             ('XAGUSD', 'XAUUSD', 'bearish'))
            self.assertEqual(event['boneless_status'], 'completed_delivery')
            self.assertEqual(event['setup_interval']['start_ny'], ny('09:00'))
            self.assertEqual(event['setup_interval']['end_ny'], ny('10:00'))
            target = event['objective_status']['XAUUSD']['opposing_liquidity']
            self.assertEqual(target['status'], 'objective_complete_while_range_valid')
            self.assertEqual(target['touch_bar_open_ny'], ny('10:28'))
            self.assertEqual(target['range_invalidated_at_ny'], ny('11:00'))
            self.assertFalse(event['entry_confirmed'])
        own = page['review']['directional_outcome']
        self.assertEqual((own['direction'], own['status']), ('bullish', 'failed_before_objectives'))
        self.assertEqual(own['initiating_identity']['source_purge']['bar_open_ny'], ny('10:28'))
        self.assertEqual(own['opposing_liquidity']['liquidity_side'], 'buy-side')

    def test_missing_source_minute_survives_context_and_overview_compaction(self):
        from gbop_voice_web.candle_evidence import parse_time
        self.replay.store('XAUUSD', [b for b in self.replay.bars['XAUUSD'] if b['time'] != parse_time(ny('10:28'))])
        _, _, page = self.full('XAUUSD')
        pair = page['review']['paired_smt']
        self.assertFalse(pair['paired_coverage_complete'])
        event = next(e for e in pair['events'] if e['side'] == 'buy_side')
        self.assertNotEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'],
                            'objective_complete_while_range_valid')

    def test_final_hour_detail_preserves_unassessed_pair_window(self):
        for asset in ('NAS100', 'XAUUSD'):
            with self.subTest(asset=asset):
                self.context = MarketConversation()
                self.full(asset)
                _, _, page = self.detail(asset, '11:00')
                pair = page['review']['paired_smt']
                self.assertEqual(pair['status'], 'no_subsequent_evidence_window')
                self.assertEqual(pair['assessment_status'], 'not_assessed')
                self.assertEqual(pair['events'], [])
                self.assertTrue(pair['message'])

    def test_budget_failure_is_bounded_has_exact_scope_and_no_complete_recap(self):
        raw, _, _ = self.full('NAS100')
        raw['review']['shift_story']['recap']['spoken_summary'] += ' oversized prose' * 6000
        _, failure = self.voice('review_market_session', raw, success=False)
        self.assertEqual(failure['status'], 'voice_overview_budget_exceeded')
        self.assertNotIn('review', failure)
        self.assertEqual(failure['detail_request']['args']['anchor_start_ny'], ny('08:00'))
        raw = self.run_tool('review_market_crt', {
            'asset': 'NAS100', 'context_action': 'continue', 'anchor_start_ny': ny('09:00'),
            'anchor_timeframe': 'H1', 'through_ny': ny('12:00'), 'detail_candle_start_ny': ny('10:00')})
        raw['review']['candle_lifecycle']['purge_candles'][0]['unbounded_detail'] = 'x' * 60000
        _, failure = self.voice('review_market_crt', raw, success=False)
        self.assertEqual(failure['status'], 'voice_detail_budget_exceeded')
        self.assertEqual(failure['detail_request']['args']['detail_candle_start_ny'], ny('10:00'))
        self.assertEqual(failure['raw_candle_request']['args']['start_ny'], ny('10:00'))
        self.assertNotIn('review', failure)
        # Even malformed/opaque context must not overflow the error envelope.
        raw['market_context']['opaque_metadata'] = 'x' * 100000
        failure = voice_tool_payload('review_market_crt', raw)
        self.assertFalse(failure['ok'])
        self.assertLessEqual(len(json.dumps(failure, separators=(',', ':'))), 32000)
        self.assertEqual(failure['market_context']['selection']['anchor_start_ny'], ny('09:00'))


if __name__ == '__main__':
    unittest.main()
