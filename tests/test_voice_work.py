"""No live provider: controlled clock and socket exercise delayed voice work."""
import asyncio
import json
import time
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery, compact_voice_tool_result
from gbop_voice_web.voice_work import VoiceToolWork
from gbop_voice_web.voice_payload import voice_tool_payload
from test_voice_latency import method


async def settle():
    for _ in range(12):
        await asyncio.sleep(0)


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.waits.append((self.now + delay, future))
        await future

    async def advance(self, seconds):
        self.now += seconds
        for deadline, future in self.waits:
            if deadline <= self.now and not future.done():
                future.set_result(None)
        await settle()


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.queue.get()
        return json.dumps(event)

    async def emit(self, event):
        await self.queue.put(event)
        await settle()


class WorkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.sessions = []
        self.receivers = []

    def session(self, member_id=42, provider=None):
        session = NS(member=NS(id=member_id, send=AsyncMock()), websocket=Socket(),
                     _voice_turn_count=1, closed=False, last_error=None, send_event=AsyncMock(return_value=True),
                     tool_output_pending=False, _tool_response_options={}, _last_response_options={},
                     recovery_tools=[{'type': 'function', 'name': 'get_journal_history'}],
                     voice_client=NS(channel=NS(id=member_id)), _logged_audio_items=set())
        session.rate_limit_recovery = VoiceRateLimitRecovery(session, jitter=lambda: .5, sleep=self.clock.sleep)
        session.tool_work = VoiceToolWork(session, sleep=self.clock.sleep)
        self.provider = provider or AsyncMock(return_value={'ok': True})
        # Bind each session to its own provider, mirroring real member-scoped calls.
        current_provider = self.provider
        async def to_thread(fn, *args):
            return await current_provider(*args)
        execute = method('execute_tool', dict(asyncio=NS(to_thread=to_thread), json=json,
            time=time, ai_execute_tool=Mock(), voice_tool_payload=voice_tool_payload,
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        session.execute_tool = MethodType(execute, session)
        session.playback = NS(source=Mock(), voice_client=NS(is_playing=lambda: True, stop_playing=Mock()))
        receiver = method('receiver_loop', dict(json=json,
                         gbop_output_manager=lambda _: session.playback))
        self.receivers.append(asyncio.create_task(receiver(session)))
        self.sessions.append(session)
        return session

    async def asyncTearDown(self):
        for session in self.sessions:
            session.tool_work.cancel()
            session.rate_limit_recovery.cancel()
        for task in self.receivers:
            task.cancel()
        await asyncio.gather(*self.receivers, return_exceptions=True)
        await settle()

    async def call(self, session, call_id='a', name='get_journal_history', response_id='r1'):
        await session.websocket.emit({'type': 'response.created', 'response': {'id': response_id}})
        await session.websocket.emit({'type': 'response.output_item.done', 'response_id': response_id,
            'item': {'type': 'function_call', 'name': name, 'call_id': call_id, 'arguments': '{}'}})

    async def finish(self, session, response_id='r1', status='completed'):
        await session.websocket.emit({'type': 'response.done', 'response': {'id': response_id, 'status': status}})

    async def test_fast_lookup_is_silent_and_one_response_follows(self):
        session = self.session()
        await self.call(session)
        await self.finish(session)
        await self.clock.advance(10)
        session.member.send.assert_not_awaited()
        self.assertEqual([c.args[0]['type'] for c in session.send_event.await_args_list],
                         ['conversation.item.create', 'response.create'])

    async def test_slow_tool_does_not_hold_up_speech_interruption_or_emit_stale_result(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True, 'journals': ['old result']}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session)
        await self.finish(session)
        await self.clock.advance(3.9)
        session.member.send.assert_not_awaited()
        source = session.playback.source
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        self.assertEqual(session._voice_turn_count, 2)
        source.abort.assert_called_once()
        self.assertFalse(session.tool_work.pending)
        gate.set()
        await self.clock.advance(10)
        session.send_event.assert_not_awaited()
        session.member.send.assert_not_awaited()

    async def test_slow_tool_status_is_once_private_text_without_model_call(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session, name='review_market_session')
        await self.finish(session)
        await self.clock.advance(4)
        session.member.send.assert_awaited_once_with("I'm still checking your request.")
        session.send_event.assert_not_awaited()
        await self.clock.advance(40)
        session.member.send.assert_awaited_once()
        gate.set()
        await settle()
        self.assertEqual(session.send_event.await_count, 2)
        await self.call(session, name='review_market_session', call_id='b', response_id='r2')
        await self.finish(session, response_id='r2')
        await self.clock.advance(20)
        session.member.send.assert_awaited_once()

    async def test_slow_journal_summary_never_sends_private_progress(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session, name='get_journal_history')
        await self.finish(session)
        await self.clock.advance(45)
        session.member.send.assert_not_awaited()
        gate.set()
        await settle()
        session.member.send.assert_not_awaited()
        self.assertEqual(session.send_event.await_count, 2)

    async def test_multiple_tools_are_serialized_and_request_only_one_followup(self):
        gate = asyncio.Event()
        calls = []
        async def blocked(member, name, args):
            calls.append(name)
            if len(calls) == 1:
                await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session)
        await session.websocket.emit({'type': 'response.output_item.done', 'response_id': 'r1',
            'item': {'type': 'function_call', 'name': 'list_trade_photos', 'call_id': 'b', 'arguments': '{}'}})
        await self.finish(session)
        self.assertEqual(calls, ['get_journal_history'])
        gate.set()
        await settle()
        self.assertEqual(calls, ['get_journal_history', 'list_trade_photos'])
        self.assertEqual([c.args[0]['type'] for c in session.send_event.await_args_list],
                         ['conversation.item.create', 'conversation.item.create', 'response.create'])

    async def test_duplicate_function_and_response_events_do_not_repeat_delivery(self):
        session = self.session()
        await self.call(session, name='send_trade_photos')
        await session.websocket.emit({'type': 'response.output_item.done', 'response_id': 'r1',
            'item': {'type': 'function_call', 'name': 'send_trade_photos', 'call_id': 'a', 'arguments': '{}'}})
        await self.finish(session)
        await self.finish(session)
        self.provider.assert_awaited_once()
        self.assertEqual(session.send_event.await_count, 2)

    async def test_socket_change_or_close_discards_delayed_results_and_status(self):
        for change in ('websocket', 'closed'):
            gate = asyncio.Event()
            async def blocked(*args):
                await gate.wait()
                return {'ok': True}
            session = self.session(provider=AsyncMock(side_effect=blocked))
            await self.call(session)
            await self.finish(session)
            setattr(session, change, Socket() if change == 'websocket' else True)
            gate.set()
            await self.clock.advance(10)
            session.send_event.assert_not_awaited()
            session.member.send.assert_not_awaited()

    async def test_cancelled_response_and_old_audio_never_reopen_playback(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session)
        await self.finish(session, status='cancelled')
        # Invalid delta would raise on the audio path in this deliberately small fake.
        await session.websocket.emit({'type': 'response.output_audio.delta', 'response_id': 'r1', 'delta': 'late'})
        gate.set()
        await self.clock.advance(10)
        session.send_event.assert_not_awaited()
        session.member.send.assert_not_awaited()
        self.assertFalse(self.receivers[-1].done())

    async def test_member_interruption_does_not_cancel_other_members_lookup(self):
        gate = asyncio.Event()
        async def blocked(member, *args):
            await gate.wait()
            return {'ok': True, 'member': member}
        a = self.session(member_id=1, provider=AsyncMock(side_effect=blocked))
        b = self.session(member_id=2, provider=AsyncMock(side_effect=blocked))
        await self.call(a)
        await self.call(b)
        await self.finish(a)
        await self.finish(b)
        await a.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        gate.set()
        await settle()
        a.send_event.assert_not_awaited()
        self.assertEqual(b.send_event.await_count, 2)
        result = json.loads(b.send_event.await_args_list[0].args[0]['item']['output'])
        self.assertEqual(result['member'], 2)

    async def test_recovery_budget_survives_completed_tool_response(self):
        session = self.session()
        recovery = session.rate_limit_recovery
        recovery.attempts = 2
        session._recovery_active = True
        await self.call(session)
        await self.finish(session)
        self.assertEqual(recovery.attempts, 2)
        recovery.failed({'code': 'rate_limit_exceeded'})
        pending = recovery.task
        await pending
        # Only the tool continuation, no third retry.
        self.assertEqual(session.send_event.await_count, 2)
        self.assertIn('stopped', session.member.send.await_args.args[0])


    async def test_incomplete_response_continues_after_slow_tool_once(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session)
        await self.finish(session, status='incomplete')
        gate.set()
        await settle()
        self.assertEqual([call.args[0]['type'] for call in session.send_event.await_args_list],
                         ['conversation.item.create', 'response.create'])
        self.assertFalse(session.tool_output_pending)

    async def test_new_turn_waits_for_started_action_without_overlapping_or_stale_reply(self):
        gate = asyncio.Event()
        calls = []
        async def blocked(member, name, args):
            calls.append(name)
            if len(calls) == 1:
                await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        await self.call(session, name='send_journal_history')
        await self.finish(session)
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        await self.call(session, call_id='b', response_id='r2')
        await self.finish(session, response_id='r2')
        self.assertEqual(calls, ['send_journal_history'])
        gate.set()
        await settle()
        self.assertEqual(calls, ['send_journal_history', 'get_journal_history'])
        self.assertEqual(session.send_event.await_count, 2)
        self.assertEqual(session.send_event.await_args_list[0].args[0]['item']['call_id'], 'b')

    async def test_interrupted_authorization_never_starts_old_tool(self):
        gate = asyncio.Event()
        async def authorize():
            await gate.wait()
            return None
        session = self.session()
        session.authorize_tool = authorize
        await self.call(session, name='open_trade')
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        gate.set()
        await settle()
        self.provider.assert_not_awaited()
        session.send_event.assert_not_awaited()

    async def test_failed_tool_output_send_does_not_create_ungrounded_reply(self):
        session = self.session()
        session.send_event.return_value = False
        await self.call(session)
        await self.finish(session)
        session.send_event.assert_awaited_once()
        self.assertFalse(session.tool_output_pending)

    async def test_disabled_status_dms_do_not_block_result_or_repeat_notice(self):
        gate = asyncio.Event()
        async def blocked(*args):
            await gate.wait()
            return {'ok': True}
        session = self.session(provider=AsyncMock(side_effect=blocked))
        session.member.send.side_effect = RuntimeError('DM blocked')
        await self.call(session, name='review_market_session')
        await self.finish(session)
        await self.clock.advance(4)
        gate.set()
        await settle()
        self.assertEqual(session.send_event.await_count, 2)
        session.member.send.assert_awaited_once()

    async def test_late_retry_created_after_interruption_cannot_act_or_reset_new_recovery(self):
        session = self.session()
        recovery = session.rate_limit_recovery
        recovery.failed({'code': 'rate_limit_exceeded', 'message': 'try again in 2s'})
        await settle()
        await self.clock.advance(3.5)
        request = session.send_event.await_args.args[0]['response']
        metadata = request['metadata']
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        recovery.attempts = 1
        await session.websocket.emit({'type': 'response.created', 'response': {'id': 'old', 'metadata': metadata}})
        await session.websocket.emit({'type': 'response.output_item.done', 'response_id': 'old',
            'item': {'type': 'function_call', 'name': 'open_trade', 'call_id': 'stale', 'arguments': '{}'}})
        await session.websocket.emit({'type': 'response.output_audio.delta', 'response_id': 'old', 'delta': 'late'})
        await self.finish(session, response_id='old')
        self.provider.assert_not_awaited()
        self.assertEqual(recovery.attempts, 1)
        self.assertEqual(session.send_event.await_count, 2)
        self.assertEqual(session.send_event.await_args.args[0], {'type': 'response.cancel', 'response_id': 'old'})
        await self.call(session, call_id='fresh', response_id='new')
        await self.finish(session, response_id='new')
        self.provider.assert_awaited_once()
        self.assertEqual(session.send_event.await_count, 4)

    async def test_late_continuation_is_fenced_after_socket_change_or_stop(self):
        for change in ('websocket', 'closed'):
            session = self.session()
            await self.call(session)
            await self.finish(session)
            metadata = session.send_event.await_args.args[0]['response']['metadata']
            setattr(session, change, Socket() if change == 'websocket' else True)
            event = {'type': 'response.created', 'response': {'id': 'old', 'metadata': metadata}}
            self.assertFalse(session.tool_work.accepts(event))
            self.assertFalse(session.tool_work.accepts({'response_id': 'old'}))

    async def test_interrupt_cancels_blocked_result_send_before_it_reaches_transport(self):
        session = self.session()
        sending = asyncio.Event()
        gate = asyncio.Event()
        delivered = []
        async def send(event, **kwargs):
            if event['type'] == 'conversation.item.create':
                sending.set()
                await gate.wait()
            delivered.append(event)
            return True
        session.send_event.side_effect = send
        await self.call(session)
        await sending.wait()
        await self.finish(session)
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        gate.set()
        await settle()
        self.assertEqual(delivered, [])
        self.assertFalse(session.tool_output_pending)

    async def test_interrupt_or_stop_cancels_blocked_continuation_send(self):
        for stop in (False, True):
            tool_gate = asyncio.Event()
            sending = asyncio.Event()
            send_gate = asyncio.Event()
            delivered = []
            async def blocked(*args):
                await tool_gate.wait()
                return {'ok': True}
            session = self.session(provider=AsyncMock(side_effect=blocked))
            session.runner = None
            session.output_source = Mock()
            async def send(event, **kwargs):
                if event['type'] == 'response.create':
                    sending.set()
                    await send_gate.wait()
                delivered.append(event)
                return True
            session.send_event.side_effect = send
            await self.call(session)
            await self.finish(session)
            tool_gate.set()
            await sending.wait()
            self.assertTrue(session.tool_work.pending)
            if stop:
                method('stop', {})(session)
            else:
                await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
            send_gate.set()
            await settle()
            self.assertEqual([event['type'] for event in delivered], ['conversation.item.create'])
            self.assertFalse(session.tool_work.pending)

    async def test_old_authorization_cannot_poison_new_turn_private_delivery_cache(self):
        gate = asyncio.Event()
        async def authorize():
            await gate.wait()
            return None
        session = self.session(provider=AsyncMock(return_value={'ok': True, 'sent_count': 1}))
        session.authorize_tool = authorize
        await self.call(session, name='send_journal_history')
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        await self.call(session, call_id='fresh', name='send_journal_history', response_id='new')
        await self.finish(session, response_id='new')
        gate.set()
        await settle()
        self.provider.assert_awaited_once()
        result = json.loads(session.send_event.await_args_list[0].args[0]['item']['output'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['sent_count'], 1)
        self.assertEqual(session.send_event.await_count, 2)


if __name__ == '__main__':
    unittest.main()
