"""Owner broadcasts with synthetic members and a fake Discord transport only."""
import ast
import asyncio
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import re
import time
from types import SimpleNamespace as NS
from typing import Literal
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.member_access import MEMBER_ACCESS_UNAVAILABLE
from gbop_voice_web.owner_broadcast import (
    AUDIENCE_LABELS, PREVIEW_SECONDS, build_draft, owner_message, parse_selection,
)

BOT = Path(__file__).resolve().parents[1] / 'bot.py'


def member(user_id, *, name=None, role=True, bot=False, guild_id=10):
    return NS(id=user_id, display_name=name or f'Member {user_id}', bot=bot,
              guild=NS(id=guild_id), role=role, send=AsyncMock(), add_roles=AsyncMock())


def draft(ids=(1, 2), audience='all', selected=()):
    return build_draft(999, 10, 'Synthetic test message', audience, selected,
                       [member(user_id) for user_id in ids])


class OwnerBroadcastPlanTests(unittest.TestCase):
    def test_exact_mentions_ids_and_deduplication(self):
        self.assertEqual(parse_selection('selected', '<@1>, <@!2> 3 <@1>'), (1, 2, 3))

    def test_ambiguous_names_roles_and_malformed_ids_fail(self):
        for value in ('Alice', '@Alice', '<@&1>', '1garbage', '-1', '0', '1;2', str(2**64)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_selection('selected', value)

    def test_audience_and_selection_must_be_explicit_and_consistent(self):
        for mode, raw in (('all', '1'), ('all_server_members', '1'), ('selected', ''),
                          ('all_except', ''), ('unknown', '1')):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_selection(mode, raw)
        self.assertEqual(parse_selection('all', ''), ())
        self.assertEqual(parse_selection('all_server_members', ''), ())

    def test_all_four_audiences_resolve_exact_ids(self):
        self.assertEqual([r.user_id for r in draft().recipients], [1, 2])
        self.assertEqual([r.user_id for r in draft(audience='selected', selected=(2,)).recipients], [2])
        excluded = draft(audience='all_except', selected=(2,))
        self.assertEqual([r.user_id for r in excluded.recipients], [1])
        self.assertEqual(excluded.excluded_count, 1)
        self.assertEqual([r.user_id for r in draft(audience='all_server_members').recipients], [1, 2])

    def test_unknown_selection_cannot_silently_broaden_audience(self):
        for mode, error in (('selected', 'not verified current human members'),
                            ('all_except', 'not currently eligible')):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, error):
                draft(audience=mode, selected=(99,))

    def test_zero_recipient_audience_has_no_confirmation(self):
        for make in (lambda: draft(()), lambda: draft(audience='all_except', selected=(1, 2))):
            with self.assertRaisesRegex(ValueError, 'No eligible recipients'):
                make()

    def test_large_invalid_selection_error_stays_within_discord_limit(self):
        with self.assertRaises(ValueError) as error:
            draft(audience='all_except', selected=tuple(range(100, 1000)))
        self.assertLess(len(str(error.exception)), 2000)
        self.assertIn('more', str(error.exception))

    def test_content_is_not_silently_truncated(self):
        self.assertTrue(owner_message('  hello\nworld  ').endswith('hello\nworld'))
        self.assertTrue(owner_message('x' * 1800).endswith('x' * 1800))
        for value in ('', '  ', 'x' * 1801):
            with self.assertRaises(ValueError):
                owner_message(value)

    def test_message_is_attributed_to_gbop(self):
        self.assertEqual(owner_message('  hello\nworld  '), '📣 **GBOP Message**\n\nhello\nworld')

    def test_draft_is_immutable_and_pagination_does_not_drop_recipients(self):
        preview = draft(tuple(range(1, 106)))
        ids = [r.user_id for page in range(preview.page_count) for r in preview.page(page)]
        self.assertEqual(ids, list(range(1, 106)))
        self.assertEqual(preview.page_count, 11)
        with self.assertRaises(FrozenInstanceError):
            preview.message = 'changed'
        with self.assertRaises(FrozenInstanceError):
            preview.recipients[0].user_id = 999
        for page in (-1, 11):
            with self.assertRaises(ValueError):
                preview.page(page)


class FakeButton:
    def __init__(self, callback, **kwargs):
        self.callback = callback
        self.disabled = False
        self.label = kwargs.get('label')

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        key = '_button_' + self.name
        if key not in instance.__dict__:
            async def call(interaction, button=None):
                return await self.callback(instance, interaction, button)
            instance.__dict__[key] = NS(disabled=False, callback=call, label=self.label)
        return instance.__dict__[key]


class FakeView:
    def __init__(self, timeout):
        self.timeout = timeout
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeEmbed:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def set_footer(self, *, text):
        self.footer = NS(text=text)


class OwnerBroadcastTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.none_mentions = object()
        discord = NS(Interaction=object, Message=object, Embed=FakeEmbed, ButtonStyle=NS(secondary=1, danger=2),
            AllowedMentions=NS(none=lambda: self.none_mentions),
            utils=NS(escape_markdown=lambda value: re.sub(r'([*_`])', r'\\\1', value)),
            ui=NS(View=FakeView, Button=object, UserSelect=object,
                  button=lambda **kwargs: lambda fn: FakeButton(fn, **kwargs),
                  select=lambda **kwargs: lambda fn: FakeButton(fn, **kwargs)))
        self.ns = dict(asyncio=asyncio, time=time, discord=discord, Literal=Literal,
            GTOP_OWNER_USER_ID=999, GTOP_GUILD_ID=10,
            is_owner=lambda m: m.id == 999, has_member_role=lambda m: m.role,
            client=NS(get_guild=Mock()), logger=Mock(),
            member_access_error=Mock(return_value=None), db=Mock(side_effect=AssertionError('No broadcast DB writes')),
            ensure_member_record=Mock(side_effect=AssertionError('No broadcast activation')),
            MEMBER_ACCESS_UNAVAILABLE=MEMBER_ACCESS_UNAVAILABLE,
            AUDIENCE_LABELS=AUDIENCE_LABELS, PREVIEW_SECONDS=PREVIEW_SECONDS,
            build_draft=build_draft, owner_message=owner_message, parse_selection=parse_selection,
            _authorized_scheduled_member=AsyncMock())
        names = {'_current_owner_message_member', '_resolve_owner_broadcast_draft',
                 '_send_owner_broadcast', 'OwnerBroadcastView', 'OwnerBroadcastMemberPicker', 'gbopmessage'}
        nodes = [n for n in ast.parse(BOT.read_text()).body if getattr(n, 'name', '') in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), 'exec'), self.ns)

    def interaction(self, user_id=999, guild_id=10):
        return NS(user=member(user_id), guild_id=guild_id,
                  response=NS(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
                  followup=NS(send=AsyncMock()), edit_original_response=AsyncMock())

    def guild(self, members, *, error=False):
        async def fetch_members(**kwargs):
            self.assertIsNone(kwargs['limit'])
            for m in members:
                yield m
            if error:
                raise RuntimeError('Synthetic listing failure')
        members = list(members)
        guild = NS(id=10, fetch_members=fetch_members,
                   fetch_member=AsyncMock(side_effect={m.id: m for m in members}.get))
        self.ns['client'].get_guild.return_value = guild
        return guild

    def view(self, preview=None):
        return self.ns['OwnerBroadcastView'](preview or draft())

    async def test_command_only_previews_and_never_calls_sender(self):
        preview = draft()
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(return_value=preview)
        self.ns['_send_owner_broadcast'] = AsyncMock()
        interaction = self.interaction()
        await self.ns['gbopmessage'](interaction, 'Synthetic test message')
        self.ns['_send_owner_broadcast'].assert_not_awaited()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        args = interaction.followup.send.await_args
        self.assertEqual(args.args[0], preview.message)
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertIs(args.kwargs['allowed_mentions'], self.none_mentions)
        self.assertIn('`1`', args.kwargs['embed'].description)
        self.assertIn('`2`', args.kwargs['embed'].description)

    async def test_preview_and_confirmed_delivery_have_identical_gbop_attribution(self):
        text = 'Synthetic announcement\nFor @everyone'
        expected_message = '📣 **GBOP Message**\n\n' + text
        for audience, selection, expected_ids in (
            ('all', '', {1, 2, 3}),
            ('all_server_members', '', {1, 2, 3}),
            ('selected', '2', {2}),
            ('all_except', '2', {1, 3}),
        ):
            with self.subTest(audience=audience):
                recipients = {uid: member(uid) for uid in (1, 2, 3)}
                self.guild(recipients.values())
                self.ns['_authorized_scheduled_member'].side_effect = recipients.get
                interaction = self.interaction()
                await self.ns['gbopmessage'](interaction, text, audience, selection)
                preview_call = interaction.followup.send.await_args
                self.assertEqual(preview_call.args[0], expected_message)
                self.assertIs(preview_call.kwargs['allowed_mentions'], self.none_mentions)
                for recipient in recipients.values():
                    recipient.send.assert_not_awaited()
                view = preview_call.kwargs['view']
                await view.confirm_send.callback(self.interaction())
                for uid, recipient in recipients.items():
                    if uid in expected_ids:
                        recipient.send.assert_awaited_once_with(
                            expected_message, allowed_mentions=self.none_mentions,
                        )
                    else:
                        recipient.send.assert_not_awaited()

    async def test_command_rejects_nonowner_admin_foreign_guild_and_dm(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for audience in AUDIENCE_LABELS:
            for user_id, guild_id in ((1, 10), (999, 11), (999, None)):
                interaction = self.interaction(user_id, guild_id)
                await self.ns['gbopmessage'](interaction, 'test', audience, '2' if audience in ('selected', 'all_except') else '')
                interaction.response.send_message.assert_awaited_once()
                interaction.response.defer.assert_not_awaited()
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_command_rejects_invalid_inputs_before_lookup(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for text, audience, members in (('', 'all', ''), ('x' * 1801, 'all', ''),
                                       ('test', 'selected', '<@&2>'), ('test', 'all', '1')):
            await self.ns['gbopmessage'](self.interaction(), text, audience, members)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_command_selection_is_passed_without_name_guessing(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(return_value=draft(audience='selected', selected=(2,)))
        await self.ns['gbopmessage'](self.interaction(), 'test', 'selected', '<@2>')
        self.ns['_resolve_owner_broadcast_draft'].assert_awaited_once_with('test', 'selected', (2,))

    async def test_no_draft_when_lookup_fails(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=ValueError('Cannot verify'))
        interaction = self.interaction()
        await self.ns['gbopmessage'](interaction, 'test')
        self.assertNotIn('view', interaction.followup.send.await_args.kwargs)

    async def test_fresh_listing_filters_bots_roles_and_consent(self):
        self.guild([member(1), member(2, bot=True), member(3, role=False), member(4), member(999, role=False)])
        self.ns['member_access_error'].side_effect = lambda db, guild, uid, owner: 'revoked' if uid == 4 else None
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'all', ())
        self.assertEqual([r.user_id for r in preview.recipients], [1, 999])

    async def test_partial_listing_failure_cannot_send_to_a_partial_audience(self):
        self.guild([member(1)], error=True)
        with self.assertRaisesRegex(ValueError, 'complete current member list'):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'all', ())

    async def test_access_lookup_outage_does_not_silently_reduce_all_audience(self):
        for audience, selected in (('all', ()), ('all_except', (2,))):
            self.guild([member(1), member(2), member(3)])
            self.ns['member_access_error'].side_effect = [None, None, MEMBER_ACCESS_UNAVAILABLE]
            with self.subTest(audience=audience), self.assertRaisesRegex(ValueError, 'complete current member list'):
                await self.ns['_resolve_owner_broadcast_draft']('test', audience, selected)

    async def test_missing_guild_fails_closed(self):
        self.ns['client'].get_guild.return_value = None
        with self.assertRaises(ValueError):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'all', ())

    async def test_selected_does_not_require_or_expand_to_guild_listing(self):
        guild = self.guild([member(2, role=False)])
        guild.fetch_members = Mock(side_effect=AssertionError('No selected member listing'))
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (2,))
        self.assertEqual([r.user_id for r in preview.recipients], [2])
        guild.fetch_member.assert_awaited_once_with(2)
        self.ns['_authorized_scheduled_member'].assert_not_awaited()

    async def test_selected_and_excluded_ineligible_members_block_preview(self):
        self.guild([])
        with self.assertRaisesRegex(ValueError, 'not verified current human members'):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (2,))
        self.guild([member(1)])
        with self.assertRaisesRegex(ValueError, 'not currently eligible'):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'all_except', (2,))

    async def test_excluded_ids_are_not_in_resolved_preview(self):
        self.guild([member(1), member(2), member(3)])
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'all_except', (2,))
        self.assertEqual([r.user_id for r in preview.recipients], [1, 3])

    async def test_send_rechecks_each_id_and_skips_revocation_or_departure(self):
        one, three = member(1), member(3)
        self.ns['_authorized_scheduled_member'].side_effect = [one, None, three]
        result = await self.ns['_send_owner_broadcast'](draft((1, 2, 3)))
        self.assertEqual(result, (2, 0, 1))
        self.assertEqual([c.args[0] for c in self.ns['_authorized_scheduled_member'].await_args_list], [1, 2, 3])
        for m in (one, three):
            m.send.assert_awaited_once_with(draft().message, allowed_mentions=self.none_mentions)

    async def test_send_does_not_add_new_members_or_retry_uncertain_failure(self):
        one = member(1)
        one.send.side_effect = TimeoutError('Synthetic uncertain delivery')
        self.ns['_authorized_scheduled_member'].return_value = one
        self.guild([one, member(2)])
        self.assertEqual(await self.ns['_send_owner_broadcast'](draft((1,))), (0, 1, 0))
        one.send.assert_awaited_once()
        self.ns['_authorized_scheduled_member'].assert_awaited_once_with(1)

    async def test_config_change_stops_send(self):
        preview = draft()
        self.ns['GTOP_GUILD_ID'] = 11
        self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (0, 0, 2))
        self.ns['_authorized_scheduled_member'].assert_not_awaited()

    async def test_confirm_sends_only_after_every_page_rendered(self):
        view = self.view(draft(tuple(range(1, 22))))
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(21, 0, 0))
        self.assertTrue(view.confirm_send.disabled)
        await view.confirm_send.callback(self.interaction())
        self.ns['_send_owner_broadcast'].assert_not_awaited()
        for page in (1, 2):
            interaction = self.interaction()
            await view.next_page.callback(interaction)
            self.assertEqual(view.page_index, page)
            self.assertEqual(interaction.response.edit_message.await_args.kwargs['content'], view.draft.message)
        self.assertFalse(view.confirm_send.disabled)
        await view.confirm_send.callback(self.interaction())
        self.ns['_send_owner_broadcast'].assert_awaited_once_with(view.draft)
        self.assertEqual(view.state, 'finished')

    async def test_failed_page_render_does_not_count_as_reviewed(self):
        view = self.view(draft(tuple(range(1, 12))))
        interaction = self.interaction()
        interaction.response.edit_message.side_effect = RuntimeError('Synthetic edit failure')
        with self.assertRaises(RuntimeError):
            await view.next_page.callback(interaction)
        self.assertEqual(view.seen_pages, {0})
        self.assertEqual(view.page_index, 0)
        self.assertTrue(view.confirm_send.disabled)

    async def test_previous_next_keep_stable_exact_recipient_pages(self):
        view = self.view(draft(tuple(range(1, 12))))
        self.assertTrue(view.previous_page.disabled)
        await view.next_page.callback(self.interaction())
        self.assertTrue(view.next_page.disabled)
        self.assertIn('`11`', view.preview_embed().description)
        self.assertNotIn('`1`', view.preview_embed().description)
        await view.previous_page.callback(self.interaction())
        self.assertIn('`1`', view.preview_embed().description)
        self.assertNotIn('`11`', view.preview_embed().description)

    async def test_repeated_and_concurrent_confirm_clicks_send_once(self):
        view = self.view()
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(2, 0, 0))
        await asyncio.gather(*(view.confirm_send.callback(self.interaction()) for _ in range(4)))
        self.ns['_send_owner_broadcast'].assert_awaited_once()

    async def test_cancel_and_confirm_race_has_at_most_one_terminal_action(self):
        view = self.view()
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(2, 0, 0))
        await asyncio.gather(view.cancel_send.callback(self.interaction()), view.confirm_send.callback(self.interaction()))
        self.ns['_send_owner_broadcast'].assert_not_awaited()
        self.assertEqual(view.state, 'cancelled')

    async def test_confirmed_or_cancelled_draft_is_not_reusable(self):
        for callback in ('confirm_send', 'cancel_send'):
            view = self.view()
            self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(2, 0, 0))
            await getattr(view, callback).callback(self.interaction())
            await view.confirm_send.callback(self.interaction())
            self.assertEqual(self.ns['_send_owner_broadcast'].await_count, 1 if callback == 'confirm_send' else 0)

    async def test_each_button_enforces_owner_and_guild_at_callback(self):
        self.ns['_send_owner_broadcast'] = AsyncMock()
        for name in ('confirm_send', 'cancel_send', 'next_page', 'previous_page'):
            for uid, gid in ((1, 10), (999, 11), (999, None)):
                view = self.view()
                interaction = self.interaction(uid, gid)
                await getattr(view, name).callback(interaction)
                self.assertEqual(view.state, 'pending')
                interaction.response.send_message.assert_awaited_once()
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_expired_preview_and_timeout_cannot_send(self):
        self.ns['_send_owner_broadcast'] = AsyncMock()
        view = self.view()
        view.expires_at = 0
        await view.confirm_send.callback(self.interaction())
        self.assertEqual(view.state, 'expired')
        view = self.view()
        await view.on_timeout()
        await view.confirm_send.callback(self.interaction())
        self.assertEqual(view.state, 'expired')
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_timeout_removes_stale_controls_when_preview_is_available(self):
        view = self.view()
        view.preview_message = NS(edit=AsyncMock())
        await view.on_timeout()
        kwargs = view.preview_message.edit.await_args.kwargs
        self.assertIsNone(kwargs['view'])
        self.assertIn('expired', kwargs['content'])
        self.assertEqual(view.state, 'expired')

    async def test_expiry_edit_failure_is_safe_and_cannot_enable_sending(self):
        view = self.view()
        view.preview_message = NS(edit=AsyncMock(side_effect=TimeoutError()))
        await view.on_timeout()
        self.assertEqual(view.state, 'expired')
        self.ns['logger'].warning.assert_called_once()

    async def test_failed_confirmation_response_cannot_trigger_send_or_retry(self):
        view = self.view()
        self.ns['_send_owner_broadcast'] = AsyncMock()
        interaction = self.interaction()
        interaction.response.edit_message.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await view.confirm_send.callback(interaction)
        await view.confirm_send.callback(self.interaction())
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_delivery_result_counts_not_attempts_are_reported(self):
        self.ns['_send_owner_broadcast'] = AsyncMock(return_value=(1, 1, 2))
        view = self.view(draft((1, 2, 3, 4)))
        interaction = self.interaction()
        await view.confirm_send.callback(interaction)
        result = interaction.followup.send.await_args.args[0]
        self.assertIn('**1** member', result)
        self.assertIn('1 delivery attempt(s) could not be confirmed', result)
        self.assertIn('2 recipient(s) were skipped', result)

    async def test_server_audiences_receive_without_gbop_access_or_profile_writes(self):
        for audience in ('selected', 'all_server_members'):
            for access_state in ('missing profile', 'inactive', 'unacknowledged', 'revoked'):
                with self.subTest(audience=audience, access_state=access_state):
                    recipient = member(2, role=False)
                    guild = self.guild([recipient])
                    self.ns['member_access_error'].return_value = access_state
                    interaction = self.interaction()
                    await self.ns['gbopmessage'](interaction, 'Exact synthetic message', audience,
                                                 '2' if audience == 'selected' else '')
                    preview = interaction.followup.send.await_args
                    self.assertIn('`2`', preview.kwargs['embed'].description)
                    self.assertIn('does not grant GBOP access', preview.kwargs['embed'].description)
                    recipient.send.assert_not_awaited()
                    await preview.kwargs['view'].confirm_send.callback(self.interaction())
                    recipient.send.assert_awaited_once_with(preview.args[0], allowed_mentions=self.none_mentions)
                    recipient.add_roles.assert_not_awaited()
                    self.assertFalse(recipient.role)
                    guild.fetch_member.assert_awaited_with(2)
        self.ns['member_access_error'].assert_not_called()
        self.ns['db'].assert_not_called()
        self.ns['ensure_member_record'].assert_not_called()
        self.ns['_authorized_scheduled_member'].assert_not_awaited()

    async def test_server_wide_uses_complete_fresh_humans_not_partial_cache_or_gbop_gate(self):
        eligible, no_role, inactive, revoked, bot = [member(1), member(2, role=False),
                                                   member(3), member(4), member(5, bot=True)]
        guild = self.guild([eligible, no_role, inactive, revoked, bot])
        guild.members = [eligible]  # A partial gateway cache must never define this audience.
        self.ns['member_access_error'].side_effect = AssertionError('No GBOP eligibility check')
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'all_server_members', ())
        self.assertEqual([r.user_id for r in preview.recipients], [1, 2, 3, 4])
        self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (4, 0, 0))
        self.assertEqual([call.args[0] for call in guild.fetch_member.await_args_list], [1, 2, 3, 4])
        bot.send.assert_not_awaited()
        self.ns['member_access_error'].assert_not_called()
        self.ns['db'].assert_not_called()

    async def test_default_and_exclusion_audiences_keep_full_gbop_eligibility(self):
        for audience, selected, expected in (('all', (), [1, 6]), ('all_except', (6,), [1])):
            self.guild([member(1), member(2, role=False), member(3), member(4), member(5, bot=True), member(6)])
            self.ns['member_access_error'].side_effect = lambda db, guild, uid, owner: {
                3: 'inactive', 4: 'revoked',
            }.get(uid)
            self.ns['_authorized_scheduled_member'].reset_mock()
            self.ns['_authorized_scheduled_member'].side_effect = lambda uid: member(uid)
            preview = await self.ns['_resolve_owner_broadcast_draft']('test', audience, selected)
            self.assertEqual([r.user_id for r in preview.recipients], expected)
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (len(expected), 0, 0))
            self.assertEqual([c.args[0] for c in self.ns['_authorized_scheduled_member'].await_args_list], expected)

    async def test_server_member_helper_rejects_malformed_ids_without_lookup(self):
        guild = self.guild([member(1)])
        for user_id in (None, True, False, '1', '', '<@1>', 'external', 0, -1, 2**64, 1.0):
            with self.subTest(user_id=user_id):
                self.assertIsNone(await self.ns['_current_owner_message_member'](user_id))
        guild.fetch_member.assert_not_awaited()

    async def test_selected_bots_nonmembers_external_and_unverified_members_block_whole_preview(self):
        for invalid in (None, member(2, bot=True), member(2, guild_id=11), member(99),
                        NS(id=2, bot=False), member(2, bot=None)):
            with self.subTest(invalid=invalid):
                one = member(1)
                guild = self.guild([one])
                guild.fetch_member.side_effect = [one, invalid]
                with self.assertRaisesRegex(ValueError, 'not verified current human members'):
                    await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (1, 2))
                one.send.assert_not_awaited()

    async def test_server_listing_partial_failure_or_invalid_guild_member_blocks_preview(self):
        self.guild([member(1)], error=True)
        with self.assertRaisesRegex(ValueError, 'complete current member list'):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'all_server_members', ())
        for invalid in (member(2, guild_id=11), member(0), member('2'), member(True),
                        member(2, bot=None), NS(id=2, bot=False)):
            self.guild([member(1), invalid])
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'complete current member list'):
                await self.ns['_resolve_owner_broadcast_draft']('test', 'all_server_members', ())

    async def test_empty_or_bot_only_server_cannot_produce_confirmation(self):
        for members in ([], [member(1, bot=True)]):
            self.guild(members)
            with self.assertRaisesRegex(ValueError, 'No eligible recipients'):
                await self.ns['_resolve_owner_broadcast_draft']('test', 'all_server_members', ())

    async def test_server_modes_fail_closed_when_guild_missing_wrong_or_fetch_fails(self):
        for guild in (None, NS(id=11)):
            self.ns['client'].get_guild.return_value = guild
            for audience in ('selected', 'all_server_members'):
                with self.subTest(audience=audience), self.assertRaisesRegex(ValueError, 'server is unavailable'):
                    await self.ns['_resolve_owner_broadcast_draft']('test', audience, (2,) if audience == 'selected' else ())
            self.assertIsNone(await self.ns['_current_owner_message_member'](2))
        guild = self.guild([member(2)])
        guild.fetch_member.side_effect = TimeoutError('Synthetic Discord outage')
        with self.assertRaisesRegex(ValueError, 'not verified current human members'):
            await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (2,))
        for audience in ('selected', 'all_server_members'):
            preview = draft((2,), audience, (2,) if audience == 'selected' else ())
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (0, 0, 1))

    async def test_server_modes_recheck_departure_bots_guild_and_freeze_the_audience(self):
        for audience in ('selected', 'all_server_members'):
            members = {uid: member(uid, role=False) for uid in (1, 2, 3, 4)}
            guild = self.guild(members.values())
            preview = await self.ns['_resolve_owner_broadcast_draft'](
                'test', audience, (1, 2, 3, 4) if audience == 'selected' else ())
            del members[2]  # Left since preview; the old object can still exist in a cache.
            members[3].bot = True
            members[4].guild.id = 11
            members[5] = member(5)  # New join must not expand the preview.
            guild.fetch_member.reset_mock()
            guild.fetch_member.side_effect = members.get
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (1, 0, 3))
            self.assertEqual([c.args[0] for c in guild.fetch_member.await_args_list], [1, 2, 3, 4])
            members[1].send.assert_awaited_once()
            for uid in (3, 4, 5):
                members[uid].send.assert_not_awaited()

    async def test_loss_of_gbop_role_or_activation_does_not_expand_or_revoke_receive_only_permission(self):
        for audience in ('selected', 'all_server_members'):
            recipient = member(2)
            self.guild([recipient])
            preview = await self.ns['_resolve_owner_broadcast_draft'](
                'test', audience, (2,) if audience == 'selected' else ())
            recipient.role = False
            self.ns['member_access_error'].return_value = 'revoked'
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (1, 0, 0))
            recipient.add_roles.assert_not_awaited()
        self.ns['member_access_error'].assert_not_called()
        self.ns['db'].assert_not_called()

    async def test_server_dm_privacy_and_uncertain_failures_are_not_retried(self):
        for audience in ('selected', 'all_server_members'):
            recipients = [member(1, role=False), member(2), member(3)]
            recipients[0].send.side_effect = PermissionError('Synthetic disabled server-member DMs')
            recipients[1].send.side_effect = TimeoutError('Synthetic uncertain transport failure')
            self.guild(recipients)
            preview = await self.ns['_resolve_owner_broadcast_draft'](
                'test', audience, (1, 2, 3) if audience == 'selected' else ())
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (1, 2, 0))
            for recipient in recipients:
                recipient.send.assert_awaited_once()

    async def test_large_server_preview_requires_every_page_and_sends_sequentially_once(self):
        recipients = [member(uid, role=False) for uid in range(1, 106)]
        active, peak = 0, 0

        async def limited_send(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)  # Synthetic yield during a rate-limited Discord send.
            active -= 1

        for recipient in recipients:
            recipient.send.side_effect = limited_send
        self.guild(recipients)
        preview = await self.ns['_resolve_owner_broadcast_draft']('Exact test', 'all_server_members', ())
        view = self.view(preview)
        await view.confirm_send.callback(self.interaction())
        self.assertTrue(all(m.send.await_count == 0 for m in recipients))
        observed = [r.user_id for r in preview.page(0)]
        for page in range(1, preview.page_count):
            interaction = self.interaction()
            await view.next_page.callback(interaction)
            self.assertEqual(interaction.response.edit_message.await_args.kwargs['content'], preview.message)
            observed.extend(r.user_id for r in preview.page(page))
        self.assertEqual(observed, list(range(1, 106)))
        await asyncio.gather(*(view.confirm_send.callback(self.interaction()) for _ in range(4)))
        self.assertEqual(peak, 1)
        self.assertEqual(view.state, 'finished')
        for recipient in recipients:
            recipient.send.assert_awaited_once_with(preview.message, allowed_mentions=self.none_mentions)

    async def test_server_preview_timeout_and_expired_result_transport_cannot_replay(self):
        recipients = [member(uid, role=False) for uid in range(1, 26)]
        self.guild(recipients)
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'all_server_members', ())
        expired = self.view(preview)
        expired.expires_at = 0
        await expired.confirm_send.callback(self.interaction())
        self.assertTrue(all(m.send.await_count == 0 for m in recipients))
        view = self.view(preview)
        for _ in range(1, preview.page_count):
            await view.next_page.callback(self.interaction())
        interaction = self.interaction()
        interaction.followup.send.side_effect = TimeoutError('Synthetic expired interaction after long delivery')
        with self.assertRaises(TimeoutError):
            await view.confirm_send.callback(interaction)
        await view.confirm_send.callback(self.interaction())
        self.assertEqual(view.state, 'finished')
        self.assertTrue(all(m.send.await_count == 1 for m in recipients))

    async def test_server_audience_buttons_reject_nonowner_and_foreign_guild(self):
        self.ns['_send_owner_broadcast'] = AsyncMock()
        for audience in ('selected', 'all_server_members'):
            for name in ('confirm_send', 'cancel_send', 'next_page', 'previous_page'):
                for uid, gid in ((1, 10), (999, 11), (999, None)):
                    view = self.view(draft((2,), audience, (2,) if audience == 'selected' else ()))
                    interaction = self.interaction(uid, gid)
                    await getattr(view, name).callback(interaction)
                    self.assertEqual(view.state, 'pending')
                    interaction.response.send_message.assert_awaited_once()
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_unknown_audience_cannot_fall_through_to_sender(self):
        self.assertEqual(await self.ns['_send_owner_broadcast'](replace(draft(), audience='unknown')), (0, 0, 2))
        self.ns['_authorized_scheduled_member'].assert_not_awaited()

    async def test_receiving_server_message_does_not_bypass_reply_restrictions(self):
        node = next(n for n in ast.parse(BOT.read_text()).body if getattr(n, 'name', '') == 'on_message')
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT), 'exec'), self.ns)
        self.ns['GBOP_PRIVATE_ROOMS'] = Mock()
        self.ns['client'].user = None
        self.ns['ai_generate_reply'] = Mock(side_effect=AssertionError('Unauthorized reply'))
        for role, record, denial in (
            (False, {'activated': 0, 'revoked': 0}, 'member role'),
            (True, {'activated': 0, 'revoked': 0}, 'Activate your GBOP'),
            (True, {'activated': 1, 'revoked': 1}, 'revoked'),
        ):
            recipient = member(2, role=role)
            self.guild([recipient])
            preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (2,))
            self.assertEqual(await self.ns['_send_owner_broadcast'](preview), (1, 0, 0))
            self.ns['ensure_member_record'].assert_not_called()
            self.ns['db'].assert_not_called()
            recipient.add_roles.assert_not_awaited()
            self.ns['ai_resolve_member'] = AsyncMock(return_value=recipient)
            self.ns['ensure_member_record'] = Mock()
            self.ns['get_member_record'] = Mock(return_value=record.copy())
            reply = NS(author=recipient, channel=NS(id=100), guild=None, mentions=[], reply=AsyncMock())
            await self.ns['on_message'](reply)
            self.assertIn(denial, reply.reply.await_args.args[0])
            self.assertEqual(self.ns['get_member_record'].return_value, record)
            self.ns['ensure_member_record'].reset_mock()
        self.ns['ai_generate_reply'].assert_not_called()


    def picker(self, audience='selected', text='Synthetic picker message'):
        return self.ns['OwnerBroadcastMemberPicker'](text, audience)

    async def choose(self, picker, ids=(2,), interaction=None):
        interaction = interaction or self.interaction()
        await picker.choose_members.callback(interaction, NS(values=[member(uid) for uid in ids]))
        return interaction

    async def test_blank_selected_members_opens_native_picker_without_lookup_or_send(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        self.ns['_send_owner_broadcast'] = AsyncMock()
        for audience in ('selected', 'all_except'):
            interaction = self.interaction()
            await self.ns['gbopmessage'](interaction, 'Exact picker text', audience, '   ')
            sent = interaction.followup.send.await_args
            self.assertTrue(sent.kwargs['ephemeral'])
            self.assertEqual(sent.args[0], owner_message('Exact picker text'))
            self.assertIs(sent.kwargs['allowed_mentions'], self.none_mentions)
            self.assertIsInstance(sent.kwargs['view'], self.ns['OwnerBroadcastMemberPicker'])
            self.assertEqual(sent.kwargs['view'].audience, audience)
            self.assertIn('25', sent.kwargs['embed'].description)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()
        self.ns['_send_owner_broadcast'].assert_not_awaited()

    async def test_native_picker_nonrole_member_becomes_exact_preview_without_access_or_send(self):
        recipient = member(2, role=False)
        guild = self.guild([recipient, member(3)])
        picker = self.picker(text='Exact text @everyone')
        interaction = await self.choose(picker)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=False)
        edit = interaction.edit_original_response.await_args.kwargs
        preview = edit['view']
        self.assertEqual([r.user_id for r in preview.draft.recipients], [2])
        self.assertEqual(edit['content'], owner_message('Exact text @everyone'))
        self.assertIs(edit['allowed_mentions'], self.none_mentions)
        self.assertIn('`2`', edit['embed'].description)
        self.assertNotIn('`3`', edit['embed'].description)
        self.assertEqual(picker.state, 'previewed')
        self.assertTrue(picker.stopped)
        self.assertIs(preview.preview_message, interaction.edit_original_response.return_value)
        guild.fetch_member.assert_awaited_once_with(2)
        recipient.send.assert_not_awaited()
        recipient.add_roles.assert_not_awaited()
        self.ns['db'].assert_not_called()
        self.ns['ensure_member_record'].assert_not_called()
        self.ns['member_access_error'].assert_not_called()

    async def test_native_picker_exclusions_retain_eligible_only_audience(self):
        self.guild([member(1), member(2), member(3, role=False)])
        interaction = await self.choose(self.picker('all_except'), (2,))
        preview = interaction.edit_original_response.await_args.kwargs['view'].draft
        self.assertEqual(preview.audience, 'all_except')
        self.assertEqual([r.user_id for r in preview.recipients], [1])
        self.assertEqual(preview.excluded_count, 1)

    async def test_native_picker_invalid_or_bot_selection_never_silently_reduces_audience(self):
        for invalid in (member(3, bot=True), member(3, guild_id=11), None):
            with self.subTest(invalid=invalid):
                self.guild([member(2), invalid] if invalid else [member(2)])
                picker = self.picker()
                interaction = await self.choose(picker, (2, 3))
                self.assertEqual(picker.state, 'pending')
                interaction.edit_original_response.assert_not_awaited()
                self.assertIn('not verified current human members', interaction.followup.send.await_args.args[0])
                self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])

    async def test_native_picker_can_reselect_after_lookup_failure(self):
        self.guild([member(2, role=False)])
        picker = self.picker()
        await self.choose(picker, (3,))
        successful = await self.choose(picker, (2,))
        self.assertEqual(picker.state, 'previewed')
        self.assertEqual(successful.edit_original_response.await_args.kwargs['view'].draft.recipients[0].user_id, 2)

    async def test_native_picker_owner_and_guild_guard_all_actions_before_lookup(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for uid, gid in ((1, 10), (999, 11), (999, None)):
            for action in ('choose', 'cancel'):
                picker = self.picker()
                interaction = self.interaction(uid, gid)
                if action == 'choose':
                    await self.choose(picker, interaction=interaction)
                else:
                    await picker.cancel_selection.callback(interaction)
                self.assertEqual(picker.state, 'pending')
                interaction.response.send_message.assert_awaited_once()
                interaction.response.defer.assert_not_awaited()
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_native_picker_configuration_change_closes_access(self):
        for setting in ('GTOP_OWNER_USER_ID', 'GTOP_GUILD_ID'):
            picker = self.picker()
            old = self.ns[setting]
            self.ns[setting] = old + 1
            if setting == 'GTOP_OWNER_USER_ID':
                self.ns['is_owner'] = lambda m: m.id == self.ns['GTOP_OWNER_USER_ID']
            interaction = await self.choose(picker)
            interaction.response.send_message.assert_awaited_once()
            interaction.edit_original_response.assert_not_awaited()
            self.ns[setting] = old

    async def test_native_picker_rejects_empty_oversized_and_malformed_values(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for ids in ((), tuple(range(1, 27)), (0,), (-1,), (2**64,), ('2',), (True,)):
            interaction = await self.choose(self.picker(), ids)
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_native_picker_cannot_reopen_after_cancel_or_expiry(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for state in ('cancelled', 'expired'):
            picker = self.picker()
            interaction = self.interaction()
            if state == 'cancelled':
                await picker.cancel_selection.callback(interaction)
            else:
                picker.expires_at = 0
                await self.choose(picker, interaction=interaction)
            self.assertEqual(picker.state, state)
            self.assertIsNone(interaction.response.edit_message.await_args.kwargs['view'])
            await self.choose(picker)
            await picker.cancel_selection.callback(self.interaction())
            self.assertEqual(picker.state, state)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_native_picker_timeout_removes_controls_and_never_sends(self):
        picker = self.picker()
        picker.preview_message = NS(edit=AsyncMock())
        await picker.on_timeout()
        self.assertEqual(picker.state, 'expired')
        picker.preview_message.edit.assert_awaited_once()
        self.assertIsNone(picker.preview_message.edit.await_args.kwargs['view'])
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        await self.choose(picker)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_native_picker_timeout_transport_failure_still_closes(self):
        picker = self.picker()
        picker.preview_message = NS(edit=AsyncMock(side_effect=TimeoutError()))
        await picker.on_timeout()
        self.assertEqual(picker.state, 'expired')
        self.ns['logger'].warning.assert_called_once()

    async def test_native_picker_expiry_during_membership_lookup_cannot_publish_preview(self):
        picker = self.picker()
        async def slow_lookup(*args):
            picker.expires_at = 0
            return draft((2,), 'selected', (2,))
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock(side_effect=slow_lookup)
        interaction = await self.choose(picker)
        self.assertEqual(picker.state, 'expired')
        self.assertIsNone(interaction.edit_original_response.await_args.kwargs['view'])

    async def test_native_picker_duplicate_events_produce_one_preview(self):
        self.guild([member(2), member(3)])
        picker = self.picker()
        interactions = [self.interaction() for _ in range(3)]
        await asyncio.gather(*(self.choose(picker, (i + 2,) if i < 2 else (2,), interaction)
                               for i, interaction in enumerate(interactions)))
        self.assertEqual(sum(i.edit_original_response.await_count for i in interactions), 1)
        self.assertEqual(sum(i.response.send_message.await_count for i in interactions), 2)
        self.assertEqual(picker.state, 'previewed')

    async def test_native_picker_uncertain_preview_edit_is_not_replayed(self):
        self.guild([member(2)])
        picker = self.picker()
        interaction = self.interaction()
        interaction.edit_original_response.side_effect = TimeoutError('Synthetic uncertain preview update')
        with self.assertRaises(TimeoutError):
            await self.choose(picker, interaction=interaction)
        attempted_preview = interaction.edit_original_response.await_args.kwargs['view']
        self.assertTrue(attempted_preview.stopped)
        self.assertEqual(picker.state, 'previewed')
        repeated = await self.choose(picker)
        repeated.edit_original_response.assert_not_awaited()

    async def test_native_picker_25_people_still_require_all_preview_pages_and_single_confirmation(self):
        recipients = [member(uid, role=False) for uid in range(1, 26)]
        self.guild(recipients)
        interaction = await self.choose(self.picker(), tuple(range(1, 26)))
        view = interaction.edit_original_response.await_args.kwargs['view']
        self.assertEqual(len(view.draft.recipients), 25)
        self.assertEqual(view.draft.page_count, 3)
        await view.confirm_send.callback(self.interaction())
        self.assertTrue(all(m.send.await_count == 0 for m in recipients))
        await view.next_page.callback(self.interaction())
        await view.next_page.callback(self.interaction())
        await asyncio.gather(*(view.confirm_send.callback(self.interaction()) for _ in range(3)))
        self.assertTrue(all(m.send.await_count == 1 for m in recipients))
        self.assertEqual(view.state, 'finished')

    async def test_native_picker_deferred_failure_prevents_member_lookup(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        picker = self.picker()
        interaction = self.interaction()
        interaction.response.defer.side_effect = TimeoutError('Synthetic acknowledgement error')
        with self.assertRaises(TimeoutError):
            await self.choose(picker, interaction=interaction)
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()
        interaction.edit_original_response.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
