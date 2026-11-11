"""Invented narratives only; SQLite persistence and deterministic extraction tests.

Management events are reported narrative, not order execution or performance.
These tests do not claim microphone/model behavior or live PostgreSQL validation.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_day import _merge
from gbop_voice_web.journal_events import validate_events
from gbop_voice_web.journal_inference import infer_story_context
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture


class JournalFluidityTests(unittest.TestCase):
    review = fixture.JournalStoryTests.review
    handlers = fixture.JournalStoryTests.handlers
    setUp = fixture.JournalStoryTests.setUp
    tearDown = fixture.JournalStoryTests.tearDown
    call = fixture.JournalStoryTests.call
    saved = fixture.JournalStoryTests.saved

    def stage(self, values=None, text='Journal this reported trade.', **kwargs):
        return self.call('stage_journal_story', {'story_json':json.dumps(values or {}), **kwargs}, text=text)

    def read(self, draft):
        with self.db() as conn:
            return journal_drafts.read(conn,10,20,draft['draft_id'])[0]

    def test_delivered_paraphrase_is_not_repeated_but_interruption_is_not_receipt(self):
        draft=self.stage({'asset':'XAUUSD','trade_date':'2040-02-06'})
        question='Was your position bullish, meaning long, or bearish, meaning short?'
        self.context.complete_response(question,completed=False)
        same=self.stage({},draft_id=draft['draft_id'])
        self.assertIn('bullish',same['next_question'])
        self.context.complete_response(question,completed=True)
        next_result=self.stage({},draft_id=draft['draft_id'])
        self.assertNotEqual(next_result['next_question'],same['next_question'])
        self.assertIn('identity:direction',self.read(draft)['asked'])

    def test_direction_answer_with_classification_correction_is_not_missing(self):
        draft = self.stage({'asset':'XAUUSD','play':'9ate8'})
        self.assertEqual(draft['direction_status'],'missing')
        changed = self.stage({}, text="I was bullish, and it wasn't a confirmed Souper Soup.", draft_id=draft['draft_id'])
        self.assertEqual(changed['story']['direction'],'Bullish')
        self.assertNotEqual(changed['direction_status'],'missing')
        self.assertEqual(changed['provenance']['direction']['kind'],'explicit')
        fresh=MarketConversation((10,20,'resumed-event-story'),auth_provider=(self.db,10,20))
        self.addCleanup(fresh.close)
        read=self.call('get_journal_story',{'draft_id':draft['draft_id']},context=fresh)
        self.assertNotEqual(read['direction_status'],'missing')
        self.assertNotIn('bullish',read['next_question'] or '')

    def test_unpatched_direction_words_are_recognized_and_later_notes_preserve_them(self):
        draft=self.stage({'asset':'XAUUSD'},text='Journal my trade. I was bullish.')
        self.assertEqual(draft['story']['direction'],'Bullish')
        self.assertEqual(draft['direction_status'],'member_reported')
        later=self.stage({'context_notes':'A patient exit.'},draft_id=draft['draft_id'])
        self.assertEqual(later['direction_status'],'member_reported')

    def test_legacy_missing_status_is_projected_consistently_without_write(self):
        draft=self.stage({'asset':'XAUUSD'},text='Journal my trade. I was bullish.')
        with self.db() as conn:
            row=conn.execute('SELECT payload FROM journal_story_drafts WHERE id=?',(draft['draft_id'],)).fetchone()
            payload=json.loads(row['payload']);payload['direction_status']='missing'
            conn.execute('UPDATE journal_story_drafts SET payload=? WHERE id=?',(json.dumps(payload),draft['draft_id']))
        before=list(self.conn.iterdump())
        fresh=MarketConversation((10,20,'legacy-read'),auth_provider=(self.db,10,20))
        self.addCleanup(fresh.close)
        read=self.call('get_journal_story',{'draft_id':draft['draft_id']},context=fresh)
        self.assertEqual(read['direction_status'],'member_reported')
        self.assertEqual(list(self.conn.iterdump()),before)

    def test_null_fields_do_not_promote_inferred_or_unknown_facts(self):
        draft=self.stage({'play':'9ate8'},text='Journal this trade.')
        before=draft['provenance']['objective']
        changed=self.stage({'objective':None,'direction':None,'result_r':None,'entries':[]},
            text='Keep the current notes.',draft_id=draft['draft_id'])
        self.assertEqual(changed['provenance']['objective'],before)
        self.assertEqual(changed['provenance'].get('direction'),draft['provenance'].get('direction'))
        self.assertNotIn('result_r',changed['provenance'])

    def test_spoken_target_replaces_inferred_default_with_explicit_provenance(self):
        first=infer_story_context('Journal my 9ate8 trade.',values={'play':'9ate8'})
        for phrase in ('I was just targeting back to, um, the prior high.', 'I was targeting the prior high.'):
            result=infer_story_context(phrase,values=first['values'],context={'provenance':first['provenance']})
            self.assertEqual(result['values']['objective'],'the prior high')
            self.assertEqual(result['provenance']['objective']['kind'],'explicit')
            self.assertNotEqual(result['provenance']['objective']['source'],'contextual_target_default')
        hypothetical=infer_story_context('I would have targeted the prior low.',values=first['values'],context={'provenance':first['provenance']})
        self.assertEqual(hypothetical['values']['objective'],first['values']['objective'])

    def test_belief_or_idiom_is_not_a_position(self):
        from gbop_voice_web.journal_direction import narrated_direction
        for text in ('I was bullish on gold but stayed flat.', 'We are long overdue for a review.',
                     'I was long overdue for a review.', 'I was short of time.', 'I was bullish. I did not enter.'):
            self.assertEqual(narrated_direction(text),[],text)

    def test_target_words_starting_with_to_remain_intact(self):
        for objective in ('tomorrow’s high','total range liquidity'):
            result=infer_story_context('I was targeting '+objective+'.')
            self.assertEqual(result['values']['objective'],objective)

    def test_brief_identity_answer_updates_same_draft(self):
        draft=self.stage({'asset':'XAUUSD'},text='Journal my gold trade.',new_trade=True)
        self.assertFalse(draft['ok'])
        confirmed=self.stage({},text='Separate, separate.',draft_id=draft['draft_id'],new_trade=True)
        self.assertTrue(confirmed['ok'],confirmed)
        self.assertEqual(confirmed['draft_id'],draft['draft_id'])
        self.assertNotIn('separate new trade',confirmed['next_question'] or '')
        self.assertTrue(self.read(draft)['new_trade'])

    def reported_entries(self):
        return [
            {'entry_index':1,'entry_kind':'initial','status':'closed',
             'events':[{'event_index':1,'kind':'partial_exit','status':'reported','objective':'prior high'},
                       {'event_index':2,'kind':'runner_exit','status':'reported','pnl_text':'Slightly positive; units unreported','notes':'Stopped after retracement; further draw not reached.'}]},
            {'entry_index':2,'entry_kind':'reentry','status':'closed','pnl_text':'About -87; units unreported',
             'notes':'Added back the quantity taken off after a retracement; exact quantity unreported.'}]

    def test_partial_runner_reentry_persist_distinctly_and_reconnect_without_duplicates(self):
        entries=self.reported_entries()
        draft=self.stage({'asset':'XAUUSD','direction':'Bullish','entries':entries}, text='Journal this separate trade. I took a partial, kept a runner, and reentered later.',new_trade=True)
        self.assertTrue(draft['ok'],draft)
        self.assertEqual(draft['review_before_finalize']['reported_entry_count'],2)
        self.assertEqual(len(draft['review_before_finalize']['reported_executions'][0]['events']),2)
        self.assertEqual(self.read(draft)['values']['entries'],entries)
        self.assertNotIn('result_r',draft['story'])
        self.assertNotIn('reported_outcome',draft['story'])
        fresh=MarketConversation((10,20,'events-reconnect'),auth_provider=(self.db,10,20))
        self.addCleanup(fresh.close)
        read=self.call('get_journal_story',{'draft_id':draft['draft_id']},context=fresh)
        repeated=self.call('stage_journal_story',{'draft_id':draft['draft_id'],'story_json':json.dumps({'entries':entries})},text='Keep those same details.',context=fresh)
        self.assertEqual(repeated['story']['entries'],entries)
        self.assertEqual(len(repeated['story']['entries']),2)
        saved=self.call('save_journal_story',{'draft_id':draft['draft_id']},text='Save the journal.',context=fresh)
        self.assertTrue(saved['ok'],saved)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)
        metadata=json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(json.loads(metadata['journal_story'])['entries'],entries)
        self.assertIsNone(self.conn.execute('SELECT result_r FROM journals').fetchone()[0])

    def test_event_correction_patches_one_event_preserving_others(self):
        draft=self.stage({'entries':self.reported_entries()})
        corrected=self.stage({'entries':[{'entry_index':1,'events':[{'event_index':2,'pnl_text':'Breakeven, not positive'}]}]},draft_id=draft['draft_id'])
        self.assertTrue(corrected['ok'],corrected)
        self.assertEqual(corrected['story']['entries'][0]['events'][0],self.reported_entries()[0]['events'][0])
        self.assertEqual(corrected['story']['entries'][0]['events'][1]['kind'],'runner_exit')
        self.assertEqual(corrected['story']['entries'][0]['events'][1]['pnl_text'],'Breakeven, not positive')
        self.assertEqual(corrected['story']['entries'][1],self.reported_entries()[1])
        self.assertTrue(self.read(draft)['corrections'])

    def test_scoped_event_clear_preserves_siblings_and_reentry(self):
        draft=self.stage({'entries':self.reported_entries()})
        changed=self.stage({'clear_fields':['entries.1.events.2.pnl_text']},draft_id=draft['draft_id'])
        self.assertNotIn('pnl_text',changed['story']['entries'][0]['events'][1])
        self.assertEqual(len(changed['story']['entries'][0]['events']),2)
        changed=self.stage({'clear_fields':['entries.1.events.1']},draft_id=draft['draft_id'])
        self.assertEqual([e['event_index'] for e in changed['story']['entries'][0]['events']],[2])
        self.assertEqual(changed['story']['entries'][1],self.reported_entries()[1])
        self.assertTrue(self.read(draft)['corrections'])

    def test_followup_interpretation_does_not_need_range_name(self):
        draft=self.stage({'asset':'XAUUSD','play':'9ate8'},text='Journal my 9ate8 range, described as a Souper Soup.')
        before=self.read(draft)['journal_range']
        changed=self.stage({},text='It was not a Souper Soup; it was a local CRT.',draft_id=draft['draft_id'])
        current=self.read(draft)['journal_range']
        self.assertEqual(current['selectors'],before['selectors'])
        self.assertEqual(current['status'],before['status'])
        self.assertIn('local CRT',current['description'])
        self.assertIn('local CRT',changed['journal_range']['reported_interpretation'])

    def test_null_event_patch_preserves_known_events(self):
        original={'entries':self.reported_entries()}
        merged=_merge(original,{'entries':[{'entry_index':1,'events':None,'pnl_text':None}]})
        self.assertEqual(merged,original)

    def test_day_merge_preserves_management_events_without_counting_extra_fills(self):
        value={'entries':self.reported_entries()}
        merged=_merge(value,{'entries':[{'entry_index':1,'events':[{'event_index':2,'notes':'Corrected stop note.'}]}]})
        self.assertEqual(len(merged['entries']),2)
        self.assertEqual(len(merged['entries'][0]['events']),2)
        self.assertEqual(merged['entries'][0]['events'][0],value['entries'][0]['events'][0])

    def test_event_shape_failures_retain_raw_narration(self):
        for events in ([{'event_index':1,'kind':'partial_exit'},{'event_index':1,'kind':'runner_exit'}],
                       [{'event_index':1,'kind':'profit_guess'}],
                       [{'event_index':True,'kind':'exit'}],
                       [{'event_index':1,'status':'executed'}],
                       [{'event_index':1,'currency':'USD'}]):
            with self.subTest(events=events):
                result=self.stage({'entries':[{'entry_index':1,'events':events}]},text='The narration must survive invalid extraction.')
                self.assertFalse(result['ok'],result)
                self.assertTrue(result['persisted'],result)
                self.assertIn('narration must survive',result['raw_story_text'])

    def test_range_correction_replaces_stale_report_but_preserves_raw_history(self):
        draft=self.stage({'asset':'XAUUSD','play':'9ate8'},text='Journal my 9ate8 range, described as a Souper Soup.')
        corrected=self.stage({'context_notes':'Reported local CRT interpretation only.'},
            text="It was not a Souper Soup of that 9ate8 range; technically it was a local CRT interpretation.",draft_id=draft['draft_id'])
        self.assertTrue(corrected['ok'],corrected)
        self.assertIn('local CRT interpretation',corrected['journal_range']['description'])
        self.assertIsNone(corrected['range_question'])
        stored=self.read(draft)
        self.assertTrue(stored['journal_range']['history'])
        self.assertIn('described as a Souper Soup',str(stored['raw_story']))




import test_journal_lifecycle_persistence as persistent


class JournalEventPersistenceTests(unittest.TestCase):
    setUp=persistent.JournalLifecyclePersistenceTests.setUp
    review=persistent.JournalLifecyclePersistenceTests.review
    handlers=persistent.JournalLifecyclePersistenceTests.handlers
    fresh=persistent.JournalLifecyclePersistenceTests.fresh
    action=persistent.JournalLifecyclePersistenceTests.action
    stage=persistent.JournalLifecyclePersistenceTests.stage
    save=persistent.JournalLifecyclePersistenceTests.save
    stored=persistent.JournalLifecyclePersistenceTests.stored
    history=persistent.JournalLifecyclePersistenceTests.history
    count=persistent.JournalLifecyclePersistenceTests.count
    reopen=persistent.JournalLifecyclePersistenceTests.reopen

    def test_events_survive_actual_database_reopen_finalization_and_retry(self):
        entries=JournalFluidityTests.reported_entries(self)
        draft=self.stage({'asset':'XAUUSD','direction':'Bullish','entries':entries},
                         'This is a separate trade. Journal my gold trade. I entered long.',new_trade=True)
        self.assertTrue(draft['persisted'],draft)
        self.reopen()
        read=self.action('get_journal_story',{'draft_id':draft['draft_id']},'Resume my journal.')
        self.assertEqual(read['story']['entries'],entries)
        saved=self.save(read)
        self.assertTrue(saved['ok'],saved)
        self.reopen()
        recalled=self.history(saved['trade_number'])
        metadata=recalled['journals'][0]['metadata']
        self.assertEqual(json.loads(metadata['journal_story'])['entries'],entries)
        again=self.save({'draft_id':draft['draft_id']})
        self.assertEqual(again['trade_number'],saved['trade_number'])
        self.assertEqual((self.count('journals'),self.count('journal_story_drafts'),self.count('thesis_executions')),(1,1,0))


if __name__=='__main__':unittest.main()
