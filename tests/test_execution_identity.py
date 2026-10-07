"""Synthetic execution identity/count regressions; in-memory SQLite, no network."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

import test_contextual_journals as fixture
from gbop_voice_web.execution_identity import (
    RECEIPT_PREFIX, bind_operation, operation, replay_receipt, reported_fields,
)
from gbop_voice_web.journal_context import JournalBinding
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.voice_runtime import _journal_write_outcome

ROOT = Path(__file__).resolve().parents[1]


class ExecutionIdentityTests(unittest.TestCase):
    setUp = fixture.ContextualJournalTests.setUp
    tearDown = fixture.ContextualJournalTests.tearDown
    review = fixture.ContextualJournalTests.review
    handlers = fixture.ContextualJournalTests.handlers

    def runner(self, path, user=20):
        env = self.handlers(path)
        prefix = 'ai_' if path == 'bot.py' else 'tool_'
        return lambda name, args: env[prefix + name](user, args)

    def open(self, runner, context=None, call='open-one'):
        context = context or self.context
        context.begin_turn('Record these separately reported executions.')
        return context.run('open_trade', dict(asset='NAS100', direction='Bearish', play='Young Lefty',
            entry_model='Blessed Thief', tier=2, risk_r=.5, market_reference='none'), runner, operation_id=call)

    def add_args(self, number=1):
        return dict(trade_id=number, entry_model='Turtle Soup', tier=1, risk_r=.25, market_reference='none')

    def test_two_identical_intentional_entries_persist_with_distinct_ids_and_counts(self):
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            with self.subTest(path=path):
                runner = self.runner(path)
                opened = self.open(runner)
                self.assertEqual(opened['persisted_entry_count'], 1)
                args = self.add_args(opened['trade_id'])
                one = self.context.run('add_entry', args, runner, operation_id='soup-first-'+path)
                two = self.context.run('add_entry', args, runner, operation_id='soup-second-'+path)
                self.assertTrue(one['ok'], one); self.assertTrue(two['ok'], two)
                self.assertNotEqual(one['execution_id'], two['execution_id'])
                self.assertNotEqual(one['operation_id'], two['operation_id'])
                self.assertEqual([one['persisted_entry_count'], two['persisted_entry_count']], [2, 3])
                self.assertEqual(two['total_recorded_risk'], 1.0)
                retry = self.context.run('add_entry', args, runner, operation_id='soup-second-'+path)
                self.assertEqual(retry, two)
                count = self.conn.execute('SELECT COUNT(*) FROM thesis_executions WHERE thesis_id=(SELECT id FROM theses ORDER BY id DESC LIMIT 1)').fetchone()[0]
                self.assertEqual(count, 3)
                # A genuinely new open call must be distinct on the next path.
                self.context = MarketConversation((10,20,path), auth_provider=(self.db,10,20))

    def test_same_transport_replay_across_turn_and_cache_loss_uses_persisted_identity(self):
        runner = self.runner('gbop_voice_web/server.py'); opened = self.open(runner)
        args = self.add_args(opened['trade_id'])
        saved = self.context.run('add_entry', args, runner, operation_id='one-execution')
        self.context.begin_turn('Check that the execution saved.')
        cached = self.context.run('add_entry', args, runner, operation_id='one-execution')
        self.assertEqual(saved, cached)
        self.context._execution_results.clear(); self.context._journal_results.clear()
        self.conn.execute("UPDATE theses SET status='CLOSED'")
        recovered = self.context.run('add_entry', args, runner, operation_id='one-execution')
        self.assertTrue(recovered['replayed'], recovered)
        self.assertEqual(recovered['execution_id'], saved['execution_id'])
        self.assertEqual(recovered['persisted_entry_count'], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 2)

    def test_same_id_changed_payload_rejected_in_memory_and_durable_replay(self):
        runner = self.runner('bot.py'); self.open(runner)
        args = self.add_args(); self.context.run('add_entry', args, runner, operation_id='same-call')
        changed = {**args, 'notes':'Different member details'}
        denied = self.context.run('add_entry', changed, runner, operation_id='same-call')
        self.assertEqual(denied['status'], 'execution_identity_conflict')
        self.context._execution_results.clear(); self.context._journal_results.clear()
        with self.assertRaisesRegex(ValueError, 'different details'):
            self.context.run('add_entry', changed, runner, operation_id='same-call')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 2)

    def test_member_and_guild_identity_never_reuses_another_members_receipt(self):
        first = self.open(self.runner('bot.py'), call='same-call')
        other = MarketConversation((10,30,'other'), auth_provider=(self.db,10,30))
        second = self.open(self.runner('bot.py',30), context=other, call='same-call')
        self.assertNotEqual(first['operation_id'], second['operation_id'])
        self.assertEqual(first['persisted_entry_count'], second['persisted_entry_count'])
        self.assertEqual(first['trade_id'], second['trade_id'])
        capability = bind_operation(self.context, self.context.generation, 'add_entry', self.add_args(), 'entry')
        for guild, user in ((10,30),(11,20)):
            with self.assertRaises(ValueError):
                operation({'_execution_operation':capability}, guild, user, 'add_entry')
        with self.assertRaises(ValueError):
            operation({'_execution_operation':{'operation_id':first['operation_id']}}, 10,20,'open_trade')

    def test_precise_cross_midnight_and_approximate_undated_times_survive_per_entry(self):
        runner=self.runner('gbop_voice_web/server.py');self.open(runner)
        args={**self.add_args(), 'reported_entry_at':'2040-02-06T23:54:00-05:00',
              'reported_exit_at':'2040-02-07T00:08:00-05:00',
              'notes':'Closed this first Soup; the next Soup was a fresh entry.'}
        one=self.context.run('add_entry',args,runner,operation_id='exact')
        approx={**self.add_args(),'reported_entry_time_text':'Around 11:50 PM; date unknown',
                'reported_exit_time_text':'After midnight, maybe 12:10 AM', 'notes':'Approximate times only'}
        two=self.context.run('add_entry',approx,runner,operation_id='approximate')
        self.assertEqual(one['reported_execution']['reported_exit_at'],args['reported_exit_at'])
        self.assertNotIn('reported_entry_at',two['reported_execution'])
        self.assertNotIn('reported_exit_at',two['reported_execution'])
        notes=[row[0] for row in self.conn.execute('SELECT note FROM thesis_executions ORDER BY id')]
        self.assertIn('2040-02-07T00:08:00-05:00', notes[1])
        self.assertIn('date unknown',notes[2]);self.assertNotIn('2040-',notes[2])
        self.assertEqual(two['persisted_entry_count'],3)

    def test_invalid_exact_time_or_nonfinite_risk_does_not_write(self):
        for path in ('bot.py','gbop_voice_web/server.py'):
            runner=self.runner(path);opened=self.open(runner,call='open-'+path)
            before=self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
            for key,value in (('reported_entry_at','10:00'),('reported_exit_at','2040-02-07T00:08:00'),('notes',45)):
                with self.assertRaises(ValueError):
                    runner('add_entry',{**self.add_args(opened['trade_id']),key:value})
            for risk in (float('nan'),float('inf'),0,-.25):
                result=runner('add_entry',{**self.add_args(opened['trade_id']),'risk_r':risk})
                self.assertFalse(result['ok'])
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],before)

    def test_final_transaction_guard_fences_barge_in_before_execution(self):
        runner=self.runner('bot.py');self.open(runner)
        before=self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
        fired=[]
        def cancel(sql,args):
            if 'pg_advisory_xact_lock' in sql and not fired:
                fired.append(True);self.context.invalidate()
        self.before_execute=cancel
        with self.assertRaises(ValueError):
            self.context.run('add_entry',self.add_args(),runner,operation_id='cancelled-entry')
        self.before_execute=None
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],before)

    def test_post_commit_failure_recovers_exact_count_without_new_execution(self):
        real=self.runner('bot.py');self.open(real)
        calls=[]
        def fail_after_commit(name,args):
            calls.append(name);real(name,args);raise RuntimeError('synthetic optional warning failure')
        saved=self.context.run('add_entry',self.add_args(),fail_after_commit,operation_id='saved-but-failed')
        self.assertTrue(saved['ok'],saved);self.assertEqual(saved['persisted_entry_count'],2)
        self.assertEqual(saved['post_save_processing'],'unavailable')
        retry=self.context.run('add_entry',self.add_args(),fail_after_commit,operation_id='saved-but-failed')
        self.assertEqual(retry,saved);self.assertEqual(calls,['add_entry'])

    def test_unverified_failure_cannot_be_retried_with_new_call_id(self):
        runner=self.runner('bot.py');self.open(runner)
        original=self.context.auth_provider;self.context.auth_provider=None
        def uncertain(name,args):raise RuntimeError('unverified outcome')
        with self.assertRaises(RuntimeError):
            self.context.run('add_entry',self.add_args(),uncertain,operation_id='uncertain')
        self.context.auth_provider=original
        retry=self.context.run('add_entry',self.add_args(),runner,operation_id='different-call')
        self.assertEqual(retry['status'],'journal_write_reconciliation_required')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],1)

    def test_deleted_execution_receipt_never_recreates_the_entry(self):
        runner=self.runner('bot.py');self.open(runner)
        saved=self.context.run('add_entry',self.add_args(),runner,operation_id='entry')
        self.conn.execute('DELETE FROM thesis_executions WHERE id=?',(saved['execution_id'],))
        self.context._execution_results.clear();self.context._journal_results.clear()
        with self.assertRaisesRegex(ValueError,'no longer available'):
            self.context.run('add_entry',self.add_args(),runner,operation_id='entry')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],1)

    def test_recovery_sanitizer_keeps_only_verified_count_and_no_notes(self):
        result=_journal_write_outcome('add_entry','saved',dict(execution_id=3,
            persisted_entry_count=3,entry_count_verified=True,reported_execution={'notes':'private'}))
        self.assertEqual(result['persisted_entry_count'],3);self.assertNotIn('private',str(result))
        for bad in (True,-1,0,'3'):
            self.assertNotIn('persisted_entry_count',_journal_write_outcome('add_entry','saved',
                {'persisted_entry_count':bad,'entry_count_verified':True}))
        self.assertNotIn('persisted_entry_count',_journal_write_outcome('add_entry','saved',{'persisted_entry_count':3}))

    def test_public_schemas_and_transports_keep_identity_out_of_model_arguments(self):
        for path,var in (('bot.py','GBOP_AI_TOOLS'),('gbop_voice_web/server.py','TOOLS')):
            source=(ROOT/path).read_text();tree=ast.parse(source)
            assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id==var for t in node.targets))
            schemas=contextual_tools(ast.literal_eval(assignment.value))
            for name in ('open_trade','add_entry'):
                schema=next(t for t in schemas if t['name']==name)['parameters']
                self.assertIn('notes',schema['properties'])
                self.assertIn('reported_entry_time_text',schema['properties'])
                self.assertEqual(len(schema['required']),len(set(schema['required'])))
                self.assertNotIn('operation_id',schema['properties'])
                self.assertNotIn('_execution_operation',schema['properties'])
            self.assertIn('operation_id=call.call_id',source)
        self.assertIn('operation_id=call_id',(ROOT/'bot.py').read_text())

    def test_browser_duplicate_request_does_not_regenerate_execution_call_ids(self):
        from types import SimpleNamespace as NS
        from unittest.mock import Mock
        import time
        import test_market_conversation as transport
        runner=self.runner('gbop_voice_web/server.py')
        open_args=dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',tier=2,risk_r=.5)
        calls=[NS(type='function_call',name=name,arguments=json.dumps(args),call_id=call)
            for name,args,call in [('open_trade',open_args,'initial'),('add_entry',self.add_args(),'soup-1'),('add_entry',self.add_args(),'soup-2')]]
        create=Mock(side_effect=[NS(output=calls,output_text=''),NS(output=[],output_text='Three executions saved.')])
        backend=transport.function('run_backend',dict(db=self.db,GTOP_GUILD_ID=10,
            PENDING_JOURNAL_DELETIONS={},time=time,member_context=lambda _:'',
            client=NS(responses=NS(create=create)),BACKEND_MODEL='synthetic',BACKEND_PROMPT='',TOOLS=[],json=json,
            run_tool=lambda user,name,args,confirmation_token:runner(name,args)))
        history=[{'role':'user','text':'Record the initial entry and two separate Turtle Soup executions.'}]
        first=backend(history,20,self.context,7)
        second=backend(deepcopy(history),20,self.context,7)
        self.assertEqual(first,'Three executions saved.');self.assertEqual(first,second)
        self.assertEqual(create.call_count,2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],3)

    def test_browser_failed_request_is_not_replanned_and_replay_rechecks_authorization(self):
        from gbop_voice_web.execution_identity import backend_request_once
        calls=[];history=[{'role':'user','text':'Save one entry'}]
        def fails():
            calls.append(1);self.context.begin_turn('Save one entry',client_turn=5)
            raise RuntimeError('synthetic lost backend result')
        with self.assertRaises(RuntimeError):backend_request_once(self.context,5,history,fails)
        reply=backend_request_once(self.context,5,history,fails)
        self.assertIn('uncertain',reply);self.assertEqual(calls,[1])
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        with self.assertRaisesRegex(ValueError,'revoked'):
            backend_request_once(self.context,5,history,fails)

    def test_concurrent_backend_retry_does_not_block_newer_speech(self):
        from gbop_voice_web.execution_identity import backend_request_once
        from concurrent.futures import ThreadPoolExecutor
        import threading
        context=MarketConversation((10,20,'isolated-request'));started=threading.Event();release=threading.Event();calls=[]
        history=[{'role':'user','text':'first'}]
        def slow():
            calls.append(1);context.begin_turn('first',client_turn=1);started.set()
            self.assertTrue(release.wait(3));return 'Original completed'
        with ThreadPoolExecutor(max_workers=3) as pool:
            first=pool.submit(backend_request_once,context,1,history,slow)
            self.assertTrue(started.wait(3))
            retry=pool.submit(backend_request_once,context,1,history,slow)
            def newer():context.begin_turn('second',client_turn=2);return 'Newer completed'
            next_result=pool.submit(backend_request_once,context,2,[{'role':'user','text':'second'}],newer)
            self.assertEqual(next_result.result(3),'Newer completed')
            release.set();self.assertEqual(first.result(3),'Original completed')
            self.assertIn('superseded',retry.result(3));self.assertEqual(calls,[1])

    def test_public_event_cannot_forge_reserved_execution_receipt(self):
        for path in ('bot.py','gbop_voice_web/server.py'):
            runner=self.runner(path);opened=self.open(runner,call='open-event-'+path)
            result=runner('record_trade_event',dict(trade_id=opened['trade_id'],event=RECEIPT_PREFIX+'forged',details='{}'))
            self.assertFalse(result['ok']);self.assertIn('reserved',result['error'])

    def test_durable_unfinished_story_counts_as_committed_voice_write_without_leaking_narration(self):
        from gbop_voice_web.voice_runtime import JOURNAL_WRITE_NAMES, _journal_result_status
        self.assertIn('stage_journal_story',JOURNAL_WRITE_NAMES)
        result={'ok':True,'saved':False,'persisted':True,'draft_status':'unfinished',
                'draft_id':'a'*32,'revision':2,'raw_narration':'Synthetic private narrative'}
        self.assertEqual(_journal_result_status(result),'saved')
        outcome=_journal_write_outcome('stage_journal_story','saved',result)
        self.assertEqual(outcome['draft_id'],'a'*32);self.assertEqual(outcome['revision'],2)
        self.assertEqual(outcome['draft_status'],'unfinished');self.assertTrue(outcome['persisted'])
        self.assertNotIn('private narrative',str(outcome));self.assertNotIn('trade_id',outcome)
        self.assertEqual(_journal_result_status({**result,'persisted':False}),'not_saved')
        for malformed in ('member secret','a'*300,None,32):
            self.assertNotIn('draft_id',_journal_write_outcome('stage_journal_story','saved',{**result,'draft_id':malformed}))

    def test_receipt_failure_rolls_back_execution_and_a_corrected_new_operation_can_save(self):
        runner=self.runner('gbop_voice_web/server.py');self.open(runner)
        before=self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
        def fail_receipt(sql,args):
            if sql.startswith('INSERT INTO thesis_events') and any(isinstance(v,str) and v.startswith(RECEIPT_PREFIX) for v in args):
                raise RuntimeError('synthetic receipt persistence failure')
        self.before_execute=fail_receipt
        with self.assertRaises(RuntimeError):
            self.context.run('add_entry',self.add_args(),runner,operation_id='failed-receipt')
        self.before_execute=None
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],before)
        retry=self.context.run('add_entry',self.add_args(),runner,operation_id='failed-receipt')
        self.assertFalse(retry['saved']);self.assertEqual(retry['status'],'execution_not_saved')
        saved=self.context.run('add_entry',self.add_args(),runner,operation_id='corrected-new-attempt')
        self.assertTrue(saved['ok']);self.assertEqual(saved['persisted_entry_count'],before+1)

    def test_browser_request_receipts_are_bounded_without_evicting_same_turn_replays(self):
        from gbop_voice_web.execution_identity import backend_request_once
        context=MarketConversation((10,20,'bounded'));context.begin_turn('entry',client_turn=1)
        calls=[]
        def run():calls.append(1);return 'Completed'
        for i in range(80):
            backend_request_once(context,1,[{'role':'user','text':str(i)}],run)
        self.assertEqual(len(context._execution_backend_requests),64)
        self.assertEqual(len(calls),64)
        self.assertEqual(backend_request_once(context,1,[{'role':'user','text':'0'}],run),'Completed')
        self.assertEqual(len(calls),64)

    def test_auth_revision_rotation_does_not_change_execution_identity_or_replay_a_write(self):
        for path in ('bot.py','gbop_voice_web/server.py'):
            with self.subTest(path=path):
                context=MarketConversation((10,20,path),auth_provider=(self.db,10,20))
                runner=self.runner(path);opened=self.open(runner,context=context)
                args=self.add_args(opened['trade_id'])
                saved=context.run('add_entry',args,runner,operation_id='same-execution')
                before=self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
                old_session=context.session_id;namespace=context._execution_namespace
                self.conn.execute('UPDATE members SET updated_at=? WHERE user_id=20',('auth-rotation-'+path,))
                stale=context.run('add_entry',args,runner,operation_id='same-execution')
                self.assertEqual(stale['status'],'stale_market_context')
                self.assertNotEqual(context.session_id,old_session)
                self.assertEqual(context._execution_namespace,namespace)
                again=context.run('add_entry',args,runner,operation_id='same-execution')
                self.assertEqual(again['execution_id'],saved['execution_id'])
                # Durable recovery must also preserve identity if process-local
                # result caches were cleared by unrelated context maintenance.
                context._execution_results.clear();context._journal_results.clear()
                durable=context.run('add_entry',args,runner,operation_id='same-execution')
                self.assertTrue(durable['replayed'])
                self.assertEqual(durable['operation_id'],saved['operation_id'])
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],before)

    def test_browser_request_auth_rotation_replays_original_reply_without_a_new_plan(self):
        from gbop_voice_web.execution_identity import backend_request_once
        runner=self.runner('gbop_voice_web/server.py');opened=self.open(runner)
        calls=[];history=[{'role':'user','text':'Record another Turtle Soup execution.'}]
        def execute():
            calls.append(1)
            self.context.begin_turn(history[0]['text'],client_turn=12)
            saved=self.context.run('add_entry',self.add_args(opened['trade_id']),runner,
                operation_id='model-call-'+str(len(calls)))
            return 'Saved execution '+str(saved['execution_id'])
        first=backend_request_once(self.context,12,history,execute)
        before=self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
        self.conn.execute("UPDATE members SET updated_at='new-auth-revision' WHERE user_id=20")
        rotated=backend_request_once(self.context,12,history,execute)
        self.assertIn('superseded',rotated)
        replay=backend_request_once(self.context,12,history,execute)
        self.assertEqual(replay,first);self.assertEqual(calls,[1])
        self.assertEqual(len(self.context._execution_backend_requests),1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],before)
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        with self.assertRaisesRegex(ValueError,'revoked'):
            backend_request_once(self.context,12,history,execute)
        self.assertEqual(calls,[1])
