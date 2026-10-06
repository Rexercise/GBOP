"""Official confirmation uses the selected timeframe, never an execution shortcut."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_evidence import interval, next_boundary, parse_time, stamp, summarize
from gbop_voice_web.double_purge import double_purge_evidence
from test_double_purge import fixture, ny, review, t


def matrix_fixture(tf, start, step):
    anchor_end = next_boundary(start, tf)
    confirmed = next_boundary(anchor_end, tf)
    bars = [dict(time=x, open=100, high=120, low=80, close=100)
            for x in range(start, anchor_end, step)]
    bars += [dict(time=x, open=90, high=95, low=85, close=90)
             for x in range(anchor_end, confirmed + step * 2, step)]
    first, opposite, returned = [next(b for b in bars if b['time'] == anchor_end + n * step)
                                 for n in (0, 1, 2)]
    first.update(open=110, high=125, low=105, close=118)
    opposite.update(open=90, high=92, low=75, close=78)
    returned.update(open=78, high=92, low=76, close=90)
    anchor = dict(summarize(bars, start, anchor_end, step), timeframe=tf)
    events = [dict(kind='buy_side_purge', **interval(first, step)),
              dict(kind='sell_side_purge', **interval(opposite, step))]
    events += [dict(kind=kind + '_observed', order_after_purge_known=True,
                   **interval(opposite, step)) for kind in ('midpoint', 'opposing_liquidity')]
    row = {'anchor': anchor, 'observed_direction': 'bearish', 'events': events,
           'invalidated_at_ny': None}
    return bars, row, confirmed


class SelectedTimeframeDoublePurgeTests(unittest.TestCase):
    def assess(self, bars, end, reviewed=None):
        return double_purge_evidence(reviewed or review(bars, end), bars, t(end), 60)

    def test_source_and_m5_returns_are_developing_until_h1_closes(self):
        bars = fixture('11:15')
        before = deepcopy(bars)
        for end in ('10:07', '10:10', '10:45', '10:59'):
            with self.subTest(end=end):
                value = self.assess(bars, end)
                self.assertFalse(value['observed'])
                self.assertTrue(value['developing'])
                self.assertIsNone(value['confirmed_at_ny'])
                self.assertIsNone(value['sequence']['known_at_ny'])
                self.assertTrue(value['original_completion_preserved'])
        value = self.assess(bars, '11:00')
        self.assertTrue(value['observed'])
        self.assertFalse(value['developing'])
        self.assertEqual(value['confirmed_at_ny'], ny('11:00'))
        self.assertEqual(value['sequence']['source_return_inside']['known_at_ny'], ny('10:07'))
        self.assertEqual(value['sequence']['assigned_return_inside']['known_at_ny'], ny('10:10'))
        self.assertEqual(value['sequence']['selected_timeframe_return_inside']['bar_open_ny'], ny('10:00'))
        self.assertEqual(value['sequence']['known_at_ny'], ny('11:00'))
        self.assertEqual(value['reversal_thesis']['window_start_ny'], ny('11:00'))
        self.assertEqual(value['reversal_thesis']['objectives']['midpoint']['status'],
                         'no_closed_post_confirmation_bars')
        self.assertEqual(bars, before)

    def test_every_selected_timeframe_uses_its_own_close_including_variable_month(self):
        for tf, start, step in [('H1', '2026-01-05T09:00:00-05:00', 60),
                                ('H4', '2026-01-05T04:00:00-05:00', 300),
                                ('D1', '2026-01-05T00:00:00-05:00', 3600),
                                ('W1', '2026-01-05T00:00:00-05:00', 86400),
                                ('MN1', '2026-01-01T00:00:00-05:00', 86400)]:
            with self.subTest(tf=tf):
                bars, row, confirmation = matrix_fixture(tf, parse_time(start), step)
                early = double_purge_evidence(row, bars, confirmation - step, step)
                self.assertFalse(early['observed'])
                self.assertTrue(early['developing'])
                official = double_purge_evidence(row, bars, confirmation, step)
                self.assertTrue(official['observed'])
                self.assertEqual(official['confirmed_at_ny'], stamp(confirmation))
                self.assertEqual(official['sequence']['selected_timeframe_return_inside']['timeframe'], tf)
                self.assertTrue(official['original_completion_preserved'])
                if tf == 'MN1':
                    self.assertEqual(official['confirmed_at_ny'], '2026-03-01T00:00:00-05:00')

    def test_preconfirmation_full_target_is_not_retrospective_delivery(self):
        bars = fixture('11:15')
        next(b for b in bars if b['time'] == t('10:59'))['high'] = 125
        value = self.assess(bars, '11:15')
        self.assertTrue(value['observed'])
        prior = value['reversal_development']['objectives']['original_side']
        self.assertEqual(prior['status'], 'observed_after_return')
        self.assertEqual(prior['evidence']['known_at_ny'], ny('11:00'))
        current = value['reversal_thesis']['objectives']['original_side']
        self.assertNotEqual(current['status'], 'observed_after_confirmation')
        self.assertIsNone(current['evidence'])
        self.assertEqual(value['reversal_thesis']['status'], 'pending_at_review_cutoff')

    def test_postconfirmation_target_survives_later_gap_and_invalidation(self):
        bars = fixture('12:02')
        next(b for b in bars if b['time'] == t('11:05'))['high'] = 125
        next(b for b in bars if b['time'] == t('11:59')).update(open=90, low=70, close=75)
        value = self.assess(bars, '12:02')
        self.assertTrue(value['observed'])
        self.assertEqual(value['confirmed_at_ny'], ny('11:00'))
        self.assertTrue(value['original_completion_preserved'])
        self.assertEqual(value['reversal_thesis']['status'], 'original_side_delivered')
        self.assertTrue(value['reversal_thesis']['earlier_delivery_preserved_after_invalidation'])
        self.assertEqual(value['reversal_thesis']['objectives']['original_side']['evidence']['known_at_ny'], ny('11:06'))
        missing = [b for b in bars if b['time'] != t('11:20')]
        with_gap = self.assess(missing, '12:02')
        self.assertEqual(with_gap['reversal_thesis']['status'], 'original_side_delivered')
        self.assertTrue(with_gap['original_completion_preserved'])

    def test_first_own_timeframe_close_outside_blocks_confirmation(self):
        bars = fixture('11:15')
        next(b for b in bars if b['time'] == t('10:59')).update(open=90, low=70, close=75)
        value = self.assess(bars, '11:15')
        self.assertFalse(value['observed'])
        self.assertFalse(value['developing'])
        self.assertEqual(value['status'], 'not_confirmed_before_range_invalidation')
        self.assertTrue(value['original_completion_preserved'])
        self.assertIsNone(value['sequence']['selected_timeframe_return_inside'])

    def test_missing_or_duplicate_confirmation_source_bar_is_unverified(self):
        for duplicate in (False, True):
            bars = fixture('11:15')
            target = next(b for b in bars if b['time'] == t('10:59'))
            bars.append(dict(target)) if duplicate else bars.remove(target)
            bars.sort(key=lambda b: b['time'])
            value = self.assess(bars, '11:15')
            self.assertFalse(value['observed'])
            self.assertEqual(value['confirmation_status'], 'unverified_incomplete_selected_timeframe_candle')
            self.assertTrue(value['original_completion_preserved'])

    def test_verified_native_h1_closure_does_not_invent_missing_source_prices(self):
        bars = fixture('11:15')
        reviewed = review(bars, '11:15')
        bars = [b for b in bars if b['time'] not in (t('10:30'), t('10:59'))]
        reviewed['opposing_purge_hourly_candle'] = dict(start_ny=ny('10:00'), end_ny=ny('11:00'),
            timeframe='H1', close=90, complete=True, source_coverage_complete=False,
            ohlc_basis='native_mt5_h1', native_ohlc_provenance={'source': 'MT5', 'timeframe': 'H1'})
        value = self.assess(bars, '11:15', reviewed)
        self.assertTrue(value['observed'])
        self.assertEqual(value['confirmed_at_ny'], ny('11:00'))
        self.assertFalse(value['sequence']['coverage_through_confirmation']['complete'])
        self.assertEqual(value['sequence']['selected_timeframe_return_inside']['confirmation_basis'], 'resolved_closed_H1')
        self.assertIsNone(value['reversal_development']['objectives']['midpoint']['distance_price_points'])
        for key, bad in [('complete', False), ('end_ny', ny('12:00')), ('timeframe', 'M5')]:
            failed = deepcopy(reviewed)
            failed['opposing_purge_hourly_candle'][key] = bad
            self.assertFalse(self.assess(bars, '11:15', failed)['observed'])
        self.assertFalse(self.assess(bars, '10:45', reviewed)['observed'])

    def test_boundary_close_counts_as_inside_but_only_at_own_tf_close(self):
        bars = fixture('11:15')
        next(b for b in bars if b['time'] == t('10:59')).update(low=80, close=80)
        value = self.assess(bars, '11:00')
        self.assertTrue(value['observed'])
        self.assertEqual(value['sequence']['selected_timeframe_return_inside']['close'], 80)

    def test_named_execution_direction_cannot_replace_physical_first_boundary_purge(self):
        bars = fixture('11:15')
        rewritten = review(bars, '11:15')
        rewritten['observed_direction'] = rewritten['direction_observed'] = 'bullish'
        rewritten['directional_outcome']['direction'] = 'bullish'
        value = self.assess(bars, '11:15', rewritten)
        self.assertFalse(value['observed'])
        self.assertEqual(value['status'], 'unverified_initiating_event_not_first_boundary_purge')


if __name__ == '__main__':
    unittest.main()
