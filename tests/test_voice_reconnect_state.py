"""Reconnect admission and at-most-once side effects, without provider calls."""
import asyncio
import contextlib
import json
import sqlite3
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from gbop_voice_web.voice_runtime import (
    bind_voice_tool_connection, delivery_result_reported, guarded_voice_tool,
    journal_write_committed, journal_write_result_reported, voice_tool_operation_id,
    VoiceConnectionRejected, VOICE_TRUNCATION,
)
from gbop_voice_web.voice_work import VoiceToolWork
from test_voice_latency import method


def session():
    value = NS(member=NS(id=101, send=AsyncMock()), websocket=object(),
        market_context=NS(owner=(7, 101, 'discord_voice'), session_id='reconnect-test',
                          closed=False, invalidate=Mock()),
        _voice_turn_count=1, closed=False, authorize_tool=AsyncMock(return_value=None),
        send_event=AsyncMock(return_value=True), tool_output_pending=False,
        _tool_response_options={}, _last_response_options={}, last_error=None)
    value.tool_work = VoiceToolWork(value)
    value.rate_limit_recovery = Mock()
    return value


class ReconnectRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_rejected_authorization_stops_without_retry_or_receiver(self):
        value = session()
        value.websocket = None
        value.ready = asyncio.Event()
        value.market_delivery = Mock()
        socket = NS(close=AsyncMock())
        value.connect_ws = AsyncMock(return_value=socket)
        value.authorize_tool.return_value = 'Synthetic private denial detail'
        value.receiver_loop = AsyncMock()
        value.sender_loop = AsyncMock()
        value.session_update = Mock()
        run = method('run', dict(asyncio=asyncio, GBOP_REALTIME_MODEL='synthetic',
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700, GBOP_VAD_EAGERNESS='synthetic',
            VOICE_TRUNCATION=VOICE_TRUNCATION))
        await asyncio.wait_for(run(value), 3)
        self.assertFalse(value.closed)
        self.assertTrue(value.connection_blocked)
        value.connect_ws.assert_awaited_once()
        value.receiver_loop.assert_not_awaited()
        value.sender_loop.assert_not_awaited()
        value.session_update.assert_not_called()
        value.send_event.assert_not_awaited()
        socket.close.assert_awaited_once()
        self.assertNotIn('private denial', value.last_error)

    async def test_run_reconnect_allows_fresh_read_even_when_provider_reuses_call_id(self):
        value = session()
        value.websocket = None
        value.ready = asyncio.Event()
        value.market_delivery = Mock()
        sockets = [NS(close=AsyncMock()), NS(close=AsyncMock())]
        value.connect_ws = AsyncMock(side_effect=sockets)
        value.session_update = lambda: {'session': {'instructions': '', 'tools': []}}
        read = AsyncMock(side_effect=[{'ok': True, 'value': 'first'}, {'ok': True, 'value': 'second'}])
        results = []

        async def receive():
            value.ready.set()
            results.append(await guarded_voice_tool(value, 'get_trade_state', {}, 'reused', read))
            if len(results) == 2:
                value.closed = True

        async def send_audio():
            await asyncio.Event().wait()

        value.receiver_loop = receive
        value.sender_loop = send_audio
        run = method('run', dict(asyncio=asyncio, GBOP_REALTIME_MODEL='synthetic',
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700, GBOP_VAD_EAGERNESS='synthetic',
            VOICE_TRUNCATION=VOICE_TRUNCATION))
        await asyncio.wait_for(run(value), 3)
        self.assertEqual(results, [{'ok': True, 'value': 'first'}, {'ok': True, 'value': 'second'}])
        self.assertEqual(read.await_count, 2)
        for socket in sockets:
            socket.close.assert_awaited_once()


async def settle():
    for _ in range(16):
        await asyncio.sleep(0)


class ReconnectStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = session()
        self.work = self.session.tool_work
        self.gates = []
        self.operations = []

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        self.work.cancel()
        self.session.closed = True
        await asyncio.gather(*self.operations, return_exceptions=True)
        await settle()

    async def reconnect(self):
        self.session.websocket = object()
        await bind_voice_tool_connection(self.session)

    async def next_turn(self):
        self.work.cancel(preserve_read_status=True)
        self.session._voice_turn_count += 1
        self.work.recover_writes()
        self.work.recover_delivery()
        await settle()

    def outcomes(self):
        texts = [call.args[0]['item']['content'][0]['text']
                 for call in self.session.send_event.await_args_list
                 if call.args[0].get('item', {}).get('role') == 'system']
        return [json.loads(text.split('Outcome JSON: ')[1]) for text in texts
                if 'Outcome JSON: ' in text]

    async def run_guard(self, name='get_trade_state', call_id='read', runner=None, args=None):
        return await guarded_voice_tool(self.session, name, args or {}, call_id,
            runner or AsyncMock(return_value={'ok': True}))

    def start_work(self, provider, name='get_trade_state', call_id='old'):
        async def execute(item):
            scope = self.work.scope()
            result = await self.work.run_tool(scope, lambda: guarded_voice_tool(
                self.session, item['name'], {}, item['call_id'], provider,
                is_current=lambda: self.work.current(scope)))
            if self.work.current(scope):
                await self.session.send_event({'type': 'conversation.item.create', 'item': {
                    'type': 'function_call_output', 'call_id': item['call_id'],
                    'output': json.dumps(result)}})
                journal_write_result_reported(self.session, item['name'], item['call_id'], result)
        self.session.execute_tool = execute
        self.work.start({'name': name, 'call_id': call_id})

    async def delayed_provider(self, result):
        started, gate = asyncio.Event(), asyncio.Event()
        self.gates.append(gate)
        async def provider():
            started.set()
            await gate.wait()
            return result
        return started, gate, provider

    async def test_fresh_authorization_and_connection_scoped_read_cache(self):
        await self.run_guard(runner=AsyncMock(return_value={'ok': True, 'value': 'old'}))
        old_cache = self.session._tool_call_results
        self.work.seen_calls.append('read')
        self.work.responses.add('response-id')
        self.work.request_scopes['request-id'] = self.work.scope()
        self.session.tool_output_pending = True
        self.session._tool_response_options = {'max_output_tokens': 2200}
        await self.reconnect()
        self.session.authorize_tool.assert_awaited()
        self.assertIsNot(self.session._tool_call_results, old_cache)
        self.assertEqual(list(self.work.seen_calls), [])
        self.assertEqual(self.work.responses, set())
        self.assertEqual(self.work.request_scopes, {})
        self.assertFalse(self.session.tool_output_pending)
        self.assertEqual(self.session._tool_response_options, {})
        runner = AsyncMock(return_value={'ok': True, 'value': 'new'})
        self.assertEqual((await self.run_guard(runner=runner))['value'], 'new')
        runner.assert_awaited_once()
        self.session.send_event.assert_not_awaited()
        self.session.member.send.assert_not_awaited()

    async def test_repeated_binding_of_same_socket_keeps_deduplication(self):
        await bind_voice_tool_connection(self.session)
        read = AsyncMock(return_value={'ok': True, 'value': 'cached'})
        await self.run_guard(runner=read)
        cache = self.session._tool_call_results
        await bind_voice_tool_connection(self.session)
        self.assertIs(self.session._tool_call_results, cache)
        await self.run_guard(runner=read)
        read.assert_awaited_once()

    async def test_revoked_access_does_not_rebind_or_discard_prior_state(self):
        await self.run_guard()
        connection, cache = self.session._tool_connection, self.session._tool_call_results
        self.session.authorize_tool.return_value = 'Access revoked'
        with self.assertRaises(VoiceConnectionRejected):
            await self.reconnect()
        self.assertIs(self.session._tool_connection, connection)
        self.assertIs(self.session._tool_call_results, cache)
        denied = await self.run_guard()
        self.assertEqual(denied, {'ok': False, 'error': 'Access revoked'})

    async def test_identity_changes_cannot_adopt_old_member_cache(self):
        await self.run_guard(runner=AsyncMock(return_value={'ok': True, 'private': 'old member'}))
        changes = [
            (self.session, 'member', NS(id=202, send=AsyncMock())),
            (self.session.market_context, 'owner', (8, 101, 'discord_voice')),
            (self.session.market_context, 'session_id', 'different-session'),
            (self.session.market_context, 'closed', True),
            (self.session, 'closed', True),
            (self.session, 'authorize_tool', None),
        ]
        for obj, key, value in changes:
            with self.subTest(key=key):
                original = getattr(obj, key)
                setattr(obj, key, value)
                with self.assertRaises(VoiceConnectionRejected):
                    await self.reconnect()
                setattr(obj, key, original)
        self.session.send_event.assert_not_awaited()

    async def test_rebind_rechecks_identity_socket_and_turn_after_auth_await(self):
        await self.run_guard()
        changes = [
            (self.session.market_context, 'session_id', 'swapped'),
            (self.session, 'websocket', object()),
            (self.session, '_voice_turn_count', 2),
            (self.session, 'closed', True),
        ]
        for obj, key, value in changes:
            with self.subTest(key=key):
                original = getattr(obj, key)
                async def changed():
                    setattr(obj, key, value)
                self.session.authorize_tool = changed
                with self.assertRaises(VoiceConnectionRejected):
                    await self.reconnect()
                setattr(obj, key, original)
        self.session.send_event.assert_not_awaited()

    async def test_old_read_worker_cannot_overwrite_reused_id_or_emit_output(self):
        started, gate, provider = await self.delayed_provider({'ok': True, 'value': 'old'})
        self.start_work(provider, call_id='reused')
        await started.wait()
        old_operation = self.work.operation
        self.operations.append(old_operation)
        old_cache = self.session._tool_call_results
        await self.reconnect()
        fresh = AsyncMock(return_value={'ok': True, 'value': 'new'})
        self.start_work(fresh, call_id='reused')
        await settle()
        gate.set()
        await old_operation
        await settle()
        self.assertEqual(self.session._tool_call_results['reused']['value'], 'new')
        self.assertEqual(old_cache['reused']['value'], 'old')
        self.assertEqual(self.session.send_event.await_count, 1)
        output = json.loads(self.session.send_event.await_args.args[0]['item']['output'])
        self.assertEqual(output['value'], 'new')
        fresh.assert_awaited_once()

    async def test_reported_commit_reconciles_in_new_context_before_new_write(self):
        result = await self.run_guard('open_trade', 'write', AsyncMock(return_value={
            'ok': True, 'trade_id': 12, 'description': 'private narrative'}))
        journal_write_result_reported(self.session, 'open_trade', 'write', result)
        entry = self.session._journal_write_recovery
        self.assertTrue(entry['reconciled'])
        # Paused sessions may restart well beyond the old observation window.
        entry['interrupted_at'] = time.monotonic() - 3600
        await self.reconnect()
        self.assertIs(self.session._journal_write_recovery, entry)
        self.assertFalse(entry['reconciled'])
        retry = AsyncMock(return_value={'ok': True, 'trade_id': 13})
        blocked = await self.run_guard('open_trade', 'new-id', retry)
        self.assertEqual(blocked['prior_write']['trade_id'], 12)
        retry.assert_not_awaited()
        self.session.send_event.assert_not_awaited()
        await self.next_turn()
        self.assertEqual(self.outcomes(), [{'tool': 'open_trade', 'status': 'saved',
                                          'saved': True, 'trade_id': 12}])
        self.assertNotIn('private narrative', json.dumps(self.outcomes()))
        await self.run_guard('add_entry', 'same-turn', retry)
        retry.assert_not_awaited()
        await self.next_turn()
        result = await self.run_guard('add_entry', 'distinct-user-request', retry)
        self.assertEqual(result['trade_id'], 13)
        retry.assert_awaited_once()

    async def test_pending_write_commits_after_reconnect_without_old_output_or_replay(self):
        started, gate = asyncio.Event(), asyncio.Event()
        self.gates.append(gate)
        writes = []
        async def provider():
            started.set()
            await gate.wait()
            writes.append(12)
            journal_write_committed(7, 101, {'trade_id': 12, 'description': 'private narrative'})
            return {'ok': True, 'trade_id': 12, 'description': 'private narrative'}
        self.start_work(provider, name='open_trade')
        await started.wait()
        operation = self.work.operation
        self.operations.append(operation)
        entry = self.session._journal_write_recovery
        await self.reconnect()
        retry = AsyncMock()
        blocked = await self.run_guard('save_journal_entry', 'retry', retry)
        self.assertEqual(blocked['prior_write']['status'], 'pending')
        self.assertIs(entry, self.session._journal_write_recovery)
        await self.next_turn()
        self.assertEqual(self.outcomes()[-1]['status'], 'pending')
        gate.set()
        await operation
        await settle()
        self.assertEqual([value['status'] for value in self.outcomes()], ['pending', 'saved'])
        self.assertEqual(writes, [12])
        retry.assert_not_awaited()
        self.assertTrue(all(call.args[0].get('item', {}).get('role') == 'system'
                            for call in self.session.send_event.await_args_list))
        self.assertFalse(self.session.tool_output_pending)
        self.assertNotIn('private narrative', str(self.session.send_event.await_args_list))

    async def test_uncertain_write_remains_blocked_across_multiple_reconnects(self):
        with self.assertRaises(RuntimeError):
            await self.run_guard('open_trade', 'write', AsyncMock(side_effect=RuntimeError('private error')))
        entry = self.session._journal_write_recovery
        for index in range(2):
            await self.reconnect()
            await self.next_turn()
            retry = AsyncMock()
            result = await self.run_guard('add_entry', 'retry-' + str(index), retry)
            self.assertEqual(result['prior_write']['status'], 'uncertain')
            self.assertIs(entry, self.session._journal_write_recovery)
            self.assertFalse(entry['reconciled'])
            retry.assert_not_awaited()
        self.assertNotIn('private error', str(self.session.send_event.await_args_list))

    async def test_delivery_result_and_receipts_survive_without_duplicate_send(self):
        result = {'ok': True, 'status': 'delivered', 'sent_count': 1,
                  'receipt_id': 'synthetic-receipt', 'tool': 'send_journal_history'}
        send = AsyncMock(return_value=result)
        reader = AsyncMock(return_value={'ok': True, 'receipts': [result]})
        await guarded_voice_tool(self.session, 'send_journal_history', {}, 'send', send,
                                 receipt_reader=reader)
        delivery_result_reported(self.session, result)
        entry = self.session._delivery_recovery
        deliveries = self.session._delivery_results
        receipts = self.session._delivery_receipts
        await self.reconnect()
        self.assertIs(self.session._delivery_results, deliveries)
        self.assertIs(self.session._delivery_receipts, receipts)
        self.assertIs(self.session._delivery_recovery, entry)
        self.assertEqual(await self.run_guard('send_journal_history', 'new-send-id', send), result)
        send.assert_awaited_once()
        await self.next_turn()
        reader.assert_awaited_once_with('synthetic-receipt')
        self.assertEqual(self.session.send_event.await_count, 1)
        self.assertIn('PRIVATE DELIVERY STATUS CONTEXT', str(self.session.send_event.await_args))
        self.session.member.send.assert_not_awaited()

    async def test_pending_delivery_finishes_after_reconnect_without_old_output(self):
        result = {'ok': True, 'status': 'delivered', 'sent_count': 1,
                  'receipt_id': 'synthetic-receipt', 'tool': 'send_journal_history'}
        started, gate, provider = await self.delayed_provider(result)
        self.start_work(provider, name='send_journal_history')
        await started.wait()
        operation = self.work.operation
        self.operations.append(operation)
        deliveries = self.session._delivery_results
        await self.reconnect()
        repeat = AsyncMock()
        blocked = await self.run_guard('send_journal_history', 'retry', repeat)
        self.assertFalse(blocked['ok'])
        repeat.assert_not_awaited()
        gate.set()
        await operation
        await settle()
        self.assertIs(self.session._delivery_results, deliveries)
        self.assertEqual(next(iter(deliveries.values()))['sent_count'], 1)
        self.assertEqual(self.session._delivery_recovery['receipt']['sent_count'], 1)
        self.session.send_event.assert_not_awaited()

    async def test_durable_private_receipt_blocks_resend_on_later_turn_after_reconnect(self):
        from gbop_voice_web.delivery_receipts import deliver
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                self.session = session()
                self.work = self.session.tool_work
                conn = sqlite3.connect(':memory:')
                conn.row_factory = sqlite3.Row
                conn.executescript("""
                    CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
                    CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT,leadership_ack INT,updated_at TEXT);
                    INSERT INTO members VALUES(7,101,1,0,1,'a');
                """)
                @contextlib.contextmanager
                def db():
                    with conn:
                        yield conn
                posts = []
                def perform(operation):
                    operation.before_send()
                    posts.append('synthetic private send')
                    if not uncertain:
                        operation.accepted(NS(json=lambda: {'id': 'message'}))
                    return operation.finish({'ok': not uncertain},
                                            'uncertain' if uncertain else 'delivered')
                async def provider():
                    return deliver(db, 7, 101, 'send_journal_history', {}, perform)
                try:
                    original = await self.run_guard('send_journal_history', 'send', provider)
                    await self.reconnect()
                    await self.next_turn()
                    recovered = await self.run_guard('send_journal_history', 'another-call', provider)
                    self.assertEqual(recovered['receipt_id'], original['receipt_id'])
                    self.assertEqual(recovered['status'], original['status'])
                    self.assertEqual(posts, ['synthetic private send'])
                finally:
                    self.work.cancel()
                    conn.close()

    async def test_operation_identity_is_stable_per_socket_and_never_rebinds_implicitly(self):
        await bind_voice_tool_connection(self.session)
        original = voice_tool_operation_id(self.session, 'provider-call')
        self.assertEqual(len(original), 64)
        self.assertEqual(voice_tool_operation_id(self.session, 'provider-call'), original)
        self.assertNotEqual(voice_tool_operation_id(self.session, 'other-call'), original)
        await self.next_turn()
        await bind_voice_tool_connection(self.session)
        self.assertEqual(voice_tool_operation_id(self.session, 'provider-call'), original)
        self.session.websocket = object()
        with self.assertRaises(VoiceConnectionRejected):
            voice_tool_operation_id(self.session, 'provider-call')
        await bind_voice_tool_connection(self.session)
        self.assertNotEqual(voice_tool_operation_id(self.session, 'provider-call'), original)
        for invalid in (None, '', 1, 'x' * 257):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    voice_tool_operation_id(self.session, invalid)

    async def test_real_transport_reused_call_id_is_distinct_execution_after_reconnect(self):
        from gbop_voice_web.market_conversation import MarketConversation
        for name in ('open_trade', 'add_entry'):
            for changed_details in (False, True):
                with self.subTest(name=name, changed_details=changed_details):
                    self.session = session()
                    self.work = self.session.tool_work
                    context = self.session.market_context = MarketConversation((7, 101, 'discord_voice'))
                    context.begin_turn('Record an unrelated execution.')
                    execution_namespace = context._execution_namespace
                    writes = []
                    def provider(member, tool, args):
                        operation = args['_execution_operation']
                        writes.append(operation.operation_id)
                        return {'ok': True, 'trade_id': 12, 'execution_id': len(writes),
                                'operation_id': operation.operation_id}
                    execute = method('execute_tool', dict(asyncio=asyncio, json=json, time=time,
                        ai_execute_tool=provider, voice_tool_payload=lambda _name, result: result,
                        GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
                    item = {'name': name, 'call_id': 'provider-reuses-this',
                            'arguments': json.dumps({'market_reference': 'none', 'notes': 'first execution'})}
                    await bind_voice_tool_connection(self.session)
                    await execute(self.session, item)
                    self.assertEqual(len(writes), 1, self.session.send_event.await_args_list)
                    first_operation = writes[0]
                    retained_results = context._execution_results
                    await self.reconnect()
                    # Reconcile the verified earlier result, then advance to a
                    # distinct later user request before admitting a new write.
                    await self.next_turn()
                    self.assertTrue(self.session._journal_write_recovery['reconciled'])
                    await self.next_turn()
                    if changed_details:
                        item = {**item, 'arguments': json.dumps({'market_reference': 'none',
                                                               'notes': 'second execution'})}
                    await execute(self.session, item)
                    self.assertEqual(len(writes), 2)
                    self.assertNotEqual(writes[0], writes[1])
                    self.assertEqual(context._execution_namespace, execution_namespace)
                    self.assertIs(context._execution_results, retained_results)
                    self.assertIn(first_operation, retained_results)
                    # Same connection/call ID still executes at most once.
                    await execute(self.session, item)
                    self.assertEqual(len(writes), 2)
                    self.work.cancel()

    async def test_reconnected_distinct_entry_persists_new_receipt_and_keeps_prior_receipt(self):
        from test_execution_identity import ExecutionIdentityTests
        from gbop_voice_web.execution_identity import replay_receipt
        fixture = ExecutionIdentityTests()
        fixture.setUp()
        try:
            self.session.member.id = 20
            context = self.session.market_context = fixture.context
            context.begin_turn('Record an unrelated execution.')
            real_provider = fixture.runner('bot.py')
            operations = []
            def provider(member, name, args):
                self.assertEqual(member, 20)
                operations.append(args['_execution_operation'])
                return real_provider(name, args)
            async def local_thread(fn, *args, **kwargs):
                # Keep the existing SQLite-only fixture on its owning thread.
                return fn(*args, **kwargs)
            execute = method('execute_tool', dict(asyncio=NS(to_thread=local_thread),
                json=json, time=time, ai_execute_tool=provider,
                voice_tool_payload=lambda _name, result: result, GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
            async def invoke(name, args, call_id):
                await execute(self.session, {'name': name, 'call_id': call_id,
                                            'arguments': json.dumps(args)})
            await bind_voice_tool_connection(self.session)
            await invoke('open_trade', dict(asset='NAS100', direction='Bearish', play='Young Lefty',
                entry_model='Blessed Thief', tier=2, risk_r=.5, market_reference='none'), 'open')
            args = fixture.add_args()
            await invoke('add_entry', args, 'reused-entry')
            self.assertEqual(fixture.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 2)
            prior_operation = operations[-1]
            prior_result = context._execution_results[prior_operation.operation_id]['result']
            await self.reconnect()
            await self.next_turn()
            await self.next_turn()
            await invoke('add_entry', args, 'reused-entry')
            self.assertEqual(fixture.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 3)
            self.assertNotEqual(operations[-1].operation_id, prior_operation.operation_id)
            self.assertIn(prior_operation.operation_id, context._execution_results)
            with fixture.db() as conn:
                recovered = replay_receipt(conn, prior_operation)
            self.assertTrue(recovered['replayed'])
            self.assertEqual(recovered['execution_id'], prior_result['execution_id'])
            await invoke('add_entry', args, 'reused-entry')
            self.assertEqual(fixture.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 3)
        finally:
            self.work.cancel()
            fixture.tearDown()

    async def test_recovery_is_reauthorized_after_reconnect_before_publication(self):
        await self.run_guard('open_trade', 'write', AsyncMock(return_value={'ok': True, 'trade_id': 12}))
        await self.reconnect()
        self.session.authorize_tool.return_value = 'Access revoked after reconnect'
        await self.next_turn()
        self.assertEqual(self.outcomes(), [])
        self.assertFalse(self.session._journal_write_recovery['reconciled'])
        self.session.send_event.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
