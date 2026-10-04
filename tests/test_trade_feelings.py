"""Invented members/in-memory journal only. No production trades or messages."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import test_contextual_journals as contextual
from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web import trade_feelings as feelings
from gbop_voice_web.journal_context import JournalBinding, WRITE_TOOLS
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.journal_presentation import journal_tool_payload, metadata_summary
from gbop_voice_web.voice_policy import build_voice_instructions


class TradeFeelingTests(unittest.TestCase):
    setUp = contextual.ContextualJournalTests.setUp
    tearDown = contextual.ContextualJournalTests.tearDown
    review = contextual.ContextualJournalTests.review
    handlers = contextual.ContextualJournalTests.handlers

    def action(self, name, args, text, *, path='bot.py', context=None):
        context = context or self.context
        context.begin_turn(text)
        env = self.handlers(path)
        prefix = 'ai_' if path == 'bot.py' else 'tool_'
        return context.run(name, args, lambda n,a: coach.coach_tool(self.db,10,20,n,a)
            if n in coach.COACH_NAMES else env[prefix+n](20,a))

    def opened(self, path='bot.py', text='I opened a synthetic test trade.'):
        result = self.action('open_trade', dict(asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25),text,path=path)
        self.assertTrue(result['ok'],result)
        return result

    def save(self, number=1, feeling='calm', stage='open', text='I feel calm', **extra):
        return self.action('record_trade_feeling', dict(trade_number=number,feeling=feeling,stage=stage,
            reported_at=None,correction_of=None,skip=False,**extra),text)

    def metadata(self, number=1):
        result=recall.history(self.db,10,20,{'trade_number':number})
        return result['journals'][0]['metadata']

    def test_full_flow_all_stages_history_recall_both_handlers(self):
        for path in ('bot.py','gbop_voice_web/server.py'):
            with self.subTest(path=path):
                opened=self.opened(path);number=opened['trade_id']
                self.assertEqual(opened['optional_feeling_prompt']['stage'],'open')
                self.assertTrue(self.save(number, text='calm')['ok'])
                added=self.action('add_entry',dict(trade_id=number,entry_model='Super Soup',risk_r=.25),'I added to my trade.',path=path)
                self.assertTrue(added['ok'],added);self.assertNotIn('optional_feeling_prompt',added)
                self.assertTrue(self.save(number,'nervous','add','I feel nervous after adding.')['ok'])
                update=self.action('record_trade_event',dict(trade_id=number,event='Management',details='Held the planned stop.',result_r=None),'I held the planned stop.',path=path)
                self.assertTrue(update['ok']);self.assertNotIn('optional_feeling_prompt',update)
                self.assertTrue(self.save(number,'focused','mid','I feel focused during the trade.')['ok'])
                closed=self.action('close_trade',dict(trade_id=number,summary='Closed reported trade',rule_adherence='',study_note='',final_result_r=None),'I closed the trade.',path=path)
                self.assertTrue(closed['ok']);self.assertIsNone(closed['final_result_r'])
                self.assertTrue(self.save(number,'relieved','close','I feel relieved after closing.')['ok'])
                reports=self.metadata(number)['feeling_history']
                self.assertEqual([r['stage'] for r in reports],list(feelings.STAGES))
                self.assertTrue(all(r['reported_at'] is None and r['recorded_at'] for r in reports))
                self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],number)
                delivered='\n'.join(recall.messages(recall.history(self.db,10,20,{'trade_number':number})))
                for word in ('calm','nervous','focused','relieved','reported time: unknown','logged:'):
                    self.assertIn(word,delivered)
                self.assertEqual(recall.history(self.db,10,30,{})['journals'],[])

    def test_correction_appends_and_does_not_replace_original(self):
        self.opened();self.save()
        result=self.action('record_trade_feeling',dict(trade_number=1,feeling='anxious',stage='open',reported_at=contextual.NY('10:05'),correction_of=1,skip=False),'Correct that: I felt anxious, not calm.')
        self.assertTrue(result['ok'],result)
        history=self.metadata()['feeling_history'];self.assertEqual(len(history),2)
        self.assertEqual(history[0]['feeling'],'calm');self.assertEqual(history[1]['correction_of'],1)
        self.assertEqual(history[1]['reported_at'],contextual.NY('10:05'))
        self.assertNotEqual(history[1]['reported_at'],history[1]['recorded_at'])
        self.assertEqual([r['feeling'] for r in feelings.effective_history(self.metadata())],['anxious'])
        self.assertIn('corrects feeling #1','\n'.join(recall.messages(recall.history(self.db,10,20,{}))))

    def test_skip_does_not_save_a_feeling_and_stops_future_prompts(self):
        self.opened()
        skipped=self.action('record_trade_feeling',dict(trade_number=1,feeling=None,stage=None,reported_at=None,correction_of=None,skip=True),'Skip this please.')
        self.assertTrue(skipped['ok'],skipped);self.assertTrue(skipped['skipped'])
        self.assertNotIn('feeling_history',self.metadata())
        with patch.object(feelings,'_now',return_value=datetime.now(timezone.utc)+timedelta(hours=1)):
            closed=self.action('close_trade',dict(trade_id=1,summary='Closed',rule_adherence='',study_note=''),'I closed it.')
        self.assertTrue(closed['ok']);self.assertNotIn('optional_feeling_prompt',closed)
        self.assertTrue(self.save(1,'calm','close','I feel calm now after closing.')['ok'])

    def test_ignore_never_blocks_add_close_or_watch_requests(self):
        self.opened()
        for text in ('Watch BTC and alert me at the level.', 'Show the current price.', 'hello', 'thanks', 'I think the trade will win'):
            self.context.begin_turn(text)
            seen=[]
            result=self.context.run('manage_market_watch',{'asset':'BTCUSD'},lambda n,a:seen.append((n,a)) or {'ok':True,'registered':True})
            self.assertTrue(result['registered']);self.assertEqual(seen[0][0],'manage_market_watch')
            attempted=self.context.run('record_trade_feeling',dict(trade_number=1,stage='open',feeling=text,reported_at=None,correction_of=None,skip=False),lambda n,a:coach.coach_tool(self.db,10,20,n,a))
            self.assertFalse(attempted['ok'],attempted)
        self.assertNotIn('feeling_history',self.metadata())
        closed=self.action('close_trade',dict(trade_id=1,summary='Closed',rule_adherence='',study_note=''),'Closed it.')
        self.assertTrue(closed['ok'])

    def test_no_repeated_prompt_on_adds_and_reconnect(self):
        self.opened()
        for _ in range(3):
            added=self.action('add_entry',dict(trade_id=1,entry_model='Super Soup',risk_r=.01),'I added again.')
            self.assertTrue(added['ok']);self.assertNotIn('optional_feeling_prompt',added)
        self.context.close();self.context=MarketConversation((10,20,'reconnect'),auth_provider=(self.db,10,20))
        added=self.action('add_entry',dict(trade_id=1,entry_model='Super Soup',risk_r=.01),'Another planned add.')
        self.assertNotIn('optional_feeling_prompt',added)
        self.assertFalse(self.save(text='calm')['ok'])  # No inherited short-answer capability.
        self.assertTrue(self.save(text='I feel calm')['ok'])

    def test_close_can_offer_one_later_optional_prompt(self):
        self.opened()
        with patch.object(feelings,'_now',return_value=datetime.now(timezone.utc)+timedelta(hours=1)):
            result=self.action('close_trade',dict(trade_id=1,summary='Closed',rule_adherence='',study_note=''),'I closed it.')
        self.assertEqual(result['optional_feeling_prompt']['stage'],'close')

    def test_unknown_trade_and_stage_ask_minimal_clarification(self):
        self.opened();self.opened()
        for number in (None,0,True,999):
            result=self.save(number=number)
            self.assertFalse(result['ok'])
        self.assertFalse(self.save(stage=None)['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_revoke_cancel_replaced_member_and_forged_binding_fail_closed(self):
        self.opened();self.context.begin_turn('I feel calm')
        arguments=dict(trade_number=1,feeling='calm',stage='open',reported_at=None,correction_of=None,skip=False)
        stale={'_journal_binding':JournalBinding(self.context,self.context.generation),**arguments}
        self.context.invalidate()
        self.assertFalse(coach.coach_tool(self.db,10,20,'record_trade_feeling',stale)['ok'])
        self.assertFalse(coach.coach_tool(self.db,10,20,'record_trade_feeling',{**arguments,'_journal_binding':{'fake':True}})['ok'])
        self.assertFalse(coach.coach_tool(self.db,10,20,'record_trade_feeling',arguments)['ok'])
        self.context.begin_turn('I feel calm')
        bound={**arguments,'_journal_binding':JournalBinding(self.context,self.context.generation)}
        self.assertFalse(coach.coach_tool(self.db,10,30,'record_trade_feeling',bound)['ok'])
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        self.assertFalse(coach.coach_tool(self.db,10,20,'record_trade_feeling',bound)['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_revocation_after_initial_check_is_fenced_at_final_write(self):
        self.opened();fired=[]
        def hook(sql,params):
            if 'pg_advisory_xact_lock(?)' in sql and not fired:
                fired.append(True);self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        self.before_execute=hook
        outcome=self.save()
        self.before_execute=None
        self.assertFalse(outcome['ok'],outcome);self.assertNotIn('feeling_history',self.metadata())

    def test_parallel_user_and_guild_cannot_select_other_members_trade(self):
        self.opened()
        context=MarketConversation((10,30,'other'),auth_provider=(self.db,10,30));context.begin_turn('I feel calm')
        result=context.run('record_trade_feeling',dict(trade_number=1,feeling='calm',stage='open'),lambda n,a:coach.coach_tool(self.db,10,30,n,a))
        self.assertFalse(result['ok']);self.assertNotIn('feeling_history',self.metadata())

    def test_report_is_not_linked_to_unrelated_selected_market_review(self):
        self.opened();self.review()
        result=self.save()
        self.assertTrue(result['ok']);self.assertNotIn('market_review',self.metadata())

    def test_repeated_tool_call_same_turn_and_timestamped_reconnect_deduplicate(self):
        self.opened();self.context.begin_turn('I felt calm at 10:05')
        args=dict(trade_number=1,feeling='calm',stage='open',reported_at=contextual.NY('10:05'))
        runner=lambda n,a:coach.coach_tool(self.db,10,20,n,a)
        first=self.context.run('record_trade_feeling',args,runner)
        second=self.context.run('record_trade_feeling',args,runner)
        self.assertEqual(first,second)
        self.context=MarketConversation((10,20,'new'),auth_provider=(self.db,10,20));self.context.begin_turn('I felt calm at 10:05')
        result=self.context.run('record_trade_feeling',args,runner)
        self.assertTrue(result['deduplicated']);self.assertEqual(len(self.metadata()['feeling_history']),1)

    def test_history_is_bounded_not_pruned_and_protected_from_metadata_overwrite(self):
        self.opened()
        with patch.object(feelings,'MAX_REPORTS',2):
            self.assertTrue(self.save()['ok']);self.assertTrue(self.save()['ok'])
            result=self.save(feeling='nervous',text='I feel nervous')
            self.assertFalse(result['ok'])
        self.assertEqual([r['feeling'] for r in self.metadata()['feeling_history']],['calm','calm'])
        outcome=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(trade_number=1,metadata_json='{"feeling_history":[]}'))
        self.assertFalse(outcome['ok'])
        self.assertEqual(len(self.metadata()['feeling_history']),2)

    def test_legacy_emotion_preserved_without_duplicate_import(self):
        self.opened()
        coach.coach_tool(self.db,10,20,'save_journal_entry',dict(trade_number=1,metadata_json='{"emotion":"Earlier handwritten note"}'))
        self.save()
        self.assertEqual(self.metadata()['emotion'],'Earlier handwritten note')
        self.assertEqual(len(self.metadata()['feeling_history']),1)

    def test_invalid_report_time_correction_and_decline_do_not_write(self):
        self.opened()
        for args in ({'reported_at':'2026-10-02T10:00:00'},{'reported_at':'yesterday'},{'correction_of':999},{'feeling':'x'*501}):
            values=dict(trade_number=1,feeling='calm',stage='open',reported_at=None,correction_of=None,skip=False);values.update(args)
            self.assertFalse(self.action('record_trade_feeling',values,'I feel '+values['feeling'])['ok'])
        invalid_skip=self.action('record_trade_feeling',dict(trade_number=1,feeling='calm',stage=None,skip=True),'Skip this.')
        self.assertFalse(invalid_skip['ok']);self.assertNotIn('feeling_history',self.metadata())

    def test_cannot_attach_trade_feeling_to_hypothetical_study(self):
        row=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(description='Synthetic study',metadata_json='{"kind":"study"}'))
        self.assertFalse(self.save(number=row['trade_number'])['ok'])

    def test_optional_prompt_failure_never_relabels_successful_trade_save(self):
        with patch.object(feelings,'optional_prompt',side_effect=RuntimeError('test fault')):
            result=self.opened()
        self.assertTrue(result['ok']);self.assertTrue(result['optional_feeling_prompt_unavailable'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],1)

    def test_explicit_feeling_in_open_message_avoids_reasking(self):
        result=self.opened(text='I opened the trade and I feel nervous.')
        self.assertNotIn('optional_feeling_prompt',result)

    def test_performance_is_descriptive_missing_aware_and_no_double_outcomes(self):
        self.opened();self.save();self.save()
        self.opened()
        report=coach.performance(self.db,10,20,{})['feeling_associations']
        self.assertEqual(report['trades_with_feelings'],1);self.assertEqual(report['trades_without_feelings'],1)
        self.assertEqual(report['groups'][0]['entries'],1)
        self.assertEqual(report['groups'][0]['missing_outcomes'],1)
        self.assertIn('No causation',report['limits'])
        self.assertIn('Groups overlap',report['limits'])

    def test_presentation_includes_bounded_history_and_full_delivery_remains_complete(self):
        self.opened()
        for i in range(8):self.save(feeling='calm '+str(i),text='I feel calm '+str(i))
        raw=self.metadata();saved=deepcopy(raw);summary=metadata_summary(raw)
        self.assertEqual(summary['feeling_history_count'],8)
        self.assertEqual(len(summary['feeling_history']),6)
        self.assertTrue(summary['feeling_history_details_omitted']);self.assertEqual(raw,saved)
        delivered='\n'.join(recall.messages(recall.history(self.db,10,20,{})))
        for i in range(8):self.assertIn('calm '+str(i),delivered)

    def test_hypotheticals_and_someone_elses_feeling_are_not_member_reports(self):
        self.opened()
        for text,feeling in (("If I feel nervous should I stop?",'nervous'),
                ('I feel calm. My friend felt anxious.','anxious'),
                ('The trade lost money.','anxious')):
            self.assertFalse(self.save(feeling=feeling,text=text)['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_short_reply_cannot_jump_to_another_trade_or_stage(self):
        self.opened();self.opened()
        self.assertFalse(self.save(number=1,text='calm')['ok'])
        self.assertFalse(self.save(number=2,stage='close',text='calm')['ok'])

    def test_audio_contract_keeps_explicit_tool_values_without_new_asr(self):
        self.opened()
        self.context.begin_turn(None)
        result=self.context.run('record_trade_feeling',dict(trade_number=1,stage='open',
            feeling='calm',reported_at=None,skip=False),lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['feeling']['source'],'member_reported')
        self.assertIsNone(result['feeling']['reported_at'])

    def test_generic_journal_edits_preserve_feeling_history_and_state(self):
        self.opened();self.save();before=self.metadata()
        result=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(trade_number=1,
            description='A corrected ordinary journal summary',metadata_json='{"play":"Corrected play"}'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.metadata()['feeling_history'],before['feeling_history'])
        self.assertEqual(self.metadata()['feeling_prompt_state'],before['feeling_prompt_state'])

    def test_catalogue_and_voice_guidance_cover_same_new_tool(self):
        self.assertIn('record_trade_feeling',WRITE_TOOLS)
        catalog=contextual_tools(coach.COACH_TOOLS)
        tool=next(row for row in catalog if row['name']=='record_trade_feeling')
        self.assertEqual(set(tool['parameters']['required']),set(tool['parameters']['properties']))
        voice=build_voice_instructions('Canon','Market','Profile')
        self.assertIn('record_trade_feeling',voice);self.assertIn('OPTIONAL',voice)
        self.assertNotIn('feeling_history',coach.META_KEYS)
