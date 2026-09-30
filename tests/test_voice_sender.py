import ast
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import unittest


class VoiceSenderTests(unittest.IsolatedAsyncioTestCase):
    async def run_sender(self, resume=False, fail=False):
        source = Path(__file__).resolve().parents[1] / 'bot.py'
        cls = next(n for n in ast.parse(source.read_text()).body
                   if isinstance(n, ast.ClassDef) and n.name == 'GBOPRealtimeSession')
        method = next(n for n in cls.body if getattr(n, 'name', '') == 'sender_loop')
        sent = []
        calls = 0
        waits = 0
        session = SimpleNamespace(closed=False)

        queue = asyncio.Queue()
        queue.put_nowait(b'first speech')
        real_get = queue.get
        async def get():
            nonlocal calls
            calls += 1
            return await real_get()

        async def wait(tasks, timeout):
            nonlocal waits
            self.assertEqual(timeout, 0.1)
            waits += 1
            if resume and waits == 2:
                queue.put_nowait(b'resumed speech')
                await asyncio.sleep(0)
                return set(tasks), set()
            return set(), set(tasks)

        async def send_event(event, quiet=False):
            self.assertEqual(event['type'], 'input_audio_buffer.append')
            sent.append(base64.b64decode(event['audio']))
            if len(sent) == (123 if resume else 121):
                session.closed = True
            await asyncio.sleep(0)
            return not fail

        session.audio_queue = SimpleNamespace(get=get)
        session.send_event = send_event
        ns = dict(base64=base64, asyncio=SimpleNamespace(
            create_task=asyncio.create_task, wait=wait, gather=asyncio.gather))
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
        if fail:
            with self.assertRaisesRegex(RuntimeError, 'audio send failed'):
                await ns['sender_loop'](session)
        else:
            await ns['sender_loop'](session)
            self.assertFalse(queue._getters, 'pending queue task must be cancelled on exit')
        return sent, calls

    async def test_silence_finishes_turn_and_is_bounded(self):
        sent, calls = await self.run_sender()
        self.assertEqual(sent[0], b'first speech')
        self.assertEqual(sent[1:], [bytes(4800)] * 120)
        self.assertEqual(calls, 2)

    async def test_new_speech_passes_through_and_resets_tail(self):
        sent, _ = await self.run_sender(resume=True)
        self.assertEqual(sent[:3], [b'first speech', bytes(4800), b'resumed speech'])
        self.assertEqual(sent[3:], [bytes(4800)] * 120)

    async def test_transport_failure_propagates(self):
        sent, _ = await self.run_sender(fail=True)
        self.assertEqual(sent, [b'first speech'])


if __name__ == '__main__':
    unittest.main()
