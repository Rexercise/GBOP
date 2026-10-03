"""Synthetic receipts and transport only: no production DB, Discord, models or ASR."""
import asyncio
import base64
import contextlib
import json
import sqlite3
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import httpx

from gbop_voice_web.delivery_receipts import (
    DeliveryBinding, DeliveryOperation, deliver, delivery_status, run_delivery, _public)
from gbop_voice_web.photo_recall import send_photos
from gbop_voice_web.voice_runtime import guarded_voice_tool
from gbop_voice_web.voice_work import VoiceToolWork


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
            INSERT INTO gbop_watch_runtime VALUES ('primary','worker',999,1,'ready');
            CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT,leadership_ack INT,updated_at TEXT);
            INSERT INTO members VALUES (1,20,1,0,1,'a'),(1,30,1,0,1,'a'),(2,20,1,0,1,'a');
        ''')
        self.context = NS(owner=(1,20,'discord'), session_id='synthetic-session', generation=4,
                           _lock=threading.RLock(), _auth_revision=None, closed=False)
        self.context.current = lambda ticket: not self.context.closed and ticket == self.context.generation
        self.args = {'_delivery_binding': DeliveryBinding(self.context, 4)}
        self.channel = NS(status_code=200,json=lambda:{'id':'private'})
        self.message = NS(status_code=200,json=lambda:{'id':'accepted'})

    @contextlib.contextmanager
    def db(self):
        with self.conn:
            yield self.conn

    def photo_result(self, count=0):
        return {'ok':True,'photos':[{'id':str(n),'mime':'image/png',
            'image_base64':base64.b64encode(b'fake bytes').decode(),'trade_number':None}
            for n in range(count)],'has_more':False,'next_offset':5}

    def send(self, responses, *, count=0, args=None, on_search=None):
        client=Mock();client.post.side_effect=responses
        result=self.photo_result(count)
        with patch.dict('os.environ',{'DISCORD_TOKEN':'test'}), patch('httpx.Client') as factory, \
                patch('gbop_voice_web.trade_photos.search', side_effect=on_search, return_value=result):
            factory.return_value.__enter__.return_value=client
            value=send_photos(self.db,1,20,self.args if args is None else args)
        return value,client

    def test_empty_notice_once_and_replay_only_reads_receipt(self):
        result,client=self.send([self.channel,self.message])
        self.assertEqual((result['status'],result['sent_count'],result['notice_sent']),('no_photos',0,True))
        self.assertEqual(result['message_ids'],['accepted'])
        self.assertEqual(client.post.call_args_list[0].kwargs['json'],{'recipient_id':'20'})
        self.assertEqual(client.post.call_args_list[1].kwargs['json']['content'],'No saved photos match this request (0 photos).')
        again,client=self.send([])
        self.assertEqual(again,result);client.post.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT state FROM gbop_watch_runtime WHERE id='primary'").fetchone()[0],'ready')

    def test_journal_timeout_persists_counts_without_private_prose(self):
        from gbop_voice_web.journal_recall import send_history
        history={'ok':True,'journal_count':1,'trade_count':2,'open_trade_count':1,
                 'closed_trade_count':1,'has_more':False,'next_offset':1,
                 'journals':[{'journal_number':1,'trade_id':1,'result_r':None,
                    'summary':'synthetic private prose','rule_adherence':None,'study_note':None}]}
        client=Mock();client.post.side_effect=[self.channel,self.message,httpx.ReadTimeout('synthetic')]
        with patch.dict('os.environ',{'DISCORD_TOKEN':'test'}), patch('httpx.Client') as factory, \
                patch('gbop_voice_web.journal_recall.history',return_value=history):
            factory.return_value.__enter__.return_value=client
            result=send_history(self.db,1,20,self.args)
        self.assertEqual((result['status'],result['sent_count'],result['journal_count']),('partial',1,1))
        self.assertTrue(result['delivery_uncertain'])
        recovered=delivery_status(self.db,1,20,{'kind':'journal'})
        self.assertEqual(recovered['receipts'][0]['journal_count'],1)
        self.assertNotIn('synthetic private prose',json.dumps(recovered))
        self.assertNotIn('synthetic private prose',self.conn.execute(
            'SELECT state FROM gbop_watch_runtime WHERE id=?',(result['receipt_id'],)).fetchone()[0])

    def test_two_photos_partial_rejection_and_no_replay(self):
        result,_=self.send([self.channel,self.message,NS(status_code=403)],count=2)
        self.assertEqual((result['status'],result['sent_count'],result['delivery_uncertain']),('partial',1,False))
        again,client=self.send([],count=2)
        self.assertEqual(again,result);client.post.assert_not_called()

    def test_timeout_retains_confirmed_count_and_uncertainty(self):
        result,_=self.send([self.channel,self.message,httpx.ReadTimeout('synthetic')],count=2)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['sent_count'],1)
        self.assertTrue(result['delivery_uncertain'])
        again,client=self.send([],count=2);client.post.assert_not_called()
        self.assertTrue(again['delivery_uncertain'])

    def test_empty_notice_timeout_keeps_no_photos_but_never_claims_notice(self):
        result,_=self.send([self.channel,httpx.ReadTimeout('synthetic')])
        self.assertEqual(result['status'],'no_photos');self.assertFalse(result['notice_sent'])
        self.assertTrue(result['delivery_uncertain']);self.assertEqual(result['sent_count'],0)

    def test_receipt_recovers_after_reconnect_without_source_content(self):
        result,_=self.send([self.channel,self.message],count=1)
        self.context.closed=True
        recovered=delivery_status(self.db,1,20,{'kind':'photos','user_id':30})
        self.assertEqual(recovered['receipts'][0],result)
        serialized=json.dumps(recovered)
        for forbidden in ('fake bytes','image_base64','synthetic-session','journal prose'):
            self.assertNotIn(forbidden,serialized)
        self.assertEqual(delivery_status(self.db,1,30,{})['receipts'],[])
        self.assertEqual(delivery_status(self.db,2,20,{})['receipts'],[])

    def test_stale_binding_and_cross_member_binding_never_search_or_send(self):
        for mutate in (lambda:setattr(self.context,'generation',5),
                       lambda:setattr(self.context,'owner',(1,30,'discord')),
                       lambda:setattr(self.context,'session_id','other')):
            mutate()
            result,client=self.send([])
            self.assertFalse(result['ok']);client.post.assert_not_called()
            self.context.generation=4;self.context.owner=(1,20,'discord');self.context.session_id='synthetic-session'

    def test_forged_binding_cannot_claim_a_receipt(self):
        result,client=self.send([],args={'_delivery_binding':{'owner':[1,20]}})
        self.assertFalse(result['ok']);client.post.assert_not_called()

    def test_admitted_operation_finishes_after_barge_in_and_disconnect(self):
        def search(*args,**kwargs):
            self.context.generation=5;self.context.closed=True
            return self.photo_result()
        result,client=self.send([self.channel,self.message],on_search=search)
        self.assertEqual(result['status'],'no_photos');self.assertEqual(client.post.call_count,2)
        self.assertEqual(delivery_status(self.db,1,20,{})['receipts'][0]['status'],'no_photos')

    def test_revocation_during_lookup_stops_message_post(self):
        def search(*args,**kwargs):
            self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=1 AND user_id=20')
            return self.photo_result(1)
        result,client=self.send([self.channel],on_search=search)
        self.assertFalse(result['ok']);self.assertEqual(result['sent_count'],0)
        self.assertEqual(client.post.call_count,1)

    def test_storage_failure_blocks_send_and_does_not_initialize_schema(self):
        self.conn.execute('DROP TABLE gbop_watch_runtime')
        result,client=self.send([])
        self.assertFalse(result['ok']);client.post.assert_not_called()
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='gbop_watch_runtime'").fetchone())

    def test_crashed_claim_is_not_replayed_and_old_pending_becomes_uncertain(self):
        op=DeliveryOperation(self.db,1,20,'send_trade_photos',self.args)
        self.assertIsNone(op.claim());op.before_send()
        result,client=self.send([]);client.post.assert_not_called()
        self.assertEqual(result['status'],'pending');self.assertTrue(result['delivery_uncertain'])
        self.assertEqual(_public(op.state,now=op.state['updated_at']+121)['status'],'uncertain')

    def test_run_delivery_strips_untrusted_binding_and_binds_current_session(self):
        runner=Mock(return_value={'ok':True})
        run_delivery(self.context,'send_trade_photos',{'_delivery_binding':'forged','offset':0},runner,generation=4)
        supplied=runner.call_args.args[1]['_delivery_binding']
        self.assertIsInstance(supplied,DeliveryBinding);self.assertIs(supplied.context,self.context)

    def test_new_turn_and_reconnect_default_recover_without_duplicate(self):
        result,_=self.send([self.channel,self.message],count=1)
        for generation,session in ((5,'synthetic-session'),(1,'new-session')):
            self.context.generation=generation;self.context.session_id=session
            args={'_delivery_binding':DeliveryBinding(self.context,generation)}
            again,client=self.send([],count=1,args=args)
            self.assertEqual(again['receipt_id'],result['receipt_id']);client.post.assert_not_called()

    def test_explicit_resend_is_once_and_requires_current_text_intent(self):
        original,_=self.send([self.channel,self.message],count=1)
        for utterance in ('did you send my photos?', "don't resend them", 'did it resend?',
                "Don't resend the photos", 'Do not send them again', 'Did you send photos again?'):
            self.context._client_text=utterance
            runner=Mock()
            result=run_delivery(self.context,'send_trade_photos',{'delivery_action':'resend'},runner)
            self.assertFalse(result['ok']);runner.assert_not_called()
        self.context._client_text='Please resend my photos.'
        result=run_delivery(self.context,'send_trade_photos',{'delivery_action':'resend'},
            lambda name,args:self.send([self.channel,self.message],count=1,args=args)[0])
        self.assertEqual(result['sent_count'],1);self.assertNotEqual(result['receipt_id'],original['receipt_id'])
        result2=run_delivery(self.context,'send_trade_photos',{'delivery_action':'resend'},
            lambda name,args:self.send([],count=1,args=args)[0])
        self.assertEqual(result,result2)

    def test_old_clean_terminal_can_be_new_request_but_uncertainty_stays_blocked(self):
        original,_=self.send([self.channel,self.message],count=1)
        raw=self.conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=?',(original['receipt_id'],)).fetchone()
        state=json.loads(raw['state']);state['updated_at']-=901
        self.conn.execute('UPDATE gbop_watch_runtime SET state=? WHERE id=?',(json.dumps(state),original['receipt_id']))
        self.context.generation=5
        args={'_delivery_binding':DeliveryBinding(self.context,5)}
        again,client=self.send([self.channel,self.message],count=1,args=args)
        self.assertNotEqual(again['receipt_id'],original['receipt_id']);self.assertEqual(client.post.call_count,2)
        raw=self.conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=?',(again['receipt_id'],)).fetchone()
        state=json.loads(raw['state']);state.update(updated_at=1,delivery_uncertain=True,status='partial')
        self.conn.execute('UPDATE gbop_watch_runtime SET state=?,last_tick=9223372036854775806 WHERE id=?',(json.dumps(state),again['receipt_id']))
        self.context.generation=6
        again,client=self.send([],count=1,args={'_delivery_binding':DeliveryBinding(self.context,6)})
        client.post.assert_not_called();self.assertTrue(again['delivery_uncertain'])

    def test_equivalent_filter_spellings_recover_across_turns_and_sessions(self):
        args={**self.args,'asset':' NAS100 ','play':'  My Play ','entry_model':'Super Soup'}
        original,_=self.send([self.channel,self.message],count=1,args=args)
        self.context.generation=5;self.context.session_id='reconnected'
        args={'_delivery_binding':DeliveryBinding(self.context,5),
              'asset':'nas100','play':'my play','entry_model':'super soup'}
        again,client=self.send([],count=1,args=args)
        client.post.assert_not_called();self.assertEqual(again['receipt_id'],original['receipt_id'])
        self.context.generation=6
        args={'_delivery_binding':DeliveryBinding(self.context,6),'asset':'','play':None,'entry_model':'   '}
        empty,_=self.send([self.channel,self.message],args=args)
        self.context.generation=7
        again,client=self.send([],args={'_delivery_binding':DeliveryBinding(self.context,7)})
        client.post.assert_not_called();self.assertEqual(again['receipt_id'],empty['receipt_id'])

    def test_kind_filter_precedes_limit_so_journals_cannot_hide_photo_receipts(self):
        photo,_=self.send([self.channel,self.message],count=1)
        for n in range(25):
            state={'tool':'send_journal_history','status':'delivered','sent_count':1,'ok':True,
                   'receipt_id':'journal-'+str(n)}
            self.conn.execute('INSERT INTO gbop_watch_runtime VALUES (?,?,0,?,?)',
                ('private_delivery_v1:journal-'+str(n),'private_delivery_v1:1:20',9223372036854775700+n,json.dumps(state)))
        receipts=delivery_status(self.db,1,20,{'kind':'photos'})['receipts']
        self.assertEqual(len(receipts),1);self.assertEqual(receipts[0]['receipt_id'],photo['receipt_id'])
        self.assertEqual(len(delivery_status(self.db,1,20,{'kind':'journal'})['receipts']),10)

    def test_simultaneous_sessions_have_one_atomic_claim(self):
        import os
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        handle,path=tempfile.mkstemp(suffix='.sqlite');os.close(handle)
        self.addCleanup(lambda:os.unlink(path))
        initial=sqlite3.connect(path)
        self.conn.backup(initial);initial.close()
        advisory=threading.Lock()
        @contextlib.contextmanager
        def concurrent_db():
            conn=sqlite3.connect(path,timeout=5);conn.row_factory=sqlite3.Row
            class Adapter:
                _conn=True
                locked=False
                def execute(inner,sql,params=()):
                    if sql.startswith('SET LOCAL'):
                        return conn.execute('SELECT 1')
                    if 'pg_advisory_xact_lock' in sql:
                        advisory.acquire();inner.locked=True
                        return conn.execute('SELECT 1')
                    return conn.execute(sql,params)
            adapter=Adapter()
            try:
                with conn:yield adapter
            finally:
                if adapter.locked:advisory.release()
                conn.close()
        barrier=threading.Barrier(2);admitted=[]
        def task(number):
            context=NS(owner=(1,20,'discord'),session_id='concurrent-'+str(number),generation=1,
                _lock=threading.RLock(),_auth_revision=None,current=lambda ticket:True)
            args={'_delivery_binding':DeliveryBinding(context,1)}
            barrier.wait()
            def perform(op):
                admitted.append(number)
                op.before_send();op.accepted(self.message)
                return op.finish({'ok':True})
            return deliver(concurrent_db,1,20,'send_trade_photos',args,perform)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(task,[1,2]))
        self.assertEqual(len(admitted),1)
        self.assertEqual(results[0]['receipt_id'],results[1]['receipt_id'])
        self.assertEqual(delivery_status(concurrent_db,1,20,{})['receipts'][0]['sent_count'],1)



class InterruptedReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_member_cannot_read_prior_cached_private_result(self):
        session=NS(_voice_turn_count=1,_recovery_active=False,member=NS(id=20))
        runner=AsyncMock(return_value={'ok':True,'sent_count':1})
        await guarded_voice_tool(session,'send_trade_photos',{},'same',runner)
        session.member=NS(id=30)
        denied=await guarded_voice_tool(session,'send_trade_photos',{},'same',runner)
        self.assertFalse(denied['ok']);self.assertNotIn('sent_count',denied)
        runner.assert_awaited_once()

    async def test_shielded_admission_keeps_terminal_receipt_without_stale_audio(self):
        session=NS(_voice_turn_count=1,websocket=object(),closed=False,member=NS(id=20),_recovery_active=False)
        work=VoiceToolWork(session);session.tool_work=work
        gate=asyncio.Event();started=asyncio.Event()
        async def send():
            started.set();await gate.wait()
            return {'ok':True,'status':'no_photos','sent_count':0,'notice_sent':True,'receipt_id':'fake'}
        runner=lambda:guarded_voice_tool(session,'send_trade_photos',{},'one',send)
        waiter=asyncio.create_task(work.run_tool(work.scope(),runner))
        await started.wait();waiter.cancel();work.cancel();session.closed=True
        with self.assertRaises(asyncio.CancelledError):await waiter
        gate.set()
        for _ in range(10):await asyncio.sleep(0)
        self.assertEqual(session._delivery_receipts['fake']['status'],'no_photos')
        self.assertEqual(session._delivery_results[(1,'send_trade_photos','{"offset": 0, "unlinked_only": false}')]['status'],'no_photos')


if __name__=='__main__':unittest.main()
