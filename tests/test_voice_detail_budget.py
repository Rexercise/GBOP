"""Focused read-only CRT pages preserve full selected-card evidence and scope."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_detail import DETAIL_CHARACTER_BUDGET
from gbop_voice_web.voice_runtime import compact_voice_tool_result
import test_retained_market_replays as retained
import test_crypto_shift_replays as crypto


def size(value):
    return len(json.dumps(value, separators=(',', ':')))


class VoiceDetailBudgetTests(unittest.TestCase):
    def replay(self, cls=retained.RetainedMarketReplayTests):
        replay = cls()
        replay.setUp()
        self.addCleanup(replay.doCleanups)
        return replay

    def request(self, replay, args):
        raw = market.market_tool(replay.db, 'review_market_crt', args)
        self.assertTrue(raw['ok'], raw)
        raw['market_context'] = {'selection': {'asset': raw['asset'], 'anchor_start_ny': args['anchor_start_ny'],
            'through_ny': args['through_ny']}, 'evidence': {'scope_id': 'same-scope', 'padding': 'x' * 3000}}
        saved = deepcopy(raw)
        page = voice_tool_payload('review_market_crt', raw)
        self.assertEqual(raw, saved)
        self.assertEqual(page['market_context'], raw['market_context'])
        self.assertLessEqual(size(page), DETAIL_CHARACTER_BUDGET)
        return raw, page

    def args(self, asset='NAS100', **overrides):
        return {'asset': asset, 'anchor_start_ny': '2026-10-02T09:00:00-04:00',
            'anchor_timeframe': 'H1', 'through_ny': '2026-10-02T12:00:00-04:00', **overrides}

    def test_actual_day_pages_fit_and_cover_every_available_identity_without_window_drift(self):
        replay = self.replay()
        for asset in ('NAS100', 'SPX', 'XAUUSD', 'XAGUSD'):
            with self.subTest(asset=asset):
                args = self.args(asset)
                visited = set()
                expected = None
                while args:
                    raw, page = self.request(replay, args)
                    self.assertTrue(page['ok'], page)
                    full = compact_voice_tool_result('review_market_crt', raw)
                    if expected is None:
                        expected = {(x['bar_open_ny'], x['purged_side']) for x in full['review']['candle_lifecycle']['purge_candles']}
                    cards = page['review']['candle_lifecycle']['purge_candles']
                    for actual in cards:
                        key = (actual['bar_open_ny'], actual['purged_side'])
                        self.assertNotIn(key, visited)
                        visited.add(key)
                        expected_card = next(x for x in full['review']['candle_lifecycle']['purge_candles']
                                             if (x['bar_open_ny'], x['purged_side']) == key)
                        self.assertEqual(actual, expected_card)
                    self.assertEqual(page['review']['events'], full['review']['events'])
                    self.assertEqual(page['review']['anchor'], full['review']['anchor'])
                    following = page['voice_detail_page']['next_request']
                    args = following['args'] if following else None
                    if args:
                        self.assertEqual(args['asset'], asset)
                        self.assertEqual(args['anchor_start_ny'], self.args()['anchor_start_ny'])
                        self.assertEqual(args['through_ny'], self.args()['through_ny'])
                        self.assertEqual(args['context_action'], 'continue')
                        self.assertIsNone(args['detail_candle_start_ny'])
                self.assertEqual(visited, expected)

    def test_exact_second_model1_preserves_own_delivery_after_invalidation_and_opening_names(self):
        replay = self.replay()
        raw, page = self.request(replay, self.args(detail_candle_start_ny='2026-10-02T10:10:00-04:00'))
        self.assertTrue(page['ok'], page)
        card = page['review']['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(card['bar_open_ny'], '2026-10-02T10:10:00-04:00')
        structure = card['super_soup_structure']
        self.assertEqual(structure['structural_quality'], 'not_clean')
        self.assertEqual(structure['local_crt_invalidated_at_ny'], '2026-10-02T10:20:00-04:00')
        own = structure['local_function_objectives']['opposing_liquidity']
        self.assertEqual(own['level'], 30963.6)
        self.assertEqual(own['evidence']['bar_open_ny'], '2026-10-02T10:52:00-04:00')
        self.assertEqual(own['relative_to_model1_invalidation'], 'after_model1_invalidation')
        self.assertIn('10:10 AM M5 Model 1', own['spoken_label'])
        self.assertEqual(page['review']['candle_lifecycle']['performance_summary'], structure['performance_summary'])
        self.assertEqual(page['review']['model1']['candles'][0]['bar_open_ny'], card['bar_open_ny'])

    def test_missing_exact_focus_never_substitutes_and_empty_cursor_never_loops(self):
        replay = self.replay()
        for selection, status in [({'detail_candle_start_ny': '2026-10-02T10:35:00-04:00'}, 'detail_identity_not_in_available_page'),
                                  ({'detail_from_ny': '2026-10-02T12:00:00-04:00'}, 'detail_page_exhausted')]:
            raw, page = self.request(replay, self.args(**selection))
            self.assertFalse(page['ok'])
            self.assertEqual(page['status'], status)
            self.assertNotIn('review', page)
            self.assertNotIn('next_request', page)
            self.assertEqual(page['through_ny'], self.args()['through_ny'])

    def test_native_crypto_focus_retains_same_hour_qualification(self):
        replay = self.replay(crypto.CryptoShiftReplayTests)
        for asset in ('BTCUSD', 'ETHUSD'):
            raw, page = self.request(replay, self.args(asset, anchor_start_ny='2026-10-02T20:00:00-04:00',
                                                    through_ny='2026-10-03T00:00:00-04:00'))
            self.assertTrue(page['ok'], page)
            for actual, expected in zip(page['review']['paired_smt']['events'], raw['review']['paired_smt']['events']):
                self.assertEqual(actual['setup_interval'], expected['setup_interval'])
                self.assertFalse(actual['setup_interval']['qualified_smt'])
                self.assertEqual(actual['recap_eligible'], expected['recap_eligible'])
                self.assertEqual(actual['paired_model1']['status'], expected['paired_model1']['status'])

    def test_selector_does_not_change_calculation_or_existing_blessed_thief_cursor(self):
        replay = self.replay()
        base = market.market_tool(replay.db, 'review_market_crt', self.args())
        args = self.args(detail_candle_start_ny='2026-10-02T10:10:00-04:00')
        focus = market.market_tool(replay.db, 'review_market_crt', args)
        self.assertEqual(focus['review'], base['review'])
        for selection in [dict(detail_candle_start_ny='bad'), dict(detail_from_ny='bad'),
                          dict(detail_from_ny='2026-10-02T10:00:00-04:00', detail_candle_start_ny='2026-10-02T10:10:00-04:00')]:
            bad = market.market_tool(replay.db, 'review_market_crt', self.args(**selection))
            self.assertFalse(bad['ok'])
        _, page = self.request(replay, self.args(blessed_thief_from_ny='2026-10-02T11:00:00-04:00'))
        self.assertEqual(page['voice_detail_page']['next_request']['args']['blessed_thief_from_ny'], '2026-10-02T11:00:00-04:00')

    def test_backend_cap_is_explicit_and_does_not_advertise_unreachable_cursor(self):
        replay = self.replay()
        raw = market.market_tool(replay.db, 'review_market_crt', self.args())
        facts = raw['review']['candle_lifecycle']['purge_candles']
        raw['review']['candle_lifecycle']['purge_candles'] = facts[-1:]
        raw['review']['candle_lifecycle']['next_identity_open_ny'] = '2026-10-02T11:59:00-04:00'
        page = voice_tool_payload('review_market_crt', raw)
        self.assertTrue(page['ok'])
        self.assertIsNone(page['voice_detail_page']['next_request'])
        self.assertEqual(page['voice_detail_page']['backend_remaining_from_ny'], '2026-10-02T11:59:00-04:00')
        self.assertIn('unavailable deeper records', page['voice_detail_page']['note'])


if __name__ == '__main__':
    unittest.main()
