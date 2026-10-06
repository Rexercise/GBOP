"""Synthetic-only regression checks for independent seven-anchor coverage."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import missing_source_intervals, parse_time
from gbop_voice_web.market_data import session_review
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS


DAY = '2026-06-04'


def source(step=60, shift='day'):
    hour = 7 if shift == 'day' else 19
    start = parse_time(f'{DAY}T{hour:02}:00:00-04:00')
    return [dict(time=t, open=100, high=110 if t < start + 3600 else 105,
                 low=90 if t < start + 3600 else 95, close=100)
            for t in range(start, start + 5 * 3600, step)]


class YoungLeftyCoverageTests(unittest.TestCase):
    def review(self, rows, step=60, shift='day'):
        return session_review(rows, DAY, shift, step)

    def test_m1_gap_is_named_without_downgrading_complete_shift(self):
        rows = source()
        del rows[17:23]
        review = self.review(rows)
        before = deepcopy(review)
        synopsis = build_shift_synopsis(review, 'NAS100')
        self.assertEqual(review, before)
        self.assertTrue(synopsis['coverage_complete'])
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertFalse(synopsis['young_lefty_relevant'])
        self.assertIn('7:00 AM H1 range is missing 6 M1 source candles', synopsis['spoken_summary'])
        self.assertIn('7:17 AM through 7:22 AM', synopsis['spoken_summary'])
        self.assertNotIn('Young Lefty: absent', synopsis['spoken_summary'])
        self.assertNotIn('Missing or unfinished candles limit', synopsis['spoken_summary'])
        coverage = synopsis['young_lefty_coverage']
        self.assertEqual((coverage['bar_count'], coverage['missing_bar_count'],
                          coverage['source_resolution_seconds']), (54, 6, 60))
        self.assertIn('no session calendar is assumed', coverage['coverage_note'])
        young = next(o['evidence'] for o in review['observations'] if o['play'] == 'Young Lefty')
        self.assertEqual(young['events'], [])
        self.assertEqual(young['variant_evidence']['reason'], 'Incomplete anchor.')
        self.assertEqual(young['variant_evidence']['labels'], [])

    def test_m5_night_gap_keeps_correct_anchor_and_resolution(self):
        rows = source(300, 'night')
        del rows[2:4]
        synopsis = self.review(rows, 300, 'night')['shift_synopsis']
        self.assertTrue(synopsis['coverage_complete'])
        self.assertIn('7:00 PM H1 range is missing 2 M5 source candles', synopsis['spoken_summary'])
        self.assertIn('7:10 PM through 7:15 PM', synopsis['spoken_summary'])
        self.assertNotIn('7:00 AM', synopsis['spoken_summary'])

    def test_single_bar_is_singular_and_not_a_false_interval(self):
        rows = source()
        del rows[8]
        synopsis = self.review(rows)['shift_synopsis']
        self.assertIn('missing 1 M1 source candle (openings: 7:08 AM, New York)',
                      synopsis['spoken_summary'])
        self.assertEqual(synopsis['young_lefty_coverage']['missing_interval_count'], 1)

    def test_separated_gaps_are_bounded_and_omissions_are_explicit(self):
        rows = source()
        rows = [r for i, r in enumerate(rows) if i not in (2, 4, 6, 8, 10)]
        synopsis = self.review(rows)['shift_synopsis']
        coverage = synopsis['young_lefty_coverage']
        self.assertEqual(coverage['missing_bar_count'], 5)
        self.assertEqual(coverage['missing_interval_count'], 5)
        self.assertEqual(len(coverage['missing_intervals']), 3)
        self.assertIn('7:02 AM; 7:04 AM; 7:06 AM; 2 more missing intervals',
                      synopsis['spoken_summary'])

    def test_count_and_resolution_survive_legacy_missing_interval_metadata(self):
        rows = source()
        del rows[20:25]
        review = self.review(rows)
        anchor = next(o['evidence']['anchor'] for o in review['observations'] if o['play'] == 'Young Lefty')
        anchor.pop('missing_intervals')
        anchor.pop('missing_interval_count')
        text = build_shift_synopsis(review)['spoken_summary']
        self.assertIn('7:00 AM H1 range is missing 5 M1 source candles.', text)
        self.assertNotIn('openings:', text)

    def test_absent_seven_hour_is_unknown_even_with_complete_eight_and_session(self):
        synopsis = self.review(source()[60:])['shift_synopsis']
        self.assertTrue(synopsis['coverage_complete'])
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertIn('missing 60 M1 source candles', synopsis['spoken_summary'])
        self.assertIn('7:00 AM through 7:59 AM', synopsis['spoken_summary'])

    def test_complete_seven_has_no_extra_payload_or_false_gap(self):
        synopsis = self.review(source())['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'absent')
        self.assertNotIn('young_lefty_coverage', synopsis)
        self.assertNotIn('source candles', synopsis['spoken_summary'])

    def test_missing_eight_does_not_claim_seven_anchor_missing(self):
        rows = source()
        del rows[70]
        review = self.review(rows)
        synopsis = review['shift_synopsis']
        self.assertFalse(review['shift_story']['progression_complete'])
        self.assertNotIn('young_lefty_coverage', synopsis)
        self.assertNotIn('7:00 AM H1 range is missing', synopsis['spoken_summary'])

    def test_voice_default_retains_exact_gap_and_stays_within_budget(self):
        rows = source()
        rows = [r for i, r in enumerate(rows) if i not in range(1, 60, 2)]
        review = self.review(rows)
        result = voice_tool_payload('review_market_session',
                                    {'ok': True, 'asset': 'NAS100', 'review': review})
        synopsis = result['review']['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_coverage']['missing_bar_count'], 30)
        self.assertIn('27 more missing intervals', synopsis['spoken_summary'])
        self.assertEqual(result['voice_view']['kind'], 'shift_synopsis')
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':'), ensure_ascii=False)),
                             SHIFT_SYNOPSIS_TARGET_CHARS)

    def test_unaligned_source_grid_does_not_invent_precise_openings(self):
        rows = source(300)
        start = rows[0]['time'] + 60
        self.assertEqual(missing_source_intervals(rows, start, start + 3600, 300), {})

    def test_forming_seven_does_not_call_future_openings_missing(self):
        synopsis = self.review(source()[:20])['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertIn('missing or unfinished seven-range evidence', synopsis['spoken_summary'])
        self.assertNotIn('young_lefty_coverage', synopsis)
        self.assertNotIn('source candles', synopsis['spoken_summary'])


if __name__ == '__main__':
    unittest.main()
