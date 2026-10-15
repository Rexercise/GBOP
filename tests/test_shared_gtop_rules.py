"""Owner-approved boundary cases through real GBOP consumers and native Pine QA."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from gbop_voice_web import shared_gtop_rules as rules
from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp
from gbop_voice_web.chronological_context import attach_qualification
from gbop_voice_web.double_purge import _selected_return
from gbop_voice_web.shift_narrative import attach_directional_outcome

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'rules/gtop_cases.json').read_text())
START = parse_time('2026-10-02T08:00:00-04:00')


def rows(candles, mirrored=False):
    result = []
    for hour, (o, h, l, c) in enumerate(candles):
        if mirrored:
            o, h, l, c = 200-o, 200-l, 200-h, 200-c
        for n in range(12):
            # The source bars preserve the own-hour OHLC without inventing
            # a second sweep. The last source close alone is the own-hour close.
            result.append(dict(time=START+hour*3600+n*300,
                               open=o, high=h if n == 0 else max(o,c),
                               low=l if n == 0 else min(o,c), close=c))
    return result


class SharedGTOPRulesTests(unittest.TestCase):
    def test_generated_products_are_current(self):
        subprocess.run([sys.executable, str(ROOT / 'tools/build_gtop_rules.py'), '--check'],
                       cwd=ROOT, check=True, capture_output=True, text=True)

    def test_scalar_cases_shared_with_pine(self):
        for case in CASES['scalar']:
            with self.subTest(case=case['id']):
                self.assertEqual(getattr(rules, case['predicate'])(*case['args']), case['expected'])

    def test_real_qualification_and_variants_match_shared_lifecycle_cases(self):
        for case in CASES['lifecycle']:
            for mirrored in (False, True):
                with self.subTest(case=case['id'], mirrored=mirrored):
                    bars = rows([case['range']] + case['candles'], mirrored)
                    end = bars[-1]['time'] + 300
                    review = crt_review(bars, START, end, 'H1', 300)
                    attach_directional_outcome(review, bars, end, 300)
                    qualified = attach_qualification(review, bars, end, 300)
                    self.assertEqual(qualified['selected_tf_return_confirmed'], case['qualified'])
                    self.assertEqual(bool(review.get('invalidated_at_ny')), case['invalid'])
                    evidence = review['variant_evidence']
                    self.assertEqual([v['code'] for v in evidence['labels']], case['codes'])
                    self.assertEqual(evidence.get('eligible_codes') == ['V6'], case['v6_pending'])
                    if case['v6_pending']:
                        self.assertEqual([v['code'] for v in evidence['explanation']['candidates']], ['V6'])
                    if case['id'] == 'v6_edge_then_inside':
                        self.assertEqual(evidence['explanation']['known_at_ny'], stamp(end))
                    self.assertEqual(review['ruleset'], rules.RULESET)

    def test_double_purge_boundary_waits_for_later_own_close(self):
        for mirrored in (False, True):
            bars = rows([[100,110,90,100], [106,112,104,106], [106,109,89,90], [100,109,94,100]], mirrored)
            anchor = dict(timeframe='H1', end_ny=stamp(START+3600), high=110, low=90)
            opposing = bars[24]
            returned, status = _selected_return(bars, anchor, opposing, START+10800, 300)
            self.assertIsNone(returned)
            self.assertEqual(status, 'awaiting_selected_timeframe_close')
            returned, status = _selected_return(bars, anchor, opposing, START+14400, 300)
            self.assertEqual(status, 'confirmed')
            self.assertEqual(returned['known_at_ny'], stamp(START+14400))

    def test_h1_return_cannot_confirm_h4_double_purge(self):
        bars = rows([[100,110,90,100]]*4 + [[106,112,89,106]] + [[106,109,94,106]]*3)
        anchor = dict(timeframe='H4', end_ny=stamp(START+4*3600), high=110, low=90)
        opposing = bars[48]
        self.assertIsNone(_selected_return(bars, anchor, opposing, START+5*3600, 300)[0])
        returned, status = _selected_return(bars, anchor, opposing, START+8*3600, 300)
        self.assertEqual(status, 'confirmed')
        self.assertEqual(returned['timeframe'], 'H4')

    def test_full_delivery_ends_unconfirmed_v6_without_restoring_v3(self):
        bars = rows([[100,110,90,100], [106,112,104,106], [106,113,104,110], [100,109,89,100]])
        end = START + 4*3600
        review = crt_review(bars, START, end, 'H1', 300)
        attach_directional_outcome(review, bars, end, 300)
        evidence = review['variant_evidence']
        self.assertEqual(review['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(evidence['labels'], [])
        self.assertFalse(evidence.get('eligible_codes'))
        self.assertFalse(evidence['explanation'].get('candidates'))
        self.assertFalse(evidence['delivery_milestones']['opposing_liquidity']['manner'].get('primary_code'))


if __name__ == '__main__':
    unittest.main()
