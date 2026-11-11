"""Synthetic voice contract/event regressions, not live-audio quality tests."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from gbop_voice_web.voice_policy import STORY_VOICE_PROMPT, build_voice_instructions
from gbop_voice_web.voice_runtime import VOICE_TRUNCATION
from test_voice_latency import method
import test_voice_work as voice_work_tests

ROOT = Path(__file__).resolve().parents[1]


def configured_eagerness(value=None):
    """Evaluate actual startup config without importing a networked Discord bot."""
    tree = ast.parse((ROOT / 'bot.py').read_text())
    nodes = [node for node in tree.body if (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'GBOP_VAD_EAGERNESS'
                for target in node.targets)
        or isinstance(node, ast.If) and any(
            isinstance(child, ast.Name) and child.id == 'GBOP_VAD_EAGERNESS'
            for child in ast.walk(node.test)))]
    namespace = {'os': NS(getenv=lambda key, default: default if value is None else value)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot.py', 'exec'), namespace)
    return namespace['GBOP_VAD_EAGERNESS']


class JournalTurnPolicyTests(unittest.TestCase):
    def test_default_and_invalid_vad_settings_are_patient(self):
        for value in (None, '', 'invalid'):
            with self.subTest(value=value):
                self.assertEqual(configured_eagerness(value), 'low')

    def test_explicit_vad_overrides_remain_supported(self):
        for value in ('low', 'medium', 'high', 'auto'):
            self.assertEqual(configured_eagerness(' ' + value.upper() + ' '), value)

    def test_wire_keeps_automatic_responses_and_server_interruption(self):
        namespace = dict(VOICE_TRUNCATION=VOICE_TRUNCATION, GBOP_REALTIME_MODEL='test',
            GBOP_REALTIME_VOICE='test', GBOP_REALTIME_MAX_OUTPUT_TOKENS=1400,
            GBOP_VAD_EAGERNESS=configured_eagerness(), GBOP_AI_TOOLS=[])
        session = NS(instructions=lambda: build_voice_instructions('Canon', 'Market', 'Profile'))
        wire = method('session_update', namespace)(session)['session']
        self.assertEqual(wire['audio']['input']['turn_detection'], {
            'type': 'semantic_vad', 'eagerness': 'low',
            'create_response': True, 'interrupt_response': True})
        self.assertEqual(wire['instructions'].count(STORY_VOICE_PROMPT), 1)

    def test_listen_first_and_incremental_corrections_are_explicit(self):
        for phrase in ('listen first; no intake menu', 'Wait through pauses/self-corrections',
                       'never finish sentences or classify unfinished corrections',
                       'Capture raw_story before questions', 'Resume SAME draft_id',
                       'Ask ≤1 short question after narration',
                       'no answered/skipped questions or missing-field menus',
                       'One recap after narration, not each chunk',
                       'corrections→changed facts only', 'review_before_finalize once',
                       'no unchanged recap/second yes'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, STORY_VOICE_PROMPT)

    def test_fluidity_does_not_relax_save_or_execution_integrity(self):
        for phrase in ('unknowns save', 'Explicit save+verified receipt',
                       'unfinished≠finalized', 'Opt-out/mock unsaved',
                       'No exactly/verbatim claims without verified actual text',
                       'summary≠original speech',
                       'events→same entry_index/stable event_index',
                       'reentry→separate entry_index',
                       'No invented money/quantity/times/fills', 'no cross-entry transfers',
                       'prepare_journal_discard→preview→assent'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, STORY_VOICE_PROMPT)


class JournalTurnEventTests(unittest.IsolatedAsyncioTestCase):
    def harness(self):
        harness = voice_work_tests.WorkTests()
        harness.setUp()
        self.addAsyncCleanup(harness.asyncTearDown)
        return harness

    async def test_repeated_barge_in_preserves_one_draft_write_without_reviving_old_reply(self):
        harness = self.harness()
        gate = asyncio.Event()
        result = {'ok': True, 'status': 'draft', 'saved': False, 'persisted': True,
                  'draft_status': 'unfinished', 'draft_id': 'a' * 32, 'revision': 1}

        async def finish_draft(*args):
            await gate.wait()
            return result

        provider = AsyncMock(side_effect=finish_draft)
        session = harness.session(provider=provider, authenticated=True)
        await harness.call(session, name='stage_journal_story')
        await harness.finish(session)
        source, playback = session.playback.source, session.playback.voice_client
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        await session.websocket.emit({'type': 'input_audio_buffer.speech_stopped'})
        await session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
        gate.set()
        await voice_work_tests.settle()
        # Superseded tool/response events cannot replay a write or its response.
        await session.websocket.emit({'type': 'response.output_item.done', 'response_id': 'r1',
            'item': {'type': 'function_call', 'name': 'stage_journal_story',
                     'call_id': 'stale-call', 'arguments': '{}'}})
        await harness.finish(session)
        provider.assert_awaited_once()
        source.abort.assert_called_once()
        playback.stop_playing.assert_called_once()
        self.assertIsNone(session.output_source)
        self.assertFalse(session.tool_output_pending)
        self.assertEqual(session._voice_turn_count, 3)
        self.assertFalse(harness.receivers[0].done())
        session.member.send.assert_not_awaited()
        events = [call.args[0] for call in session.send_event.await_args_list]
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event['type'], 'conversation.item.create')
            self.assertEqual(event['item']['role'], 'system')
        outcome = session._journal_write_recovery['outcome']
        self.assertEqual(outcome['status'], 'saved')
        self.assertEqual(outcome['draft_status'], 'unfinished')
        self.assertNotIn('trade_number', outcome)

    async def test_completed_draft_emits_one_exact_result_and_one_continuation(self):
        harness = self.harness()
        result = {'ok': True, 'status': 'draft', 'saved': False, 'persisted': True,
                  'draft_status': 'unfinished', 'draft_id': 'b' * 32, 'revision': 1,
                  'story': {'asset': 'EXAMPLE', 'direction': None}, 'next_question': None}
        provider = AsyncMock(return_value=result)
        session = harness.session(provider=provider, authenticated=True)
        await harness.call(session, name='stage_journal_story')
        await harness.finish(session)
        await harness.finish(session)
        provider.assert_awaited_once()
        events = [call.args[0] for call in session.send_event.await_args_list]
        self.assertEqual([event['type'] for event in events],
                         ['conversation.item.create', 'response.create'])
        self.assertEqual(json.loads(events[0]['item']['output']), result)
        session.member.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
