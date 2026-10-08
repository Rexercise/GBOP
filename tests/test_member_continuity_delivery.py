"""Split local delivery admission and durable receipts; no real member data."""
from copy import deepcopy
from dataclasses import replace
import unittest

from gbop_voice_web import journal_drafts, member_continuity
from gbop_voice_web.market_conversation import MarketConversation
import test_progressive_journal as journal_fixture
import test_feeling_conversation as feeling_fixture


class SplitMemberDeliveryTests(unittest.TestCase):
    def fixture(self, kind=journal_fixture.ProgressiveJournalTests):
        case = kind()
        case.setUp()
        self.addCleanup(case.tearDown)
        member_continuity.init_continuity(case.db)
        return case

    def test_immediate_yes_uses_delivered_clarification_before_persistence_worker_runs(self):
        case = self.fixture(feeling_fixture.FeelingConversationTests)
        case.opened()
        case.context.begin_turn(feeling_fixture.NOTE)
        receipt = case.context.admit_response(feeling_fixture.QUESTION, response_id='heard-question')
        self.assertIsNotNone(receipt)
        self.assertEqual(case.context._feeling_clarification['stage'], 'mid')
        # The persistence worker is deliberately not run until after immediate Yes.
        case.context.begin_turn('Yes')
        result = case.run_save()
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(case.metadata()['feeling_history']), 1)
        case.context.persist_response(receipt)
        self.assertEqual(len(case.metadata()['feeling_history']), 1)
        case.context.begin_turn('Yes')
        self.assertFalse(case.run_save()['ok'])
        self.assertEqual(len(case.metadata()['feeling_history']), 1)

    def test_next_story_turn_keeps_locally_admitted_question_before_worker(self):
        case = self.fixture()
        first = case.stage()
        key = case.context._journal_story['question']['key']
        receipt = case.context.admit_response(first['next_question'], response_id='heard-journal-question')
        self.assertIn(key, case.context._journal_story['asked'])
        self.assertNotIn(key, case.stored(first['draft_id'])['asked'])
        next_result = case.stage({'entries': [{'entry_index': 3, 'notes': 'A newer member correction.'}]},
                                 draft_id=first['draft_id'])
        self.assertNotEqual(next_result['next_question'], first['next_question'])
        case.context.persist_response(receipt)
        stored = case.stored(first['draft_id'])
        self.assertIn(key, stored['asked'])
        self.assertEqual(stored['values']['entries'][2]['notes'], 'A newer member correction.')

    def test_delivered_key_merges_after_other_context_edit_without_overwriting_facts(self):
        case = self.fixture()
        first = case.stage()
        key = case.context._journal_story['question']['key']
        receipt = case.context.admit_response(first['next_question'], response_id='question-before-handoff')
        other = MarketConversation((10, 20, 'helper2'), auth_provider=(case.db, 10, 20))
        result = case.call('stage_journal_story', {'draft_id': first['draft_id'],
            'story_json': '{"context_notes":"Newer helper correction must survive."}'},
            text='Correct my journal context.', context=other)
        self.assertTrue(result['ok'], result)
        before = case.stored(first['draft_id'])
        case.context.persist_response(receipt)
        after = case.stored(first['draft_id'])
        self.assertEqual(after['values'], before['values'])
        self.assertEqual(after['raw_story'], before['raw_story'])
        self.assertEqual(after['corrections'], before['corrections'])
        self.assertIn(key, after['asked'])
        self.assertEqual(after['substantive_updated_at'], before['substantive_updated_at'])

    def test_deletion_pause_revocation_and_replacement_reject_pending_receipt(self):
        for change in ('delete', 'pause', 'revoke', 'replace'):
            with self.subTest(change=change):
                case = self.fixture()
                first = case.stage()
                key = case.context._journal_story['question']['key']
                receipt = case.context.admit_response(first['next_question'], response_id='pending-' + change)
                if change == 'delete':
                    case.conn.execute('DELETE FROM journal_story_drafts WHERE id=?', (first['draft_id'],))
                    case.conn.commit()
                    with case.db() as conn:
                        member_continuity.clear_record_context(conn, 10, 20)
                elif change == 'pause':
                    case.context.begin_turn('Do not record this conversation.')
                elif change == 'revoke':
                    case.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
                    case.conn.commit()
                else:
                    case.context.close()
                before = list(case.conn.iterdump())
                case.context.persist_response(receipt)
                self.assertEqual(list(case.conn.iterdump()), before)
                if change != 'delete':
                    self.assertNotIn(key, case.stored(first['draft_id'])['asked'])

    def test_duplicate_or_forged_receipt_cannot_replay_or_change_text(self):
        case = self.fixture()
        case.context.begin_turn('A short private topic.')
        receipt = case.context.admit_response('Delivered response.', response_id='one')
        self.assertIsNone(case.context.admit_response('Duplicate response.', response_id='one'))
        self.assertEqual(case.context.persist_response(replace(receipt, text='Forged replacement.')), 0)
        case.context.persist_response(receipt)
        before = list(case.conn.iterdump())
        case.context.persist_response(receipt)
        self.assertEqual(list(case.conn.iterdump()), before)
        self.assertIs(case.context._completed_responses[(receipt.generation, receipt.response_id)], True)
        other = MarketConversation((10, 30, 'other-member'), auth_provider=(case.db, 10, 30))
        self.assertEqual(other.persist_response(receipt), 0)
        self.assertEqual(list(case.conn.iterdump()), before)

    def test_incomplete_or_stale_delivery_cannot_create_admission(self):
        case = self.fixture()
        first = case.stage()
        ticket = case.context.generation
        self.assertIsNone(case.context.admit_response(first['next_question'], completed=False))
        case.context.begin_turn('New turn.')
        self.assertIsNone(case.context.admit_response(first['next_question'], generation=ticket))
        self.assertEqual(case.stored(first['draft_id'])['asked'], [])


if __name__ == '__main__':
    unittest.main()
