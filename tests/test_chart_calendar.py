"""Chart calendar continuity: closures are explicit; missing bars stay missing."""
import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gbop_voice_web import shared_gtop_rules as rules
from gbop_voice_web.tradingview_evidence import parse_capture, review_capture
from gbop_voice_web.chart_context import parse_snapshot, FIELDS
from test_shared_chart_evidence import shared_capture, at
from test_tradingview_evidence import capture


def daily_capture():
    data = shared_capture()
    data['context'].update(chart_tf='1D', range_tf='1M', assigned_tf='1D',
                           cutoff=at(14, 21), captured_at=at(14, 21))
    parent = data['parents'][0]
    parent.update(range_open=int(datetime(2026, 9, 1, 21, tzinfo=timezone.utc).timestamp()),
                  range_end=at(1, 21), coverage='unverified')
    parent['butterfly'].update(state='source_close_pending', direction='bearish',
        purge_at=None, return_at=None, last_close_at=None, last_close=None,
        model_at=at(9, 21), cisd_at=at(12, 21),
        model_invalid_open=at(11, 22), model_invalid_at=at(12, 21))
    data['parents'] = [parent]
    data['overview'] = [dict({k: v for k, v in parent.items() if k != 'butterfly'},
                             range_tf='1M', direction='bearish')]
    # Actual source intervals deliberately include a daily maintenance closure
    # and weekends. No test depends on a symbol-name or weekday heuristic.
    windows = [(1, 2), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (11, 12), (12, 13), (13, 14)]
    previous = parent['range_end']
    data['bars'], data['calendar'] = [], []
    for start, end in windows:
        s, e = at(start, 22), at(end, 21)
        prices = [109, 113, 108, 112] if start == 8 else [112, 112, 106, 107] if start == 11 else [100, 105, 95, 100]
        data['bars'].append([s, e, *prices])
        data['calendar'].append([s, previous, s])
        previous = e
    return data


class ChartCalendarTests(unittest.TestCase):
    def test_corrected_native_nas100_capture_agrees_on_identity_validation_and_soup(self):
        raw = (Path(__file__).resolve().parents[1] / 'docs/verification/nas100-daily-model1-correction-capture.json').read_text()
        data = json.loads(raw)
        result = review_capture(raw, data['context']['captured_at'])
        self.assertFalse(result['rules_match'])  # Preserve the historical 1.2.0 capture.
        self.assertTrue(all('rule editions differ' in issue for issue in result['discrepancies']))
        evidence = result['parents'][0]['assigned_evidence']
        self.assertTrue(evidence['complete'])
        self.assertTrue(evidence['primary_model_verified'])
        self.assertEqual(evidence['model1'], [1791147600,1791234000,30890.4,31155.7,30735.8,31133.0])
        self.assertEqual(evidence['model1_trading_day'], '2026-10-05')
        self.assertEqual(evidence['cisd_trading_day'], '2026-10-12')
        self.assertEqual(evidence['model1_validation_status'], 'validated_by_cisd')
        self.assertIsNone(evidence['super_soup'])
        self.assertEqual([event['kind'] for event in result['linked_events']],
                         ['model1', 'cisd', 'model1_range_invalidated'])

    def test_native_hourly_shift_capture_preserves_variant_and_m5_events(self):
        raw = (Path(__file__).resolve().parents[1] / 'docs/verification/xauusd-hourly-shift-capture.json').read_text()
        data = json.loads(raw)
        result = review_capture(raw, data['context']['captured_at'])
        self.assertFalse(result['rules_match'])  # Historical capture retains its original rule edition.
        self.assertTrue(any('rule editions differ' in x for x in result['discrepancies']))
        parent = result['parents'][0]
        self.assertEqual(parent['range']['timeframe'], 'H1')
        self.assertEqual((parent['range']['low'], parent['range']['high']), (4130.245, 4159.5))
        self.assertEqual(parent['butterfly_reported']['variant_display'], 'Potential V3')
        self.assertEqual(parent['butterfly_reported']['variant'], 'unclassified')
        self.assertEqual(parent['butterfly_reported']['state'], 'confirmed')
        self.assertIsNone(parent['butterfly_reported']['invalid_at'])
        evidence = parent['assigned_evidence']
        self.assertTrue(evidence['complete'])
        self.assertTrue(evidence['primary_model_verified'])
        self.assertEqual(evidence['model1'], [1792070100,1792070400,4157.815,4168.72,4157.735,4163.745])
        self.assertEqual(evidence['cisd'], [1792070400,1792070700,4163.65,4163.835,4154.445,4155.4])
        self.assertEqual(evidence['cisd'], evidence['model1_own_crt_invalidating_candle'])
        self.assertEqual(parent['super_soup_range']['status'], 'invalidated')

    def test_historical_nas100_capture_exposes_late_recross_error(self):
        raw = (Path(__file__).resolve().parents[1] / 'docs/verification/nas100-daily-calendar-capture.json').read_text()
        data = json.loads(raw)
        result = review_capture(raw, data['context']['captured_at'])
        self.assertFalse(result['rules_match'])  # Historical capture retains its original rule edition.
        self.assertTrue(any('rule editions differ' in x for x in result['discrepancies']))
        parent = result['parents'][0]
        evidence = parent['assigned_evidence']
        self.assertTrue(evidence['complete'])
        self.assertTrue(evidence['primary_model_verified'])
        self.assertEqual(evidence['model1'][2:], [30890.4,31155.7,30735.8,31133.0])
        self.assertTrue(any('model_at' in x for x in result['discrepancies']))
        self.assertEqual(evidence['model1_validation_status'], 'validated_by_cisd')
        self.assertIsNone(evidence['super_soup'])
        self.assertEqual(evidence['cisd'][0], 1791752400)
        self.assertEqual(evidence['model1_own_crt_invalidating_candle'][2:], [31149.2,31400.8,31094.0,31288.0])
        self.assertEqual(parent['butterfly_reported']['state'], 'source_close_pending')

    def review(self, data):
        return review_capture(json.dumps(data), data['context']['captured_at'])['parents'][0]

    def test_daily_closures_preserve_primary_cisd_soup_and_pending_month(self):
        result = self.review(daily_capture())
        evidence = result['assigned_evidence']
        self.assertTrue(evidence['complete'])
        self.assertTrue(evidence['primary_model_verified'])
        self.assertEqual(evidence['model1'][0], at(8, 22))
        self.assertEqual(evidence['cisd'][0], at(11, 22))
        self.assertEqual(evidence['model1_own_crt_invalidating_candle'], evidence['cisd'])
        self.assertEqual(evidence['calendar_bridges'], 9)
        self.assertEqual(result['discrepancies'], [])
        self.assertEqual(result['butterfly_reported']['state'], 'source_close_pending')
        self.assertIsNone(result['butterfly_reported']['invalid_at'])

    def test_legacy_capture_does_not_assume_weekends_or_daily_closures(self):
        data = daily_capture(); del data['calendar']
        evidence = self.review(data)['assigned_evidence']
        self.assertFalse(evidence['complete'])
        self.assertFalse(evidence['primary_model_verified'])
        self.assertIsNotNone(evidence['model1'])
        self.assertIsNone(evidence['cisd'])
        self.assertIsNone(evidence['model1_own_crt_invalidating_candle'])

    def test_missing_prefix_does_not_hide_retained_cisd_or_claim_first_identity(self):
        data = daily_capture()
        data['bars'].pop(2); data['calendar'].pop(2)
        data['parents'][0]['butterfly'].update(primary_model_verified=False, assigned_complete=False)
        for bearish in (True, False):
            with self.subTest(bearish=bearish):
                result = self.review(data)
                evidence = result['assigned_evidence']
                self.assertFalse(evidence['complete'])
                self.assertFalse(evidence['primary_model_verified'])
                self.assertEqual(evidence['cisd'][0], at(11, 22))
                self.assertEqual(evidence['model1_own_crt_invalidating_candle'], evidence['cisd'])
                self.assertEqual(evidence['model1_validation_status'], 'validated_by_cisd')
                self.assertIsNone(evidence['super_soup'])
                self.assertEqual(result['discrepancies'], [])
                self.assertIn('not verified as the first Model 1', result['spoken_summary'])
                self.assertIn('CISD is confirmed', result['spoken_summary'])
                self.assertIsNone(result['butterfly_reported']['invalid_at'])
            data['parents'][0]['butterfly']['direction'] = 'bullish'
            data['bars'] = [[s, e, 200-o, 200-l, 200-h, 200-c] for s,e,o,h,l,c in data['bars']]

    def test_retained_model_still_requires_a_closed_strict_cisd(self):
        for close in (108, 109, 107):
            with self.subTest(close=close):
                data = daily_capture()
                data['bars'] = data['bars'][5:7]; data['calendar'] = data['calendar'][5:7]
                data['bars'][-1][-1] = close
                # Equality and a wick breach are not CISD. Future close excluded.
                cutoff = at(12, 21) if close != 107 else at(12, 20)
                data['context']['cutoff'] = cutoff
                data['parents'][0]['butterfly'].update(primary_model_verified=False,
                    assigned_complete=False, cisd_at=None, model_invalid_open=None, model_invalid_at=None)
                if close == 107:
                    data['bars'].pop(); data['calendar'].pop()
                evidence = self.review(data)['assigned_evidence']
                self.assertFalse(evidence['primary_model_verified'])
                self.assertIsNone(evidence['cisd'])
                self.assertIsNone(evidence['model1_own_crt_invalidating_candle'])

    def test_unknown_prefix_does_not_establish_soup_from_later_local_pattern(self):
        data = daily_capture()
        data['bars'] = data['bars'][5:7]; data['calendar'] = data['calendar'][5:7]
        data['bars'][-1][2:] = [112, 114, 109, 111]
        data['context']['cutoff'] = at(12, 21)
        evidence = self.review(data)['assigned_evidence']
        self.assertFalse(evidence['primary_model_verified'])
        self.assertIsNone(evidence['super_soup'])

    def test_gap_after_model_preserves_identity_without_inventing_later_events(self):
        data = daily_capture(); data['calendar'][6][2] -= 3600
        evidence = self.review(data)['assigned_evidence']
        self.assertTrue(evidence['primary_model_verified'])
        self.assertFalse(evidence['complete'])
        self.assertIsNone(evidence['cisd'])
        self.assertIsNone(evidence['model1_own_crt_invalidating_candle'])

    def test_unknown_prefix_stays_unverified_even_with_later_calendar_witnesses(self):
        data = daily_capture()
        data['bars'] = data['bars'][1:]; data['calendar'] = data['calendar'][1:]
        evidence = self.review(data)['assigned_evidence']
        self.assertFalse(evidence['primary_model_verified'])
        self.assertFalse(evidence['complete'])
        self.assertEqual(evidence['cisd'][0], at(11, 22))

    def test_null_mismatched_or_wrong_previous_witness_cannot_bridge(self):
        for witness in ([None, None], [at(2, 21), at(4, 23)], [at(2, 20), at(4, 22)]):
            with self.subTest(witness=witness):
                data = daily_capture(); data['calendar'][1][1:] = witness
                self.assertFalse(self.review(data)['assigned_evidence']['complete'])

    def test_native_daily_date_names_the_session_without_changing_event_times(self):
        data = daily_capture()
        for witness, row in zip(data['calendar'], data['bars']):
            witness.append(row[1] // 86400 * 86400)
        result = self.review(data)
        self.assertEqual(result['assigned_evidence']['model1_trading_day'], '2026-10-09')
        self.assertEqual(result['assigned_evidence']['model1'][0], at(8,22))
        self.assertIn('2026-10-09 daily candle', result['spoken_summary'])
        data['calendar'][0][-1] += 1
        with self.assertRaises(ValueError): self.review(data)

    def test_bad_calendar_shape_order_and_timestamps_rejected(self):
        mutations = [lambda d: d.update(calendar=None),
            lambda d: d['calendar'].pop(), lambda d: d['calendar'].reverse(),
            lambda d: d['calendar'][0].__setitem__(0, True),
            lambda d: d['calendar'][0].__setitem__(1, True),
            lambda d: d['calendar'][0].__setitem__(1, at(2, 22)),
            lambda d: d['calendar'][0].__setitem__(2, at(1, 20)),
            lambda d: d['calendar'][0].extend([0, 0])]
        for mutate in mutations:
            data = daily_capture(); mutate(data)
            with self.subTest(data=data['calendar']), self.assertRaises(ValueError):
                parse_capture(json.dumps(data), at(14, 21))

    def test_24_hour_markets_do_not_get_an_implicit_weekend_exception(self):
        data = daily_capture(); data['context']['symbol'] = 'BITSTAMP:ETHUSD'
        data['calendar'][1][2] = at(2, 21)  # next bar should have opened immediately
        self.assertFalse(self.review(data)['assigned_evidence']['complete'])

    def test_calendar_is_filtered_with_each_parent_and_validated_before_filtering(self):
        data = shared_capture()
        data['calendar'] = [[r[0], r[0], r[0]] for r in data['bars']]
        result = review_capture(json.dumps(data), at(15, 8))
        self.assertTrue(all(p['assigned_evidence']['complete'] for p in result['parents']))
        data['calendar'][0][1] = 'bad'
        with self.assertRaises(ValueError): parse_capture(json.dumps(data), at(15, 8))

    def test_missing_indicator_event_is_a_disagreement_too(self):
        data = daily_capture(); data['parents'][0]['butterfly']['cisd_at'] = None
        result = self.review(data)
        self.assertTrue(any('cisd_at' in issue and 'missing' in issue for issue in result['discrepancies']))

    def test_exact_boundary_stays_pending_across_a_closure(self):
        data = daily_capture(); data['bars'][6][-2:] = [108, 108]
        data['bars'] = data['bars'][:7]; data['calendar'] = data['calendar'][:7]
        data['context'].update(cutoff=at(12, 21))
        data['parents'][0]['butterfly'].update(cisd_at=None, model_invalid_open=None, model_invalid_at=None)
        result = self.review(data)
        self.assertTrue(result['assigned_evidence']['complete'])
        self.assertIsNone(result['assigned_evidence']['cisd'])
        self.assertEqual(result['super_soup_range']['status'], 'no_invalidating_close_observed')

    def test_unknown_projection_is_unnecessary_for_touching_candles(self):
        self.assertTrue(rules.chart_contiguous(100, 100, None, None))
        self.assertFalse(rules.chart_contiguous(100, 101, None, None))

    def test_old_active_parent_is_bounded_by_rows_while_broker_window_stays_capped(self):
        data = daily_capture()
        data['parents'][0]['range_open'] -= 366*86400
        data['parents'][0]['range_end'] -= 366*86400
        data['overview'][0].update(range_open=data['parents'][0]['range_open'], range_end=data['parents'][0]['range_end'])
        result = self.review(data)
        self.assertFalse(result['assigned_evidence']['complete'])
        self.assertFalse(result['assigned_evidence']['primary_model_verified'])
        single = dict(data['context'], **data['parents'][0], schema='gtop.chart.v1')
        legacy = {k:single[k] for k in FIELDS}
        with self.assertRaises(ValueError): parse_snapshot(json.dumps(legacy), at(14,21))
        data['bars'] *= 23
        data['calendar'] *= 23
        with self.assertRaises(ValueError): parse_capture(json.dumps(data), at(14,21))
        old = capture()
        old['range_open'] -= 365*86400; old['range_end'] -= 365*86400
        with patch('gbop_voice_web.tradingview_evidence.crt_review', side_effect=AssertionError('Do not expand old calendar history')):
            self.assertIsNone(review_capture(json.dumps(old), old['captured_at'])['independent_candle_review'])


if __name__ == '__main__': unittest.main()
