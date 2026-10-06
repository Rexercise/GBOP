"""Synthetic cross-checks for native H1 and own-timeframe DP confirmation."""
import unittest

from gbop_voice_web.candle_evidence import crt_review
from gbop_voice_web.market_data import attach_lifecycle
from test_native_anchor_resolution import START, bars, native
from test_young_lefty_context import two_sided


class NativeDoublePurgeIntegrationTests(unittest.TestCase):
    def test_native_hour_confirms_without_fabricating_missing_last_source_bar(self):
        rows = two_sided()
        higher = native(rows, START+3600)
        del rows[119]
        result = attach_lifecycle(crt_review(rows, START, START+18000, 'H1', 60,
                                             native_h1=[higher]), rows, START+18000, 60)
        confirmation = result['opposing_purge_hourly_candle']
        self.assertTrue(confirmation['complete'])
        self.assertFalse(confirmation['source_coverage_complete'])
        dp = result['double_purge']
        self.assertTrue(dp['observed'])
        self.assertEqual(dp['confirmed_at_ny'], '2026-06-11T09:00:00-04:00')
        self.assertFalse(dp['sequence']['coverage_through_confirmation']['complete'])
        self.assertEqual(dp['reversal_thesis']['status'], 'original_side_delivered')
        self.assertEqual(dp['reversal_thesis']['objectives']['original_side']['evidence']['bar_open_ny'],
                         '2026-06-11T09:05:00-04:00')
        self.assertFalse(any(b['time'] == START+119*60 for b in rows))

    def test_custom_offset_h1_uses_own_grid_source_close(self):
        rows = bars()
        rows[95].update(high=112)
        rows[125].update(low=88)
        higher = native(rows, START+7200)
        result = attach_lifecycle(crt_review(rows, START+1800, START+18000, 'H1', 60,
                                             native_h1=[higher]), rows, START+18000, 60)
        self.assertNotIn('opposing_purge_hourly_candle', result)
        dp = result['double_purge']
        self.assertTrue(dp['observed'])
        self.assertEqual(dp['confirmed_at_ny'], '2026-06-11T09:30:00-04:00')
        self.assertEqual(dp['sequence']['selected_timeframe_return_inside']['bar_open_ny'],
                         '2026-06-11T08:30:00-04:00')


if __name__ == '__main__':
    unittest.main()
