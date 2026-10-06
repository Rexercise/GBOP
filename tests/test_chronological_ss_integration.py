"""Real SS engine through chronological default recap; synthetic candles only."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.market_data import session_review
from gbop_voice_web.shift_synopsis import _local_fact
from gbop_voice_web.chronological_context import pending_sentence
from gbop_voice_web.voice_payload import voice_tool_payload
from test_super_soup_canonical_variants import review, dataset, MODEL, terminal_v2
from test_chronological_transport import expand


class ChronologicalSuperSoupIntegrationTests(unittest.TestCase):
    def test_terminal_v2_primary_is_completed_before_cisd_in_both_directions(self):
        for bullish in (False, True):
            with self.subTest(bullish=bullish):
                raw, body, bars, end = review([MODEL, terminal_v2()], bullish=bullish)
                before = deepcopy(raw)
                fact = _local_fact(raw)
                primary = fact['pending_range']['primary_body_model1']
                self.assertEqual(primary['bar_open_ny'], body['bar_open_ny'])
                self.assertEqual(primary['own_CRT_status'], 'completed')
                self.assertEqual(primary['completed_SS']['variants'][0]['code'], 'V2')
                self.assertTrue(primary['completed_SS']['completion_preserved_after_outside_close'])
                self.assertEqual(primary['strict_CISD']['status'], 'confirmed')
                text = pending_sentence(fact['pending_range'])
                self.assertLess(text.index('Kryptonite completed'), text.index('strict CISD'))
                self.assertNotIn('own CRT invalidated', text)
                self.assertEqual(raw, before)
                early, _, _, _ = review([MODEL, terminal_v2()], bullish=bullish, through=end-60)
                pending = _local_fact(early)['pending_range']['primary_body_model1']
                self.assertNotIn('completed_SS', pending)
                self.assertNotEqual(pending['strict_CISD']['status'], 'confirmed')

    def test_complete_shift_default_preserves_primary_ss_completion_and_parent_pending(self):
        for bullish in (False, True):
            bars, start, end, _ = dataset([MODEL, terminal_v2()], bullish=bullish)
            beginning = parse_time('2026-10-05T07:00:00-04:00')
            cutoff = parse_time('2026-10-05T12:00:00-04:00')
            prefix = [dict(time=t,open=100,high=150 if t < beginning+3600 else 120,
                           low=50 if t < beginning+3600 else 80,close=100)
                      for t in range(beginning,start,60)]
            price = 101 if bullish else 99
            bars = prefix + bars + [dict(time=t,open=price,high=price+.5,low=price-.5,close=price)
                                    for t in range(end,cutoff,60)]
            raw = {'ok': True, 'asset': 'NAS100', 'review': session_review(bars, '2026-10-05', 'day', 60)}
            before = deepcopy(raw)
            wire = voice_tool_payload('review_market_session', raw)
            self.assertTrue(wire['ok'], wire)
            self.assertLessEqual(len(json.dumps(wire,separators=(',',':'))),12000)
            synopsis = expand(wire)['review']['shift_synopsis']
            nine = next(f for f in synopsis['ranges'] if f['anchor_start_ny'] == stamp(start))
            self.assertEqual(nine['outcome'], 'pending_at_review_cutoff')
            primary = nine['pending_range']['primary_body_model1']
            self.assertEqual(primary['bar_open_ny'], stamp(start+3600))
            self.assertEqual(primary['completed_SS']['variants'][0]['code'], 'V2')
            self.assertIn('Super Soup V2 — Pattern Trader’s Kryptonite completed',synopsis['spoken_summary'])
            self.assertEqual(raw,before)


if __name__ == '__main__':
    unittest.main()
