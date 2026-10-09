"""Synthetic storage-only discard/recovery coverage. No live data or network."""
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_context import journal_transaction


class JournalDraftArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
            CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,revoked INTEGER,
                PRIMARY KEY(guild_id,user_id));
            INSERT INTO members VALUES(10,20,1,0),(10,30,1,0),(11,20,1,0);
            CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER);
            CREATE TABLE journals(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,thesis_id INTEGER);
            CREATE TABLE journal_details(journal_id INTEGER,guild_id INTEGER,user_id INTEGER,metadata TEXT);
            CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY,thesis_id INTEGER);
            CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER);
            CREATE TABLE risk_flags(id INTEGER PRIMARY KEY,thesis_id INTEGER);
            INSERT INTO theses VALUES(91,10,20);
            INSERT INTO journals VALUES(81,10,20,91);
            INSERT INTO journal_details VALUES(81,10,20,'{}');
            INSERT INTO thesis_executions VALUES(1,91);
            INSERT INTO thesis_events VALUES(1,91);
            INSERT INTO risk_flags VALUES(1,91);
        ''')
        for sql in journal_drafts.SCHEMA_SQL[:2]:self.conn.execute(sql)
        self.statements=[]
        parent=self
        class Adapter:
            def execute(self,sql,args=()):
                parent.statements.append(sql)
                if 'pg_advisory_xact_lock' in sql:return parent.conn.execute('SELECT 1')
                return parent.conn.execute(sql,args)
        @contextmanager
        def db():
            with self.conn:yield Adapter()
        self.db=db

    def tearDown(self):self.conn.close()

    def call(self,operation,*args,guild=10,user=20,**kwargs):
        with journal_transaction(self.db,{},guild,user,serialize=True) as conn:
            return operation(conn,guild,user,*args,**kwargs)

    def create(self,draft_id='draft-a',**fields):
        draft={'id':draft_id,'revision':7,'values':{'asset':'NAS100','result_r':None},
            'raw_story':[{'text':'  I entered short.\nNo exit yet. 😌  ','source':'member','at':'2040-02-01'}],
            'corrections':[{'revision':6,'field':'play','before':'old','after':'new'}],
            'provenance':{'asset':{'source':'member','revision':2}},'asked':['exit'],
            'audit':[{'revision':7,'note':'Keep this exact audit.'}]}
        draft.update(fields)
        return self.call(journal_drafts.write,draft)

    def row(self,draft_id='draft-a'):
        return dict(self.conn.execute('SELECT * FROM journal_story_drafts WHERE id=?',(draft_id,)).fetchone())

    def read(self,draft_id='draft-a',**kwargs):
        return self.call(journal_drafts.read,draft_id,**kwargs)

    def mutations(self):
        return [sql for sql in self.statements if sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE'))]

    def canonical(self):
        tables=('members','journals','journal_details','theses','thesis_executions','thesis_events','risk_flags')
        return {table:[tuple(row) for row in self.conn.execute('SELECT * FROM '+table)] for table in tables}

    def test_discard_hides_list_and_explicit_reads_without_destroying_content(self):
        draft=self.create();self.create('draft-b')
        before=self.row();payload=json.loads(before['payload']);canonical=self.canonical()
        self.statements=[]
        with patch.object(journal_drafts,'stamp',return_value='2099-01-01T00:00:00+00:00'):
            archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        after=self.row();stored=json.loads(after['payload'])
        self.assertEqual(archived['draft_status'],'discarded')
        self.assertEqual(after['status'],'unfinished')
        self.assertEqual(after['revision'],before['revision']+1)
        for key,value in payload.items():self.assertEqual(stored[key],value,key)
        self.assertEqual(after['created_at'],before['created_at'])
        self.assertEqual(after['updated_at'],before['updated_at'])
        self.assertEqual(archived['substantive_updated_at'],draft['substantive_updated_at'])
        self.assertEqual(self.read(),[])
        self.assertEqual([d['id'] for d in self.read(None)],['draft-b'])
        self.assertEqual(self.read(include_discarded=True),[archived])
        self.assertEqual({d['id'] for d in self.read(None,include_discarded=True)},{'draft-a','draft-b'})
        self.assertEqual(canonical,self.canonical())
        self.assertEqual(len(self.mutations()),1)
        self.assertTrue(self.mutations()[0].startswith('UPDATE journal_story_drafts SET revision='))

    def test_exact_discard_retry_is_read_only_and_other_revisions_fail(self):
        draft=self.create();revision=draft['storage_revision']
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=revision)
        before=self.row();self.statements=[]
        self.assertEqual(self.call(journal_drafts.discard,draft['id'],expected_revision=revision),archived)
        for expected in (None,False,True,0,str(revision),revision-1,archived['storage_revision'],999):
            with self.subTest(expected=expected),self.assertRaises(ValueError):
                self.call(journal_drafts.discard,draft['id'],expected_revision=expected)
        self.assertEqual(self.row(),before);self.assertEqual(self.mutations(),[])

    def test_default_reads_exclude_archived_payload_before_deserialization(self):
        draft=self.create();self.create('draft-b')
        self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        archived_payload=self.row()['payload']
        self.assertTrue(archived_payload.startswith(journal_drafts._DISCARD_PREFIX))
        original_loads=json.loads;decoded=[]
        def track_loads(value,*args,**kwargs):
            decoded.append(value)
            return original_loads(value,*args,**kwargs)
        with patch.object(journal_drafts.json,'loads',side_effect=track_loads):
            self.assertEqual(self.read(),[])
            self.assertEqual([d['id'] for d in self.read(None)],['draft-b'])
        self.assertNotIn(archived_payload,decoded)
        self.assertEqual(self.read(include_discarded=True)[0]['draft_status'],'discarded')

    def test_quoted_marker_narration_and_nested_audit_keys_stay_visible(self):
        passage='I said {"_discarded":null} and \\"_discarded\\":true; preserve those exact words.'
        draft=self.create(raw_story=[{'text':passage}],audit={'_discarded':{'reason':'Quoted historic term'}},
                          xdiscarded='This key must not match a SQL underscore wildcard.')
        self.assertEqual(self.read()[0]['raw_story'],draft['raw_story'])
        self.assertEqual(self.read(None)[0]['audit'],draft['audit'])

    def test_noncanonical_archive_marker_still_fails_closed_in_python(self):
        draft=self.create()
        self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        value=json.loads(self.row()['payload'])
        marker=value.pop('_discarded');value['_discarded']=marker
        noncanonical=json.dumps(value,ensure_ascii=False,indent=2)
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(noncanonical,));self.conn.commit()
        self.assertEqual(self.read(),[]);self.assertEqual(self.read(None),[])
        self.assertEqual(self.read(include_discarded=True)[0]['draft_status'],'discarded')

    def test_discarded_pages_are_bounded_owned_and_complete(self):
        ids=set()
        for index in range(15):
            draft=self.create('archive-'+str(index))
            self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
            ids.add(draft['id'])
        self.create('active')
        foreign=self.call(journal_drafts.write,{'id':'foreign','values':{}},user=30)
        self.call(journal_drafts.discard,foreign['id'],user=30,expected_revision=foreign['storage_revision'])
        self.statements=[]
        first=self.call(journal_drafts.read_discarded_page)
        second=self.call(journal_drafts.read_discarded_page,offset=first['next_offset'])
        self.assertEqual(len(first['drafts']),10);self.assertTrue(first['has_more'])
        self.assertEqual(first['next_offset'],10)
        self.assertEqual(len(second['drafts']),5);self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_offset'])
        self.assertEqual({d['id'] for page in (first,second) for d in page['drafts']},ids)
        self.assertEqual(set(d['id'] for d in first['drafts']) & set(d['id'] for d in second['drafts']),set())
        self.assertTrue(all(d['draft_status']=='discarded' for d in first['drafts']+second['drafts']))
        selects=[sql for sql in self.statements if 'SELECT * FROM journal_story_drafts' in sql]
        self.assertEqual(len(selects),2)
        self.assertTrue(all('LIMIT ? OFFSET ?' in sql for sql in selects))
        self.assertEqual(self.call(journal_drafts.read_discarded_page,offset=15),
            {'drafts':[],'next_offset':None,'has_more':False})
        self.assertEqual([d['id'] for d in self.call(journal_drafts.read_discarded_page,user=30)['drafts']],['foreign'])

    def test_discarded_page_fetches_no_more_than_limit_plus_one_rows(self):
        for index in range(13):
            draft=self.create('archive-'+str(index))
            self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        counts=[];parent=self
        class CountFetched:
            def execute(self,sql,args=()):
                cursor=parent.conn.execute(sql,args)
                if 'SELECT * FROM journal_story_drafts' not in sql:return cursor
                class CountCursor:
                    def fetchall(self):
                        rows=cursor.fetchall();counts.append(len(rows));return rows
                return CountCursor()
        result=journal_drafts.read_discarded_page(CountFetched(),10,20,limit=3)
        self.assertEqual(counts,[4]);self.assertEqual(len(result['drafts']),3)
        self.assertEqual(result['next_offset'],3)

    def test_discarded_page_rejects_invalid_and_unbounded_pagination(self):
        for offset in (None,False,True,-1,1.5,'0',10001):
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                self.call(journal_drafts.read_discarded_page,offset=offset)
        for limit in (None,False,True,0,-1,1.5,'10',21):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                self.call(journal_drafts.read_discarded_page,limit=limit)

    def test_discarded_page_does_not_mistake_quoted_or_nested_markers(self):
        self.create(raw_story=[{'text':'{"_discarded":null}'}],audit={'_discarded':None})
        self.assertEqual(self.call(journal_drafts.read_discarded_page),
            {'drafts':[],'next_offset':None,'has_more':False})

    def test_discarded_page_reports_safety_bound_without_invalid_next_cursor(self):
        for index in range(8):
            draft=self.create('archive-'+str(index))
            self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        with patch.object(journal_drafts,'DISCARDED_MAX_OFFSET',3):
            result=self.call(journal_drafts.read_discarded_page,offset=3,limit=3)
        self.assertTrue(result['has_more']);self.assertTrue(result['pagination_limit_reached'])
        self.assertIsNone(result['next_offset'])

    def test_restore_recovers_exact_narration_and_audit_without_performance_writes(self):
        draft=self.create();before=self.row();canonical=self.canonical()
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.statements=[]
        with patch.object(journal_drafts,'stamp',return_value='2099-01-02T00:00:00+00:00'):
            recovered=self.call(journal_drafts.restore,draft['id'],expected_revision=archived['storage_revision'])
        self.assertEqual(recovered['draft_status'],'unfinished')
        self.assertEqual(recovered['storage_revision'],draft['storage_revision']+2)
        self.assertEqual(self.read(),[recovered]);self.assertNotIn('_discarded',recovered)
        self.assertEqual(recovered['raw_story'],draft['raw_story'])
        self.assertEqual(recovered['corrections'],draft['corrections'])
        self.assertEqual(recovered['audit'],draft['audit'])
        self.assertEqual(recovered['substantive_updated_at'],draft['substantive_updated_at'])
        self.assertEqual(self.row()['updated_at'],before['updated_at'])
        self.assertEqual([event['action'] for event in recovered['_discard_history']],['discard','restore'])
        self.assertEqual(canonical,self.canonical());self.assertEqual(len(self.mutations()),1)
        self.statements=[];stored=self.row()
        self.assertEqual(self.call(journal_drafts.restore,draft['id'],expected_revision=archived['storage_revision']),recovered)
        with self.assertRaises(ValueError):
            self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(self.row(),stored);self.assertEqual(self.mutations(),[])

    def test_changed_revision_and_unarchived_restore_reject_without_mutating(self):
        draft=self.create();old=draft['storage_revision']
        draft['values']['context_notes']='A new fact.'
        changed=self.call(journal_drafts.write,draft,expected_revision=old);before=self.row()
        with self.assertRaises(ValueError):self.call(journal_drafts.discard,draft['id'],expected_revision=old)
        with self.assertRaises(ValueError):self.call(journal_drafts.restore,draft['id'],expected_revision=changed['storage_revision'])
        self.assertEqual(self.row(),before)

    def test_foreign_member_guild_and_missing_id_cannot_read_discard_or_restore(self):
        draft=self.create();before=self.row()
        for guild,user in ((10,30),(11,20)):
            with self.subTest(guild=guild,user=user):
                self.assertEqual(self.call(journal_drafts.read,draft['id'],guild=guild,user=user,include_discarded=True),[])
                for operation in (journal_drafts.discard,journal_drafts.restore):
                    with self.assertRaises(ValueError):
                        self.call(operation,draft['id'],guild=guild,user=user,expected_revision=draft['storage_revision'])
                with self.assertRaises(ValueError):self.call(journal_drafts.write,draft,guild=guild,user=user)
        for operation in (journal_drafts.discard,journal_drafts.restore):
            with self.assertRaises(ValueError):self.call(operation,'missing',expected_revision=1)
        self.assertEqual(self.row(),before)

    def test_revoked_member_cannot_discard_or_restore(self):
        draft=self.create();before=self.row()
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20');self.conn.commit()
        for operation in (journal_drafts.discard,journal_drafts.restore):
            with self.assertRaisesRegex(ValueError,'inactive or revoked'):
                self.call(operation,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(self.row(),before)

    def test_finalized_row_and_all_link_channels_are_protected(self):
        cases=[('finalized',{},dict(finalized=True)),('journal_id',{},dict(journal_id=81)),
            ('thesis_id',{},dict(thesis_id=91)),('saved_id',{'saved_journal_id':81},{}),
            ('selected_key',{'selected_key':[81,91]},{}),('selected_empty',{'selected_key':[]},{}),
            ('trade_number',{'trade_number':1},{}),('saved_revision',{'saved_revision':7},{})]
        for name,fields,options in cases:
            with self.subTest(name=name):
                draft=self.create(name,**fields)
                if options:draft=self.call(journal_drafts.write,draft,expected_revision=draft['storage_revision'],**options)
                before=self.row(name)
                for operation in (journal_drafts.discard,journal_drafts.restore):
                    with self.assertRaisesRegex(ValueError,'saved or linked'):
                        self.call(operation,name,expected_revision=draft['storage_revision'])
                self.assertEqual(self.row(name),before)

    def test_unfinished_correction_of_finalized_record_retains_link_protection(self):
        draft=self.create()
        saved=self.call(journal_drafts.write,draft,expected_revision=draft['storage_revision'],finalized=True,journal_id=81,thesis_id=91)
        saved['values']['context_notes']='Correction awaiting finalization.'
        correction=self.call(journal_drafts.write,saved,expected_revision=saved['storage_revision'])
        self.assertEqual(correction['draft_status'],'unfinished');before=self.row()
        with self.assertRaisesRegex(ValueError,'saved or linked'):
            self.call(journal_drafts.discard,draft['id'],expected_revision=correction['storage_revision'])
        self.assertEqual(self.row(),before)

    def test_saved_metadata_association_protects_draft_even_without_links(self):
        draft=self.create()
        for story in ({'draft_id':draft['id']},json.dumps({'draft_id':draft['id']})):
            with self.subTest(story=story):
                self.conn.execute('UPDATE journal_details SET metadata=?',(json.dumps({'journal_story':story}),));self.conn.commit()
                before=self.row()
                with self.assertRaisesRegex(ValueError,'saved or linked'):
                    self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
                self.assertEqual(self.row(),before)

    def test_other_members_metadata_does_not_link_this_members_draft(self):
        draft=self.create()
        self.conn.execute('INSERT INTO journals VALUES(82,10,30,NULL)')
        self.conn.execute('INSERT INTO journal_details VALUES(82,10,30,?)',
            (json.dumps({'journal_story':json.dumps({'draft_id':draft['id']})}),));self.conn.commit()
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(archived['draft_status'],'discarded')

    def test_archived_draft_rejects_stale_write_finalization_and_recreation(self):
        draft=self.create()
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        before=self.row();self.statements=[]
        for expected in (None,draft['storage_revision'],archived['storage_revision']):
            for finalized in (False,True):
                with self.subTest(expected=expected,finalized=finalized),self.assertRaisesRegex(ValueError,'discarded'):
                    self.call(journal_drafts.write,draft,expected_revision=expected,finalized=finalized)
        self.assertEqual(self.row(),before);self.assertEqual(self.mutations(),[])

    def test_forged_archive_state_or_history_cannot_be_written(self):
        draft=self.create();before=self.row()
        for key,value in (('_discarded',None),('_discarded',{'action':'discard'}),('_discard_history',[])):
            forged=deepcopy(draft);forged[key]=value
            with self.subTest(key=key,value=value),self.assertRaisesRegex(ValueError,'managed by'):
                self.call(journal_drafts.write,forged,expected_revision=draft['storage_revision'])
            forged['id']='forged-new'
            with self.assertRaisesRegex(ValueError,'managed by'):self.call(journal_drafts.write,forged)
        self.assertEqual(self.row(),before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journal_story_drafts').fetchone()[0],1)

    def test_restored_history_survives_normal_writes_and_cannot_be_replaced(self):
        draft=self.create()
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        restored=self.call(journal_drafts.restore,draft['id'],expected_revision=archived['storage_revision'])
        forged=deepcopy(restored);forged['_discard_history']=[]
        with self.assertRaisesRegex(ValueError,'managed by'):
            self.call(journal_drafts.write,forged,expected_revision=restored['storage_revision'])
        edit=deepcopy(restored);edit.pop('_discard_history');edit['values']['context_notes']='Added after recovery.'
        written=self.call(journal_drafts.write,edit,expected_revision=restored['storage_revision'])
        self.assertEqual(written['_discard_history'],restored['_discard_history'])
        for operation,revision in ((journal_drafts.discard,draft['storage_revision']),(journal_drafts.restore,archived['storage_revision'])):
            with self.assertRaises(ValueError):self.call(operation,draft['id'],expected_revision=revision)

    def test_restore_rechecks_new_canonical_association(self):
        draft=self.create()
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.conn.execute('UPDATE journal_details SET metadata=?',
            (json.dumps({'journal_story':json.dumps({'draft_id':draft['id']})}),));self.conn.commit()
        before=self.row()
        with self.assertRaisesRegex(ValueError,'saved or linked'):
            self.call(journal_drafts.restore,draft['id'],expected_revision=archived['storage_revision'])
        self.assertEqual(self.row(),before)

    def test_discard_restore_reset_save_permission_but_preserve_recording_optout(self):
        draft=self.create(save_authorized=True,recording_paused=True)
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.assertFalse(archived['save_authorized']);self.assertTrue(archived['recording_paused'])
        restored=self.call(journal_drafts.restore,draft['id'],expected_revision=archived['storage_revision'])
        self.assertFalse(restored['save_authorized']);self.assertTrue(restored['recording_paused'])

    def test_preview_eligibility_is_read_only_and_rejects_stale_or_archived_rows(self):
        draft=self.create();before=self.row();self.statements=[]
        self.call(journal_drafts.ensure_discardable,draft)
        self.assertEqual(self.row(),before);self.assertEqual(self.mutations(),[])
        stale=deepcopy(draft);stale['storage_revision']=0
        with self.assertRaises(ValueError):self.call(journal_drafts.ensure_discardable,stale)
        archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        for value in (draft,archived):
            with self.assertRaisesRegex(ValueError,'already discarded'):
                self.call(journal_drafts.ensure_discardable,value)

    def test_transition_history_is_bounded_without_truncating_narration_audit(self):
        draft=self.create();original=deepcopy(draft)
        for _ in range(journal_drafts.DISCARD_HISTORY_LIMIT):
            draft=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
            draft=self.call(journal_drafts.restore,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(len(draft['_discard_history']),journal_drafts.DISCARD_HISTORY_LIMIT)
        for key in ('raw_story','corrections','audit','revision','substantive_updated_at'):
            self.assertEqual(draft[key],original[key])

    def test_budget_pressure_only_compacts_archive_transition_history(self):
        draft=self.create();original=deepcopy(draft)
        for _ in range(3):
            draft=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
            draft=self.call(journal_drafts.restore,draft['id'],expected_revision=draft['storage_revision'])
        budget=len(self.row()['payload'].encode('utf-8'))
        with patch.object(journal_drafts,'PAYLOAD_LIMIT',budget):
            archived=self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.assertLess(len(archived['_discard_history']),len(draft['_discard_history'])+1)
        self.assertLessEqual(len(self.row()['payload'].encode('utf-8')),budget)
        self.assertEqual(archived['raw_story'],original['raw_story'])
        self.assertEqual(archived['audit'],original['audit'])
        self.assertEqual(archived['_discard_history'][-1],archived['_discarded'])

    def test_lost_compare_and_swap_never_reports_success(self):
        draft=self.create();before=self.row();parent=self
        class LostUpdate:
            def execute(self,sql,args=()):
                if sql.startswith('UPDATE journal_story_drafts'):
                    return parent.conn.execute('SELECT 1 WHERE 0')
                return parent.conn.execute(sql,args)
        with journal_transaction(self.db,{},10,20,serialize=True):
            with self.assertRaisesRegex(ValueError,'changed'):
                journal_drafts.discard(LostUpdate(),10,20,draft['id'],expected_revision=draft['storage_revision'])
        changed=deepcopy(draft);changed['values']['context_notes']='New fact.'
        with journal_transaction(self.db,{},10,20,serialize=True):
            with self.assertRaisesRegex(ValueError,'changed'):
                journal_drafts.write(LostUpdate(),10,20,changed,expected_revision=draft['storage_revision'])
        self.assertEqual(self.row(),before)

    def test_payload_limit_fails_atomically_without_truncating_narration(self):
        draft=self.create();before=self.row();self.statements=[]
        with patch.object(journal_drafts,'PAYLOAD_LIMIT',len(before['payload'].encode('utf-8'))):
            with self.assertRaisesRegex(ValueError,'too large'):
                self.call(journal_drafts.discard,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(self.row(),before);self.assertEqual(self.mutations(),[])

    def test_payload_ownership_inconsistency_fails_closed(self):
        draft=self.create();payload=json.loads(self.row()['payload']);payload['member']=[10,30]
        self.conn.execute('UPDATE journal_story_drafts SET payload=?',(json.dumps(payload),));self.conn.commit()
        before=self.row()
        for operation in (journal_drafts.discard,journal_drafts.restore):
            with self.assertRaisesRegex(ValueError,'ownership'):
                self.call(operation,draft['id'],expected_revision=draft['storage_revision'])
        self.assertEqual(self.row(),before)


if __name__=='__main__':unittest.main()
