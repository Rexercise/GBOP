"""Invented journals only; durable save, recovery and cross-member boundaries."""
import json
import unittest
from gbop_voice_web import journal_coach as coach
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.journal_story import compact_review
import test_contextual_journals as fixture


class PlanComparisonTests(unittest.TestCase):
    setUp=fixture.ContextualJournalTests.setUp
    tearDown=fixture.ContextualJournalTests.tearDown
    review=fixture.ContextualJournalTests.review

    def call(self,name,args,text='Journal this trade.',context=None,user=20):
        context=context or self.context;context.begin_turn(text)
        return context.run(name,args,lambda n,a:coach.coach_tool(self.db,10,user,n,a))

    def journal(self,**changes):
        values={'asset':'NAS100','direction':'Bearish','play':'9ate8','trade_date':'2040-02-06','session':'day',
                'evidence_notes':'I saw the range high swept; exact time uncertain.',
                'decision_notes':'I waited for my stated confirmation before entering.',
                'reported_outcome':'stopped_out',**changes}
        draft=self.call('stage_journal_story',{'story_json':json.dumps(values)})
        self.assertTrue(draft['ok'],draft)
        return draft

    def finalize(self,draft):
        return self.call('save_journal_story',{'draft_id':draft['draft_id']},'Save this journal.')

    def plan(self,user=20,**changes):
        return coach.coach_tool(self.db,10,user,'save_shift_plan',{'session_date':'2040-02-06','shift':'day',
                  'plan':'Wait for confirmation; one thesis maximum.',**changes})

    def compare(self,user=20,**changes):
        return coach.coach_tool(self.db,10,user,'review_journal_against_plan',{'trade_number':1,**changes})

    def test_compact_readback_recovers_exact_notes_and_saved_unknown_result(self):
        draft=self.journal()
        self.assertIn('exact time uncertain',draft['review_before_finalize']['reported_evidence'])
        self.assertIsNone(draft['review_before_finalize']['result_r'])
        self.context.close()
        fresh=MarketConversation((10,20,'resume-plan-test'),auth_provider=(self.db,10,20))
        resumed=self.call('get_journal_story',{'draft_id':draft['draft_id']},'Resume my unfinished journal.',context=fresh)
        self.assertEqual(resumed['story']['decision_notes'],draft['story']['decision_notes'])
        saved=self.call('save_journal_story',{'draft_id':draft['draft_id']},'Save this journal.',context=fresh)
        self.assertTrue(saved['saved'],saved);self.assertIsNone(saved['result_r'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)

    def test_compare_exact_owned_scope_and_never_grade_stopout(self):
        self.finalize(self.journal());self.plan()
        result=self.compare()
        self.assertEqual(result['status'],'ready_to_compare')
        self.assertEqual(result['saved_plan']['plan'],'Wait for confirmation; one thesis maximum.')
        self.assertEqual(result['journal_review']['reported_evidence'],'I saw the range high swept; exact time uncertain.')
        self.assertEqual(result['journal_review']['reported_decision'],'I waited for my stated confirmation before entering.')
        self.assertEqual(result['review_dimensions']['plan_adherence']['value'],'unknown')
        self.assertIsNone(result['review_dimensions']['actual_outcome']['result_r'])
        self.assertFalse(result['pre_trade_plan_version_verified'])

    def test_no_saved_plan_is_explicit_not_generated(self):
        self.finalize(self.journal())
        self.assertEqual(self.compare()['status'],'no_saved_plan')
        self.assertIsNone(self.compare()['saved_plan'])

    def test_unknown_trade_date_does_not_use_recording_date(self):
        self.finalize(self.journal(trade_date=None))
        self.plan()
        result=self.compare()
        self.assertEqual(result['status'],'plan_scope_required')
        self.assertEqual(result['missing_essentials'],['session_date'])
        self.assertEqual(self.compare(session_date='2040-02-06')['status'],'ready_to_compare')

    def test_conflicting_plan_date_or_shift_denied(self):
        self.finalize(self.journal());self.plan()
        self.assertFalse(self.compare(session_date='2040-02-07')['ok'])
        self.assertFalse(self.compare(shift='night')['ok'])

    def test_peer_plan_and_journal_cannot_be_retrieved_with_forged_identity(self):
        self.finalize(self.journal());self.plan()
        self.assertFalse(self.compare(user=30,user_id=20,guild_id=10)['ok'])
        foreign=coach.coach_tool(self.db,11,20,'review_journal_against_plan',{'trade_number':1})
        self.assertFalse(foreign['ok'])

    def test_same_number_in_two_accounts_keeps_own_journal_and_own_plan(self):
        self.finalize(self.journal());self.plan()
        coach.save_entry(self.db,10,30,{'description':'Peer private journal','metadata_json':json.dumps({'asset':'EURUSD','trade_date':'2040-02-06','session':'day','kind':'trade'})})
        self.plan(user=30,plan='Peer private plan')
        result=self.compare()
        self.assertNotIn('Peer private',json.dumps(result))
        peer=self.compare(user=30)
        self.assertEqual(peer['saved_plan']['plan'],'Peer private plan')
        self.assertNotIn('range high swept',json.dumps(peer))

    def test_compact_review_marks_omitted_text_and_does_not_fabricate(self):
        result=compact_review({'evidence_notes':'x'*500})
        self.assertIn('read full draft',result['reported_evidence'])
        self.assertEqual(result['reported_decision'],'not reported')
        self.assertIsNone(result['result_r'])


if __name__=='__main__':unittest.main()
