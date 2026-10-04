"""Exact production handlers exercised with invented members and in-memory data."""
import json
import unittest
from gbop_voice_web import journal_coach as coach, journal_recall as recall, trade_photos as photos
from gbop_voice_web.trade_numbers import trade_record_id, recorded_trade_risk
import test_contextual_journals as contextual


class UnifiedTradeWorkflowTests(unittest.TestCase):
    setUp = contextual.ContextualJournalTests.setUp
    tearDown = contextual.ContextualJournalTests.tearDown
    review = contextual.ContextualJournalTests.review
    handlers = contextual.ContextualJournalTests.handlers

    def test_open_append_two_photos_close_and_recall_share_one_identity_both_backends(self):
        for path, opening, closing in [('bot.py','ai_open_trade','ai_close_trade'),
                ('gbop_voice_web/server.py','tool_open_trade','tool_close_trade')]:
            with self.subTest(path=path):
                env=self.handlers(path)
                opened=env[opening](20,dict(asset='TEST',direction='Bearish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
                number=opened['trade_id'];tid=trade_record_id(self.db,10,20,number)
                initial=self.conn.execute('SELECT id FROM journals WHERE thesis_id=?',(tid,)).fetchone()[0]
                selected=[]
                for i in range(2):
                    pid=photos.save_upload(self.db,10,20,f'{path}-photo-{i}',b'\x89PNG\r\n\x1a\nfixture'+bytes([i]))['photo_id']
                    photos.annotate(self.db,10,20,dict(photo_id=pid,trade_number=number,analysis='Synthetic chart'))
                    saved=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(trade_number=number,
                        photo_id=pid,description=f'Observation {i}',metadata_json='{"kind":"trade"}'))
                    self.assertTrue(saved['ok'],saved);selected.append(pid)
                self.assertEqual(self.conn.execute('SELECT count(*) FROM journals WHERE thesis_id=?',(tid,)).fetchone()[0],1)
                self.assertEqual({p['id'] for p in photos.search(self.db,10,20,{'trade_number':number})['photos']},set(selected))
                self.assertEqual({p['id'] for p in photos.search(self.db,10,20,{'journal_number':number})['photos']},set(selected))
                closed=env[closing](20,dict(trade_id=number,summary='Real reported stopout; R unknown',rule_adherence='',study_note='Review timing',final_result_r=None))
                self.assertTrue(closed['ok'],closed);self.assertEqual(closed['journal_id'],initial)
                self.assertEqual(closed['journal_number'],number)
                rows=[r for r in recall.history(self.db,10,20,{})['journals'] if r.get('trade_id')==number]
                self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['result_r'])
                before=self.conn.execute('SELECT count(*) FROM journals').fetchone()[0]
                again=env[closing](20,dict(trade_id=number,summary='Retry',rule_adherence='',study_note=''))
                self.assertFalse(again['ok']);self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],before)
                self.assertEqual(recall.history(self.db,10,30,{})['journals'],[])
                self.assertTrue(any(f'Trade #{number}' in line for line in recall.member_context_lines(self.db,10,20)))

    def test_journal_only_has_unknown_risk_and_can_explicitly_gain_first_execution(self):
        for path, opening in [('bot.py','ai_open_trade'),('gbop_voice_web/server.py','tool_open_trade')]:
            env=self.handlers(path)
            saved=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(description='A reported idea with missing fill details',metadata_json='{"kind":"reflection","asset":"TEST"}'))
            self.assertTrue(saved['ok'],saved);number=saved['trade_number'];tid=trade_record_id(self.db,10,20,number)
            self.assertIsNone(recorded_trade_risk(self.db,10,20,tid))
            self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions WHERE thesis_id=?',(tid,)).fetchone()[0],0)
            count=self.conn.execute('SELECT count(*) FROM theses').fetchone()[0]
            opened=env[opening](20,dict(trade_id=number,asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
            self.assertTrue(opened['ok'],opened);self.assertEqual(opened['trade_id'],number)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],count)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM journals WHERE thesis_id=?',(tid,)).fetchone()[0],1)
            self.assertEqual(recorded_trade_risk(self.db,10,20,tid),.25)
            self.assertEqual(self.conn.execute('SELECT description FROM journals WHERE thesis_id=?',(tid,)).fetchone()[0],'A reported idea with missing fill details')
            duplicate=env[opening](20,dict(trade_id=number,asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
            self.assertFalse(duplicate['ok']);self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions WHERE thesis_id=?',(tid,)).fetchone()[0],1)

    def test_context_reuse_does_not_attach_current_unrelated_market_to_existing_trade(self):
        saved=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(description='Earlier unrelated idea',metadata_json='{"kind":"reflection","asset":"TEST"}'))
        env=self.handlers('gbop_voice_web/server.py')
        self.context.begin_turn('Record the execution on my existing idea.')
        opened=self.context.run('open_trade',dict(trade_id=saved['trade_number'],asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25),lambda n,a:env['tool_open_trade'](20,a))
        self.assertTrue(opened['ok'],opened)
        meta=json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertNotIn('market_review',meta)

    def test_race_revocation_on_new_trade_rolls_back_all_records(self):
        env=self.handlers('gbop_voice_web/server.py');fired=[]
        def hook(sql,params):
            if 'pg_advisory_xact_lock(?)' in sql and not fired:
                fired.append(True);self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        self.before_execute=hook
        with self.assertRaisesRegex(ValueError,'revoked'):
            env['tool_open_trade'](20,dict(asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
        self.before_execute=None
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_reserved_events_cannot_forge_canonical_markers(self):
        for path,opening,event in [('bot.py','ai_open_trade','ai_record_trade_event'),('gbop_voice_web/server.py','tool_open_trade','tool_record_trade_event')]:
            env=self.handlers(path)
            opened=env[opening](20,dict(asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
            count=self.conn.execute('SELECT count(*) FROM thesis_events').fetchone()[0]
            for reserved in ('journal_canonical_v1',' journal_context_v1 ','JOURNAL_SOURCE_V1','journal_audit_v1'):
                outcome=env[event](20,dict(trade_id=opened['trade_id'],event=reserved,details='{"journal_id":999}',result_r=None))
                self.assertFalse(outcome['ok'],outcome)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_events').fetchone()[0],count)

    def test_add_entry_and_management_event_recheck_open_status_after_lock(self):
        for path,opening,adding,event in [('bot.py','ai_open_trade','ai_add_entry','ai_record_trade_event'),('gbop_voice_web/server.py','tool_open_trade','tool_add_entry','tool_record_trade_event')]:
            env=self.handlers(path)
            for operation,args in ((adding,dict(entry_model='Super Soup',risk_r=.25)),(event,dict(event='Reflection',details='A management note',result_r=None))):
                opened=env[opening](20,dict(asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25))
                tid=trade_record_id(self.db,10,20,opened['trade_id']);fired=[]
                before=self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0]
                def raced(sql,params):
                    if 'pg_advisory_xact_lock(?)' in sql and not fired:
                        fired.append(True);self.conn.execute("UPDATE theses SET status='CLOSED' WHERE id=?",(tid,))
                self.before_execute=raced
                response=env[operation](20,dict(args,trade_id=opened['trade_id']))
                self.before_execute=None
                self.assertFalse(response['ok'],response)
                self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],before)
                self.assertEqual(self.conn.execute("SELECT count(*) FROM thesis_events WHERE event='Reflection'").fetchone()[0],0)

    def test_close_preserves_canonical_correction_to_opening_context(self):
        for path,opening,closing in [('bot.py','ai_open_trade','ai_close_trade'),('gbop_voice_web/server.py','tool_open_trade','tool_close_trade')]:
            env=self.handlers(path)
            opened=env[opening](20,dict(asset='TEST',direction='Bullish',play='Synthetic',entry_model='Super Soup',risk_r=.25,reported_entry_at=contextual.NY('10:00')))
            corrected=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(trade_number=opened['trade_id'],metadata_json=json.dumps({'reported_entry_at':contextual.NY('10:05')})))
            self.assertTrue(corrected['ok'],corrected)
            closed=env[closing](20,dict(trade_id=opened['trade_id'],summary='Finished',rule_adherence='',study_note='',reported_exit_at=contextual.NY('10:20')))
            self.assertTrue(closed['ok'],closed)
            detail=self.conn.execute('SELECT metadata FROM journal_details WHERE journal_id=?',(closed['journal_id'],)).fetchone()[0]
            self.assertEqual(json.loads(detail)['reported_entry_at'],contextual.NY('10:05'))
