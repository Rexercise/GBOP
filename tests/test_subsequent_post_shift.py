"""Subsequent phases extend only their already-frozen parent/qualification."""
from copy import deepcopy
import unittest

from gbop_voice_web.candle_evidence import stamp
from gbop_voice_web.market_data import session_review
from gbop_voice_web.post_shift_followthrough import build_followthrough, continuation_candidates
from test_young_lefty_delivery import START, sequence


def pending():
    rows = sequence()
    for b in rows[180:]:
        b['low'] = max(b['low'],91)
        b['open'] = max(b['open'],95)
        b['close'] = max(b['close'],100)
        b['high'] = max(b['high'],b['open'],b['close'])
    return rows


class SubsequentPostShiftTests(unittest.TestCase):
    def setUp(self):
        self.rows = pending()
        self.original = session_review(self.rows,'2026-06-11','day',60)
        self.candidates = continuation_candidates(self.original,'NAS100','USTECm')
        self.third = next(c for c in self.candidates if c['phase']=='purge_3')
        self.later = [dict(time=START+18000+i*60,open=100,high=105,low=95,close=100) for i in range(60)]
        self.later[17]['low'] = 89

    def follow(self, phase='purge_3', original=None, expected=True):
        return build_followthrough(original or self.original,self.rows+self.later,
            asset='NAS100',symbol='USTECm',anchor_start_ny=stamp(START),phase=phase,
            expected_scope_id=self.third['source_scope_id'] if expected else None,
            through=START+21600,as_of=START+21600)

    def test_only_verified_third_pending_phase_is_offered(self):
        self.assertEqual(self.third['anchor_start_ny'],stamp(START))
        self.assertEqual(self.third['direction'],'bearish')
        self.assertEqual(self.third['full_objective_level'],90)
        self.assertNotIn('purge_4',[c['phase'] for c in self.candidates])

    def test_later_delivery_preserves_frozen_scope_and_original_result(self):
        before = deepcopy(self.original)
        result = self.follow()
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.original,before)
        frozen = result['review']['frozen_cutoff']
        self.assertEqual(frozen['context']['phase'],'purge_3')
        self.assertEqual(frozen['phase_outcome'],'midpoint_only')
        self.assertEqual(frozen['original_directional_outcome'],'opposing_liquidity_delivered')
        self.assertEqual(result['review']['appendix']['status'],'full_objective_delivered_after_cutoff')
        self.assertEqual(result['review']['appendix']['objectives']['full_objective']['evidence']['bar_open_ny'],stamp(START+19020))

    def test_malformed_or_unbounded_phase_cannot_be_qualified(self):
        for phase in (None,42,{},'purge_2','purge_03','purge_-3','purge_3x','purge_'+'9'*100000):
            with self.subTest(phase=str(phase)[:20]):
                result = self.follow(phase)
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'],'invalid_followthrough_phase')
        result = self.follow('purge_999')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'range_not_qualified_pending_at_shift_cutoff')

    def test_later_confirmation_cannot_create_cutoff_qualification(self):
        changed = deepcopy(self.original)
        row = next(o['evidence'] for o in changed['observations'] if o['play']=='Young Lefty')
        row['double_purge']['continuation']['legs'][0]['confirmation']['known_at_ny'] = stamp(START+21600)
        self.assertFalse(self.follow(original=changed)['ok'])

    def test_unqualified_final_hour_never_becomes_frozen_pending(self):
        result = build_followthrough(self.original,self.rows+self.later,asset='NAS100',symbol='USTECm',
            anchor_start_ny=stamp(START+14400),phase='original',expected_scope_id=None,
            through=START+21600,as_of=START+21600)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'range_not_qualified_pending_at_shift_cutoff')


if __name__=='__main__':
    unittest.main()
