"""Synthetic daily journaling acceptance cases; never touches member data."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import test_contextual_journals as fixture
from gbop_voice_web import journal_coach as coach, journal_day as day
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.journal_context import JournalBinding

DAY = {'trade_date':'2026-10-27','reflection':'A calm morning, tense holding the later setup.', 'trades':[
    {'trade_key':'morning_988','story':{'asset':'NAS100','direction':'Bullish','play':'9ate8',
     'objective':'8 AM buy-side','objective_status':'delivered',
     'entries':[{'entry_index':1,'entry_model':'Turtle Wick Soup','status':'closed',
                 'candle_label':'First purge of 8 AM low','notes':'8 AM high'}]}},
    {'trade_key':'eleven','story':{'asset':'NAS100','direction':'Bearish','play':'11 AM range',
     'objective':'11 AM sell-side','objective_status':'pending','context_notes':'Range established during the shift; entries post-shift.',
     'entries':[{'entry_index':1,'entry_model':'Model 1','status':'open','reported_entry_time_text':'12:15'},
                {'entry_index':2,'entry_model':'Super Soup','status':'open'}]}}]}

class JournalDayTests(unittest.TestCase):
    setUp=fixture.ContextualJournalTests.setUp
    tearDown=fixture.ContextualJournalTests.tearDown
    review=fixture.ContextualJournalTests.review
    def call(self,name,args,text='Journal my trading day: morning 988 and the 11 AM range.',context=None,user=20):
        context=context or self.context
        context.begin_turn(text)
        return context.run(name,args,lambda n,a:coach.coach_tool(self.db,10,user,n,a))
    def stage(self,change=None,**kwargs):
        return self.call('stage_journal_day',{'day_json':json.dumps(DAY if change is None else change),**kwargs})
    def save(self,draft):
        return self.call('save_journal_day',{'draft_id':draft['draft_id']},'Save it.')
    def test_capture_counts_and_atomic_save(self):
        draft=self.stage();self.assertTrue(draft['ok'],draft)
        self.assertEqual(draft['counts'],dict(trades=2,filled_entries=3,open_entries=2,closed_entries=1,pending_orders=0,unknown_entries=0))
        self.assertNotIn('reported_entry_time_text',draft['trades'][0]['story']['entries'][0])
        result=self.save(draft);self.assertTrue(result['ok'],result);self.assertTrue(result['saved'])
        self.assertEqual(len(result['receipts']),2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)
        self.assertEqual(self.context._journal_story['kind'],'journal_day')
        self.assertEqual(result['trades'][1]['story']['objective_status'],'pending')
    def test_repair_and_return_to_previous_setup(self):
        first=self.stage();second=self.stage({'trades':[{'trade_key':'morning_988','story':{'entries':[{'entry_index':1,'entry_model':'Super Soup','reported_entry_time_text':'10:15'}]}}]})
        fixed=self.stage({'trades':[{'trade_key':'morning_988','story':{'clear_fields':['entries.1.reported_entry_time_text'], 'entries':[{'entry_index':1,'entry_model':'Turtle Wick Soup'}]}}]})
        self.assertNotIn('reported_entry_time_text',fixed['trades'][0]['story']['entries'][0])
        self.assertEqual(fixed['trades'][1]['story'],first['trades'][1]['story'])
        more=self.stage({'trades':[{'trade_key':'morning_988','story':{'entries':[{'entry_index':2,'entry_model':'Super Soup','status':'closed','notes':'I felt calm.'}]}}]})
        self.assertEqual(more['counts']['filled_entries'],4)
        self.assertGreaterEqual(len(self.context._journal_story['day_trades']['morning_988']['corrections']),2)
    def test_pending_order_is_not_position_or_objective(self):
        value=deepcopy(DAY);value['trades'][1]['story']['entries'][1]['status']='pending_order'
        result=self.stage(value);self.assertTrue(result['ok'],result)
        self.assertEqual(result['counts']['filled_entries'],2);self.assertEqual(result['counts']['pending_orders'],1)
        updated=self.stage({'trades':[{'trade_key':'eleven','story':{'entries':[{'entry_index':2,'status':'open'}]}}]})
        self.assertEqual(updated['counts']['pending_orders'],0);self.assertEqual(updated['counts']['open_entries'],2)
        self.assertEqual(updated['trades'][1]['story']['objective_status'],'pending')
    def test_save_retry_and_resume_by_date(self):
        draft=self.stage();result=self.save(draft);self.assertTrue(result['ok'],result)
        before=list(self.conn.iterdump());again=self.save(draft)
        self.assertTrue(again['ok'],again);self.assertEqual(before,list(self.conn.iterdump()))
        fresh=MarketConversation((10,20,'resume-day'),auth_provider=(self.db,10,20))
        resumed=self.call('get_journal_day',{'trade_date':'2026-10-27'},'Read my day.',context=fresh)
        self.assertEqual(resumed['draft_id'],draft['draft_id']);self.assertTrue(resumed['saved'])
        self.assertEqual(resumed['reflection'],DAY['reflection'])
    def test_two_same_direction_setups_save_separately(self):
        value=deepcopy(DAY);value['trades'][1]['story']['direction']='Bullish'
        result=self.save(self.stage(value));self.assertTrue(result['ok'],result)
        self.assertEqual(len({r['trade_number'] for r in result['receipts']}),2)
    def test_failed_second_setup_rolls_back_entire_day(self):
        draft=self.stage();before=list(self.conn.iterdump());original=day._save_setup
        def fail(*args):
            if args[8]=='eleven':raise ValueError('Simulated second setup failure.')
            return original(*args)
        with patch.object(day,'_save_setup',side_effect=fail):result=self.save(draft)
        self.assertFalse(result['ok'],result)
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(self.context._journal_story['kind'],'journal_day')
        self.assertTrue(self.save(draft)['ok'])
    def test_update_saved_day_reuses_both_journals(self):
        draft=self.stage();saved=self.save(draft);self.assertTrue(saved['ok'],saved)
        updated=self.stage({'trades':[{'trade_key':'eleven','story':{'objective_status':'delivered', 'entries':[{'entry_index':1,'status':'closed'},{'entry_index':2,'status':'closed'}]}}]})
        self.assertFalse(updated['saved']);result=self.save(updated);self.assertTrue(result['ok'],result)
        self.assertEqual(result['counts']['closed_entries'],3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],2)
    def test_other_member_and_revoked_access_rejected(self):
        draft=self.stage();other=MarketConversation((10,30,'other-day'),auth_provider=(self.db,10,30))
        result=self.call('get_journal_day',{'draft_id':draft['draft_id']},context=other,user=30)
        self.assertFalse(result['ok']);self.assertNotIn('tense',str(result))
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20');self.conn.commit()
        self.assertFalse(self.save(draft)['ok'])
    def test_mock_cannot_be_finalized(self):
        draft=self.call('stage_journal_day',{'day_json':json.dumps(DAY)},'Mock journal: pretend I traded the morning and 11 AM setups.')
        self.assertTrue(draft['ok'],draft);self.assertFalse(draft['persisted']);self.assertFalse(self.save(draft)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_saved_deleted_child_does_not_get_recreated(self):
        draft=self.stage();self.assertTrue(self.save(draft)['ok'])
        self.conn.execute('DELETE FROM journals WHERE id=1');self.conn.commit()
        result=self.save(draft);self.assertFalse(result['ok']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],1)
    def test_voice_route_and_reconciliation(self):
        schemas={t['name']:t for t in contextual_tools(coach.COACH_TOOLS)}
        for name in day.DAY_NAMES:self.assertNotIn('market_reference',schemas[name]['parameters']['properties'])
        self.context.begin_turn()
        result=self.context.run('stage_journal_day',{'day_json':json.dumps(DAY),'raw_story':'I journaled two setups today.'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(result['ok'],result)
        self.context.begin_turn()
        args={'draft_id':result['draft_id'],'confirmation_text':'Save my day.','_journal_binding':JournalBinding(self.context,self.context.generation)}
        reader=day.prepare_day_finalization(self.db,10,20,args)
        self.assertFalse(reader()['saved'])
        saved=day.save_day(self.db,10,20,args);self.assertTrue(saved['saved']);self.assertTrue(reader()['saved'])
    def test_new_day_does_not_overwrite_yesterday(self):
        first=self.stage();new=deepcopy(DAY);new['trade_date']='2026-10-28'
        second=self.stage(new);self.assertTrue(second['ok'],second);self.assertNotEqual(first['draft_id'],second['draft_id'])
        old=self.call('get_journal_day',{'draft_id':first['draft_id']},'Read yesterday.')
        self.assertEqual(old['trade_date'],'2026-10-27')

    def test_malformed_extraction_retains_words_and_blocks_finalization(self):
        self.stage()
        raw='Back to my morning trade, scratch that earlier entry description.'
        result=self.call('stage_journal_day',{'day_json':'{"trades":[{"story":{"entries":[{"entry_index":1,"status":"winner"}]}}]}'},raw)
        self.assertFalse(result['ok']);self.assertIn(raw,result['raw_story_text'])
        self.assertFalse(self.save(result)['ok'])
        corrected=self.stage({'trades':[{'trade_key':'morning_988','story':{'entries':[{'entry_index':1,'entry_model':'Turtle Wick Soup'}]}}]})
        self.assertTrue(self.save(corrected)['ok'])
    def test_stale_session_cannot_overwrite_day(self):
        draft=self.stage();other=MarketConversation((10,20,'parallel-day'),auth_provider=(self.db,10,20))
        self.call('get_journal_day',{'draft_id':draft['draft_id']},'Read the recap.',context=other)
        updated=self.stage({'reflection':'Corrected feeling for the day.'})
        stale=self.call('stage_journal_day',{'draft_id':draft['draft_id'],'day_json':json.dumps({'reflection':'Stale rewrite.'})},'Correct my feeling.',context=other)
        self.assertFalse(stale['ok']);self.assertIn('another conversation',stale['error'])
        self.assertEqual(self.call('get_journal_day',{'draft_id':draft['draft_id']},'Read recap.')['reflection'],updated['reflection'])
    def test_paused_capture_is_session_only_and_preserves_saved_history(self):
        draft=self.stage();before=list(self.conn.iterdump())
        value=self.call('stage_journal_day',{'day_json':json.dumps({'reflection':'Private unsaved thought.'})},"Don't save this, just draft it.")
        self.assertFalse(value['persisted'])
        stored=json.loads(self.conn.execute('SELECT payload FROM journal_story_drafts WHERE id=?',(draft['draft_id'],)).fetchone()[0])
        self.assertNotEqual(stored.get('day_reflection'),'Private unsaved thought.')
        resumed=self.call('stage_journal_day',{'day_json':'{}'},'Resume journaling this day.')
        self.assertTrue(resumed['persisted']);self.assertEqual(resumed['reflection'],'Private unsaved thought.')
    def test_old_single_trade_drafts_do_not_block_day_or_get_finalized(self):
        for text in ('Start a new journal for my first separate trade.','Start another new journal for a separate trade.'):
            old=self.call('stage_journal_story',{'new_draft':True,'story_json':'{"context_notes":"Earlier unfinished narration."}'},text)
            self.assertTrue(old['ok'],old)
        draft=self.stage();self.assertTrue(draft['ok'],draft);self.assertTrue(self.save(draft)['ok'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM journal_story_drafts WHERE status='unfinished'").fetchone()[0],2)
    def test_day_recovery_returns_counts_without_private_prose(self):
        draft=self.stage();self.context.begin_turn('Save it.')
        args={'draft_id':draft['draft_id'],'_journal_binding':JournalBinding(self.context,self.context.generation)}
        reader=day.prepare_day_finalization(self.db,10,20,args)
        self.assertTrue(day.save_day(self.db,10,20,args)['ok']);result=reader()
        self.assertTrue(result['saved']);self.assertEqual(result['counts']['filled_entries'],3)
        for key in ('trades','raw_story_text','reflection'):self.assertNotIn(key,result)
    def test_range_reference_stays_distinct_from_exact_fill(self):
        data=deepcopy(DAY);data['trades'][1]['range_reference']='NAS100 October 27 2026 11 AM range'
        draft=self.stage(data);self.assertTrue(draft['ok'],draft)
        self.assertEqual(draft['trades'][1]['range_status'],'reported_unverified')
        self.assertNotIn('reported_entry_at',draft['trades'][1]['story']['entries'][1])
        self.assertTrue(self.save(draft)['ok'])

if __name__=='__main__':unittest.main()
