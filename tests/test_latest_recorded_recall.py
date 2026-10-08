"""Newest member information, synthetic-only reads with no external delivery."""
import json
import unittest
from unittest.mock import Mock, patch

from gbop_voice_web import journal_recall as recall
from gbop_voice_web.journal_presentation import journal_tool_payload, JOURNAL_PRESENTATION_MAX_CHARS
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_policy import JOURNAL_VOICE_PROMPT
from tests import test_unified_journal_recall as fixtures


class LatestRecordedRecallTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.UnifiedRecallTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.conn, self.db = fixture.conn, fixture.db
        self.detail, self.event = fixture.detail, fixture.event

    def draft(self, ident='draft-one', text='I felt nervous mid-trade.', timestamp='2026-10-08T12:00:00+00:00',
              *, guild=10, user=20, thesis=None, journal=None, values=None, updated=None):
        payload = dict(id=ident, member=[guild,user], values=values or {'context_notes':text,'entries':[]},
            raw_story=[dict(text=text,source='member_text',recorded_at=timestamp)],
            corrections=[],provenance={})
        if thesis or journal:
            payload['selected_key']=[journal,thesis]
        self.conn.execute('INSERT INTO journal_story_drafts VALUES(?,?,?,?,?,?,?,?,?,?)',
            (ident,guild,user,'unfinished',1,json.dumps(payload),journal,thesis,timestamp,updated or timestamp))
        return payload

    def read(self, latest='trade', **args):
        return recall.history(self.db,10,20,{'latest':latest,**args})

    def test_last_trade_and_journal_return_newest_standalone_note_not_older_execution(self):
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Feeling calmer mid-trade','',NULL,'','2026-10-08')")
        before = self.conn.total_changes
        with patch('httpx.Client',side_effect=AssertionError('Recall must not send')):
            for kind in ('trade','journal'):
                result=self.read(kind)
                self.assertTrue(result['ok'],result)
                self.assertEqual(result['journals'][0]['record_key'],'legacy_journal:111')
                self.assertIn('Feeling calmer',result['context_text'])
                self.assertTrue(result['latest_recorded'])
                self.assertFalse(result['is_latest_trade'])
                self.assertNotIn('needs_clarification',result)
                self.assertNotIn('latest_saved',result)
        self.assertEqual(before,self.conn.total_changes)

    def test_latest_save_wins_even_when_older_execution_date_is_known(self):
        self.detail(100,metadata={'trade_date':'2026-10-07'})
        self.event('journal_canonical_v1',{'journal_id':100})
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'New feeling note','',NULL,'','2026-10-08')")
        self.assertEqual(self.read()['journals'][0]['record_key'],'legacy_journal:111')
        strict=self.read(date_basis='trade')
        self.assertEqual(strict['status'],'trade_date_ambiguous')
        self.assertNotIn('journals',strict)

    def test_linked_note_keeps_full_execution_and_trade_context(self):
        self.conn.executescript('''CREATE TABLE thesis_executions(id INT,thesis_id INT,guild_id INT,user_id INT,
            entry_model TEXT,tier INT,risk_r REAL,entry_invalidation TEXT,note TEXT,created_at TEXT);
            INSERT INTO thesis_executions VALUES(1,25,10,20,'Model 1',1,1.0,'Reported invalidation','Real entry note','2026-10-02');''')
        self.draft(thesis=25,journal=100)
        result=self.read()
        self.assertEqual(len(result['journals']),1)
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        for term in ('Synthetic journal','Real entry note','I felt nervous mid-trade.','unfinished'):
            self.assertIn(term,result['context_text'])
        self.assertEqual(result['journals'][0]['update_count'],1)
        self.assertEqual(result['journal_count'],2)
        self.assertEqual(result['unfinished_journal_count'],1)

    def test_draft_only_returns_immediately_without_creating_performance_records(self):
        self.conn.execute('DELETE FROM journals');self.conn.execute('DELETE FROM theses')
        self.draft()
        before=self.conn.total_changes
        result=self.read()
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['journals'][0]['record_kind'],'unfinished_journal')
        self.assertEqual(result['journals'][0]['record_key'],'draft:draft-one')
        self.assertIn('I felt nervous',result['context_text'])
        self.assertEqual((result['trade_count'],result['journal_count']),(0,0))
        self.assertEqual(before,self.conn.total_changes)
        self.assertEqual(self.conn.execute('SELECT status FROM journal_story_drafts').fetchone()[0],'unfinished')

    def test_legacy_only_is_not_empty_or_a_trade(self):
        self.conn.execute('DELETE FROM theses')
        result=self.read()
        self.assertTrue(result['ok'])
        self.assertEqual(result['journals'][0]['record_key'],'legacy_journal:100')
        self.assertTrue(result['journals'][0]['is_legacy'])
        self.assertIsNone(result['journals'][0]['trade_number'])

    def test_newer_standalone_draft_wins_over_richer_older_bundle(self):
        self.draft('linked','Older linked note','2026-10-07T12:00:00+00:00',thesis=25,journal=100)
        self.draft('fresh','New standalone reflection','2026-10-08T12:00:00+00:00')
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'draft:fresh')
        self.assertNotIn('Older linked note',result['context_text'])

    def test_member_and_guild_isolation_for_drafts_and_notes(self):
        self.draft('foreign-user',user=30)
        self.draft('foreign-guild',guild=11)
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,30,'Foreign secret','',NULL,'','2026-10-09')")
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertNotIn('Foreign secret',json.dumps(result))
        self.assertEqual(result['unfinished_journal_count'],0)
        denied=recall.history(self.db,10,20,{'draft_id':'foreign-user'})
        self.assertEqual(denied['status'],'draft_unavailable')

    def test_deleted_and_foreign_link_targets_are_not_resurrected(self):
        for ident,tid,jid in [('deleted',999,999),('foreign',50,None)]:
            self.draft(ident,thesis=tid,journal=jid)
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertEqual(result['unfinished_journal_count'],0)
        self.assertEqual(recall.history(self.db,10,20,{'draft_id':'deleted'})['status'],'draft_unavailable')
        self.conn.execute('DELETE FROM journals WHERE id=100')
        self.assertNotIn('Synthetic journal',self.read()['context_text'])

    def test_draft_bookkeeping_does_not_advance_recency(self):
        self.draft(timestamp='2026-09-01T12:00:00+00:00',updated='2026-12-31T12:00:00+00:00')
        self.assertEqual(self.read()['journals'][0]['record_key'],'trade:25')

    def test_historical_draft_resume_and_provenance_clocks_do_not_win(self):
        payload=self.draft(timestamp='2026-09-01T12:00:00+00:00',updated='2026-12-31T12:00:00+00:00')
        payload['raw_story'].append({'text':'Resume my journal','recorded_at':'2026-12-31T12:00:00Z'})
        payload['provenance']={'context_notes':{'recorded_at':'2026-12-31T12:00:00Z'}}
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),))
        self.assertEqual(self.read()['journals'][0]['record_key'],'trade:25')

    def test_substantive_draft_marker_and_historical_correction_count(self):
        payload=self.draft(timestamp='2026-09-01T12:00:00+00:00')
        payload['corrections']=[{'recorded_at':'2026-10-08T12:00:00Z','fields':{
            'context_notes':{'before':'Old feeling','after':'Corrected feeling'}}}]
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),))
        self.assertEqual(self.read()['journals'][0]['record_key'],'draft:draft-one')
        payload['corrections']=[];payload['substantive_updated_at']='2026-10-09T12:00:00Z'
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),))
        self.assertEqual(self.read()['journals'][0]['saved_at'],'2026-10-09T12:00:00Z')

    def test_control_only_and_empty_placeholder_drafts_do_not_hide_saved_information(self):
        self.draft(text='Resume my journal',values={'entries':[{'entry_index':1}]})
        self.assertEqual(self.read()['journals'][0]['record_key'],'trade:25')

    def test_offset_timestamps_are_compared_as_instants(self):
        self.draft(timestamp='2026-10-08T12:00:00+02:00')
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Actually later','',NULL,'','2026-10-08T10:30:00Z')")
        self.assertEqual(self.read()['journals'][0]['record_key'],'legacy_journal:111')

    def test_read_receipt_and_context_events_do_not_win_recency(self):
        for kind in ('journal_read_v1','journal_receipt_v1','journal_context_v1','journal_canonical_v1','execution_receipt_v1','journal_execution_v1:receipt'):
            self.event(kind,{'journal_id':100})
        self.conn.execute("UPDATE thesis_events SET created_at='2026-12-31'")
        self.draft()
        self.assertEqual(self.read()['journals'][0]['record_key'],'draft:draft-one')

    def test_member_delivery_event_keeps_trading_prose_and_advances_recency(self):
        self.draft()
        self.event('delivery','Price delivered to target; I closed half and felt calmer.')
        self.conn.execute("UPDATE thesis_events SET created_at='2026-10-09'")
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertIn('closed half and felt calmer',result['context_text'])

    def test_member_context_report_survives_without_market_noise(self):
        self.draft()
        self.event('journal_context_v1',{'reported_entry_at':'2026-10-01T09:30:00-04:00',
            'reported_outcome':'stopped_out','market_review':{'automatic_snapshot':'Do not show'}})
        self.conn.execute("UPDATE thesis_events SET created_at='2026-10-09'")
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertIn('stopped_out',result['context_text'])
        self.assertIn('2026-10-01T09:30:00-04:00',result['context_text'])
        self.assertNotIn('automatic_snapshot',result['context_text'])

    def test_control_passages_remain_in_full_recall_without_advancing_recency(self):
        payload=self.draft()
        payload['raw_story'].extend([{'text':'yes','recorded_at':'2026-12-31'},
                                    {'text':'Resume my journal','recorded_at':'2026-12-31'}])
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),))
        result=self.read()
        self.assertEqual(result['journals'][0]['saved_at'],'2026-10-08T12:00:00+00:00')
        self.assertIn('yes',result['context_text'])
        self.assertIn('Resume my journal',result['context_text'])

    def test_optional_feeling_prompt_only_audit_rewinds_details_timestamp(self):
        before_meta={'asset':'TEST-A'}
        after_meta={**before_meta,'feeling_prompt_state':{'during':{'status':'skipped'}}}
        self.detail(metadata=after_meta)
        self.conn.execute("UPDATE journal_details SET updated_at='2026-12-31'")
        journal=dict(self.conn.execute('SELECT * FROM journals WHERE id=100').fetchone())
        self.event('journal_audit_v1',dict(journal_id=100,
            before={'journal':journal,'details':{'metadata':json.dumps(before_meta),'updated_at':'2026-10-01'}},
            after={'journal':journal,'details':{'metadata':json.dumps(after_meta),'updated_at':'2026-12-31'}}))
        self.conn.execute("UPDATE thesis_events SET created_at='2026-12-31'")
        self.draft()
        self.assertEqual(self.read()['journals'][0]['record_key'],'draft:draft-one')

    def test_thesis_only_correction_is_substantive_even_without_detail_stamp(self):
        self.draft()
        journal=dict(self.conn.execute('SELECT * FROM journals WHERE id=100').fetchone())
        self.event('journal_audit_v1',dict(journal_id=100,
            before={'journal':journal,'thesis':{'objective':'Old objective'}},
            after={'journal':journal,'thesis':{'objective':'Corrected objective'}}))
        self.conn.execute("UPDATE thesis_events SET created_at='2026-10-09'")
        result=self.read()
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertIn('Corrected objective',result['context_text'])

    def test_legacy_noop_resave_uses_last_audit_not_updated_at(self):
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Older note','',NULL,'','2026-09-01')")
        self.detail(111,metadata={'legacy_audit':[{'recorded_at':'2026-09-02','journal':{}}]})
        self.conn.execute("UPDATE journal_details SET updated_at='2026-12-31' WHERE journal_id=111")
        self.assertEqual(self.read()['journals'][0]['record_key'],'trade:25')

    def test_legacy_without_audit_does_not_use_bookkeeping_timestamp(self):
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Older note','',NULL,'','2026-09-01')")
        self.detail(111,metadata={})
        self.conn.execute("UPDATE journal_details SET updated_at='2026-12-31' WHERE journal_id=111")
        self.assertEqual(self.read()['journals'][0]['record_key'],'trade:25')

    def test_missing_draft_data_is_disclosed_not_claimed_complete(self):
        self.draft()
        self.conn.execute("UPDATE journal_story_drafts SET payload='invalid json'")
        result=self.read()
        self.assertFalse(result['drafts_available'])
        self.assertIn('newer information may be missing',result['selection_note'])

    def test_strict_trade_dates_never_fall_back_or_backfill(self):
        before=self.conn.total_changes
        result=self.read(date_basis='trade')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'trade_date_ambiguous')
        self.assertEqual(before,self.conn.total_changes)

    def test_full_draft_pages_are_bound_and_bounded_across_new_saves(self):
        self.draft(text='Ω'*35000)
        context=MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('Tell me about my last trade')
        runner=lambda name,args:recall.history(self.db,10,20,args)
        offset=0;full=''
        for index in range(100):
            result=context.run('get_journal_history',{'latest':'trade','detail_offset':offset},runner)
            shown=journal_tool_payload('get_journal_history',result)
            self.assertLessEqual(len(json.dumps(shown)),JOURNAL_PRESENTATION_MAX_CHARS)
            self.assertEqual(shown['journals'][0]['record_key'],'draft:draft-one')
            self.assertTrue(shown['latest_recorded'])
            full+=shown['context_text']
            if index==0:self.draft('newer','Newer draft','2026-12-31T12:00:00Z')
            if not shown['has_more_details']:break
            self.assertGreater(shown['next_detail_offset'],offset)
            offset=shown['next_detail_offset']
        else:self.fail('Draft pagination did not finish')
        self.assertEqual(full.count('Ω'),70000)  # structured narrative and original narration, both preserved
        self.assertNotIn('Newer draft',full)
        self.assertIn('unfinished',full)

    def test_selected_draft_edit_between_pages_requires_restart(self):
        payload=self.draft(text='abcdefghijklmnopqrstuvwxyz '*1500)
        context=MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('Tell me about my last journal')
        runner=lambda name,args:recall.history(self.db,10,20,args)
        first=context.run('get_journal_history',{'latest':'journal'},runner)
        self.assertTrue(first['has_more_details'])
        payload['values']['context_notes']='New correction. '+payload['values']['context_notes']
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),))
        changed=context.run('get_journal_history',{'latest':'journal','detail_offset':first['next_detail_offset']},runner)
        self.assertEqual(changed['status'],'recall_content_changed')
        self.assertNotIn('context_text',changed)
        restarted=context.run('get_journal_history',{'latest':'journal','detail_offset':0},runner)
        self.assertTrue(restarted['ok'])
        self.assertIn('New correction.',restarted['context_text'])
        self.assertNotEqual(first['context_version'],restarted['context_version'])

    def test_linked_standalone_note_draft_survives_all_continuation_pages(self):
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Standalone reflection','',NULL,'','2026-10-08')")
        self.draft(text='Ω'*30000,journal=111)
        context=MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('Tell me about my last journal')
        runner=lambda name,args:recall.history(self.db,10,20,args)
        offset=0;full=''
        for index in range(100):
            result=context.run('get_journal_history',{'latest':'journal','detail_offset':offset},runner)
            shown=journal_tool_payload('get_journal_history',result)
            self.assertLessEqual(len(json.dumps(shown)),JOURNAL_PRESENTATION_MAX_CHARS)
            self.assertEqual(shown['journals'][0]['record_key'],'legacy_journal:111')
            self.assertEqual(shown['journals'][0]['unfinished_journal_count'],1)
            full+=shown['context_text']
            if index==0:self.draft('newer','Newer standalone information','2026-12-31T12:00:00Z')
            if not shown['has_more_details']:break
            self.assertGreater(shown['next_detail_offset'],offset)
            offset=shown['next_detail_offset']
        else:self.fail('Linked standalone draft pagination did not finish')
        self.assertEqual(full.count('Ω'),60000)
        self.assertIn('Standalone reflection',full)
        self.assertNotIn('Newer standalone information',full)

    def test_recall_does_not_authorize_send_and_draft_cannot_be_sent(self):
        self.draft()
        context=MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('Tell me about my last trade')
        runner=lambda name,args:recall.history(self.db,10,20,args)
        result=context.run('get_journal_history',{'latest':'trade'},runner)
        self.assertTrue(result['ok'])
        delivery=Mock()
        self.assertFalse(context.run('send_journal_history',{'latest':'trade'},delivery)['ok'])
        delivery.assert_not_called()
        with patch('httpx.Client',side_effect=AssertionError('No send')):
            self.assertFalse(recall.send_history(self.db,10,20,{'draft_id':'draft-one'})['ok'])
            self.assertFalse(recall.send_history(self.db,10,20,{'latest':'trade'})['ok'])

    def test_index_and_general_pagination_do_not_include_or_pin_drafts(self):
        self.draft()
        result=recall.history(self.db,10,20,{'view':'index','limit':1})
        self.assertEqual(result['journals'][0]['record_key'],'trade:25')
        self.assertTrue(result['has_more'])
        next_page=recall.history(self.db,10,20,{'view':'index','offset':result['next_offset']})
        self.assertEqual(next_page['journals'][0]['record_key'],'trade:40')
        self.assertNotIn('draft:',json.dumps(next_page))


class LatestRecordedIntentTests(unittest.TestCase):
    def test_casual_last_trade_and_journal_override_model_literal_selection(self):
        for text in ('My last trade','Tell me about my latest journal','What was my most recent trade?',
                     'My latest recorded trade','Tell me about the last piece of recorded information'):
            args,denial=recall.bind_recall_intent('get_journal_history',
                {'view':'index','date_basis':'trade','trade_number':99,'legacy_journal_number':99},text)
            self.assertIsNone(denial,text)
            self.assertIn(args['latest'],('trade','journal'))
            self.assertIsNone(args['date_basis'],text)
            self.assertIsNone(args['trade_number'])
            self.assertIsNone(args['legacy_journal_number'])
            self.assertEqual(args['view'],'detail')

    def test_explicit_chronology_remains_strict(self):
        for text in ('My chronologically latest trade','What was the latest by trade date?',
                     'Tell me my latest trade by date','Show my latest actual trade',
                     'What was my actual last trade?','Show my actual latest trade',
                     'My actual most recent trade'):
            args,denial=recall.bind_recall_intent('get_journal_history',{},text)
            self.assertIsNone(denial)
            self.assertEqual((args['latest'],args['date_basis']),('trade','trade'),text)

    def test_audio_schema_and_both_prompts_teach_same_direct_read_default(self):
        for prompt in (recall.JOURNAL_RECALL_PROMPT,JOURNAL_VOICE_PROMPT):
            self.assertIn('date_basis=null',prompt)
            self.assertIn('unfinished',prompt)
            self.assertIn('directly',prompt)
        self.assertIn('default null',recall.RECALL_SELECTORS['date_basis']['description'])
        self.assertNotIn('draft_id',recall.JOURNAL_RECALL_TOOLS[0]['parameters']['properties'])
        from pathlib import Path
        for path in ('bot.py','gbop_voice_web/server.py'):
            source=(Path(__file__).resolve().parents[1]/path).read_text()
            self.assertIn('Ordinary last trade/journal returns newest substantive recorded information',source)
            self.assertNotIn('Latest trade differs from latest journal.',source)


if __name__=='__main__':unittest.main()
