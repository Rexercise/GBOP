"""Knowledge and evidence-contract tests, not a live conversational acceptance test."""
from copy import deepcopy
import unittest

from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_context import LIFECYCLE_PROMPT, enrich_smt


def paired_review(gold_touch='2026-10-02T09:30:00-04:00'):
    def objectives(touch, midpoint, target):
        return {
            'midpoint': {'level': midpoint, 'first_later_touch_ny': touch,
                         'same_event_bar_touch_order_unknown': False},
            'opposing_liquidity': {'level': target, 'first_later_touch_ny': touch,
                                  'same_event_bar_touch_order_unknown': False},
        }
    return {
        'ok': True, 'precision_seconds': 300, 'paired_coverage_complete': True,
        'invalidating_closes_ny': {'XAUUSD': '2026-10-02T11:00:00-04:00', 'XAGUSD': None},
        'events': [{
            'direction': 'bearish', 'play_context': '9ate8', 'side': 'buy_side',
            'swept_asset': 'XAGUSD', 'nonconfirming_asset': 'XAUUSD',
            'bar_open_ny': '2026-10-02T09:15:00-04:00',
            'bar_close_ny': '2026-10-02T09:20:00-04:00',
            'anchors_valid_at_event': True,
            'objectives_after_divergence': {
                'XAUUSD': objectives(gold_touch, 100, 90),
                'XAGUSD': objectives('2026-10-02T09:35:00-04:00', 25, 20),
            },
        }],
    }


class BonelessTimeAlignmentTests(unittest.TestCase):
    def test_boneless_identity_is_same_interval_not_a_local_body_requirement(self):
        for phrase in (
            'SAME opening and closing timestamps on that SAME timeframe',
            'identity_basis=local_body_purge', 'identity_basis=smt_time_aligned',
            'It does not need to visibly body-purge its own boundary',
            'not merely a candidate',
        ):
            self.assertIn(phrase, CANONICAL_KNOWLEDGE)

    def test_exact_candles_and_local_prices_are_required(self):
        for phrase in (
            'review_market_crt or inspect_market_candles',
            "boneless candle's OWN OHLC", 'A coarse SMT detection bar',
            'Do not use a later unrelated local Model 1 instead',
            "A partner's confirmation does not automatically establish",
        ):
            self.assertIn(phrase, CANONICAL_KNOWLEDGE)

    def test_opening_label_and_closing_confirmation_are_distinct(self):
        self.assertIn('OPENING time and timeframe', CANONICAL_KNOWLEDGE)
        self.assertIn('the 10 AM H1 candle closed outside the range', CANONICAL_KNOWLEDGE)
        self.assertIn('confirming invalidation at 11 AM New York', CANONICAL_KNOWLEDGE)
        self.assertIn('the 10:45 AM M5 candle closes at 10:50 AM', CANONICAL_KNOWLEDGE)
        self.assertIn('candle_open_ny', CANONICAL_KNOWLEDGE)
        self.assertIn('confirmed_at_ny', CANONICAL_KNOWLEDGE)

    def test_existing_recap_and_independent_outcomes_remain_required(self):
        for phrase in (
            'separate directional theses', 'not as an optional afterthought',
            'Clean formation can fail; unclean formation can still perform',
            'nested CRT outcome and parent-range outcome',
            'the presence of SMT alone does not establish completion',
        ):
            self.assertIn(phrase, CANONICAL_KNOWLEDGE)

    def test_runtime_guidance_carries_identity_and_recap_rules(self):
        for phrase in (
            'OPENING time and timeframe', 'identity_basis=smt_time_aligned',
            'inspect_market_candles', 'BEFORE the overall verdict',
            'independent outcomes', 'Do not substitute a later unrelated local Model 1',
        ):
            self.assertIn(phrase, LIFECYCLE_PROMPT)

    def test_boneless_delivery_survives_later_invalidation_without_local_purge(self):
        original = paired_review()
        saved = deepcopy(original)
        result = enrich_smt(original)
        event = result['events'][0]
        self.assertEqual(event['boneless_asset'], 'XAUUSD')
        self.assertFalse(event['local_purge_inferred_for_boneless_asset'])
        self.assertEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'],
                         'objective_complete_while_range_valid')
        self.assertIn('XAUUSD completed its own opposing-liquidity objective', result['spoken_summary'])
        self.assertIn('a failed local opposite-direction attempt does not erase completed SMT delivery',
                      result['response_contract'])
        self.assertEqual(original, saved)

    def test_partner_delivery_does_not_fabricate_boneless_delivery(self):
        event = enrich_smt(paired_review(None))['events'][0]
        self.assertEqual(event['objective_status']['XAGUSD']['opposing_liquidity']['status'],
                         'objective_complete_while_range_valid')
        self.assertEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'],
                         'not_completed_before_invalidation')

    def test_late_and_same_bar_touches_keep_their_limits(self):
        for touch, expected in (
            ('2026-10-02T11:05:00-04:00', 'not_completed_before_invalidation'),
            ('2026-10-02T10:55:00-04:00', 'touch_in_invalidating_bar_order_unresolved'),
        ):
            with self.subTest(touch=touch):
                status = enrich_smt(paired_review(touch))['events'][0]['objective_status']['XAUUSD']['opposing_liquidity']['status']
                self.assertEqual(status, expected)


if __name__ == '__main__':
    unittest.main()
