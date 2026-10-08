"""Member-context admission never blocks barge-in or exposes unauthorised audio."""
import asyncio
import base64
import threading
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.market_conversation import MarketConversation
from test_voice_latency import method


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


class MemberContinuityAudioTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        context = MarketConversation((10, 20, 'voice'))
        context.begin_turn()
        source = NS(feed=Mock(), finish=Mock(), abort=Mock())
        manager = NS(begin=AsyncMock(return_value=source))
        preparation = asyncio.get_running_loop().create_future()
        session = NS(closed=False, websocket=object(), market_context=context,
            _continuity_audio_queue=asyncio.Queue(maxsize=128), _continuity_audio_task=None,
            _continuity_turn_task=preparation, _logged_audio_items=set(),
            output_source=None, output_item_id=None, market_delivery=NS(started=Mock()),
            voice_client=NS(channel=NS(id=40)), last_error=None)
        def stop():
            session.closed = True
            context.close()
            if session._continuity_audio_task:
                session._continuity_audio_task.cancel()
        session.stop = Mock(side_effect=stop)
        namespace = dict(asyncio=asyncio, base64=base64, gbop_output_manager=lambda _: manager)
        session.authorized_output_loop = MethodType(method('authorized_output_loop', namespace), session)
        session.enqueue_authorized_output = MethodType(method('enqueue_authorized_output', namespace), session)
        async def cleanup():
            stop()
            if session._continuity_audio_task:
                await asyncio.gather(session._continuity_audio_task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        return session, preparation, source

    def audio(self, session):
        session.enqueue_authorized_output({'type': 'response.output_audio.delta',
            'item_id': 'item1', 'response_id': 'r1', 'delta': base64.b64encode(b'pcm').decode()})
        session.enqueue_authorized_output({'type': 'response.output_audio.done', 'item_id': 'item1'})

    async def test_audio_waits_for_auth_refresh_without_blocking_event_loop_and_drains_in_order(self):
        session, preparation, source = self.session()
        self.audio(session)
        await settle()
        source.feed.assert_not_called()
        self.assertFalse(session._continuity_audio_task.done())
        preparation.set_result(None)
        await settle()
        source.feed.assert_called_once_with(b'pcm')
        source.finish.assert_called_once()
        session.market_delivery.started.assert_called_once_with('item1', 'r1')

    async def test_barge_in_during_slow_context_refresh_discards_old_audio(self):
        session, preparation, source = self.session()
        self.audio(session)
        await settle()
        session.market_context.begin_turn('New member request')
        preparation.set_result(None)
        await settle()
        source.feed.assert_not_called()
        source.finish.assert_not_called()

    async def test_barge_in_does_not_wait_for_old_preparation_before_new_audio(self):
        session, old_preparation, source = self.session()
        self.audio(session)
        await settle()
        old_worker = session._continuity_audio_task
        reset = method('reset_authorized_output', dict(asyncio=asyncio))
        session.market_context.begin_turn('New request')
        reset(session)
        fresh = asyncio.get_running_loop().create_future()
        fresh.set_result(None)
        session._continuity_turn_task = fresh
        self.audio(session)
        await settle()
        await asyncio.gather(old_worker, return_exceptions=True)
        self.assertTrue(old_preparation.cancelled())
        source.feed.assert_called_once_with(b'pcm')
        source.finish.assert_called_once()

    async def test_replaced_socket_cannot_play_queued_audio(self):
        session, preparation, source = self.session()
        self.audio(session)
        session.websocket = object()
        preparation.set_result(None)
        await settle()
        source.feed.assert_not_called()

    async def test_denied_member_stops_session_before_buffered_audio_can_play(self):
        session, preparation, source = self.session()
        self.audio(session)
        session.authorize_member_tool = AsyncMock(return_value='Revoked')
        session.refresh_context = AsyncMock()
        prepare = method('prepare_continuity_turn', dict(asyncio=asyncio))
        await prepare(session, session.market_context.generation)
        preparation.set_result(None)
        await settle()
        session.stop.assert_called_once()
        source.feed.assert_not_called()
        session.refresh_context.assert_not_awaited()

    async def test_storage_failure_stops_voice_instead_of_using_old_context(self):
        session, preparation, source = self.session()
        session.authorize_member_tool = AsyncMock(return_value=None)
        session.refresh_context = AsyncMock()
        prepare = method('prepare_continuity_turn', dict(asyncio=asyncio))
        with patch('gbop_voice_web.member_continuity.start_turn', side_effect=ValueError('Unavailable')):
            await prepare(session, session.market_context.generation)
        session.stop.assert_called_once()
        self.assertTrue(session.closed)
        session.refresh_context.assert_not_awaited()

    async def test_post_playback_database_work_does_not_block_event_loop(self):
        started, release = threading.Event(), threading.Event()
        def commit(*args, **kwargs):
            started.set()
            if not release.wait(2):
                raise RuntimeError('Synthetic blocked DB did not release')
            return 0
        session = NS(closed=False, market_context=NS(admit_response=Mock(return_value=object()), persist_response=commit),
                     _continuity_delivery_tasks=set(), _market_response_delivered=Mock())
        submit = method('_submit_completed_response', dict(asyncio=asyncio))
        submit(session, 'Confirmed playback', 1, 'response1')
        tasks = list(session._continuity_delivery_tasks)
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set())
            await asyncio.wait_for(asyncio.sleep(0), .2)
            self.assertFalse(tasks[0].done())
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertFalse(session._continuity_delivery_tasks)


if __name__ == '__main__':
    unittest.main()
