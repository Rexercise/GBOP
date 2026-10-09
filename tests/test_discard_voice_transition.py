"""Real Discord speech/playback transitions, with synthetic SQLite and no network."""
import ast
import asyncio
import base64
from collections import deque
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web import journal_coach as coach
from gbop_voice_web.journal_context import JournalBinding
from gbop_voice_web.response_delivery import MarketResponseDelivery
from gbop_voice_web.voice_runtime import delivery_identity
from gbop_voice_web.voice_work import VoiceToolWork
import test_journal_discard_flow as fixture
from test_voice_latency import method


class EventStream:
    def __init__(self):
        self.events = deque()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.events:
            raise StopAsyncIteration
        return json.dumps(self.events.popleft())


class Player:
    def __init__(self):
        self.channel = NS(id=99)
        self.source = self.after = None
        self.playing = False

    def play(self, source, after):
        self.source, self.after, self.playing = source, after, True

    def is_playing(self):
        return self.playing

    def stop_playing(self):
        self.playing = False
        self.after(None)

    async def drain(self):
        while self.source.read():
            pass
        self.playing = False
        self.after(None)
        await asyncio.sleep(0)


def playback_manager():
    # Loading only these classes avoids importing bot.py's deployment startup.
    path = Path(__file__).resolve().parents[1] / 'bot.py'
    names = {'GBOPRealtimeAudioSource', 'GBOPOutputManager'}
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.ClassDef) and node.name in names]
    namespace = {'asyncio': asyncio, 'threading': threading,
                 'discord': NS(AudioSource=object),
                 'gbop_pcm24_mono_to_pcm48_stereo': lambda value: value}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['GBOPOutputManager'](99)


class DiscardVoiceTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixture.JournalDiscardFlowTests()
        self.fixture.setUp()
        self.draft = self.fixture.stage()
        self.preview = self.fixture.preview(self.draft, deliver=False, text=None)
        self.context = self.fixture.context
        self.player = Player()
        self.manager = playback_manager()
        self.session = NS(member=NS(id=20), voice_client=self.player,
            websocket=EventStream(), _voice_turn_count=1, closed=False,
            market_context=self.context,
            market_delivery=MarketResponseDelivery(self.context),
            rate_limit_recovery=Mock(), send_event=AsyncMock(return_value=True),
            output_source=None, output_item_id=None, _logged_audio_items=set(),
            tool_output_pending=False, _tool_response_options={}, _last_response_options={})
        self.work = self.session.tool_work = VoiceToolWork(self.session)

        async def inline(fn, *args):
            return fn(*args)

        self.receiver = method('receiver_loop', {'json': json, 'base64': base64,
            'asyncio': NS(to_thread=inline), 'ai_save_message': Mock(),
            'gbop_output_manager': lambda channel: self.manager})

    async def asyncTearDown(self):
        self.work.cancel()
        await asyncio.sleep(0)
        self.fixture.tearDown()

    async def events(self, *events):
        self.session.websocket.events.extend(events)
        await self.receiver(self.session)
        await asyncio.sleep(0)

    async def play_preview(self, *, drain=True, transcript=True, status='completed'):
        events = [
            {'type': 'response.created', 'response': {'id': 'preview-response'}},
            {'type': 'response.output_audio.delta', 'response_id': 'preview-response',
             'item_id': 'preview-item', 'delta': base64.b64encode(b'\0' * 100).decode()},
        ]
        if transcript:
            events.append({'type': 'response.output_audio_transcript.done',
                'response_id': 'preview-response', 'item_id': 'preview-item',
                'transcript': self.preview['confirmation_prompt']})
        events.extend([
            {'type': 'response.output_audio.done', 'response_id': 'preview-response',
             'item_id': 'preview-item'},
            {'type': 'response.done', 'response': {'id': 'preview-response', 'status': status}},
        ])
        await self.events(*events)
        if drain:
            await self.player.drain()

    async def speech(self):
        await self.events({'type': 'input_audio_buffer.speech_started'})

    def discard(self, text='Yes, discard it.'):
        return self.context.run('discard_journal_story',
            {'draft_id': self.draft['draft_id'], 'confirmation_text': text},
            lambda name, args: coach.coach_tool(self.fixture.db, 10, 20, name, args))

    def assert_unchanged(self, result):
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.fixture.stored(self.draft['draft_id'])['draft_status'], 'unfinished')

    async def test_completed_playback_then_real_speech_accepts_one_confirmation(self):
        await self.play_preview()
        old_generation = self.context.generation
        self.assertTrue(self.context._journal_discard_preview['delivered'])
        stale_binding = JournalBinding(self.context, old_generation)
        await self.speech()
        pending = self.context._journal_discard_preview
        self.assertTrue(pending['delivered'])
        self.assertTrue(pending['advanced_member_turn'])
        self.assertEqual(pending['generation'], old_generation + 1)
        self.assertEqual(self.context.generation, old_generation + 2)
        with self.assertRaises(ValueError):
            with stale_binding.guard(10, 20):
                self.fail('The earlier generation must remain fenced.')
        result = self.discard()
        self.assertTrue(result['discarded'], result)
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 0)
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0], 0)

    async def test_generated_but_undrained_preview_cannot_authorize(self):
        await self.play_preview(drain=False)
        self.assertFalse(self.context._journal_discard_preview['delivered'])
        await self.speech()
        self.assertIsNone(self.context._journal_discard_preview)
        await self.player.drain()  # A late interrupted callback cannot revive it.
        self.assert_unchanged(self.discard())

    async def test_partial_response_cannot_authorize(self):
        await self.play_preview(status='incomplete')
        await self.speech()
        self.assertIsNone(self.context._journal_discard_preview)
        self.assert_unchanged(self.discard())

    async def test_missing_transcript_and_late_receipt_cannot_authorize(self):
        await self.play_preview(transcript=False)
        await self.speech()
        await self.events({'type': 'response.output_audio_transcript.done',
            'response_id': 'preview-response', 'item_id': 'preview-item',
            'transcript': self.preview['confirmation_prompt']})
        self.assertIsNone(self.context._journal_discard_preview)
        self.assert_unchanged(self.discard())

    async def test_second_speech_does_not_carry_the_confirmation_again(self):
        await self.play_preview()
        await self.speech()
        self.assertIsNotNone(self.context._journal_discard_preview)
        await self.speech()
        self.assertIsNone(self.context._journal_discard_preview)
        self.assert_unchanged(self.discard())

    async def test_same_turn_still_cannot_confirm(self):
        await self.play_preview()
        self.assert_unchanged(self.discard())

    async def test_expired_stale_wrong_owner_or_replaced_session_cannot_carry(self):
        await self.play_preview()
        original = dict(self.context._journal_discard_preview)
        for changes in ({'expires_at': 0}, {'generation': self.context.generation - 1},
                        {'owner': (10, 30, self.context.session_id)},
                        {'owner': (10, 20, 'old-session')},
                        {'advanced_member_turn': True}, {'response_id': None}):
            with self.subTest(changes=changes):
                self.context._journal_discard_preview = {**original,
                    'generation': self.context.generation, **changes}
                await self.speech()
                self.assertIsNone(self.context._journal_discard_preview)
                self.assert_unchanged(self.discard())

    async def test_plain_invalidation_cancellation_and_response_failure_clear_preview(self):
        await self.play_preview()
        original = dict(self.context._journal_discard_preview)
        for action in ('invalidate', 'cancel', 'read_status_cancel', 'cancelled', 'failed', 'close'):
            with self.subTest(action=action):
                self.context._journal_discard_preview = {**original, 'generation': self.context.generation}
                if action == 'invalidate':
                    self.context.invalidate()
                elif action == 'cancel':
                    self.work.cancel()
                elif action == 'read_status_cancel':
                    self.work.cancel(preserve_read_status=True)
                elif action == 'close':
                    self.context.close()
                else:
                    await self.events({'type': 'response.done',
                        'response': {'id': action, 'status': action}})
                self.assertIsNone(self.context._journal_discard_preview)

    async def test_changed_authorization_invalidates_a_carried_preview(self):
        await self.play_preview()
        await self.speech()
        self.fixture.conn.execute("UPDATE members SET updated_at='auth-v2' WHERE user_id=20")
        self.fixture.conn.commit()
        self.assert_unchanged(self.discard())
        self.assertIsNone(self.context._journal_discard_preview)

    async def test_draft_revision_check_still_rejects_concurrent_edit(self):
        await self.play_preview()
        other = self.fixture.fresh()
        self.fixture.call('get_journal_story', {'draft_id': self.draft['draft_id']},
                          'Read this draft.', context=other)
        self.fixture.call('stage_journal_story', {'draft_id': self.draft['draft_id'],
            'story_json': '{"context_notes":"Synthetic newer fact"}'},
            'Synthetic newer fact.', context=other)
        await self.speech()
        self.assert_unchanged(self.discard())

    async def test_negative_confirmation_leaves_draft_unchanged(self):
        await self.play_preview()
        await self.speech()
        self.assert_unchanged(self.discard('No, keep it.'))

    async def test_finalized_draft_remains_protected_after_speech_carry(self):
        await self.play_preview()
        other = self.fixture.fresh()
        saved = self.fixture.call('save_journal_story', {'draft_id': self.draft['draft_id']},
            'Save the journal narrative.', context=other)
        self.assertTrue(saved['ok'], saved)
        await self.speech()
        result = self.discard()
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.fixture.stored(self.draft['draft_id'])['draft_status'], 'finalized')
        self.assertEqual(self.fixture.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 1)

    async def test_speech_still_cancels_tools_and_retains_interrupted_status(self):
        await self.play_preview()
        gate = asyncio.Event()
        task = asyncio.create_task(gate.wait())
        await asyncio.sleep(0)
        self.work.tasks.add(task)
        self.work.tail = task
        self.work.responses.add('old-tool-response')
        identity = delivery_identity(self.session)
        read = {'identity': identity, 'websocket': self.session.websocket,
                'turn': self.session._voice_turn_count, 'tool': 'get_journal_history'}
        write = {'identity': identity, 'websocket': self.session.websocket,
                 'turn': self.session._voice_turn_count, 'reconciled': False}
        self.work.read_calls['read-call'] = read
        self.session._journal_write_recovery = write
        self.work.write_barriers['old-call'] = 'old-write'
        old_scope = self.work.scope()
        await self.speech()
        self.assertTrue(task.cancelled())
        self.assertFalse(self.work.current(old_scope))
        self.assertFalse(self.work.pending)
        self.assertIsNone(self.work.tail)
        self.assertEqual(self.work.write_barriers, {})
        self.assertFalse(self.work.accepts({'response_id': 'old-tool-response'}))
        self.assertIn('interrupted_at', read)
        self.assertIn('interrupted_at', write)
        self.assertIs(self.session._journal_write_recovery, write)
        self.assertTrue(self.context._journal_discard_preview['delivered'])
        self.session.send_event.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
