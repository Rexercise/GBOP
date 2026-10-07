"""Synthetic SQLite regression coverage; never connects to member data."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach, journal_drafts
from gbop_voice_web.journal_story import STORY_PROMPT, story_prompt_context
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture
STORY=fixture.STORY


class ProgressiveJournalTests(unittest.TestCase):
    setUp=fixture.JournalStoryTests.setUp
    tearDown=fixture.JournalStoryTests.tearDown
    review=fixture.JournalStoryTests.review
    handlers=fixture.JournalStoryTests.handlers
    call=fixture.JournalStoryTests.call
    stage=fixture.JournalStoryTests.stage
    saved=fixture.JournalStoryTests.saved
    # Inherit the same synthetic member fixture, not its test methods.
    def fresh(self,user=20):
        return MarketConversation((10,user,'reconnected'),auth_provider=(self.db,10,user))

    def stored(self,draft_id):
        with self.db() as conn:return journal_drafts.read(conn,10,20,draft_id)[0]

    def test_raw_narration_persists_before_model_or_question(self):
        text='Journal my NAS trade. I entered short but have not finished telling you the story.'
        self.context.begin_turn(text)
        rows=self.conn.execute('SELECT * FROM journal_story_drafts').fetchall()
        self.assertEqual(len(rows),1)
        value=json.loads(rows[0]['payload'])
        self.assertEqual(value['raw_story'][0]['text'],text)
        self.assertEqual(value['values']['asset'],'NAS100')
        self.assertEqual(value['values']['direction'],'Bearish')
        self.assertEqual(rows[0]['status'],'unfinished')
        self.assertIn('CURRENT UNFINISHED',story_prompt_context(self.context))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM theses').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0],0)
        report=coach.performance(self.db,10,20,{})
        self.assertEqual(report['summary']['known_outcomes'],0)
        self.assertEqual(report['summary']['missing_outcomes'],0)

    def test_reconnect_resumes_same_draft_facts_and_raw_narration(self):
        first=self.stage();original=self.stored(first['draft_id'])
        self.context.close();fresh=self.fresh()
        resumed=self.call('get_journal_story',{},text='Continue my journal.',context=fresh)
        self.assertEqual(resumed['draft_id'],first['draft_id'])
        self.assertTrue(resumed['persisted']);self.assertEqual(len(resumed['story']['entries']),3)
        corrected=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':json.dumps({'entries':[{'entry_index':2,'notes':'Actually exited around midnight the next day.'}]})},text='Actually exited around midnight the next day.',context=fresh)
        self.assertTrue(corrected['ok'],corrected)
        stored=self.stored(first['draft_id'])
        self.assertEqual(stored['raw_story'][0],original['raw_story'][0])
        self.assertIn('Actually exited',stored['raw_story'][-1]['text'])
        self.assertTrue(stored['corrections'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],1)

    def test_journaling_does_not_imply_finalization(self):
        first=self.stage()
        result=self.call('save_journal_story',{'draft_id':first['draft_id']},text='Journal this reported story.')
        self.assertFalse(result['ok']);self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')
        finalized=self.saved(first);self.assertTrue(finalized['ok'],finalized)
        stored=self.stored(first['draft_id'])
        self.assertEqual(stored['draft_status'],'finalized')
        self.assertEqual(stored['saved_journal_id'],self.conn.execute('SELECT id FROM journals').fetchone()[0])
        self.assertTrue(stored['raw_story'])
        self.assertIsNone(self.conn.execute('SELECT result_r FROM journals').fetchone()[0])

    def test_unrelated_turns_are_not_background_recorded(self):
        self.context.begin_turn('How is the weather?')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)
        first=self.stage();before=self.stored(first['draft_id'])
        self.context.begin_turn('What restaurants are open?')
        self.assertEqual(self.stored(first['draft_id']),before)

    def test_explicit_preview_does_not_persist(self):
        result=self.call('stage_journal_story',{'story_json':json.dumps(STORY)},text='Do not save this. Preview only.')
        self.assertTrue(result['ok'],result);self.assertFalse(result['persisted'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)

    def test_storage_failure_never_claims_saved(self):
        original=journal_drafts.write
        with patch.object(journal_drafts,'write',side_effect=RuntimeError('synthetic storage failure')):
            self.context.begin_turn('Journal my NAS trade. I entered short around nine this morning.')
        self.assertIn('could not be saved',story_prompt_context(self.context))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)
        result=self.stage();self.assertTrue(result['persisted'])
        self.assertNotIn('STORAGE NOTICE',story_prompt_context(self.context))

    def test_stale_conversation_cannot_overwrite_newer_draft(self):
        first=self.stage();other=self.fresh()
        self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read my draft.',context=other)
        self.stage({'context_notes':'Newer correction from first device.'},draft_id=first['draft_id'])
        result=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Stale device overwrite"}'},text='Correction.',context=other)
        self.assertFalse(result['ok']);self.assertIn('another conversation',result['error'])
        self.assertEqual(self.stored(first['draft_id'])['values']['context_notes'],'Newer correction from first device.')
        refreshed=self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read my draft.',context=other)
        self.assertTrue(refreshed['ok'])

    def test_multiple_unfinished_drafts_require_selection(self):
        first=self.stage()
        second=self.call('stage_journal_story',{'story_json':'{"asset":"EURUSD","context_notes":"A separate trade."}','new_draft':True,'new_trade':True},text='Journal a separate trade.')
        self.assertTrue(second['ok'],second)
        fresh=self.fresh();choices=self.call('get_journal_story',{},text='Continue my journal.',context=fresh)
        self.assertEqual(choices['status'],'draft_selection_required');self.assertEqual(len(choices['drafts']),2)
        selected=self.call('get_journal_story',{'draft_id':first['draft_id']},text='Use the first journal.',context=fresh)
        self.assertEqual(selected['draft_id'],first['draft_id'])
        selected=self.call('get_journal_story',{'draft_id':second['draft_id']},text='Use the other journal.',context=fresh)
        self.assertEqual(selected['draft_id'],second['draft_id'])

    def test_audio_requires_actual_narration_and_resumes_same_member(self):
        fresh=self.fresh();fresh.begin_turn(None)
        runner=lambda n,a:coach.coach_tool(self.db,10,20,n,a)
        rejected=fresh.run('stage_journal_story',{'story_json':'{"asset":"NAS"}'},runner)
        self.assertFalse(rejected['ok'])
        result=fresh.run('stage_journal_story',{'story_json':'{"asset":"NAS"}','raw_story':'I am journaling my NAS trade, still describing it.'},runner)
        self.assertTrue(result['persisted'],result);self.assertEqual(result['story']['asset'],'NAS100')
        stored=self.stored(result['draft_id']);self.assertEqual(stored['raw_story'][0]['source'],'voice_transcription')

    def test_raw_only_interrupted_narration_is_recoverable_after_reconnect(self):
        text='Journal my NAS trade. I waited for the second sweep, accidentally entered twice, cancelled the resting order, then my broker disconnected. The second entry was a hedge.'
        self.context.begin_turn(text)
        self.context.close()
        result=self.call('get_journal_story',{},text='Continue my journal.',context=self.fresh())
        self.assertIn(text,result['raw_story_text'])
        self.assertFalse(result['raw_has_more'])
        self.assertIn('broker disconnected',result['raw_story_text'])

    def test_raw_story_pages_are_lossless(self):
        text='Journal my NAS trade. '+('Narrated context with uncertainty. '*400)
        self.context.begin_turn(text)
        fresh=self.fresh();result=self.call('get_journal_story',{},text='Continue my journal.',context=fresh)
        pieces=[result['raw_story_text']]
        while result['raw_has_more']:
            result=self.call('get_journal_story',{'draft_id':result['draft_id'],'raw_offset':result['next_raw_offset']},text='Read the rest.',context=fresh)
            pieces.append(result['raw_story_text'])
        self.assertIn(text,''.join(pieces));self.assertEqual(len(''.join(pieces)),result['raw_total_chars'])

    def test_known_entry_date_is_not_reasked(self):
        result=self.stage({'asset':'NAS100','direction':'Bearish','entries':[{'entry_index':1,'reported_entry_at':'2026-10-02T10:05:00-04:00'}]})
        self.assertIsNone(result['next_question'])

    def test_privacy_pause_without_draft_and_across_reconnect(self):
        self.context.begin_turn('Do not record this conversation. I only want a preview.')
        result=self.call('stage_journal_story',{'story_json':'{"asset":"NAS"}'},text='I entered NAS but I am still only talking through it.')
        self.assertFalse(result['persisted'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)
        first=self.stage();self.assertTrue(first['persisted'])
        self.context.begin_turn('Stop recording this journal until I explicitly ask to resume.')
        fresh=self.fresh();self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read the saved draft.',context=fresh)
        before=self.stored(first['draft_id'])
        preview=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Private unrecorded continuation"}'},text='I only wanted to discuss this next bit.',context=fresh)
        self.assertFalse(preview['persisted']);self.assertEqual(self.stored(first['draft_id']),before)
        fresh.begin_turn(None)
        denied=fresh.run('save_journal_story',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertFalse(denied['ok']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)
        fresh.begin_turn(None)
        explicit=fresh.run('save_journal_story',{'draft_id':first['draft_id'],'confirmation_text':'Save this journal.'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(explicit['ok'],explicit)

    def test_contextual_target_default_is_labeled_and_explicit_target_wins(self):
        first=self.call('stage_journal_story',{'story_json':'{}'},text='Journal my NAS 9ate8 trade.')
        self.assertEqual(first['story']['objective'],'Opposing liquidity of the 8 range')
        self.assertEqual(first['provenance']['objective']['source'],'contextual_target_default')
        self.assertNotIn('direction',first['story'])
        self.assertEqual(first['provenance']['asset']['kind'],'inferred')
        corrected=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{}'},text='My target was the midpoint of the 8 range.')
        self.assertEqual(corrected['story']['objective'],'the midpoint of the 8 range')
        self.assertEqual(corrected['provenance']['objective']['kind'],'explicit')
        self.assertTrue(self.saved(corrected)['ok'])
        meta=json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(meta['story_provenance']['objective']['kind'],'explicit')
        self.assertIn('asset',meta['provenance']['narration_inferred'])
        self.assertNotIn('asset',meta['provenance']['member_reported'])

    def test_context_corrections_refresh_only_inferred_targets(self):
        first=self.call('stage_journal_story',{'story_json':'{"play":"9ate8"}'},text='Journal my NAS 9ate8 trade.')
        nine=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{}'},text='I was trading the 9 range.')
        self.assertEqual(nine['story']['objective'],'Opposing liquidity of the 9 range')
        corrected=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"play":"Young Lefty"}'},text='Actually this was Young Lefty.')
        self.assertNotIn('objective',corrected['story'])
        explicit=self.stage({'objective':'My chosen midpoint'},draft_id=first['draft_id'])
        changed=self.stage({'play':'9ate8'},draft_id=first['draft_id'])
        self.assertEqual(changed['story']['objective'],'My chosen midpoint')

    def test_explicit_clear_cannot_be_refilled_from_rejected_raw_name(self):
        first=self.call('stage_journal_story',{'story_json':'{"play":"9ate8"}'},text='Journal my NAS 9ate8 trade.')
        cleared=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"clear_fields":["play"]}'},text='Remove 9ate8 from the play; I have not decided the play.')
        self.assertNotIn('play',cleared['story']);self.assertNotIn('objective',cleared['story'])
        self.assertNotIn('9ate8',cleared['story'].get('title',''))
        target=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"play":"9ate8","clear_fields":["objective"]}'},text='Keep 9ate8, but clear the target.')
        self.assertNotIn('objective',target['story'])
        next_turn=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{}'},text='My entry was around 9 AM.')
        self.assertNotIn('objective',next_turn['story'])
        fresh=self.fresh()
        self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read my draft.',context=fresh)
        still_clear=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{}'},text='I waited patiently.',context=fresh)
        self.assertNotIn('objective',still_clear['story'])
        supplied=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"objective":"midpoint"}'},text='My objective was midpoint.',context=fresh)
        self.assertEqual(supplied['story']['objective'],'midpoint')

    def test_short_resume_clears_durable_pause_without_repeated_command(self):
        first=self.stage();self.context.begin_turn('Stop recording this journal.')
        fresh=self.fresh();resumed=self.call('get_journal_story',{'draft_id':first['draft_id']},text='Resume my journal.',context=fresh)
        self.assertTrue(resumed['ok']);self.assertFalse(fresh._journal_recording_paused)
        changed=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Continuing after the pause."}'},text='Continuing after the pause.',context=fresh)
        self.assertTrue(changed['persisted']);self.assertFalse(self.stored(first['draft_id']).get('recording_paused'))

    def test_reading_an_old_draft_cannot_clear_new_session_optout(self):
        first=self.stage();fresh=self.fresh()
        fresh.begin_turn('Do not record anything in this conversation.')
        self.call('get_journal_story',{'draft_id':first['draft_id']},text='Read the old journal only.',context=fresh)
        self.assertTrue(fresh._journal_recording_paused)
        before=self.stored(first['draft_id'])
        preview=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Private discussion"}'},text='I only want to discuss this.',context=fresh)
        self.assertFalse(preview['persisted']);self.assertEqual(self.stored(first['draft_id']),before)

    def test_conflicting_merged_times_keep_raw_and_block_finalization(self):
        first=self.stage({'asset':'NAS100','direction':'Bearish','entries':[{'entry_index':1,'reported_entry_at':'2026-10-02T10:00:00-04:00','reported_exit_at':'2026-10-02T11:00:00-04:00'}]})
        changed=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"entries":[{"entry_index":1,"reported_entry_at":"2026-10-02T12:00:00-04:00"}]}'},text='Correction: the entry was actually noon.')
        self.assertTrue(changed['persisted']);self.assertFalse(changed['ready_to_finalize'])
        self.assertIn('actually noon',changed['raw_story_text'])
        self.assertFalse(self.saved(changed)['ok'])
        corrected=self.stage({'entries':[{'entry_index':1,'reported_exit_at':'2026-10-02T13:00:00-04:00'}]},draft_id=first['draft_id'])
        self.assertTrue(corrected['ready_to_finalize']);self.assertTrue(self.saved(corrected)['ok'])

    def test_audio_optout_and_hypothetical_never_persist_or_finalize(self):
        for narration in ('Do not save this. Preview only. I entered short on NAS.',
                          'Draft a hypothetical example: I entered short on NAS.'):
            fresh=self.fresh();fresh.begin_turn(None)
            runner=lambda n,a:coach.coach_tool(self.db,10,20,n,a)
            result=fresh.run('stage_journal_story',{'story_json':'{"asset":"NAS","context_notes":"Private preview"}',
                'raw_story':narration},runner)
            self.assertTrue(result['ok'],result);self.assertFalse(result['persisted'])
            self.assertFalse(fresh.run('save_journal_story',{'draft_id':result['draft_id']},runner)['ok'])
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0],0)

    def test_audio_narration_does_not_authorize_same_turn_finalization(self):
        fresh=self.fresh();fresh.begin_turn(None)
        runner=lambda n,a:coach.coach_tool(self.db,10,20,n,a)
        result=fresh.run('stage_journal_story',{'story_json':'{"asset":"NAS"}',
            'raw_story':'Journal my NAS trade. I am still describing it.'},runner)
        self.assertTrue(result['persisted'])
        final=fresh.run('save_journal_story',{'draft_id':result['draft_id']},runner)
        self.assertFalse(final['ok'],final)

    def test_approximate_times_are_preserved_without_timestamp_or_result_guess(self):
        first=self.call('stage_journal_story',{'story_json':'{}'},text='Journal my NAS trade. I entered short about 11:50 PM and exited around 12:20 AM the next day.')
        self.assertTrue(first['ok'],first)
        self.assertIn('reported_entry_time_text',first['story'])
        self.assertIn('next day',first['story']['reported_exit_time_text'])
        self.assertNotIn('reported_entry_at',first['story']);self.assertNotIn('reported_outcome',first['story'])
        self.assertNotIn('trade_date',first['story'])

    def test_member_and_linked_record_deletion_cascades(self):
        self.conn.execute('CREATE UNIQUE INDEX members_owner_unique ON members(guild_id,user_id)');self.conn.commit()
        self.conn.execute('PRAGMA foreign_keys=ON')
        first=self.stage();self.assertTrue(self.saved(first)['ok'])
        self.conn.execute('DELETE FROM journals');self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)
        self.context=self.fresh();second=self.stage()
        self.conn.execute('DELETE FROM members WHERE guild_id=10 AND user_id=20');self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0],0)

    def test_server_clock_does_not_guess_member_today(self):
        first=self.call('stage_journal_story',{'story_json':'{}'},text='Journal my NAS trade. I entered short today.')
        self.assertNotIn('trade_date',first['story'])


if __name__=='__main__':unittest.main()
