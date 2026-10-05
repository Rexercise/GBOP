"""Interrupted private sends: real voice handlers, synthetic DB and no network."""
import asyncio
import contextlib
import json
import sqlite3
import threading
import time
import unittest
from types import MethodType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.delivery_receipts import deliver, delivery_status
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.member_access import member_access_error
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery
from gbop_voice_web.voice_work import VoiceToolWork
from test_voice_latency import method
from test_voice_work import Socket, settle


class CurrentDeliveryContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
            CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT,leadership_ack INT,updated_at TEXT);
            INSERT INTO members VALUES(1,20,1,0,1,'a'),(1,30,1,0,1,'a');
        ''')
        self.lock = threading.RLock()
        self.started, self.complete = threading.Event(), threading.Event()
        self.action_calls, self.status_calls, self.posts = [], [], []
        self.outcome = {'ok': True, 'status': 'delivered', 'photo_sent_count': 1, 'text_sent_count': 1}
        self.status_override = None
        self.session = NS(member=NS(id=20, send=AsyncMock()), websocket=Socket(),
            _voice_turn_count=1, closed=False, last_error=None, send_event=AsyncMock(return_value=True),
            tool_output_pending=False, _tool_response_options={}, _last_response_options={},
            recovery_tools=[], voice_client=NS(channel=NS(id=20)), _logged_audio_items=set())
        self.session.market_context = MarketConversation((1,20,'discord_voice'), auth_provider=(self.db,1,20))
        self.session.market_context.begin_turn()
        self.session.authorize_tool = lambda: asyncio.to_thread(member_access_error,self.db,1,self.session.member.id,0)
        self.session.rate_limit_recovery = VoiceRateLimitRecovery(self.session)
        self.session.tool_work = VoiceToolWork(self.session)
        execute = method('execute_tool', dict(asyncio=asyncio, json=json, time=time,
            ai_execute_tool=self.dispatch, voice_tool_payload=voice_tool_payload, GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        self.session.execute_tool = MethodType(execute, self.session)
        self.playback = NS(source=Mock(), voice_client=NS(is_playing=lambda: True, stop_playing=Mock()))
        receiver = method('receiver_loop', dict(json=json, gbop_output_manager=lambda _:self.playback))
        self.receiver = asyncio.create_task(receiver(self.session))

    @contextlib.contextmanager
    def db(self):
        with self.lock, self.conn:
            yield self.conn

    def dispatch(self, member, name, args):
        if name == 'get_delivery_status':
            self.status_calls.append((member, dict(args)))
            if self.status_override is not None:
                return self.status_override
            return delivery_status(self.db,1,member,args)
        self.action_calls.append((member,name))
        def perform(operation):
            self.operation = operation
            self.started.set()
            if not self.complete.wait(3):
                raise RuntimeError('Synthetic test did not release delivery')
            count = self.outcome.get('sent_count', self.outcome.get('text_sent_count',0)
                                     + self.outcome.get('photo_sent_count',0))
            for number in range(count):
                operation.before_send()
                self.posts.append('synthetic message ' + str(number))
                operation.accepted(NS(json=lambda:{'id':'synthetic-message-' + str(number)}))
            if self.outcome.get('delivery_uncertain'):
                operation.state['delivery_uncertain'] = True
            return operation.finish(self.outcome,self.outcome['status'])
        return deliver(self.db,1,member,name,args,perform)

    async def asyncTearDown(self):
        self.complete.set()
        operation = self.session.tool_work.operation
        self.session.tool_work.cancel()
        self.session.rate_limit_recovery.cancel()
        self.session.closed = True
        self.receiver.cancel()
        await asyncio.gather(self.receiver, return_exceptions=True)
        if operation:
            await operation
        self.conn.close()

    async def wait_until(self, check):
        async def poll():
            while not check():
                await asyncio.sleep(.001)
        await asyncio.wait_for(poll(),3)
        await settle()

    async def start_delivery(self):
        await self.session.websocket.emit({'type':'response.created','response':{'id':'old'}})
        await self.session.websocket.emit({'type':'response.output_item.done','response_id':'old',
            'item':{'type':'function_call','name':'send_journal_history','call_id':'send-1','arguments':'{}'}})
        await self.wait_until(self.started.is_set)
        await self.session.websocket.emit({'type':'response.done','response':{'id':'old','status':'completed'}})

    async def interrupt(self):
        await self.session.websocket.emit({'type':'input_audio_buffer.speech_started'})

    async def finish_delivery(self):
        operation = self.session.tool_work.operation
        self.complete.set()
        if operation:
            await operation
        task = self.session.tool_work.delivery_recovery
        if task:
            await task
        await settle()

    def context_items(self):
        return [c.args[0] for c in self.session.send_event.await_args_list
            if c.args[0].get('item',{}).get('role') == 'system']

    def recovered_receipt(self):
        text = self.context_items()[-1]['item']['content'][0]['text']
        return json.loads(text.split('Receipt JSON: ',1)[1])

    async def test_pending_interrupt_complete_current_status_context_once_without_resend(self):
        await self.start_delivery()
        self.assertEqual(delivery_status(self.db,1,20,{})['receipts'][0]['status'],'pending')
        await self.interrupt()
        self.assertEqual(self.context_items(),[])
        await self.finish_delivery()
        self.assertEqual(self.recovered_receipt()['status'],'delivered')
        self.assertEqual(self.recovered_receipt()['text_sent_count'],1)
        self.assertEqual(self.recovered_receipt()['photo_sent_count'],1)
        self.assertEqual(self.recovered_receipt()['sent_count'],2)
        self.assertEqual(self.status_calls,[(20,{'kind':'all','receipt_id':self.operation.id})])
        self.assertEqual(self.action_calls,[(20,'send_journal_history')])
        self.assertEqual(len(self.posts),2)
        self.assertEqual([c.args[0]['type'] for c in self.session.send_event.await_args_list],
                         ['conversation.item.create'])
        self.assertFalse(self.session.tool_output_pending)
        self.session.member.send.assert_not_awaited()
        # No tool is required for the next status question: receipt is already
        # in the current model context. Repeated turns cannot append it again.
        await self.interrupt()
        await self.interrupt()
        self.assertEqual(len(self.context_items()),1)
        self.assertEqual(len(self.status_calls),1)

    async def test_partial_and_uncertain_receipts_preserve_confirmed_counts(self):
        self.outcome = {'ok':False,'status':'partial','delivery_uncertain':True,
                        'text_sent_count':1,'photo_sent_count':0,'error':'Last photo may have arrived.'}
        await self.start_delivery();await self.interrupt();await self.finish_delivery()
        receipt = self.recovered_receipt()
        self.assertEqual(receipt['status'],'partial')
        self.assertFalse(receipt['ok']);self.assertTrue(receipt['delivery_uncertain'])
        self.assertEqual((receipt['text_sent_count'],receipt['photo_sent_count']),(1,0))
        self.assertEqual(len(self.posts),1)

    async def test_unavailable_receipt_is_unknown_not_delivered_or_still_waiting(self):
        self.status_override = {'ok':False,'error':'Synthetic read unavailable'}
        await self.start_delivery();await self.interrupt();await self.finish_delivery()
        receipt = self.recovered_receipt()
        self.assertEqual(receipt['status'],'unknown')
        self.assertNotIn('sent_count',receipt)
        self.assertNotIn('photo_sent_count',receipt)
        self.assertEqual(len(self.posts),2)

    async def test_pending_receipt_refreshes_to_terminal_on_next_status_turn(self):
        await self.start_delivery();await self.interrupt()
        self.status_override = {'ok':True,'receipts':[{
            'receipt_id':self.operation.id,'tool':'send_journal_history',
            'ok':False,'status':'pending','sent_count':0,'delivery_uncertain':True}]}
        await self.finish_delivery()
        self.assertEqual(self.recovered_receipt()['status'],'pending')
        await self.interrupt()
        task = self.session.tool_work.delivery_recovery
        if task:
            await task
        self.assertEqual(len(self.context_items()),1)
        self.status_override = None
        await self.interrupt()
        task = self.session.tool_work.delivery_recovery
        if task:
            await task
        self.assertEqual(self.recovered_receipt()['status'],'delivered')
        self.assertEqual(len(self.context_items()),2)
        self.assertEqual(len(self.posts),2)

    async def test_revocation_after_status_read_blocks_context_publication(self):
        await self.start_delivery();await self.interrupt()
        checks = 0
        async def authorize():
            nonlocal checks
            checks += 1
            return None if checks == 1 else 'Synthetic revocation after receipt read'
        self.session.authorize_tool = authorize
        await self.finish_delivery()
        self.assertEqual(len(self.status_calls),1)
        self.assertEqual(checks,2)
        self.assertEqual(self.context_items(),[])

    async def test_expired_receipt_context_is_not_injected_into_unrelated_later_turn(self):
        await self.start_delivery();await self.interrupt()
        self.session._delivery_recovery['started'] -= 901
        await self.finish_delivery()
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])

    async def test_context_contains_transport_facts_without_private_content(self):
        await self.start_delivery();await self.interrupt()
        self.status_override = {'ok':True,'receipts':[{
            'receipt_id':self.operation.id,'tool':'send_journal_history',
            'ok':True,'status':'delivered','sent_count':1,
            'summary':'private synthetic prose','image_base64':'private synthetic image'}]}
        await self.finish_delivery()
        serialized = json.dumps(self.context_items())
        self.assertNotIn('private synthetic',serialized)
        self.assertEqual(self.recovered_receipt()['sent_count'],1)

    async def test_wrong_receipt_cannot_supply_facts_to_current_operation(self):
        self.status_override = {'ok':True,'receipts':[{'receipt_id':'unrelated','tool':'send_journal_history',
            'ok':True,'status':'delivered','sent_count':99,'journal_numbers':[999]}]}
        await self.start_delivery();await self.interrupt();await self.finish_delivery()
        receipt = self.recovered_receipt()
        self.assertEqual(receipt['status'],'unknown')
        self.assertNotIn('sent_count', receipt)
        self.assertNotIn('journal_numbers', receipt)
        self.assertEqual(receipt['receipt_id'], self.operation.id)

    async def test_revoked_member_cannot_recover_cached_or_durable_facts(self):
        await self.start_delivery();await self.interrupt()
        with self.db() as conn:
            conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        await self.finish_delivery()
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])
        self.assertEqual(self.posts,[])

    async def test_changed_member_cannot_recover_previous_members_receipt(self):
        await self.start_delivery();await self.interrupt()
        self.session.member = NS(id=30,send=AsyncMock())
        await self.finish_delivery()
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])

    async def test_changed_conversation_identity_rejects_recovery(self):
        await self.start_delivery();await self.interrupt()
        self.session.market_context.session_id = 'replacement-session'
        await self.finish_delivery()
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])

    async def test_changed_socket_rejects_recovery_and_old_audio(self):
        old_socket = self.session.websocket
        await self.start_delivery();await self.interrupt()
        self.session.websocket = Socket()
        await self.finish_delivery()
        await old_socket.emit({'type':'response.output_audio.delta','response_id':'old','delta':'not audio'})
        self.assertFalse(self.receiver.done())
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])

    async def test_second_barge_in_cancels_blocked_context_publication(self):
        sending, release = asyncio.Event(), asyncio.Event()
        delivered = []
        async def send(event, **kwargs):
            sending.set()
            await release.wait()
            delivered.append(event)
            return True
        self.session.send_event.side_effect = send
        await self.start_delivery();await self.interrupt()
        self.complete.set()
        await asyncio.wait_for(sending.wait(),3)
        await self.interrupt()
        # The next generation may retry its own context item. Stop that new
        # attempt too: neither old blocked publication can escape cancellation.
        self.session.tool_work.cancel()
        release.set();await settle()
        self.assertEqual(delivered,[])
        self.assertEqual(len(self.posts),2)

    async def test_normal_result_already_in_context_is_not_injected_again(self):
        await self.start_delivery();await self.finish_delivery()
        await self.wait_until(lambda:self.session.tool_output_pending or self.session.send_event.await_count>=2)
        await self.interrupt()
        self.assertEqual(self.context_items(),[])
        self.assertEqual(self.status_calls,[])
        self.assertEqual(len(self.posts),2)


if __name__ == '__main__':
    unittest.main()
