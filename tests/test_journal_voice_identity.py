"""Synthetic voice identity recovery; no production narration or network."""
import json
import unittest
from gbop_voice_web.journal_story import _new_trade_request
import test_journal_story as fixture


class VoiceIdentityRecoveryTests(unittest.TestCase):
    setUp = fixture.JournalStoryTests.setUp
    tearDown = fixture.JournalStoryTests.tearDown
    call = fixture.JournalStoryTests.call
    handlers = fixture.JournalStoryTests.handlers
    review = fixture.JournalStoryTests.review

    def test_missing_spoken_identity_returns_recovery_without_touching_prior_draft(self):
        old = self.call('stage_journal_story', {'story_json':'{"asset":"NAS100"}'})
        before = list(self.conn.iterdump())
        failed = self.call('stage_journal_story', {
            'new_draft':True, 'new_trade':True,
            'raw_story':'I traded gold and lost. My exact entry and R are unknown.',
            'story_json':'{"asset":"XAUUSD","reported_outcome":"loss"}'}, text=None)
        self.assertEqual(failed['status'], 'new_draft_intent_required', failed)
        self.assertFalse(failed['persisted'])
        self.assertFalse(failed['write_attempted'])
        self.assertNotIn('draft_id', failed)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertEqual(self.context._journal_story['id'], old['draft_id'])
        # A faithful repair supplies the previously omitted member request.
        fixed = self.call('stage_journal_story', {
            'new_draft':True, 'new_trade':True,
            'raw_story':'Journal a separate real trade. I traded gold and lost. My exact entry and R are unknown.',
            'story_json':json.dumps({'asset':'XAUUSD','reported_outcome':'loss','trade_date':'2040-02-07'})}, text=None)
        self.assertTrue(fixed['ok'], fixed)
        self.assertTrue(fixed['persisted'])
        self.assertNotEqual(fixed['draft_id'], old['draft_id'])
        saved = self.call('save_journal_story', {'draft_id':fixed['draft_id'],
            'confirmation_text':'Save this journal.'}, text=None)
        self.assertTrue(saved['ok'], saved)
        self.assertIsNone(saved['result_r'])
        again = self.call('save_journal_story', {'draft_id':fixed['draft_id'],
            'confirmation_text':'Save this journal.'}, text=None)
        self.assertTrue(again['deduplicated'], again)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)
        prior = self.call('get_journal_story', {'draft_id':old['draft_id']}, text=None)
        self.assertEqual(prior['story']['asset'], 'NAS100')

    def test_real_and_actual_trade_wording_retains_negation_guard(self):
        for adjective in ('real', 'actual', 'reported'):
            self.assertTrue(_new_trade_request(f'This is a separate {adjective} trade.'))
            self.assertFalse(_new_trade_request(f'This is not a separate {adjective} trade.'))
            self.assertFalse(_new_trade_request(f'Do not start another {adjective} trade.'))

    def test_negative_new_trade_does_not_persist_an_extra_draft(self):
        self.call('stage_journal_story', {'story_json':'{"asset":"NAS100"}'})
        before = list(self.conn.iterdump())
        failed = self.call('stage_journal_story', {'new_draft':True,'new_trade':True,
            'raw_story':'This is not a separate actual trade.', 'story_json':'{}'}, text=None)
        self.assertEqual(failed['status'], 'new_draft_intent_required')
        self.assertEqual(before, list(self.conn.iterdump()))


if __name__ == '__main__':
    unittest.main()
