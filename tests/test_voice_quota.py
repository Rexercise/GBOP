"""Credit exhaustion stops retry loops; manual restart keeps safety state."""
import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.voice_quota import (QUOTA_NOTICE, block_for_quota,
                                       block_for_connection, is_quota_error, resume_after_quota)
from gbop_voice_web.voice_runtime import VOICE_TRUNCATION
from test_voice_latency import method


def session():
    ready = asyncio.Event()
    ready.set()
    queue = asyncio.Queue()
    queue.put_nowait(b'old speech')
    return NS(member=NS(send=AsyncMock()), closed=False, ready=ready,
        websocket=NS(send=AsyncMock(), close=AsyncMock()), audio_queue=queue,
        tool_work=Mock(), market_delivery=Mock(), rate_limit_recovery=Mock(),
        output_source=Mock(), tool_output_pending=True, last_error=None,
        authorize_tool=AsyncMock(return_value=None), runner=None,
        run=AsyncMock(), _journal_write_recovery={'saved': 'keep'},
        _delivery_recovery={'uncertain': 'keep'})


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    def test_only_quota_codes_block_not_tpm_network_or_untrusted_message_text(self):
        for error in ({'code': 'credit_balance_exhausted'}, {'type': 'insufficient_quota'},
                      {'code': 'billing_hard_limit_reached'}):
            self.assertTrue(is_quota_error(error))
        for error in (None, [], 'insufficient_quota', {'code': []},
                      {'code': 'rate_limit_exceeded'}, {'code': 'server_error'},
                      {'message': 'no credits remaining'}, {'status': 429}):
            self.assertFalse(is_quota_error(error))

    async def test_block_is_immediate_drops_queued_audio_and_keeps_barriers(self):
        value = session()
        entered = asyncio.Event()
        async def notify(_):
            entered.set()
            await asyncio.Event().wait()
        value.member.send.side_effect = notify
        write, delivery = value._journal_write_recovery, value._delivery_recovery
        task = asyncio.create_task(block_for_quota(value, {'code': 'credit_balance_exhausted'}))
        await entered.wait()
        self.assertTrue(value.quota_blocked)
        self.assertFalse(value.ready.is_set())
        self.assertTrue(value.audio_queue.empty())
        self.assertIs(value._journal_write_recovery, write)
        self.assertIs(value._delivery_recovery, delivery)
        value.output_source.abort.assert_called_once()
        value.tool_work.cancel.assert_called_once()
        value.rate_limit_recovery.cancel.assert_called_once_with(reset=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(value.quota_blocked)

    async def test_duplicate_quota_and_disabled_dms_never_repeat_notice(self):
        value = session()
        value.member.send.side_effect = RuntimeError('private transport detail')
        for _ in range(5):
            self.assertTrue(await block_for_quota(value, {'type': 'insufficient_quota'}))
        value.member.send.assert_awaited_once_with(QUOTA_NOTICE)
        self.assertEqual(value.last_error, QUOTA_NOTICE)
        value.run.assert_not_awaited()

    async def test_receiver_cleanup_does_not_cancel_the_one_bounded_notice(self):
        value = session()
        entered, release = asyncio.Event(), asyncio.Event()
        delivered = []
        async def notify(text):
            entered.set()
            await release.wait()
            delivered.append(text)
        value.member.send.side_effect = notify
        receiver = asyncio.create_task(block_for_quota(value, {'code': 'credit_balance_exhausted'}))
        await entered.wait()
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        self.assertTrue(value.quota_blocked)
        self.assertFalse(value._quota_notice_task.done())
        release.set()
        await value._quota_notice_task
        self.assertEqual(delivered, [QUOTA_NOTICE])
        await block_for_quota(value, {'code': 'credit_balance_exhausted'})
        value.member.send.assert_awaited_once()

    async def test_other_errors_leave_session_and_other_members_unchanged(self):
        value, other = session(), session()
        self.assertFalse(await block_for_quota(value, {'code': 'rate_limit_exceeded'}))
        self.assertTrue(value.ready.is_set())
        value.member.send.assert_not_awaited()
        await block_for_quota(value, {'code': 'credit_balance_exhausted'})
        self.assertTrue(other.ready.is_set())
        self.assertFalse(getattr(other, 'quota_blocked', False))

    async def test_blocked_enqueue_and_send_do_not_transmit_or_replay_audio(self):
        value = session()
        await block_for_quota(value, {'code': 'credit_balance_exhausted'})
        method('enqueue_audio', {'asyncio': asyncio})(value, b'new speech')
        self.assertTrue(value.audio_queue.empty())
        send = method('send_event', {'json': json})
        self.assertFalse(await send(value, {'type': 'response.create'}))
        self.assertFalse(await send(value, {'type': 'input_audio_buffer.append', 'audio': 'private'}))
        value.websocket.send.assert_not_awaited()

    async def test_manual_resume_uses_same_session_and_waits_for_old_runner(self):
        value = session()
        await block_for_quota(value, {'code': 'credit_balance_exhausted'})
        write, delivery = value._journal_write_recovery, value._delivery_recovery
        cleaned = asyncio.Event()
        async def old():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        value.runner = asyncio.create_task(old())
        await asyncio.sleep(0)
        await resume_after_quota(value)
        await value.runner
        self.assertTrue(cleaned.is_set())
        self.assertFalse(value.quota_blocked)
        value.authorize_tool.assert_awaited_once()
        value.run.assert_awaited_once()
        self.assertIs(value._journal_write_recovery, write)
        self.assertIs(value._delivery_recovery, delivery)

    async def test_denied_or_closed_resume_stays_blocked(self):
        for condition in ('denied', 'closed', 'missing'):
            value = session()
            value.quota_blocked = True
            if condition == 'denied':
                value.authorize_tool.return_value = 'private denial'
            elif condition == 'closed':
                value.closed = True
            else:
                del value.authorize_tool
            with self.assertRaises(RuntimeError):
                await resume_after_quota(value)
            self.assertTrue(value.quota_blocked)
            value.run.assert_not_awaited()

    async def test_access_pause_resumes_same_session_only_after_reauthorization(self):
        value = session()
        write, delivery = value._journal_write_recovery, value._delivery_recovery
        block_for_connection(value)
        self.assertTrue(value.connection_blocked)
        self.assertFalse(value.closed)
        self.assertTrue(value.audio_queue.empty())
        method('enqueue_audio', {'asyncio': asyncio})(value, b'blocked')
        self.assertTrue(value.audio_queue.empty())
        await resume_after_quota(value)
        await value.runner
        self.assertFalse(value.connection_blocked)
        value.authorize_tool.assert_awaited_once()
        self.assertIs(value._journal_write_recovery, write)
        self.assertIs(value._delivery_recovery, delivery)

    async def test_simultaneous_resume_starts_one_runner_only(self):
        value = session()
        value.quota_blocked = True
        entered = asyncio.Event()
        release = asyncio.Event()
        async def authorize():
            entered.set()
            await release.wait()
        value.authorize_tool = authorize
        first = asyncio.create_task(resume_after_quota(value))
        await entered.wait()
        second = asyncio.create_task(resume_after_quota(value))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        await value.runner
        value.run.assert_awaited_once()

    async def test_identity_change_during_resume_does_not_restart(self):
        value = session()
        value.quota_blocked = True
        async def authorize():
            value.member = NS(id=999, send=AsyncMock())
        value.authorize_tool = authorize
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            await resume_after_quota(value)
        self.assertTrue(value.quota_blocked)
        value.run.assert_not_awaited()

    async def test_receiver_stale_quota_response_blocks_without_recovery_retry(self):
        value = session()
        value.tool_work.accepts.return_value = False
        async def events():
            yield json.dumps({'type': 'response.done', 'response': {
                'id': 'private-response', 'status': 'failed', 'usage': {'input_tokens': 0},
                'status_details': {'error': {'code': 'credit_balance_exhausted'}}}})
            raise AssertionError('Receiver must stop consuming after quota exhaustion.')
        value.websocket = events()
        with patch('builtins.print'):
            await method('receiver_loop', {'json': json})(value)
        self.assertTrue(value.quota_blocked)
        value.rate_limit_recovery.failed.assert_not_called()

    async def test_malformed_control_events_do_not_crash_receiver(self):
        from gbop_voice_web.voice_work import VoiceToolWork
        value = session()
        value._voice_turn_count = 1
        value.tool_work = VoiceToolWork(value)
        malformed = [
            {'type': 'error', 'error': None},
            {'type': 'response.created', 'response': []},
            {'type': 'response.created', 'response': {'id': []}},
            {'type': 'response.created', 'response': {'id': 'r', 'metadata': {'gbop_request': []}}},
            {'type': 'response.done', 'response': {'status_details': []}},
            {'type': 'response.done', 'response': {'status_details': {'error': []}}},
            {'type': 'response.output_audio.delta', 'item_id': []},
            {'type': 'response.output_audio_transcript.done', 'transcript': {}},
            {'type': 'response.output_item.done', 'item': []},
            {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'call_id': []}},
        ]
        async def events():
            for event in malformed:
                yield json.dumps(event)
        value.websocket = events()
        with patch('builtins.print') as output:
            await method('receiver_loop', {'json': json})(value)
        self.assertFalse(getattr(value, 'quota_blocked', False))
        value.member.send.assert_not_awaited()

    async def test_startup_quota_does_not_turn_timeout_into_reconnect_loop(self):
        value = session()
        value.ready.clear()
        value.connect_ws = AsyncMock(return_value=value.websocket)
        value.session_update = lambda: {'session': {'instructions': '', 'tools': []}}
        value.send_event = AsyncMock(return_value=True)
        async def receive():
            await block_for_quota(value, {'code': 'credit_balance_exhausted'})
        value.receiver_loop = receive
        value.sender_loop = AsyncMock()
        async def bounded_wait(coro, *, timeout):
            return await asyncio.wait_for(coro, timeout=min(timeout, .03))
        controlled_asyncio = NS(**{name: getattr(asyncio, name) for name in
            ('create_task', 'to_thread', 'wait', 'FIRST_COMPLETED', 'CancelledError', 'gather', 'sleep')},
            wait_for=bounded_wait)
        run = method('run', dict(asyncio=controlled_asyncio, GBOP_REALTIME_MODEL='same',
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=1400, GBOP_VAD_EAGERNESS='medium',
            VOICE_TRUNCATION=VOICE_TRUNCATION))
        with patch('gbop_voice_web.voice_runtime.bind_voice_tool_connection', new=AsyncMock()), patch('builtins.print'):
            await asyncio.wait_for(run(value), timeout=1)
        self.assertEqual(value.connect_ws.await_count, 1)
        self.assertFalse(value.ready.is_set())
        self.assertFalse(value.closed)
        self.assertTrue(value.quota_blocked)
        self.assertEqual(value.last_error, QUOTA_NOTICE)


if __name__ == '__main__':
    unittest.main()
