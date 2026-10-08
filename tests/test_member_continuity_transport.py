"""Synthetic transport/audience checks for seamless member continuity. No live calls."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.member_continuity import continuity_prompt
from test_voice_latency import method

ROOT = Path(__file__).resolve().parents[1]


def function(name, namespace):
    node = next(n for n in ast.parse((ROOT/'bot.py').read_text()).body
                if getattr(n, 'name', '') == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), namespace)
    return namespace[name]


class MemberContinuityTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_every_contextual_surface_has_same_two_member_scoped_tools(self):
        source = [{'type': 'function', 'name': 'ordinary', 'parameters': {}}]
        tools = contextual_tools(source)
        self.assertTrue({'ordinary', 'get_member_continuity', 'remember_member_context'}.issubset({row['name'] for row in tools}))
        self.assertEqual(len(source), 1)
        self.assertEqual([row['name'] for row in contextual_tools(tools)].count('remember_member_context'), 1)
        for tool in tools[1:]:
            self.assertNotIn('user_id', tool['parameters']['properties'])
            self.assertNotIn('guild_id', tool['parameters']['properties'])

    def test_voice_instructions_hydrate_bounded_private_context_without_history_preload(self):
        context = MarketConversation((10, 20, 'discord_voice'))
        def hydrate(c):
            self.assertIs(c, context)
            c._member_continuity = {'available': True, 'revision': 7,
                'untrusted_context': {'summary': 'Member corrected NAS plan to Young Lefty.'},
                'unfinished_drafts': [{'draft_id': 'same-owned-draft'}]}
        namespace = dict(get_profile=lambda *a: {'configured': True}, db=object(), GTOP_GUILD_ID=10,
                         profile_context=lambda p: 'member profile', market_clock=lambda: 'clock',
                         CANONICAL_KNOWLEDGE='canon', MARKET_PROMPT='market',
                         build_voice_instructions=lambda c, m, p: c + m + p)
        session = NS(member=NS(id=20), market_context=context)
        with patch('gbop_voice_web.member_continuity.hydrate', side_effect=hydrate), \
                patch('gbop_voice_web.midpoint_preferences.preference_context', return_value=''):
            value = method('instructions', namespace)(session)
        self.assertIn('Young Lefty', value)
        self.assertIn('same-owned-draft', value)
        self.assertLessEqual(len(continuity_prompt(context)), 1100)
        context.continuity_private = False
        self.assertEqual(continuity_prompt(context), '')
        namespace['get_profile'] = Mock(side_effect=AssertionError('No private profile in shared voice'))
        with patch('gbop_voice_web.member_continuity.hydrate'), \
                patch('gbop_voice_web.midpoint_preferences.preference_context', side_effect=AssertionError('No saved preferences preload')):
            shared = method('instructions', namespace)(session)
        self.assertNotIn('Young Lefty', shared)
        self.assertIn('Shared voice', shared)

    def test_one_listener_does_not_make_public_voice_private(self):
        guild = NS(id=10)
        owner = NS(id=20, bot=False, guild=guild)
        other = NS(id=21, bot=False, guild=guild)
        channel = NS(id=40, guild=guild, private=False, members=[owner])
        guild.get_member = lambda _: owner
        guild.get_channel = lambda _: channel
        private = function('gbop_continuity_private', {
            'private_room_autojoin_allowed': lambda m, c: c.private,
            'client': NS(get_guild=lambda _: guild), 'GTOP_GUILD_ID': 10})
        self.assertFalse(private(owner, channel))
        channel.private = True
        channel.members = [owner, NS(id=99, bot=True)]
        self.assertTrue(private(owner, channel))
        channel.members = [owner, other]
        self.assertFalse(private(owner, channel))

    async def test_role_removed_during_voice_blocks_next_tool_before_db_read(self):
        member = NS(id=20)
        gate = Mock()
        namespace = dict(client=NS(get_guild=lambda _: NS(get_member=lambda _: member)),
                         GTOP_GUILD_ID=10, GTOP_OWNER_USER_ID=1, db=object(),
                         is_owner=lambda m: False, has_member_role=lambda m: False,
                         member_access_error=gate, asyncio=asyncio)
        session = NS(member=member, market_context=NS(continuity_private=True))
        result = await method('authorize_member_tool', namespace)(session)
        self.assertIn('access', result)
        gate.assert_not_called()

    async def test_private_acl_loss_stops_retained_session_before_async_cleanup(self):
        after = NS(id=40, guild=NS(id=10))
        order = []
        session = NS(member=NS(id=20), voice_client=NS(channel=NS(id=40)),
                     market_context=NS(continuity_private=True), stop=lambda: order.append('stopped'))
        async def close(member):
            order.append('closed')
        manager = NS(sessions={(10, 20): session}, close_member=close)
        handler = function('on_guild_channel_update', dict(GTOP_GUILD_ID=10,
            GBOP_REALTIME_MANAGER=manager, gbop_continuity_private=lambda *a, **k: False))
        await handler(NS(), after)
        self.assertEqual(order, ['stopped', 'closed'])

    async def test_generated_voice_transcript_is_not_persisted_as_delivered_answer(self):
        async def events():
            yield '{"type":"response.output_audio_transcript.done","item_id":"item-1","transcript":"Generated only"}'
        delivery = NS(transcript=Mock())
        save = Mock(side_effect=AssertionError('Generated text must not be persisted.'))
        session = NS(websocket=events(), market_delivery=delivery,
                     rate_limit_recovery=Mock(), member=NS(id=20))
        await method('receiver_loop', dict(json=__import__('json'), ai_save_message=save))(session)
        delivery.transcript.assert_called_once_with('item-1', 'Generated only', None)
        save.assert_not_called()

    def test_startup_continuity_stays_inside_existing_expanded_instruction_budget(self):
        from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
        from gbop_voice_web.market_data import MARKET_PROMPT
        from gbop_voice_web.midpoint_preferences import MIDPOINT_PROMPT
        from gbop_voice_web.voice_policy import build_voice_instructions
        context = MarketConversation((10, 20, 'voice2'))
        context._member_continuity = {'available': True, 'revision': 1234,
            'untrusted_context': {'summary': 'S' * 1600, 'latest_user_excerpt': 'U' * 600,
                                  'delivered_answer_excerpt': 'A' * 500},
            'unfinished_drafts': [{'draft_id': 'a' * 32}, {'draft_id': 'b' * 32}],
            'last_referenced_trade': {'trade_number': 10}}
        prompt = (build_voice_instructions(CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile')
                  + '\n\n' + MIDPOINT_PROMPT + continuity_prompt(context))
        self.assertLess(len(prompt), 54000)
        self.assertEqual(prompt.count(CANONICAL_KNOWLEDGE), 1)
        self.assertEqual(prompt.count(MARKET_PROMPT), 1)

    def test_discord_identities_share_member_scoped_session_class(self):
        source = (ROOT/'bot.py').read_text()
        self.assertIn('GBOP_VOICE_CLIENTS.append(discord.Client(intents=intents))', source)
        self.assertIn("MarketConversation((GTOP_GUILD_ID, member.id, 'discord_voice')", source)
        self.assertNotIn('bot_token', str(contextual_tools([])))


class HelperAudienceCacheLagTests(unittest.IsolatedAsyncioTestCase):
    def test_helper_stale_members_cannot_hide_primary_audience(self):
        primary_guild, helper_guild = NS(id=10), NS(id=10)
        owner = NS(id=20, bot=False, guild=primary_guild)
        helper_owner = NS(id=20, bot=False, guild=helper_guild)
        intruder = NS(id=21, bot=False, guild=primary_guild)
        primary = NS(id=40, guild=primary_guild, private=True, members=[owner, intruder])
        helper = NS(id=40, guild=helper_guild, private=True, members=[helper_owner])
        primary_guild.get_member = lambda _: owner
        primary_guild.get_channel = lambda _: primary
        checked = []
        def allowed(member, channel):
            checked.append((member, channel))
            return channel.private
        private = function('gbop_continuity_private', dict(
            client=NS(get_guild=lambda _: primary_guild), GTOP_GUILD_ID=10,
            private_room_autojoin_allowed=allowed))
        self.assertFalse(private(helper_owner, helper))
        self.assertEqual(checked, [(owner, primary)])
        primary.members = [owner]
        primary.private = False
        self.assertFalse(private(helper_owner, helper))
        primary_guild.get_channel = lambda _: None
        self.assertFalse(private(helper_owner, helper))

    async def test_explicit_human_join_stops_even_if_both_caches_lag(self):
        guild = NS(id=10)
        owner = NS(id=20, guild=guild, bot=False)
        visitor = NS(id=21, guild=guild, bot=False)
        stale_channel = NS(id=40, guild=guild, members=[owner])
        order = []
        session = NS(member=owner, voice_client=NS(channel=stale_channel),
                     market_context=NS(continuity_private=True), stop=lambda: order.append('stopped'))
        class CleanupReached(Exception):
            pass
        async def close(member):
            self.assertIs(member, owner)
            order.append('cleanup')
            raise CleanupReached()
        manager = NS(sessions={(10, 20): session}, close_member=close)
        handler = function('on_voice_state_update', dict(
            GBOP_REALTIME_MANAGER=manager, gbop_continuity_private=lambda *a: True,
            GTOP_GUILD_ID=10))
        # Privacy helper deliberately sees stale caches; only the incoming
        # concrete member/channel identity can close this disclosure window.
        with self.assertRaises(CleanupReached):
            await handler(visitor, NS(channel=None), NS(channel=stale_channel))
        self.assertEqual(order, ['stopped', 'cleanup'])


if __name__ == '__main__':
    unittest.main()
