"""Composed journal cycles and actual SQLite connection reopening, synthetic only.

The database is a temporary file. PostgreSQL advisory locks/RLS statements still
use the existing SQLite adapter; these tests make no PostgreSQL coverage claim.
Tool arguments stand in for model extraction. No network or real members.
"""
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach
import test_contextual_journals as storage_fixture
import test_journal_lifecycle_acceptance as lifecycle_fixture


class JournalLifecyclePersistenceTests(unittest.TestCase):
    review = storage_fixture.ContextualJournalTests.review
    handlers = storage_fixture.ContextualJournalTests.handlers
    fresh = lifecycle_fixture.JournalLifecycleAcceptanceTests.fresh
    stage = lifecycle_fixture.JournalLifecycleAcceptanceTests.stage
    save = lifecycle_fixture.JournalLifecycleAcceptanceTests.save
    stored = lifecycle_fixture.JournalLifecycleAcceptanceTests.stored
    history = lifecycle_fixture.JournalLifecycleAcceptanceTests.history
    count = lifecycle_fixture.JournalLifecycleAcceptanceTests.count

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='gbop-journal-test-')
        self.addCleanup(temporary.cleanup)
        self.database_path = Path(temporary.name).resolve() / 'synthetic-journals.sqlite3'
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in journal persistence tests'))
        guard.start()
        self.addCleanup(guard.stop)
        connect = sqlite3.connect
        # Reuse only the synthetic schema/adapter. The replacement returns a
        # real file-backed sqlite3 connection, not a fake storage implementation.
        with patch.object(sqlite3, 'connect', side_effect=lambda *a, **k: connect(self.database_path, **k)):
            storage_fixture.ContextualJournalTests.setUp(self)
        self.addCleanup(lambda: self.conn.close())
        self.context.close()
        self.context = self.fresh()
        self.addCleanup(lambda: self.context.close())
        self.envs = {path: self.handlers(path) for path in ('bot.py', 'gbop_voice_web/server.py')}
        self.tool_calls = []
        self.assertEqual(Path(self.conn.execute('PRAGMA database_list').fetchone()[2]), self.database_path)

    def action(self, name, args, text='Journal my reported trade.', **kwargs):
        self.tool_calls.append(name)
        return lifecycle_fixture.JournalLifecycleAcceptanceTests.action(self, name, args, text, **kwargs)

    def reopen(self):
        old = self.conn
        self.assertFalse(old.in_transaction, 'Reopen must not hide an uncommitted write.')
        self.context.close()
        old.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            old.execute('SELECT 1')
        self.assertGreater(self.database_path.stat().st_size, 0)
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.context = self.fresh(surface='reopened_database')
        self.envs = {path: self.handlers(path) for path in self.envs}
        self.assertIsNot(self.conn, old)
        self.assertEqual(Path(self.conn.execute('PRAGMA database_list').fetchone()[2]), self.database_path)

    def identity(self):
        row = self.conn.execute('SELECT id,thesis_id FROM journals').fetchone()
        return tuple(row) if row else None

    def assert_identity(self, identity, draft_id, executions):
        self.assertEqual(self.identity(), identity)
        self.assertEqual((self.count('theses'), self.count('journals'), self.count('journal_story_drafts'),
                          self.count('thesis_executions')), (1, 1, 1, executions))
        row = self.conn.execute('SELECT id,journal_id,thesis_id FROM journal_story_drafts').fetchone()
        self.assertEqual(tuple(row), (draft_id, *identity))
        meta = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(json.loads(meta['journal_story'])['draft_id'], draft_id)

    def composed_cycle(self, path):
        draft = self.stage({'play': 'Young Lefty', 'reported_outcome': 'open',
            'entries': [{'entry_index': 1, 'entry_model': 'Soup', 'risk_r': .2, 'status': 'open'}]},
            'Journal my NAS trade. I entered long with 0.2R risk.')
        self.assertTrue(draft['persisted'], draft)
        self.assertEqual((self.count('journals'), self.count('thesis_executions')), (0, 0))
        saved = self.save(draft)
        number, draft_id, identity = saved['trade_number'], draft['draft_id'], self.identity()
        self.assert_identity(identity, draft_id, 0)

        opened = self.action('open_trade', dict(trade_id=number, asset='NAS100', direction='Bullish',
            play='Young Lefty', entry_model='Soup', tier=1, risk_r=.2, market_reference='none'),
            f'Record my reported execution on Trade #{number}, risk 0.2R.', path=path, operation_id='opening')
        self.assertTrue(opened['ok'], opened)
        self.assertEqual(opened['trade_id'], number)
        self.assert_identity(identity, draft_id, 1)

        additions = []
        add_args = dict(trade_id=number, entry_model='Soup', tier=1, risk_r=.1, market_reference='none')
        for index in (2, 3):
            added = self.action('add_entry', add_args, f'I added entry {index} with 0.1R risk.',
                                path=path, operation_id=f'entry-{index}')
            self.assertTrue(added['ok'], added)
            additions.append(added)
            self.assert_identity(identity, draft_id, index)
        self.assertNotEqual(additions[0]['execution_id'], additions[1]['execution_id'])
        expanded = self.stage({'entries': [{'entry_index': i, 'entry_model': 'Soup', 'risk_r': .1,
            'status': 'open'} for i in (2, 3)]}, 'Add the two entries to this same journal.', draft_id=draft_id)
        self.save(expanded)
        self.assertEqual([e['entry_index'] for e in self.stored(draft)['values']['entries']], [1, 2, 3])

        correction = self.action('save_journal_entry', {'trade_number': number,
            'metadata_json': '{"direction":"Bearish"}'}, f'Correct Trade #{number}: I was short, not long.')
        self.assertTrue(correction['ok'], correction)
        refreshed = self.action('get_journal_story', {'draft_id': draft_id}, 'Read the corrected journal.')
        self.assertEqual(refreshed['story']['direction'], 'Bearish')
        changed = self.stage({'direction': 'Bullish', 'context_notes': 'All three entries were the same short thesis.'},
            'Keep the three entries together.', draft_id=draft_id)
        self.assertEqual(changed['story']['direction'], 'Bearish')

        # Lose the save reply after a real commit, with the normal callback
        # suppressed. Reconciliation must use stored identity/revision evidence.
        self.context.begin_turn('Save this journal.')
        calls = []
        def lose_reply(name, args):
            calls.append(name)
            with patch('gbop_voice_web.voice_runtime.journal_write_committed'):
                result = coach.coach_tool(self.db, 10, 20, name, args)
            self.assertTrue(result['ok'], result)
            raise RuntimeError('Synthetic reply lost after commit')
        save_args = {'draft_id': draft_id}
        recovered = self.context.run('save_journal_story', save_args, lose_reply)
        self.assertTrue(recovered['reconciliation_verified'], recovered)
        before_retry = list(self.conn.iterdump())
        self.assertEqual(self.context.run('save_journal_story', save_args, lose_reply), recovered)
        self.assertEqual(calls, ['save_journal_story'])
        self.assertEqual(list(self.conn.iterdump()), before_retry)
        self.assert_identity(identity, draft_id, 3)
        before_retry = list(self.conn.iterdump())
        replay = self.action('add_entry', add_args, 'Check that the third entry saved.',
                             path=path, operation_id='entry-3')
        self.assertEqual(replay['execution_id'], additions[1]['execution_id'])
        self.assertEqual(list(self.conn.iterdump()), before_retry)

        partial = self.action('record_trade_event', dict(trade_id=number, event='Partial exit',
            details='Entries one and two closed; entry three still has a runner.', result_r=None,
            market_reference='none'), 'I partially exited and left a runner.', path=path)
        self.assertTrue(partial['ok'], partial)
        partial_story = self.stage({'reported_outcome': 'open', 'entries': [
            {'entry_index': 1, 'status': 'closed'}, {'entry_index': 2, 'status': 'closed'},
            {'entry_index': 3, 'status': 'partially_closed', 'notes': 'Runner still open.'}]},
            'Entries one and two closed; the third runner remains open.', draft_id=draft_id)
        self.save(partial_story)
        partial_row = self.history(number)['journals'][0]
        self.assertEqual(partial_row['trade_facts']['status'], 'OPEN')
        self.assertIsNone(partial_row['result_r'])
        self.assertEqual(self.stored(draft)['values']['entries'][2]['status'], 'partially_closed')
        self.assert_identity(identity, draft_id, 3)

        closed = self.action('close_trade', dict(trade_id=number, final_result_r=None, rule_adherence='unknown',
            summary='Closed the remaining runner; result R unknown.', study_note='Review the same short thesis.',
            market_reference='none'), 'I closed the remaining runner; all entries are closed.', path=path)
        self.assertTrue(closed['ok'], closed)
        finished = self.stage({'reported_outcome': 'unknown', 'entries': [
            {'entry_index': i, 'status': 'closed', 'notes': 'Confirmed closed; no remaining runner.'} for i in (1, 2, 3)]},
            'All three entries are closed. Save this journal with result R unknown.', draft_id=draft_id)
        self.assertEqual(self.save(finished)['trade_number'], number)
        self.assert_identity(identity, draft_id, 3)
        before_recall = list(self.conn.iterdump())
        row = self.history(number, context=self.fresh())['journals'][0]
        self.assertEqual(row['trade_number'], number)
        self.assertEqual(row['trade_facts']['direction'], 'Bearish')
        self.assertEqual(row['trade_facts']['status'], 'CLOSED')
        self.assertIsNone(row['result_r'])
        story = json.loads(row['metadata']['journal_story'])
        self.assertEqual([e['entry_index'] for e in story['entries']], [1, 2, 3])
        self.assertEqual([e['status'] for e in story['entries']], ['closed'] * 3)
        self.assertIn('NAS100 · Bearish', row['summary'])
        self.assertEqual(list(self.conn.iterdump()), before_recall)

    def test_composed_discord_cycle_preserves_identity_and_retries(self):
        self.composed_cycle('bot.py')

    def test_composed_web_cycle_preserves_identity_and_retries(self):
        self.composed_cycle('gbop_voice_web/server.py')

    def test_narrative_saves_and_reports_close_with_both_r_values_unknown(self):
        draft = self.stage({'entries': [{'entry_index': 1, 'entry_model': 'Soup', 'status': 'open'}]},
            'Journal my NAS trade. I entered short. I do not know my opening risk R or result R.')
        saved = self.save(draft)
        identity = self.identity()
        self.assertIsNone(saved['result_r'])
        self.assertIsNone(saved['metadata']['recorded_risk'])
        self.assertNotIn('risk_r', self.stored(draft)['values']['entries'][0])
        closed = self.stage({'reported_outcome': 'unknown', 'entries': [{'entry_index': 1, 'status': 'closed'}]},
            'The trade is now fully closed. Risk and result R are still unknown. Save this journal.',
            draft_id=draft['draft_id'])
        final = self.save(closed)
        self.assertEqual(final['trade_number'], saved['trade_number'])
        self.assert_identity(identity, draft['draft_id'], 0)
        row = self.history(saved['trade_number'])['journals'][0]
        story = json.loads(row['metadata']['journal_story'])
        self.assertEqual(story['entries'][0]['status'], 'closed')
        self.assertNotIn('risk_r', story['entries'][0])
        self.assertIsNone(story.get('result_r'))
        self.assertIsNone(row['result_r'])
        self.assertIsNone(row['metadata']['recorded_risk'])
        self.assertIsNone(self.conn.execute('SELECT final_result_r FROM theses').fetchone()[0])
        self.assertIn('entry risk: unknown', row['summary'])
        self.assertIn('Overall result: R unknown', row['summary'])
        self.assertEqual(row['trade_facts']['status'], 'JOURNALED')  # Narrative, not an invented execution ledger.
        self.assertFalse(set(self.tool_calls) & {'open_trade', 'add_entry', 'close_trade'})
        performance = coach.performance(self.db, 10, 20, {})['summary']
        self.assertEqual((performance['known_outcomes'], performance['missing_outcomes'], performance['breakeven']), (0, 1, 0))

    def test_file_reopen_retains_unfinished_correction_finalization_and_retry(self):
        raw = 'Journal my NAS trade. I entered long and have not finished the account.'
        draft = self.stage({'entries': [{'entry_index': 1, 'notes': 'Risk R unknown.'}]}, raw)
        first_payload = self.stored(draft)
        self.reopen()
        resumed = self.action('get_journal_story', {}, 'Read my unfinished journal.')
        self.assertEqual(resumed['draft_id'], draft['draft_id'])
        self.assertIn(raw, resumed['raw_story_text'])
        self.assertEqual(resumed['story'], first_payload['values'])
        self.assertEqual(self.count('journals'), 0)
        corrected = self.stage({}, 'Correction: I was short, not long.', draft_id=draft['draft_id'])
        self.assertEqual(corrected['story']['direction'], 'Bearish')
        corrected_payload = self.stored(draft)
        self.assertTrue(corrected_payload['corrections'])
        self.reopen()
        resumed = self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read my unfinished journal.')
        self.assertEqual(resumed['story']['direction'], 'Bearish')
        self.assertEqual(self.stored(draft)['corrections'], corrected_payload['corrections'])
        self.assertEqual(self.stored(draft)['raw_story'], corrected_payload['raw_story'])
        saved = self.save(resumed)
        identity = self.identity()
        finalized_payload = self.stored(draft)
        self.reopen()
        self.assertEqual(self.stored(draft)['draft_status'], 'finalized')
        self.assertEqual(self.stored(draft)['values'], finalized_payload['values'])
        self.assertEqual(self.stored(draft)['corrections'], finalized_payload['corrections'])
        before_retry = list(self.conn.iterdump())
        retried = self.save(draft)
        self.assertTrue(retried['deduplicated'], retried)
        self.assertEqual(retried['trade_number'], saved['trade_number'])
        self.assertEqual(list(self.conn.iterdump()), before_retry)
        self.assert_identity(identity, draft['draft_id'], 0)
        row = self.history(saved['trade_number'])['journals'][0]
        self.assertEqual(row['trade_facts']['direction'], 'Bearish')
        self.assertIsNone(row['result_r'])
        self.assertIsNone(row['metadata']['recorded_risk'])

    def test_numbered_continuation_after_reopening_reuses_saved_draft(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        saved = self.save(draft)
        identity = self.identity()
        self.reopen()
        text = f'Continue my journal for Trade #{saved["trade_number"]}. I felt calm at the entry.'
        self.context.begin_turn(text)
        self.assertIsNone(self.context._journal_capture_error)
        self.assertEqual(self.context._journal_story['id'], draft['draft_id'])
        self.assertIn(text, [p['text'] for p in self.stored(draft)['raw_story']])
        self.assertEqual(self.stored(draft)['draft_status'], 'unfinished')
        self.assert_identity(identity, draft['draft_id'], 0)
        self.assertEqual(self.save(draft)['trade_number'], saved['trade_number'])

    def test_numbered_continuation_selects_owned_record_without_retargeting_another(self):
        first = self.stage(text='Journal my NAS trade. I entered short.')
        self.save(first)
        second = self.stage({'asset': 'EURUSD', 'direction': 'Bullish'},
            'Journal a separate trade on EURUSD. I entered long.', new_draft=True, new_trade=True)
        self.save(second)
        second_before = self.stored(second)
        text = 'Continue my journal for Trade #1. I felt calm at the entry.'
        self.context.begin_turn(text)
        self.assertIsNone(self.context._journal_capture_error)
        self.assertEqual(self.context._journal_story['id'], first['draft_id'])
        self.assertIn(text, [p['text'] for p in self.stored(first)['raw_story']])
        self.assertEqual(self.stored(second), second_before)
        self.assertEqual((self.count('theses'), self.count('journals'), self.count('journal_story_drafts')), (2, 2, 2))

    def test_unresolved_or_multiple_trade_numbers_do_not_change_storage(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        self.save(draft)
        for text in ('Continue my journal for Trade #99. I felt calm at the entry.',
                     'Continue my journal for Trade #1 or Trade #2. I am unsure which.'):
            with self.subTest(text=text):
                before = list(self.conn.iterdump())
                self.context.begin_turn(text)
                self.assertTrue(self.context._journal_capture_error)
                self.assertEqual(list(self.conn.iterdump()), before)
                self.assertEqual(self.context._journal_story['id'], draft['draft_id'])

    def test_numbered_continuation_is_member_scoped(self):
        first = self.stage({'context_notes': 'First member private account.'},
            'Journal my NAS trade. I entered short.')
        self.save(first)
        first_before = self.stored(first)
        other = self.fresh(user=30)
        self.addCleanup(other.close)
        second = self.stage({'context_notes': 'Second member private account.'},
            'Journal my NAS trade. I entered long.', context=other, user=30)
        self.assertEqual(self.save(second, context=other, user=30)['trade_number'], 1)
        text = 'Continue my journal for Trade #1. I felt calm at the entry.'
        other.begin_turn(text)
        self.assertIsNone(other._journal_capture_error)
        self.assertEqual(other._journal_story['id'], second['draft_id'])
        self.assertIn(text, [p['text'] for p in self.stored(second, user=30)['raw_story']])
        self.assertNotIn('First member private account.', str(other._journal_story))
        self.assertEqual(self.stored(first), first_before)
        self.assertEqual((self.count('theses'), self.count('journals'), self.count('journal_story_drafts')), (2, 2, 2))


if __name__ == '__main__':
    unittest.main()
