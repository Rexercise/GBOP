import ast
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.voice_runtime import compact_voice_tool_result, VOICE_TRUNCATION


def method(name, namespace):
    source = Path(__file__).resolve().parents[1] / 'bot.py'
    cls = next(n for n in ast.parse(source.read_text()).body if getattr(n, 'name', '') == 'GBOPRealtimeSession')
    node = next(n for n in cls.body if getattr(n, 'name', '') == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


class VoiceLatencyTests(unittest.IsolatedAsyncioTestCase):
    def test_market_paging_preserves_extremes_events_and_uncertainty(self):
        rows = [{'start_ny': f'2026-10-02T09:{i:02}:00-04:00', 'close': i,
                 'complete': i != 8} for i in range(9)]
        evidence = {'anchor': {'high': 100, 'low': 90},
                    'events': [{'kind': 'range_invalidated', 'confirmed_at_ny': '10:00'}],
                    'limits': 'No automatic entry confirmation.',
                    'assigned_candles': {'candles': rows, 'summary': {'complete': False},
                                         'next_start_ny': None}}
        original = {'ok': True, 'review': {'observations': [{'evidence': evidence}]}}
        result = compact_voice_tool_result('review_market_session', original)
        new = result['review']['observations'][0]['evidence']
        self.assertEqual(new['events'], evidence['events'])
        self.assertEqual(new['anchor'], evidence['anchor'])
        self.assertEqual(new['limits'], evidence['limits'])
        page = new['assigned_candles']
        self.assertEqual(page['candles'], rows[:4])
        self.assertEqual(page['next_start_ny'], rows[4]['start_ny'])
        self.assertFalse(page['summary']['complete'])
        self.assertEqual(len(original['review']['observations'][0]['evidence']['assigned_candles']['candles']), 9)

    def test_short_pages_keep_existing_continuation(self):
        result = {'review': {'candles': [{'start_ny': '09:00'}], 'next_start_ny': '10:00'}}
        self.assertEqual(compact_voice_tool_result('inspect_market_candles', result), result)

    def test_member_records_and_errors_are_not_compacted(self):
        result = {'ok': True, 'journal': ['complete details'] * 100, 'warning': 'Risk exceeded'}
        self.assertEqual(compact_voice_tool_result('get_journal_history', result), result)
        error = {'ok': False, 'error': 'No market data'}
        self.assertEqual(compact_voice_tool_result('review_market_crt', error), error)

    async def test_tool_execution_sends_result_once_without_rebuilding_member_context(self):
        result = {'ok': True, 'trade_id': 4, 'saved': True, 'risk_warning': 'Preserve this warning'}
        execute = Mock(return_value=result)
        async def run_tool(fn, *args):
            return fn(*args)
        ns = dict(asyncio=NS(to_thread=run_tool), json=json, time=time, ai_execute_tool=execute,
                  compact_voice_tool_result=compact_voice_tool_result)
        session = NS(member=NS(id=42), send_event=AsyncMock(), refresh_context=AsyncMock())
        await method('execute_tool', ns)(session, {'name': 'open_trade', 'call_id': 'call-1', 'arguments': '{}'})
        execute.assert_called_once_with(42, 'open_trade', {})
        session.send_event.assert_awaited_once()
        self.assertEqual(json.loads(session.send_event.await_args.args[0]['item']['output']), result)
        session.refresh_context.assert_not_awaited()
        self.assertTrue(session.tool_output_pending)

    def test_bounded_history_keeps_all_function_definitions(self):
        tool = {'type': 'function', 'name': 'test', 'strict': True, 'parameters': {'type': 'object'}}
        ns = dict(VOICE_TRUNCATION=VOICE_TRUNCATION, GBOP_REALTIME_MODEL='existing-model',
                  GBOP_REALTIME_VOICE='existing-voice', GBOP_REALTIME_MAX_OUTPUT_TOKENS=700,
                  GBOP_VAD_EAGERNESS='high', GBOP_AI_TOOLS=[tool])
        update = method('session_update', ns)(NS(instructions=lambda: 'Full canon'))['session']
        self.assertEqual(update['instructions'], 'Full canon')
        self.assertEqual(update['tools'][0]['parameters'], tool['parameters'])
        self.assertEqual(update['truncation']['token_limits']['post_instructions'], 6000)
        self.assertTrue(update['audio']['input']['turn_detection']['interrupt_response'])

    async def test_rate_limit_is_visible_without_replaying_a_tool(self):
        async def events():
            yield json.dumps({'type': 'response.done', 'response': {'status': 'failed',
                'status_details': {'error': {'code': 'rate_limit_exceeded'}}}})
        session = NS(websocket=events(), last_error=None, tool_output_pending=True,
                     member='test', _voice_turn_count=1, send_event=AsyncMock(),
                     rate_limit_recovery=Mock())
        await method('receiver_loop', dict(json=json))(session)
        self.assertIn('rate limit', session.last_error)
        self.assertFalse(session.tool_output_pending)
        session.send_event.assert_not_awaited()
        session.rate_limit_recovery.failed.assert_called_once()


if __name__ == '__main__':
    unittest.main()
