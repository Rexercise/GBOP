import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.discord_controls import voice_readiness
from test_private_voice import code


def bot(*, ready=True, in_server=True, unavailable=False, speak=True, manage=False):
    permissions = NS(view_channel=True, connect=True, speak=speak, manage_channels=manage)
    guild = NS(unavailable=unavailable, me=NS(guild_permissions=permissions)) if in_server else None
    return NS(is_ready=lambda: ready, get_guild=lambda _: guild)


class VoiceReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_configured_is_not_ready_until_connected_to_the_server(self):
        state = voice_readiness([bot(manage=True), bot(ready=False)], 123)
        self.assertEqual(state['configured'], 2)
        self.assertEqual(state['ready_here'], 1)
        self.assertTrue(state['slots'][0]['manage_channels'])
        self.assertEqual(voice_readiness([bot(in_server=False)], 123)['ready_here'], 0)
        self.assertEqual(voice_readiness([bot(unavailable=True)], 123)['ready_here'], 0)

    def test_ready_identity_can_still_lack_voice_permissions(self):
        state = voice_readiness([bot(), bot(speak=False)], 123)
        self.assertEqual(state['ready_here'], 2)
        self.assertTrue(state['slots'][0]['voice_permissions'])
        self.assertFalse(state['slots'][1]['voice_permissions'])
        self.assertEqual(voice_readiness([], 123), {'configured': 0, 'ready_here': 0, 'slots': []})

    async def test_monitor_logs_changes_and_propagates_cancellation(self):
        states = [{'ready_here': 1}, {'ready_here': 1}, {'ready_here': 2}]
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        ns = dict(asyncio=NS(sleep=sleep), json=json, voice_readiness=Mock(side_effect=states),
                  GBOP_VOICE_CLIENTS=[], GTOP_GUILD_ID=123)
        with patch('builtins.print') as output:
            with self.assertRaises(asyncio.CancelledError):
                await code('gbop_voice_readiness_monitor', ns)()
        self.assertEqual(output.call_count, 2)
        self.assertIn('"ready_here": 2', output.call_args.args[0])

    async def test_shutdown_cancels_monitor_and_helper(self):
        async def blocked(*_):
            await asyncio.Event().wait()

        helper = NS(start=AsyncMock(side_effect=blocked), close=AsyncMock())
        async def primary_start(_):
            await asyncio.sleep(0)
            raise RuntimeError('primary stopped')
        primary = NS(start=AsyncMock(side_effect=primary_start), close=AsyncMock())
        tasks_before = asyncio.all_tasks()
        ns = dict(asyncio=asyncio, logging=NS(error=Mock()), client=primary,
                  GBOP_VOICE_CLIENTS=[primary, helper], GBOP_VOICE_HELPER_TOKEN='test-helper',
                  DISCORD_TOKEN='test-primary', gbop_voice_readiness_monitor=blocked)
        with self.assertRaisesRegex(RuntimeError, 'primary stopped'):
            await code('gbop_run_clients', ns)()
        primary.close.assert_awaited_once()
        helper.close.assert_awaited_once()
        self.assertEqual(asyncio.all_tasks(), tasks_before)


if __name__ == '__main__':
    unittest.main()
