"""Opening-based voice labels keep closing timestamps in the evidence contract."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_lifecycle import lifecycle_summary
from gbop_voice_web.market_context import LIFECYCLE_PROMPT
from gbop_voice_web.market_data import MARKET_PROMPT
from gbop_voice_web.smt_reference import closing_candle, reconcile_paired_recap
from gbop_voice_web.voice_runtime import compact_voice_tool_result
import test_candle_lifecycle as lifecycle_fixtures
from test_shift_narrative import candles
from gbop_voice_web.shift_review import review_shift
from test_smt_reference import fixture, review, bearish


class SpokenCandleNameTests(unittest.TestCase):
    def test_range_and_invalidating_candle_keep_different_names(self):
        data = candles([(100, 110, 90, 100), (100, 109, 91, 100),
                        (100, 115, 95, 112), (112, 114, 106, 112)])
        story = review_shift(data, '2026-10-02', 'day', 300)
        summary = story['recap']['spoken_summary']
        self.assertIn('The 8:00 AM range was invalidated by the closure of the 10:00 AM H1 candle', summary)
        self.assertIn('10:00 AM range became selected on that candle\'s closure', summary)
        self.assertNotIn('11:00 AM', summary)
        invalidation = next(e for e in story['ranges'][0]['events'] if e['kind'] == 'range_invalidated')
        self.assertEqual(invalidation['candle_open_ny'], '2026-10-02T10:00:00-04:00')
        self.assertEqual(invalidation['confirmed_at_ny'], '2026-10-02T11:00:00-04:00')
        self.assertEqual(story['range_transitions'][0]['confirmed_at_ny'], invalidation['confirmed_at_ny'])

    def test_lifecycle_names_confirmation_and_return_candles_by_open(self):
        result = lifecycle_fixtures.CandleLifecycleTests().review()
        before = deepcopy(result)
        summary = lifecycle_summary(result)
        self.assertIn('Model 1 was identified on the closure of the 10:00 AM M5 candle', summary)
        self.assertIn('CSD confirmed on the closure of the 10:10 AM M5 candle', summary)
        self.assertIn('returned inside the Model 1 range on the closure of the 10:05 AM M5 candle', summary)
        self.assertIn('On the closure of the 10:30 AM M5 candle, price crossed back', summary)
        self.assertNotIn('CSD confirmed at 10:15', summary)
        self.assertNotIn('10:35 AM', summary)
        self.assertNotIn('–', summary)
        self.assertEqual(result, before)

    def test_objective_uses_source_candle_not_its_close_as_touch_time(self):
        story = review_shift(candles([(100, 110, 90, 100), (106, 112, 104, 106),
                                     (106, 109, 89, 100), (100, 109, 91, 100)]),
                             '2026-10-02', 'day', 300)
        summary = story['recap']['spoken_summary']
        self.assertIn('sell-side of the 8:00 AM H1 range during the 10:00 AM M5 candle', summary)
        self.assertNotIn('10:05 AM', summary)
        target = next(o for o in story['ranges'][0]['objectives'] if o['objective'] == 'opposing_liquidity')
        self.assertEqual(target['evidence']['bar_close_ny'], '2026-10-02T10:05:00-04:00')

    def test_paired_identity_omits_close_clock_without_changing_reference(self):
        paired = review(fixture())
        reference = deepcopy(bearish(paired)['paired_model1']['boneless_reference'])
        result = {'paired_smt': paired, 'shift_story': {'recap': {}, 'ranges': []}}
        reconcile_paired_recap(result, 'XAUUSD')
        summary = result['shift_story']['recap']['spoken_summary']
        self.assertIn('the 9:15 AM M5 candle, matching', summary)
        self.assertIn('qualified setup interval', summary)
        self.assertEqual(reference['smt_qualified_at_ny'], '2026-10-02T10:00:00-04:00')
        self.assertNotIn('close at 9:20 AM', summary)
        self.assertIn('sell-side of the 8:00 AM H1 range in the candle opening 9:20 AM', summary)
        compact = compact_voice_tool_result('review_market_session', {'ok': True, 'review': result})
        self.assertEqual(bearish(compact['review']['paired_smt'])['paired_model1']['boneless_reference'], reference)
        self.assertEqual(reference['bar_close_ny'], '2026-10-02T09:20:00-04:00')

    def test_custom_opening_and_midnight_keep_exact_dates_in_evidence(self):
        custom = closing_candle('2026-10-02T13:00:00-04:00', 'H4', '2026-10-02T09:00:00-04:00')
        self.assertEqual(custom['spoken_label'], 'the closure of the 9:00 AM H4 candle')
        self.assertEqual(custom['candle_close_ny'], '2026-10-02T13:00:00-04:00')
        night = closing_candle('2026-10-03T00:00:00-04:00')
        self.assertEqual(night['spoken_label'], 'the closure of the 11:00 PM H1 candle')
        self.assertEqual(night['candle_close_ny'], '2026-10-03T00:00:00-04:00')

    def test_prompts_only_speak_closing_timestamps_on_request(self):
        self.assertIn('Give the closing timestamp only when requested', LIFECYCLE_PROMPT)
        self.assertIn('speak closing timestamps only when requested', MARKET_PROMPT)
        self.assertNotIn('confirms invalidation at 11:00', MARKET_PROMPT)
        self.assertNotIn('closes at 11 AM', LIFECYCLE_PROMPT)


if __name__ == '__main__':
    unittest.main()
