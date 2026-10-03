"""Synthetic price fixtures; no broker access, saved trades or live order writes."""
import unittest
from pathlib import Path

from gbop_voice_web.candle_evidence import (
    ASSIGNED, crt_review, model1_evidence, next_boundary, parse_time, stamp, summarize,
)
from gbop_voice_web.shift_review import review_shift

ROOT = Path(__file__).resolve().parents[1]
START = parse_time('2026-10-02T09:00:00-04:00')


def bar(t, o=95, h=100, l=90, c=95):
    return {'time': t, 'open': o, 'high': h, 'low': l, 'close': c}


def anchor_bars(step=300):
    return [bar(t) for t in range(START, START + 3600, step)]


def body(t, side='buy'):
    return bar(t, 99, 103, 98, 102) if side == 'buy' else bar(t, 91, 92, 87, 88)


class Model1IdentityTests(unittest.TestCase):
    def test_body_close_is_model1_without_any_later_candle(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [body(t)], START, t + 300, 'H1', 300)
        model = result['model1']
        self.assertEqual(model['status'], 'identified')
        self.assertFalse(result['entry_confirmed'])
        self.assertEqual(model['csd_status'], 'not_assessed')
        self.assertEqual(model['super_soup_status'], 'not_assessed')
        self.assertEqual(model['execution_status'], 'not_assessed')
        candle = model['candles'][0]
        self.assertEqual(candle['identity'], 'Model 1 candle')
        self.assertEqual(candle['timeframe'], 'M5')
        self.assertEqual(candle['bar_open_ny'], stamp(t))
        self.assertEqual(candle['bar_close_ny'], stamp(t + 300))
        self.assertEqual(candle['identified_at_ny'], stamp(t + 300))
        self.assertEqual([candle[k] for k in ('open', 'high', 'low', 'close')], [99, 103, 98, 102])
        self.assertEqual(candle['purged_level'], 100)
        self.assertEqual(candle['purged_range_start_ny'], stamp(START))
        self.assertFalse(candle['exact_tick_time_known'])

    def test_bullish_body_close_has_the_same_identity(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [body(t, 'sell')], START, t + 300, 'H1', 300)
        candle = result['model1']['candles'][0]
        self.assertEqual(candle['purged_side'], 'sell')
        self.assertEqual(candle['direction'], 'bullish')
        self.assertEqual(candle['purged_level'], 90)

    def test_later_reversal_does_not_create_or_rename_the_model1(self):
        t = START + 3600
        data = anchor_bars() + [body(t)]
        before = crt_review(data, START, t + 300, 'H1', 300)['model1']['candles']
        after = crt_review(data + [bar(t + 300, 102, 103, 96, 97)], START, t + 600, 'H1', 300)
        self.assertEqual(after['model1']['candles'], before)
        self.assertFalse(after['entry_confirmed'])

    def test_failure_preserves_observed_model1(self):
        t = START + 3600
        data = anchor_bars() + [body(t)] + [bar(x, 102, 103, 101, 102) for x in range(t + 300, t + 3600, 300)]
        result = crt_review(data, START, t + 3600, 'H1', 300)
        self.assertEqual(result['status'], 'invalidated_by_close')
        self.assertEqual(result['model1']['status'], 'identified')
        self.assertEqual(result['model1']['identified_count'], 1)

    def test_nothing_after_invalidation_is_attached_to_failed_range(self):
        t = START + 3600
        data = anchor_bars() + [body(t)] + [bar(x, 102, 103, 101, 102) for x in range(t + 300, t + 3600, 300)]
        data += [body(t + 3600, 'sell')]
        result = crt_review(data, START, t + 3900, 'H1', 300)
        self.assertEqual(result['model1']['identified_count'], 1)
        self.assertEqual(result['model1']['window_end_ny'], stamp(t + 3600))

    def test_wick_only_purge_is_not_a_body_purge(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [bar(t, 99, 103, 98, 99)], START, t + 300, 'H1', 300)
        self.assertEqual(result['model1']['status'], 'not_observed_in_complete_window')
        self.assertEqual(result['model1']['candles'], [])

    def test_close_at_level_is_not_a_close_through(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [bar(t, 99, 101, 98, 100)], START, t + 300, 'H1', 300)
        self.assertEqual(result['model1']['identified_count'], 0)

    def test_open_at_level_can_cross_and_close_through(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [bar(t, 100, 103, 99, 102)], START, t + 300, 'H1', 300)
        self.assertEqual(result['model1']['identified_count'], 1)

    def test_body_wholly_outside_does_not_cross_the_range_boundary(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [bar(t, 101, 103, 100, 102)], START, t + 300, 'H1', 300)
        self.assertEqual(result['model1']['identified_count'], 0)

    def test_missing_anchor_is_unverified_not_absent(self):
        t = START + 3600
        result = crt_review(anchor_bars()[1:] + [body(t)], START, t + 300, 'H1', 300)
        self.assertEqual(result['model1']['status'], 'unverified_incomplete_anchor')

    def test_missing_assigned_source_bar_cannot_establish_model1(self):
        t = START + 3600
        following = [body(t)] + [bar(x, 102, 103, 101, 102) for x in (t + 60, t + 180, t + 240)]
        result = crt_review(anchor_bars(60) + following, START, t + 300, 'H1', 60)
        self.assertEqual(result['model1']['status'], 'unverified_incomplete_observation')
        self.assertEqual(result['model1']['candles'], [])

    def test_forming_m5_cannot_establish_model1(self):
        t = START + 3600
        following = [body(t)] + [bar(x, 102, 103, 101, 102) for x in range(t + 60, t + 240, 60)]
        result = crt_review(anchor_bars(60) + following, START, t + 240, 'H1', 60)
        self.assertEqual(result['model1']['status'], 'unverified_incomplete_observation')
        self.assertTrue(result['model1']['forming_assigned_candle'])

    def test_m1_cross_does_not_replace_required_m5_close(self):
        t = START + 3600
        following = [body(t)] + [bar(x, 102, 103, 98, 99) for x in range(t + 60, t + 300, 60)]
        result = crt_review(anchor_bars(60) + following, START, t + 300, 'H1', 60)
        self.assertEqual(result['model1']['status'], 'not_observed_in_complete_window')

    def test_m1_aggregation_identifies_the_closed_m5_body(self):
        t = START + 3600
        following = [body(t)] + [bar(x, 102, 103, 101, 102) for x in range(t + 60, t + 300, 60)]
        result = crt_review(anchor_bars(60) + following, START, t + 300, 'H1', 60)
        candle = result['model1']['candles'][0]
        self.assertEqual(candle['timeframe'], 'M5')
        self.assertEqual(candle['source_resolution_seconds'], 60)
        self.assertEqual(candle['bar_close_ny'], stamp(t + 300))

    def test_gap_after_identified_candle_does_not_erase_it(self):
        t = START + 3600
        result = crt_review(anchor_bars() + [body(t)], START, t + 900, 'H1', 300)
        self.assertEqual(result['model1']['status'], 'identified')
        self.assertFalse(result['model1']['observation_complete'])
        self.assertEqual(result['model1']['incomplete_assigned_candles'], 2)

    def test_coarse_source_cannot_reconstruct_m5(self):
        t = START + 3600
        result = crt_review(anchor_bars(900) + [body(t)], START, t + 900, 'H1', 900)
        self.assertEqual(result['model1']['status'], 'resolution_unavailable')

    def test_all_canonical_assigned_timeframes(self):
        steps = {'M5': 300, 'M15': 900, 'H1': 3600, 'H4': 14400, 'D1': 86400}
        for tf, assigned in ASSIGNED.items():
            with self.subTest(tf=tf):
                start = parse_time('2026-09-01T00:00:00-04:00')
                stop = next_boundary(start, tf)
                end = next_boundary(stop, assigned)
                step = steps[assigned]
                data = [bar(t) for t in range(start, stop, step)] + [body(stop)]
                result = crt_review(data, start, end, tf, step)
                self.assertEqual(result['model1']['assigned_timeframe'], assigned)
                self.assertEqual(result['model1']['identified_count'], 1)

    def test_explicit_cbdr_assigned_m30(self):
        start = parse_time('2026-10-02T14:00:00-04:00')
        stop = start + 21600
        data = [bar(t) for t in range(start, stop, 1800)] + [body(stop)]
        result = crt_review(data, start, stop + 1800, 'H6', 1800, 'M30')
        self.assertEqual(result['model1']['candles'][0]['timeframe'], 'M30')

    def test_missing_mapping_requests_assigned_timeframe(self):
        data = [bar(t) for t in range(START, START + 7200, 300)] + [body(START + 7200)]
        result = crt_review(data, START, START + 7500, 'H2', 300)
        self.assertEqual(result['model1']['status'], 'assigned_timeframe_required')

    def test_empty_post_anchor_window_is_not_absence(self):
        result = crt_review(anchor_bars(), START, START + 3600, 'H1', 300)
        self.assertEqual(result['model1']['status'], 'no_observation_window')

    def test_payload_limit_discloses_remaining_candles(self):
        anchor = summarize(anchor_bars(), START, START + 3600, 300)
        t = START + 3600
        following = [body(x) for x in range(t, t + 35 * 300, 300)]
        result = model1_evidence(following, anchor, 'M5', t + 35 * 300, 300)
        self.assertEqual(result['identified_count'], 35)
        self.assertEqual(len(result['candles']), 32)
        self.assertEqual(result['next_candle_start_ny'], stamp(t + 32 * 300))

    def test_shift_exposes_candle_despite_false_execution_flag(self):
        start = START - 3600
        data = [bar(t) for t in range(start, start + 4 * 3600, 300)]
        data[12] = body(START)
        story = review_shift(data, '2026-10-02', 'day', 300)
        selected = story['ranges'][0]
        self.assertFalse(selected['entry_confirmed'])
        self.assertEqual(selected['model1']['status'], 'identified')
        self.assertEqual(selected['model1']['candles'][0]['bar_open_ny'], stamp(START))
        self.assertIn('never negates', story['limits'])

    def test_knowledge_removes_obsolete_identity_gate(self):
        canon = (ROOT / 'gbop_voice_web/gtop_knowledge.txt').read_text()
        self.assertNotIn('model 1 candidate', canon.lower())
        self.assertNotIn('model 1 confirms only', canon.lower())
        self.assertIn('MODEL 1 RESPONSE CONTRACT', canon)
        self.assertIn('model1.candles', canon)
        self.assertIn('Missing data means unverified, not absent.', canon)
        self.assertIn('H1 -> M5.', canon)
        self.assertIn('Super Soup', canon)

    def test_discord_and_browser_share_the_canonical_knowledge(self):
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            with self.subTest(path=path):
                code = (ROOT / path).read_text()
                self.assertIn('from gbop_voice_web.gtop_protocol import', code)
                self.assertIn('CANONICAL_KNOWLEDGE', code)


if __name__ == '__main__':
    unittest.main()
