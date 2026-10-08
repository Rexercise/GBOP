"""Authenticated browser continuity hooks: invented members, SQLite, no network."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from gbop_voice_web import member_continuity
from gbop_voice_web.market_conversation import MarketConversation
import test_contextual_journals as fixture
from test_market_conversation import function


class BrowserMemberContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture.ContextualJournalTests()
        self.fixture.setUp()
        self.db = self.fixture.db
        member_continuity.init_continuity(self.db)
        self.context = MarketConversation((10, 20, 'browser_voice'), auth_provider=(self.db, 10, 20))
        self.context._continuity_required = True
        member_continuity.hydrate(self.context)
        self.state = {'user_id': 20, 'live_session_id': 'browser-current', 'market_context': self.context}
        self.auth = AsyncMock(return_value=self.state)
        ns = dict(require_authenticated_user=self.auth, LiveInputRequest=object,
                  LiveDeliveryRequest=object, HTTPException=HTTPException)
        self.cancel = function('cancel_live_context', dict(ns))
        self.input = function('checkpoint_live_input', dict(ns))
        self.delivery = function('delivered_live_context', dict(ns))
        # Keep this SQLite fixture in its owning thread; verify each DB hook is
        # submitted to to_thread instead of executing directly in the handler.
        async def inline(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        self.worker = AsyncMock(side_effect=inline)
        self.patch = patch.object(asyncio, 'to_thread', self.worker)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.fixture.tearDown()

    def body(self, **values):
        return NS(session_id='browser-current', turn_id=values.pop('turn_id', 1), **values)

    def row(self):
        with self.db() as conn:
            return dict(conn.execute('SELECT * FROM member_conversation_context WHERE guild_id=10 AND user_id=20').fetchone())

    async def start(self, turn=1):
        return await self.cancel(None, self.body(turn_id=turn, closed=False))

    async def test_direct_browser_speech_and_delivered_answer_need_no_delegation(self):
        self.assertTrue((await self.start())['ok'])
        lease = self.row()['active_turn']
        result = await self.input(None, self.body(text='I want to discuss my patience next.'))
        self.assertTrue(result['recorded'])
        result = await self.delivery(None, self.body(text='We can discuss patience.', response_id='browser_1'))
        self.assertTrue(result['ok'])
        self.assertEqual(self.row()['latest_user_excerpt'], 'I want to discuss my patience next.')
        self.assertEqual(self.row()['delivered_answer_excerpt'], 'We can discuss patience.')
        self.assertEqual(self.row()['active_turn'], lease)
        self.assertEqual(self.worker.await_count, 3)
        self.assertIsNone(self.context._begun_client_turn)

    async def test_backend_generation_does_not_renew_the_same_client_turn_lease(self):
        await self.start()
        lease = self.row()['active_turn']
        self.context.begin_turn('A normal delegated request.', client_turn=1)
        self.assertEqual(self.row()['active_turn'], lease)
        self.assertEqual(self.row()['turn_sequence'], 1)
        other = MarketConversation((10, 20, 'discord_voice'), auth_provider=(self.db, 10, 20))
        other.begin_turn('Newer Discord utterance.')
        newer = deepcopy(self.row())
        # Backend arrives late with changed text; it cannot steal the lease back.
        with self.assertRaises(ValueError):
            self.context.begin_turn('Late changed backend request.', client_turn=1)
        self.assertEqual(self.row(), newer)
        self.assertTrue((await self.start())['ok'])  # Late same-turn cancel is inert.
        self.assertEqual(self.row(), newer)

    async def test_delegate_before_cancel_preserves_newer_discord_lease(self):
        self.context.advance_client_turn(1)
        self.context.begin_turn('Delegate got here first.', client_turn=1)
        other = MarketConversation((10, 20, 'discord_voice'), auth_provider=(self.db, 10, 20))
        other.begin_turn('Newer utterance after the delegate.')
        before = deepcopy(self.row())
        await self.start()
        self.assertEqual(self.row(), before)
        self.assertEqual(self.worker.await_count, 0)

    async def test_old_input_and_delivery_cannot_overwrite_newer_member_turn(self):
        await self.start()
        other = MarketConversation((10, 20, 'discord_voice'), auth_provider=(self.db, 10, 20))
        other.begin_turn('A newer private topic.')
        before = deepcopy(self.row())
        self.assertFalse((await self.input(None, self.body(text='Old browser input.')))['recorded'])
        await self.delivery(None, self.body(text='Old answer.', response_id='old'))
        self.assertEqual(self.row(), before)

    async def test_exact_session_and_turn_are_required_for_input(self):
        await self.start()
        before = deepcopy(self.row())
        for session, turn in [('someone-else', 1), ('browser-current', 0), ('browser-current', 2)]:
            result = await self.input(None, NS(session_id=session, turn_id=turn, text='Wrong scope.'))
            self.assertFalse(result['recorded'])
        self.assertEqual(self.row(), before)
        with self.assertRaises(HTTPException):
            await self.input(None, self.body(text='x' * 12001))
        self.assertEqual(self.row(), before)

    async def test_actual_privacy_request_precedes_delivered_answer_storage(self):
        await self.start()
        await self.input(None, self.body(text='Do not record this conversation. ' + 'Invented private detail. ' * 60))
        result = await self.delivery(None, self.body(text='Okay, recording is paused.', response_id='pause'))
        self.assertTrue(result['ok'])
        self.assertTrue(self.row()['recording_paused'])
        self.assertEqual(self.row()['latest_user_excerpt'], '')
        self.assertEqual(self.row()['delivered_answer_excerpt'], '')
        await self.start(2)
        await self.input(None, self.body(turn_id=2, text='A new topic does not resume recording.'))
        self.assertTrue(self.row()['recording_paused'])
        await self.start(3)
        await self.input(None, self.body(turn_id=3, text='Resume recording.'))
        self.assertFalse(self.row()['recording_paused'])

    async def test_close_and_unauthorized_input_cannot_revive_context(self):
        await self.start()
        before = deepcopy(self.row())
        await self.cancel(None, self.body(turn_id=2, closed=True))
        result = await self.input(None, self.body(turn_id=2, text='After close.'))
        self.assertFalse(result['recorded'])
        self.assertEqual(self.row(), before)
        self.auth.side_effect = PermissionError('Not authenticated')
        with self.assertRaises(PermissionError):
            await self.input(None, self.body(text='Unauthorized'))
        self.assertEqual(self.row(), before)

    async def test_quick_prior_optout_is_atomic_with_new_speech_claim(self):
        await self.start()
        result = await self.cancel(None, self.body(turn_id=2, closed=False,
            previous_input='Do not record this conversation. Journal this private hypothetical story.'))
        self.assertTrue(result['ok'])
        self.assertTrue(self.row()['recording_paused'])
        self.assertEqual(self.row()['latest_user_excerpt'], '')
        self.assertIsNone(getattr(self.context, '_client_text', None))
        self.assertEqual(self.fixture.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)
        self.assertEqual(self.fixture.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0], 0)
        await self.input(None, self.body(turn_id=2, text='The next short utterance has no privacy wording.'))
        await self.delivery(None, self.body(turn_id=2, text='A direct answer.', response_id='response2'))
        self.assertTrue(self.row()['recording_paused'])
        self.assertEqual(self.row()['latest_user_excerpt'], '')
        self.assertEqual(self.row()['delivered_answer_excerpt'], '')

    async def test_previous_input_cannot_authorize_resume_or_capture_old_story(self):
        await self.start()
        await self.input(None, self.body(text='Do not record this conversation.'))
        await self.cancel(None, self.body(turn_id=2, closed=False,
            previous_input='Resume recording. Save the journal.'))
        self.assertTrue(self.row()['recording_paused'])
        self.assertEqual(self.row()['latest_user_excerpt'], '')
        self.assertEqual(self.fixture.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)

    async def test_stale_previous_input_cannot_pause_a_newer_turn(self):
        await self.start(2)
        before = deepcopy(self.row())
        await self.cancel(None, self.body(turn_id=1, closed=False, previous_input='Do not record this.'))
        self.assertEqual(self.row(), before)

    async def test_oversized_input_closes_context_and_blocks_later_writes(self):
        await self.start()
        before = deepcopy(self.row())
        with self.assertRaises(HTTPException):
            await self.input(None, self.body(text='Do not record this. ' + 'x' * 12000))
        self.assertTrue(self.context.closed)
        self.assertNotIn('market_context', self.state)
        self.assertFalse((await self.input(None, self.body(text='Late shorter retry')))['ok'])
        self.assertEqual(self.row(), before)

    async def test_oversized_previous_input_fences_before_rejecting(self):
        await self.start()
        before = deepcopy(self.row())
        with self.assertRaises(HTTPException):
            await self.cancel(None, self.body(turn_id=2, closed=False, previous_input='x' * 12001))
        self.assertTrue(self.context.closed)
        self.assertNotIn('market_context', self.state)
        self.assertEqual(self.row(), before)
