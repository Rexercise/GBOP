"""Shared speech guidance and privacy-safe diagnostics for live review regression."""
import ast
import json
from pathlib import Path
import unittest

from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_data import MARKET_PROMPT, LIVE_MARKET_PROMPT, MARKET_RESPONSE_CONTRACT
from gbop_voice_web.market_scope_log import market_scope_log
from gbop_voice_web.market_conversation import MarketConversation
from test_market_conversation import Provider

ROOT = Path(__file__).resolve().parents[1]


class LiveResponseContractTests(unittest.TestCase):
    def test_both_market_interfaces_receive_same_answer_contract(self):
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            self.assertIn(MARKET_RESPONSE_CONTRACT, prompt)
            for phrase in ('first name the range and direction', 'midpoint only, pending',
                           'A later opposite-direction Model 1', 'same asset',
                           'No qualifying body Model 1 does not mean no setup',
                           'purge alone is not CSD', "'boneless' clearly, as bone-less"):
                self.assertIn(phrase, prompt)

    def test_shared_canon_preserves_wick_and_delivered_v2(self):
        for phrase in ('wick-purge path remains a setup',
                       'A wick origin alone does not establish Model 1 or Super Soup',
                       'Preserve that earlier V2 delivery',
                       'Historical delivery alone does not authorize pre-9 execution',
                       'Boneless qualification and delivery are separate',
                       'potential/pending boneless from the setup'):
            self.assertIn(phrase, CANONICAL_KNOWLEDGE)

    def test_scope_log_explains_requested_and_resolved_range(self):
        args = {'asset': 'NAS100', 'anchor_start_ny': '2026-10-02T09:00:00-04:00',
                'context_action': 'continue', 'detail_candle_start_ny': '2026-10-02T10:00:00-04:00'}
        result = {'ok': True, 'asset': 'NAS100', 'market_context': {
            'selection': {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'},
            'scope_id': 'scope_' + 'a'*20, 'evidence_id': 'evidence_' + 'b'*20},
            'review': {'anchor': {'start_ny': args['anchor_start_ny'], 'timeframe': 'H1'},
                'model1': {'candles': [{'bar_open_ny': args['detail_candle_start_ny'], 'timeframe': 'M5'}]}}}
        log = market_scope_log('review_market_crt', args, result,
              {'ok': False, 'status': 'voice_detail_budget_exceeded'})
        self.assertEqual(log['requested']['anchor_start_ny'], args['anchor_start_ny'])
        self.assertEqual(log['resolved']['anchor_start_ny'], args['anchor_start_ny'])
        self.assertEqual(log['model1_identities'][0]['bar_open_ny'], args['detail_candle_start_ny'])
        self.assertTrue(log['ok'])
        self.assertFalse(log['voice_ok'])
        self.assertEqual(log['voice_status'], 'voice_detail_budget_exceeded')
        self.assertEqual(log['scope_id'], 'scope_' + 'a'*20)

    def test_real_conversation_shape_logs_pinned_identifiers(self):
        context = MarketConversation()
        context.begin_turn()
        args = {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'}
        result = context.run('review_market_session', args, Provider())
        self.assertTrue(result['ok'], result)
        log = market_scope_log('review_market_session', args, result)
        for key in ('scope_id', 'evidence_id'):
            self.assertEqual(log[key], result['market_context'][key])

    def test_diagnostics_exclude_member_records_transcripts_prices_and_errors(self):
        secret = 'PRIVATE TEST JOURNAL CONTENT'
        args = {'asset': secret, 'anchor_start_ny': secret, 'member_id': 123,
                'journal_id': 456, 'transcript': secret, 'context_action': secret}
        result = {'ok': False, 'error': secret, 'status': secret, 'price': 100.1,
                  'journal': secret, 'market_context': {'evidence': {
                      'recap': {'headline': secret}, 'scope_id': secret}},
                  'review': {'anchor': {'start_ny': secret, 'timeframe': []},
                             'model1': {'candles': [{'bar_open_ny': secret, 'timeframe': secret}]}}}
        log = market_scope_log('review_market_crt', args, result)
        encoded = json.dumps(log)
        self.assertNotIn(secret, encoded)
        for key in ('member_id', 'journal_id', 'transcript', 'price', 'error', 'recap'):
            self.assertNotIn(key, encoded)
        self.assertIsNone(market_scope_log('get_journal_history', args, result))
        self.assertIsNone(market_scope_log('open_trade', args, result))

    def test_log_bounds_identities_and_rejects_malformed_timestamps(self):
        result = {'ok': True, 'review': {'model1': {'candles': [
            {'bar_open_ny': '2026-10-02T10:00:00-04:00', 'timeframe': 'M5'}]*30}}}
        log = market_scope_log('review_market_crt', {'date_ny': '2026-99-99',
              'through_ny': '2026-10-02T99:00:00-04:00'}, result)
        self.assertEqual(log['requested'], {})
        self.assertEqual(log['model1_count'], 30)
        self.assertEqual(len(log['model1_identities']), 8)

    def test_both_runtime_paths_use_allowlisted_logger(self):
        for filename in ('bot.py', 'gbop_voice_web/server.py'):
            tree = ast.parse((ROOT / filename).read_text())
            self.assertTrue(any(isinstance(n, ast.ImportFrom)
                and n.module == 'gbop_voice_web.market_scope_log' for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
