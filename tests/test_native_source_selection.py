"""Pure synthetic source-selection checks; no database, broker or network calls."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market

from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.current_market import _current_precision
from gbop_voice_web.market_data import (
    NATIVE_H1_SOURCE, _select_shift_history, history_bars, shift_catalog,
)
from gbop_voice_web.shift_availability import assess_shift


DAY = '2026-10-05'
SEVEN = parse_time(DAY + 'T07:00:00-04:00')
EIGHT, NINE, END = SEVEN + 3600, SEVEN + 7200, SEVEN + 5 * 3600


def candles(start=SEVEN, end=END, step=60):
    return [dict(time=t, open=100, high=105, low=95, close=100)
            for t in range(start, end, step)]


def native(start):
    return dict(time=start, open=100, high=105, low=95, close=100,
                provenance=dict(NATIVE_H1_SOURCE, asset='NAS100', symbol='TEST-NAS', captured_at=END))


def source_sets():
    return {60: candles(), 300: candles(step=300)}


def omit(sets, at, step=60):
    sets[step] = [b for b in sets[step] if b['time'] != at]


def conflict(sets, at, step=300):
    next(b for b in sets[step] if b['time'] == at)['high'] = 106


def feed(sets, hours):
    return dict(asset='NAS100', symbol='TEST-NAS', captured_at_utc=stamp(END),
                bars=sets[300], bars_m1=sets[60], native_h1_source=NATIVE_H1_SOURCE,
                bars_h1=[{k: v for k, v in b.items() if k != 'provenance'} for b in hours])


class NativeShiftSourceSelectionTests(unittest.TestCase):
    def test_conflicting_complete_coarse_anchor_cannot_displace_consistent_fine(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        conflict(sets, EIGHT)
        hours = [native(SEVEN), native(EIGHT)]
        bars, step = _select_shift_history(sets, DAY, 'day', END, hours)
        self.assertEqual(step, 60)
        self.assertEqual(assess_shift(bars, DAY, 'day', step, END, hours)['review_scope'], 'full')
        self.assertNotIn(EIGHT + 60, [b['time'] for b in bars])

    def test_native_eight_does_not_repair_eight_purge_ordering(self):
        sets = source_sets()
        omit(sets, SEVEN + 60)
        omit(sets, EIGHT + 60)
        bars, step = _select_shift_history(sets, DAY, 'day', END,
                                           [native(SEVEN), native(EIGHT)])
        self.assertEqual(step, 300)
        self.assertEqual(len(bars), 60)

    def test_missing_seven_minutes_do_not_displace_fine_complete_post_seven(self):
        sets = source_sets()
        omit(sets, SEVEN + 60)
        bars, step = _select_shift_history(sets, DAY, 'day', END,
                                           [native(SEVEN), native(EIGHT)])
        self.assertEqual(step, 60)
        self.assertNotIn(SEVEN + 60, [b['time'] for b in bars])

    def test_no_native_keeps_legacy_coarse_preference_for_complete_eight(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        for hours in (None, []):
            self.assertEqual(_select_shift_history(sets, DAY, 'day', END, hours)[1], 300)

    def test_unrelated_native_hour_does_not_change_legacy_choice(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        self.assertEqual(_select_shift_history(sets, DAY, 'day', END, [native(NINE)])[1], 300)

    def test_history_wrapper_loads_native_snapshot_without_a_database(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        conflict(sets, EIGHT)
        hours = [native(SEVEN), native(EIGHT)]
        bars, step = history_bars(None, feed(sets, hours), SEVEN, END, DAY, 'day', END)
        self.assertEqual(step, 60)
        self.assertNotIn(EIGHT + 60, [b['time'] for b in bars])

    def test_shift_catalog_scores_the_same_native_aware_source(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        conflict(sets, EIGHT)
        rows = shift_catalog(None, feed(sets, [native(SEVEN), native(EIGHT)]), END,
                             DAY, requested_only=True)
        day = next(row for row in rows if row['shift'] == 'day')
        self.assertEqual(day['source_resolution_seconds'], 60)
        self.assertEqual(day['review_scope'], 'full')
        self.assertFalse(day['anchor_source_coverage_complete'])

    def test_night_uses_nineteen_and_twenty_with_real_post_nineteen_coverage(self):
        offset = 12 * 3600
        sets = {step: [dict(b, time=b['time'] + offset) for b in candles(step=step)]
                for step in (60, 300)}
        omit(sets, EIGHT + offset + 60)
        hours = [dict(native(t + offset), provenance=dict(native(t)['provenance'], captured_at=END + offset))
                 for t in (SEVEN, EIGHT)]
        self.assertEqual(_select_shift_history(sets, DAY, 'night', END + offset, hours)[1], 300)
        conflict(sets, EIGHT + offset)
        self.assertEqual(_select_shift_history(sets, DAY, 'night', END + offset, hours)[1], 60)


class NativeCurrentSourceSelectionTests(unittest.TestCase):
    def test_current_and_scan_prefer_consistent_recovered_reference(self):
        sets = source_sets()
        omit(sets, SEVEN + 60)
        conflict(sets, SEVEN)
        for starts in ([SEVEN], [SEVEN, EIGHT, NINE]):
            bars, step = _current_precision(sets, SEVEN, END,
                                            [native(SEVEN), native(EIGHT)], starts)
            self.assertEqual(step, 60)
            self.assertNotIn(SEVEN + 60, [b['time'] for b in bars])

    def test_native_anchor_gap_does_not_reduce_post_anchor_precision_score(self):
        sets = source_sets()
        omit(sets, SEVEN + 60)
        self.assertEqual(_current_precision(sets, SEVEN, END, [native(SEVEN)], [SEVEN])[1], 60)
        self.assertEqual(_current_precision(sets, SEVEN, END)[1], 300)

    def test_post_anchor_gap_still_prefers_complete_coarse_at_equal_freshness(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        self.assertEqual(_current_precision(sets, SEVEN, END,
                                           [native(SEVEN), native(EIGHT)], [SEVEN, EIGHT])[1], 300)

    def test_relevant_anchor_starts_do_not_apply_unrelated_native_conflict(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        conflict(sets, SEVEN)
        self.assertEqual(_current_precision(sets, SEVEN, END, [native(SEVEN)], [EIGHT])[1], 300)

    def test_non_h1_and_forming_anchor_requests_keep_legacy_selection(self):
        sets = source_sets()
        omit(sets, EIGHT + 60)
        for starts in ([], [END]):
            self.assertEqual(_current_precision(sets, SEVEN, END,
                                               [native(SEVEN), native(EIGHT)], starts)[1], 300)

    def test_consistent_current_selection_keeps_newest_actual_source_end(self):
        cutoff = NINE + 56 * 60
        sets = {60: candles(end=cutoff), 300: candles(end=NINE + 55 * 60, step=300)}
        omit(sets, EIGHT + 60)
        self.assertEqual(_current_precision(sets, SEVEN, cutoff,
                                           [native(SEVEN), native(EIGHT)], [SEVEN, EIGHT])[1], 60)


class NativeExactH1ToolSelectionTests(unittest.TestCase):
    def setUp(self):
        self.sets = source_sets()
        omit(self.sets, SEVEN + 60)
        conflict(self.sets, SEVEN)
        self.hours = [native(SEVEN)]

    def tool(self, name, timeframe='H1', *, hours=None, start=SEVEN):
        snapshot = dict(feed(self.sets, self.hours if hours is None else hours), ok=True)
        args = (dict(asset='NAS100', start_ny=stamp(start), end_ny=stamp(END), timeframe=timeframe)
                if name == 'inspect_market_candles' else
                dict(asset='NAS100', anchor_start_ny=stamp(start), through_ny=stamp(END),
                     anchor_timeframe=timeframe))
        with patch.object(market, 'read_feed', side_effect=lambda *a, **k: deepcopy(snapshot)), \
                patch.dict(market.PAIRINGS, {}, clear=True):
            return market.market_tool(None, name, args, now=END)

    def test_exact_crt_keeps_current_scan_native_aware_source(self):
        selected = _current_precision(self.sets, SEVEN, END, self.hours, [SEVEN])[1]
        result = self.tool('review_market_crt')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['available_precision_seconds'], selected)
        anchor = result['review']['anchor']
        self.assertTrue(anchor['complete'])
        self.assertEqual(anchor['native_h1_status'], 'verified_closed_native')
        self.assertFalse(anchor['source_coverage_complete'])
        self.assertEqual(anchor['missing_bar_count'], 1)
        self.assertNotIn('bars_h1', result)
        self.assertNotIn('native_h1_source', result)

    def test_h1_inspection_uses_same_source_and_preserves_missing_minute(self):
        result = self.tool('inspect_market_candles', timeframe='1H')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['available_precision_seconds'], 60)
        first = result['review']['candles'][0]
        self.assertTrue(first['complete'])
        self.assertEqual(first['native_h1_status'], 'verified_closed_native')
        self.assertEqual(first['missing_bar_count'], 1)

    def test_exact_h1_without_relevant_native_keeps_legacy_selection(self):
        for name in ('review_market_crt', 'inspect_market_candles'):
            with self.subTest(name=name):
                result = self.tool(name, hours=[])
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['available_precision_seconds'], 300)
        result = self.tool('review_market_crt', hours=[native(EIGHT)])
        self.assertEqual(result['available_precision_seconds'], 300)
        result = self.tool('inspect_market_candles', start=SEVEN + 1800)
        legacy = history_bars(None, feed(self.sets, self.hours), SEVEN + 1800, END)[1]
        self.assertEqual(result['available_precision_seconds'], legacy)

    def test_non_h1_detail_never_invokes_native_source_selector(self):
        with patch('gbop_voice_web.current_market._current_precision', side_effect=AssertionError('unexpected native selection')):
            for name, frame in (('review_market_crt', 'H4'), ('inspect_market_candles', 'M5')):
                with self.subTest(name=name, timeframe=frame):
                    result = self.tool(name, timeframe=frame)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['available_precision_seconds'], 300)

    def test_fractal_history_selection_remains_unchanged(self):
        with patch('gbop_voice_web.current_market._current_precision', side_effect=AssertionError('unexpected native selection')), \
                patch('gbop_voice_web.fractal_lineage.review_fractal', return_value={'unchanged': True}) as fractal:
            result = self.tool('review_market_fractal')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['available_precision_seconds'], 300)
        self.assertEqual(fractal.call_args.args[4], 300)

    def test_smt_route_does_not_use_native_selection(self):
        with patch('gbop_voice_web.current_market._current_precision', side_effect=AssertionError('unexpected native selection')), \
                patch.object(market, 'paired_market_review', return_value={'ok': True, 'unchanged': True}) as paired:
            result = market.market_tool(None, 'review_market_smt', dict(asset='NAS100', comparison_asset='SPX',
                anchor_start_ny=stamp(SEVEN), through_ny=stamp(END), anchor_timeframe='H1'), now=END)
        self.assertEqual(result, {'ok': True, 'unchanged': True})
        paired.assert_called_once()


if __name__ == '__main__':
    unittest.main()
