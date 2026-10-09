"""Measurements retain target identity and denominators, without invented thresholds."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.objective_approach import objective_approach
from gbop_voice_web.target_approach import target_approach_measurement, owner_inducement_example
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES
from gbop_voice_web.market_data import MARKET_PROMPT, LIVE_MARKET_PROMPT
from gbop_voice_web.voice_detail import _approach


def measure(target=100, closest=97, direction='bullish', **kwargs):
    return target_approach_measurement({'kind': 'midpoint', 'level': target}, closest, direction,
                                      **kwargs)


class TargetApproachTests(unittest.TestCase):
    def test_two_denominators_are_explicit_and_independent(self):
        value = measure(reference_low=80, reference_high=120, reference_boundary=80)
        self.assertEqual(value['gap_price_points'], 3)
        self.assertEqual(value['full_range_reference']['denominator_price_points'], 40)
        self.assertEqual(value['full_range_reference']['gap_percent'], 7.5)
        path = value['boundary_to_target_reference']
        self.assertEqual(path['denominator_price_points'], 20)
        self.assertEqual(path['remaining_gap_percent'], 15)
        self.assertEqual(path['progress_percent'], 85)

    def test_mirrored_direction_has_same_distance_and_progress(self):
        bullish = measure(reference_low=80, reference_high=120, reference_boundary=80)
        bearish = measure(closest=103, direction='bearish', reference_low=80,
                          reference_high=120, reference_boundary=120)
        self.assertEqual(bullish['gap_price_points'], bearish['gap_price_points'])
        self.assertEqual(bullish['full_range_reference'], bearish['full_range_reference'])
        self.assertEqual(bullish['boundary_to_target_reference']['progress_percent'],
                         bearish['boundary_to_target_reference']['progress_percent'])

    def test_arbitrary_key_level_pda_high_low_liquidity_not_just_ce(self):
        for kind in ('PD array', 'key level', 'high', 'low', 'liquidity'):
            target = {'kind': kind, 'level': 200, 'identity': 'explicit member selected level'}
            before = deepcopy(target)
            result = target_approach_measurement(target, 198, 'bullish', reference_boundary=180)
            self.assertEqual(result['target'], target)
            self.assertEqual(result['gap_price_points'], 2)
            self.assertNotIn('full_range_reference', result)
            self.assertEqual(target, before)
            self.assertIsNot(result['target'], target)

    def test_no_denominator_is_invented_when_reference_missing(self):
        result = measure()
        self.assertNotIn('full_range_reference', result)
        self.assertNotIn('boundary_to_target_reference', result)

    def test_touch_or_cross_is_not_non_touch_inducement(self):
        for closest in (100, 102):
            result = measure(closest=closest)
            self.assertEqual(result['gap_price_points'], 0)
            self.assertEqual(result['touch_relation'], 'at_or_through_target_observed')

    def test_no_arbitrary_five_or_twenty_percent_threshold(self):
        for closest in (99.999, 98, 92, 80):
            result = measure(closest=closest, reference_low=80, reference_high=120)
            self.assertIsNone(result['numeric_inducement_threshold'])
            self.assertEqual(result['inducement_classification'], 'requires_qualitative_context')
            self.assertEqual(result['touch_relation'], 'no_touch_observed')

    def test_decimal_price_gap_avoids_float_roundoff(self):
        result = measure(target=30912.53, closest=30879.33,
                         reference_low=30829.47, reference_high=30995.59,
                         reference_boundary=30829.47)
        self.assertEqual(result['gap_price_points'], 33.2)
        self.assertAlmostEqual(result['full_range_reference']['gap_percent'], 19.9855526125692)
        self.assertAlmostEqual(result['boundary_to_target_reference']['progress_percent'], 60.0288947748615)

    def test_unreached_reference_path_progress_is_zero_not_negative(self):
        result = measure(closest=75, reference_boundary=80)
        self.assertEqual(result['boundary_to_target_reference']['progress_percent'], 0)
        self.assertEqual(result['gap_price_points'], 25)

    def test_invalid_denominators_and_unrecognized_direction_rejected(self):
        for kwargs in ({'reference_low': 80}, {'reference_high': 120},
                       {'reference_low': 120, 'reference_high': 80},
                       {'reference_low': 100, 'reference_high': 100},
                       {'reference_boundary': 100}, {'reference_boundary': 120},
                       {'direction': 'sideways'}, {'closest': float('nan')},
                       {'closest': True}, {'target': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                measure(**kwargs)
        for target in ({'level': 100}, {'kind': '', 'level': 100}, None):
            with self.assertRaises(ValueError):
                target_approach_measurement(target, 97, 'bullish')

    def test_retained_failed_eight_has_verified_three_percent_and_ninety_four_percent(self):
        fixture = json.loads((Path(__file__).parent /
            'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        raw = next(i['candles'] for i in fixture['instruments'] if i['asset'] == 'NAS100')
        bars = [dict(zip(('time', 'open', 'high', 'low', 'close'), c)) for c in raw]
        start = parse_time('2026-10-02T08:00:00-04:00')
        end = parse_time('2026-10-02T12:00:00-04:00')
        review = crt_review(bars, start, end, 'H1', 60)
        evidence = objective_approach(review, bars, end, 60)
        fact = evidence['objectives']['midpoint']
        result = fact['target_approach']
        self.assertEqual(result['target']['anchor_start_ny'], '2026-10-02T08:00:00-04:00')
        self.assertEqual(result['evidence_status'], 'closest_approach_verified')
        self.assertEqual(result['gap_price_points'], 7.76)
        self.assertAlmostEqual(result['full_range_reference']['gap_percent'], 2.9991497255932)
        self.assertAlmostEqual(result['boundary_to_target_reference']['progress_percent'], 94.0017005488135)
        compact = _approach(evidence)['objectives']['midpoint']['target_approach']
        for key in ('target', 'gap_price_points', 'full_range_reference',
                    'boundary_to_target_reference', 'numeric_inducement_threshold'):
            self.assertEqual(compact[key], result[key])
        self.assertEqual(result['inducement_classification'], 'requires_qualitative_context')

    def test_unverified_window_keeps_observed_distance_not_verified_label(self):
        start = parse_time('2026-10-02T08:00:00-04:00')
        review = {'anchor': {'start_ny': '2026-10-02T08:00:00-04:00',
            'end_ny': '2026-10-02T09:00:00-04:00', 'timeframe': 'H1',
            'complete': True, 'high': 120, 'low': 80, 'midpoint': 100},
            'observed_direction': 'bearish', 'events': [{'kind': 'buy_side_purge',
            'bar_open_ny': '2026-10-02T09:00:00-04:00', 'bar_close_ny': '2026-10-02T09:01:00-04:00'}]}
        bars = [{'time': start + 3660, 'open': 110, 'high': 115, 'low': 103, 'close': 109}]
        result = objective_approach(review, bars, start + 3900, 60)['objectives']['midpoint']
        self.assertIsNone(result['distance_price_points'])
        self.assertEqual(result['target_approach']['gap_price_points'], 3)
        self.assertEqual(result['target_approach']['evidence_status'], 'unverified_incomplete_window')

    def test_owner_example_label_requires_exact_verified_scope_not_a_percent_rule(self):
        anchor = {'start_ny': '2026-10-02T08:00:00-04:00', 'timeframe': 'H1',
                  'low': 30692.34, 'high': 30951.08}
        fact = {'status': 'closest_approach_verified', 'level': 30821.71, 'distance_price_points': 7.76,
                'closest_observed_price': 30829.47,
                'closest_source_interval': {'precision_seconds': 60,
                    'bar_open_ny': '2026-10-02T09:32:00-04:00'}}
        label = owner_inducement_example('NAS100', anchor, 'bearish', fact)
        self.assertEqual(label['basis'], 'explicit_owner_characterization')
        self.assertIsNone(label['general_numeric_threshold'])
        cases = [
            ('XAUUSD', anchor, 'bearish', fact),
            ('NAS100', anchor, 'bullish', fact),
            ('NAS100', {**anchor, 'start_ny': '2026-10-01T08:00:00-04:00'}, 'bearish', fact),
            ('NAS100', {**anchor, 'timeframe': 'H4'}, 'bearish', fact),
            ('NAS100', anchor, 'bearish', {**fact, 'status': 'unverified_incomplete_window'}),
            ('NAS100', anchor, 'bearish', {**fact, 'closest_observed_price': 30828}),
            ('NAS100', anchor, 'bearish', {**fact, 'closest_source_interval': {'precision_seconds': 300,
                'bar_open_ny': '2026-10-02T09:32:00-04:00'}}),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertIsNone(owner_inducement_example(*args))

    def test_expanded_runtime_prompt_remains_bounded_with_member_wording_contract(self):
        from gbop_voice_web.voice_policy import build_voice_instructions
        from gbop_voice_web.midpoint_preferences import MIDPOINT_PROMPT
        prompt = build_voice_instructions(CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile') + '\n\n' + MIDPOINT_PROMPT
        self.assertLess(len(prompt), 54000)
        self.assertEqual(prompt.count(CANONICAL_KNOWLEDGE), 1)
        self.assertEqual(prompt.count(MIDPOINT_PROMPT), 1)
        self.assertIn('Do not combine this with risk or other onboarding questions', MIDPOINT_PROMPT)

    def test_canonical_examples_and_no_threshold_apply_on_all_transports(self):
        self.assertIn('gtop_targets.txt', KNOWLEDGE_FILES)
        for phrase in ('SAME CRT', 'prior first-purged side',
                       'No universal numeric inducement cutoff', '7.76 points', '33.20 points',
                       'not proof of intentional manipulation', 'At their first relevant mention',
                       'Unrecognized nicknames are not saved alternatives'):
            self.assertIn(phrase, CANONICAL_KNOWLEDGE)
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            self.assertIn('Outcomes: first name the range and direction, then full delivery, midpoint only, pending, failed or unverified; then mechanism', prompt)
            self.assertIn('No glossary, universal threshold, inferred intent/profit', prompt)
            self.assertIn('original first-purged boundary', prompt)


if __name__ == '__main__':
    unittest.main()
