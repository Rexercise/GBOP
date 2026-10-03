"""Real retained crypto OHLC: event-specific SMT cannot replace H1 truth."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time, summarize
from gbop_voice_web.voice_runtime import compact_voice_tool_result

FIXTURE = Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_crypto_night.json'
FIELDS = ('time', 'open', 'high', 'low', 'close')


def ny(clock, day='2026-10-02'):
    return f'{day}T{clock}:00-04:00'


class CryptoShiftReplayTests(unittest.TestCase):
    def test_prompt_respects_backend_precedence_for_bounded_events(self):
        from gbop_voice_web.market_context import LIFECYCLE_PROMPT
        self.assertIn('Follow recap.evidence_precedence and spoken_summary', LIFECYCLE_PROMPT)
        self.assertNotIn('Lead with recap.paired_interpretation', LIFECYCLE_PROMPT)

    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text())
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.HISTORY_SQL)
        self.addCleanup(self.conn.close)
        self.bars = {}
        self.symbols = {}
        for item in self.fixture['instruments']:
            asset, step = item['asset'], item['step']
            self.symbols[asset] = item['symbol']
            bars = [dict(zip(FIELDS, values)) for values in item['candles']]
            self.bars[asset, step] = bars
            days = {}
            for bar in bars:
                days.setdefault(bar['time'] // 86400 * 86400, []).append(bar)
            for day, values in days.items():
                self.conn.execute('INSERT INTO gbop_market_history VALUES (?,?,?,?,?)',
                                  (asset, item['symbol'], step, day, json.dumps(values)))
        self.feed_patch = patch.object(market, 'read_feed', side_effect=self.feed)
        self.feed_patch.start()
        self.addCleanup(self.feed_patch.stop)

    @contextmanager
    def db(self):
        yield self.conn

    def feed(self, db, asset, now=None):
        asset = market.asset_name(asset)
        return dict(ok=True, asset=asset, symbol=self.symbols[asset],
                    status='historical_replay', is_live=False, bars=[], bars_m1=[])

    def tool(self, asset='BTCUSD'):
        result = market.market_tool(self.db, 'review_market_session',
                                   dict(asset=asset, date_ny='2026-10-02', shift='night'))
        self.assertTrue(result['ok'], result)
        return result

    def test_fixture_integrity_and_complete_native_resolution_coverage(self):
        provenance = self.fixture['provenance']
        digest = hashlib.sha256(json.dumps(self.fixture['instruments'], sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(digest, provenance['instruments_sha256'])
        self.assertEqual(provenance['kind'], 'retained_broker_history')
        for (asset, step), bars in self.bars.items():
            with self.subTest(asset=asset, step=step):
                self.assertEqual([b['time'] for b in bars], list(range(
                    parse_time(ny('19:00')), parse_time(ny('00:00', '2026-10-03')), step)))

    def test_native_m1_and_m5_independently_verify_btc_soup_and_eth_outside_close(self):
        expected = {'BTCUSD': [(84499.66, 84672.67, 84423.19, 84640.81),
                               (84639.55, 84712.65, 84575.95, 84626.8)],
                    'ETHUSD': [(2667.47, 2677.18, 2664.32, 2675.15),
                               (2675.16, 2683.85, 2673.58, 2678.7)]}
        for (asset, step), bars in self.bars.items():
            actual = []
            for opening in ('20:00', '21:00'):
                start = parse_time(ny(opening))
                candle = summarize(bars, start, start + 3600, step)
                self.assertTrue(candle['complete'])
                actual.append(tuple(candle[k] for k in FIELDS[1:]))
            self.assertEqual(actual, expected[asset])
            anchor, nine = actual
            self.assertGreater(nine[1], anchor[1])
            if asset == 'BTCUSD':
                self.assertTrue(anchor[2] < nine[3] < anchor[1])
            else:
                self.assertGreater(nine[3], anchor[1])

    def test_same_setup_hour_purges_are_both_bones_not_boneless_smt(self):
        review = self.tool()['review']
        paired = review['paired_smt']
        event = next(e for e in paired['events'] if e['side'] == 'buy_side')
        self.assertEqual(event['bar_open_ny'], ny('21:04'))
        self.assertIsNone(event['boneless_asset'])
        self.assertFalse(paired['divergence_confirmed'])
        self.assertTrue(paired['timing_asynchrony_observed'])
        self.assertFalse(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['setup_interval']['status'], 'both_assets_purged_same_setup_interval')
        self.assertEqual(event['setup_interval']['own_purge_observed'], {'BTCUSD': True, 'ETHUSD': True})
        self.assertTrue(event['anchors_valid_at_event'])
        self.assertEqual(event['peer_later_swept_at_ny'], ny('21:11'))
        self.assertEqual(event['scope']['status'], 'peer_caught_up')
        self.assertFalse(event['scope']['whole_shift_boneless_identity_established'])
        closed = event['scope']['execution_candles']
        self.assertEqual(closed['BTCUSD']['candle_science'], 'wick_above')
        self.assertTrue(closed['BTCUSD']['anchor_valid_at_close'])
        self.assertEqual(closed['ETHUSD']['candle_science'], 'close_above')
        self.assertFalse(closed['ETHUSD']['anchor_valid_at_close'])
        # Earlier body-purge timing cannot create an inherited identity when
        # the completed setup interval proves both corresponding purges.
        self.assertEqual(event['paired_model1']['status'], 'both_assets_purged_same_setup_interval')
        self.assertNotIn('boneless_reference', event['paired_model1'])
        recap = review['shift_story']['recap']
        self.assertEqual(recap['evidence_precedence'], 'local_chronology_only')
        self.assertNotIn('SMT-supported', recap['headline'])
        self.assertIn('9:11 PM M1', recap['spoken_summary'])
        self.assertNotIn('boneless', recap['spoken_summary'])
        self.assertNotIn('divergence', recap['spoken_summary'])
        self.assertEqual(paired['spoken_summary'], '')

    def test_night_midpoint_only_cannot_become_day_full_objective_delivery(self):
        review = self.tool()['review']
        self.assertEqual((review['date_ny'], review['shift']), ('2026-10-02', 'night'))
        selected = next(r for r in review['shift_story']['ranges'] if r['anchor_start_ny'] == ny('20:00'))
        self.assertEqual(selected['direction_observed'], 'bearish')
        midpoint, opposite = selected['objectives']
        self.assertEqual(midpoint['level'], 84547.93)
        self.assertEqual(midpoint['evidence']['bar_open_ny'], ny('23:05'))
        self.assertEqual(midpoint['status'], 'observed_after_purge')
        self.assertEqual(opposite['level'], 84423.19)
        self.assertEqual(opposite['status'], 'not_observed_before_invalidation')
        self.assertEqual(selected['invalidated_at_ny'], ny('00:00', '2026-10-03'))
        self.assertNotIn('objective_complete_while_range_valid', {
            e['objective_status']['BTCUSD']['opposing_liquidity']['status'] for e in review['paired_smt']['events']})

    def test_compact_result_preserves_same_shift_event_scope_and_independent_h1_closes(self):
        full = self.tool()
        saved = deepcopy(full)
        compact = compact_voice_tool_result('review_market_session', full)
        self.assertEqual(full, saved)
        self.assertEqual((compact['review']['date_ny'], compact['review']['shift']), ('2026-10-02', 'night'))
        self.assertEqual(compact['review']['paired_smt'], full['review']['paired_smt'])
        self.assertEqual(compact['review']['shift_recap'], full['review']['shift_story']['recap'])

    def test_native_m5_fallback_preserves_hourly_classification_with_coarser_catchup(self):
        self.conn.execute('DELETE FROM gbop_market_history WHERE step=60')
        result = self.tool()
        self.assertEqual(result['available_precision_seconds'], 300)
        event = result['review']['paired_smt']['events'][0]
        self.assertEqual(event['scope']['status'], 'peer_caught_up')
        self.assertEqual(event['scope']['peer_catchup_bar_open_ny'], ny('21:10'))
        self.assertEqual(event['scope']['execution_candles']['BTCUSD']['candle_science'], 'wick_above')
        self.assertEqual(event['scope']['execution_candles']['ETHUSD']['candle_science'], 'close_above')

    def test_unfinished_execution_hour_is_not_classified_from_future_candles(self):
        result = market.paired_market_review(self.db, 'BTCUSD', 'ETHUSD',
                                            parse_time(ny('20:00')), parse_time(ny('21:10')))
        self.assertFalse(result['divergence_confirmed'])
        self.assertTrue(result['timing_asynchrony_observed'])
        event = result['events'][0]
        self.assertEqual(event['setup_interval']['status'], 'provisional_timing_asynchrony')
        self.assertIsNone(event['boneless_asset'])
        self.assertNotIn('boneless_reference', event['paired_model1'])
        for candle in result['execution_candles'].values():
            self.assertFalse(candle['complete'])
            self.assertIsNone(candle['anchor_valid_at_close'])
            self.assertEqual(candle['candle_science'], 'unverified_incomplete_execution_candle')


if __name__ == '__main__':
    unittest.main()
