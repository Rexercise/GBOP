"""Discord/Cloudflare failures use fake transport, with no API calls or sleeps."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from gbop_voice_web import command_feedback as feedback
from test_private_voice import code


HTML = '<html>Cloudflare error 1015 ' + 'private-upstream-data' * 300 + '</html>'


def http_error(status=429, headers=None):
    return discord.HTTPException(NS(status=status, reason='upstream', headers=headers or {}), HTML)


def interaction(*, done=True, deferred=True):
    return NS(command=NS(name='meet'),
              response=NS(is_done=Mock(return_value=done),
                          type=discord.InteractionResponseType.deferred_channel_message if deferred else None,
                          send_message=AsyncMock()),
              edit_original_response=AsyncMock(), followup=NS(send=AsyncMock()))


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_tree_handler_replaces_deferred_thinking_with_short_safe_error(self):
        target = interaction()
        target.followup.send.side_effect = ValueError('over 2000 characters')
        ns = dict(discord=discord, app_commands=discord.app_commands,
                  report_command_error=feedback.report_command_error,
                  GBOP_DISCORD_COOLDOWN=feedback.DiscordCooldown())
        handler = code('gbop_tree_error', ns)
        with self.assertLogs(feedback.LOG, level='WARNING') as logs:
            await handler(target, NS(original=http_error()))
        message = target.edit_original_response.await_args.kwargs['content']
        self.assertLess(len(message), 2000)
        self.assertIn('rate-limiting', message)
        self.assertNotIn('private-upstream-data', message + ''.join(logs.output))
        target.followup.send.assert_not_awaited()

    async def test_initial_response_is_ephemeral_and_generic_error_is_not_echoed(self):
        target = interaction(done=False)
        with self.assertLogs(feedback.LOG, level='WARNING'):
            self.assertTrue(await feedback.report_command_error(
                target, RuntimeError(HTML), feedback.DiscordCooldown()))
        args = target.response.send_message.await_args
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertNotIn(HTML, args.args[0])
        target.edit_original_response.assert_not_awaited()

    async def test_already_answered_command_uses_private_followup(self):
        target = interaction(deferred=False)
        with self.assertLogs(feedback.LOG, level='WARNING'):
            await feedback.report_command_error(target, ValueError(HTML), feedback.DiscordCooldown())
        target.edit_original_response.assert_not_awaited()
        self.assertTrue(target.followup.send.await_args.kwargs['ephemeral'])

    async def test_edit_failure_uses_one_fallback_and_logs_both_failures(self):
        target = interaction()
        target.edit_original_response.side_effect = http_error(404)
        target.followup.send.side_effect = http_error(400)
        with self.assertLogs(feedback.LOG, level='WARNING') as logs:
            delivered = await feedback.report_command_error(target, ValueError(HTML), feedback.DiscordCooldown())
        self.assertFalse(delivered)
        target.edit_original_response.assert_awaited_once()
        target.followup.send.assert_awaited_once()
        output = ''.join(logs.output)
        self.assertIn('stage=feedback-fallback', output)
        self.assertIn('feedback-undelivered', output)
        self.assertNotIn('private-upstream-data', output)

    async def test_429_on_feedback_stops_without_second_route(self):
        target = interaction()
        target.edit_original_response.side_effect = http_error()
        cooldown = feedback.DiscordCooldown()
        with self.assertLogs(feedback.LOG, level='WARNING') as logs:
            self.assertFalse(await feedback.report_command_error(target, ValueError(), cooldown))
        target.followup.send.assert_not_awaited()
        self.assertGreater(cooldown.remaining(), 0)
        self.assertIn('feedback-undelivered', ''.join(logs.output))

    async def test_feedback_timeout_is_bounded_and_does_not_retry(self):
        target = interaction()
        cancelled = asyncio.Event()
        async def stalled(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        target.edit_original_response.side_effect = stalled
        with patch.object(feedback, 'FEEDBACK_TIMEOUT_SECONDS', .01), self.assertLogs(feedback.LOG, level='WARNING'):
            self.assertFalse(await asyncio.wait_for(feedback.report_command_error(
                target, ValueError(), feedback.DiscordCooldown()), timeout=.5))
        self.assertTrue(cancelled.is_set())
        target.followup.send.assert_not_awaited()


class CooldownTests(unittest.TestCase):
    def test_repeated_limits_back_off_and_success_resets(self):
        now = [1000]
        cooldown = feedback.DiscordCooldown(clock=lambda: now[0])
        for expected in (300, 600, 1200, 1800, 1800):
            cooldown.record(http_error())
            self.assertEqual(cooldown.remaining(), expected)
            with self.assertRaises(feedback.MeetingCooldown):
                cooldown.check()
            now[0] += expected
        cooldown.recovered()
        cooldown.record(http_error())
        self.assertEqual(cooldown.remaining(), 300)

    def test_retry_after_is_respected_even_above_local_backoff_cap(self):
        cooldown = feedback.DiscordCooldown(clock=lambda: 1000)
        cooldown.record(http_error(headers={'Retry-After': '3600'}))
        self.assertEqual(cooldown.remaining(), 3600)
        self.assertEqual(feedback.retry_after_seconds(discord.RateLimited(123)), 123)
        date = format_datetime(datetime.now(timezone.utc) + timedelta(hours=2))
        self.assertGreater(feedback.retry_after_seconds(http_error(headers={'Retry-After': date})), 7198)

    def test_invalid_retry_after_uses_safe_default(self):
        for value in ('nan', 'inf', '-10', 'broken', ''):
            cooldown = feedback.DiscordCooldown(clock=lambda: 1000)
            cooldown.record(http_error(headers={'Retry-After': value}))
            self.assertEqual(cooldown.remaining(), 300)

    def test_non_discord_429_does_not_block_meetings(self):
        error = RuntimeError('other provider')
        error.status = 429
        cooldown = feedback.DiscordCooldown()
        cooldown.record(error)
        self.assertEqual(cooldown.failures, 0)


class MeetingCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_cancels_setup_releases_lock_and_completes_deferred_reply(self):
        target, lock, cooldown = interaction(), asyncio.Lock(), feedback.DiscordCooldown()
        cancelled = asyncio.Event()
        async def start(_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with patch.object(feedback, 'MEETING_TIMEOUT_SECONDS', .01), self.assertLogs(feedback.LOG, level='WARNING'):
            await asyncio.wait_for(feedback.run_private_meeting(target, start, lock, cooldown), timeout=.5)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(lock.locked())
        self.assertIn('could not confirm completion in time', target.edit_original_response.await_args.kwargs['content'])

    async def test_timeout_also_bounds_lock_wait(self):
        target, lock, cooldown = interaction(), asyncio.Lock(), feedback.DiscordCooldown()
        start = AsyncMock()
        async with lock:
            with patch.object(feedback, 'MEETING_TIMEOUT_SECONDS', .01), self.assertLogs(feedback.LOG, level='WARNING'):
                await asyncio.wait_for(feedback.run_private_meeting(target, start, lock, cooldown), timeout=.5)
            self.assertTrue(lock.locked())
        start.assert_not_awaited()
        target.edit_original_response.assert_awaited_once()

    async def test_new_requests_during_cooldown_make_no_setup_calls(self):
        target, cooldown = interaction(), feedback.DiscordCooldown()
        start = AsyncMock(side_effect=http_error())
        with self.assertLogs(feedback.LOG, level='WARNING'):
            for _ in range(3):
                await feedback.run_private_meeting(target, start, asyncio.Lock(), cooldown)
        start.assert_awaited_once()
        self.assertEqual(cooldown.failures, 1)
        self.assertEqual(target.edit_original_response.await_count, 3)

    async def test_recovery_allows_only_one_probe_and_resets_after_success(self):
        now = [0]
        cooldown = feedback.DiscordCooldown(clock=lambda: now[0])
        cooldown.record(http_error())
        now[0] = 301
        entered, finish = asyncio.Event(), asyncio.Event()
        async def start(_):
            entered.set()
            await finish.wait()
        probe = asyncio.create_task(feedback.run_private_meeting(interaction(), start, asyncio.Lock(), cooldown))
        await entered.wait()
        second = AsyncMock()
        with self.assertLogs(feedback.LOG, level='WARNING'):
            await feedback.run_private_meeting(interaction(), second, asyncio.Lock(), cooldown)
        second.assert_not_awaited()
        finish.set()
        await probe
        self.assertEqual(cooldown.failures, 0)
        self.assertFalse(cooldown.probing)

    async def test_in_flight_success_cannot_clear_another_requests_rate_limit(self):
        cooldown = feedback.DiscordCooldown()
        async def start(_):
            cooldown.record(http_error())
        await feedback.run_private_meeting(interaction(), start, asyncio.Lock(), cooldown)
        self.assertGreater(cooldown.remaining(), 0)

    async def test_external_cancellation_propagates_without_error_reply(self):
        target, lock = interaction(), asyncio.Lock()
        with self.assertRaises(asyncio.CancelledError):
            await feedback.run_private_meeting(target, AsyncMock(side_effect=asyncio.CancelledError()),
                                               lock, feedback.DiscordCooldown())
        self.assertFalse(lock.locked())
        target.edit_original_response.assert_not_awaited()
