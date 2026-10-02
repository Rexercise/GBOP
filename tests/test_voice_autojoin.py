import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from test_private_voice import code, Entity
from gbop_voice_web.discord_controls import private_room_autojoin_allowed, private_room_owner, pick_voice_guild, in_voice_channel


class AutoJoinTests(unittest.IsolatedAsyncioTestCase):
    def room(self):
        everyone = Entity(id=1)
        member = Entity(id=42, bot=False, guild=NS(id=123, default_role=everyone), send=AsyncMock())
        acl = {everyone: NS(view_channel=False, connect=False), member: NS(view_channel=True, connect=True)}
        room = NS(id=10, name='gbop-private-rex', overwrites=acl,
                  overwrites_for=lambda target: acl.get(target, NS(view_channel=None, connect=None)))
        member.voice = NS(channel=room)
        return member, room, everyone

    def test_only_owner_in_still_private_room_is_eligible(self):
        member, room, everyone = self.room()
        self.assertTrue(private_room_autojoin_allowed(member, room))
        visitor = Entity(id=43, bot=False, guild=member.guild)
        self.assertFalse(private_room_autojoin_allowed(visitor, room))
        room.overwrites[everyone].view_channel = True
        self.assertFalse(private_room_autojoin_allowed(member, room))
        room.overwrites[everyone].view_channel = False
        room.overwrites[Entity(id=77)] = NS(view_channel=True, connect=True)
        self.assertFalse(private_room_autojoin_allowed(member, room))

    async def test_authorized_entry_connects_but_revoked_entry_does_not(self):
        for allowed in (True, False):
            member, room, _ = self.room()
            ready = asyncio.Event(); ready.set()
            connect = AsyncMock(return_value=(room, NS(ready=ready)))
            async def authorize(fn, arg):
                return allowed, None
            ns = dict(GTOP_GUILD_ID=123, private_room_autojoin_allowed=private_room_autojoin_allowed,
                      asyncio=NS(to_thread=authorize, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError),
                      gbop_voice_member_allowed=Mock(), gbop_connect_member_voice=connect)
            await code('gbop_autojoin_private_room', ns)(member, room)
            self.assertEqual(connect.await_count, int(allowed))

    async def test_full_slots_report_to_owner_without_interrupting_another_call(self):
        member, room, _ = self.room()
        async def authorize(fn, arg):
            return True, None
        connect = AsyncMock(side_effect=RuntimeError('All voice slots are in use'))
        ns = dict(GTOP_GUILD_ID=123, private_room_autojoin_allowed=private_room_autojoin_allowed,
                  asyncio=NS(to_thread=authorize, TimeoutError=asyncio.TimeoutError),
                  gbop_voice_member_allowed=Mock(), gbop_connect_member_voice=connect,
                  gbop_private_voice_view=lambda room: None)
        await code('gbop_autojoin_private_room', ns)(member, room)
        self.assertIn('slots are in use', member.send.await_args.args[0])

    async def test_event_join_triggers_once_outside_lock_and_mute_does_not(self):
        member, room, _ = self.room()
        lock = asyncio.Lock()
        async def joined(*args):
            self.assertFalse(lock.locked())
        auto = AsyncMock(side_effect=joined)
        ns = dict(asyncio=asyncio, gbop_voice_control_lock=lambda _: lock,
                  GBOP_REALTIME_MANAGER=NS(sessions={}), gbop_voice_connections=lambda _: [],
                  gbop_autojoin_private_room=auto)
        event = code('on_voice_state_update', ns)
        await event(member, NS(channel=None), NS(channel=room))
        await event(member, NS(channel=room), NS(channel=room))
        auto.assert_awaited_once_with(member, room)

    async def connection(self, depart=False):
        member, room, _ = self.room()
        lock = asyncio.Lock()
        class VoiceClient:
            def is_listening(self):
                return getattr(self, 'listening', False)
        vc = VoiceClient(); vc.channel = room; vc.disconnect = AsyncMock()
        guild = NS(voice_client=None, me=object(), get_channel=lambda _: room)
        room.permissions_for = lambda _: NS(view_channel=True, connect=True, speak=True)
        room.members = [member]
        async def connect(**kwargs):
            await asyncio.sleep(0)
            guild.voice_client = vc
            if depart:
                member.voice = None
                room.members = []
            return vc
        room.connect = AsyncMock(side_effect=connect)
        listener = Mock(side_effect=lambda client: setattr(client, 'listening', True))
        manager = NS(paused={(123, 42)}, get_session=AsyncMock(), close_channel=AsyncMock())
        ns = dict(asyncio=asyncio, gbop_voice_control_lock=lambda _: lock, in_voice_channel=in_voice_channel,
                  private_room_owner=private_room_owner, pick_voice_guild=pick_voice_guild,
                  GBOP_VOICE_CLIENTS=[NS(is_ready=lambda: True, get_guild=lambda _: guild)],
                  voice_recv=NS(VoiceRecvClient=VoiceClient), GBOP_REALTIME_MANAGER=manager,
                  gbop_start_realtime_listener=listener)
        return code('gbop_connect_member_voice', ns), member, room, vc, listener, manager

    async def test_auto_and_manual_connection_race_creates_one_listener(self):
        connect, member, room, _, listener, manager = await self.connection()
        await asyncio.gather(connect(member, room), connect(member, room))
        room.connect.assert_awaited_once()
        listener.assert_called_once()
        self.assertNotIn((123, 42), manager.paused)

    async def test_departure_during_handshake_does_not_leave_orphan_bot(self):
        connect, member, room, vc, listener, manager = await self.connection(depart=True)
        with self.assertRaisesRegex(RuntimeError, 'left the voice channel'):
            await connect(member, room)
        vc.disconnect.assert_awaited_once()
        manager.close_channel.assert_awaited_once_with(room.id)
        listener.assert_not_called()
        manager.get_session.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
