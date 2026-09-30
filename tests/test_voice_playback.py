import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class Source:
    played_ms = 80
    def abort(self):
        self.aborted = True


class VoiceClient:
    def is_playing(self):
        return True
    def stop_playing(self):
        self.stopped = True
    def play(self, source, after):
        self.source = source


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        source = Path(__file__).resolve().parents[1] / 'bot.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'GBOPOutputManager')
        ns = dict(asyncio=asyncio, GBOPRealtimeAudioSource=Source)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
        manager = ns['GBOPOutputManager'](1)
        manager.source = Source()
        manager.session = SimpleNamespace(send_event=AsyncMock())
        manager.voice_client = VoiceClient()
        manager.item_id = 'old-item'
        return manager

    async def test_followup_does_not_cancel_new_response(self):
        manager = self.manager()
        session = manager.session
        await manager.begin(session, manager.voice_client, 'new-item')
        session.send_event.assert_not_awaited()
        self.assertEqual(manager.item_id, 'new-item')

    async def test_explicit_interrupt_still_cancels(self):
        manager = self.manager()
        session = manager.session
        await manager.interrupt()
        self.assertEqual(session.send_event.await_args_list[0].args[0],
                         {'type': 'response.cancel'})

    async def test_other_member_cancels_previous_session_only(self):
        manager = self.manager()
        old = manager.session
        new = SimpleNamespace(send_event=AsyncMock())
        await manager.begin(new, manager.voice_client, 'other-item')
        old.send_event.assert_awaited()
        new.send_event.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
