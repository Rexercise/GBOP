"""Deterministic current-payload and pre-generation regressions; no paid calls."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock, patch

from gbop_voice_web import market_data as market
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.voice_payload import voice_tool_payload
import test_voice_context_payloads as context_fixtures
from test_voice_payload_budget import expanded
from test_market_conversation import function

ROOT = Path(__file__).resolve().parents[1]
NY = lambda clock: '2026-10-02T' + clock + ':00-04:00'
QUESTION = 'For the 9 AM range, did the 10:00 AM Model 1 have a Super Soup?'


class DirectionalVoiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.replay = context_fixtures.VoiceContextPayloadTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)
        self.raw = self.replay.run_tool('review_market_session', {
            'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'})
        self.context = self.replay.context
        self.calls = []

    def run_market(self, name, args):
        self.calls.append((name, deepcopy(args)))
        return market.market_tool(self.replay.replay.db, name, args)

    def test_realistic_overview_has_named_direction_attempt_and_observed_soup(self):
        wire = voice_tool_payload('review_market_session', self.raw)
        self.assertTrue(wire['ok'], wire)
        self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), 32000)
        page = expanded(wire, wire)
        row = next(r for r in page['review']['shift_story']['ranges'] if r['anchor_start_ny'] == NY('09:00'))
        body = {x['bar_open_ny']: x for x in row['model1']['candles']}
        self.assertEqual(set(body), {NY('10:00'), NY('10:10'), NY('11:15')})
        for clock in ('10:00', '10:10'):
            self.assertEqual((body[NY(clock)]['direction'], body[NY(clock)]['purged_side'], body[NY(clock)]['purged_level']),
                             ('bearish', 'buy', 30995.59))
        late = body[NY('11:15')]
        self.assertEqual((late['direction'], late['purged_side'], late['purged_level']), ('bullish', 'sell', 30829.47))
        self.assertEqual(late['attempt_role'], 'separate_opposite_direction')
        self.assertEqual(late['source_vs_original_delivery'], 'after_delivery')
        wick = next(x for x in row['candle_lifecycle']['wick_soup_candles'] if x['bar_open_ny'] == NY('11:10'))
        self.assertEqual(wick['identity'], 'Turtle Wick Soup')
        self.assertEqual(wick['source_vs_original_delivery'], 'same_source_bar_order_unknown')
        self.assertEqual(wick['formation_vs_original_delivery'], 'at_or_after_delivery_source_close')
        soup = row['candle_lifecycle']['model1_outcomes'][0]['super_soup']
        self.assertEqual(soup['pre_csd_status'], 'observed_before_csd')
        self.assertEqual(soup['candle']['bar_open_ny'], NY('10:05'))
        self.assertEqual(soup['structure_known_at_ny'], NY('10:10'))
        self.assertNotIn('model1_identity_columns', wire['voice_view'])
        self.assertNotIn('model1_outcome_columns', wire['voice_view'])

    def test_voice_margin_tolerates_small_live_metadata_growth(self):
        grown = deepcopy(self.raw)
        grown['transport_metadata'] = 'x' * 300
        self.assertTrue(voice_tool_payload('review_market_session', grown)['ok'])
        generation = self.context.begin_turn(QUESTION)
        request = self.context.required_evidence_request()
        raw = self.context.run(request['tool'], request['args'], self.run_market, generation=generation)
        raw['transport_metadata'] = 'x' * 300
        page = voice_tool_payload('review_market_crt', raw)
        self.assertTrue(page['ok'], page)
        self.assertLessEqual(len(json.dumps(page, separators=(',', ':'))), 32000)

    def test_prefetch_supplies_exact_evidence_before_no_tool_answer(self):
        generation = self.context.begin_turn(QUESTION)
        value = prefetch_market_evidence(self.context, self.run_market, generation)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], 'review_market_crt')
        self.assertEqual(self.calls[0][1]['anchor_start_ny'], NY('09:00'))
        self.assertEqual(self.calls[0][1]['detail_candle_start_ny'], NY('10:00'))
        self.assertIn('observed_before_csd', value)
        self.assertIn(NY('10:05'), value)
        self.assertNotIn('voice_detail_budget_exceeded', value)

    def test_midpoint_question_has_bounded_measured_detail(self):
        generation = self.context.begin_turn('How far was the 8 AM range from its midpoint?')
        value = prefetch_market_evidence(self.context, self.run_market, generation)
        self.assertEqual(self.calls[0][1]['anchor_start_ny'], NY('08:00'))
        self.assertIn('"distance_price_points":7.76', value)
        self.assertIn(NY('09:32'), value)
        self.assertNotIn('voice_detail_budget_exceeded', value)

    def test_prefetch_failure_is_bounded_and_does_not_expose_exception(self):
        generation = self.context.begin_turn(QUESTION)
        value = prefetch_market_evidence(self.context, Mock(side_effect=RuntimeError('private connection query')), generation)
        self.assertIn('market_detail_read_failed', value)
        self.assertNotIn('private connection query', value)

    def test_cancellation_during_prefetch_discards_evidence(self):
        generation = self.context.begin_turn(QUESTION)
        def cancelled(name, args):
            self.context.invalidate()
            return {'ok': True}
        self.assertIsNone(prefetch_market_evidence(self.context, cancelled, generation))

    def test_audio_turn_has_no_synthesized_text_or_automatic_reads(self):
        generation = self.context.begin_turn()
        runner = Mock()
        self.assertIsNone(prefetch_market_evidence(self.context, runner, generation))
        runner.assert_not_called()

    def test_browser_backend_prefetches_even_if_model_requests_no_tools(self):
        create = Mock(return_value=NS(output=[], output_text='measured result'))
        backend = function('run_backend', dict(
            PENDING_JOURNAL_DELETIONS={}, time=time, member_context=lambda _: '',
            GTOP_GUILD_ID=1, client=NS(responses=NS(create=create)),
            BACKEND_MODEL='offline-test', BACKEND_PROMPT='', TOOLS=[], json=json,
            run_tool=lambda user, name, args, token: self.run_market(name, args)))
        result = backend([{'role': 'user', 'text': QUESTION}], 2, self.context, 1)
        self.assertEqual(result, 'measured result')
        self.assertEqual([name for name, _ in self.calls], ['review_market_crt'])
        create.assert_called_once()
        self.assertIn('observed_before_csd', json.dumps(create.call_args.kwargs['input']))

    def test_discord_text_prefetches_even_if_model_requests_no_tools(self):
        create = Mock(return_value=NS(output=[], output_text='measured result'))
        tree = ast.parse((ROOT / 'bot.py').read_text())
        node = next(n for n in tree.body if getattr(n, 'name', None) == 'ai_run_turn')
        namespace = dict(GTOP_GUILD_ID=1, GBOP_AI_TOOLS=[], init_ai_db=lambda: None,
            ai_recent_messages=lambda *a, **k: [], ai_member_context=lambda _: '',
            GTOP_AI_PROMPT='', OPENAI_MODEL='offline-test', ai_client=NS(responses=NS(create=create)),
            ai_execute_tool=lambda user, name, args: self.run_market(name, args), json=json)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), namespace)
        with patch('gbop_voice_web.market_conversation.TEXT_MARKET_CONTEXTS.get', return_value=self.context):
            result = namespace['ai_run_turn'](2, QUESTION)
        self.assertEqual(result, 'measured result')
        self.assertEqual([name for name, _ in self.calls], ['review_market_crt'])
        create.assert_called_once()
        self.assertIn('observed_before_csd', json.dumps(create.call_args.kwargs['input']))


if __name__ == '__main__':
    unittest.main()
