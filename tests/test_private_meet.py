"""Private /meet flow with fake Discord transport, no accounts or API calls."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.discord_controls import in_voice_channel, private_room_autojoin_allowed
from test_private_voice import code, Entity
from gbop_voice_web.private_room_cleanup import PrivateRoomCleanup
from gbop_voice_web.discord_controls import private_room_owner


class PrivateMeetTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, connected=True, already_here=False):
        room = NS(id=10, mention='#private-meeting')
        current = room if already_here else NS(id=20)
        member = NS(id=42, guild=NS(id=123), voice=NS(channel=current) if connected else None)
        async def move(destination, **kwargs):
            member.voice = NS(channel=destination)
        member.move_to = AsyncMock(side_effect=move)
        ready = asyncio.Event(); ready.set()
        session = NS(ready=ready, voice_client=NS(channel=room))
        interaction = NS(user=member, guild=member.guild, response=NS(defer=AsyncMock()),
                         followup=NS(send=AsyncMock()))
        ns = dict(asyncio=asyncio, discord=NS(Interaction=object, HTTPException=PermissionError),
                  gbop_private_room=AsyncMock(return_value=room),
                  gbop_private_voice_view=Mock(return_value='room-view'),
                  private_room_autojoin_allowed=Mock(return_value=True),
                  gbop_connect_member_voice=AsyncMock(return_value=(room, session)),
                  in_voice_channel=in_voice_channel,
                  GBOP_PRIVATE_ROOMS=PrivateRoomCleanup(), GBOP_MEETING_LOCKS={}, require_member=AsyncMock(return_value=True))
        start = code('gbop_start_private_meeting', ns)
        command = code('meet', ns)
        return interaction, room, session, ns, start, command

    def message(self, interaction):
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
        return interaction.followup.send.await_args.args[0]

    def test_command_is_one_word_with_no_options(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text())
        command = next(n for n in source.body if getattr(n, 'name', '') == 'meet')
        self.assertEqual([arg.arg for arg in command.args.args], ['interaction'])
        self.assertFalse(command.args.kwonlyargs)
        decorator = command.decorator_list[0]
        self.assertEqual(next(k.value.value for k in decorator.keywords if k.arg == 'name'), 'meet')
        self.assertTrue(any(k.arg == 'guild' for k in decorator.keywords))

    async def test_connected_member_moves_and_ai_is_ready(self):
        interaction, room, session, ns, _, command = self.fixture()
        await command(interaction)
        ns['require_member'].assert_awaited_once_with(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        ns['gbop_private_room'].assert_awaited_once_with(interaction, announce=False, prefer_temporary=True)
        interaction.user.move_to.assert_awaited_once_with(room, reason='Member requested /meet with GBOP')
        ns['gbop_connect_member_voice'].assert_awaited_once_with(interaction.user, room)
        self.assertIn('You and GBOP are ready', self.message(interaction))
        self.assertFalse(ns['GBOP_MEETING_LOCKS'][(123, 42)].locked())

    async def test_text_only_gets_private_link_without_move_or_ai_session(self):
        interaction, room, _, ns, _, command = self.fixture(connected=False)
        await command(interaction)
        interaction.user.move_to.assert_not_awaited()
        ns['gbop_connect_member_voice'].assert_not_awaited()
        message = self.message(interaction)
        self.assertIn('Open it below and join voice', message)
        self.assertIn('No second command', message)
        self.assertIn('speech is sent to OpenAI', message)
        self.assertEqual(interaction.followup.send.await_args.kwargs['view'], 'room-view')

    async def test_meeting_reply_explains_preserved_legacy_room(self):
        interaction, _, _, ns, _, command = self.fixture(connected=False)
        ns['GBOP_PRIVATE_ROOMS'].preserved_rooms[(123, 42)] = {77}
        await command(interaction)
        message = self.message(interaction)
        self.assertIn('temporary rooms clear after hang-up', message)
        self.assertIn('<#77>', message)
        self.assertIn('protect their message history', message)

    async def test_already_in_room_reuses_connection_without_move(self):
        interaction, room, _, ns, _, command = self.fixture(already_here=True)
        await command(interaction)
        interaction.user.move_to.assert_not_awaited()
        ns['gbop_connect_member_voice'].assert_awaited_once_with(interaction.user, room)
        self.assertIn('ready', self.message(interaction))

    async def test_inactive_or_revoked_gate_blocks_before_room_or_move(self):
        interaction, _, _, ns, _, command = self.fixture()
        ns['require_member'].return_value = False
        await command(interaction)
        ns['gbop_private_room'].assert_not_awaited()
        interaction.user.move_to.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        ns['gbop_connect_member_voice'].assert_not_awaited()

    async def test_room_setup_failure_does_not_move_or_send_duplicate_error(self):
        interaction, _, _, ns, _, command = self.fixture()
        ns['gbop_private_room'].return_value = None
        await command(interaction)
        interaction.user.move_to.assert_not_awaited()
        ns['gbop_connect_member_voice'].assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_forbidden_move_keeps_manual_join_path_and_does_not_connect(self):
        interaction, _, _, ns, _, command = self.fixture()
        interaction.user.move_to.side_effect = PermissionError('Missing Permissions')
        await command(interaction)
        ns['gbop_connect_member_voice'].assert_not_awaited()
        message = self.message(interaction)
        self.assertIn('Move Members', message)
        self.assertIn('join voice; GBOP joins automatically', message)

    async def test_privacy_change_before_move_fails_closed(self):
        interaction, _, _, ns, _, command = self.fixture()
        ns['private_room_autojoin_allowed'].return_value = False
        await command(interaction)
        interaction.user.move_to.assert_not_awaited()
        ns['gbop_connect_member_voice'].assert_not_awaited()
        self.assertIn('private permissions changed', self.message(interaction))

    async def test_privacy_change_during_move_fails_closed(self):
        interaction, _, _, ns, _, command = self.fixture()
        ns['private_room_autojoin_allowed'].side_effect = [True, False]
        await command(interaction)
        interaction.user.move_to.assert_awaited_once()
        ns['gbop_connect_member_voice'].assert_not_awaited()
        self.assertIn('has not started listening', self.message(interaction))

    async def test_gateway_delay_is_awaited_without_holding_guild_lock(self):
        interaction, room, _, ns, _, command = self.fixture()
        async def move_later(*args, **kwargs):
            asyncio.get_running_loop().call_later(0.001, setattr, interaction.user, 'voice', NS(channel=room))
        interaction.user.move_to.side_effect = move_later
        await command(interaction)
        ns['gbop_connect_member_voice'].assert_awaited_once()
        self.assertIn('You and GBOP are ready', self.message(interaction))

    async def test_departure_during_move_does_not_start_voice(self):
        interaction, _, _, ns, _, command = self.fixture()
        async def leave(*args, **kwargs):
            interaction.user.voice = None
        interaction.user.move_to.side_effect = leave
        await command(interaction)
        ns['gbop_connect_member_voice'].assert_not_awaited()
        self.assertIn("couldn't confirm your move", self.message(interaction))

    async def test_newer_member_navigation_is_not_overridden(self):
        interaction, _, _, ns, _, command = self.fixture()
        async def navigate(*args, **kwargs):
            interaction.user.voice = NS(channel=NS(id=30))
        interaction.user.move_to.side_effect = navigate
        await command(interaction)
        interaction.user.move_to.assert_awaited_once()
        ns['gbop_connect_member_voice'].assert_not_awaited()
        self.assertIn("couldn't confirm your move", self.message(interaction))

    async def test_move_gateway_timeout_is_not_reported_as_ready(self):
        interaction, _, _, ns, _, command = self.fixture()
        interaction.user.move_to.side_effect = None
        async def timed_out(awaitable, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError()
        ns['asyncio'] = NS(Lock=asyncio.Lock, TimeoutError=asyncio.TimeoutError, wait_for=timed_out)
        await command(interaction)
        ns['gbop_connect_member_voice'].assert_not_awaited()
        self.assertIn("couldn't confirm your move", self.message(interaction))

    async def test_full_pool_reports_browser_fallback_without_claiming_success(self):
        interaction, _, _, ns, _, command = self.fixture(already_here=True)
        ns['gbop_connect_member_voice'].side_effect = RuntimeError('All GBOP Discord voice slots are in use.')
        await command(interaction)
        message = self.message(interaction)
        self.assertIn('slots are in use', message)
        self.assertIn('browser session', message)
        self.assertNotIn('You and GBOP are ready', message)

    async def test_ai_not_ready_is_reported_separately(self):
        interaction, _, _, ns, _, command = self.fixture(already_here=True)
        async def timed_out(awaitable, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError()
        ns['asyncio'] = NS(Lock=asyncio.Lock, TimeoutError=asyncio.TimeoutError, wait_for=timed_out)
        await command(interaction)
        message = self.message(interaction)
        self.assertIn('AI connection is still starting', message)
        self.assertNotIn('You and GBOP are ready', message)

    async def test_departure_during_ai_start_is_not_reported_as_ready(self):
        interaction, _, session, ns, _, command = self.fixture(already_here=True)
        async def leave():
            interaction.user.voice = None
        session.ready = NS(wait=AsyncMock(side_effect=leave))
        await command(interaction)
        self.assertIn('You left the private meeting', self.message(interaction))

    async def test_repeated_commands_are_serialized_per_member(self):
        interaction, room, _, ns, _, command = self.fixture()
        running = 0
        high_water = 0
        async def start(_):
            nonlocal running, high_water
            running += 1
            high_water = max(high_water, running)
            await asyncio.sleep(0)
            running -= 1
        ns['gbop_start_private_meeting'] = AsyncMock(side_effect=start)
        await asyncio.gather(command(interaction), command(interaction))
        self.assertEqual(high_water, 1)

    async def test_different_members_do_not_share_meeting_lock(self):
        interaction, _, _, ns, _, command = self.fixture()
        other = NS(user=NS(id=43), guild=interaction.guild,
                   response=NS(defer=AsyncMock()))
        both_started = asyncio.Event()
        started = 0
        async def start(_):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
        ns['gbop_start_private_meeting'] = AsyncMock(side_effect=start)
        await asyncio.gather(command(interaction), command(other))
        self.assertEqual(started, 2)

    async def test_move_event_does_not_duplicate_meeting_or_notification(self):
        lock = asyncio.Lock()
        member = NS(guild=NS(id=123), id=42)
        ns = dict(asyncio=asyncio, GTOP_GUILD_ID=123,
                  private_room_autojoin_allowed=Mock(return_value=True),
                  GBOP_MEETING_LOCKS={(123, 42): lock},
                  gbop_voice_member_allowed=Mock(), gbop_connect_member_voice=AsyncMock())
        auto = code('gbop_autojoin_private_room', ns)
        async with lock:
            await auto(member, NS(id=10))
        ns['gbop_voice_member_allowed'].assert_not_called()
        ns['gbop_connect_member_voice'].assert_not_awaited()


class PrivateMeetMigrationTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, manage=True, missing_access=False):
        everyone = Entity(id=1)
        member = Entity(id=42, bot=False, display_name='Rexercise', voice=None)
        bot = Entity(id=99, bot=True, guild_permissions=NS(manage_channels=manage))
        acl = {everyone: NS(view_channel=False, connect=False),
               member: NS(view_channel=True, connect=True, speak=True, use_voice_activation=True),
               bot: NS(view_channel=True, connect=not missing_access, speak=True, use_voice_activation=True)}
        def room(channel_id, overwrites):
            result = NS(id=channel_id, name='gbop-private-rexercise', mention=f'<#{channel_id}>',
                        overwrites=overwrites, edit=AsyncMock(), members=[], last_message_id=999)
            result.overwrites_for = lambda person: overwrites.get(person, NS())
            result.permissions_for = lambda person: NS(manage_roles=True)
            return result
        old = room(10, acl)
        guild = NS(id=123, me=bot, default_role=everyone, voice_channels=[old],
                   get_member=lambda member_id: bot if member_id == bot.id else member)
        member.guild = guild
        async def create(**kwargs):
            await asyncio.sleep(0)
            new = room(10 + len(guild.voice_channels), kwargs['overwrites'])
            guild.voice_channels.append(new)
            return new
        guild.create_voice_channel = AsyncMock(side_effect=create)
        interaction = NS(guild=guild, user=member, followup=NS(send=AsyncMock()))
        registry = PrivateRoomCleanup()
        lock = asyncio.Lock()
        ns = dict(asyncio=asyncio, re=__import__('re'), private_room_owner=private_room_owner,
                  GBOP_PRIVATE_ROOMS=registry, GBOP_VOICE_CLIENTS=[NS(user=bot)],
                  gbop_voice_control_lock=lambda _: lock, gbop_private_voice_view=lambda *args: None,
                  discord=NS(PermissionOverwrite=lambda **kwargs: NS(**kwargs), Forbidden=PermissionError))
        setup = code('gbop_private_room', ns)
        return setup, interaction, old, registry

    async def test_meet_creates_temporary_room_without_changing_legacy(self):
        setup, interaction, old, registry = self.fixture()
        old_acl = dict(old.overwrites)
        new = await setup(interaction, announce=False, prefer_temporary=True)
        self.assertNotEqual(old.id, new.id)
        old.edit.assert_not_awaited()
        self.assertEqual(old.overwrites, old_acl)
        self.assertEqual(old.last_message_id, 999)
        self.assertIn(new.id, registry.rooms)
        self.assertNotIn(old.id, registry.rooms)
        self.assertTrue(all(acl.send_messages is False for acl in new.overwrites.values()))
        self.assertIn('<#10>', registry.notice(123, 42))
        self.assertIn('protect their message history', registry.notice(123, 42))

    async def test_repeated_and_concurrent_meet_reuses_one_tracked_room(self):
        setup, interaction, old, registry = self.fixture()
        first, second = await asyncio.gather(*[
            setup(interaction, announce=False, prefer_temporary=True) for _ in range(2)])
        self.assertIs(first, second)
        interaction.guild.create_voice_channel.assert_awaited_once()
        old.edit.assert_not_awaited()
        self.assertEqual(len(registry.rooms), 1)

    async def test_room_command_preserves_legacy_reuse_behavior(self):
        setup, interaction, old, registry = self.fixture()
        result = await setup(interaction, announce=False)
        self.assertIs(result, old)
        interaction.guild.create_voice_channel.assert_not_awaited()
        old.edit.assert_not_awaited()
        self.assertNotIn(old.id, registry.rooms)

    async def test_missing_manage_channels_reuses_safe_legacy_with_retention_notice(self):
        setup, interaction, old, registry = self.fixture(manage=False)
        result = await setup(interaction, announce=False, prefer_temporary=True)
        self.assertIs(result, old)
        interaction.guild.create_voice_channel.assert_not_awaited()
        old.edit.assert_not_awaited()
        self.assertIn('Manage Channels', registry.notice(123, 42))
        self.assertIn('stays after hang-up', registry.notice(123, 42))

    async def test_legacy_fallback_with_missing_bot_access_is_never_modified(self):
        setup, interaction, old, registry = self.fixture(manage=False, missing_access=True)
        self.assertIsNone(await setup(interaction, announce=False, prefer_temporary=True))
        old.edit.assert_not_awaited()
        interaction.guild.create_voice_channel.assert_not_awaited()
        self.assertIn('kept unchanged', interaction.followup.send.await_args.args[0])

    async def test_tracked_room_privacy_change_does_not_create_duplicate_or_bypass_checks(self):
        setup, interaction, old, registry = self.fixture()
        room = await setup(interaction, announce=False, prefer_temporary=True)
        room.overwrites[Entity(id=88)] = NS(view_channel=True, connect=True)
        self.assertIsNone(await setup(interaction, announce=False, prefer_temporary=True))
        interaction.guild.create_voice_channel.assert_awaited_once()
        room.edit.assert_not_awaited()
        self.assertIn('shared access', interaction.followup.send.await_args.args[0])

    async def test_restart_preserves_previous_room_and_creates_new_tracked_room(self):
        setup, interaction, old, registry = self.fixture()
        first = await setup(interaction, announce=False, prefer_temporary=True)
        registry.rooms.clear()  # Process restart loses only transient provenance.
        second = await setup(interaction, announce=False, prefer_temporary=True)
        self.assertNotEqual(first.id, second.id)
        first.edit.assert_not_awaited()
        old.edit.assert_not_awaited()
        self.assertEqual(registry.preserved_rooms[(123, 42)], {old.id, first.id})

    async def test_preserved_room_notice_survives_temporary_cleanup_notice_clear(self):
        setup, interaction, old, registry = self.fixture()
        await setup(interaction, announce=False, prefer_temporary=True)
        registry.notices[(123, 42)] = 'Room kept: transient previous failure'
        registry.notices.pop((123, 42))  # Successful remove_if_empty clears this map only.
        self.assertIn('<#10>', registry.notice(123, 42))
        self.assertIn('protect their message history', registry.notice(123, 42))


if __name__ == '__main__':
    unittest.main()
