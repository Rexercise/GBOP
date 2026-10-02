import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.discord_controls import pick_voice_guild, private_room_owner, in_voice_channel

SOURCE = Path(__file__).resolve().parents[1] / 'bot.py'


def code(name, namespace):
    node = next(n for n in ast.parse(SOURCE.read_text()).body if getattr(n, 'name', '') == name)
    if hasattr(node, 'decorator_list'):
        node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace[name]


class Entity:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class PrivateVoiceTests(unittest.IsolatedAsyncioTestCase):
    def bot(self, channel_id=None, ready=True):
        guild = NS(id=1, voice_client=None if channel_id is None else NS(channel=NS(id=channel_id)))
        return NS(is_ready=lambda: ready, get_guild=lambda _: guild)

    def test_second_room_uses_idle_identity_without_moving_first(self):
        first, second = self.bot(10), self.bot()
        self.assertIs(pick_voice_guild([first, second], 1, 20), second.get_guild(1))
        self.assertEqual(first.get_guild(1).voice_client.channel.id, 10)

    def test_existing_room_reused_before_idle_identity(self):
        first, second = self.bot(), self.bot(20)
        self.assertIs(pick_voice_guild([first, second], 1, 20), second.get_guild(1))

    def test_full_or_unready_pool_does_not_allocate(self):
        self.assertIsNone(pick_voice_guild([self.bot(10), self.bot(20)], 1, 30))
        self.assertIsNone(pick_voice_guild([self.bot(ready=False)], 1, 30))

    def test_private_owner_from_permissions_not_display_name(self):
        owner = Entity(id=42, bot=False)
        helper = Entity(id=99, bot=True)
        room = NS(name='gbop-private-rex', overwrites={
            owner: NS(view_channel=True, connect=True), helper: NS(view_channel=True, connect=True)})
        self.assertEqual(private_room_owner(room), 42)
        room.overwrites[Entity(id=43, bot=False)] = NS(view_channel=True, connect=True)
        self.assertIsNone(private_room_owner(room))

    async def test_close_one_room_preserves_other_room_audio_and_member(self):
        left = NS(voice_client=NS(channel=NS(id=10)), close=AsyncMock())
        right = NS(voice_client=NS(channel=NS(id=20)), close=AsyncMock())
        left_audio, right_audio = NS(interrupt=AsyncMock()), NS(interrupt=AsyncMock())
        sinks = {10: NS(cleanup=Mock()), 20: NS(cleanup=Mock())}
        ns = dict(asyncio=asyncio, GBOP_RT_OUTPUT_MANAGERS={10: left_audio, 20: right_audio},
                  GBOP_RT_SINKS=sinks)
        manager = code('GBOPRealtimeManager', ns)()
        manager.sessions = {(1, 42): left, (1, 43): right}
        await manager.close_channel(10)
        left.close.assert_awaited_once()
        left_audio.interrupt.assert_awaited_once()
        right.close.assert_not_awaited()
        right_audio.interrupt.assert_not_awaited()
        self.assertIn((1, 43), manager.sessions)
        self.assertIn(20, sinks)

    async def test_visiting_admin_audio_cannot_become_members_trade_input(self):
        owner = Entity(id=42, bot=False)
        visitor = NS(id=1, guild=NS(id=100))
        room = NS(id=10, name='gbop-private-rex', overwrites={owner: NS(view_channel=True, connect=True)})
        visitor.voice = NS(channel=room)
        ns = dict(asyncio=asyncio, in_voice_channel=in_voice_channel, private_room_owner=private_room_owner)
        manager = code('GBOPRealtimeManager', ns)()
        self.assertIsNone(await manager.get_session(visitor, NS(channel=room), asyncio.get_running_loop()))

    async def room_request(self, manage=True):
        everyone = Entity(id=1)
        owner = Entity(id=42, bot=False, display_name='Rexercise')
        primary = Entity(id=99, bot=True, guild_permissions=NS(manage_channels=manage))
        helper = Entity(id=100, bot=True)
        room = NS(mention='#gbop-private-rexercise')
        guild = NS(id=123, me=primary, default_role=everyone, voice_channels=[],
                   get_member=lambda i: {99: primary, 100: helper}.get(i),
                   create_voice_channel=AsyncMock(return_value=room))
        interaction = NS(guild=guild, user=owner, followup=NS(send=AsyncMock()))
        ns = dict(asyncio=asyncio, re=__import__('re'), private_room_owner=private_room_owner,
                  gbop_voice_control_lock=lambda _: asyncio.Lock(), gbop_private_voice_view=lambda *args: None,
                  discord=NS(PermissionOverwrite=lambda **kw: NS(**kw)),
                  GBOP_VOICE_CLIENTS=[NS(user=primary), NS(user=helper)])
        await code('gbop_private_room', ns)(interaction)
        return guild, everyone, owner, primary, helper, interaction

    async def test_created_room_denies_everyone_and_allows_only_member_and_bots(self):
        guild, everyone, owner, primary, helper, _ = await self.room_request()
        acl = guild.create_voice_channel.await_args.kwargs['overwrites']
        self.assertEqual(set(acl), {everyone, owner, primary, helper})
        self.assertFalse(acl[everyone].view_channel)
        self.assertFalse(acl[everyone].connect)
        self.assertTrue(acl[owner].speak)
        self.assertTrue(acl[primary].connect)
        self.assertTrue(acl[helper].connect)

    async def test_missing_channel_permission_provides_browser_option(self):
        guild, _, _, _, _, interaction = await self.room_request(manage=False)
        guild.create_voice_channel.assert_not_awaited()
        self.assertIn('Manage Channels', interaction.followup.send.await_args.args[0])
        self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])


if __name__ == '__main__':
    unittest.main()
