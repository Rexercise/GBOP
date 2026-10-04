"""Canonical identity, legacy safety and durable audit; synthetic SQLite only."""
import json
import unittest
from gbop_voice_web import journal_coach as coach, trade_photos as photos
from gbop_voice_web.journal_context import merge_metadata
from gbop_voice_web.journal_numbers import journal_display, resolve_journal_selector
from gbop_voice_web.unified_journal import (ensure_canonical_journal, canonical_journal_id,
    source_journal_id, CANONICAL_EVENT, AUDIT_EVENT, SOURCE_EVENT)
from tests.test_journal_coach import CoachTests


class UnifiedJournalTests(CoachTests):
    def thesis(self, ident=41, user=20, guild=10, status='OPEN'):
        self.conn.execute('''INSERT INTO theses(id,guild_id,user_id,asset,direction,play,objective,
            thesis_invalidation,status,created_at) VALUES(?,?,?,'XAUUSD','long','Custom',
            'Member objective','Member invalidation',?,'2026-10-03')''', (ident,guild,user,status))
        return ident

    def canonical(self, thesis, **kwargs):
        coach.init_coach(self.db)
        with self.db() as conn:
            return ensure_canonical_journal(conn,10,20,thesis,**kwargs)

    def events(self, event):
        return [json.loads(r[0]) for r in self.conn.execute('SELECT details FROM thesis_events WHERE event=? ORDER BY id',(event,))]

    def test_standalone_is_identity_without_fabricated_execution_or_budget(self):
        saved=self.save(description='I stopped out; risk and R unknown.',metadata_json='{"kind":"trade","reported_outcome":"stopped_out"}')
        self.assertTrue(saved['ok'],saved)
        self.assertEqual((saved['trade_number'],saved['journal_number']),(1,1))
        row=self.conn.execute('SELECT * FROM theses').fetchone()
        self.assertEqual((row['asset'],row['direction'],row['objective']),('Not specified',)*3)
        self.assertEqual((row['status'],row['max_r'],row['final_result_r']),('JOURNALED',0,None))
        self.assertEqual(saved['metadata']['recorded_risk'],None)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM theses WHERE status='OPEN'").fetchone()[0],0)
        self.assertEqual(len(self.events(CANONICAL_EVENT)),1)

    def test_study_and_unspecified_reflection_do_not_enter_performance(self):
        self.save(description='Example to study',result_r=100,metadata_json='{"kind":"study"}')
        self.save(description='Thoughts about discipline',result_r=100)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM theses WHERE status='IDEA'").fetchone()[0],2)
        self.assertEqual(coach.performance(self.db,10,20,{})['summary']['entries'],0)

    def test_repeat_close_updates_one_identity_and_full_audit(self):
        thesis=self.thesis()
        journal=self.canonical(thesis,fields={'description':'Original setup','study_note':'First reflection'})
        self.assertEqual(self.canonical(thesis,fields={'result_r':2,'description':'Exit reason'}),journal)
        self.assertEqual(self.canonical(thesis,fields={'result_r':3}),journal)
        count=len(self.events(AUDIT_EVENT))
        self.assertEqual(self.canonical(thesis,fields={'result_r':3}),journal)
        self.assertEqual(len(self.events(AUDIT_EVENT)),count)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT study_note FROM journals').fetchone()[0],'First reflection')
        audit=self.events(AUDIT_EVENT)
        self.assertEqual(audit[-1]['before']['journal']['result_r'],2)
        self.assertEqual(audit[-1]['after']['journal']['result_r'],3)
        self.assertEqual(audit[1]['before']['journal']['description'],'Original setup')

    def test_existing_trade_save_reuses_canonical_and_cannot_forge_identity(self):
        self.thesis()
        journal=self.canonical(41,fields={'description':'Setup'})
        response=self.save(trade_number=1,study_note='I respected the stop.',metadata_json='{"exit_reason":"Stopped"}')
        self.assertTrue(response['ok'],response)
        self.assertEqual(self.conn.execute('SELECT id FROM journals').fetchone()[0],journal)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],1)
        for field in ('journal_id','thesis_id','_thesis_id'):
            self.assertFalse(self.save(**{field:journal},description='Intrusion')['ok'])

    def test_two_sources_stay_attached_and_each_page_retry_is_idempotent(self):
        one=self.save(photo_id=self.photo,entry_index=1,description='First page',metadata_json='{"kind":"trade"}')
        second=photos.save_upload(self.db,10,20,'p2',b'\x89PNG\r\n\x1a\nother')['photo_id']
        two=self.save(trade_number=one['trade_number'],photo_id=second,entry_index=2,study_note='Second page')
        self.assertTrue(two['ok'],two)
        d=self.conn.execute('SELECT * FROM journal_details').fetchone()
        self.assertEqual(d['photo_id'],self.photo)
        self.assertEqual(len(two['metadata']['source_attachments']),2)
        for photo,index in ((self.photo,1),(second,2)):
            response=self.save(photo_id=photo,entry_index=index,study_note='Source retry')
            self.assertTrue(response['ok'],response)
            self.assertEqual(response['trade_number'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
        with self.db() as conn:
            self.assertEqual(source_journal_id(conn,10,20,second,2),d['journal_id'])
        self.assertEqual(len(self.events(SOURCE_EVENT)),2)

    def test_source_entry_cannot_be_reassigned_to_another_trade(self):
        self.save(photo_id=self.photo,description='A')
        self.save(description='B')
        bad=self.save(trade_number=2,photo_id=self.photo,description='Wrong trade')
        self.assertFalse(bad['ok'],bad)
        self.assertEqual(self.conn.execute('SELECT description FROM journals ORDER BY id').fetchone()[0],'A')

    def test_duplicate_linked_history_stays_exact_and_conflicts_remain_unknown(self):
        self.thesis()
        coach.init_coach(self.db)
        self.conn.executemany('INSERT INTO journals(id,guild_id,user_id,thesis_id,description,result_r,created_at) VALUES(?,10,20,41,?,?,?)',
                             [(7,'Old A',2,'original-A'),(9,'Old B',-1,'original-B')])
        self.conn.execute('INSERT INTO journal_details VALUES(7,10,20,?,1,?,?)',(self.photo,'{"transcription":"Original page"}','old timestamp'))
        before=[tuple(r) for r in self.conn.execute('SELECT * FROM journals ORDER BY id')]
        before_details=tuple(self.conn.execute('SELECT * FROM journal_details').fetchone())
        with self.db() as conn:
            self.assertIsNone(canonical_journal_id(conn,10,20,41))
        journal=self.canonical(41,fields={'study_note':'New reflection'})
        self.assertNotIn(journal,(7,9))
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM journals WHERE id IN (7,9) ORDER BY id')],before)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM journal_details WHERE journal_id=7').fetchone()),before_details)
        self.assertIsNone(self.conn.execute('SELECT result_r FROM journals WHERE id=?',(journal,)).fetchone()[0])
        metadata=json.loads(self.conn.execute('SELECT metadata FROM journal_details WHERE journal_id=?',(journal,)).fetchone()[0])
        self.assertTrue(metadata['legacy_history']['needs_clarification'])
        self.assertIn('result_r',metadata['legacy_history']['conflicting_fields'])
        self.assertEqual(metadata['source_attachments'][0]['photo_id'],self.photo)
        self.assertEqual(self.canonical(41,fields={'result_r':4}),journal)
        self.assertFalse(self.save(legacy_journal_number=1,description='Overwrite old row')['ok'])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM journals WHERE id IN (7,9) ORDER BY id')],before)

    def test_legacy_unlinked_is_never_guessed_or_reassigned_and_alias_collision_requires_clarification(self):
        self.thesis(41)
        self.conn.execute("INSERT INTO journals(id,guild_id,user_id,description,created_at) VALUES(3,10,20,'Old unlinked','old')")
        journal=self.canonical(41,fields={'description':'Canonical'})
        with self.db() as conn:
            ambiguous=resolve_journal_selector(conn,10,20,journal_number=1)
            exact=resolve_journal_selector(conn,10,20,trade_number=1)
            legacy=resolve_journal_selector(conn,10,20,legacy_journal_number=1)
        self.assertEqual(ambiguous['status'],'ambiguous_journal_number')
        self.assertEqual(exact['record_id'],journal)
        self.assertEqual(legacy['record_id'],3)
        self.assertIsNone(legacy['thesis_id'])
        changed=self.save(legacy_journal_number=1,study_note='Explicit historical correction')
        self.assertTrue(changed['ok'],changed)
        self.assertIsNone(changed['trade_number'])
        self.assertIsNone(self.conn.execute('SELECT thesis_id FROM journals WHERE id=3').fetchone()[0])
        self.assertEqual(changed['metadata']['legacy_audit'][0]['journal']['description'],'Old unlinked')

    def test_owner_scope_enforced_by_helper_and_numbers(self):
        self.thesis(user=30)
        coach.init_coach(self.db)
        with self.db() as conn:
            with self.assertRaisesRegex(ValueError,'not found'):
                ensure_canonical_journal(conn,10,20,41,fields={'description':'No'})
            self.assertFalse(resolve_journal_selector(conn,10,20,trade_number=1)['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_revocation_checked_again_after_helper_lock(self):
        self.thesis()
        coach.init_coach(self.db)
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        with self.db() as conn:
            with self.assertRaisesRegex(ValueError,'revoked'):
                ensure_canonical_journal(conn,10,20,41,fields={'description':'No'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_full_correction_history_survives_more_than_eight_updates(self):
        self.save(description='Base',metadata_json='{"kind":"trade"}')
        for n in range(20):
            result=self.save(trade_number=1,description=f'Correction {n}',result_r=n)
            self.assertTrue(result['ok'],result)
        self.assertEqual(len(result['metadata']['provenance']['corrections']),20)
        self.assertEqual(result['metadata']['provenance']['corrections'][0]['fields']['description']['before'],'Base')
        self.assertGreaterEqual(len(self.events(AUDIT_EVENT)),21)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)

    def test_explicit_identity_corrections_sync_trade_and_keep_original_audit(self):
        self.thesis(status='CLOSED')
        self.canonical(41,fields={'description':'Old summary'},metadata={'asset':'XAUUSD','play':'Custom'})
        response=self.save(trade_number=1,description='Corrected exit summary',metadata_json=json.dumps(
            {'asset':'NAS100','direction':'short','play':'Member custom model','session':'night'}))
        self.assertTrue(response['ok'],response)
        thesis=self.conn.execute('SELECT * FROM theses').fetchone()
        self.assertEqual((thesis['asset'],thesis['direction'],thesis['play'],thesis['session']),
                         ('NAS100','short','Member custom model','night'))
        self.assertEqual(thesis['close_note'],'Corrected exit summary')
        self.assertEqual(thesis['objective'],'Member objective')
        self.assertEqual(thesis['max_r'],1)
        correction=next(a for a in reversed(self.events(AUDIT_EVENT)) if 'member_corrected_thesis_fields' in a)
        self.assertEqual(correction['before']['thesis']['asset'],'XAUUSD')
        self.assertEqual(correction['after']['thesis']['asset'],'NAS100')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)

    def test_open_journal_reflection_does_not_overwrite_objective_or_close_note(self):
        self.thesis(status='OPEN')
        self.canonical(41,fields={'description':'Original setup'})
        response=self.save(trade_number=1,description='Reflection while open',metadata_json='{"asset":null,"play":"Custom corrected"}')
        self.assertTrue(response['ok'],response)
        thesis=self.conn.execute('SELECT * FROM theses').fetchone()
        self.assertEqual(thesis['asset'],'XAUUSD')
        self.assertEqual(thesis['play'],'Custom corrected')
        self.assertEqual(thesis['objective'],'Member objective')
        self.assertIsNone(thesis['close_note'])
        self.assertEqual(thesis['status'],'OPEN')

    def test_inconsistent_detail_ownership_fails_closed_and_preserves_exact_bytes(self):
        from gbop_voice_web.journal_context import save_closed_metadata
        self.thesis()
        journal=self.canonical(41,fields={'description':'Keep original'},metadata={'asset':'XAUUSD'})
        self.conn.execute("UPDATE journal_details SET user_id=30,metadata=' { \"private\" : \"exact bytes\" } ',updated_at='original stamp' WHERE journal_id=?",(journal,))
        self.conn.commit()
        before_journal=tuple(self.conn.execute('SELECT * FROM journals').fetchone())
        before_detail=tuple(self.conn.execute('SELECT * FROM journal_details').fetchone())
        before_events=[tuple(r) for r in self.conn.execute('SELECT * FROM thesis_events')]
        response=self.save(trade_number=1,description='Cannot overwrite',metadata_json='{"asset":"BTCUSD"}')
        self.assertFalse(response['ok'],response)
        self.assertIn('ownership',response['error'])
        with self.assertRaisesRegex(ValueError,'ownership'):
            self.canonical(41,fields={'description':'Cannot overwrite either'})
        with self.db() as conn:
            with self.assertRaisesRegex(ValueError,'ownership'):
                save_closed_metadata(conn,10,20,journal,{'asset':'BTCUSD'})
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM journals').fetchone()),before_journal)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM journal_details').fetchone()),before_detail)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM thesis_events')],before_events)

    def test_unlinked_legacy_edit_cannot_overwrite_foreign_details(self):
        coach.init_coach(self.db)
        self.conn.execute("INSERT INTO journals(id,guild_id,user_id,description) VALUES(3,10,20,'Keep')")
        self.conn.execute("INSERT INTO journal_details(journal_id,guild_id,user_id,metadata,updated_at) VALUES(3,11,20,' { \"secret\" : 1 } ','exact')")
        self.conn.commit()
        before=tuple(self.conn.execute('SELECT * FROM journal_details').fetchone())
        response=self.save(legacy_journal_number=1,description='No overwrite')
        self.assertFalse(response['ok'],response)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM journal_details').fetchone()),before)
        self.assertEqual(self.conn.execute('SELECT description FROM journals').fetchone()[0],'Keep')

    def test_deleted_canonical_never_promotes_remaining_legacy_history(self):
        self.thesis()
        coach.init_coach(self.db)
        self.conn.executemany("INSERT INTO journals(id,guild_id,user_id,thesis_id,description) VALUES(?,10,20,41,?)",[(7,'Old A'),(9,'Old B')])
        original=self.canonical(41)
        self.conn.execute('DELETE FROM journals WHERE id IN (?,9)',(original,))
        with self.db() as conn:
            self.assertIsNone(canonical_journal_id(conn,10,20,41))
        replacement=self.canonical(41,fields={'description':'New canonical'})
        self.assertNotEqual(replacement,7)
        self.assertEqual(self.conn.execute('SELECT description FROM journals WHERE id=7').fetchone()[0],'Old A')

    def test_audit_failure_rolls_back_identity_and_fields_atomically(self):
        self.thesis()
        coach.init_coach(self.db)
        with self.assertRaisesRegex(RuntimeError,'Synthetic audit failure'):
            with self.db() as conn:
                original=conn.execute
                def fail(sql,params=()):
                    if 'INSERT INTO thesis_events' in sql and AUDIT_EVENT in params:
                        raise RuntimeError('Synthetic audit failure')
                    return original(sql,params)
                conn.execute=fail
                ensure_canonical_journal(conn,10,20,41,fields={'description':'Must roll back'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_events').fetchone()[0],0)

    def test_competing_writers_use_same_transaction_locked_identity(self):
        import contextlib
        import sqlite3
        import tempfile
        import threading
        self.thesis()
        coach.init_coach(self.db)
        self.conn.commit()
        with tempfile.TemporaryDirectory() as directory:
            path=directory+'/journal.db'
            disk=sqlite3.connect(path)
            self.conn.backup(disk)
            disk.close()
            lock=threading.RLock()
            start=threading.Barrier(2)
            ids=[]; errors=[]
            @contextlib.contextmanager
            def db():
                conn=sqlite3.connect(path)
                conn.row_factory=sqlite3.Row
                acquired=[]
                class Adapter:
                    def execute(_,sql,params=()):
                        if 'pg_advisory_xact_lock' in sql:
                            lock.acquire(); acquired.append(True)
                            return conn.execute('SELECT 1')
                        return conn.execute(sql,params)
                try:
                    with conn:
                        yield Adapter()
                finally:
                    conn.close()
                    for _ in acquired: lock.release()
            def save(n):
                try:
                    start.wait(timeout=5)
                    with db() as conn:
                        ids.append(ensure_canonical_journal(conn,10,20,41,fields={'study_note':str(n)}))
                except BaseException as exc:
                    errors.append(exc)
            threads=[threading.Thread(target=save,args=(n,)) for n in (1,2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors,[])
            self.assertEqual(len(set(ids)),1)
            with db() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT count(*) FROM thesis_events WHERE event=?',(CANONICAL_EVENT,)).fetchone()[0],1)

    def test_close_uses_latest_member_corrections_not_immutable_opening_context(self):
        from gbop_voice_web.journal_context import close_metadata, save_trade_metadata
        self.thesis()
        opening=merge_metadata({}, {'reported_entry_at':'2026-10-03T10:00:00-04:00','asset':'XAUUSD'})
        self.canonical(41,fields={'description':'Opening'},metadata=opening)
        with self.db() as conn:
            save_trade_metadata(conn,10,20,41,opening)
        correction=self.save(trade_number=1,metadata_json='{"reported_entry_at":"2026-10-03T10:05:00-04:00"}')
        self.assertTrue(correction['ok'],correction)
        with self.db() as conn:
            row=conn.execute('SELECT * FROM theses WHERE id=41').fetchone()
            closing=close_metadata(conn,10,20,row,{'reported_exit_at':'2026-10-03T10:20:00-04:00'})
            ensure_canonical_journal(conn,10,20,41,fields={'result_r':2},metadata=closing)
        metadata=json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(metadata['reported_entry_at'],'2026-10-03T10:05:00-04:00')
        self.assertEqual(metadata['reported_exit_at'],'2026-10-03T10:20:00-04:00')
        self.assertEqual(len(metadata['provenance']['corrections']),1)
        event=json.loads(self.conn.execute("SELECT details FROM thesis_events WHERE event='journal_context_v1'").fetchone()[0])
        self.assertEqual(event['reported_entry_at'],'2026-10-03T10:00:00-04:00')

    def test_read_identity_without_optional_event_table_does_not_mutate(self):
        self.thesis()
        self.conn.execute("INSERT INTO journals(id,guild_id,user_id,thesis_id,description) VALUES(4,10,20,41,'Historical')")
        self.conn.execute('DROP TABLE thesis_events')
        with self.db() as conn:
            displays=journal_display(conn,10,20)
        self.assertEqual(displays[0]['journal_number'],1)
        self.assertTrue(displays[0]['canonical'])
        self.assertFalse(self.conn.execute('PRAGMA table_info(thesis_events)').fetchall())
