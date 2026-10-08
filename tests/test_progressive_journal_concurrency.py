"""Deterministic lock-order and rollback tests; no network or member database."""
from contextlib import contextmanager
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_context import JournalBinding, journal_transaction
from gbop_voice_web.market_conversation import MarketConversation
import test_progressive_journal as fixture


class ProgressiveJournalLockTests(unittest.TestCase):
    def contend_with_existing_write(self, action):
        """Old write owns the member lock when the transport callback starts.

        The old write reaches the conversation guard only after the callback
        requests that same member lock. An inverted conversation->member order
        therefore deterministically times out rather than hanging the suite.
        """
        member_lock = threading.Lock()
        old_has_member_lock = threading.Event()
        callback_requests_member_lock = threading.Event()
        timeouts = []
        old_errors = []
        admitted = []

        class Connection:
            held = False

            def execute(self, sql, params=()):
                if sql == 'SELECT pg_advisory_xact_lock(?)':
                    if threading.current_thread().name == 'prior-journal-write':
                        member_lock.acquire()
                        self.held = True
                        old_has_member_lock.set()
                        if not callback_requests_member_lock.wait(2):
                            raise RuntimeError('The callback never reached its member lock.')
                    else:
                        callback_requests_member_lock.set()
                        if not member_lock.acquire(timeout=2):
                            timeouts.append('Conversation/member lock order was inverted.')
                            raise RuntimeError(timeouts[-1])
                        self.held = True
                return self

            def fetchall(self):
                return []  # This fixture exercises journal locks without continuity schema.

            def fetchone(self):
                return {'activated': 1, 'revoked': 0, 'leadership_ack': 1,
                        'updated_at': 'synthetic-auth-v1'}

        @contextmanager
        def db():
            connection = Connection()
            try:
                yield connection
            finally:
                if connection.held:
                    member_lock.release()

        context = MarketConversation((10, 20, 'synthetic-lock-test'),
                                     auth_provider=(db, 10, 20))
        context.generation = 1
        old_binding = JournalBinding(context, 1)

        def prior_write():
            try:
                with journal_transaction(db, {'_journal_binding': old_binding},
                                         10, 20, serialize=True):
                    admitted.append('prior')
            except Exception as exc:
                old_errors.append(exc)

        worker = threading.Thread(target=prior_write, name='prior-journal-write', daemon=True)
        worker.start()
        self.assertTrue(old_has_member_lock.wait(2))
        try:
            action(context, db, admitted)
        finally:
            # Also release the deterministic wait if the callback failed early.
            callback_requests_member_lock.set()
            worker.join(3)
        self.assertFalse(worker.is_alive(), 'The prior write did not finish.')
        self.assertFalse(timeouts, timeouts)
        self.assertTrue(callback_requests_member_lock.is_set())
        return context, admitted, old_errors

    def test_begin_turn_waits_for_member_lock_without_holding_conversation_lock(self):
        def action(context, db, admitted):
            def stage(db, guild, user, args):
                with journal_transaction(db, args, guild, user, serialize=True):
                    admitted.append('new-capture')
                return {'ok': True}

            with patch('gbop_voice_web.journal_coach.init_coach'), \
                    patch('gbop_voice_web.journal_story.stage_story', side_effect=stage) as capture:
                context.begin_turn('Journal my NAS trade. I entered short at nine.')
            self.assertEqual(capture.call_count, 1)

        context, admitted, errors = self.contend_with_existing_write(action)
        self.assertEqual(admitted, ['new-capture'])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn('current authenticated conversation', str(errors[0]))
        self.assertIsNone(getattr(context, '_journal_capture_error', None))

    def test_delivered_question_waits_for_member_lock_without_holding_conversation_lock(self):
        def action(context, db, admitted):
            question = 'What day was this trade?'
            draft = {'id': 'a' * 32, 'owner': (10, 20, context.session_id),
                     'persisted': True, 'draft_status': 'unfinished', 'storage_revision': 1,
                     'asked': [], 'question': {'key': 'chronology:date', 'text': question,
                                              'generation': context.generation}}
            context._journal_story = draft

            def write(conn, guild, user, value, **kwargs):
                admitted.append('question-receipt')
                return {**deepcopy(value), 'storage_revision': 2}

            durable_draft = deepcopy(draft)  # DB state is distinct from immediately admitted local state.
            with patch.object(journal_drafts, 'read', side_effect=lambda *a: [deepcopy(durable_draft)]), \
                    patch.object(journal_drafts, 'write', side_effect=write):
                context.complete_response(question, generation=1, response_id='delivered-1')

        context, admitted, errors = self.contend_with_existing_write(action)
        self.assertFalse(errors, errors)
        self.assertEqual(admitted, ['prior', 'question-receipt'])
        self.assertEqual(context._journal_story['asked'], ['chronology:date'])
        self.assertEqual(context._journal_story['storage_revision'], 2)


class ProgressiveJournalRollbackTests(unittest.TestCase):
    def test_finalization_storage_failure_rolls_back_canonical_records(self):
        case = fixture.ProgressiveJournalTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        draft = case.stage()
        before = case.stored(draft['draft_id'])
        real_write = journal_drafts.write

        def fail_finalized_write(conn, guild, user, value, **kwargs):
            if kwargs.get('finalized'):
                raise RuntimeError('Synthetic finalization storage failure.')
            return real_write(conn, guild, user, value, **kwargs)

        with patch.object(journal_drafts, 'write', side_effect=fail_finalized_write):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic finalization'):
                case.saved(draft)

        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0], 0)
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)
        self.assertEqual(case.stored(draft['draft_id']), before)
        # A new authenticated attempt after the rollback creates exactly one
        # canonical narrative, preserving its original durable identity.
        case.context = case.fresh()
        saved = case.saved(draft)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(saved['draft_id'], draft['draft_id'])
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0], 1)
        self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
