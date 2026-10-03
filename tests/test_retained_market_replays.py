"""Offline broker-history replay: storage -> market tools -> voice-ready facts.

The JSON contains real retained OHLC, not the synthetic candles in other tests.
Fault-injection tests delete bars explicitly; they never rewrite the source file.
No live service, member record, model call or exact LLM wording is exercised.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.voice_runtime import compact_voice_tool_result


FIXTURE = Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json'
FIELDS = ('time', 'open', 'high', 'low', 'close')


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


def selected(review, clock):
    return next(r for r in review['shift_story']['ranges'] if r['anchor_start_ny'] == ny(clock))


def model(row, clock):
    return next(c for c in row['candle_lifecycle']['purge_candles'] if c['bar_open_ny'] == ny(clock))


class RetainedMarketReplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text())
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.HISTORY_SQL)
        self.addCleanup(self.conn.close)
        self.bars = {}
        self.symbols = {}
        for item in self.fixture['instruments']:
            asset = item['asset']
            self.symbols[asset] = item['symbol']
            self.bars[asset] = [dict(zip(FIELDS, row)) for row in item['candles']]
            self.store(asset, self.bars[asset])
        # Only today's feed discovery is replaced. Real history_bars reads the
        # unmodified retained rows from SQLite and selects the source resolution.
        self.feed_patch = patch.object(market, 'read_feed', side_effect=self.feed)
        self.feed_patch.start()
        self.addCleanup(self.feed_patch.stop)

    @contextmanager
    def db(self):
        yield self.conn

    def store(self, asset, bars):
        self.conn.execute('INSERT OR REPLACE INTO gbop_market_history VALUES (?,?,?,?,?)',
                          (asset, self.symbols[asset], 60, parse_time('2026-10-02T00:00:00+00:00'),
                           json.dumps(bars)))

    def feed(self, db, asset):
        asset = market.asset_name(asset)
        return dict(ok=True, asset=asset, symbol=self.symbols[asset],
                    status='historical_replay', is_live=False, bars=[], bars_m1=[])

    def tool(self, asset, *, anchor=None, through='12:00'):
        if anchor is None:
            name = 'review_market_session'
            args = dict(asset=asset, date_ny='2026-10-02', shift='day')
        else:
            name = 'review_market_crt'
            args = dict(asset=asset, anchor_start_ny=ny(anchor),
                        anchor_timeframe='H1', through_ny=ny(through))
        result = market.market_tool(self.db, name, args)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['available_precision_seconds'], 60)
        self.assertFalse(result['is_live'])
        return result

    def source(self, asset, start, end):
        return [b for b in self.bars[asset] if parse_time(ny(start)) <= b['time'] < parse_time(ny(end))]

    def assert_first_low_touch(self, asset, start, end, level, clock):
        hits = [b for b in self.source(asset, start, end) if b['low'] <= level]
        self.assertTrue(hits)
        self.assertEqual(hits[0]['time'], parse_time(ny(clock)))

    def test_fixture_provenance_integrity_and_complete_minute_coverage(self):
        provenance = self.fixture['provenance']
        self.assertEqual(provenance['kind'], 'retained_broker_history')
        self.assertEqual(provenance['source_table'], 'public.gbop_market_history')
        self.assertEqual(provenance['source_resolution_seconds'], 60)
        self.assertEqual(provenance['fields'], list(FIELDS))
        self.assertTrue(provenance['limits'])
        digest = hashlib.sha256(json.dumps(self.fixture['instruments'], sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(digest, provenance['instruments_sha256'])
        expected_times = list(range(parse_time(ny('07:00')), parse_time(ny('12:00')), 60))
        self.assertEqual(set(self.bars), {'NAS100', 'SPX', 'XAUUSD', 'XAGUSD'})
        for asset, bars in self.bars.items():
            with self.subTest(asset=asset):
                self.assertEqual([b['time'] for b in bars], expected_times)
                for b in bars:
                    self.assertEqual(set(b), set(FIELDS))
                    self.assertLessEqual(b['low'], min(b['open'], b['close']))
                    self.assertGreaterEqual(b['high'], max(b['open'], b['close']))

    def test_nas_failed_9ate8_does_not_erase_later_selected_range_delivery(self):
        review = self.tool('NAS')['review']
        old, later = selected(review, '08:00'), selected(review, '09:00')
        self.assertTrue(review['shift_story']['coverage']['complete'])
        self.assertEqual(old['status'], 'invalidated_by_close')
        self.assertEqual(old['invalidated_at_ny'], ny('10:00'))
        self.assertTrue(all(o['status'] == 'not_observed_before_invalidation' for o in old['objectives']))
        # Independently check the retained 9 AM H1 close is outside the 8 AM high.
        anchor = self.source('NAS100', '08:00', '09:00')
        self.assertEqual(max(b['high'] for b in anchor), 30951.08)
        self.assertEqual(self.source('NAS100', '09:00', '10:00')[-1]['close'], 30957.34)
        self.assertGreater(30957.34, max(b['high'] for b in anchor))
        transitions = review['shift_story']['range_transitions']
        self.assertEqual([(t['from_anchor_ny'], t['to_anchor_ny'], t['confirmed_at_ny']) for t in transitions],
                         [(ny('08:00'), ny('09:00'), ny('10:00'))])
        self.assertEqual(later['role'], 'selected_range')
        self.assertEqual(later['direction_observed'], 'bearish')
        self.assertEqual(later['selected_at_ny'], ny('10:00'))
        self.assertIsNone(later['invalidated_at_ny'])
        for objective, level, clock in [('midpoint', 30912.53, '11:03'),
                                        ('opposing_liquidity', 30829.47, '11:12')]:
            fact = next(o for o in later['objectives'] if o['objective'] == objective)
            self.assertAlmostEqual(fact['level'], level)
            self.assertEqual(fact['status'], 'observed_after_purge')
            self.assertEqual(fact['evidence']['bar_open_ny'], ny(clock))
            self.assert_first_low_touch('NAS100', '10:02', '12:00', level, clock)
        self.assertEqual([v['code'] for v in later['variant_evidence']['labels']], ['V1'])
        self.assertFalse(later['entry_confirmed'])

    def test_metals_boneless_delivery_uses_each_assets_own_prices_and_times(self):
        review = self.tool('gold')['review']
        pair = review['paired_smt']
        self.assertTrue(pair['paired_coverage_complete'])
        event = next(e for e in pair['events'] if e['side'] == 'buy_side')
        self.assertEqual(event['bar_open_ny'], ny('09:02'))
        self.assertEqual((event['swept_asset'], event['boneless_asset']), ('XAGUSD', 'XAUUSD'))
        self.assertFalse(event['local_purge_inferred_for_boneless_asset'])
        self.assertGreater(self.source('XAGUSD', '09:02', '09:03')[0]['high'], 62.046)
        self.assertLess(max(b['high'] for b in self.source('XAUUSD', '09:00', '10:00')), 4227.775)
        for asset, level, touched, invalid in [('XAUUSD', 4174.379, '10:28', '11:00'),
                                              ('XAGUSD', 60.789, '10:54', '12:00')]:
            fact = event['objective_status'][asset]['opposing_liquidity']
            self.assertEqual(fact['status'], 'objective_complete_while_range_valid')
            self.assertEqual(fact['level'], level)
            self.assertEqual(fact['touch_bar_open_ny'], ny(touched))
            self.assertEqual(fact['range_invalidated_at_ny'], ny(invalid))
            self.assert_first_low_touch(asset, '09:03', '12:00', level, touched)
        # The visible silver purge is wick-only on the assigned M5. Do not turn
        # genuine SMT into a fabricated inherited Model 1 body-purge identity.
        self.assertEqual(event['paired_model1']['status'], 'not_observed_while_smt_valid')
        self.assertNotIn('boneless_reference', event['paired_model1'])
        silver_model = self.source('XAGUSD', '09:00', '09:05')
        self.assertGreater(max(b['high'] for b in silver_model), 62.046)
        self.assertLess(silver_model[-1]['close'], 62.046)
        recap = review['shift_story']['recap']
        bearish = next(r for r in recap['paired_interpretation'] if r['direction'] == 'bearish')
        self.assertEqual(bearish['asset_role'], 'boneless leg')
        self.assertEqual(bearish['objective_status'], event['objective_status']['XAUUSD'])
        self.assertEqual(selected(review, '08:00')['direction_observed'], 'bullish')
        self.assertTrue(recap['local_only_spoken_summary'])
        # A later opposite-direction transient event must not hide the earlier
        # completed bearish boneless delivery by promoting local failure first.
        self.assertEqual(recap['evidence_precedence'], 'paired_delivery_then_local_chronology')
        self.assertIn('completed its sell-side of the 8:00 AM H1 range', recap['headline'])

    def test_nas_clean_and_unclean_local_function_do_not_restore_crt_validity(self):
        row = selected(self.tool('NAS')['review'], '09:00')
        cases = [('10:00', 'clean', 'midpoint_delivered_then_invalidated', 30930.59, '10:59'),
                 ('10:10', 'not_clean', 'failed_before_objectives', 30963.6, '10:52')]
        for opening, quality, crt_outcome, level, delivered in cases:
            with self.subTest(model1=opening):
                fact = model(row, opening)
                soup = fact['super_soup_structure']
                self.assertEqual(fact['identity'], 'Model 1 candle')
                self.assertEqual(soup['structural_quality'], quality)
                self.assertEqual(soup['local_crt_outcome'], crt_outcome)
                self.assertEqual(soup['local_crt_invalidated_at_ny'], ny('10:20'))
                self.assertEqual(soup['local_function_outcome'], 'opposing_liquidity_delivered')
                self.assertEqual(soup['parent_function_outcome'], 'opposing_liquidity_delivered')
                self.assertEqual(soup['variants'], [])
                self.assertIsNone(soup['local_crt_objectives']['opposing_liquidity']['evidence'])
                own = soup['local_function_objectives']['opposing_liquidity']
                self.assertEqual(own['level'], level)
                self.assertEqual(own['evidence']['bar_open_ny'], ny(delivered))
                self.assertEqual(own['relative_to_model1_invalidation'], 'after_model1_invalidation')
                self.assert_first_low_touch('NAS100', '10:20', '12:00', level, delivered)

    def test_silver_clean_super_soup_can_fail_without_any_local_objective(self):
        row = selected(self.tool('silver')['review'], '08:00')
        fact = model(row, '10:50')
        soup = fact['super_soup_structure']
        self.assertEqual(soup['structural_quality'], 'clean')
        self.assertEqual(soup['event']['bar_open_ny'], ny('10:55'))
        self.assertEqual(soup['local_crt_invalidated_at_ny'], ny('11:05'))
        self.assertEqual(soup['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(soup['local_function_outcome'], 'failed_before_objectives')
        self.assertEqual(soup['variants'], [])
        self.assertTrue(all(o['evidence'] is None for o in soup['local_function_objectives'].values()))
        # Earlier bearish parent delivery and this later bullish local failure
        # are different claims; neither is replaced by the other.
        self.assertEqual(row['objectives'][1]['evidence']['bar_open_ny'], ny('10:54'))
        self.assertEqual(fact['direction'], 'bullish')

    def test_blessed_thief_keeps_subsequent_open_and_delayed_directional_revisit(self):
        review = self.tool('NAS', anchor='09:00')['review']
        sequence = review['blessed_thief']
        self.assertEqual(sequence['timeframe'], 'H1')
        self.assertEqual(sequence['status'], 'objective_reached')
        self.assertEqual(sequence['window_end_ny'], ny('11:13'))
        self.assertEqual(sequence['total_candle_count'], 2)
        for candle, opening, price, trigger in zip(sequence['candles'], ['10:00', '11:00'],
                                                  [30957.59, 30939.59], ['10:53', '11:02']):
            self.assertEqual(candle['bar_open_ny'], ny(opening))
            self.assertEqual(candle['opening_price'], price)
            revisit = candle['open_revisit']
            self.assertEqual(revisit['status'], 'directional_revisit_observed')
            self.assertEqual(revisit['directional_trigger']['evidence']['bar_open_ny'], ny(trigger))
            self.assertFalse(candle['automatic_entry_at_open'])
            self.assertFalse(revisit['exact_fill_known'])
            self.assertEqual(candle['execution_status'], 'not_assessed')
        self.assertEqual(sequence['candles'][1]['role'], 'subsequent_candle')
        self.assertEqual(sequence['candles'][1]['source_bar_count'], 13)
        self.assertTrue(sequence['candles'][1]['forming_at_cutoff'])
        # Stop before the 11:02 minute closes: later candles still in storage
        # must not backfill a trigger or an objective into the earlier review.
        before = self.tool('NAS', anchor='09:00', through='11:02')['review']['blessed_thief']
        self.assertIsNone(before['candles'][1]['open_revisit']['directional_trigger'])
        self.assertEqual(before['objective']['status'], 'not_observed_by_cutoff')

    def test_target_requires_its_closed_source_minute_and_no_future_lookahead(self):
        for cutoff, expected in [('11:12', 'not_observed_by_cutoff'),
                                  ('11:13', 'observed_after_manipulation')]:
            with self.subTest(cutoff=cutoff):
                review = self.tool('NAS', anchor='09:00', through=cutoff)['review']
                self.assertEqual(review['blessed_thief']['objective']['status'], expected)

    def test_missing_minute_is_explicit_fault_injection_not_a_new_actual_replay(self):
        # Synthetic corruption of genuine data tests evidence limits, not a claim
        # that the broker had this gap. Keep source fixtures unchanged.
        self.store('NAS100', [b for b in self.bars['NAS100'] if b['time'] != parse_time(ny('09:59'))])
        review = self.tool('NAS')['review']
        self.assertFalse(review['shift_story']['progression_complete'])
        self.assertIsNone(review['shift_story']['active_anchor_ny'])
        self.assertEqual(review['shift_story']['range_transitions'], [])
        self.assertEqual(selected(review, '09:00')['role'], 'independent_range_context')

    def test_missing_gold_minute_cannot_inherit_silvers_completed_objective(self):
        # A second explicit fault injection: no paired coverage fabrication.
        self.store('XAUUSD', [b for b in self.bars['XAUUSD'] if b['time'] != parse_time(ny('10:28'))])
        pair = self.tool('gold')['review']['paired_smt']
        self.assertFalse(pair['paired_coverage_complete'])
        event = next(e for e in pair['events'] if e['side'] == 'buy_side')
        own = event['objective_status']['XAUUSD']['opposing_liquidity']
        self.assertNotEqual(own['status'], 'objective_complete_while_range_valid')

    def test_voice_ready_payload_preserves_truth_without_a_freeform_text_snapshot(self):
        for asset in ('NAS100', 'XAUUSD', 'XAGUSD'):
            with self.subTest(asset=asset):
                raw = self.tool(asset)
                original = deepcopy(raw)
                voice = compact_voice_tool_result('review_market_session', raw)
                self.assertEqual(raw, original)
                self.assertIn('shift_recap', voice['review'])
                self.assertEqual(voice['review']['shift_recap'], raw['review']['shift_story']['recap'])
                self.assertTrue(voice['review']['shift_recap']['spoken_summary'])
                self.assertEqual(voice['review']['paired_smt'], raw['review']['paired_smt'])
                for compact, full in zip(voice['review']['shift_story']['ranges'], raw['review']['shift_story']['ranges']):
                    for key in ('status', 'role', 'invalidated_at_ny', 'objectives', 'variant_evidence',
                                'blessed_thief', 'paired_interpretation', 'entry_confirmed'):
                        self.assertEqual(compact.get(key), full.get(key), (asset, key))
                    self.assertEqual(compact['candle_lifecycle'].get('spoken_summary'), full['candle_lifecycle'].get('spoken_summary'))
                    if full['candle_lifecycle']['purge_candles']:
                        self.assertTrue(compact['candle_lifecycle']['spoken_summary'])
                    for c, f in zip(compact['candle_lifecycle']['purge_candles'], full['candle_lifecycle']['purge_candles']):
                        for key in ('identity', 'bar_open_ny', 'model1_crt_invalidating_close', 'super_soup_structure'):
                            self.assertEqual(c.get(key), f.get(key), (asset, key))


if __name__ == '__main__':
    unittest.main()
