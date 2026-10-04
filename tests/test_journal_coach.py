import contextlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from gbop_voice_web import journal_coach as coach
from gbop_voice_web import trade_photos as photos


class CoachTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:')
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
        CREATE TABLE journals(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT,thesis_id INTEGER);
        CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,asset TEXT NOT NULL,direction TEXT NOT NULL,
            play TEXT NOT NULL,session TEXT,objective TEXT NOT NULL,thesis_invalidation TEXT NOT NULL,status TEXT NOT NULL,
            max_r REAL NOT NULL DEFAULT 1,created_at TEXT NOT NULL,final_result_r REAL,close_note TEXT,closed_at TEXT);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,event TEXT,details TEXT,result_r REAL,created_at TEXT);
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,entry_model TEXT,tier INTEGER);
        CREATE TABLE risk_flags(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,rule_code TEXT,message TEXT,created_at TEXT);
        CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,revoked INTEGER);
        INSERT INTO members VALUES(10,20,1,0),(10,30,1,0),(11,20,1,0),(10,40,1,1);
        ''')
        conn=self.conn
        class Adapter:
            def execute(_,sql,params=()):
                if any(x in sql for x in ('pg_advisory_xact_lock','ENABLE ROW LEVEL SECURITY','REVOKE ALL')):
                    return conn.execute('SELECT 1')
                return conn.execute(sql,params)
        @contextlib.contextmanager
        def db():
            with conn:
                yield Adapter()
        self.db=db
        self.photo=photos.save_upload(db,10,20,'p1',b'\x89PNG\r\n\x1a\nexample')['photo_id']

    def tearDown(self):
        self.conn.close()

    def save(self,**args):
        return coach.coach_tool(self.db,10,20,'save_journal_entry',args)

    def test_handwritten_import_preserves_unknowns_and_original(self):
        result=self.save(photo_id=self.photo,description='Gold long. Result [unclear]',metadata_json=json.dumps({'transcription':'Gold long. Result [unclear]','asset':'XAUUSD','kind':'trade','uncertainties':'Result illegible'}))
        self.assertTrue(result['saved']); self.assertIsNone(result['result_r'])
        found=coach.find_setups(self.db,10,20,{'query':'unclear'})['entries'][0]
        self.assertEqual(found['photo_id'],self.photo)
        self.assertEqual(found['metadata']['transcription'],'Gold long. Result [unclear]')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)
        thesis=self.conn.execute('SELECT * FROM theses').fetchone()
        self.assertEqual(thesis['status'],'JOURNALED')
        self.assertEqual(thesis['max_r'],0)
        retrieved=photos.search(self.db,10,20,{'journal_number':1})['photos'][0]
        self.assertEqual(retrieved['id'],self.photo)
        self.assertEqual(retrieved['handwritten_journals'][0]['description'],'Gold long. Result [unclear]')

    def test_retries_and_corrections_do_not_duplicate(self):
        args=dict(photo_id=self.photo,entry_index=1,description='Trade one',result_r=3,metadata_json='{"kind":"trade","asset":"XAUUSD"}')
        self.save(**args); self.save(**args)
        updated=self.save(journal_number=1,description='Corrected',metadata_json='{"tier":2}')
        self.assertEqual(updated['result_r'],3)
        self.assertEqual(updated['metadata']['asset'],'XAUUSD')
        self.assertTrue(updated['photo_attached'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
        self.assertIsNone(self.save(journal_number=1,clear_result=True)['result_r'])

    def test_multiple_entries_and_metadata_photo_filter(self):
        for index,asset in enumerate(('XAUUSD','NAS100'),1):
            self.save(photo_id=self.photo,entry_index=index,description=asset,metadata_json=json.dumps({'asset':asset,'tier':index,'entry_model':'Super Soup','labels':['textbook'],'kind':'trade'}))
        self.assertEqual(len(coach.find_setups(self.db,10,20,{'label':'textbook'})['entries']),2)
        self.assertEqual(len(photos.search(self.db,10,20,{'asset':'NAS100','tier':2})['photos']),1)
        self.assertEqual(photos.search(self.db,10,20,{'asset':'NAS100','tier':1})['photos'],[])

    def test_privacy_revocation_and_foreign_photo(self):
        self.save(photo_id=self.photo,description='Private')
        for guild,user in ((10,30),(11,20)):
            self.assertEqual(coach.find_setups(self.db,guild,user,{})['entries'],[])
            self.assertFalse(coach.coach_tool(self.db,guild,user,'save_journal_entry',{'photo_id':self.photo,'description':'Intrude'})['ok'])
        self.assertFalse(coach.coach_tool(self.db,10,40,'get_performance_review',{})['ok'])
        self.assertFalse(self.save(journal_number=99,description='No')['ok'])

    def test_statistics_unknowns_study_exclusion_and_grouping(self):
        for result,kind in ((3,'trade'),(-1,'trade'),(0,'trade'),(None,'trade'),(50,'study')):
            self.save(description='Example',result_r=result,metadata_json=json.dumps({'kind':kind,'entry_model':'Super Soup'}))
        summary=coach.performance(self.db,10,20,{})['summary']
        self.assertEqual(summary['entries'],4)
        self.assertEqual(summary['known_outcomes'],3)
        self.assertEqual(summary['missing_outcomes'],1)
        self.assertEqual(summary['total_r'],2)
        self.assertEqual(summary['win_rate_percent'],33.33)

    def test_one_outcome_per_thesis_mixed_entries(self):
        self.conn.execute("INSERT INTO theses(id,guild_id,user_id,asset,direction,play,session,objective,thesis_invalidation,status,created_at) VALUES(41,10,20,'XAUUSD','long','9ate8','day','Target','Invalidation','CLOSED','2026-10-03')")
        self.conn.execute("INSERT INTO thesis_executions VALUES(1,41,10,20,'Super Soup',1),(2,41,10,20,'Model 1',2)")
        for result in (2,3):
            self.conn.execute("INSERT INTO journals(guild_id,user_id,description,result_r,thesis_id) VALUES(10,20,'Trade',?,41)",(result,))
        report=coach.performance(self.db,10,20,{})
        self.assertEqual(report['summary']['entries'],1)
        self.assertIsNone(report['groups']['Mixed']['total_r'])
        self.assertEqual(report['summary']['missing_outcomes'],1)

    def test_weekly_undated_pages_excluded(self):
        self.save(photo_id=self.photo,description='Trade without date',result_r=3,metadata_json='{"kind":"trade"}')
        self.assertEqual(coach.performance(self.db,10,20,{'days':7})['undated_entries_excluded'],1)
        self.assertEqual(coach.performance(self.db,10,20,{})['summary']['total_r'],3)

    def test_plan_persistence_isolation_and_correction(self):
        args=dict(session_date='2026-09-30',shift='day',plan='8 AM range; stop after one thesis')
        self.assertTrue(coach.save_plan(self.db,10,20,args)['ok'])
        coach.save_plan(self.db,10,20,{**args,'plan':'Updated target'})
        self.assertEqual(coach.get_plans(self.db,10,20,{})['plans'][0]['plan'],'Updated target')
        self.assertEqual(coach.get_plans(self.db,10,30,{})['plans'],[])

    def test_activity_check_uses_measured_risk_and_keeps_neutral(self):
        for column in ('risk_r REAL','created_at TEXT'):
            self.conn.execute('ALTER TABLE thesis_executions ADD COLUMN '+column)
        today=coach.stamp()
        self.conn.execute("INSERT INTO theses(id,guild_id,user_id,asset,direction,play,objective,thesis_invalidation,status,final_result_r,created_at,closed_at) VALUES(41,10,20,'NAS100','long','Test','Target','Invalidation','CLOSED',-1,?,?)",(today,today))
        self.conn.execute("INSERT INTO thesis_executions(id,thesis_id,guild_id,user_id,risk_r,created_at) VALUES(1,41,10,20,0.25,?),(2,41,10,20,0.75,?)",(today,today))
        report=coach.coach_tool(self.db,10,20,'get_activity_check',{})
        self.assertTrue(report['ok'])
        self.assertEqual(report['executions'],2)
        self.assertEqual([s['type'] for s in report['signals']],['multiple_entries','larger_risk_after_loss'])
        self.assertEqual(coach.activity_check(self.db,10,30,{})['executions'],0)

    @patch.dict('os.environ',{'GTOP_OWNER_USER_ID':'20'})
    def test_community_review_is_owner_only(self):
        self.save(description='Trade',result_r=3,metadata_json='{"kind":"trade"}')
        self.assertTrue(coach.coach_tool(self.db,10,20,'get_community_review',{})['ok'])
        self.assertFalse(coach.coach_tool(self.db,10,30,'get_community_review',{})['ok'])
        self.assertEqual(coach.community_review(self.db,10,20,{})['known_outcomes'],1)

    def test_validation_and_delete_cascade(self):
        for args in ({'description':''},{'description':'x','result_r':float('nan')},{'description':'x','metadata_json':'{"tier":8}'},{'description':'x','metadata_json':'{"trade_date":"yesterday"}'}):
            self.assertFalse(self.save(**args)['ok'])
        self.save(photo_id=self.photo,description='Journal')
        self.conn.execute('DELETE FROM journals')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journal_details').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM trade_photos').fetchone()[0],1)
