"""Synthetic finalize recovery only: no production account or network writes."""
import asyncio
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web import journal_coach as coach, journal_drafts, journal_finalization
from gbop_voice_web.journal_context import JournalBinding
from gbop_voice_web.voice_runtime import (
    _journal_result_status, guarded_voice_tool, journal_write_result_reported,
    journal_write_recovery_ready, JOURNAL_WRITE_NAMES,
)
import test_progressive_journal as fixture


class FinalizationProofTests(unittest.TestCase):
    setUp = fixture.ProgressiveJournalTests.setUp
    tearDown = fixture.ProgressiveJournalTests.tearDown
    review = fixture.ProgressiveJournalTests.review
    handlers = fixture.ProgressiveJournalTests.handlers
    call = fixture.ProgressiveJournalTests.call
    stage = fixture.ProgressiveJournalTests.stage
    saved = fixture.ProgressiveJournalTests.saved
    stored = fixture.ProgressiveJournalTests.stored

    def reader(self, draft):
        return journal_finalization.prepare_story_finalization(self.db, 10, 20,
            {'draft_id': draft['draft_id'], '_journal_binding': JournalBinding(self.context, self.context.generation)})

    def test_unchanged_unfinished_revision_is_read_only_negative_proof(self):
        draft = self.stage(); reader = self.reader(draft)
        before = list(self.conn.iterdump()); result = reader()
        self.assertEqual(result['status'], 'journal_not_saved')
        self.assertEqual(_journal_result_status(result), 'not_saved')
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertNotIn('story', result)

    def test_committed_revision_returns_owned_identity_without_private_narrative(self):
        draft = self.stage(); reader = self.reader(draft); saved = self.saved(draft)
        before = list(self.conn.iterdump()); result = reader()
        self.assertTrue(result['saved']); self.assertTrue(result['reconciliation_verified'])
        self.assertEqual(result['trade_number'], saved['trade_number'])
        self.assertEqual(result['draft_status'], 'finalized')
        self.assertNotIn('description', result); self.assertNotIn('story', result)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_unchanged_correction_with_older_saved_journal_is_not_saved(self):
        first = self.stage(); self.assertTrue(self.saved(first)['ok'])
        changed = self.stage({'context_notes': 'Synthetic corrected context.'}, draft_id=first['draft_id'])
        result = self.reader(changed)()
        self.assertEqual(result['status'], 'journal_not_saved')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)

    def test_newer_content_and_bookkeeping_revisions_stay_unknown(self):
        draft = self.stage(); reader = self.reader(draft)
        self.stage({'context_notes': 'A concurrent correction.'}, draft_id=draft['draft_id'])
        self.assertIsNone(reader())
        current = self.stored(draft['draft_id']); reader = self.reader(draft)
        with self.db() as conn:
            current['asked'].append('synthetic-question')
            journal_drafts.write(conn, 10, 20, current, expected_revision=current['storage_revision'])
        self.assertIsNone(reader())

    def test_deleted_or_missing_table_is_not_negative_proof(self):
        draft = self.stage(); reader = self.reader(draft)
        self.conn.execute('DELETE FROM journal_story_drafts')
        self.assertIsNone(reader())
        self.conn.execute('DROP TABLE journal_story_drafts')
        self.assertIsNone(reader())

    def test_foreign_owner_and_replaced_session_are_never_reconciled(self):
        draft = self.stage(); reader = self.reader(draft)
        old = self.context.owner; self.context.owner = (10, 30, 'other')
        self.assertIsNone(reader()); self.context.owner = old
        self.context.session_id = 'different-session'
        self.assertIsNone(reader())

    def test_revoked_member_fails_closed(self):
        draft = self.stage(); reader = self.reader(draft)
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        with self.assertRaises(ValueError): reader()

    def test_wrong_finalized_links_or_metadata_stay_unknown(self):
        draft = self.stage(); reader = self.reader(draft); self.saved(draft)
        self.conn.execute('UPDATE journal_story_drafts SET thesis_id=999')
        self.assertIsNone(reader())
        self.conn.execute('UPDATE journal_story_drafts SET thesis_id=1')
        self.conn.execute("UPDATE journal_details SET metadata='{}'")
        self.assertIsNone(reader())

    def test_exception_after_commit_without_callback_recovers_and_never_duplicates(self):
        draft = self.stage(); self.context.begin_turn('Finalize the journal.')
        args = {'draft_id': draft['draft_id']}; calls = []
        def runner(name, values):
            calls.append(name)
            with patch('gbop_voice_web.voice_runtime.journal_write_committed'):
                result = coach.coach_tool(self.db, 10, 20, name, values)
            self.assertTrue(result['ok'], result)
            raise RuntimeError('Synthetic reply transport failed.')
        result = self.context.run('save_journal_story', args, runner)
        self.assertTrue(result['saved']); self.assertTrue(result['reconciliation_verified'])
        again = self.context.run('save_journal_story', args, runner)
        self.assertEqual(again, result); self.assertEqual(calls, ['save_journal_story'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)

    def test_failed_preflight_never_enters_writer(self):
        draft = self.stage(); self.context.begin_turn('Finalize the journal.'); runner = Mock()
        with patch.object(journal_finalization, 'prepare_story_finalization', side_effect=OSError('private DB error')):
            result = self.context.run('save_journal_story', {'draft_id': draft['draft_id']}, runner)
        runner.assert_not_called(); self.assertFalse(result['write_attempted'])
        self.assertNotIn('private DB error', json.dumps(result))
        self.assertEqual(_journal_result_status(result), 'not_saved')
        self.assertTrue(self.saved(draft)['ok'])

    def test_pre_dispatch_cancel_has_definite_status_and_generic_stale_does_not(self):
        draft = self.stage(); ticket = self.context.generation; self.context.invalidate(); runner = Mock()
        result = self.context.run('save_journal_story', {'draft_id': draft['draft_id']}, runner, generation=ticket)
        runner.assert_not_called(); self.assertFalse(result['write_attempted'])
        self.assertEqual(_journal_result_status(result), 'not_saved')
        self.assertEqual(_journal_result_status({'ok': False, 'status': 'stale_market_context'}), 'uncertain')


class VoiceFinalizationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    review = fixture.ProgressiveJournalTests.review
    handlers = fixture.ProgressiveJournalTests.handlers
    call = fixture.ProgressiveJournalTests.call
    stage = fixture.ProgressiveJournalTests.stage
    saved = fixture.ProgressiveJournalTests.saved
    stored = fixture.ProgressiveJournalTests.stored

    async def asyncSetUp(self):
        connect = sqlite3.connect
        with patch('sqlite3.connect', side_effect=lambda *a, **k: connect(*a, **{**k, 'check_same_thread': False})):
            fixture.ProgressiveJournalTests.setUp(self)
        self.draft = self.stage({'asset': 'NAS100', 'direction': 'Bullish', 'play': 'Young Lefty',
            'context_notes': 'Synthetic reflection retained with the whole account history.',
            'reported_entry_time_text': 'Around ten in the morning',
            'reported_exit_time_text': 'Runner closed the following day', 'reported_outcome': 'win',
            'entries': [{'entry_index': 1, 'entry_model': 'Super Soup', 'status': 'closed',
                         'notes': 'Synthetic first entry, partials, and next-day runner.'}]})
        self.session = NS(member=NS(id=20), market_context=self.context, websocket=object(),
            _voice_turn_count=1, closed=False, authorize_tool=AsyncMock(return_value=None))
        self.context.begin_turn(None)
        self.counter = 0

    async def asyncTearDown(self):
        fixture.ProgressiveJournalTests.tearDown(self)

    def new_turn(self):
        self.session._voice_turn_count += 1; self.context.begin_turn(None)

    async def voice(self, name, args, runner=None, report=True, call_id=None):
        self.counter += 1; call_id = call_id or 'call-' + str(self.counter)
        async def run():
            return self.context.run(name, args, runner or (lambda n, a: coach.coach_tool(self.db, 10, 20, n, a)))
        result = await guarded_voice_tool(self.session, name, args, call_id, run)
        if report: journal_write_result_reported(self.session, name, call_id, result)
        return result

    async def uncertain_finalize(self, committed=False):
        original = journal_finalization.prepare_story_finalization
        self.recovery_available = False
        def prepare(*args):
            read = original(*args)
            def recover():
                if not self.recovery_available: raise OSError('Synthetic temporary receipt read outage.')
                return read()
            return recover
        def fail(name, args):
            if committed:
                with patch('gbop_voice_web.voice_runtime.journal_write_committed'):
                    result = coach.coach_tool(self.db, 10, 20, name, args)
                self.assertTrue(result['ok'], result)
            raise OSError('Synthetic uncertain response.')
        with patch.object(journal_finalization, 'prepare_story_finalization', side_effect=prepare):
            with self.assertRaises(OSError):
                await self.voice('save_journal_story', {'draft_id': self.draft['draft_id'], 'confirmation_text': 'Finalize.'}, fail)
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'uncertain')

    async def test_read_reconciles_failed_save_then_explicit_retry_and_next_draft_work(self):
        before = self.stored(self.draft['draft_id'])
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        read = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']}, report=False)
        self.assertEqual(read['journal_write_recovery']['status'], 'not_saved')
        self.assertFalse(self.session._journal_write_recovery['reconciled'])
        journal_write_result_reported(self.session, 'get_journal_story', 'call-2', read)
        self.assertTrue(self.session._journal_write_recovery['reconciled'])
        args = {'draft_id': self.draft['draft_id'], 'confirmation_text': 'Finalize.'}
        same_turn = await self.voice('save_journal_story', args)
        self.assertEqual(same_turn['status'], 'journal_write_reconciliation_required')
        self.new_turn(); saved = await self.voice('save_journal_story', args)
        self.assertTrue(saved['ok'], saved); self.assertEqual(saved['draft_id'], self.draft['draft_id'])
        self.assertEqual(self.stored(self.draft['draft_id'])['raw_story'], before['raw_story'])
        self.assertEqual(self.stored(self.draft['draft_id'])['corrections'], before['corrections'])
        self.assertIsNone(self.conn.execute('SELECT result_r FROM journals').fetchone()[0])
        self.new_turn(); next_draft = await self.voice('stage_journal_story', {'new_draft': True,
            'new_trade': True, 'raw_story': 'Journal a separate new trade for the next day.', 'story_json': '{}'})
        self.assertTrue(next_draft['persisted'], next_draft)
        self.assertNotEqual(next_draft['draft_id'], self.draft['draft_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)

    async def test_ambiguous_commit_read_and_finalize_return_same_record(self):
        await self.uncertain_finalize(committed=True); self.recovery_available = True; self.new_turn()
        read = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertEqual(read['journal_write_recovery']['status'], 'saved')
        self.new_turn()
        result = await self.voice('save_journal_story', {'draft_id': self.draft['draft_id'], 'confirmation_text': 'Finalize.'})
        self.assertTrue(result['ok'], result); self.assertTrue(result['deduplicated'])
        self.assertEqual(result['trade_number'], read['journal_write_recovery']['trade_number'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)

    async def test_failed_reconciliation_keeps_every_write_blocked_without_repeated_reads(self):
        await self.uncertain_finalize(); self.new_turn()
        entry = self.session._journal_write_recovery; entry['reader'] = Mock(side_effect=OSError('outage'))
        for name in sorted(JOURNAL_WRITE_NAMES):
            runner = Mock(); result = await self.voice(name, {}, runner)
            runner.assert_not_called(); self.assertEqual(result['prior_write']['status'], 'uncertain')
        self.assertEqual(entry['reader'].call_count, 1)
        self.new_turn(); await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertEqual(entry['reader'].call_count, 2)

    async def test_pending_writer_and_failed_read_output_never_release_barrier(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        entry = self.session._journal_write_recovery; real = entry['reader']; entry['reader'] = Mock(wraps=real)
        entry['running'] = True
        read = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        entry['reader'].assert_not_called(); self.assertNotIn('journal_write_recovery', read)
        entry['running'] = False; self.new_turn()
        await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']}, report=False)
        self.assertFalse(entry['reconciled'])
        blocked = await self.voice('stage_journal_story', {}, report=False)
        self.assertEqual(blocked['status'], 'journal_write_reconciliation_required')
        self.assertFalse(entry['reconciled'])

    async def test_same_call_id_never_reexecutes_uncertain_or_reconciled_finalize(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        runner = Mock(); again = await self.voice('save_journal_story', {}, runner, call_id='call-1')
        runner.assert_not_called(); self.assertEqual(again['status'], 'not_saved')
        self.new_turn(); again = await self.voice('save_journal_story', {}, runner, call_id='call-1')
        runner.assert_not_called(); self.assertEqual(again['status'], 'not_saved')

    async def test_barge_in_before_dispatch_is_not_a_permanent_uncertain_write(self):
        started = asyncio.Event(); gate = asyncio.Event(); ticket = self.context.generation; writer = Mock()
        async def queued():
            started.set(); await gate.wait()
            return self.context.run('save_journal_story', {'draft_id': self.draft['draft_id']}, writer, generation=ticket)
        task = asyncio.create_task(guarded_voice_tool(self.session, 'save_journal_story', {}, 'interrupted', queued))
        await started.wait(); self.new_turn(); gate.set(); result = await task
        writer.assert_not_called(); self.assertFalse(result['write_attempted'])
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'not_saved')
        await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.new_turn()
        result = await self.voice('save_journal_story', {'draft_id': self.draft['draft_id'], 'confirmation_text': 'Finalize.'})
        self.assertTrue(result['ok'], result)

    async def test_changed_socket_during_recovery_discards_receipt(self):
        await self.uncertain_finalize(); self.new_turn()
        started = threading.Event(); finish = threading.Event()
        def read():
            started.set(); finish.wait(2)
            return {'ok': False, 'saved': False, 'reconciliation_verified': True}
        entry = self.session._journal_write_recovery; entry['reader'] = read
        task = asyncio.create_task(self.voice('get_journal_story', {'draft_id': self.draft['draft_id']}))
        await asyncio.to_thread(started.wait, 2); self.session.websocket = object(); finish.set()
        result = await task
        self.assertNotIn('journal_write_recovery', result); self.assertEqual(entry['outcome']['status'], 'uncertain')

    async def test_timed_out_reader_does_not_fan_out_or_publish_late(self):
        await self.uncertain_finalize(); self.new_turn()
        started = threading.Event(); finish = threading.Event()
        entry = self.session._journal_write_recovery
        def reader():
            started.set(); finish.wait(2)
            return {'ok': False, 'saved': False, 'reconciliation_verified': True}
        entry['reader'] = Mock(side_effect=reader)
        real_wait = asyncio.wait_for
        async def short_wait(awaitable, timeout):
            return await real_wait(awaitable, timeout=.02)
        with patch('gbop_voice_web.voice_runtime.asyncio.wait_for', side_effect=short_wait):
            await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertTrue(started.is_set()); self.assertTrue(entry['recovery_running'])
        self.new_turn(); await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertEqual(entry['reader'].call_count, 1)
        finish.set()
        for _ in range(100):
            if not entry['recovery_running']: break
            await asyncio.sleep(.001)
        self.assertFalse(entry['recovery_running'])
        self.assertEqual(entry['outcome']['status'], 'uncertain')
        self.new_turn(); result = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertEqual(entry['reader'].call_count, 2)
        self.assertEqual(result['journal_write_recovery']['status'], 'not_saved')

    async def test_closed_context_during_recovery_hides_cached_read(self):
        await self.uncertain_finalize(); self.new_turn()
        self.session._tool_call_results['cached-read'] = {'ok': True, 'story': 'Synthetic private cached text.'}
        def reader():
            self.context.closed = True
            return {'ok': False, 'saved': False, 'reconciliation_verified': True}
        entry = self.session._journal_write_recovery; entry['reader'] = reader
        result = await self.voice('get_journal_story', {}, call_id='cached-read')
        self.assertNotIn('story', result); self.assertFalse(entry['reconciled'])

    async def test_failed_read_never_gets_private_recovery_metadata(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        result = await self.voice('get_journal_story', {}, lambda n, a: {'ok': False, 'error': 'Access revoked.'})
        self.assertEqual(result, {'ok': False, 'error': 'Access revoked.'})
        self.assertFalse(self.session._journal_write_recovery['reconciled'])

    async def test_revocation_during_normal_read_prevents_receipt_delivery(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        self.session.authorize_tool.side_effect = [None, None, 'Access revoked.']
        result = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertEqual(result, {'ok': False, 'error': 'Access revoked.'})
        self.assertFalse(self.session._journal_write_recovery['reconciled'])

    async def test_revocation_cannot_release_cached_private_read(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        self.session._tool_call_results['cached-read'] = {'ok': True, 'story': 'Synthetic private cached text.'}
        self.session.authorize_tool.side_effect = [None, 'Access revoked.']
        result = await self.voice('get_journal_story', {}, call_id='cached-read')
        self.assertEqual(result, {'ok': False, 'error': 'Access revoked.'})
        self.assertFalse(self.session._journal_write_recovery['reconciled'])

    async def test_projection_that_omits_receipt_does_not_release_fence(self):
        from gbop_voice_web.voice_payload import voice_tool_payload
        from pathlib import Path
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        read = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']}, report=False)
        raw = {'ok': True, 'padding': 'x' * 50000,
               'journal_write_recovery': read['journal_write_recovery']}
        presented = voice_tool_payload('get_journal_history', raw)
        self.assertNotIn('journal_write_recovery', presented)
        journal_write_result_reported(self.session, 'get_journal_history', 'large-read', presented)
        self.assertFalse(self.session._journal_write_recovery['reconciled'])
        self.assertIn('journal_write_result_reported(self, name, call_id, voice_result)',
                      (Path(__file__).resolve().parents[1] / 'bot.py').read_text())

    async def test_revocation_after_read_discards_receipt(self):
        await self.uncertain_finalize(); self.recovery_available = True; self.new_turn()
        self.session.authorize_tool.side_effect = [None, 'Access revoked.']
        result = await self.voice('get_journal_story', {'draft_id': self.draft['draft_id']})
        self.assertNotIn('journal_write_recovery', result)
        self.assertEqual(self.session._journal_write_recovery['outcome']['status'], 'uncertain')


if __name__ == '__main__': unittest.main()
