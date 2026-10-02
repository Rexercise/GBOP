import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from unittest.mock import Mock

from gbop_voice_web.discord_controls import in_voice_channel, summon_requested, private_room_owner, pick_voice_guild


SOURCE = Path(__file__).resolve().parents[1] / 'bot.py'


class DiscordControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = SimpleNamespace(id=20)
        self.member = SimpleNamespace(id=10, guild=SimpleNamespace(id=1),
                                      voice=SimpleNamespace(channel=self.channel))
        self.vc = SimpleNamespace(channel=self.channel, guild=self.member.guild)
        self.output = SimpleNamespace(session=None, interrupt=AsyncMock(), voice_client=self.vc)
        self.created = []

        def make_session(member, vc, loop):
            session = SimpleNamespace(closed=False, voice_client=vc,
                                      run=AsyncMock(), close=AsyncMock(), enqueue_audio=lambda pcm: None)
            self.created.append(session)
            return session

        node = next(n for n in ast.parse(SOURCE.read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'GBOPRealtimeManager')
        self.ns = dict(asyncio=asyncio, GBOP_RT_LOCKS={}, in_voice_channel=in_voice_channel,
                       private_room_owner=private_room_owner, GBOP_RT_SINKS={},
                       gbop_voice_member_allowed=lambda m: (True, None),
                       GBOP_RT_OUTPUT_MANAGERS={20: self.output}, GBOPRealtimeSession=make_session)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.manager = self.ns['GBOPRealtimeManager']()

    async def test_pause_closes_only_requesting_member_and_blocks_new_audio(self):
        session = SimpleNamespace(close=AsyncMock(), voice_client=self.vc)
        other = SimpleNamespace(close=AsyncMock())
        self.manager.sessions = {(1, 10): session, (1, 11): other}
        self.output.session = session
        await self.manager.pause_member(self.member)
        session.close.assert_awaited_once()
        other.close.assert_not_awaited()
        self.output.interrupt.assert_awaited_once()
        await self.manager.feed(self.member, self.vc, b'audio', asyncio.get_running_loop())
        self.assertFalse(self.created)
        self.assertIn((1, 11), self.manager.sessions)

    async def test_pause_does_not_interrupt_other_members_reply(self):
        self.manager.sessions[(1, 10)] = SimpleNamespace(close=AsyncMock(), voice_client=self.vc)
        self.output.session = SimpleNamespace()
        await self.manager.pause_member(self.member)
        self.output.interrupt.assert_not_awaited()

    async def test_departed_member_cannot_reconnect_from_queued_audio(self):
        self.member.voice.channel = None
        await self.manager.feed(self.member, self.vc, b'queued', asyncio.get_running_loop())
        self.assertFalse(self.created)

    async def test_other_channel_cannot_create_session(self):
        self.member.voice.channel = SimpleNamespace(id=99)
        self.assertIsNone(await self.manager.get_session(self.member, self.vc, asyncio.get_running_loop()))

    async def test_resume_reconnects_and_concurrent_requests_share_session(self):
        self.manager.paused.add((1, 10))
        self.manager.paused.discard((1, 10))
        sessions = await asyncio.gather(*[
            self.manager.get_session(self.member, self.vc, asyncio.get_running_loop()) for _ in range(3)])
        self.assertEqual(len(self.created), 1)
        self.assertTrue(all(s is sessions[0] for s in sessions))
        await sessions[0].runner

    async def test_denied_member_never_creates_realtime_session(self):
        self.ns['gbop_voice_member_allowed'] = lambda m: (False, 'revoked')
        await self.manager.feed(self.member, self.vc, b'audio', asyncio.get_running_loop())
        self.assertFalse(self.created)

    async def test_close_guild_keeps_other_guilds_sessions_and_pause(self):
        first = SimpleNamespace(close=AsyncMock(), voice_client=self.vc)
        second = SimpleNamespace(close=AsyncMock(), voice_client=SimpleNamespace(channel=SimpleNamespace(id=99)))
        self.manager.sessions = {(1, 10): first, (2, 20): second}
        self.manager.paused = {(1, 10), (2, 20)}
        await self.manager.close_guild(1)
        first.close.assert_awaited_once()
        second.close.assert_not_awaited()
        self.assertEqual(self.manager.paused, {(2, 20)})

    def test_summons_do_not_swallow_market_requests(self):
        for text in ['join me', 'Please join our voice channel!', 'come here', 'start voice']:
            self.assertTrue(summon_requested(text), text)
        for text in ['join my trade', 'when did NAS purge?', 'join us and close my trade']:
            self.assertFalse(summon_requested(text), text)

    async def run_join(self, channel_id=20, allowed=True):
        node = next(n for n in ast.parse(SOURCE.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'voice')
        node.decorator_list = []
        class Client:
            channel = SimpleNamespace(id=channel_id, mention='#trading')
            def is_listening(self):
                return True
        vc = Client()
        guild = SimpleNamespace(id=1, voice_client=vc, me=object(),
                                get_channel=lambda _: self.channel,
                                get_member=lambda _: self.member)
        bot = SimpleNamespace(is_ready=lambda: True, get_guild=lambda _: guild)
        ready = asyncio.Event()
        ready.set()
        manager = SimpleNamespace(paused={(1, 10)}, get_session=AsyncMock(
            return_value=SimpleNamespace(ready=ready)))
        listener = Mock()
        ns = dict(discord=SimpleNamespace(Interaction=object), asyncio=asyncio,
                  private_room_owner=private_room_owner, pick_voice_guild=pick_voice_guild,
                  GBOP_VOICE_CLIENTS=[bot], gbop_private_voice_view=lambda: None,
                  require_member=AsyncMock(return_value=allowed),
                  gbop_voice_control_lock=lambda _: asyncio.Lock(),
                  voice_recv=SimpleNamespace(VoiceRecvClient=Client),
                  GBOP_REALTIME_MANAGER=manager, gbop_start_realtime_listener=listener)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
        self.channel.permissions_for = lambda _: SimpleNamespace(view_channel=True, connect=True, speak=True)
        self.channel.mention = '#trading'
        interaction = SimpleNamespace(user=self.member, guild=guild,
                                      response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await ns['voice'](interaction)
        return manager, listener, interaction

    async def test_join_does_not_move_bot_from_existing_channel(self):
        manager, _, interaction = await self.run_join(channel_id=99)
        manager.get_session.assert_not_awaited()
        self.assertIn('slots are in use', interaction.followup.send.await_args.args[0])

    async def test_join_reuses_listener_and_resumes_member(self):
        manager, listener, interaction = await self.run_join()
        listener.assert_not_called()
        self.assertNotIn((1, 10), manager.paused)
        self.assertIn('active', interaction.followup.send.await_args.args[0])

    async def test_unauthorized_join_stops_before_voice_access(self):
        manager, listener, interaction = await self.run_join(allowed=False)
        manager.get_session.assert_not_awaited()
        listener.assert_not_called()
        interaction.response.defer.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
