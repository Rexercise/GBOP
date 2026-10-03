"""Synthetic transport receipts: generation is not evidence of delivery."""
import ast
import asyncio
import itertools
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.response_delivery import MarketResponseDelivery


class ResponseDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.context = NS(generation=4, closed=False, complete_response=Mock(return_value=2))
        self.context.current = lambda g: not self.context.closed and self.context.generation == g
        self.updated = Mock()
        self.delivery = MarketResponseDelivery(self.context, self.updated)

    def events(self):
        return [lambda: self.delivery.transcript('item', '9ate8 failed. Young Lefty failed.', 'response'),
                lambda: self.delivery.playback_done('item', completed=True),
                lambda: self.delivery.response_done({'id': 'response', 'status': 'completed'})]

    def test_all_three_receipts_required_in_any_order(self):
        for order in itertools.permutations(range(3)):
            self.setUp()
            self.delivery.started('item', 'response')
            events = self.events()
            for i in order[:2]:
                events[i]()
            self.context.complete_response.assert_not_called()
            events[order[-1]]()
            self.context.complete_response.assert_called_once_with(
                '9ate8 failed. Young Lefty failed.', generation=4, response_id='item', completed=True)
            for event in events:
                event()
            self.context.complete_response.assert_called_once()
            self.updated.assert_called_once_with(4)

    def test_transcript_only_or_tool_only_never_marks_discussion(self):
        self.delivery.transcript('item', '9ate8 failed.', 'response')
        self.delivery.response_done({'id': 'response', 'status': 'completed'})
        self.context.complete_response.assert_not_called()

    def test_cancel_failure_incomplete_and_playback_abort_never_mark(self):
        for status in ('cancelled', 'failed', 'incomplete'):
            self.setUp()
            self.delivery.started('item', 'response')
            self.delivery.transcript('item', '9ate8 failed.', 'response')
            self.delivery.playback_done('item', completed=True)
            self.delivery.response_done({'id': 'response', 'status': status})
            self.context.complete_response.assert_not_called()
        self.setUp()
        self.delivery.started('item', 'response')
        self.delivery.playback_done('item', completed=False)
        for event in self.events():
            event()
        self.context.complete_response.assert_not_called()

    def test_barge_in_closed_and_superseded_generation_are_fenced(self):
        for action in ('cancel', 'closed', 'generation'):
            self.setUp()
            self.delivery.started('item', 'response')
            if action == 'cancel':
                self.delivery.cancel()
            else:
                setattr(self.context, action, True if action == 'closed' else 5)
            for event in self.events():
                event()
            self.context.complete_response.assert_not_called()

    def test_response_identity_cannot_be_reassigned(self):
        self.delivery.started('item', 'older')
        for event in self.events():
            event()
        self.context.complete_response.assert_not_called()

    def test_member_contexts_never_share_receipts(self):
        other = NS(generation=4, current=lambda g: True, complete_response=Mock(return_value=1))
        other_delivery = MarketResponseDelivery(other)
        self.delivery.started('item', 'response')
        for event in self.events():
            event()
        other_delivery.response_done({'id': 'response', 'status': 'completed'})
        other.complete_response.assert_not_called()

    def test_bounded_receipt_storage(self):
        for i in range(150):
            self.delivery.started(str(i), str(i))
            self.delivery.response_done({'id': str(i), 'status': 'completed'})
        self.assertEqual(len(self.delivery.items), 64)
        self.assertEqual(len(self.delivery.responses), 64)


class PlaybackDrainTests(unittest.TestCase):
    def test_database_connection_attempt_has_a_deadline(self):
        path = Path(__file__).resolve().parents[1] / 'db_compat.py'
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'ConnectionCompat')
        init = next(n for n in node.body if getattr(n, 'name', '') == '__init__')
        connect = Mock()
        namespace = {'psycopg': NS(connect=connect), 'SUPABASE_DB_URL': 'synthetic-dsn'}
        exec(compile(ast.Module(body=[init], type_ignores=[]), str(path), 'exec'), namespace)
        namespace['__init__'](NS())
        self.assertEqual(connect.call_args.kwargs['connect_timeout'], 10)
        self.assertEqual(connect.call_args.kwargs['sslmode'], 'require')
        self.assertIsNone(connect.call_args.kwargs['prepare_threshold'])

    def source(self):
        path = Path(__file__).resolve().parents[1] / 'bot.py'
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'GBOPRealtimeAudioSource')
        ns = dict(discord=NS(AudioSource=object), threading=threading,
                  gbop_pcm24_mono_to_pcm48_stereo=lambda x: x)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        return ns['GBOPRealtimeAudioSource']()

    def test_completion_requires_actual_drain_not_generated_finish(self):
        source = self.source()
        source.feed(b'a' * 100)
        source.finish()
        self.assertFalse(source.drained)
        self.assertEqual(len(source.read()), source.FRAME_BYTES)
        self.assertFalse(source.drained)
        self.assertEqual(source.read(), b'')
        self.assertTrue(source.drained)
        source.cleanup()  # Discord may clean up before its completion callback.
        self.assertTrue(source.drained)

    def test_abort_is_not_a_completed_playback(self):
        source = self.source()
        source.feed(b'a' * 100)
        source.finish()
        source.abort()
        self.assertEqual(source.read(), b'')
        self.assertFalse(source.drained)


class BrowserDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_range_reply_has_bounded_expanded_audio_budget(self):
        import json
        import time
        from test_voice_latency import method
        from gbop_voice_web.voice_payload import voice_tool_payload
        from unittest.mock import Mock
        result = {'ok': True, 'asset': 'NAS100', 'review': {'other_range_followup': {
            'spoken_summary': 'The 9 AM range delivered. The 10 AM independent range failed. '
                'The 11 AM range closes at the cutoff.', 'ranges': []}}}
        execute = method('execute_tool', dict(asyncio=asyncio, json=json, time=time,
            ai_execute_tool=Mock(return_value=result), voice_tool_payload=voice_tool_payload,
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        session = NS(member=NS(id=42), send_event=AsyncMock(return_value=True),
                     _tool_response_options={})
        await execute(session, {'name': 'review_other_market_ranges', 'call_id': 'ranges',
                               'arguments': '{}'})
        self.assertEqual(session._tool_response_options, {'max_output_tokens': 2200})

    async def test_authenticated_exact_session_and_turn_required(self):
        from test_market_conversation import function
        context = NS(closed=False, client_turn=7, generation=9,
                     complete_response=Mock(return_value=2))
        state = {'user_id': 42, 'live_session_id': 'current', 'market_context': context}
        fn = function('delivered_live_context', dict(
            LiveDeliveryRequest=object, require_authenticated_user=AsyncMock(return_value=state),
            HTTPException=RuntimeError))
        for sid, turn in [('other', 7), ('current', 6), ('current', 8)]:
            result = await fn(None, NS(session_id=sid, turn_id=turn, response_id='r', text='9ate8'))
            self.assertFalse(result['ok'])
        context.complete_response.assert_not_called()
        result = await fn(None, NS(session_id='current', turn_id=7, response_id='r', text='9ate8'))
        self.assertEqual(result, {'ok': True, 'recorded': 2})
        context.complete_response.assert_called_once_with('9ate8', generation=9,
            response_id='r', completed=True)

    async def test_unauthorized_receipt_cannot_touch_context(self):
        from test_market_conversation import function
        fn = function('delivered_live_context', dict(
            LiveDeliveryRequest=object, require_authenticated_user=AsyncMock(side_effect=PermissionError),
            HTTPException=RuntimeError))
        with self.assertRaises(PermissionError):
            await fn(None, NS(session_id='current', turn_id=7, response_id='r', text='9ate8'))
