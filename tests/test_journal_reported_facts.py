"""Invented voice passages and in-memory SQLite; no production records."""
import json
import unittest
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.journal_direction import narrated_direction
from gbop_voice_web.journal_reported_facts import explicit_title
import test_journal_story as fixture


class ReportedFactsTests(unittest.TestCase):
    setUp = fixture.JournalStoryTests.setUp
    tearDown = fixture.JournalStoryTests.tearDown
    review = fixture.JournalStoryTests.review
    call = fixture.JournalStoryTests.call
    saved = fixture.JournalStoryTests.saved

    def voice(self, raw, draft_id=None, patch=None):
        args = {'story_json':json.dumps(patch or {}),'raw_story':raw}
        if draft_id: args['draft_id']=draft_id
        return self.call('stage_journal_story',args,text=None)

    def test_omitted_fields_reconciled_without_fabricating_execution(self):
        first=self.voice('Start a separate new trade. Title: Failed 9ate8. Lessons: ignored bearish context and forced a counter-trend trade.',patch={'asset':'NAS100','play':'9ate8','trade_date':'2040-02-06'})
        self.assertEqual(first['story']['title'],'Failed 9ate8')
        self.assertIn('ignored bearish context',first['story']['context_notes'])
        self.assertEqual(first['story']['entries'],[])
        raw='Entry confirmed: there was an entry. It was a bullish trade thought to be Super Soup. Exact price and risk remain unknown.'
        entry=self.voice(raw,first['draft_id'])
        self.assertEqual(len(entry['story']['entries']),1)
        self.assertNotIn('entry_model',entry['story']['entries'][0])
        self.assertIn('thought to be Super Soup',entry['story']['entries'][0]['notes'])
        result=self.voice('Direction confirmed: bullish long. Keep the other details unknown.',first['draft_id'])
        self.assertEqual(result['story']['direction'],'Bullish')
        self.assertEqual(result['direction_status'],'member_reported')
        self.assertEqual(result['story']['title'],'Failed 9ate8')
        self.assertEqual(len(self.voice(raw,first['draft_id'])['story']['entries']),1)
        self.context.close()
        fresh=MarketConversation((10,20,'reported-facts-reconnect'),auth_provider=(self.db,10,20))
        read=self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read my draft',context=fresh)
        self.assertEqual(read['story']['title'],'Failed 9ate8')
        saved=self.call('save_journal_story',{'draft_id':first['draft_id']},text='Save my journal.',context=fresh)
        self.assertTrue(saved['ok'],saved)
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0],'Bullish')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)
        self.assertIsNone(saved['result_r'])

    def test_custom_title_survives_stale_model_and_accepts_explicit_correction(self):
        first=self.voice('Title: Failed 9ate8.')
        stale=self.voice('No further details.',first['draft_id'],{'title':'Auto generated NAS100 title'})
        self.assertEqual(stale['story']['title'],'Failed 9ate8')
        omitted=self.voice(None,first['draft_id'],{'title':'Another auto title'})
        self.assertEqual(omitted['story']['title'],'Failed 9ate8')
        changed=self.voice('Title: Countertrend review.',first['draft_id'])
        self.assertEqual(changed['story']['title'],'Countertrend review')

    def test_current_title_correction_and_prior_negation(self):
        self.assertEqual(explicit_title('Not Super Soup. Title: Failed 9ate8.'),'Failed 9ate8')
        self.assertEqual(explicit_title('Title: Old name. Title: Corrected name.'),'Corrected name')
        self.assertEqual(explicit_title('Change the title to Countertrend review.'),'Countertrend review')
        self.assertIsNone(explicit_title('Title: '+ 'x'*161))

    def test_explicit_position_is_not_replaced_by_opposing_market_context(self):
        result=self.voice('The daily double purge and Young Lefty were bearish. I still went bullish.')
        self.assertEqual(result['story']['direction'],'Bullish')
        self.assertEqual(result['story']['entries'],[])
        self.assertFalse(narrated_direction('If I still went bullish, it could fail.'))

    def test_voice_context_capture_contract(self):
        from gbop_voice_web.voice_policy import STORY_VOICE_PROMPT
        from gbop_voice_web.journal_story import STORY_PROMPT
        self.assertIn('Keep purge sequence.',STORY_VOICE_PROMPT)
        self.assertIn('compress them to a setup label',STORY_PROMPT)

    def test_rejected_or_uncertain_facts_are_not_promoted(self):
        for raw in ('Maybe bullish long.', 'Direction confirmed: bullish short.', 'Do not title that Failed 9ate8.', 'Title: maybe Failed 9ate8.'):
            self.assertFalse(narrated_direction(raw),raw)
            self.assertIsNone(explicit_title(raw),raw)
        for raw in ('Entry thought Super Soup.', 'There might have been an entry.', 'No entry confirmed.', 'If I entered long, it might have worked.'):
            result=self.voice(raw)
            self.assertEqual(result['story']['entries'],[],raw)
