"""Owner broadcasts with synthetic members and a fake Discord transport only."""
import ast
import asyncio
from dataclasses import FrozenInstanceError
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


def member(user_id, *, name=None, role=True, bot=False):
    return NS(id=user_id, display_name=name or f'Member {user_id}', bot=bot,
              role=role, send=AsyncMock())


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
        for mode, raw in (('all', '1'), ('selected', ''), ('all_except', ''), ('unknown', '1')):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_selection(mode, raw)
        self.assertEqual(parse_selection('all', ''), ())

    def test_all_three_audiences_resolve_exact_ids(self):
        self.assertEqual([r.user_id for r in draft().recipients], [1, 2])
        self.assertEqual([r.user_id for r in draft(audience='selected', selected=(2,)).recipients], [2])
        excluded = draft(audience='all_except', selected=(2,))
        self.assertEqual([r.user_id for r in excluded.recipients], [1])
        self.assertEqual(excluded.excluded_count, 1)

    def test_unknown_selection_cannot_silently_broaden_audience(self):
        for mode in ('selected', 'all_except'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'not currently eligible'):
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
        discord = NS(Interaction=object, Embed=FakeEmbed, ButtonStyle=NS(secondary=1, danger=2),
            AllowedMentions=NS(none=lambda: self.none_mentions),
            utils=NS(escape_markdown=lambda value: re.sub(r'([*_`])', r'\\\1', value)),
            ui=NS(View=FakeView, Button=object, button=lambda **kwargs: lambda fn: FakeButton(fn, **kwargs)))
        self.ns = dict(asyncio=asyncio, time=time, discord=discord, Literal=Literal,
            GTOP_OWNER_USER_ID=999, GTOP_GUILD_ID=10,
            is_owner=lambda m: m.id == 999, has_member_role=lambda m: m.role,
            client=NS(get_guild=Mock()), logger=Mock(),
            member_access_error=Mock(return_value=None), db=object(),
            MEMBER_ACCESS_UNAVAILABLE=MEMBER_ACCESS_UNAVAILABLE,
            AUDIENCE_LABELS=AUDIENCE_LABELS, PREVIEW_SECONDS=PREVIEW_SECONDS,
            build_draft=build_draft, owner_message=owner_message, parse_selection=parse_selection,
            _authorized_scheduled_member=AsyncMock())
        names = {'_resolve_owner_broadcast_draft', '_send_owner_broadcast', 'OwnerBroadcastView', 'gbopmessage'}
        nodes = [n for n in ast.parse(BOT.read_text()).body if getattr(n, 'name', '') in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT), 'exec'), self.ns)

    def interaction(self, user_id=999, guild_id=10):
        return NS(user=member(user_id), guild_id=guild_id,
                  response=NS(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
                  followup=NS(send=AsyncMock()))

    def guild(self, members, *, error=False):
        async def fetch_members(**kwargs):
            self.assertIsNone(kwargs['limit'])
            for m in members:
                yield m
            if error:
                raise RuntimeError('Synthetic listing failure')
        guild = NS(fetch_members=fetch_members)
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
        for user_id, guild_id in ((1, 10), (999, 11), (999, None)):
            interaction = self.interaction(user_id, guild_id)
            await self.ns['gbopmessage'](interaction, 'test')
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()
        self.ns['_resolve_owner_broadcast_draft'].assert_not_awaited()

    async def test_command_rejects_invalid_inputs_before_lookup(self):
        self.ns['_resolve_owner_broadcast_draft'] = AsyncMock()
        for text, audience, members in (('', 'all', ''), ('x' * 1801, 'all', ''),
                                       ('test', 'selected', ''), ('test', 'all', '1')):
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
        self.ns['_authorized_scheduled_member'].return_value = member(2)
        preview = await self.ns['_resolve_owner_broadcast_draft']('test', 'selected', (2,))
        self.assertEqual([r.user_id for r in preview.recipients], [2])
        self.ns['_authorized_scheduled_member'].assert_awaited_once_with(2)

    async def test_selected_and_excluded_ineligible_members_block_preview(self):
        self.ns['_authorized_scheduled_member'].return_value = None
        with self.assertRaisesRegex(ValueError, 'not currently eligible'):
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


if __name__ == '__main__':
    unittest.main()
