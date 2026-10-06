"""Offline-only usage/privacy and lossless transport regression coverage."""
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.api_usage import (RealtimeUsageRecorder, log_response_usage,
                                     response_usage)
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_payload import voice_tool_payload
from test_market_conversation import auth_db, function
from test_voice_latency import method


ROOT = Path(__file__).resolve().parents[1]


def bot_function(name, namespace):
    source = ROOT / 'bot.py'
    node = next(n for n in ast.parse(source.read_text()).body if getattr(n, 'name', '') == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


def model_response(output=None):
    return NS(output=output or [], output_text='Verified receipt.', status='completed',
              usage=NS(input_tokens=120, output_tokens=12, total_tokens=132,
                       input_tokens_details=NS(cached_tokens=0),
                       output_tokens_details=NS(reasoning_tokens=0)))


class UsageTests(unittest.TestCase):
    def test_responses_sdk_fields_are_allowlisted_and_zero_is_retained(self):
        response = model_response()
        response.id = 'private-response-id'
        response.metadata = {'member_id': 42, 'private_note': 'Do not log'}
        response.usage.input_tokens_details.cache_write_tokens = 64
        row = response_usage(response, 'discord_text')
        self.assertEqual(row, dict(surface='discord_text', response_events=1,
            usage_reported=True, status='completed', input_tokens=120,
            output_tokens=12, total_tokens=132, input_cached_tokens=0,
            input_cache_write_tokens=64, output_reasoning_tokens=0))
        self.assertNotIn('private', json.dumps(row))
        self.assertNotIn('input_audio_tokens', row)

    def test_realtime_modalities_and_cache_details_are_not_added_to_totals(self):
        response = {'status': 'cancelled', 'usage': {'input_tokens': 300,
            'output_tokens': 25, 'total_tokens': 325,
            'input_token_details': {'text_tokens': 200, 'audio_tokens': 90,
                'image_tokens': 10, 'cached_tokens': 150,
                'cached_tokens_details': {'text_tokens': 140, 'audio_tokens': 10,
                                         'image_tokens': 0}},
            'output_token_details': {'text_tokens': 5, 'audio_tokens': 20}}}
        original = deepcopy(response)
        row = response_usage(response, 'discord_realtime')
        self.assertEqual(response, original)
        self.assertEqual(row['total_tokens'], 325)
        self.assertEqual(row['input_cached_tokens'], 150)
        self.assertEqual(row['input_cached_audio_tokens'], 10)
        self.assertEqual(row['input_cached_image_tokens'], 0)
        self.assertEqual(row['output_audio_tokens'], 20)
        self.assertNotIn('output_reasoning_tokens', row)

    def test_missing_usage_is_unknown_not_zero(self):
        self.assertEqual(response_usage({}, 'browser_backend'),
            {'surface': 'browser_backend', 'response_events': 1, 'usage_reported': False})
        self.assertEqual(response_usage({'usage': {}}, 'discord_realtime'),
            {'surface': 'discord_realtime', 'response_events': 1, 'usage_reported': True})

    def test_unexpected_metadata_and_malformed_counters_never_enter_logs(self):
        response = {'status': 'SECRET', 'id': 'SECRET', 'model': 'SECRET',
                    'usage': {'private': 'SECRET', 'member_id': 42,
                              'input_tokens': True, 'output_tokens': -1,
                              'total_tokens': 'SECRET',
                              'input_token_details': {'text_tokens': 1.5,
                                                      'audio_tokens': float('inf')}}}
        row = response_usage(response, 'discord_realtime')
        self.assertEqual(set(row), {'surface', 'response_events', 'usage_reported'})
        with patch('builtins.print') as emit:
            log_response_usage(response, 'discord_realtime')
        self.assertNotIn('SECRET', str(emit.call_args))
        with self.assertRaises(ValueError):
            response_usage({}, 'private-member-name')

    def test_logging_failure_cannot_change_delivery_or_confirmed_receipts(self):
        with patch('builtins.print', side_effect=OSError('Log unavailable')):
            self.assertIsNone(log_response_usage(model_response(), 'discord_text'))
        class BrokenUsage:
            @property
            def usage(self):
                raise ValueError('Unusable telemetry')
        self.assertIsNone(log_response_usage(BrokenUsage(), 'browser_backend'))

    def test_realtime_deduplication_is_bounded_and_connection_local(self):
        recorder = RealtimeUsageRecorder()
        with patch('gbop_voice_web.api_usage.log_response_usage', return_value={}) as emit:
            for number in range(300):
                response = {'id': 'private-' + str(number)}
                recorder.record(response)
                recorder.record(response)
            self.assertEqual(emit.call_count, 300)
            self.assertEqual(len(recorder._seen), 256)
            RealtimeUsageRecorder().record({'id': 'private-299'})
            self.assertEqual(emit.call_count, 301)


class TransportTests(unittest.TestCase):
    def run_surface(self, surface):
        receipt = {'ok': True, 'status': 'delivered', 'sent_count': 1,
            'partial': False, 'next_offset': None, 'receipts': [
                {'receipt_id': 'synthetic-receipt', 'message': 'Keep : ,  spacing, café and 選択',
                 'trade_number': 0, 'warning': 'Nothing else was saved.'}]}
        before = deepcopy(receipt)
        call = NS(type='function_call', name='get_delivery_status',
                  arguments='{"kind":"all","receipt_id":null}', call_id='synthetic-call')
        create = Mock(side_effect=[model_response([call]), model_response()])
        dispatch = Mock(return_value=receipt)
        common = dict(time=time, json=json, db=auth_db, GTOP_GUILD_ID=1)
        if surface == 'browser_backend':
            fn = function('run_backend', dict(common, PENDING_JOURNAL_DELETIONS={},
                member_context=lambda _: 'Full member context',
                client=NS(responses=NS(create=create)), BACKEND_MODEL='unchanged-model',
                BACKEND_PROMPT='Full canon and Yes consent rules', TOOLS=[], run_tool=dispatch))
            args = ([{'role': 'user', 'text': 'Check the saved receipt.'}], 2,
                    MarketConversation((1, 2, 'synthetic')), 1)
        else:
            fn = bot_function('ai_run_turn', dict(common, init_ai_db=lambda: None,
                ai_recent_messages=lambda *a, **k: [{'role': 'assistant', 'content': 'Earlier exact preview'}],
                ai_member_context=lambda _: 'Full member context',
                ai_client=NS(responses=NS(create=create)), OPENAI_MODEL='unchanged-model',
                GTOP_AI_PROMPT='Full canon and Yes consent rules', GBOP_AI_TOOLS=[],
                ai_execute_tool=dispatch))
            args = (2, 'Check the saved receipt.', None, 'synthetic-api-efficiency')
        with patch('gbop_voice_web.api_usage.log_response_usage', wraps=log_response_usage) as usage:
            self.assertEqual(fn(*args), 'Verified receipt.')
        self.assertEqual(create.call_count, 2)
        self.assertEqual(usage.call_count, 2)
        self.assertTrue(all(c.args[1] == surface for c in usage.call_args_list))
        request = create.call_args.kwargs
        self.assertEqual(request['model'], 'unchanged-model')
        self.assertFalse(request['store'])
        self.assertIn('Full canon and Yes consent rules', request['instructions'])
        outputs = [x for x in request['input'] if isinstance(x, dict)
                   and x.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 1)
        encoded = outputs[0]['output']
        expected = voice_tool_payload('get_delivery_status', receipt)
        self.assertEqual(json.loads(encoded), expected)
        self.assertEqual(encoded, json.dumps(expected, separators=(',', ':')))
        self.assertLess(len(encoded), len(json.dumps(expected)))
        self.assertEqual(receipt, before)
        self.assertEqual(dispatch.call_count, 1)

    def test_browser_backend_tool_chain_is_lossless_and_uses_same_requests(self):
        self.run_surface('browser_backend')

    def test_discord_text_tool_chain_is_lossless_and_uses_same_requests(self):
        self.run_surface('discord_text')

    def test_broken_log_sink_does_not_replace_answer_or_repeat_tool(self):
        with patch('builtins.print', side_effect=OSError('Log unavailable')):
            self.run_surface('browser_backend')
            self.run_surface('discord_text')

    def test_member_context_skips_unused_read_but_preserves_real_record_context(self):
        for surface in ('discord_text', 'browser_backend'):
            with self.subTest(surface=surface):
                execute = Mock(return_value=NS(fetchall=lambda: []))
                @contextmanager
                def database():
                    yield NS(execute=execute)
                ns = dict(db=database, GTOP_GUILD_ID=1, GTOP_OWNER_USER_ID=2, OWNER_USER_ID=2,
                    init_ai_db=lambda: None, get_profile=lambda *args: {},
                    market_clock=lambda: 'Exact clock', profile_context=lambda _: 'Risk profile',
                    intelligence_context=lambda *args: 'Member intelligence',
                    trade_assist_context=lambda *args: 'Saved trade plan')
                with patch('gbop_voice_web.journal_recall.member_context_lines',
                           return_value=['Real saved journal context']) as journal, \
                     patch('gbop_voice_web.midpoint_preferences.preference_context',
                           return_value='Saved label preference'):
                    fn = (bot_function('ai_member_context', ns) if surface == 'discord_text'
                          else function('member_context', ns))
                    output = fn(2)
                self.assertEqual(execute.call_count, 1)
                self.assertIn('FROM theses', execute.call_args.args[0])
                journal.assert_called_once_with(database, 1, 2)
                self.assertIn('Real saved journal context', output)
                self.assertIn('Saved trade plan', output)
                self.assertIn('Risk profile', output)


class InterruptedUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_realtime_usage_is_logged_once_without_reviving_response(self):
        done = {'type': 'response.done', 'response': {'id': 'stale-private-id',
                'status': 'cancelled', 'usage': {'input_tokens': 100, 'output_tokens': 2}}}
        async def events():
            yield json.dumps(done)
            yield json.dumps(done)
        work = NS(accepts=lambda _: False, stale_request=lambda _: False)
        session = NS(websocket=events(), tool_work=work, send_event=AsyncMock())
        with patch('builtins.print') as emit:
            await method('receiver_loop', dict(json=json))(session)
        usage_rows = [json.loads(c.args[1]) for c in emit.call_args_list
                      if c.args[0] == '[GBOP-API-USAGE]']
        self.assertEqual(len(usage_rows), 1)
        self.assertEqual(usage_rows[0]['input_tokens'], 100)
        self.assertEqual(usage_rows[0]['status'], 'cancelled')
        self.assertNotIn('stale-private-id', str(usage_rows))
        session.send_event.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
