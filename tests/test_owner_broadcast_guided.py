"""Offline guided owner-message tests against the real, pinned Discord SDK.

Only named, side-effect-free definitions are AST-extracted from bot.py. The bot
module is never imported, its startup never runs, and every guild, response,
database hook and delivery transport below is synthetic. Actual discord.py
decorators, bound callbacks, components, modals and serializers remain intact.
"""
import ast
import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path
import time
from types import SimpleNamespace as NS
from typing import Literal
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from gbop_voice_web.member_access import MEMBER_ACCESS_UNAVAILABLE
from gbop_voice_web.owner_broadcast import (
    AUDIENCE_LABELS, PREVIEW_SECONDS, build_draft, owner_message, parse_selection,
)


BOT = Path(__file__).resolve().parents[1] / 'bot.py'
EXTRACTED_NAMES = {
    '_current_owner_message_member', '_resolve_owner_broadcast_draft',
    '_send_owner_broadcast', 'OwnerBroadcastView', 'OwnerBroadcastMemberPicker',
    'OwnerBroadcastWizard', 'OwnerBroadcastSetupView',
    'OwnerBroadcastComposeModal', 'OwnerBroadcastGuidedPreview', 'gbopmessage',
}


def member(user_id, *, role=True, bot=False, guild_id=10, name=None):
    return NS(id=user_id, display_name=name or f'Member {user_id}', role=role,
              bot=bot, guild=NS(id=guild_id), send=AsyncMock(), add_roles=AsyncMock())


def components(view):
    """Exercise the SDK wire serializer, including row layout validation."""
    return [item for row in view.to_components() for item in row['components']]


class SyntheticInteraction:
    """Enforce one initial acknowledgement, while retaining inspectable mocks."""
    def __init__(self, user_id=999, guild_id=10):
        self.user = member(user_id)
        self.guild_id = guild_id
        self.data = {}
        self.events = []
        self.done = False
        self.message = NS(edit=AsyncMock())
        self.response = NS(is_done=lambda: self.done)
        for name in ('send_message', 'edit_message', 'defer', 'send_modal'):
            setattr(self.response, name, AsyncMock(side_effect=self._initial(name)))
        self.followup = NS(send=AsyncMock(side_effect=self._later('followup')))
        self.edit_original_response = AsyncMock(side_effect=self._later('edit_original'))
        self.original_response = AsyncMock(return_value=self.message)

    def _validate(self, args, kwargs):
        view = kwargs.get('view')
        if view is not None:
            components(view)
        embed = kwargs.get('embed')
        if embed is not None:
            embed.to_dict()

    def _initial(self, name):
        async def call(*args, **kwargs):
            if self.done:
                raise AssertionError('Interaction acknowledged more than once')
            self._validate(args, kwargs)
            if name == 'send_modal':
                args[0].to_dict()
            self.done = True
            self.events.append(name)
            return self.message
        return call

    def _later(self, name):
        async def call(*args, **kwargs):
            if not self.done:
                raise AssertionError('Followup or edit before acknowledgement')
            self._validate(args, kwargs)
            self.events.append(name)
            return self.message
        return call


class GuidedOwnerBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Construction is local only: never login, connect, start, or run this client.
        self.sdk_client = discord.Client(intents=discord.Intents.none())
        self.tree = app_commands.CommandTree(self.sdk_client)
        self.ns = dict(
            asyncio=asyncio, time=time, discord=discord, app_commands=app_commands,
            tree=self.tree, GUILD=discord.Object(id=10), Literal=Literal,
            GTOP_OWNER_USER_ID=999, GTOP_GUILD_ID=10, logger=Mock(),
            client=NS(get_guild=Mock()), has_member_role=lambda m: m.role,
            member_access_error=Mock(return_value=None),
            db=Mock(side_effect=AssertionError('No database calls from guided broadcast')),
            ensure_member_record=Mock(side_effect=AssertionError('No activation or profile writes')),
            MEMBER_ACCESS_UNAVAILABLE=MEMBER_ACCESS_UNAVAILABLE,
            AUDIENCE_LABELS=AUDIENCE_LABELS, PREVIEW_SECONDS=PREVIEW_SECONDS,
            build_draft=build_draft, owner_message=owner_message, parse_selection=parse_selection,
            _authorized_scheduled_member=AsyncMock(),
        )
        self.ns['is_owner'] = lambda m: m.id == self.ns['GTOP_OWNER_USER_ID']
        nodes = [n for n in ast.parse(BOT.read_text()).body
                 if getattr(n, 'name', '') in EXTRACTED_NAMES]
        self.assertEqual({n.name for n in nodes}, EXTRACTED_NAMES)
        # Retain all class, component and command decorators unchanged.
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), 'exec'), self.ns)
        self.command = self.ns['gbopmessage']
        self.members = {uid: member(uid) for uid in (1, 2, 3)}
        self.guild(self.members.values())
        self.ns['_authorized_scheduled_member'].side_effect = self.members.get

    def guild(self, members, *, listing_error=None):
        listed = list(members)
        async def fetch_members(**kwargs):
            self.assertEqual(kwargs, {'limit': None})
            for candidate in listed:
                yield candidate
            if listing_error:
                raise listing_error
        result = NS(id=10, members=[], fetch_members=fetch_members,
                    fetch_member=AsyncMock(side_effect={m.id: m for m in listed}.get))
        self.ns['client'].get_guild.return_value = result
        return result

    async def start(self, **kwargs):
        interaction = SyntheticInteraction()
        await self.command.callback(interaction, **kwargs)
        view = interaction.response.send_message.await_args.kwargs['view']
        return view.wizard, view, interaction

    async def choose_audience(self, view, audience, interaction=None):
        view.choose_audience._values = [audience]
        interaction = interaction or SyntheticInteraction()
        await view.choose_audience.callback(interaction)
        return view.wizard.current_view, interaction

    async def choose_members(self, view, ids=(2,), interaction=None):
        # SDK UserSelect receives resolved values from Discord; inject synthetic
        # resolved members while keeping its actual .values and bound callback.
        view.choose_members._values = [member(uid, role=False) for uid in ids]
        interaction = interaction or SyntheticInteraction()
        await view.choose_members.callback(interaction)
        return view.wizard.current_view, interaction

    async def setup_compose(self, audience='all', ids=(2,)):
        wizard, view, _ = await self.start()
        view, _ = await self.choose_audience(view, audience)
        if audience in ('selected', 'all_except'):
            view, _ = await self.choose_members(view, ids)
        self.assertEqual(wizard.stage, 'compose')
        return wizard, view

    async def open_modal(self, view, text='Synthetic announcement\nFor @everyone'):
        interaction = SyntheticInteraction()
        await view.compose.callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        modal.message_text._value = text
        return modal, interaction

    async def preview(self, audience='all', ids=(2,), text='Synthetic announcement\nFor @everyone'):
        wizard, view = await self.setup_compose(audience, ids)
        modal, _ = await self.open_modal(view, text)
        interaction = SyntheticInteraction()
        await modal.on_submit(interaction)
        preview = interaction.edit_original_response.await_args.kwargs['view']
        self.assertIsInstance(preview, self.ns['OwnerBroadcastGuidedPreview'])
        return wizard, preview, interaction

    def assert_no_delivery(self):
        for recipient in self.members.values():
            recipient.send.assert_not_awaited()
            recipient.add_roles.assert_not_awaited()
        self.ns['db'].assert_not_called()
        self.ns['ensure_member_record'].assert_not_called()

    def assert_no_mentions(self, value):
        self.assertEqual(value.to_dict(), discord.AllowedMentions.none().to_dict())

    async def test_real_discord_command_schema_has_optional_text_and_four_audiences(self):
        self.assertEqual(discord.__version__, '2.7.1')
        self.assertIsInstance(self.command, app_commands.Command)
        schema = self.command.to_dict(self.tree)
        options = {item['name']: item for item in schema['options']}
        self.assertEqual(schema['name'], 'gbopmessage')
        self.assertEqual(options['text']['type'], discord.AppCommandOptionType.string.value)
        self.assertFalse(options['text']['required'])
        self.assertTrue(all(not item['required'] for item in options.values()))
        self.assertEqual({choice['value'] for choice in options['audience']['choices']}, set(AUDIENCE_LABELS))

    async def test_bare_command_renders_audience_privately_without_lookups_or_send(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        wizard, view, interaction = await self.start()
        self.assertEqual(wizard.stage, 'audience')
        self.assertEqual(interaction.events, ['send_message'])
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent['ephemeral'])
        self.assert_no_mentions(sent['allowed_mentions'])
        serialized = components(view)
        select = next(item for item in serialized if item['type'] == 3)
        self.assertEqual({item['value'] for item in select['options']}, set(AUDIENCE_LABELS))
        self.assertEqual([item.get('label') for item in serialized if item['type'] == 2], ['Cancel'])
        self.assertIs(wizard.preview_message, interaction.message)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()
        self.ns['client'].get_guild.assert_not_called()
        self.assert_no_delivery()

    async def test_guided_command_shortcuts_choose_appropriate_first_step(self):
        for kwargs, expected in (
            ({'audience': 'selected'}, 'members'),
            ({'audience': 'all_except'}, 'members'),
            ({'audience': 'all_server_members'}, 'compose'),
            ({'audience': 'selected', 'members': '<@2>'}, 'compose'),
            ({'audience': 'all_except', 'members': '2'}, 'compose'),
        ):
            with self.subTest(kwargs=kwargs):
                wizard, view, _ = await self.start(**kwargs)
                self.assertEqual(wizard.stage, expected)
                self.assertTrue(components(view))
        self.assert_no_delivery()

    async def test_all_four_audiences_reach_exact_preview_then_existing_transport(self):
        self.members[3].role = False
        self.ns['_authorized_scheduled_member'].side_effect = lambda uid: self.members.get(uid) if uid != 3 else None
        expected = {'all': [1, 2], 'selected': [3], 'all_server_members': [1, 2, 3], 'all_except': [1]}
        for audience, ids in expected.items():
            with self.subTest(audience=audience):
                for m in self.members.values():
                    m.send.reset_mock()
                wizard, view, interaction = await self.preview(audience, (3,) if audience == 'selected' else (2,))
                self.assertEqual([r.user_id for r in view.draft.recipients], ids)
                self.assert_no_delivery()
                self.assertEqual(interaction.events, ['defer', 'edit_original'])
                edit = interaction.edit_original_response.await_args.kwargs
                self.assertEqual(edit['content'], owner_message('Synthetic announcement\nFor @everyone'))
                self.assert_no_mentions(edit['allowed_mentions'])
                self.assertIs(view.preview_message, interaction.message)
                await view.confirm_send.callback(SyntheticInteraction())
                for uid, m in self.members.items():
                    self.assertEqual(m.send.await_count, int(uid in ids))
                    if uid in ids:
                        self.assertEqual(m.send.await_args.args[0], edit['content'])
                        self.assert_no_mentions(m.send.await_args.kwargs['allowed_mentions'])
                self.assertEqual(view.state, 'finished')

    async def test_native_user_select_has_any_role_schema_and_exact_deduplicated_ids(self):
        wizard, view, _ = await self.start(audience='selected')
        self.assertIsInstance(view.choose_members, discord.ui.UserSelect)
        select = next(item for item in components(view) if item['type'] == 5)
        self.assertEqual((select['min_values'], select['max_values']), (1, 25))
        self.assertIn('any role', select['placeholder'])
        self.assertNotIn('options', select)
        view, interaction = await self.choose_members(view, (3, 2, 3))
        self.assertEqual(wizard.selected_ids, (3, 2))
        self.assertEqual(interaction.events, ['edit_message'])
        self.ns['client'].get_guild.assert_not_called()
        self.assert_no_delivery()

    async def test_member_picker_defaults_survive_back_without_expanding_selection(self):
        wizard, compose = await self.setup_compose('selected', (2, 3))
        await compose.back.callback(SyntheticInteraction())
        self.assertEqual(wizard.stage, 'members')
        picker = next(item for item in components(wizard.current_view) if item['type'] == 5)
        self.assertEqual([(value['id'], value['type']) for value in picker['default_values']], [(2, 'user'), (3, 'user')])

    async def test_modal_real_schema_limits_and_modal_opening_is_first_ack(self):
        wizard, view = await self.setup_compose()
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        modal, interaction = await self.open_modal(view)
        self.assertIsInstance(modal, discord.ui.Modal)
        self.assertEqual(interaction.events, ['send_modal'])
        schema = modal.to_dict()
        field = schema['components'][0]['components'][0]
        self.assertEqual(schema['title'], 'Write GBOP Message')
        self.assertEqual((field['type'], field['style']), (4, 2))
        self.assertEqual((field['min_length'], field['max_length'], field['required']), (1, 1800, True))
        self.assertLessEqual(modal.timeout, PREVIEW_SECONDS)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()
        self.assert_no_delivery()

    async def test_modal_acknowledges_before_fresh_membership_lookup(self):
        wizard, view = await self.setup_compose()
        modal, _ = await self.open_modal(view)
        interaction = SyntheticInteraction()
        original = self.ns['_resolve_owner_broadcast_draft']
        async def checked(*args):
            self.assertEqual(interaction.events, ['defer'])
            return await original(*args)
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=checked)
        await modal.on_submit(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=False)
        self.assert_no_delivery()

    async def test_modal_rejects_blank_and_oversized_values_without_lookup(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for text in ('', '  \n ', 'x' * 1801):
            with self.subTest(length=len(text)):
                wizard, view = await self.setup_compose()
                modal, _ = await self.open_modal(view, text)
                interaction = SyntheticInteraction()
                await modal.on_submit(interaction)
                self.assertEqual(interaction.events, ['send_message'])
                self.assertEqual(wizard.stage, 'compose')
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_maximum_length_multiline_text_is_preserved_exactly(self):
        text = 'Line one\n' + 'x' * (1800 - len('Line one\n'))
        _, view, interaction = await self.preview(text=text)
        self.assertEqual(view.draft.message, owner_message(text))
        self.assertEqual(interaction.edit_original_response.await_args.kwargs['content'], view.draft.message)

    async def test_interrupted_modal_reopens_and_only_newest_token_can_submit(self):
        wizard, view = await self.setup_compose()
        old, _ = await self.open_modal(view, 'Abandoned message')
        newest, _ = await self.open_modal(view, 'Latest message')
        old_interaction = SyntheticInteraction()
        await old.on_submit(old_interaction)
        self.assertEqual(old_interaction.events, ['send_message'])
        self.ns['client'].get_guild.assert_not_called()
        newest_interaction = SyntheticInteraction()
        await newest.on_submit(newest_interaction)
        self.assertEqual(wizard.stage, 'preview')
        self.assertEqual(wizard.current_view.draft.message, owner_message('Latest message'))
        self.assert_no_delivery()

    async def test_duplicate_modal_submissions_publish_only_one_preview(self):
        wizard, setup = await self.setup_compose()
        modal, _ = await self.open_modal(setup)
        interactions = [SyntheticInteraction() for _ in range(4)]
        await asyncio.gather(*(modal.on_submit(interaction) for interaction in interactions))
        self.assertEqual(sum(i.edit_original_response.await_count for i in interactions), 1)
        self.assertEqual(sum(i.response.defer.await_count for i in interactions), 1)
        self.assertEqual(sum(i.response.send_message.await_count for i in interactions), 3)
        self.assert_no_delivery()

    async def test_duplicate_audience_and_member_clicks_cannot_replace_newer_step(self):
        wizard, old, _ = await self.start()
        old.choose_audience._values = ['selected']
        interactions = [SyntheticInteraction() for _ in range(3)]
        await asyncio.gather(*(old.choose_audience.callback(i) for i in interactions))
        self.assertEqual(sum(i.response.edit_message.await_count for i in interactions), 1)
        picker = wizard.current_view
        picker.choose_members._values = [member(2)]
        interactions = [SyntheticInteraction() for _ in range(3)]
        await asyncio.gather(*(picker.choose_members.callback(i) for i in interactions))
        self.assertEqual(sum(i.response.edit_message.await_count for i in interactions), 1)
        self.assertEqual(wizard.selected_ids, (2,))
        self.assertEqual(wizard.stage, 'compose')

    async def test_back_cancel_and_audience_change_invalidate_open_modals(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for action in ('back', 'cancel', 'change'):
            wizard, view = await self.setup_compose('selected')
            modal, _ = await self.open_modal(view)
            if action == 'change':
                await view.back.callback(SyntheticInteraction())
                await wizard.current_view.back.callback(SyntheticInteraction())
                await self.choose_audience(wizard.current_view, 'all_server_members')
            else:
                await getattr(view, action).callback(SyntheticInteraction())
            interaction = SyntheticInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(interaction.events, ['send_message'])
            self.assertTrue(view.is_finished())
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_preview_edits_invalidate_confirmation_and_preserve_message_defaults(self):
        self.ns['_send_owner_broadcast'] = AsyncMock()
        for action, stage in (('edit_message', 'compose'), ('edit_audience', 'audience'), ('edit_members', 'members')):
            wizard, old, _ = await self.preview('selected')
            await getattr(old, action).callback(SyntheticInteraction())
            self.assertEqual(wizard.stage, stage)
            self.assertEqual(old.state, 'replaced')
            self.assertTrue(old.is_finished())
            await old.confirm_send.callback(SyntheticInteraction())
            if stage == 'compose':
                reopened, _ = await self.open_modal(wizard.current_view)
                self.assertEqual(reopened.message_text.default, 'Synthetic announcement\nFor @everyone')
                self.assertEqual(wizard.current_view.compose.label, 'Edit message')
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_changing_audience_clears_previous_selection_and_rebuilds_snapshot(self):
        wizard, old, _ = await self.preview('selected', (2,))
        await old.edit_audience.callback(SyntheticInteraction())
        setup, _ = await self.choose_audience(wizard.current_view, 'all_server_members')
        self.assertEqual(wizard.selected_ids, ())
        self.members[4] = member(4, role=False)
        self.guild(self.members.values())
        modal, _ = await self.open_modal(setup, 'Changed message')
        await modal.on_submit(SyntheticInteraction())
        self.assertEqual([r.user_id for r in wizard.current_view.draft.recipients], [1, 2, 3, 4])
        self.assertEqual(wizard.current_view.draft.message, owner_message('Changed message'))
        self.assertEqual(wizard.current_view.seen_pages, {0})
        self.assertEqual([r.user_id for r in old.draft.recipients], [2])

    async def test_preview_requires_every_page_and_only_confirms_once(self):
        self.members = {uid: member(uid, role=False) for uid in range(1, 26)}
        self.guild(self.members.values())
        _, view, _ = await self.preview('all_server_members')
        self.assertEqual(view.draft.page_count, 3)
        self.assertTrue(view.confirm_send.disabled)
        await view.confirm_send.callback(SyntheticInteraction())
        self.assert_no_delivery()
        seen = [r.user_id for r in view.draft.page(0)]
        for page in (1, 2):
            interaction = SyntheticInteraction()
            await view.next_page.callback(interaction)
            self.assertEqual(view.page_index, page)
            self.assertEqual(interaction.response.edit_message.await_args.kwargs['content'], view.draft.message)
            seen += [r.user_id for r in view.draft.page(page)]
        self.assertEqual(seen, list(range(1, 26)))
        self.assertFalse(view.confirm_send.disabled)
        await asyncio.gather(*(view.confirm_send.callback(SyntheticInteraction()) for _ in range(4)))
        self.assertEqual(view.state, 'finished')
        for recipient in self.members.values():
            recipient.send.assert_awaited_once()

    async def test_failed_page_render_does_not_unlock_confirmation(self):
        self.members = {uid: member(uid) for uid in range(1, 12)}
        self.guild(self.members.values())
        _, view, _ = await self.preview('all_server_members')
        interaction = SyntheticInteraction()
        interaction.response.edit_message.side_effect = TimeoutError('Synthetic page transport')
        with self.assertRaises(TimeoutError):
            await view.next_page.callback(interaction)
        self.assertEqual(view.seen_pages, {0})
        self.assertEqual(view.page_index, 0)
        self.assertTrue(view.confirm_send.disabled)
        await view.confirm_send.callback(SyntheticInteraction())
        self.assert_no_delivery()

    async def test_frozen_preview_rechecks_membership_and_never_adds_new_joiners(self):
        wizard, view, _ = await self.preview('all_server_members')
        departed = self.members.pop(2)
        self.members[3].bot = True
        self.members[4] = member(4, role=False)
        guild = self.guild(self.members.values())
        with self.assertRaises(FrozenInstanceError):
            view.draft.message = 'Tampered'
        await view.confirm_send.callback(SyntheticInteraction())
        self.assertEqual([call.args[0] for call in guild.fetch_member.await_args_list], [1, 2, 3])
        self.members[1].send.assert_awaited_once()
        for m in (departed, self.members[3], self.members[4]):
            m.send.assert_not_awaited()
        self.ns['member_access_error'].assert_not_called()
        self.ns['db'].assert_not_called()

    async def test_selected_no_role_member_requires_fresh_human_membership(self):
        self.members[2].role = False
        self.ns['member_access_error'].side_effect = AssertionError('Receive-only requires no GBOP access check')
        guild = self.guild(self.members.values())
        _, view, _ = await self.preview('selected', (2,))
        guild.fetch_member.assert_awaited_once_with(2)
        self.assertEqual([r.user_id for r in view.draft.recipients], [2])
        await view.confirm_send.callback(SyntheticInteraction())
        self.assertEqual(guild.fetch_member.await_count, 2)
        self.members[2].send.assert_awaited_once()
        self.members[2].add_roles.assert_not_awaited()
        self.ns['_authorized_scheduled_member'].assert_not_awaited()
        self.ns['db'].assert_not_called()

    async def test_current_gbop_eligibility_is_rechecked_for_default_and_exclusions(self):
        for audience in ('all', 'all_except'):
            for m in self.members.values():
                m.send.reset_mock()
            _, view, _ = await self.preview(audience, (2,))
            self.ns['_authorized_scheduled_member'].reset_mock()
            self.ns['_authorized_scheduled_member'].side_effect = lambda uid: self.members[1] if uid == 1 else None
            await view.confirm_send.callback(SyntheticInteraction())
            self.assertEqual([call.args[0] for call in self.ns['_authorized_scheduled_member'].await_args_list],
                             [1, 2, 3] if audience == 'all' else [1, 3])
            self.members[1].send.assert_awaited_once()
            self.members[2].send.assert_not_awaited()
            self.members[3].send.assert_not_awaited()

    async def test_preview_cancel_and_confirm_race_has_one_terminal_action(self):
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(3, 0, 0))
        _, view, _ = await self.preview()
        await asyncio.gather(view.cancel_send.callback(SyntheticInteraction()),
                             view.confirm_send.callback(SyntheticInteraction()))
        self.assertEqual(view.state, 'cancelled')
        await view.edit_message.callback(SyntheticInteraction())
        await view.confirm_send.callback(SyntheticInteraction())
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_expired_setup_modal_and_preview_cannot_continue(self):
        self.ns['_send_owner_broadcast'] = AsyncMock()
        wizard, view, _ = await self.start()
        wizard.expires_at = 0
        await self.choose_audience(view, 'all')
        self.assertTrue(wizard.closed)
        wizard, setup = await self.setup_compose()
        modal, _ = await self.open_modal(setup)
        wizard.expires_at = 0
        await modal.on_submit(SyntheticInteraction())
        self.assertTrue(wizard.closed)
        wizard, preview, _ = await self.preview()
        wizard.expires_at = 0
        await preview.confirm_send.callback(SyntheticInteraction())
        self.assertTrue(wizard.closed)
        self.ns['_send_owner_broadcast'].assert_not_awaited()
        self.assert_no_delivery()

    async def test_new_steps_keep_original_absolute_expiry(self):
        wizard, view, _ = await self.start()
        deadline = wizard.expires_at
        view, _ = await self.choose_audience(view, 'selected')
        view, _ = await self.choose_members(view)
        modal, _ = await self.open_modal(view)
        await modal.on_submit(SyntheticInteraction())
        self.assertEqual(wizard.expires_at, deadline)
        self.assertEqual(wizard.current_view.expires_at, deadline)
        self.assertLessEqual(wizard.current_view.timeout, PREVIEW_SECONDS)

    async def test_sdk_scheduled_modal_and_page_interactions_keep_absolute_timeout(self):
        # Run the SDK's actual listener, scheduler and timer. An accepted
        # interaction normally refreshes its inactivity timeout; guided drafts
        # must retain their original deadline instead.
        for step in ('compose', 'page'):
            with self.subTest(step=step):
                if step == 'compose':
                    wizard, view = await self.setup_compose()
                    button = view.compose
                else:
                    self.members = {uid: member(uid) for uid in range(1, 12)}
                    self.guild(self.members.values())
                    wizard, view, _ = await self.preview('all_server_members')
                    button = view.next_page
                deadline = time.monotonic() + 0.3
                wizard.expires_at = deadline
                if step == 'page':
                    view.expires_at = deadline
                view.timeout = 0.3
                expired = asyncio.Event()
                async def record_expiry(**kwargs):
                    self.assertIsNone(kwargs['view'])
                    self.assertIn('expired', kwargs['content'])
                    expired.set()
                wizard.preview_message.edit.side_effect = record_expiry
                view.on_error = AsyncMock()
                view._start_listening_from_store(Mock())
                try:
                    await asyncio.sleep(0.1)
                    interaction = SyntheticInteraction()
                    await view._scheduled_task(button, interaction)
                    view.on_error.assert_not_awaited()
                    self.assertEqual(interaction.events, ['send_modal'] if step == 'compose' else ['edit_message'])
                    self.assertLessEqual(view.timeout, 0.22)
                    self.assertLessEqual(view._BaseView__timeout_expiry, deadline + 0.02)
                    self.assertTrue(await asyncio.wait_for(view.wait(), 1))
                    await asyncio.wait_for(expired.wait(), 1)
                    self.assertTrue(wizard.closed)
                finally:
                    view.stop()

    async def test_queued_timeout_cannot_overwrite_inflight_confirmation(self):
        wizard, view, _ = await self.preview()
        sending, finish = asyncio.Event(), asyncio.Event()
        async def send_once(draft):
            sending.set()
            await finish.wait()
            return 3, 0, 0
        self.ns['_send_owner_broadcast'] = AsyncMock(side_effect=send_once)
        await wizard.lock.acquire()
        confirmation = asyncio.create_task(view.confirm_send.callback(SyntheticInteraction()))
        await asyncio.sleep(0)  # Queue confirmation first behind the held lock.
        timeout = asyncio.create_task(view.on_timeout())
        await asyncio.sleep(0)  # Timeout observes pending, then queues on the same lock.
        wizard.lock.release()
        try:
            await asyncio.wait_for(sending.wait(), 2)
            await asyncio.wait_for(timeout, 2)
            self.assertEqual(view.state, 'sending')
            self.assertFalse(wizard.closed)
            wizard.preview_message.edit.assert_not_awaited()
        finally:
            finish.set()
            await asyncio.wait_for(confirmation, 2)
        self.assertEqual(view.state, 'finished')
        self.ns['_send_owner_broadcast'].assert_awaited_once_with(view.draft)
        await view.on_timeout()
        wizard.preview_message.edit.assert_not_awaited()

    async def test_duplicate_confirm_queued_past_expiry_never_claims_nothing_sent(self):
        wizard, view, _ = await self.preview()
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(3, 0, 0))
        editing, release = asyncio.Event(), asyncio.Event()
        first, duplicate = SyntheticInteraction(), SyntheticInteraction()
        acknowledge = first.response.edit_message.side_effect
        async def blocked_ack(*args, **kwargs):
            editing.set()
            await release.wait()
            return await acknowledge(*args, **kwargs)
        first.response.edit_message.side_effect = blocked_ack
        confirmation = asyncio.create_task(view.confirm_send.callback(first))
        await asyncio.wait_for(editing.wait(), 2)
        replay = asyncio.create_task(view.confirm_send.callback(duplicate))
        await asyncio.sleep(0)
        wizard.expires_at = view.expires_at = time.monotonic() - 1
        release.set()
        await asyncio.wait_for(asyncio.gather(confirmation, replay), 2)
        self.ns['_send_owner_broadcast'].assert_awaited_once_with(view.draft)
        self.assertEqual(view.state, 'finished')
        self.assertFalse(wizard.closed)
        duplicate.response.edit_message.assert_not_awaited()
        duplicate.edit_original_response.assert_not_awaited()
        self.assertIn('already closed or sending', duplicate.response.send_message.await_args.args[0])
        self.assertNotIn('Nothing was sent', duplicate.response.send_message.await_args.args[0])

    async def test_stale_timeout_does_not_expire_new_step(self):
        wizard, old, _ = await self.start()
        current, _ = await self.choose_audience(old, 'all')
        await old.on_timeout()
        self.assertFalse(wizard.closed)
        self.assertIs(wizard.current_view, current)
        wizard.preview_message.edit.assert_not_awaited()
        await current.on_timeout()
        self.assertTrue(wizard.closed)
        self.assertIsNone(wizard.preview_message.edit.await_args.kwargs['view'])

    async def test_timeout_transport_failure_still_invalidates_modal(self):
        wizard, view = await self.setup_compose()
        modal, _ = await self.open_modal(view)
        wizard.preview_message.edit.side_effect = TimeoutError('Synthetic timeout edit')
        await view.on_timeout()
        self.assertTrue(wizard.closed)
        self.ns['logger'].warning.assert_called()
        await modal.on_submit(SyntheticInteraction())
        self.ns['client'].get_guild.assert_not_called()

    async def test_resolver_outage_can_reopen_and_recover_without_retrying_old_modal(self):
        original = self.ns['_resolve_owner_broadcast_draft']
        for error in (ValueError('Complete list unavailable'), RuntimeError('Synthetic outage')):
            wizard, view = await self.setup_compose()
            modal, _ = await self.open_modal(view)
            self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=error)
            interaction = SyntheticInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(interaction.events, ['defer', 'followup'])
            self.assertEqual(wizard.stage, 'compose')
            interaction.edit_original_response.assert_not_awaited()
            await modal.on_submit(SyntheticInteraction())
            self.ns['_resolve_owner_broadcast_draft'].assert_awaited_once()
            self.ns['_resolve_owner_broadcast_draft'] = original
            newest, _ = await self.open_modal(view, 'Recovered text')
            await newest.on_submit(SyntheticInteraction())
            self.assertEqual(wizard.current_view.draft.message, owner_message('Recovered text'))
        self.assert_no_delivery()

    async def test_partial_membership_or_access_outage_never_publishes_partial_preview(self):
        for audience in ('all', 'all_server_members', 'all_except'):
            wizard, setup = await self.setup_compose(audience)
            self.guild(self.members.values(), listing_error=TimeoutError('Synthetic listing outage'))
            modal, _ = await self.open_modal(setup)
            interaction = SyntheticInteraction()
            await modal.on_submit(interaction)
            self.assertEqual(wizard.stage, 'compose')
            self.assertIn('complete current member list', interaction.followup.send.await_args.args[0])
            interaction.edit_original_response.assert_not_awaited()
        self.guild(self.members.values())
        self.ns['member_access_error'].return_value = MEMBER_ACCESS_UNAVAILABLE
        wizard, setup = await self.setup_compose()
        modal, _ = await self.open_modal(setup)
        interaction = SyntheticInteraction()
        await modal.on_submit(interaction)
        interaction.edit_original_response.assert_not_awaited()
        self.assert_no_delivery()

    async def test_invalid_selected_member_blocks_entire_preview(self):
        for invalid in (None, member(2, bot=True), member(2, guild_id=11)):
            guild = self.guild([self.members[1]])
            guild.fetch_member.side_effect = lambda uid: self.members[1] if uid == 1 else invalid
            wizard, setup = await self.setup_compose('selected', (1, 2))
            modal, _ = await self.open_modal(setup)
            interaction = SyntheticInteraction()
            await modal.on_submit(interaction)
            self.assertIn('not verified current human members', interaction.followup.send.await_args.args[0])
            interaction.edit_original_response.assert_not_awaited()
            self.assertEqual(wizard.stage, 'compose')
        self.assert_no_delivery()

    async def test_defer_failure_consumes_modal_but_allows_fresh_modal(self):
        wizard, view = await self.setup_compose()
        old, _ = await self.open_modal(view)
        interaction = SyntheticInteraction()
        interaction.response.defer.side_effect = TimeoutError('Synthetic acknowledgement outage')
        with self.assertRaises(TimeoutError):
            await old.on_submit(interaction)
        self.ns['client'].get_guild.assert_not_called()
        self.assertEqual(wizard.stage, 'compose')
        await old.on_submit(SyntheticInteraction())
        self.ns['client'].get_guild.assert_not_called()
        newest, _ = await self.open_modal(view, 'New accepted text')
        await newest.on_submit(SyntheticInteraction())
        self.assertEqual(wizard.current_view.draft.message, owner_message('New accepted text'))

    async def test_uncertain_preview_edit_closes_all_controls_without_sending(self):
        wizard, setup = await self.setup_compose()
        modal, _ = await self.open_modal(setup)
        interaction = SyntheticInteraction()
        interaction.edit_original_response.side_effect = TimeoutError('Synthetic uncertain preview edit')
        with self.assertRaises(TimeoutError):
            await modal.on_submit(interaction)
        preview = interaction.edit_original_response.await_args.kwargs['view']
        self.assertTrue(wizard.closed)
        self.assertTrue(preview.is_finished())
        await preview.confirm_send.callback(SyntheticInteraction())
        await setup.compose.callback(SyntheticInteraction())
        await modal.on_submit(SyntheticInteraction())
        self.assert_no_delivery()

    async def test_navigation_transport_failure_closes_old_and_new_controls(self):
        wizard, old, _ = await self.start()
        interaction = SyntheticInteraction()
        interaction.response.edit_message.side_effect = TimeoutError('Synthetic navigation outage')
        with self.assertRaises(TimeoutError):
            await self.choose_audience(old, 'all', interaction)
        self.assertTrue(wizard.closed)
        self.assertTrue(old.is_finished())
        self.assertTrue(wizard.current_view.is_finished())
        await wizard.current_view.compose.callback(SyntheticInteraction())
        self.ns['client'].get_guild.assert_not_called()

    async def test_failed_confirmation_ack_does_not_send_or_allow_replay(self):
        wizard, view, _ = await self.preview()
        interaction = SyntheticInteraction()
        interaction.response.edit_message.side_effect = TimeoutError('Synthetic confirmation outage')
        with self.assertRaises(TimeoutError):
            await view.confirm_send.callback(interaction)
        await view.confirm_send.callback(SyntheticInteraction())
        await view.edit_message.callback(SyntheticInteraction())
        self.assert_no_delivery()

    async def test_failed_dm_and_result_transports_never_retry_delivery(self):
        self.members[1].send.side_effect = TimeoutError('Synthetic uncertain DM')
        self.members[2].send.side_effect = PermissionError('Synthetic closed DMs')
        _, view, _ = await self.preview('all_server_members')
        interaction = SyntheticInteraction()
        interaction.followup.send.side_effect = TimeoutError('Synthetic expired result token')
        with self.assertRaises(TimeoutError):
            await view.confirm_send.callback(interaction)
        self.assertEqual(view.state, 'finished')
        await view.confirm_send.callback(SyntheticInteraction())
        for m in self.members.values():
            m.send.assert_awaited_once()
        summary = interaction.followup.send.await_args.args[0]
        self.assertIn('**1** member', summary)
        self.assertIn('2 delivery attempt(s) could not be confirmed', summary)

    async def test_cancel_back_or_expiry_during_lookup_cannot_publish_stale_preview(self):
        original = self.ns['_resolve_owner_broadcast_draft']
        for action in ('cancel', 'back', 'expire'):
            wizard, setup = await self.setup_compose()
            modal, _ = await self.open_modal(setup)
            entered, release = asyncio.Event(), asyncio.Event()
            async def blocked(*args):
                entered.set()
                await release.wait()
                return await original(*args)
            self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=blocked)
            interaction = SyntheticInteraction()
            pending = asyncio.create_task(modal.on_submit(interaction))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if action == 'expire':
                    wizard.expires_at = 0
                else:
                    await asyncio.wait_for(getattr(setup, action).callback(SyntheticInteraction()), 2)
            finally:
                release.set()
                await asyncio.wait_for(pending, 2)
            edits = interaction.edit_original_response.await_args_list
            self.assertTrue(all(call.kwargs.get('view') is None for call in edits))
            self.assertNotEqual(wizard.stage, 'preview')
            self.assert_no_delivery()
        self.ns['_resolve_owner_broadcast_draft'] = original

    async def test_old_lookup_completion_cannot_replace_newer_preview(self):
        wizard, old_setup = await self.setup_compose()
        old_modal, _ = await self.open_modal(old_setup, 'Old text')
        original = self.ns['_resolve_owner_broadcast_draft']
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(text, *args):
            if text == 'Old text':
                entered.set()
                await release.wait()
            return await original(text, *args)
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=resolve)
        old_interaction = SyntheticInteraction()
        pending = asyncio.create_task(old_modal.on_submit(old_interaction))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(old_setup.back.callback(SyntheticInteraction()), 2)
            new_setup, _ = await self.choose_audience(wizard.current_view, 'all_server_members')
            new_modal, _ = await self.open_modal(new_setup, 'Newest text')
            await new_modal.on_submit(SyntheticInteraction())
            latest = wizard.current_view
        finally:
            release.set()
            await asyncio.wait_for(pending, 2)
        self.assertIs(wizard.current_view, latest)
        self.assertEqual(latest.draft.message, owner_message('Newest text'))
        old_interaction.edit_original_response.assert_not_awaited()
        self.assert_no_delivery()

    async def test_owner_guild_and_configuration_guard_every_guided_stage(self):
        for uid, gid in ((1, 10), (999, 11), (999, None)):
            interaction = SyntheticInteraction(uid, gid)
            await self.command.callback(interaction)
            self.assertNotIn('view', interaction.response.send_message.await_args.kwargs)
            wizard, setup = await self.setup_compose('selected')
            revision = wizard.revision
            for name in ('compose', 'back', 'cancel'):
                interaction = SyntheticInteraction(uid, gid)
                await getattr(setup, name).callback(interaction)
                self.assertEqual(interaction.events, ['send_message'])
                self.assertEqual(wizard.revision, revision)
            modal, _ = await self.open_modal(setup)
            interaction = SyntheticInteraction(uid, gid)
            await modal.on_submit(interaction)
            self.assertEqual(interaction.events, ['send_message'])
            _, preview, _ = await self.preview('selected')
            for name in ('confirm_send', 'cancel_send', 'next_page', 'previous_page',
                         'edit_message', 'edit_audience', 'edit_members'):
                interaction = SyntheticInteraction(uid, gid)
                await getattr(preview, name).callback(interaction)
                self.assertEqual(interaction.events, ['send_message'])
                self.assertEqual(preview.state, 'pending')
        for setting in ('GTOP_OWNER_USER_ID', 'GTOP_GUILD_ID'):
            wizard, setup = await self.setup_compose()
            old = self.ns[setting]
            self.ns[setting] += 1
            interaction = SyntheticInteraction()
            await setup.compose.callback(interaction)
            self.assertEqual(interaction.events, ['send_message'])
            self.ns[setting] = old
        self.assert_no_delivery()

    async def test_invalid_audience_and_picker_values_never_progress(self):
        for values in ([], ['invalid'], ['all', 'selected']):
            wizard, view, _ = await self.start()
            view.choose_audience._values = values
            interaction = SyntheticInteraction()
            await view.choose_audience.callback(interaction)
            self.assertEqual(wizard.stage, 'audience')
            self.assertEqual(interaction.events, ['send_message'])
        for ids in ((), tuple(range(1, 27)), (0,), (-1,), (2**64,), ('2',), (True,)):
            wizard, picker, _ = await self.start(audience='selected')
            _, interaction = await self.choose_members(picker, ids)
            self.assertEqual(wizard.stage, 'members')
            self.assertEqual(interaction.events, ['send_message'])
        self.ns['client'].get_guild.assert_not_called()

    async def test_text_shortcut_still_uses_original_preview_and_native_picker(self):
        for audience, selection, view_name in (
            ('all', '', 'OwnerBroadcastView'),
            ('all_server_members', '', 'OwnerBroadcastView'),
            ('selected', '2', 'OwnerBroadcastView'),
            ('all_except', '2', 'OwnerBroadcastView'),
            ('selected', '', 'OwnerBroadcastMemberPicker'),
            ('all_except', '', 'OwnerBroadcastMemberPicker'),
        ):
            interaction = SyntheticInteraction()
            await self.command.callback(interaction, 'Shortcut text', audience, selection)
            self.assertEqual(interaction.events, ['defer', 'followup'])
            sent = interaction.followup.send.await_args
            self.assertEqual(type(sent.kwargs['view']).__name__, view_name)
            self.assertEqual(sent.args[0], owner_message('Shortcut text'))
            self.assertTrue(sent.kwargs['ephemeral'])
            self.assert_no_mentions(sent.kwargs['allowed_mentions'])
        self.assert_no_delivery()


if __name__ == '__main__':
    unittest.main()
