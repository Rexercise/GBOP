"""Interrupted journal writes: synthetic identities, in-memory DB, no network."""
import asyncio
import json
import sqlite3
import threading
import time
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.voice_runtime import (
    JOURNAL_WRITE_NAMES, guarded_voice_tool, journal_write_committed,
    journal_write_result_reported, recovery_options,
)
from gbop_voice_web.voice_work import VoiceToolWork, WRITE_STATUS_SECONDS


async def settle():
    for _ in range(16):
        await asyncio.sleep(0)


class JournalWriteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.execute('CREATE TABLE saved(id INTEGER PRIMARY KEY, member INT, story TEXT)')
        self.session = NS(member=NS(id=101, send=AsyncMock()), websocket=object(),
            market_context=NS(owner=(7, 101, 'discord_voice'), session_id='synthetic-session',
                              closed=False, invalidate=Mock()),
            _voice_turn_count=1, closed=False, authorize_tool=AsyncMock(return_value=None),
            send_event=AsyncMock(return_value=True), tool_output_pending=False,
            _tool_response_options={}, last_error=None)
        self.work = self.session.tool_work = VoiceToolWork(self.session)
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.runner_calls = 0
        self.tasks = []
        self.session.execute_tool = self.execute
        self.provider = self.save_then_wait

    async def asyncTearDown(self):
        self.gate.set()
        self.work.cancel()
        self.session.closed = True
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.work.operation:
            await self.work.operation
        await settle()
        self.conn.close()

    def commit(self, *, guild=7, member=101, with_hook=True):
        with self.conn:
            self.conn.execute('INSERT INTO saved(member,story) VALUES(?,?)',
                              (member, 'invented private journal narrative'))
        if with_hook:
            journal_write_committed(guild, member, {'trade_id': 12, 'execution_id': 35,
                'description': 'invented private journal narrative', 'result_r': -1.0,
                'error': 'invented private error', 'metadata': {'sensitive': 'not for recovery'}})

    async def save_then_wait(self):
        self.runner_calls += 1
        await asyncio.to_thread(self.commit)
        self.started.set()
        await self.gate.wait()
        return {'ok': True, 'trade_id': 12, 'execution_id': 35,
                'description': 'invented private journal narrative', 'warnings': ['invented warning']}

    async def execute(self, item):
        scope = self.work.scope()
        result = await self.work.run_tool(scope, lambda: guarded_voice_tool(
            self.session, item['name'], {}, item['call_id'], self.provider,
            is_current=lambda: self.work.current(scope)))
        if not self.work.current(scope):
            return
        sent = await self.session.send_event({'type': 'conversation.item.create', 'item': {
            'type': 'function_call_output', 'call_id': item['call_id'], 'output': json.dumps(result)}})
        if sent is False or not self.work.current(scope):
            return
        journal_write_result_reported(self.session, item['name'], item['call_id'], result)
        self.session.tool_output_pending = True

    async def start(self, name='open_trade', call_id='original'):
        self.work.start({'name': name, 'call_id': call_id})
        self.tasks.extend(self.work.tasks)
        await asyncio.wait_for(self.started.wait(), 3)
        await settle()

    async def interrupt(self):
        self.work.cancel(preserve_read_status=True)
        self.session._voice_turn_count += 1
        self.session.tool_output_pending = False
        self.work.recover_writes()
        await settle()

    def outcomes(self):
        texts = [call.args[0]['item']['content'][0]['text']
                 for call in self.session.send_event.await_args_list
                 if call.args[0].get('item', {}).get('role') == 'system']
        return [json.loads(text.split('Outcome JSON: ')[1]) for text in texts]

    async def finish(self):
        operation = self.work.operation
        self.gate.set()
        if operation:
            await operation
        await settle()

    async def test_commit_before_warning_recovers_saved_trade_without_old_reply(self):
        await self.start()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 1)
        self.assertFalse(self.work.operation.done())
        await self.interrupt()
        self.assertEqual(self.outcomes(), [{'tool': 'open_trade', 'status': 'saved',
            'saved': True, 'trade_id': 12, 'execution_id': 35}])
        self.assertFalse(self.gate.is_set())
        await self.finish()
        self.assertEqual(len(self.outcomes()), 1)
        self.assertEqual(self.runner_calls, 1)
        self.assertFalse(self.session.tool_output_pending)
        self.assertEqual([c.args[0]['type'] for c in self.session.send_event.await_args_list],
                         ['conversation.item.create'])
        self.session.member.send.assert_not_awaited()
        await self.interrupt()
        self.assertEqual(len(self.outcomes()), 1)

    async def test_actual_barge_in_before_dispatch_proves_not_saved_then_unblocks(self):
        from gbop_voice_web.market_conversation import MarketConversation
        context = MarketConversation((7, 101, 'discord_voice'))
        self.session.market_context = context
        ticket = context.generation
        writer = Mock()
        async def queued():
            self.runner_calls += 1
            self.started.set()
            await self.gate.wait()
            return context.run('save_journal_story', {}, writer, generation=ticket)
        self.provider = queued
        await self.start(name='save_journal_story'); await self.interrupt()
        self.assertEqual(self.outcomes()[-1]['status'], 'pending')
        await self.finish()
        writer.assert_not_called()
        self.assertFalse(self.session._journal_write_recovery['running'])
        self.assertEqual(self.outcomes()[-1]['status'], 'not_saved')
        self.assertFalse(self.session.tool_output_pending)
        await self.interrupt()
        requested = AsyncMock(return_value={'ok': True, 'saved': True})
        result = await guarded_voice_tool(self.session, 'stage_journal_story', {}, 'next-journal', requested)
        self.assertTrue(result['ok']); requested.assert_awaited_once()
        self.assertEqual(self.runner_calls, 1)

    async def test_pending_transitions_to_saved_after_interrupt(self):
        async def pending():
            self.runner_calls += 1
            self.started.set()
            await self.gate.wait()
            await asyncio.to_thread(self.commit)
            return {'ok': True, 'trade_id': 12}
        self.provider = pending
        await self.start(); await self.interrupt()
        self.assertEqual(self.outcomes()[-1]['status'], 'pending')
        self.assertIsNone(self.outcomes()[-1]['saved'])
        await self.finish()
        self.assertEqual([o['status'] for o in self.outcomes()], ['pending', 'saved'])
        self.assertEqual(self.runner_calls, 1)

    async def test_no_commit_evidence_and_exception_stays_uncertain_not_failed_save(self):
        async def uncertain():
            self.started.set()
            await self.gate.wait()
            raise RuntimeError('invented secret error must not be recovered')
        self.provider = uncertain
        await self.start(); await self.interrupt(); await self.finish()
        self.assertEqual([o['status'] for o in self.outcomes()], ['pending', 'uncertain'])
        retry = AsyncMock(return_value={'ok': True})
        result = await guarded_voice_tool(self.session, 'save_journal_entry', {}, 'retry', retry)
        self.assertEqual(result['prior_write']['status'], 'uncertain')
        retry.assert_not_awaited()
        self.assertNotIn('invented secret', json.dumps(self.outcomes()))

    async def test_committed_write_survives_later_warning_exception(self):
        async def warning_error():
            await self.save_then_wait()
            raise RuntimeError('invented private profile failure')
        self.provider = warning_error
        await self.start(); await self.interrupt(); await self.finish()
        self.assertEqual(self.outcomes()[-1]['status'], 'saved')
        replay = await guarded_voice_tool(self.session, 'open_trade', {}, 'original', AsyncMock())
        self.assertTrue(replay['ok'])
        self.assertEqual(replay['trade_id'], 12)
        self.assertNotIn('invented private', json.dumps(replay))

    async def test_later_trade_number_enriches_an_already_recovered_commit(self):
        async def partial_commit_metadata():
            self.commit(with_hook=False)
            journal_write_committed(7, 101, {'execution_id': 35})
            self.started.set()
            await self.gate.wait()
            return {'ok': True, 'trade_id': 12, 'execution_id': 35}
        self.provider = partial_commit_metadata
        await self.start(); await self.interrupt()
        self.assertEqual(self.outcomes()[-1]['status'], 'saved')
        self.assertNotIn('trade_id', self.outcomes()[-1])
        await self.finish()
        self.assertEqual(self.outcomes()[-1]['trade_id'], 12)
        self.assertEqual([o['status'] for o in self.outcomes()], ['saved', 'saved'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 1)
        self.assertFalse(self.session.tool_output_pending)

    async def test_normal_post_commit_failure_returns_saved_metadata(self):
        async def save_and_fail():
            self.commit()
            raise RuntimeError('invented profile failure')
        result = await guarded_voice_tool(self.session, 'open_trade', {}, 'one', save_and_fail)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(result['post_save_processing'], 'unavailable')
        self.assertEqual(result['trade_id'], 12)
        self.assertNotIn('invented profile', json.dumps(result))

    async def test_result_and_recovery_caches_hold_only_metadata(self):
        await self.start(); await self.interrupt(); await self.finish()
        serialized = json.dumps(self.session._tool_call_results)
        serialized += json.dumps(self.session._journal_write_recovery['outcome'])
        serialized += json.dumps(self.outcomes())
        self.assertNotIn('invented private', serialized)
        self.assertNotIn('warnings', serialized)
        self.assertNotIn('result_r', serialized)
        self.assertNotIn('metadata', serialized)

    async def test_pending_replayed_call_id_reports_pending_and_never_runs_again(self):
        async def pending():
            self.started.set(); await self.gate.wait()
            return {'ok': True, 'trade_id': 12}
        self.provider = pending
        await self.start()
        retry = AsyncMock()
        result = await guarded_voice_tool(self.session, 'open_trade', {}, 'original', retry)
        self.assertEqual(result['status'], 'pending')
        retry.assert_not_awaited()

    async def test_all_write_aliases_block_until_reconciled(self):
        await self.start()
        for i, name in enumerate(JOURNAL_WRITE_NAMES):
            retry = AsyncMock()
            result = await guarded_voice_tool(self.session, name, {}, f'retry-{i}', retry)
            self.assertEqual(result['status'], 'journal_write_reconciliation_required')
            self.assertEqual(result['prior_write']['trade_id'], 12)
            retry.assert_not_awaited()

    async def test_recovery_turn_cannot_repeat_write_but_later_distinct_request_can(self):
        await self.start(); await self.interrupt(); await self.finish()
        retry = AsyncMock(return_value={'ok': True, 'trade_id': 13})
        result = await guarded_voice_tool(self.session, 'open_trade', {}, 'premature', retry)
        self.assertFalse(result['ok']); retry.assert_not_awaited()
        await self.interrupt()
        result = await guarded_voice_tool(self.session, 'open_trade', {}, 'distinct-new-request', retry)
        self.assertTrue(result['ok']); self.assertEqual(result['trade_id'], 13)
        retry.assert_awaited_once()

    async def test_premature_queued_retry_cannot_turn_into_new_write_while_waiting(self):
        await self.start()
        self.work.cancel(preserve_read_status=True)
        self.session._voice_turn_count += 1
        self.work.start({'name': 'save_journal_story', 'call_id': 'premature-queued'})
        self.tasks.extend(self.work.tasks)
        self.work.recover_writes()
        await settle(); await self.finish()
        self.assertEqual(self.runner_calls, 1)
        outputs = [json.loads(c.args[0]['item']['output'])
                   for c in self.session.send_event.await_args_list
                   if c.args[0].get('item', {}).get('type') == 'function_call_output']
        self.assertEqual(outputs[-1]['status'], 'journal_write_reconciliation_required')
        self.assertEqual(outputs[-1]['prior_write']['status'], 'saved')

    async def test_intentional_same_turn_queued_entries_wait_for_predecessor_then_both_save(self):
        async def save_entry():
            self.runner_calls += 1
            ordinal = self.runner_calls
            await asyncio.to_thread(self.commit, with_hook=False)
            receipt = {'ok': True, 'trade_id': 12, 'execution_id': ordinal,
                       'persisted_entry_count': ordinal, 'entry_count_verified': True}
            journal_write_committed(7, 101, receipt)
            if ordinal == 1:
                self.started.set()
                await self.gate.wait()
            return receipt
        self.provider = save_entry
        await self.start(name='add_entry', call_id='intentional-first')
        self.work.start({'name': 'add_entry', 'call_id': 'intentional-second'})
        self.tasks.extend(self.work.tasks)
        await settle()
        self.assertEqual(self.runner_calls, 1)
        self.assertNotIn('intentional-second', self.work.write_barriers)
        waiting = list(self.work.tasks)
        self.gate.set()
        await asyncio.gather(*waiting)
        await settle()
        self.assertEqual(self.runner_calls, 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 2)
        outputs = [json.loads(c.args[0]['item']['output'])
                   for c in self.session.send_event.await_args_list
                   if c.args[0].get('item', {}).get('type') == 'function_call_output']
        self.assertEqual([result['persisted_entry_count'] for result in outputs], [1, 2])
        self.assertTrue(all(result['ok'] for result in outputs))
        self.assertTrue(self.session._journal_write_recovery['reconciled'])

    async def test_same_turn_queued_entry_remains_blocked_when_prior_output_was_not_delivered(self):
        self.session.send_event.side_effect = [False, True]
        await self.start(name='add_entry', call_id='first-undelivered')
        self.work.start({'name': 'add_entry', 'call_id': 'second-awaiting-receipt'})
        self.tasks.extend(self.work.tasks)
        waiting = list(self.work.tasks)
        self.gate.set()
        await asyncio.gather(*waiting)
        await settle()
        self.assertEqual(self.runner_calls, 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 1)
        outputs = [json.loads(c.args[0]['item']['output'])
                   for c in self.session.send_event.await_args_list
                   if c.args[0].get('item', {}).get('type') == 'function_call_output']
        self.assertEqual(outputs[-1]['status'], 'journal_write_reconciliation_required')
        self.assertFalse(self.session._journal_write_recovery['reconciled'])

    async def test_barge_in_cancels_unstarted_same_turn_second_entry(self):
        await self.start(name='add_entry', call_id='first-before-interrupt')
        self.work.start({'name': 'add_entry', 'call_id': 'second-before-interrupt'})
        self.tasks.extend(self.work.tasks)
        await self.interrupt()
        await self.finish()
        self.assertEqual(self.runner_calls, 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 1)
        self.assertEqual([o['status'] for o in self.outcomes()], ['saved'])

    async def test_normal_result_is_not_recovered_again_and_next_entry_is_allowed(self):
        await self.start(); await self.finish()
        self.assertTrue(self.session._journal_write_recovery['reconciled'])
        new_entry = AsyncMock(return_value={'ok': True, 'trade_id': 12, 'execution_id': 36})
        result = await guarded_voice_tool(self.session, 'add_entry', {}, 'second-entry', new_entry)
        self.assertTrue(result['ok']); new_entry.assert_awaited_once()
        journal_write_result_reported(self.session, 'add_entry', 'second-entry', result)
        await self.interrupt()
        self.assertEqual(self.outcomes(), [])

    async def test_validation_rejection_is_not_saved_and_allows_corrected_request(self):
        result = await guarded_voice_tool(self.session, 'open_trade', {}, 'invalid',
                                         AsyncMock(return_value={'ok': False, 'needs': 'tier'}))
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'not_saved')
        journal_write_result_reported(self.session, 'open_trade', 'invalid', result)
        corrected = AsyncMock(return_value={'ok': True, 'trade_id': 12})
        await guarded_voice_tool(self.session, 'open_trade', {}, 'corrected', corrected)
        corrected.assert_awaited_once()

    async def test_successfully_skipped_optional_reflection_does_not_claim_saved(self):
        result = await guarded_voice_tool(self.session, 'record_trade_feeling', {}, 'skip',
            AsyncMock(return_value={'ok': True, 'skipped': True, 'feeling_recorded': False}))
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'not_saved')
        journal_write_result_reported(self.session, 'record_trade_feeling', 'skip', result)
        self.assertTrue(self.session._journal_write_recovery['reconciled'])
        await self.interrupt()
        self.assertEqual(self.outcomes(), [])

    async def test_failed_function_output_send_is_reconciled_on_later_turn(self):
        self.session.send_event.return_value = False
        await self.start(); await self.finish()
        self.assertFalse(self.session._journal_write_recovery['reconciled'])
        self.session.send_event.return_value = True
        await self.interrupt()
        self.assertEqual(self.outcomes()[-1]['status'], 'saved')

    async def test_owner_guild_session_socket_closed_and_revoked_fence_recovery(self):
        await self.start()
        changes = [
            (self.session, 'member', NS(id=202, send=AsyncMock())),
            (self.session.market_context, 'owner', (8, 101, 'discord_voice')),
            (self.session.market_context, 'session_id', 'another-session'),
            (self.session, 'websocket', object()),
            (self.session, 'closed', True),
            (self.session.market_context, 'closed', True),
            (self.session, 'authorize_tool', AsyncMock(return_value='Access revoked')),
        ]
        for obj, key, changed in changes:
            with self.subTest(key=key, changed=changed):
                original = getattr(obj, key)
                setattr(obj, key, changed)
                await self.interrupt()
                self.assertEqual(self.outcomes(), [])
                setattr(obj, key, original)
        await self.interrupt()
        self.assertEqual(self.outcomes()[-1]['trade_id'], 12)

    async def test_recovery_rechecks_scope_after_awaited_authorization(self):
        await self.start()
        authorized = asyncio.Event()
        async def blocked_auth():
            await authorized.wait()
        self.session.authorize_tool = blocked_auth
        await self.interrupt()
        self.session.market_context.session_id = 'changed-during-auth'
        authorized.set(); await settle()
        self.assertEqual(self.outcomes(), [])

    async def test_cache_replay_rechecks_access_and_connection(self):
        await self.start(); await self.finish()
        runner = AsyncMock()
        self.session.authorize_tool.return_value = 'Access revoked'
        denied = await guarded_voice_tool(self.session, 'open_trade', {}, 'original', runner)
        self.assertNotIn('trade_id', denied)
        self.session.authorize_tool.return_value = None
        self.session.websocket = object()
        denied = await guarded_voice_tool(self.session, 'open_trade', {}, 'original', runner)
        self.assertNotIn('trade_id', denied)
        runner.assert_not_awaited()

    async def test_wrong_member_commit_callback_cannot_assert_saved(self):
        async def wrong_owner():
            await asyncio.to_thread(self.commit, member=202)
            raise RuntimeError('No authenticated commit evidence')
        with self.assertRaises(RuntimeError):
            await guarded_voice_tool(self.session, 'open_trade', {}, 'wrong', wrong_owner)
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'uncertain')

    async def test_wrong_guild_commit_callback_cannot_assert_saved(self):
        async def wrong_guild():
            await asyncio.to_thread(self.commit, guild=8)
            raise RuntimeError('No authenticated guild commit evidence')
        with self.assertRaises(RuntimeError):
            await guarded_voice_tool(self.session, 'open_trade', {}, 'wrong-guild', wrong_guild)
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'uncertain')

    async def test_malformed_commit_identifiers_never_enter_metadata(self):
        async def malformed_metadata():
            journal_write_committed(7, 101, {'trade_id': 'invented private text',
                'trade_number': True, 'journal_number': -12, 'execution_id': 35.5})
            return {'ok': True}
        await guarded_voice_tool(self.session, 'open_trade', {}, 'malformed', malformed_metadata)
        await settle()
        expected = {'tool': 'open_trade', 'status': 'saved', 'saved': True}
        self.assertEqual(self.session._journal_write_recovery['outcome'], expected)
        self.assertEqual(self.session._tool_call_results['malformed'], {'ok': True, **expected})

    async def check_recovery_transport_failure(self, *, raises):
        await self.start()
        if raises:
            self.session.send_event.side_effect = RuntimeError('synthetic transport failure')
        else:
            self.session.send_event.return_value = False
        await self.interrupt()
        self.assertFalse(self.session._journal_write_recovery['reconciled'])
        self.session.send_event.side_effect = None
        self.session.send_event.return_value = True
        await self.interrupt()
        self.assertTrue(self.session._journal_write_recovery['reconciled'])
        self.assertEqual(self.outcomes()[-1]['trade_id'], 12)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM saved').fetchone()[0], 1)
        self.assertFalse(self.session.tool_output_pending)

    async def test_failed_recovery_send_can_publish_context_on_later_turn(self):
        await self.check_recovery_transport_failure(raises=False)

    async def test_raised_recovery_send_can_publish_context_on_later_turn(self):
        await self.check_recovery_transport_failure(raises=True)

    async def test_recovery_expiry_does_not_clear_uncertain_write_barrier(self):
        await self.start()
        self.work.cancel(preserve_read_status=True)
        self.session._voice_turn_count += 1
        self.session._journal_write_recovery['interrupted_at'] -= WRITE_STATUS_SECONDS + 1
        self.work.recover_writes(); await settle()
        self.assertEqual(self.outcomes(), [])
        runner = AsyncMock()
        denied = await guarded_voice_tool(self.session, 'add_entry', {}, 'late', runner)
        self.assertFalse(denied['ok']); runner.assert_not_awaited()

    async def test_second_barge_in_fences_blocked_context_send(self):
        await self.start()
        sending, release = asyncio.Event(), asyncio.Event()
        delivered = []
        async def send(event):
            sending.set(); await release.wait(); delivered.append(event)
            return True
        self.session.send_event.side_effect = send
        await self.interrupt()
        await asyncio.wait_for(sending.wait(), 3)
        await self.interrupt()
        self.work.cancel()
        release.set(); await settle()
        self.assertEqual(delivered, [])
        self.assertFalse(self.session._journal_write_recovery['reconciled'])

    async def test_terminal_update_during_pending_context_send_is_not_lost(self):
        async def pending():
            self.started.set(); await self.gate.wait()
            await asyncio.to_thread(self.commit)
            return {'ok': True, 'trade_id': 12}
        self.provider = pending
        await self.start()
        sending, release = asyncio.Event(), asyncio.Event()
        async def send(event):
            if not sending.is_set():
                sending.set(); await release.wait()
            return True
        self.session.send_event.side_effect = send
        await self.interrupt()
        await self.finish()
        self.assertEqual(self.outcomes()[-1]['status'], 'pending')
        release.set(); await settle()
        self.assertEqual([o['status'] for o in self.outcomes()], ['pending', 'saved'])
        self.assertTrue(self.session._journal_write_recovery['reconciled'])

    async def test_real_discord_handlers_recover_while_post_save_work_is_running(self):
        from test_voice_latency import method
        from test_voice_work import Socket
        from gbop_voice_web.voice_payload import voice_tool_payload
        from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery
        started, release = threading.Event(), threading.Event()
        calls = []
        def dispatch(member, name, args):
            calls.append((member, name))
            self.commit()
            started.set()
            if not release.wait(3):
                raise RuntimeError('Synthetic test did not release post-save work')
            return {'ok': True, 'trade_id': 12, 'execution_id': 35}
        self.session.websocket = Socket()
        self.session.market_context.generation = 0
        self.session.market_context.begin_turn = Mock()
        self.session.market_context.run = lambda name, args, runner, **kwargs: runner(name, args)
        self.session.voice_client = NS(channel=NS(id=17))
        self.session.rate_limit_recovery = VoiceRateLimitRecovery(self.session)
        self.session._logged_audio_items = set()
        self.session._last_response_options = {}
        execute = method('execute_tool', dict(asyncio=asyncio, json=json, time=time,
            ai_execute_tool=dispatch, voice_tool_payload=voice_tool_payload,
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        self.session.execute_tool = MethodType(execute, self.session)
        source = Mock()
        playback = NS(source=source, voice_client=NS(is_playing=lambda: True, stop_playing=Mock()))
        receiver = method('receiver_loop', dict(json=json, gbop_output_manager=lambda _: playback))
        receiving = asyncio.create_task(receiver(self.session))
        try:
            await self.session.websocket.emit({'type': 'response.created', 'response': {'id': 'old'}})
            await self.session.websocket.emit({'type': 'response.output_item.done', 'response_id': 'old',
                'item': {'type': 'function_call', 'name': 'open_trade', 'call_id': 'live-save', 'arguments': '{}'}})
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            await self.session.websocket.emit({'type': 'input_audio_buffer.speech_started'})
            self.assertEqual(self.outcomes()[-1]['trade_id'], 12)
            self.assertEqual(self.outcomes()[-1]['status'], 'saved')
            self.assertFalse(release.is_set())
            operation = self.work.operation
            release.set()
            if operation:
                await operation
            await settle()
            self.assertEqual(calls, [(101, 'open_trade')])
            self.assertEqual(len(self.session.send_event.await_args_list), 1)
            self.assertFalse(self.session.tool_output_pending)
            source.abort.assert_called_once()
        finally:
            release.set()
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)
            self.session.rate_limit_recovery.cancel()

    async def test_optout_story_preview_is_tracked_without_claiming_database_save(self):
        draft = AsyncMock(return_value={'ok': True, 'staged': True, 'saved': False, 'persisted': False})
        result = await guarded_voice_tool(self.session, 'stage_journal_story', {}, 'stage', draft)
        self.assertTrue(result['staged'])
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'not_saved')
        self.assertFalse(self.session._journal_write_recovery['outcome']['saved'])
        self.session.recovery_tools = [{'name': 'get_journal_story'}, {'name': 'save_journal_story'}]
        self.assertEqual(recovery_options(self.session)['tools'], [{'name': 'get_journal_story'}])


if __name__ == '__main__':
    unittest.main()
