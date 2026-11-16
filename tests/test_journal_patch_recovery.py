"""Synthetic voice patch failures; no production member data."""
import json
import unittest
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture


class PatchRecoveryTests(unittest.TestCase):
    setUp=fixture.JournalStoryTests.setUp
    tearDown=fixture.JournalStoryTests.tearDown
    call=fixture.JournalStoryTests.call
    handlers=fixture.JournalStoryTests.handlers
    review=fixture.JournalStoryTests.review

    def start(self):
        return self.call('stage_journal_story',{'story_json':'{"asset":"NAS100"}',
            'raw_story':'Journal my NAS100 trade. I entered long and then exited.',
            'new_trade':True},text=None)

    def test_bad_voice_patch_without_raw_returns_retained_owned_draft(self):
        first=self.start()
        before=list(self.conn.iterdump())
        rejected=self.call('stage_journal_story',{'draft_id':first['draft_id'],
            'story_json':'{"executions":[]}', 'new_trade':True},text=None)
        self.assertEqual(rejected['status'],'structured_patch_rejected',rejected)
        self.assertEqual(rejected['draft_id'],first['draft_id'])
        self.assertFalse(rejected['rejected_patch_applied'])
        self.assertEqual(rejected['story']['asset'],'NAS100')
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertIn('entries',rejected['story_fields'])
        self.assertIn('entry_index',rejected['entry_fields'])
        fixed=self.call('stage_journal_story',{'draft_id':first['draft_id'],
            'raw_story':'This is a separate new trade.', 'story_json':'{}'},text=None)
        self.assertTrue(fixed['ok'],fixed)
        saved=self.call('save_journal_story',{'draft_id':first['draft_id'],
            'confirmation_text':'Save it.'},text=None)
        self.assertTrue(saved['ok'],saved)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)

    def test_bad_patch_retains_new_voice_passage_but_not_bad_fields(self):
        first=self.start()
        rejected=self.call('stage_journal_story',{'draft_id':first['draft_id'],
            'raw_story':'My exit was at the midpoint.',
            'story_json':'{"executions":[]}'},text=None)
        self.assertEqual(rejected['status'],'narration_saved_needs_correction')
        self.assertNotIn('executions',rejected['story'])
        self.assertFalse(rejected['rejected_patch_applied'])
        self.assertIn('My exit was at the midpoint.',str(rejected))

    def test_foreign_draft_never_revealed_by_recovery(self):
        first=self.start()
        other=MarketConversation((10,30,'foreign'),auth_provider=(self.db,10,30))
        self.addCleanup(other.close)
        rejected=self.call('stage_journal_story',{'draft_id':first['draft_id'],
            'story_json':'{"executions":[]}'},text=None,user=30,context=other)
        self.assertFalse(rejected['ok'])
        self.assertNotIn('NAS100',str(rejected))
        self.assertNotIn('story',rejected)

    def test_no_draft_keeps_missing_state(self):
        result=self.call('stage_journal_story',{'story_json':'[]'},text=None)
        self.assertEqual(result['status'],'no_unfinished_journal')
        self.assertNotIn('next_tool',result)
        self.assertNotIn('draft_id',result)

    def test_multiple_drafts_keep_selection_route(self):
        first=self.start()
        self.call('stage_journal_story',{'story_json':'{}','new_draft':True},
                  text='Journal a new draft.')
        fresh=MarketConversation((10,20,'multiple'),auth_provider=(self.db,10,20))
        self.addCleanup(fresh.close)
        result=self.call('stage_journal_story',{'story_json':'[]'},text=None,context=fresh)
        self.assertEqual(result['status'],'draft_selection_required',result)
        self.assertEqual(len(result['drafts']),2)
        self.assertNotIn('save_journal_story',result['instruction'])

    def test_daily_recap_keeps_day_route(self):
        from test_journal_day import DAY
        day=self.call('stage_journal_day',{'day_json':json.dumps(DAY)},text='Journal my trading day.')
        result=self.call('stage_journal_story',{'story_json':'[]','draft_id':day['draft_id']},text=None)
        self.assertEqual(result['kind'],'journal_day',result)
        self.assertEqual(result['status'],'draft')
        self.assertNotIn('save_journal_story',result['instruction'])
