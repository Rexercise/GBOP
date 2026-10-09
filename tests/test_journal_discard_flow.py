"""Text/voice draft lifecycle regression coverage against synthetic SQLite only."""
from copy import deepcopy
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach, journal_drafts, journal_recall
from gbop_voice_web.journal_context import JournalBinding
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.journal_discard import DISCARD_NAMES
from gbop_voice_web.voice_runtime import _journal_write_outcome, JOURNAL_WRITE_NAMES
import test_progressive_journal as fixture


class JournalDiscardFlowTests(unittest.TestCase):
    setUp=fixture.ProgressiveJournalTests.setUp
    tearDown=fixture.ProgressiveJournalTests.tearDown
    review=fixture.ProgressiveJournalTests.review
    handlers=fixture.ProgressiveJournalTests.handlers
    call=fixture.ProgressiveJournalTests.call
    stage=fixture.ProgressiveJournalTests.stage
    saved=fixture.ProgressiveJournalTests.saved
    fresh=fixture.ProgressiveJournalTests.fresh
    stored=fixture.ProgressiveJournalTests.stored

    def preview(self,draft,context=None,deliver=True,text='Discard this unfinished draft.'):
        context=context or self.context
        result=self.call('prepare_journal_discard',{'draft_id':draft['draft_id']},text=text,context=context)
        self.assertTrue(result['ok'],result)
        if deliver:context.complete_response(result['confirmation_prompt'],response_id='discard-preview')
        return result

    def discard(self,draft,context=None,text='Yes, discard it.',**args):
        return self.call('discard_journal_story',{'draft_id':draft['draft_id'],**args},text=text,context=context)

    def test_delivered_preview_then_confirmation_archives_without_deleting(self):
        first=self.stage();original=self.stored(first['draft_id']);before=self.conn.execute('SELECT count(*) FROM journals').fetchone()[0]
        preview=self.preview(first)
        self.assertEqual(self.stored(first['draft_id'])['raw_story'],original['raw_story'])
        result=self.discard(first)
        self.assertTrue(result['discarded'],result);self.assertTrue(result['restorable'])
        with self.db() as conn:
            self.assertEqual(journal_drafts.read(conn,10,20,first['draft_id']),[])
            archive=journal_drafts.read(conn,10,20,first['draft_id'],include_discarded=True)[0]
        self.assertEqual(archive['raw_story'],original['raw_story']);self.assertEqual(archive['values'],original['values'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],before)
        self.assertIsNone(getattr(self.context,'_journal_story',None))
        self.assertNotIn('Blessed Thief',self.context.prompt())
        recalled=journal_recall.history(self.db,10,20,{'latest':'journal','date_basis':'saved','view':'detail'})
        self.assertNotIn(first['draft_id'],str(recalled))
        listed=self.call('get_journal_story',{'view':'discarded'},'Show discarded drafts.')
        self.assertEqual(listed['drafts'][0]['draft_id'],first['draft_id'])
        self.assertNotIn('raw_story',str(listed))
        restored=self.call('restore_journal_story',{'draft_id':first['draft_id'],'storage_revision':archive['storage_revision']},'Restore this draft.')
        self.assertTrue(restored['restored'],restored)
        self.assertEqual(self.stored(first['draft_id'])['raw_story'],original['raw_story'])
        self.assertFalse(self.stored(first['draft_id'])['save_authorized'])

    def test_same_turn_missing_delivery_partial_cancel_expired_and_false_confirmation_fail(self):
        for mode in ('same_turn','undelivered','partial','cancel','unrelated','expired','typed_override','reconnect'):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                try:
                    first=self.stage();preview=self.preview(first,deliver=mode not in ('undelivered','partial'))
                    if mode=='partial':self.context.complete_response(preview['confirmation_prompt'],completed=False)
                    if mode=='same_turn':
                        result=self.context.run('discard_journal_story',{'draft_id':first['draft_id'],'confirmation_text':'Yes'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                    elif mode=='reconnect':result=self.discard(first,context=self.fresh())
                    else:
                        if mode=='expired':self.context._journal_discard_preview['expires_at']=0
                        if mode=='unrelated':self.context.begin_turn('What is the weather?')
                        result=self.discard(first,text='No, keep it.' if mode in ('cancel','typed_override') else 'Yes',confirmation_text='Yes')
                    self.assertFalse(result['ok'],result)
                    self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')
                finally:pass

    def test_voice_exact_reply_and_receipt_metadata(self):
        first=self.stage();preview=self.preview(first,text=None)
        result=self.discard(first,text=None,confirmation_text='Yes, discard it.')
        self.assertTrue(result['discarded'],result)
        receipt=_journal_write_outcome('discard_journal_story','saved',result)
        self.assertTrue(receipt['discarded']);self.assertEqual(receipt['draft_status'],'discarded')
        self.assertNotIn('story',receipt)
        self.assertTrue({'discard_journal_story','restore_journal_story'}<=JOURNAL_WRITE_NAMES)

    def test_browser_next_turn_carries_only_matching_delivered_preview(self):
        for proof in ('discard-preview','wrong',None):
            with self.subTest(proof=proof):
                context=self.fresh();context.begin_turn('Read drafts',client_turn=1)
                first=self.stage()
                context.run('get_journal_story',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                preview=context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                context.complete_response(preview['confirmation_prompt'],response_id='discard-preview')
                context.advance_client_turn(2,continuation=bool(proof),response_id=proof)
                context.begin_turn('Yes',client_turn=2)
                result=context.run('discard_journal_story',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                self.assertEqual(result['ok'],proof=='discard-preview',result)
                # Discarded original cannot be silently recycled for the next case.
                self.context=self.fresh()

    def test_cross_session_stale_preview_and_paused_local_copy_cannot_revive(self):
        first=self.stage();other=self.fresh()
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read draft.',context=other)
        other._journal_story['persisted']=False
        other._journal_story['values']['context_notes']='session-only private continuation'
        self.preview(first);self.assertTrue(self.discard(first)['ok'])
        rejected=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{}'},'Continue the story.',context=other)
        self.assertFalse(rejected['ok'],rejected)
        self.assertNotIn('private continuation',other.prompt())
        resume=self.call('stage_journal_story',{'story_json':'{}'},'Continue my journal.',context=other)
        self.assertFalse(resume['ok'],resume)
        with self.db() as conn:self.assertEqual(journal_drafts.read(conn,10,20),[])

    def test_changed_and_finalized_drafts_fail_confirmation(self):
        first=self.stage();other=self.fresh();self.preview(first)
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read it.',context=other)
        self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"New fact"}'},'New fact.',context=other)
        self.assertFalse(self.discard(first)['ok'])
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read it.')
        self.preview(first)
        saved=self.call('save_journal_story',{'draft_id':first['draft_id']},'Save the journal narrative.',context=other)
        self.assertTrue(saved['ok'],saved)
        self.assertFalse(self.discard(first)['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)

    def test_restore_needs_explicit_source_and_no_inherited_finalize_permission(self):
        first=self.stage();self.preview(first);self.assertTrue(self.discard(first)['ok'])
        listed=self.call('get_journal_story',{'view':'discarded'},'Show archived drafts.')
        args={k:listed['drafts'][0][k] for k in ('draft_id','storage_revision')}
        denied=self.call('restore_journal_story',{**args,'confirmation_text':'Restore this draft.'},'Do not restore it.')
        self.assertFalse(denied['ok'])
        self.assertTrue(self.call('restore_journal_story',args,'Restore this draft.')['ok'])
        self.assertFalse(self.call('save_journal_story',{'draft_id':first['draft_id']},'Read this journal.')['ok'])

    def test_archived_index_is_bounded_and_named_restore_is_explicit(self):
        ids=[]
        for n in range(12):
            draft=self.call('stage_journal_story',{'new_draft':True,'story_json':json.dumps({'title':'A'*2400+str(n),'context_notes':'Synthetic story'})},'Start a new draft.')
            with self.db() as conn:
                row=journal_drafts.read(conn,10,20,draft['draft_id'])[0]
                journal_drafts.discard(conn,10,20,row['id'],expected_revision=row['storage_revision'])
            ids.append(draft['draft_id'])
            self.context=self.fresh()
        result=self.call('get_journal_story',{'view':'discarded'},'Show discarded drafts.')
        self.assertEqual(len(result['drafts']),10);self.assertTrue(result['has_more'])
        self.assertLess(len(json.dumps(result)),6000)
        page=self.call('get_journal_story',{'view':'discarded','offset':result['next_offset']},'Read next page.')
        self.assertEqual(len(page['drafts']),2);self.assertFalse(page['has_more'])
        selected=result['drafts'][0];args={k:selected[k] for k in ('draft_id','storage_revision')}
        for text in ('Restore my internet connection','Recover my account','Do not restore the draft','Should I restore this draft?'):
            self.assertFalse(self.call('restore_journal_story',args,text)['ok'])
        restored=self.call('restore_journal_story',args,'Restore draft '+selected['draft_id'])
        self.assertTrue(restored['restored'],restored)

    def test_discard_other_draft_does_not_block_active_draft(self):
        first=self.stage()
        other=self.call('stage_journal_story',{'new_draft':True,'story_json':'{"context_notes":"Second synthetic story"}'},'Start a new draft.')
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read first draft.')
        self.preview(other);self.assertTrue(self.discard(other,text='Yes, discard that draft.')['ok'])
        self.assertEqual(self.context._journal_story['id'],first['draft_id'])
        self.assertIn(first['draft_id'],self.context.prompt())
        result=self.call('stage_journal_story',{'story_json':'{}'},'Continue my journal.')
        self.assertTrue(result['ok'],result);self.assertEqual(result['draft_id'],first['draft_id'])

    def test_lifecycle_wording_cannot_bypass_member_recording_optout(self):
        first=self.stage()
        self.context.begin_turn('Do not record any more of this draft, and do not delete it.')
        self.assertTrue(self.context._journal_recording_paused)
        self.assertTrue(self.stored(first['draft_id'])['recording_paused'])
        result=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Private next passage"}'},'Private next passage.')
        self.assertFalse(result['persisted'])
        self.assertNotIn('Private next passage',str(self.stored(first['draft_id'])['values']))

    def test_transient_status_read_failure_retains_private_paused_continuation(self):
        first=self.stage()
        paused=self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"Private unsaved continuation"}'},'Do not save this. Preview only.')
        self.assertFalse(paused['persisted'])
        before=deepcopy(self.context._journal_story)
        with patch.object(journal_drafts,'read',side_effect=OSError('Synthetic read unavailable')):
            prompt=self.context.prompt()
        self.assertNotIn('Private unsaved continuation',prompt)
        self.assertEqual(self.context._journal_story,before)
        self.assertIn('Private unsaved continuation',self.context.prompt())
        self.assertEqual(self.context._journal_story,before)

    def test_repeat_same_confirmed_operation_is_read_only(self):
        first=self.stage();self.preview(first);self.assertTrue(self.discard(first)['ok'])
        before=list(self.conn.iterdump())
        again=self.context.run('discard_journal_story',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(again['ok'],again);self.assertEqual(list(self.conn.iterdump()),before)

    def test_archive_between_finalization_preflight_and_writer_blocks_canonical_save(self):
        first=self.stage();real_save=coach.save_entry
        def raced(db,guild,user,args):
            with self.db() as conn:
                active=journal_drafts.read(conn,10,20,first['draft_id'])[0]
                journal_drafts.discard(conn,10,20,first['draft_id'],expected_revision=active['storage_revision'])
            return real_save(db,guild,user,args)
        with patch.object(coach,'save_entry',side_effect=raced):
            result=self.saved(first)
        self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],0)

    def test_lost_commit_acknowledgement_recovers_exact_discard_and_restore(self):
        from gbop_voice_web.journal_discard import discard_story, restore_story
        first=self.stage();self.preview(first)
        self.context.begin_turn('Yes')
        readers=[];lose=[True]
        @contextmanager
        def lost_ack():
            with self.db() as conn:yield conn
            if lose[0]:
                lose[0]=False
                raise OSError('Synthetic lost commit acknowledgement')
        args={'draft_id':first['draft_id'],'_journal_binding':JournalBinding(self.context,self.context.generation)}
        with patch('gbop_voice_web.voice_runtime.journal_write_recovery_ready',side_effect=lambda g,u,r:readers.append(r)):
            result=discard_story(lost_ack,10,20,args)
        self.assertTrue(result['discarded'],result);self.assertTrue(result['reconciliation_verified'])
        before=list(self.conn.iterdump())
        self.assertTrue(readers[0]() ['discarded']);self.assertEqual(before,list(self.conn.iterdump()))
        with self.db() as conn:archived=journal_drafts.read(conn,10,20,first['draft_id'],include_discarded=True)[0]
        self.context.begin_turn('Restore this draft.');lose[0]=True
        restored=restore_story(lost_ack,10,20,{'draft_id':first['draft_id'],'storage_revision':archived['storage_revision'],
                        '_journal_binding':JournalBinding(self.context,self.context.generation)})
        self.assertTrue(restored['restored'],restored)
        self.assertIsNone(readers[0]())  # A later restore cannot be reported as the old discard.

    def test_failed_archive_recovery_does_not_retry_write(self):
        first=self.stage();self.preview(first)
        with patch.object(journal_drafts,'discard',side_effect=OSError('Synthetic failed update')) as writer:
            result=self.discard(first)
        self.assertFalse(result['ok']);self.assertFalse(result['saved'])
        self.assertTrue(result['reconciliation_verified']);self.assertEqual(writer.call_count,1)
        self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')

    def test_tools_have_no_market_binding_and_lifecycle_requests_never_capture(self):
        tools={t['name']:t for t in contextual_tools(coach.COACH_TOOLS)}
        for name in DISCARD_NAMES:
            self.assertNotIn('market_reference',tools[name]['parameters']['properties'])
        self.context.begin_turn('Journal this: discard the unfinished draft please.')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journal_story_drafts').fetchone()[0],0)
        rejected=self.call('stage_journal_story',{'story_json':'{}'},'Delete this draft.')
        self.assertFalse(rejected['ok'])

class DraftPromptVoiceLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_refresh_does_not_block_voice_and_old_generation_cannot_publish(self):
        import asyncio
        import threading
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from test_voice_latency import method
        started, release = threading.Event(), threading.Event()
        generation=[1];worker_threads=[]
        def prompt():
            worker_threads.append(threading.get_ident());started.set();release.wait(2)
            return 'Current draft state'
        context=SimpleNamespace(prompt=prompt,current=lambda g:g==generation[0])
        session=SimpleNamespace(closed=False,websocket=object(),market_context=context,
                                _market_base_instructions='base',send_event=AsyncMock())
        callback=method('_market_response_delivered',{'asyncio':asyncio})
        callback(session,1)
        for _ in range(100):
            if started.is_set():break
            await asyncio.sleep(.002)
        self.assertTrue(started.is_set());self.assertNotEqual(worker_threads[0],threading.get_ident())
        generation[0]=2;release.set()
        await asyncio.sleep(.02)
        session.send_event.assert_not_called()

    async def test_real_status_read_does_not_hold_conversation_lock_during_io(self):
        import asyncio
        import threading
        import time
        from types import SimpleNamespace
        started,release=threading.Event(),threading.Event()
        class Connection:
            def execute(self,*args):return self
            def fetchone(self):return {'activated':1,'revoked':0,'leadership_ack':1,'updated_at':'auth-v1'}
        @contextmanager
        def db():yield Connection()
        context=MarketConversation((10,20,'voice-lock'),auth_provider=(db,10,20))
        context.begin_turn(None)
        context._journal_story={'id':'a'*32,'storage_revision':1,'persisted':True,
            'owner':(10,20,context.session_id),'values':{},'raw_story':[],'draft_status':'unfinished'}
        def read(*args,**kwargs):
            started.set();release.wait(2);return [deepcopy(context._journal_story)]
        with patch.object(journal_drafts,'read',side_effect=read):
            task=asyncio.create_task(asyncio.to_thread(context.prompt))
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.002)
            self.assertTrue(started.is_set())
            timer=threading.Timer(.3,release.set);timer.start()
            begin=time.monotonic();context.begin_turn(None);elapsed=time.monotonic()-begin
            release.set();timer.cancel();await task
        self.assertLess(elapsed,.1,'A read-only draft status check blocked speech interruption.')


if __name__=='__main__':unittest.main()
