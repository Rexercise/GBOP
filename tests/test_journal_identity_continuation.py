"""Synthetic save/identity conversations only; no production member records."""
import json
import unittest
from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_story import _finalize_request
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture


class IdentityContinuationTests(unittest.TestCase):
    setUp=fixture.JournalStoryTests.setUp
    tearDown=fixture.JournalStoryTests.tearDown
    call=fixture.JournalStoryTests.call
    handlers=fixture.JournalStoryTests.handlers
    review=fixture.JournalStoryTests.review

    def stage(self,text,**args):
        return self.call('stage_journal_story',{'story_json':'{"asset":"NAS100","direction":"Bullish"}',**args},text=text)

    def test_spoken_explicit_finalization_not_magic_phrase(self):
        for text in ('Yeah, finalize it.', 'Yes, let’s finalize it.',
                     'Okay, yeah, go ahead and save it.', 'Could you please save it?'):
            self.assertTrue(_finalize_request(text),text)
        for text in ('Yeah.', 'Yes, separate.', "Don't save it.",
                     'I said save it yesterday.', 'Should I save it?',
                     'If I say finalize, what happens?', 'Save if everything is ready.',
                     'Do not save; finalize it.', 'Not yet save it.'):
            self.assertFalse(_finalize_request(text),text)

    def test_pending_identity_answer_does_not_require_model_flag(self):
        first=self.stage('Journal my trade.',new_trade=True)
        self.assertEqual(first['status'],'new_trade_intent_required')
        confirmed=self.stage('Separate, separate.',draft_id=first['draft_id'])
        self.assertTrue(confirmed['ok'],confirmed)
        with self.db() as conn:
            draft=journal_drafts.read(conn,10,20,first['draft_id'])[0]
        self.assertTrue(draft['new_trade'])
        self.assertNotIn('new_trade_confirmation_required',draft)
        self.assertNotIn('separate new trade',confirmed['next_question'] or '')

    def test_reconciliation_keeps_save_authorization_across_reconnect(self):
        first=self.stage('Journal this separate new trade.',new_trade=True)
        self.assertTrue(self.call('save_journal_story',{'draft_id':first['draft_id']},text='Save it.')['ok'])
        second=self.stage('Journal a new draft.',new_draft=True)
        self.assertTrue(second['ok'],second)
        blocked=self.call('save_journal_story',{'draft_id':second['draft_id']},text='Yeah, finalize it.')
        self.assertEqual(blocked['status'],'existing_trade_selection_required')
        self.assertTrue(blocked['save_authorized'])
        fresh=MarketConversation((10,20,'identity-reconnect'),auth_provider=(self.db,10,20))
        self.addCleanup(fresh.close)
        answered=self.call('stage_journal_story',{'draft_id':second['draft_id'],'story_json':'{}'},
                           text='This is a separate new trade.',context=fresh)
        self.assertTrue(answered['ok'],answered)
        saved=self.call('save_journal_story',{'draft_id':second['draft_id']},text=None,context=fresh)
        self.assertTrue(saved['ok'],saved)
        repeated=self.call('save_journal_story',{'draft_id':second['draft_id']},text=None,context=fresh)
        self.assertTrue(repeated['deduplicated'],repeated)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)

    def test_no_new_identity_from_negation_or_unprompted_bare_assent(self):
        first=self.stage('Journal my trade.',new_trade=True)
        denied=self.stage('Not a new trade.',draft_id=first['draft_id'])
        self.assertFalse(denied['ready_to_finalize'])
        with self.db() as conn:
            self.assertFalse(journal_drafts.read(conn,10,20,first['draft_id'])[0]['new_trade'])
        other=self.stage('Journal a new draft.',new_draft=True)
        self.stage('Separate.',draft_id=other['draft_id'])
        with self.db() as conn:
            self.assertFalse(journal_drafts.read(conn,10,20,other['draft_id'])[0]['new_trade'])

    def test_save_optout_revokes_retained_intent(self):
        draft=self.stage('Journal this separate new trade. Save it.',new_trade=True)
        self.stage('Do not save it.',draft_id=draft['draft_id'])
        denied=self.call('save_journal_story',{'draft_id':draft['draft_id']},text=None)
        self.assertFalse(denied['ok'],denied)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)

    def test_named_range_choice_can_complete_long_context_answer(self):
        from gbop_voice_web import journal_range
        self.context.begin_turn(None)
        values={'asset':'NAS100','trade_date':'2040-02-06'}
        draft={'id':'synthetic-range','revision':1}
        journal_range.stage(self.context,draft,
            'The 9ate8 and Young Lefty are both relevant.',values)
        self.assertEqual(draft['journal_range']['question'],'Which named range should I inspect first?')
        draft['range_question']='Is this a correction to this journal’s range, or a separate trade?'
        raw='Range preference provided: start with 9ate8 using the 8 AM parent range and the 9 AM candle. Treat the bearish Young Lefty and daily double purge as context, as described by Rex.'
        journal_range.stage(self.context,draft,raw,values)
        self.assertEqual(draft['journal_range']['selectors']['range_name'],'9ate8')
        self.assertEqual(draft['journal_range']['selectors']['shift'],'day')
        self.assertEqual(draft['journal_range']['status'],'reported_unverified')
        self.assertEqual(draft['journal_range']['description'],raw)
        self.assertNotIn('range_question',draft)
        self.assertNotIn('question',draft['journal_range'])
        self.assertEqual(self.context._journal_range_request['draft_id'],draft['id'])

    def test_unresolved_range_refinement_does_not_replace_known_asset(self):
        from gbop_voice_web import journal_range
        self.context.begin_turn(None)
        values={'asset':'NAS100','trade_date':'2040-02-06'}
        draft={'id':'synthetic-range','revision':1}
        journal_range.stage(self.context,draft,'9ate8 and Young Lefty',values)
        journal_range.stage(self.context,draft,
            'start with gold 9ate8 using the 8 AM parent range and the 9 AM candle. Treat the bearish Young Lefty as context',values)
        self.assertEqual(draft['journal_range']['selectors']['asset'],'NAS100')
        self.assertIn('correction',draft['range_question'])

    def test_negated_range_priority_is_not_a_selection(self):
        from gbop_voice_web import journal_range
        self.context.begin_turn(None)
        draft={'id':'synthetic-range','revision':1}
        journal_range.stage(self.context,draft,
            "Do not start with 9ate8 using the 8 AM parent range. Treat Young Lefty as context.",
            {'asset':'NAS100','trade_date':'2040-02-06'})
        self.assertNotIn('range_name',draft['journal_range']['selectors'])
        self.assertEqual(draft['journal_range']['status'],'range_reference_clarification')
