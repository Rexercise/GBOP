"""Synthetic member review/index regressions; no live records or credentials."""
import json
import unittest
from unittest.mock import patch
from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web.voice_payload import voice_tool_payload
from tests import test_journal_coach as fixtures


class MemberReviewTests(unittest.TestCase):
    setUp = fixtures.CoachTests.setUp
    tearDown = fixtures.CoachTests.tearDown

    def trade(self, result=None, **metadata):
        saved = coach.coach_tool(self.db,10,20,'save_journal_entry',{
            'description':'Synthetic trade','result_r':result,
            'metadata_json':json.dumps({'kind':'trade',**metadata})})
        self.assertTrue(saved['ok'],saved)
        return saved['trade_number']

    def legacy(self, *, result=None, kind='trade', **metadata):
        cur = self.conn.execute("INSERT INTO journals(guild_id,user_id,description,result_r,rule_adherence,created_at) VALUES(10,20,'Synthetic legacy',?,'partial','2026-10-01')",(result,))
        self.conn.execute("INSERT INTO journal_details(journal_id,guild_id,user_id,metadata,updated_at) VALUES(?,10,20,?,'2026-10-01')",(cur.lastrowid,json.dumps({'kind':kind,**metadata})))
        self.conn.commit()

    def test_four_linked_two_legacy_are_not_six_ungraded_trades(self):
        for result in (None,None,-.5,-.5):
            self.trade(result)
        self.legacy()
        self.legacy()
        before = list(self.conn.iterdump())
        report = coach.performance(self.db,10,20,{})
        self.assertEqual(report['record_counts'],{'review_entries':6,'linked_trades':4,'standalone_legacy_journals':2})
        self.assertEqual(report['summary']['known_outcomes'],2)
        self.assertEqual(report['summary']['total_r'],-1)
        self.assertEqual(report['summary']['average_r'],-.5)
        self.assertEqual(report['self_grades']['ungraded'],4)
        self.assertEqual(report['legacy_self_grades']['ungraded'],2)
        self.assertEqual(report['self_grades']['counts'],dict(type1=0,type2=0,type3=0,type4=0))
        self.assertEqual(report['adherence'],{'unknown':4,'partial':2})
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(voice_tool_payload('get_performance_review',report)['self_grades'],report['self_grades'])

    def test_explicit_grade_grouping_and_conflicts_stay_ungraded(self):
        for kind, adherence, outcome, result in (
            ('type1','followed_plan','profit',1), ('type2','followed_plan','loss',-1),
            ('type3','off_plan','loss',-1), ('type4','off_plan','profit',1)):
            number=self.trade(result)
            grade=coach.coach_tool(self.db,10,20,'record_trade_self_grade',{
                'trade_number':number,'grade':kind,'adherence':adherence,'outcome':outcome,
                'predefined_stop':True if kind=='type2' else None})
            self.assertTrue(grade['ok'],grade)
        self.trade(-1,adherence='no',emotion='fomo')
        self.legacy()
        report=coach.performance(self.db,10,20,{'group_by':'self_grade'})
        self.assertTrue(report['ok'])
        self.assertEqual(report['self_grades']['counts'],dict(type1=1,type2=1,type3=1,type4=1))
        self.assertEqual(report['self_grades']['ungraded'],1)
        self.assertEqual(report['legacy_self_grades']['ungraded'],1)
        self.assertEqual(report['groups']['ungraded']['entries'],2)
        coach.coach_tool(self.db,10,20,'save_journal_entry',{'trade_number':1,'result_r':-1})
        report=coach.performance(self.db,10,20,{})
        self.assertEqual(report['self_grades']['counts']['type1'],0)
        self.assertEqual(report['self_grades']['ungraded'],2)

    def test_period_and_study_filters_also_apply_to_grade_counts(self):
        self.trade(1,trade_date='2000-01-01')
        self.trade()
        self.legacy(kind='study',result=500)
        report=coach.performance(self.db,10,20,{'days':7})
        self.assertEqual(report['self_grades']['entries'],0)
        self.assertEqual(report['undated_entries_excluded'],1)
        self.assertEqual(coach.performance(self.db,10,20,{})['record_counts']['review_entries'],2)

    def test_index_no_ids_needed_preserves_aliases_paging_and_does_not_fetch_details(self):
        self.trade()
        self.legacy()
        self.trade(-.5)
        self.legacy()
        before=list(self.conn.iterdump())
        with patch.object(recall,'_trade_timelines',side_effect=AssertionError('unneeded timeline')),\
             patch('gbop_voice_web.journal_bundle.attach_photos',side_effect=AssertionError('unneeded photos')):
            one=recall.history(self.db,10,20,{'view':'index','limit':2})
            two=recall.history(self.db,10,20,{'view':'index','limit':2,'offset':one['next_offset']})
        self.assertTrue(one['has_more'])
        self.assertFalse(two['has_more'])
        self.assertEqual([r['trade_number'] for r in one['journals']],[1,2])
        self.assertEqual([r['legacy_journal_number'] for r in two['journals']],[2,4])
        self.assertEqual(one['trade_count'],2)
        self.assertEqual(one['legacy_journal_count'],2)
        self.assertNotIn('photo_count',one)
        self.assertNotIn('summary',one['journals'][0])
        self.assertEqual(one['journals'][0]['detail_request']['args'],{'view':'detail','trade_number':1})
        for page in (one,two):
            transported=voice_tool_payload('get_journal_history',page)
            self.assertEqual(transported['journals'],page['journals'])
            self.assertEqual(transported['next_offset'],page['next_offset'])
        self.assertEqual(before,list(self.conn.iterdump()))

    def test_index_includes_empty_trade_and_keeps_duplicate_history_one_trade(self):
        self.trade()
        self.conn.execute("INSERT INTO journals(guild_id,user_id,description,thesis_id) VALUES(10,20,'Other history',1)")
        self.conn.execute("INSERT INTO theses(id,guild_id,user_id,asset,direction,play,objective,thesis_invalidation,status,created_at) VALUES(2,10,20,'TEST','long','Synthetic','Target','Stop','OPEN','2026-10-01')")
        self.conn.commit()
        page=recall.history(self.db,10,20,{'view':'index'})
        self.assertEqual(len(page['journals']),2)
        self.assertEqual(page['legacy_journal_count'],0)
        self.assertEqual(page['journals'][0]['legacy_history_count'],1)
        self.assertEqual(page['journals'][1]['status'],'OPEN')
        self.assertIsNone(page['journals'][1]['self_grade'])

    def test_index_bounds_large_nonidentity_labels_and_grade_notes(self):
        self.trade()
        coach.coach_tool(self.db,10,20,'record_trade_self_grade',{'trade_number':1,
            'grade':'type1','adherence':'followed_plan','outcome':'profit','note':'😀'*500})
        # Legacy stores can contain labels longer than current input limits.
        self.conn.execute("UPDATE theses SET asset=?",('X'*23000,))
        page=recall.history(self.db,10,20,{'view':'index','limit':1})
        result=voice_tool_payload('get_journal_history',page)
        self.assertEqual(result['journals'][0]['trade_number'],1)
        self.assertTrue(result['journals'][0]['asset_label_omitted'])
        self.assertNotIn('note',result['journals'][0]['self_grade'])
        self.assertEqual(result['journals'][0]['detail_request']['args'],{'view':'detail','trade_number':1})
        self.assertFalse(result['has_more'])
        self.assertEqual(result['next_offset'],1)
        self.assertLess(len(json.dumps(result)),5000)

    def test_compound_request_does_not_bind_index_to_exact_or_latest_record(self):
        from gbop_voice_web.market_conversation import MarketConversation
        self.trade()
        self.trade()
        self.legacy()
        for text in ('Read my last trade and list all my trade and journal numbers',
                     'Show Trade #2 and list all my trade numbers'):
            context=MarketConversation((10,20,'test'),auth_provider=(self.db,10,20))
            context.begin_turn(text)
            result=context.run('get_journal_history',{'view':'index','limit':10,'offset':0},
                lambda name,args:recall.history(self.db,10,20,args))
            self.assertTrue(result['ok'],result)
            self.assertEqual(len(result['journals']),3)
            self.assertEqual([r['trade_number'] for r in result['journals'][:2]],[1,2])

    def test_index_and_review_are_member_scoped_and_schema_exposes_all_options(self):
        self.trade()
        self.legacy()
        for guild,user in ((10,30),(11,20)):
            self.assertEqual(recall.history(self.db,guild,user,{'view':'index'})['journals'],[])
            self.assertEqual(coach.performance(self.db,guild,user,{})['self_grades']['entries'],0)
        tool=next(t for t in coach.COACH_TOOLS if t['name']=='get_performance_review')
        self.assertIn('self_grade',tool['parameters']['properties']['group_by']['enum'])
        self.assertIn('index',recall.RECALL_SELECTORS['view']['enum'])
        self.assertFalse(recall.history(self.db,10,20,{'view':'wrong'})['ok'])
        for args in ({'latest':'journal'},{'trade_number':1},{'date_basis':'trade'},{'detail_offset':1}):
            self.assertEqual(recall.history(self.db,10,20,{'view':'index',**args})['status'],'index_selectors_conflict')


if __name__=='__main__':
    unittest.main()
