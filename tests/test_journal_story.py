"""Invented journal narratives and in-memory storage only; no member data or network."""
from copy import deepcopy
import json
import unittest
from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web.market_conversation import MarketConversation
import test_contextual_journals as fixture

STORY={'asset':'NAS100','direction':'Bearish','play':'Young Lefty','trade_date':'2040-02-06',
       'thesis_invalidation':'close above the 10 o’clock AM','reported_outcome':'open',
       'context_notes':'Distribution from the next candle was anticipated, not observed.',
       'entries':[{'entry_index':1,'entry_model':'Blessed Thief','candle_label':'9 AM','risk_r':0.75,'objective':'Young Lefty low','status':'open','notes':'Considered breakeven; no stop move confirmed.'},
                  {'entry_index':2,'entry_model':'Soup','candle_label':'10 AM candle purging the 9 AM high','status':'closed','pnl_text':'Profitable at the 50% level; amount and R unknown'},
                  {'entry_index':3,'entry_model':'Soup','candle_label':'Later re-purge above the 9 AM high, described near the 10 AM high','status':'open','notes':'Fresh re-entry, not the first Soup left partially open.'}]}

class JournalStoryTests(unittest.TestCase):
    setUp=fixture.ContextualJournalTests.setUp
    tearDown=fixture.ContextualJournalTests.tearDown
    review=fixture.ContextualJournalTests.review
    handlers=fixture.ContextualJournalTests.handlers
    def call(self,name,args,text='Journal this reported story.',user=20,context=None):
        context=context or self.context;context.begin_turn(text)
        return context.run(name,args,lambda n,a:coach.coach_tool(self.db,10,user,n,a))
    def stage(self,values=None,**kwargs):
        return self.call('stage_journal_story',{'story_json':json.dumps(values if values is not None else STORY),**kwargs})
    def saved(self,draft):
        return self.call('save_journal_story',{'draft_id':draft['draft_id']},'Save the journal narrative.')
    def test_stage_preserves_whole_sequence_without_database_records(self):
        result=self.stage();self.assertTrue(result['ok'],result);self.assertFalse(result['saved'])
        self.assertEqual(len(result['story']['entries']),3)
        self.assertEqual(result['story']['entries'][1]['status'],'closed')
        self.assertIn('anticipated, not observed',result['draft_summary'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)
    def test_spoken_repair_updates_only_draft_and_never_deletes_a_record(self):
        original=deepcopy(STORY);original['play']='9ate8';original['entries'][0]['entry_model']='Super Soup'
        first=self.stage(original)
        result=self.stage({'play':'Young Lefty','entries':[{'entry_index':1,'entry_model':'Blessed Thief'}]},draft_id=first['draft_id'])
        self.assertEqual(result['story']['entries'][0]['entry_model'],'Blessed Thief')
        self.assertEqual(len(result['story']['entries']),3)
        self.assertNotIn('Super Soup',result['draft_summary'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_explicit_clear_can_retract_unknown_play_without_clearing_other_fields(self):
        first=self.stage();result=self.stage({'clear_fields':['play','entries.1.entry_model']},draft_id=first['draft_id'])
        self.assertNotIn('play',result['story']);self.assertNotIn('entry_model',result['story']['entries'][0])
        self.assertEqual(result['story']['entries'][0]['risk_r'],.75)
        self.assertEqual(result['story']['entries'][1]['entry_model'],'Soup')
    def test_ambiguous_boundary_is_preserved_and_only_delivered_question_is_counted(self):
        first=self.stage();question=first['next_question'];self.assertIn('Which boundary',question)
        self.context.complete_response(question,completed=False)
        second=self.stage({});self.assertEqual(second['next_question'],question)
        self.context.complete_response(question,completed=True)
        third=self.stage({});self.assertNotEqual(third['next_question'],question)
        self.assertIn('when did you enter',third['next_question'])
        self.assertEqual(third['story']['thesis_invalidation'],STORY['thesis_invalidation'])
        self.assertNotIn('invalidation_boundary',third['story'])
    def test_clarified_boundary_does_not_need_repeated_question(self):
        first=self.stage();result=self.stage({'invalidation_boundary':'10 AM candle high'},draft_id=first['draft_id'])
        self.assertIn('when did you enter',result['next_question']);self.assertIn('10 AM candle high',result['draft_summary'])
    def test_unknown_entry_risks_do_not_block_narrative_or_create_execution_rows(self):
        saved=self.saved(self.stage());self.assertTrue(saved['ok'],saved);self.assertEqual(saved['trade_number'],1)
        self.assertFalse(saved['execution_records_changed']);self.assertIsNone(saved['result_r'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT max_r,status FROM theses').fetchone()['max_r'],0)
    def test_existing_trade_must_be_selected_before_another_record_is_created(self):
        env=self.handlers('bot.py');opened=env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.75))
        self.assertTrue(opened['ok']);draft=self.stage()
        result=self.saved(draft);self.assertFalse(result['ok']);self.assertEqual(result['status'],'existing_trade_selection_required')
        selected=self.stage({},draft_id=draft['draft_id'],trade_number=opened['trade_id'])
        result=self.saved(selected);self.assertTrue(result['ok'],result);self.assertEqual(result['trade_number'],opened['trade_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT risk_r FROM thesis_executions').fetchone()[0],.75)
    def test_fresh_session_recall_retains_entries_unknowns_and_no_inferred_outcome(self):
        self.assertTrue(self.saved(self.stage())['ok']);self.context.close()
        fresh=MarketConversation((10,20,'fresh-story-read'),auth_provider=(self.db,10,20));fresh.begin_turn('Read Trade #1')
        result=fresh.run('get_journal_history',{'trade_number':1},lambda n,a:recall.history(self.db,10,20,a))
        text='\n'.join(recall.messages(result))
        for value in ('Blessed Thief','Entry 2: Soup','Entry 3: Soup','status: closed','Fresh re-entry','entry risk: unknown','boundary: not clarified'):
            self.assertIn(value,text.lower() if value=='boundary: not clarified' else text)
        self.assertIsNone(result['journals'][0]['result_r'])
    def test_member_session_and_forged_draft_ids_are_rejected(self):
        draft=self.stage()
        other=MarketConversation((10,30,'other-story'),auth_provider=(self.db,10,30))
        result=self.call('get_journal_story',{'draft_id':draft['draft_id']},user=30,context=other)
        self.assertFalse(result['ok']);self.assertNotIn('Blessed Thief',str(result))
        self.assertFalse(self.call('save_journal_story',{'draft_id':'forged'})['ok'])
        self.context.owner=(11,20,'wrong-guild')
        self.assertFalse(self.call('get_journal_story',{'draft_id':draft['draft_id']})['ok'])
    def test_revoked_member_cannot_stage_or_save(self):
        draft=self.stage();self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20');self.conn.commit()
        self.assertFalse(self.saved(draft)['ok']);self.assertFalse(self.stage({})['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_saved_draft_reads_report_saved_and_edits_report_unsaved(self):
        draft=self.stage();self.assertTrue(self.saved(draft)['ok'])
        state=self.call('get_journal_story',{'draft_id':draft['draft_id']})
        self.assertTrue(state['saved'],state)
        changed=self.stage({'entries':[{'entry_index':3,'notes':'Still open at the later review.'}]},draft_id=draft['draft_id'])
        self.assertFalse(changed['saved'])
    def test_same_draft_retry_is_read_only_and_does_not_duplicate_audit(self):
        draft=self.stage();saved=self.saved(draft);before=list(self.conn.iterdump());writes=[]
        self.before_execute=lambda sql,args:writes.append(sql) if sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) else None
        again=self.saved(draft);self.before_execute=None
        self.assertTrue(again['ok'],again);self.assertEqual(again['trade_number'],saved['trade_number'])
        self.assertEqual(before,list(self.conn.iterdump()));self.assertEqual(writes,[])
    def test_deleted_saved_draft_is_not_recreated(self):
        draft=self.stage();self.assertTrue(self.saved(draft)['ok'])
        self.conn.execute('DELETE FROM journals');self.assertFalse(self.saved(draft)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_selected_record_cannot_slide_to_another_trade_number(self):
        env=self.handlers('bot.py')
        for _ in range(2):self.assertTrue(env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))['ok'])
        draft=self.stage(trade_number=1)
        self.conn.execute('DELETE FROM journals WHERE thesis_id=1');self.conn.execute('DELETE FROM theses WHERE id=1')
        before=self.conn.execute('SELECT description FROM journals').fetchone()[0]
        result=self.saved(draft);self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT description FROM journals').fetchone()[0],before)
    def test_invalid_risk_schema_and_duplicate_entry_indexes_fail_closed(self):
        for patch in ({'entries':[{'entry_index':1,'risk_r':-1}]},{'entries':[{'entry_index':1},{'entry_index':1}]},
                {'entries':[{'entry_index':1,'status':'winner'}]},{'delete_journal':True}):
            self.assertFalse(self.stage(patch)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)

    def test_spoken_ten_oclock_boundary_is_not_silently_normalized(self):
        story=deepcopy(STORY);story['thesis_invalidation']="close above ten o'clock"
        result=self.stage(story)
        self.assertIn('Which boundary',result['next_question'])
        self.assertEqual(result['story']['thesis_invalidation'],story['thesis_invalidation'])
    def test_same_turn_read_and_save_cache_tracks_story_revision(self):
        self.context.begin_turn('Save this reported story.')
        runner=lambda n,a:coach.coach_tool(self.db,10,20,n,a)
        first=self.context.run('stage_journal_story',{'story_json':json.dumps(STORY)},runner)
        args={'draft_id':first['draft_id']}
        old=self.context.run('get_journal_story',args,runner)
        self.assertTrue(self.context.run('save_journal_story',args,runner)['ok'])
        changed=self.context.run('stage_journal_story',{**args,'story_json':json.dumps({'entries':[{'entry_index':3,'notes':'Updated member report.'}]})},runner)
        read=self.context.run('get_journal_story',args,runner)
        self.assertGreater(read['revision'],old['revision'])
        self.assertEqual(read['story']['entries'][2]['notes'],'Updated member report.')
        saved=self.context.run('save_journal_story',args,runner)
        self.assertEqual(saved['revision'],changed['revision'])
        self.assertIn('Updated member report.',self.conn.execute('SELECT description FROM journals').fetchone()[0])
    def test_closed_trade_also_requires_reconciliation(self):
        env=self.handlers('bot.py');env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        self.conn.execute("UPDATE theses SET status='CLOSED'")
        result=self.saved(self.stage())
        self.assertFalse(result['ok']);self.assertEqual(result['candidate_trades'][0]['status'],'CLOSED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],1)
    def test_explicit_separate_trade_flag_updates_current_unsaved_draft(self):
        env=self.handlers('bot.py');env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        draft=self.stage();self.assertFalse(self.saved(draft)['ok'])
        self.assertFalse(self.stage({},draft_id=draft['draft_id'],new_trade=True)['ok'])
        distinct=self.call('stage_journal_story',{'draft_id':draft['draft_id'],'story_json':'{}','new_trade':True},'This is a separate trade; journal it.')
        self.assertTrue(distinct['ok'],distinct);saved=self.saved(distinct)
        self.assertTrue(saved['ok'],saved);self.assertEqual(saved['trade_number'],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],1)
    def test_clear_of_saved_facts_is_rejected_without_contradictory_metadata(self):
        draft=self.stage();self.assertTrue(self.saved(draft)['ok']);before=list(self.conn.iterdump())
        result=self.stage({'clear_fields':['play']},draft_id=draft['draft_id'])
        self.assertFalse(result['ok']);self.assertEqual(before,list(self.conn.iterdump()))
        self.assertEqual(self.context._journal_story['values']['play'],'Young Lefty')
    def test_save_optout_and_hypothetical_are_not_overridden_by_model_tool_call(self):
        draft=self.stage()
        result=self.call('save_journal_story',{'draft_id':draft['draft_id']},"Don't save this; just draft it.")
        self.assertFalse(result['ok']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
        example=self.call('stage_journal_story',{'new_draft':True,'story_json':json.dumps(STORY)},'Draft a hypothetical example; do not save it.')
        self.assertTrue(example['ok']);self.assertFalse(self.saved(example)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_malformed_stored_story_is_skipped_and_generic_metadata_cannot_forge_it(self):
        old=coach.coach_tool(self.db,10,20,'save_journal_entry',{'description':'Unrelated synthetic reflection','metadata_json':'{"kind":"reflection"}'})
        self.assertTrue(old['ok'])
        self.conn.execute('UPDATE journal_details SET metadata=?',(json.dumps({'kind':'reflection','journal_story':'not-json'}),))
        result=self.saved(self.stage());self.assertTrue(result['ok'],result)
        forged=coach.coach_tool(self.db,10,20,'save_journal_entry',{'description':'Unrelated','metadata_json':'{"journal_story":"hello"}'})
        self.assertFalse(forged['ok'])
    def test_story_schema_does_not_advertise_unsupported_market_binding(self):
        from gbop_voice_web.market_conversation import contextual_tools
        from gbop_voice_web.journal_story import STORY_TOOLS
        for tool in contextual_tools(STORY_TOOLS):self.assertNotIn('market_reference',tool['parameters']['properties'])
        self.context.begin_turn('Journal my reported sequence.')
        result=self.context.run('stage_journal_story',{'story_json':json.dumps(STORY),'market_reference':'selected_review'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertFalse(result['ok']);self.assertNotIn('market_review',str(result))

    def test_durable_retry_repairs_interrupted_post_save_context(self):
        draft=self.stage();self.assertTrue(self.saved(draft)['ok'])
        for key in ('saved_revision','saved_journal_id','selected_key'):
            self.context._journal_story.pop(key,None)
        before=list(self.conn.iterdump())
        result=self.saved(draft);self.assertTrue(result['ok']);self.assertTrue(result['deduplicated'])
        state=self.call('get_journal_story',{'draft_id':draft['draft_id']})
        self.assertTrue(state['saved']);self.assertEqual(before,list(self.conn.iterdump()))
    def test_direct_save_optout_revokes_prior_authorization_until_new_request(self):
        draft=self.stage()
        self.assertFalse(self.call('save_journal_story',{'draft_id':draft['draft_id']},"Don't save this yet.")['ok'])
        self.assertFalse(self.call('save_journal_story',{'draft_id':draft['draft_id']},'What is in the draft?')['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
        self.assertTrue(self.saved(draft)['ok'])

    def test_negated_separate_trade_request_cannot_bypass_reconciliation(self):
        env=self.handlers('bot.py');env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        draft=self.stage();result=self.call('stage_journal_story',{'draft_id':draft['draft_id'],'story_json':'{}','new_trade':True},'This is not a separate trade; use the existing one.')
        self.assertFalse(result['ok']);self.assertFalse(self.saved(draft)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],1)
    def test_invalid_new_draft_request_keeps_prior_narrative_intact(self):
        draft=self.stage();before=deepcopy(self.context._journal_story)
        result=self.call('stage_journal_story',{'story_json':'{}','new_draft':True,'new_trade':True},'Journal this correction.')
        self.assertFalse(result['ok']);self.assertEqual(self.context._journal_story,before)
        self.assertEqual(self.context._journal_story['id'],draft['draft_id'])
    def test_no_tool_optout_turn_revokes_save_authorization(self):
        draft=self.stage();self.context.begin_turn("Don't save this.")
        result=self.call('save_journal_story',{'draft_id':draft['draft_id']},'What is NAS100?')
        self.assertFalse(result['ok']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_short_direction_reconciles_existing_bearish_trade(self):
        env=self.handlers('bot.py');env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        story=deepcopy(STORY);story['direction']='short'
        result=self.saved(self.stage(story));self.assertFalse(result['ok'])
        self.assertEqual(result['candidate_trades'][0]['trade_number'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],1)
    def test_no_canonical_journal_selection_is_rechecked_by_raw_thesis_inside_transaction(self):
        env=self.handlers('bot.py')
        for _ in range(2):env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        self.conn.execute('DELETE FROM journals WHERE thesis_id=1')
        draft=self.stage(trade_number=1);fired=[]
        def concurrent_delete(sql,args):
            if 'pg_advisory_xact_lock(?)' in sql and not fired:
                fired.append(True);self.conn.execute('DELETE FROM theses WHERE id=1')
        self.before_execute=concurrent_delete
        result=self.saved(draft);self.before_execute=None
        self.assertTrue(fired);self.assertFalse(result['ok'],result)
        self.assertNotIn('Entry 3:',self.conn.execute('SELECT description FROM journals WHERE thesis_id=2').fetchone()[0])

    def test_concurrent_matching_trade_is_reconciled_inside_creation_transaction(self):
        draft=self.stage();fired=[]
        def other_channel_commit(sql,args):
            if 'pg_advisory_xact_lock(?)' in sql and not fired:
                fired.append(True)
                self.conn.execute("INSERT INTO theses(guild_id,user_id,asset,direction,play,status,max_r,created_at) VALUES(10,20,'NAS100','Bearish','Young Lefty','OPEN',1,'2040-02-06T12:00:00Z')")
        self.before_execute=other_channel_commit
        result=self.saved(draft);self.before_execute=None
        self.assertTrue(fired);self.assertFalse(result['ok'],result)
        self.assertEqual(result['status'],'existing_trade_selection_required')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
    def test_explicit_new_trade_permission_survives_serialized_candidate_check(self):
        draft=self.call('stage_journal_story',{'story_json':json.dumps(STORY),'new_trade':True},'Journal a separate trade.')
        env=self.handlers('bot.py');env['ai_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='Young Lefty',entry_model='Blessed Thief',risk_r=.1))
        self.assertTrue(self.saved(draft)['ok'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],2)

if __name__=='__main__':unittest.main()
