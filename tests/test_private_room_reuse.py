"""Synthetic Discord REST persistence with real discord.py channel/ACL models.

No credentials, network, Discord messages or production storage. The JSON file
represents Discord's persisted channel list, separately from the gateway cache.
It is not a claim about PostgreSQL or Discord's live transport consistency.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

import discord

from test_private_voice import code
from gbop_voice_web.discord_controls import private_room_owner, private_room_autojoin_allowed
from gbop_voice_web.private_room_cleanup import PrivateRoomCleanup
from gbop_voice_web.private_room_reuse import select_private_room, PrivateRoomAccessError
from gbop_voice_web.command_feedback import DiscordCooldown, run_private_meeting


def user_data(user_id, bot=False):
    return dict(id=str(user_id), username='same-display-name', discriminator='0', avatar=None, bot=bot)


class DiscordFixture:
    def __init__(self, path, guild_id=123, user_id=42):
        self.path = Path(path)
        if not self.path.exists():
            self.path.write_text('[]')
        self.client = discord.Client(intents=discord.Intents.all())
        state = self.client._connection
        state.user = discord.ClientUser(state=state, data=user_data(99, True))
        self.guild = discord.Guild(state=state, data=dict(
            id=str(guild_id), name='synthetic', owner_id='999',
            roles=[dict(id=str(guild_id), name='@everyone', permissions=str(discord.Permissions(manage_channels=True).value))],
            members=[dict(user=user_data(99, True), roles=[], flags=0)]))
        # Deliberately omit the requester from the gateway member cache.
        self.member = discord.Member(data=dict(user=user_data(user_id), roles=[], flags=0), guild=self.guild, state=state)
        self.registry = PrivateRoomCleanup()
        self.lock = asyncio.Lock()
        self.fetches = 0
        self.creates = 0
        self.uncertain_create = False

        async def fetch_channels():
            self.fetches += 1
            await asyncio.sleep(0)
            return [self.decode(data) for data in json.loads(self.path.read_text())]

        async def create(**kwargs):
            self.creates += 1
            await asyncio.sleep(0)
            rows = json.loads(self.path.read_text())
            channel_id = max([int(row['id']) for row in rows] + [1000]) + 1
            data = self.data(channel_id, self.member.id)
            data['name'] = kwargs['name']
            data['permission_overwrites'] = []
            for target, acl in kwargs['overwrites'].items():
                allow, deny = acl.pair()
                data['permission_overwrites'].append(dict(
                    id=str(target.id), type=0 if isinstance(target, discord.Role) else 1,
                    allow=str(allow.value), deny=str(deny.value)))
            rows.append(data)
            self.path.write_text(json.dumps(rows))
            if self.uncertain_create:
                raise TimeoutError('synthetic lost response after accepted create')
            return self.decode(data)

        # Guild's transport is mocked; actual model permissions/identity are used.
        self.fetch = AsyncMock(side_effect=fetch_channels)
        self.create = AsyncMock(side_effect=create)
        self.facade = NS(id=guild_id, me=self.guild.me, default_role=self.guild.default_role,
                         get_member=self.guild.get_member, fetch_channels=self.fetch,
                         create_voice_channel=self.create, voice_channels=[])
        self.interaction = NS(guild=self.facade, user=self.member, followup=NS(send=AsyncMock()))
        self.ns = dict(asyncio=asyncio, discord=discord, re=__import__('re'),
                       private_room_owner=private_room_owner,
                       select_private_room=select_private_room, PrivateRoomAccessError=PrivateRoomAccessError,
                       GBOP_PRIVATE_ROOMS=self.registry, GBOP_VOICE_CLIENTS=[NS(user=state.user)],
                       gbop_voice_control_lock=lambda _: self.lock,
                       gbop_private_voice_view=lambda *args: None)
        self.setup = code('gbop_private_room', self.ns)

    def data(self, channel_id, owner_id):
        hidden = discord.Permissions(view_channel=True, connect=True).value
        access = discord.Permissions(view_channel=True, connect=True, speak=True, use_voice_activation=True).value
        return dict(id=str(channel_id), type=2, name='gbop-private-same-name', position=0,
                    bitrate=64000, user_limit=0, last_message_id='555',
                    permission_overwrites=[
                        dict(id=str(self.guild.id), type=0, allow='0', deny=str(hidden)),
                        dict(id=str(owner_id), type=1, allow=str(access), deny='0'),
                        dict(id='99', type=1, allow=str(access), deny='0')])

    def decode(self, data):
        return discord.VoiceChannel(state=self.client._connection, guild=self.guild, data=data)

    async def request(self, temporary=False):
        return await self.setup(self.interaction, announce=False, prefer_temporary=temporary)


class PrivateRoomReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'discord-channels.json'
        self.f = DiscordFixture(self.path)

    def put(self, *rows):
        self.path.write_text(json.dumps(rows))

    async def test_uncached_requester_is_reused_by_acl_id_and_can_autojoin(self):
        self.put(self.f.data(1001, 42))
        self.assertIsNone(self.f.guild.get_member(42))
        room = await self.f.request()
        self.assertEqual(room.id, 1001)
        self.assertEqual(private_room_owner(room), 42)
        self.assertTrue(private_room_autojoin_allowed(self.f.member, room))
        self.assertEqual(self.f.creates, 0)
        self.assertNotIn(room.id, self.f.registry.rooms)

    async def test_repeated_mixed_requests_keep_one_room_despite_no_gateway_events(self):
        first = await self.f.request()
        for temporary in (False, True, False, True):
            self.assertEqual((await self.f.request(temporary)).id, first.id)
        self.assertEqual(self.f.creates, 1)
        self.assertEqual(self.f.facade.voice_channels, [])
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

    async def test_twelve_simultaneous_button_and_meet_requests_create_once(self):
        rooms = await asyncio.gather(*(self.f.request(bool(i % 2)) for i in range(12)))
        self.assertEqual(len({room.id for room in rooms}), 1)
        self.assertEqual(self.f.creates, 1)
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

    async def test_fresh_process_recovers_discord_persistence_without_cleanup_rights(self):
        room = await self.f.request()
        source = '''import asyncio,sys
from test_private_room_reuse import DiscordFixture
async def run():
 f=DiscordFixture(sys.argv[1]); r=await f.request(True)
 assert r.id==int(sys.argv[2]); assert f.creates==0; assert not f.registry.rooms
 print('REUSED_WITHOUT_DELETION_PROVENANCE')
asyncio.run(run())'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', source, str(self.path), str(room.id)],
                                         capture_output=True, text=True, timeout=20,
                                         env={**os.environ, 'PYTHONPATH': os.pathsep.join([
                                             str(Path(__file__).resolve().parent),
                                             str(Path(__file__).resolve().parents[1])])})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('REUSED_WITHOUT_DELETION_PROVENANCE', result.stdout)
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

    async def test_deleted_reference_and_stale_gateway_cache_get_one_replacement(self):
        stale = self.f.decode(self.f.data(777, 42))
        self.f.facade.voice_channels = [stale]
        self.f.registry.created(stale, self.f.member)
        first, second = await asyncio.gather(self.f.request(), self.f.request(True))
        self.assertNotEqual(first.id, 777)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.f.creates, 1)

    async def test_duplicate_existing_rooms_choose_oldest_without_modifying_any(self):
        self.put(self.f.data(1003, 42), self.f.data(1001, 42), self.f.data(1002, 42))
        before = self.path.read_bytes()
        self.assertEqual((await self.f.request(True)).id, 1001)
        self.assertEqual((await self.f.request()).id, 1001)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.f.registry.rooms)
        self.assertEqual(self.f.creates, 0)

    async def test_two_same_named_members_never_share_room(self):
        self.put(self.f.data(1001, 43))
        own = await self.f.request()
        self.assertNotEqual(own.id, 1001)
        other = DiscordFixture(self.path, user_id=43)
        self.assertEqual((await other.request()).id, 1001)
        self.assertEqual(other.creates, 0)

    async def test_same_member_in_other_guild_cannot_supply_reuse_candidate(self):
        other = DiscordFixture(Path(self.temp.name) / 'other.json', guild_id=456)
        other_room = other.decode(other.data(2001, 42))
        self.f.fetch.side_effect = None
        self.f.fetch.return_value = [other_room]
        own = await self.f.request()
        self.assertEqual(own.guild.id, 123)
        self.assertNotEqual(own.id, other_room.id)
        self.assertEqual(self.f.creates, 1)

    async def test_unknown_second_member_or_role_grant_fails_closed_without_edit(self):
        for kind in (0, 1):
            data = self.f.data(1001, 42)
            data['permission_overwrites'].append(dict(id='888', type=kind, allow=str(discord.Permissions(view_channel=True).value), deny='0'))
            self.put(data)
            before = self.path.read_bytes()
            self.assertIsNone(await self.f.request())
            self.assertEqual(self.f.creates, 0)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertIn('kept unchanged', self.f.interaction.followup.send.await_args.args[0])

    async def test_fresh_permissions_override_safe_cached_room(self):
        data = self.f.data(1001, 42)
        self.f.facade.voice_channels = [self.f.decode(data)]
        data['permission_overwrites'][0]['deny'] = '0'
        self.put(data)
        self.assertIsNone(await self.f.request())
        self.assertEqual(self.f.creates, 0)

    async def test_known_deleted_room_is_not_reused_before_cache_removal(self):
        self.put(self.f.data(1001, 42))
        self.f.registry.deleted.add(1001)
        self.assertNotEqual((await self.f.request()).id, 1001)
        self.assertEqual(self.f.creates, 1)

    async def test_live_requesters_current_private_room_is_preferred_over_duplicates(self):
        self.put(self.f.data(1001, 42), self.f.data(1002, 42))
        self.f.guild._voice_states[42] = NS(channel=self.f.decode(self.f.data(1002, 42)))
        self.assertEqual((await self.f.request()).id, 1002)
        self.assertEqual(self.f.creates, 0)

    async def test_missing_bot_access_never_gets_silently_restored(self):
        data = self.f.data(1001, 42)
        data['permission_overwrites'] = data['permission_overwrites'][:2]
        self.put(data)
        before = self.path.read_bytes()
        self.assertIsNone(await self.f.request())
        self.assertEqual(self.f.creates, 0)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_foreign_member_request_is_rejected_before_create(self):
        other = DiscordFixture(Path(self.temp.name) / 'other.json', guild_id=456)
        self.f.interaction.user = other.member
        self.assertIsNone(await self.f.request())
        self.assertEqual(self.f.creates, 0)

    async def test_text_channel_with_private_name_is_never_reused(self):
        data = self.f.data(1001, 42)
        data['type'] = 0
        text_room = discord.TextChannel(state=self.f.client._connection, guild=self.f.guild, data=data)
        self.f.fetch.side_effect = None
        self.f.fetch.return_value = [text_room]
        own = await self.f.request()
        self.assertIsInstance(own, discord.VoiceChannel)
        self.assertEqual(self.f.creates, 1)

    async def test_lookup_failure_never_means_no_room_or_triggers_create(self):
        for failure in (TimeoutError(), PermissionError('synthetic denied')):
            self.f.fetch.side_effect = failure
            with self.assertRaises(type(failure)):
                await self.f.request()
            self.assertEqual(self.f.creates, 0)

    async def test_accepted_create_with_lost_reply_reconciles_without_duplicate(self):
        self.f.uncertain_create = True
        with self.assertRaises(TimeoutError):
            await self.f.request()
        recovered = DiscordFixture(self.path)
        self.assertEqual((await recovered.request()).id, 1001)
        self.assertEqual(recovered.creates, 0)
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

    async def test_revoked_command_gate_stops_before_lookup(self):
        self.f.interaction.response = NS(defer=AsyncMock())
        ns = dict(self.f.ns, require_member=AsyncMock(return_value=False),
                  gbop_private_room=self.f.setup, GBOP_MEETING_LOCKS={}, GBOP_DISCORD_COOLDOWN=DiscordCooldown(),
                  run_private_meeting=run_private_meeting, Literal=__import__('typing').Literal)
        await code('gbop_control', ns)(self.f.interaction, action='room')
        self.f.fetch.assert_not_awaited()
        self.f.create.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
