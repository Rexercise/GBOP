"""Broker-portable objective identity and assigned-candle-first evidence contracts."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_evidence import (
    ASSIGNED, assigned_purge_evidence, crt_review, next_boundary, parse_time, stamp,
)
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.market_context import LIFECYCLE_PROMPT
from gbop_voice_web.market_data import asset_name, attach_lifecycle, MARKET_PROMPT, LIVE_MARKET_PROMPT
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_super_soup_function import review, MODEL, CLEAN, INVALID, LOCAL_TARGET

START = parse_time('2026-10-02T09:00:00-04:00')
T = START + 3600


def bar(t, o=95, h=100, l=90, c=95):
    return dict(time=t, open=o, high=h, low=l, close=c)


def minute_fixture(*, close=102):
    anchor = [bar(t) for t in range(START, T, 60)]
    return anchor + [bar(T, 99, 100, 98, 99), bar(T+60, 99, 100, 98, 99),
                     bar(T+120, 99, 103, 98, 102), bar(T+180, 102, 103, 98, 102),
                     bar(T+240, 102, 103, 98, close)]


class SpokenObjectiveTests(unittest.TestCase):
    def test_range_side_precedes_optional_broker_price_and_nested_identity_is_explicit(self):
        anchor = dict(start_ny=stamp(START), timeframe='H1')
        for direction, side in [('bearish', 'sell-side'), ('bullish', 'buy-side')]:
            self.assertEqual(objective_identity('opposing_liquidity', direction, anchor)['spoken_label'],
                             f'{side} of the 9:00 AM H1 range')
        model = dict(bar_open_ny=stamp(T), timeframe='M5')
        midpoint = objective_identity('midpoint', 'bearish', model, model1=True)
        self.assertEqual(midpoint['spoken_label'], '50% (midpoint) of the 10:00 AM M5 Model 1 candle')
        self.assertEqual(midpoint['level_basis'], 'provider_price_not_broker_invariant')

    def test_actual_model1_time_precedes_exact_m1_purge_time_in_voice(self):
        data = minute_fixture()
        result = attach_lifecycle(crt_review(data, START, T+300, 'H1', 60), data, T+300, 60)
        model = result['model1']['candles'][0]
        self.assertEqual(model['bar_open_ny'], stamp(T))
        self.assertEqual(model['purge_source_interval']['bar_open_ny'], stamp(T+120))
        event = next(e for e in result['events'] if e['kind'] == 'buy_side_purge')
        assigned = event['assigned_purge']
        self.assertEqual(assigned['model1_qualification'], 'model1_body_purge')
        self.assertEqual(assigned['assigned_candle']['start_ny'], stamp(T))
        text = assigned['spoken_summary']
        self.assertLess(text.index('10:00 AM M5'), text.index('10:02 AM M1'))
        self.assertIn('buy-side purge of the 9:00 AM H1 range', text)
        self.assertNotIn('10:05 AM', text)
        timeline = result['candle_lifecycle']['spoken_summary']
        self.assertLess(timeline.index('Model 1 was identified on the closure of the 10:00 AM M5 candle'),
                        timeline.index('10:02 AM M1'))
        saved = deepcopy(result)
        compact = compact_voice_tool_result('review_market_crt', {'ok': True, 'review': result})
        self.assertEqual(compact['review']['model1']['candles'][0], model)
        self.assertEqual(result, saved)

    def test_containing_wick_candle_is_not_model1_or_csd(self):
        result = crt_review(minute_fixture(close=99), START, T+300, 'H1', 60)
        event = next(e for e in result['events'] if e['kind'] == 'buy_side_purge')
        self.assertEqual(event['assigned_purge']['model1_qualification'], 'wick_only')
        self.assertEqual(event['assigned_purge']['csd_status'], 'not_assessed')
        self.assertEqual(result['model1']['candles'], [])

    def test_forming_and_missing_assigned_candles_do_not_qualify_model1(self):
        for data, end in [(minute_fixture()[:63], T+180),
                          ([b for b in minute_fixture() if b['time'] != T+60], T+300)]:
            result = crt_review(data, START, end, 'H1', 60)
            event = next(e for e in result['events'] if e['kind'] == 'buy_side_purge')
            self.assertEqual(event['assigned_purge']['model1_qualification'], 'unverified_incomplete_assigned_candle')
            self.assertEqual(result['model1']['candles'], [])

    def test_all_canonical_mappings_and_custom_cbdr_alignment(self):
        # Only the requested observation buckets need data for this containment helper.
        # Map from each actual parent close; do not UTC-floor daily or custom sessions.
        for tf, mapped in list(ASSIGNED.items()) + [('H6', 'M30'), ('H4', 'M20')]:
            with self.subTest(tf=tf, mapped=mapped):
                start = parse_time('2026-10-01T01:00:00-04:00')
                end = next_boundary(start, tf)
                close = next_boundary(end, mapped)
                data = [bar(t, 99, 103 if t >= end+120 else 100, 98, 102 if t >= end+120 else 99)
                        for t in range(end, close, 60)]
                anchor = dict(start_ny=stamp(start), end_ny=stamp(end), timeframe=tf, high=100, low=90)
                assigned = assigned_purge_evidence(data, anchor, mapped, data[2], 'buy', close, 60)
                self.assertEqual(assigned['assigned_candle']['start_ny'], stamp(end))
                self.assertEqual(assigned['assigned_timeframe'], mapped)
                self.assertEqual(assigned['source_purge']['bar_open_ny'], stamp(end+120))
                self.assertEqual(assigned['model1_qualification'], 'model1_body_purge')

    def test_coarse_source_keeps_m5_precision(self):
        data = [bar(t) for t in range(START, T, 300)] + [bar(T, 99, 103, 98, 102)]
        result = crt_review(data, START, T+300, 'H1', 300)
        assigned = next(e for e in result['events'] if e['kind'] == 'buy_side_purge')['assigned_purge']
        self.assertNotIn('M1', assigned['spoken_summary'])
        self.assertFalse(assigned['source_purge']['exact_tick_time_known'])

    def test_body_purge_answers_own_delivery_then_invalidation_then_parent(self):
        result = review([MODEL, INVALID, LOCAL_TARGET], authoritative=True)
        text = result['candle_lifecycle']['performance_summary']
        self.assertTrue(text.startswith('The Model 1 range purge reached sell-side of the 10:00 AM M5 Model 1 candle'))
        self.assertLess(text.index('sell-side of the 10:00 AM M5 Model 1 candle'), text.index('invalidated'))
        self.assertLess(text.index('invalidated'), text.index('sell-side of the 9:00 AM H1 range'))
        self.assertIn('does not restore CRT validity', text)
        self.assertNotIn('win', text)
        body = result['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(body['super_soup_structure']['local_function_objectives']['opposing_liquidity']['level'], 98)

    def test_midpoint_before_invalidation_and_full_delivery_after_remain_distinct(self):
        result = review([MODEL, CLEAN, (102, 103, 100, 102), INVALID, LOCAL_TARGET], authoritative=True)
        text = result['candle_lifecycle']['performance_summary']
        self.assertIn('Super Soup reached sell-side of the 10:00 AM M5 Model 1 candle', text)
        self.assertIn('Before invalidation, it had reached 50% (midpoint) of the 10:00 AM M5 Model 1 candle', text)
        self.assertLess(text.index('Before invalidation'), text.index('Separately,'))

    def test_clean_structure_without_delivery_is_not_a_success(self):
        result = review([MODEL, CLEAN], authoritative=True)
        text = result['candle_lifecycle']['performance_summary']
        self.assertIn('sell-side of the 10:00 AM M5 Model 1 candle', text)
        self.assertIn('not reached by the review cutoff', text)
        self.assertNotIn('win', text)

    def test_recognized_aliases_resolve_directly_without_broad_guessing(self):
        for alias, expected in [('NAS', 'NAS100'), ('nasdaq', 'NAS100'), ('oil', 'WTI'), ('WTI', 'WTI')]:
            self.assertEqual(asset_name(alias), expected)
        for ambiguous in ('N', 'index', 'gas', 'NASS'):
            with self.assertRaises(ValueError):
                asset_name(ambiguous)
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            self.assertIn('NAS/NASDAQ', prompt)
            self.assertIn('oil/USOIL', prompt)
            self.assertIn('FIRST', prompt)
            self.assertIn('Broker prices differ', prompt)
            self.assertIn('performance_summary', prompt)
        self.assertIn('containing assigned candle is not automatically', LIFECYCLE_PROMPT)


if __name__ == '__main__':
    unittest.main()
