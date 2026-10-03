import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = NS(closed=False, websocket=object(), _voice_turn_count=1,
                          member=NS(send=AsyncMock()), send_event=AsyncMock(), last_error=None)
        self.recovery = VoiceRateLimitRecovery(self.session)
        self.error = {'code': 'rate_limit_exceeded', 'message': 'Please try again in 6.102s.'}

    async def test_retries_response_only_after_server_delay_and_preserves_system_instructions(self):
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.recovery.failed(self.error)
            await self.recovery.task
        sleep.assert_awaited_once_with(7.102)
        self.session.send_event.assert_awaited_once_with(
            {'type': 'response.create', 'response': {'tool_choice': 'none'}})
        self.session.member.send.assert_awaited_once()
        self.assertIsNone(self.recovery.task)

    async def test_retries_are_bounded_and_failure_is_reported(self):
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep', new_callable=AsyncMock):
            for _ in range(3):
                self.recovery.failed(self.error)
                await self.recovery.task
        self.assertEqual(self.session.send_event.await_count, 2)
        self.assertEqual(self.session.member.send.await_count, 2)
        self.assertIn('ask again', self.session.member.send.await_args.args[0])

    async def test_interruption_cancels_retry(self):
        self.recovery.failed(self.error)
        pending = self.recovery.task
        self.recovery.cancel(reset=True)
        await asyncio.gather(pending, return_exceptions=True)
        self.session.send_event.assert_not_awaited()
        self.assertEqual(self.recovery.attempts, 0)

    async def test_stale_turn_disconnected_socket_and_closed_session_never_retry(self):
        for changed in ('_voice_turn_count', 'websocket', 'closed'):
            self.setUp()
            async def change(_):
                setattr(self.session, changed, {'_voice_turn_count': 2, 'websocket': object(), 'closed': True}[changed])
            with patch('gbop_voice_web.voice_runtime.asyncio.sleep', side_effect=change):
                self.recovery.failed(self.error)
                await self.recovery.task
            self.session.send_event.assert_not_awaited()

    async def test_duplicate_failure_does_not_schedule_another_retry(self):
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep', new_callable=AsyncMock):
            self.recovery.failed(self.error)
            pending = self.recovery.task
            self.recovery.failed(self.error)
            self.assertIs(self.recovery.task, pending)
            await pending
        self.assertEqual(self.recovery.attempts, 1)

    async def test_disabled_dms_do_not_block_recovery(self):
        self.session.member.send.side_effect = RuntimeError('DM blocked')
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep', new_callable=AsyncMock):
            self.recovery.failed(self.error)
            await self.recovery.task
        self.session.send_event.assert_awaited_once()

    async def test_other_failures_and_closed_sessions_do_not_retry(self):
        self.recovery.failed({'code': 'insufficient_quota'})
        self.assertIsNone(self.recovery.task)
        self.session.closed = True
        self.recovery.failed(self.error)
        self.assertIsNone(self.recovery.task)

    async def test_malformed_delay_falls_back_without_crashing_receiver(self):
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.recovery.failed({'code': 'rate_limit_exceeded', 'message': 'try again in ...s'})
            await self.recovery.task
        sleep.assert_awaited_once_with(15.0)
