"""Full Model 1 extrema, strict closes and cutoff truth across every CISD view.

Synthetic prices are deliberately labeled; retained NAS OHLC is read unchanged
from the independently checksummed market-history fixture.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp, summarize
from gbop_voice_web.directional_evidence import candidate_lifecycle_card
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.voice_runtime import compact_voice_tool_result

START = parse_time('2026-10-02T09:00:00-04:00')
T = START + 3600
FIELDS = ('time', 'open', 'high', 'low', 'close')
RULE = 'assigned_timeframe_close_strictly_beyond_model1_full_extreme'


def bar(t, o, h, l, c):
    return dict(time=t, open=o, high=h, low=l, close=c)


def scenario(values, bullish=False):
    data = [bar(t, 95, 100, 90, 95) for t in range(START, T, 300)]
    data += [bar(T + i * 300, *v) for i, v in enumerate(values)]
    if bullish:
        data = [dict(time=b['time'], open=200-b['open'], high=200-b['low'],
                     low=200-b['high'], close=200-b['close']) for b in data]
    return data


def synthetic(bullish=False):
    # Body-open cross, wick below low, exact-low close, then strict-low close.
    return scenario([(99, 103, 98, 102), (102, 103, 98.5, 98.75),
                     (98.75, 102, 97, 99), (99, 101, 97.5, 98),
                     (98, 100, 96.5, 97), (97, 100, 96.5, 98.5)], bullish)


def views(data, cutoff, step=300):
    raw = crt_review(data, START, cutoff, 'H1', step)
    legacy = raw['model1']['lifecycles'][0]['csd']
    structural = raw['model1']['lifecycle'][0]['csd']
    authoritative = attach_lifecycle(deepcopy(raw), data, cutoff, step)
    fact = authoritative['candle_lifecycle']['purge_candles'][0]
    compact = compact_voice_tool_result('review_market_crt', {'ok': True, 'review': authoritative})
    voice_fact = compact['review']['candle_lifecycle']['purge_candles'][0]
    return legacy, structural, fact, candidate_lifecycle_card(fact), voice_fact


class FullExtremeCisdTests(unittest.TestCase):
    def test_bearish_and_bullish_body_wick_equality_and_forming_do_not_confirm(self):
        for bullish in (False, True):
            for seconds, reason in [(600, 'body_open_cross'), (900, 'wick_through_extreme'),
                                    (1200, 'close_exactly_at_extreme'), (1440, 'still_forming')]:
                with self.subTest(bullish=bullish, reason=reason):
                    legacy, structural, fact, card, voice_fact = views(synthetic(bullish), T + seconds)
                    for csd in (legacy, structural, fact['csd'], voice_fact['csd']):
                        self.assertNotIn(csd['status'], ('observed', 'confirmed'))
                        self.assertIsNone(csd.get('evidence'))
                    self.assertNotIn('candle', card['csd'])
                    self.assertEqual(fact['csd']['reference_level'], 102 if bullish else 98)
                    self.assertEqual(card['csd']['reference_level'], 102 if bullish else 98)

    def test_first_strict_close_and_threshold_match_across_engines_and_voice(self):
        for bullish in (False, True):
            with self.subTest(bullish=bullish):
                legacy, structural, fact, card, voice_fact = views(synthetic(bullish), T + 1500)
                level, boundary = (102, 'high') if bullish else (98, 'low')
                self.assertEqual(legacy['status'], 'observed')
                self.assertEqual(legacy['evidence']['candle']['start_ny'], stamp(T + 1200))
                self.assertEqual(legacy['evidence']['confirmed_at_ny'], stamp(T + 1500))
                self.assertEqual(legacy['evidence']['reference_level'], level)
                self.assertEqual(structural['status'], 'confirmed')
                self.assertEqual(structural['evidence']['bar_open_ny'], stamp(T + 1200))
                self.assertEqual(structural['evidence']['bar_close_ny'], stamp(T + 1500))
                for csd in (structural, fact['csd'], voice_fact['csd']):
                    self.assertEqual(csd['reference_level'], level)
                    self.assertEqual(csd['reference_boundary'], boundary)
                    self.assertEqual(csd['rule'], RULE)
                self.assertEqual(fact['csd']['evidence']['confirmed_at_ny'], stamp(T + 1500))
                self.assertEqual(card['csd']['confirmed_at_ny'], stamp(T + 1500))
                self.assertEqual(card['csd']['reference_level'], level)
                self.assertEqual(card['csd']['reference_boundary'], boundary)
                self.assertEqual(card['csd']['rule'], 'strict_full_extreme_close')
                self.assertEqual(voice_fact['csd'], fact['csd'])

    def test_original_body_open_retest_remains_separate_from_cisd_threshold(self):
        for bullish in (False, True):
            with self.subTest(bullish=bullish):
                legacy, _, fact, _, _ = views(synthetic(bullish), T + 1800)
                self.assertEqual(legacy['status'], 'observed')
                self.assertEqual(fact['body_reference_retest']['evidence']['level'], 101 if bullish else 99)
                self.assertNotEqual(fact['body_reference_retest']['evidence']['level'], fact['csd']['reference_level'])
                self.assertEqual(fact['body_reference_retest']['evidence']['bar_open_ny'], stamp(T + 1500))


class AssignedCloseAndSoupDirectionTests(unittest.TestCase):
    def test_finer_source_close_cannot_confirm_assigned_m5_candle(self):
        data = [bar(t, 95, 100, 90, 95) for t in range(START, T, 60)]
        data += [bar(T, 99, 103, 98, 102)]
        data += [bar(T + i * 60, 102, 103, 100, 102) for i in range(1, 5)]
        # The first M1 closes below 98, but its full M5 eventually closes at 99.
        data += [bar(T + 300, 102, 102, 96, 97)]
        data += [bar(T + i * 60, 99, 102, 98, 99) for i in range(6, 10)]
        data += [bar(T + 600, 99, 101, 96, 97)]
        data += [bar(T + i * 60, 97, 100, 96, 97) for i in range(11, 15)]
        for cutoff in (T + 360, T + 600, T + 840):
            with self.subTest(cutoff=cutoff):
                legacy, structural, fact, card, voice_fact = views(data, cutoff, 60)
                for csd in (legacy, structural, fact['csd'], voice_fact['csd']):
                    self.assertNotIn(csd['status'], ('observed', 'confirmed'))
                    self.assertIsNone(csd.get('evidence'))
                self.assertNotIn('candle', card['csd'])
        legacy, structural, fact, card, _ = views(data, T + 900, 60)
        self.assertEqual(legacy['evidence']['confirmed_at_ny'], stamp(T + 900))
        self.assertEqual(structural['evidence']['bar_open_ny'], stamp(T + 600))
        self.assertEqual(fact['csd']['evidence']['bar_open_ny'], stamp(T + 600))
        self.assertEqual(card['csd']['confirmed_at_ny'], stamp(T + 900))

    def test_strict_soup_repurges_same_swept_extreme_before_cisd(self):
        for bullish in (False, True):
            for right_side in (False, True):
                with self.subTest(bullish=bullish, same_swept_extreme=right_side):
                    wick = (102, 104, 101, 102) if right_side else (102, 103, 97, 99)
                    data = scenario([(99, 103, 98, 102), wick, (102, 103, 96, 97)], bullish)
                    _, _, fact, card, voice_fact = views(data, T + 900)
                    expected = 'observed_before_csd' if right_side else 'not_observed_before_csd'
                    self.assertEqual(fact['super_soup']['status'], expected)
                    self.assertEqual(card['super_soup']['pre_csd_status'], expected)
                    self.assertEqual(voice_fact['super_soup'], fact['super_soup'])
                    self.assertEqual(fact['csd']['status'], 'confirmed')
                    if right_side:
                        self.assertEqual(fact['super_soup']['reference_level'], 97 if bullish else 103)

    def test_body_purge_and_return_keeps_unclean_delivery_without_strict_soup(self):
        for bullish in (False, True):
            with self.subTest(bullish=bullish):
                data = scenario([(99, 103, 98, 102), (102, 105, 101, 104),
                                 (104, 105, 101, 102), (102, 103, 96, 97)], bullish)
                _, _, fact, card, voice_fact = views(data, T + 1200)
                self.assertNotEqual(fact['super_soup']['status'], 'observed_before_csd')
                structure = fact['super_soup_structure']
                self.assertEqual(structure['event']['purge_form'], 'body_purge')
                self.assertEqual(structure['structural_quality'], 'not_clean')
                self.assertEqual(structure['local_function_outcome'], 'opposing_liquidity_delivered')
                self.assertEqual(structure['local_function_objectives']['opposing_liquidity']['relative_to_model1_invalidation'],
                                 'after_model1_invalidation')
                self.assertEqual(card['super_soup']['structural_quality'], 'not_clean')
                self.assertEqual(voice_fact['super_soup_structure'], structure)


class CisdAlertContractTests(unittest.TestCase):
    @staticmethod
    def events(fact):
        # Extract deterministic event metadata only; no watch registration or send.
        from gbop_voice_web.market_watch_runtime import extract_events
        return extract_events({'ok': True, 'asset': 'SYNTHETIC', 'review': {
            'anchor': {'start_ny': stamp(START)},
            'candle_lifecycle': {'purge_candles': [fact]}}})

    def test_alert_uses_strict_full_boundary_only_after_assigned_close(self):
        for bullish in (False, True):
            for cutoff in (T + 1440, T + 1500):
                with self.subTest(bullish=bullish, cutoff=cutoff):
                    _, _, fact, _, _ = views(synthetic(bullish), cutoff)
                    events = [e for e in self.events(fact) if e['kind'] == 'csd']
                    if cutoff < T + 1500:
                        self.assertEqual(events, [])
                    else:
                        self.assertEqual(len(events), 1)
                        self.assertEqual(events[0]['at'], T + 1500)
                        self.assertIn('full high 102' if bullish else 'full low 98', events[0]['text'])
                        self.assertIn('strictly beyond', events[0]['text'])
                        self.assertNotIn('body reference', events[0]['text'])

    def test_same_confirmation_candle_sweep_never_becomes_pre_cisd_alert(self):
        data = scenario([(99, 103, 98, 102), (102, 104, 96, 97)])
        _, _, fact, _, _ = views(data, T + 600)
        events = self.events(fact)
        self.assertEqual([e['kind'] for e in events].count('csd'), 1)
        self.assertNotIn('super_soup', [e['kind'] for e in events])

    def test_canonical_guidance_names_full_extrema_and_rejects_body_wick_equality(self):
        from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
        text = CANONICAL_KNOWLEDGE.lower()
        for phrase in ('full low', 'full high', 'body-open', 'wick', 'equality'):
            self.assertIn(phrase, text)


class RetainedNasFullExtremeCisdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        cls.data = [dict(zip(FIELDS, row)) for item in data['instruments']
                    if item['asset'] == 'NAS100' for row in item['candles']]

    def test_retained_prices_independently_establish_first_below_low_close(self):
        candles = {stamp(t): summarize(self.data, t, t + 300, 60) for t in range(T, T + 3900, 300)}
        model = candles[stamp(T)]
        self.assertEqual([model[k] for k in FIELDS[1:]], [30957.59, 31026.14, 30930.59, 31023.89])
        body_cross = candles[stamp(T + 3000)]  # 10:50 M5
        wick = candles[stamp(T + 3300)]  # 10:55 M5
        close = candles[stamp(T + 3600)]  # 11:00 M5
        self.assertEqual(body_cross['close'], 30939.09)
        self.assertLess(body_cross['close'], model['open'])
        self.assertGreater(body_cross['close'], model['low'])
        self.assertEqual((wick['low'], wick['close']), (30929.84, 30939.34))
        self.assertLess(wick['low'], model['low'])
        self.assertGreater(wick['close'], model['low'])
        self.assertEqual(close['close'], 30913.09)
        self.assertLess(close['close'], model['low'])
        self.assertTrue(all(c['close'] >= model['low'] for t, c in candles.items() if stamp(T) < t < stamp(T + 3600)))

    def test_stored_future_prices_cannot_confirm_before_1105(self):
        # The full noon history remains in memory for each earlier cutoff.
        for clock in ('10:55', '11:00', '11:04'):
            with self.subTest(cutoff=clock):
                cutoff = parse_time(f'2026-10-02T{clock}:00-04:00')
                legacy, structural, fact, card, voice_fact = views(self.data, cutoff, 60)
                for csd in (legacy, structural, fact['csd'], voice_fact['csd']):
                    self.assertNotIn(csd['status'], ('observed', 'confirmed'))
                    self.assertIsNone(csd.get('evidence'))
                self.assertEqual(card['csd']['reference_level'], 30930.59)
                self.assertEqual(fact['model1_crt_invalidating_close']['bar_close_ny'], stamp(T + 1200))

    def test_1100_confirmed_at_1105_preserves_1020_local_invalidation(self):
        legacy, structural, fact, card, voice_fact = views(self.data, T + 3900, 60)
        self.assertEqual(legacy['evidence']['candle']['start_ny'], stamp(T + 3600))
        self.assertEqual(structural['evidence']['bar_open_ny'], stamp(T + 3600))
        self.assertEqual(fact['csd']['evidence']['bar_open_ny'], stamp(T + 3600))
        self.assertEqual(fact['csd']['evidence']['close'], 30913.09)
        self.assertEqual(card['csd']['confirmed_at_ny'], stamp(T + 3900))
        self.assertEqual(card['csd']['reference_level'], 30930.59)
        self.assertEqual(fact['csd'], voice_fact['csd'])
        self.assertEqual(fact['model1_crt_invalidating_close']['bar_close_ny'], stamp(T + 1200))
        self.assertEqual(fact['super_soup_structure']['local_crt_outcome'], 'midpoint_delivered_then_invalidated')
        self.assertEqual(fact['super_soup']['status'], 'observed_before_csd')
        self.assertEqual(fact['super_soup_structure']['local_function_objectives']['opposing_liquidity']['relative_to_model1_invalidation'],
                         'after_model1_invalidation')


if __name__ == '__main__':
    unittest.main()
