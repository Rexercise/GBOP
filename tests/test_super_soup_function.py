"""Local functional delivery is independent of CRT validity and parent delivery."""
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.voice_runtime import compact_voice_tool_result

START = parse_time('2026-10-02T09:00:00-04:00')
T = START + 3600
MODEL = (99, 103, 98, 102)
CLEAN = (102, 104, 101, 102)
INVALID = (102, 105, 101, 104)
LOCAL_TARGET = (104, 104, 97, 100)


def bars(values):
    data = [dict(time=t, open=95, high=100, low=90, close=95) for t in range(START, T, 300)]
    return data + [dict(time=T+i*300, **dict(zip(('open', 'high', 'low', 'close'), v)))
                   for i, v in enumerate(values)]


def review(values, *, missing=(), cutoff=None, authoritative=False):
    data = [b for b in bars(values) if b['time'] not in missing]
    end = cutoff or T + len(values) * 300
    result = crt_review(data, START, end, 'H1', 300)
    return attach_lifecycle(result, data, end, 300) if authoritative else result


def soup(result):
    return result['model1']['lifecycle'][0]['super_soup']


def first(result):
    return next(f for f in result['candle_lifecycle']['purge_candles'] if f['purge_type'] == 'body_soup')


class SuperSoupFunctionTests(unittest.TestCase):
    def test_invalid_model1_delivers_own_liquidity_without_parent_objectives(self):
        before = review([MODEL, INVALID])
        result = review([MODEL, INVALID, LOCAL_TARGET])
        local = soup(result)
        self.assertEqual(local['structural_quality'], 'not_clean')
        self.assertEqual(local['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(local['local_crt_objectives']['opposing_liquidity']['status'],
                         'not_observed_before_invalidation')
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['parent_function_outcome'], 'pending_at_cutoff')
        self.assertTrue(all(t['status'] == 'not_observed_by_cutoff' for t in local['parent_range_objectives'].values()))
        target = local['local_function_objectives']['opposing_liquidity']
        self.assertEqual(target['level'], 98)
        self.assertEqual(target['evidence']['bar_open_ny'], stamp(T + 600))
        self.assertEqual(target['evidence']['bar_close_ny'], stamp(T + 900))
        self.assertEqual(target['relative_to_model1_invalidation'], 'after_model1_invalidation')
        self.assertEqual(local['local_crt_invalidated_at_ny'], stamp(T + 600))
        self.assertEqual(local['variants'], [])
        self.assertEqual(before['model1']['candles'], result['model1']['candles'])
        self.assertEqual(before['model1']['lifecycle'][0]['model1_crt_invalidating_close'],
                         result['model1']['lifecycle'][0]['model1_crt_invalidating_close'])
        self.assertFalse(result['entry_confirmed'])

    def test_clean_failed_crt_can_deliver_function_later_without_completed_variant(self):
        local = soup(review([MODEL, CLEAN, INVALID, LOCAL_TARGET]))
        self.assertEqual(local['structural_quality'], 'clean')
        self.assertEqual(local['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['variants'], [])

    def test_midpoint_only_does_not_become_opposing_delivery(self):
        local = soup(review([MODEL, INVALID, (104, 104, 100, 102)]))
        self.assertEqual(local['local_function_outcome'], 'midpoint_only_at_cutoff')
        self.assertEqual(local['local_function_objectives']['midpoint']['level'], 100.5)
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['status'], 'not_observed_by_cutoff')

    def test_no_local_delivery_remains_pending_after_local_invalidation(self):
        local = soup(review([MODEL, INVALID]))
        self.assertEqual(local['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(local['local_function_outcome'], 'pending_at_cutoff')
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['relative_to_model1_invalidation'],
                         'not_observed')

    def test_no_soup_does_not_acquire_function_from_an_unrelated_touch(self):
        local = soup(review([MODEL, (102, 103, 98, 99)]))
        self.assertEqual(local['local_function_outcome'], 'not_applicable')
        self.assertNotIn('local_function_objectives', local)

    def test_same_purge_source_bar_delivery_stays_unresolved(self):
        local = soup(review([MODEL, (102, 105, 97, 104)]))
        self.assertEqual(local['local_function_outcome'], 'unverified')
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['status'],
                         'same_purge_bar_order_unresolved')

    def test_same_local_invalidating_bar_touch_does_not_claim_post_invalidation_order(self):
        result = review([MODEL, CLEAN, (102, 105, 97, 104)], authoritative=True)
        local = first(result)['super_soup_structure']
        self.assertEqual(local['local_crt_outcome'], 'unverified')
        # The touch follows the earlier purge, but its order against the later
        # invalidating close is unknown. This is physical function, not validity.
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['relative_to_model1_invalidation'],
                         'same_model1_invalidating_bar_order_unresolved')
        self.assertIn('order relative to the Model 1 invalidating close is unresolved',
                      result['candle_lifecycle']['spoken_summary'])
        self.assertNotIn('after its', result['candle_lifecycle']['spoken_summary'])

    def test_delivery_before_later_invalidation_keeps_its_chronology(self):
        local = soup(review([MODEL, CLEAN, (102, 102, 98, 99), INVALID]))
        self.assertEqual(local['local_crt_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['relative_to_model1_invalidation'],
                         'before_model1_invalidation')

    def test_missing_data_prevents_claiming_a_later_functional_delivery(self):
        local = soup(review([MODEL, INVALID, INVALID, LOCAL_TARGET], missing=(T + 600,)))
        self.assertEqual(local['local_function_outcome'], 'unverified')
        self.assertIsNone(local['local_function_objectives']['opposing_liquidity']['evidence'])

    def test_later_gap_does_not_erase_an_observed_functional_delivery(self):
        local = soup(review([MODEL, INVALID, LOCAL_TARGET], cutoff=T + 1500))
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')

    def test_review_cutoff_does_not_use_future_target_bars(self):
        local = soup(review([MODEL, INVALID, LOCAL_TARGET], cutoff=T + 600))
        self.assertEqual(local['local_function_outcome'], 'pending_at_cutoff')
        self.assertEqual(local['local_function_window_end_ny'], stamp(T + 600))

    def test_parent_invalidation_still_ends_function_observation(self):
        values = [MODEL, INVALID] + [(104, 105, 101, 104)] * 10 + [LOCAL_TARGET]
        local = soup(review(values))
        self.assertEqual(local['local_function_outcome'], 'failed_before_objectives')
        self.assertIsNone(local['local_function_objectives']['opposing_liquidity']['evidence'])
        self.assertEqual(local['local_function_window_end_ny'], stamp(T + 3600))

    def test_bullish_post_invalidation_function_is_symmetric(self):
        data = bars([MODEL, INVALID, LOCAL_TARGET])
        reflected = [dict(time=b['time'], open=200-b['open'], high=200-b['low'],
                          low=200-b['high'], close=200-b['close']) for b in data]
        local = soup(crt_review(reflected, START, T + 900, 'H1', 300))
        self.assertEqual(local['local_crt_outcome'], 'failed_before_objectives')
        self.assertEqual(local['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(local['local_function_objectives']['opposing_liquidity']['level'], 102)
        self.assertEqual(local['parent_function_outcome'], 'pending_at_cutoff')

    def test_authoritative_and_voice_views_preserve_function_and_invalidity(self):
        result = review([MODEL, INVALID, LOCAL_TARGET], authoritative=True)
        body = first(result)
        summary = result['candle_lifecycle']['spoken_summary']
        self.assertIn('structure was not clean', summary)
        self.assertIn('Model 1 range purge reached sell-side of the 10:00 AM M5 Model 1 candle', summary)
        self.assertIn('after the closure of the 10:05 AM M5 candle invalidated the Model 1 CRT', summary)
        self.assertIn('does not restore CRT validity', summary)
        self.assertIn('local_function_outcome', result['candle_lifecycle']['response_contract'])
        compact = compact_voice_tool_result('review_market_crt', {'ok': True, 'review': result})
        self.assertEqual(first(compact['review'])['super_soup_structure'], body['super_soup_structure'])
        self.assertEqual(compact['review']['candle_lifecycle']['spoken_summary'], summary)
        self.assertEqual(body['identity'], 'Model 1 candle')
        self.assertEqual(body['model1_crt_invalidating_close']['bar_close_ny'], stamp(T + 600))


if __name__ == '__main__':
    unittest.main()
