import json
import unittest
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.shift_review import review_shift
from gbop_voice_web.market_data import session_review
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_shift_review import fixture


def candles(hours):
    start = parse_time('2026-10-02T08:00:00')
    return [dict(time=start+i*3600+n*300, open=o, high=h, low=l, close=c)
            for i, (o, h, l, c) in enumerate(hours) for n in range(12)]


class ShiftNarrativeTests(unittest.TestCase):
    def review(self, data):
        return review_shift(data, '2026-10-02', 'day', 300)

    def test_later_v1_and_delivery_lead_the_recap(self):
        r = self.review(fixture())
        self.assertEqual([x['code'] for x in r['ranges'][1]['variant_evidence']['labels']], ['V1'])
        recap = r['recap']
        self.assertIn('9ate8 failed, but the later 9:00 AM range delivered', recap['headline'])
        self.assertEqual(len(recap['selected_range_chapters']), 2)
        text = recap['selected_range_chapters'][1]['text']
        for fact in ['10:00 AM', 'selected range', 'sell-side', 'opposing liquidity', 'V1 Textbook']:
            self.assertIn(fact, text)
        self.assertIn('final H1 closed', recap['closing'])
        self.assertFalse(r['ranges'][1]['variant_evidence']['entry_confirmed'])

    def test_v2_requires_ordered_distribution_in_candle_two(self):
        data = fixture(); data[26]['high'] = 101
        r = self.review(data)['ranges'][1]['variant_evidence']
        self.assertEqual([x['code'] for x in r['labels']], ['V2'])

    def test_same_bar_target_does_not_confirm_variant(self):
        data = fixture(); data[24]['high'] = 101
        r = self.review(data)['ranges'][1]['variant_evidence']
        self.assertEqual(r['labels'], [])
        self.assertNotEqual(r['status'], 'distribution_observed')

    def test_midpoint_only_does_not_confirm_v1_or_v2(self):
        data = fixture()
        for b in data[36:]: b['high'] = 99
        r = self.review(data)['ranges'][1]['variant_evidence']
        self.assertEqual(r['labels'], [])
        self.assertEqual(r['status'], 'developing')

    def test_failed_manipulation_is_not_a_completed_variant(self):
        r = self.review(fixture())['ranges'][0]['variant_evidence']
        self.assertEqual(r['labels'], [])
        self.assertEqual(r['status'], 'invalidated')

    def test_v3_distribution_in_fourth_candle(self):
        data = candles([(100,110,90,100),(105,109,101,105),(104,109,101,105),(104,109,101,103)])
        data[12]['high'] = 112
        data[37]['low'] = 89
        r = self.review(data)['ranges'][0]['variant_evidence']
        self.assertEqual([x['code'] for x in r['labels']], ['V3'])

    def test_v4_and_v5_inside_bar_structure(self):
        for count in [1, 2]:
            data = candles([(100,110,90,100)] + [(100,109,91,100)]*3)
            purge = (count+1)*12
            data[purge].update(high=112, low=105, close=106)
            r = self.review(data)['ranges'][0]['variant_evidence']
            self.assertEqual([x['code'] for x in r['labels']], ['V4' if count == 1 else 'V5'])
            self.assertEqual(r['inside_bars_before_manipulation'], count)

    def test_v6_requires_later_soup_of_manipulation_extreme_before_distribution(self):
        data = candles([(100,110,90,100),(106,109,104,106),(106,109,104,106),(106,109,104,106)])
        data[12]['high'] = 112
        data[24]['high'] = 113
        data[37]['low'] = 89
        r = self.review(data)['ranges'][0]['variant_evidence']
        self.assertIn('V6', [x['code'] for x in r['labels']])
        self.assertIn('10:00', r['resoup_hour_ny'])
        data[24]['high'] = 111  # original boundary again, not the first soup extreme
        r = self.review(data)['ranges'][0]['variant_evidence']
        self.assertNotIn('V6', [x['code'] for x in r['labels']])

    def test_post_distribution_resoup_is_not_v6(self):
        data = candles([(100,110,90,100),(106,109,104,106),(106,109,104,106),(106,109,104,106)])
        data[12]['high'] = 112
        data[14]['low'] = 99  # midpoint before re-soup
        data[24]['high'] = 113
        self.assertNotIn('V6', [x['code'] for x in self.review(data)['ranges'][0]['variant_evidence']['labels']])

    def test_missing_bar_prevents_classification(self):
        data = fixture(); del data[28]
        r = self.review(data)
        self.assertEqual(r['ranges'][1]['variant_evidence']['labels'], [])
        self.assertIn('Missing or unfinished', r['recap']['closing'])

    def test_no_purge_does_not_invent_setup(self):
        r = self.review(candles([(100,110,90,100)] + [(100,109,91,100)]*3))
        self.assertEqual(r['ranges'][0]['variant_evidence']['labels'], [])
        self.assertIn('No purge', r['recap']['selected_range_chapters'][0]['text'])

    def test_cutoff_selection_is_not_a_new_setup(self):
        data = fixture(); data[-1].update(low=70,close=75)
        r = self.review(data)
        self.assertIn('no remaining shift candles', r['recap']['selected_range_chapters'][-1]['text'])

    def test_night_recap_preserves_pm_hours_and_midnight(self):
        r = review_shift(fixture(night=True), '2026-10-02', 'night', 300)
        self.assertIn('9:00 PM', r['recap']['headline'])
        self.assertIn('12:00 AM', r['recap']['closing'])

    def test_voice_starts_with_complete_recap_without_mutating_source(self):
        r = session_review(fixture(), '2026-10-02', 'day')
        before = json.dumps(r)
        voice = compact_voice_tool_result('review_market_session', {'ok': True, 'review': r})
        self.assertEqual(next(iter(voice['review'])), 'shift_recap')
        self.assertEqual(voice['review']['shift_recap'], r['shift_story']['recap'])
        self.assertEqual(json.dumps(r), before)
        self.assertEqual(voice['review']['shift_story']['ranges'][1]['variant_evidence'], r['shift_story']['ranges'][1]['variant_evidence'])

    def test_price_precision_is_preserved(self):
        data = fixture()
        for b in data[12:24]: b['high'] = 100.12345
        text = self.review(data)['recap']['selected_range_chapters'][1]['text']
        self.assertIn('100.12345', text)


if __name__ == '__main__': unittest.main()
