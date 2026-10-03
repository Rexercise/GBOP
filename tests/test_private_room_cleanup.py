"""Hang-up lifecycle and conservative channel deletion, using fake transports."""
import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from test_private_voice import code, Entity
from gbop_voice_web.discord_controls import private_room_owner, in_voice_channel
from gbop_voice_web.private_room_cleanup import PrivateRoomCleanup


class CleanupFixture:
    def fixture(self):
        everyone = Entity(id=1)
        owner = Entity(id=42, bot=False, voice=None)
        bot = Entity(id=99, bot=True)
        acl = {everyone: NS(view_channel=False, connect=False, send_messages=False),
               owner: NS(view_channel=True, connect=True, send_messages=False),
               bot: NS(view_channel=True, connect=True, send_messages=False)}
        perms = NS(view_channel=True, connect=True, read_message_history=True, manage_channels=True)
        room = NS(id=10, name='gbop-private-rex', members=[], last_message_id=None,
                  overwrites=acl, overwrites_for=lambda person: acl.get(person, NS()),
                  permissions_for=lambda _: perms, delete=AsyncMock())
        guild = NS(id=123, me=bot, default_role=everyone,
                   get_channel=lambda channel_id: room if channel_id == room.id else None,
                   get_member=lambda member_id: owner if member_id == owner.id else bot,
                   fetch_channel=AsyncMock(return_value=room), voice_client=None)
        owner.guild = guild
        bot.guild = guild
        room.guild = guild
        messages = []
        async def history(**kwargs):
            for message in messages:
                yield message
        room.history = Mock(side_effect=history)
        registry = PrivateRoomCleanup()
        registry.created(room, owner)
        registry.entered(room, owner)
        return owner, bot, room, guild, registry, messages, perms


class TemporaryRoomTests(CleanupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_empty_verified_room_deleted_and_not_reused_from_stale_cache(self):
        owner, _, room, guild, registry, _, _ = self.fixture()
        self.assertTrue(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
        room.delete.assert_awaited_once()
        self.assertNotIn(room.id, registry.rooms)
        self.assertIn(room.id, registry.deleted)

    async def test_legacy_name_and_acl_never_prove_creation(self):
        _, _, room, guild, registry, _, _ = self.fixture()
        registry.rooms.clear()
        self.assertFalse(await registry.remove_if_empty(guild, room.id, None, lambda _: False))
        guild.fetch_channel.assert_not_awaited()
        room.delete.assert_not_awaited()

    async def test_all_messages_including_bot_or_system_messages_preserved(self):
        for author in ('human', 'bot', 'system'):
            with self.subTest(author=author):
                owner, _, room, guild, registry, messages, _ = self.fixture()
                messages.append(NS(author=author))
                self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
                room.delete.assert_not_awaited()
                self.assertIn('messages', registry.notice(guild.id, owner.id))

    async def test_missing_history_or_connect_permission_never_means_empty(self):
        for permission in ('read_message_history', 'connect', 'view_channel', 'manage_channels'):
            with self.subTest(permission=permission):
                _, _, room, guild, registry, _, perms = self.fixture()
                setattr(perms, permission, False)
                self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
                room.history.assert_not_called()
                room.delete.assert_not_awaited()

    async def test_history_errors_preserve_room(self):
        _, _, room, guild, registry, _, _ = self.fixture()
        async def forbidden(**kwargs):
            raise PermissionError('history denied')
            yield
        room.history.side_effect = forbidden
        with self.assertRaises(PermissionError):
            await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False)
        room.delete.assert_not_awaited()

    async def test_humans_or_other_bots_keep_room(self):
        for occupant in (Entity(id=88, bot=False), Entity(id=89, bot=True)):
            _, _, room, guild, registry, _, _ = self.fixture()
            room.members.append(occupant)
            self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
            room.delete.assert_not_awaited()

    async def test_connection_not_yet_released_keeps_room(self):
        _, _, room, guild, registry, _, _ = self.fixture()
        self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: True))
        room.delete.assert_not_awaited()

    async def test_message_seen_or_last_message_marker_prevents_delete(self):
        for kind in ('event', 'last_id'):
            _, _, room, guild, registry, _, _ = self.fixture()
            if kind == 'event':
                registry.message_seen(room.id)
            else:
                room.last_message_id = 999
            self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
            room.delete.assert_not_awaited()

    async def test_permission_changes_owner_changes_and_shared_rooms_preserved(self):
        for kind in ('shared', 'owner', 'text', 'name'):
            owner, _, room, guild, registry, _, _ = self.fixture()
            if kind == 'shared':
                room.overwrites[guild.default_role].view_channel = True
            elif kind == 'owner':
                owner.bot = True
            elif kind == 'text':
                room.overwrites[owner].send_messages = True
            else:
                room.name = 'community'
            self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
            room.delete.assert_not_awaited()

    async def test_rejoin_reservation_and_new_activity_during_http_wait_cancel_delete(self):
        for kind in ('rejoin', 'reservation', 'message', 'permissions', 'visitor'):
            with self.subTest(kind=kind):
                owner, _, room, guild, registry, _, _ = self.fixture()
                async def history(**kwargs):
                    if kind == 'rejoin':
                        registry.entered(room, owner)
                    elif kind == 'reservation':
                        registry.reserve(room, owner)
                    elif kind == 'message':
                        registry.message_seen(room.id)
                    elif kind == 'permissions':
                        room.overwrites[guild.default_role].view_channel = True
                    else:
                        room.members.append(Entity(id=66, bot=False))
                    if False:
                        yield
                room.history.side_effect = history
                self.assertFalse(await registry.remove_if_empty(guild, room.id, registry.token(room.id), lambda _: False))
                room.delete.assert_not_awaited()

    async def test_old_room_cleanup_cannot_delete_replacement(self):
        owner, _, room, guild, registry, _, _ = self.fixture()
        old_token = registry.token(room.id)
        registry.created(room, owner)
        registry.entered(room, owner)
        self.assertFalse(await registry.remove_if_empty(guild, room.id, old_token, lambda _: False))
        room.delete.assert_not_awaited()


class HangupTests(CleanupFixture, unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        owner, bot, room, guild, registry, messages, perms = self.fixture()
        connected = True
        vc = NS(channel=room, guild=guild, is_listening=lambda: True,
                stop_listening=Mock(), stop_playing=Mock(), cleanup=Mock())
        def is_connected():
            return connected
        vc.is_connected = is_connected
        async def disconnect(**kwargs):
            nonlocal connected
            connected = False
            guild.voice_client = None
        vc.disconnect = AsyncMock(side_effect=disconnect)
        guild.voice_client = vc
        connections = lambda _: [guild.voice_client] if guild.voice_client else []
        left = NS(voice_client=vc, stop=Mock(), close=AsyncMock())
        right = NS(voice_client=NS(channel=NS(id=20)), stop=Mock(), close=AsyncMock())
        left_audio = NS(source=NS(abort=Mock()), interrupt=AsyncMock())
        right_audio = NS(source=NS(abort=Mock()), interrupt=AsyncMock())
        sinks = {10: NS(cleanup=Mock()), 20: NS(cleanup=Mock())}
        lock = asyncio.Lock()
        ns = dict(asyncio=asyncio, private_room_owner=private_room_owner,
                  GBOP_PRIVATE_ROOMS=registry, GBOP_RT_STOPPING=set(), GBOP_RT_CLEANUP_TASKS=set(),
                  GBOP_RT_OUTPUT_MANAGERS={10: left_audio, 20: right_audio}, GBOP_RT_SINKS=sinks,
                  gbop_voice_control_lock=lambda _: lock, gbop_voice_connections=connections,
                  gbop_autojoin_private_room=AsyncMock(), GBOP_VOICE_CLIENTS=[NS(user=bot)])
        manager = code('GBOPRealtimeManager', ns)()
        manager.sessions = {(guild.id, owner.id): left, (guild.id, 43): right}
        ns['GBOP_REALTIME_MANAGER'] = manager
        for name in ('gbop_disconnect_idle_voice', 'gbop_cleanup_private_room', 'on_voice_state_update'):
            code(name, ns)
        return owner, bot, room, guild, registry, vc, left, right, ns

    async def drain(self, ns):
        await asyncio.gather(*ns['GBOP_RT_CLEANUP_TASKS'])

    async def test_hangup_stops_audio_frees_slot_deletes_only_empty_room(self):
        owner, _, room, guild, registry, vc, left, right, ns = self.runtime()
        async def confirm_disconnect(**kwargs):
            left.stop.assert_called_once()
            self.assertNotIn((guild.id, owner.id), ns['GBOP_REALTIME_MANAGER'].sessions)
            vc.stop_listening.assert_called_once()
            vc.stop_playing.assert_called_once()
            guild.voice_client = None
        vc.disconnect.side_effect = confirm_disconnect
        vc.is_connected = lambda: False
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
        await self.drain(ns)
        room.delete.assert_awaited_once()
        left.close.assert_awaited_once()
        right.stop.assert_not_called()
        self.assertIn((guild.id, 43), ns['GBOP_REALTIME_MANAGER'].sessions)
        self.assertIn(20, ns['GBOP_RT_SINKS'])
        self.assertEqual(ns['GBOP_RT_STOPPING'], set())

    async def test_owner_leaves_visitor_room_retained_but_bot_frees_slot(self):
        owner, _, room, guild, _, vc, left, _, ns = self.runtime()
        room.members = [Entity(id=55, bot=False)]
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
        await self.drain(ns)
        vc.disconnect.assert_awaited_once()
        self.assertIsNone(guild.voice_client)
        left.stop.assert_called_once()
        room.delete.assert_not_awaited()

    async def test_message_history_and_failed_delete_do_not_keep_bot_busy(self):
        for problem in ('messages', 'permission', 'history_error'):
            with self.subTest(problem=problem):
                owner, _, room, guild, registry, vc, left, _, ns = self.runtime()
                if problem == 'messages':
                    registry.message_seen(room.id)
                elif problem == 'permission':
                    room.delete.side_effect = PermissionError('Manage Channels revoked')
                else:
                    guild.fetch_channel.side_effect = PermissionError('cannot read')
                await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
                await self.drain(ns)
                self.assertIsNone(guild.voice_client)
                left.stop.assert_called_once()
                self.assertIn('Room kept', registry.notice(guild.id, owner.id))
                self.assertIn(room.id, registry.rooms)

    async def test_stale_departure_after_fast_rejoin_preserves_current_session(self):
        owner, _, room, _, _, vc, left, _, ns = self.runtime()
        owner.voice = NS(channel=room)
        room.members = [owner]
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
        vc.disconnect.assert_not_awaited()
        left.stop.assert_not_called()
        room.delete.assert_not_awaited()

    async def test_room_switch_closes_old_before_starting_new(self):
        owner, _, room, _, _, vc, _, _, ns = self.runtime()
        destination = NS(id=30)
        owner.voice = NS(channel=destination)
        async def joined(*args):
            vc.disconnect.assert_awaited_once()
            self.assertFalse(ns['gbop_voice_control_lock'](123).locked())
        ns['gbop_autojoin_private_room'].side_effect = joined
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=destination))
        await self.drain(ns)
        ns['gbop_autojoin_private_room'].assert_awaited_once_with(owner, destination)

    async def test_delayed_bot_disconnect_cannot_stop_new_connection(self):
        _, bot, room, _, _, vc, left, _, ns = self.runtime()
        await ns['on_voice_state_update'](bot, NS(channel=room), NS(channel=None))
        left.stop.assert_not_called()
        vc.disconnect.assert_not_awaited()
        room.delete.assert_not_awaited()

    async def test_other_member_in_shared_channel_is_not_disconnected(self):
        owner, _, room, _, _, vc, left, _, ns = self.runtime()
        room.name = 'community'
        room.members = [Entity(id=55, bot=False)]
        ns['GBOP_RT_OUTPUT_MANAGERS'][room.id].session = left
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
        vc.disconnect.assert_not_awaited()
        room.delete.assert_not_awaited()
        left.stop.assert_called_once()

    async def test_failed_disconnect_keeps_slot_reserved_but_stops_all_ai(self):
        owner, _, room, guild, _, vc, left, _, ns = self.runtime()
        vc.disconnect.side_effect = RuntimeError('Discord unavailable')
        await ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None))
        await self.drain(ns)
        left.stop.assert_called_once()
        self.assertIs(guild.voice_client, vc)
        room.delete.assert_not_awaited()

    async def test_slow_websocket_does_not_delay_disconnect_or_deletion(self):
        owner, _, room, guild, _, vc, left, _, ns = self.runtime()
        release = asyncio.Event()
        left.close.side_effect = release.wait
        await asyncio.wait_for(ns['on_voice_state_update'](owner, NS(channel=room), NS(channel=None)), 0.3)
        self.assertIsNone(guild.voice_client)
        room.delete.assert_awaited_once()
        release.set()
        await self.drain(ns)

    async def test_voice_stopping_gate_blocks_queued_audio_session_recreation(self):
        owner, _, room, _, _, vc, _, _, ns = self.runtime()
        owner.voice = NS(channel=room)
        ns['GBOP_RT_STOPPING'].add(room.id)
        self.assertIsNone(await ns['GBOP_REALTIME_MANAGER'].get_session(owner, vc, asyncio.get_running_loop()))


if __name__ == '__main__':
    unittest.main()
