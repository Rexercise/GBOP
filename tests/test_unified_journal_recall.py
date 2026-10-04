"""Synthetic owner-scoped regression fixtures; no member data or live transport."""
import contextlib
from copy import deepcopy
import json
import sqlite3
import unittest

from gbop_voice_web import journal_recall as recall, trade_photos as photos
from gbop_voice_web.delivery_receipts import canonical_arguments
from gbop_voice_web.journal_coach import init_coach
from gbop_voice_web.journal_presentation import journal_tool_payload, JOURNAL_PRESENTATION_MAX_CHARS


class UnifiedRecallTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
        CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INT,user_id INT,asset TEXT,play TEXT,status TEXT);
        INSERT INTO theses VALUES(25,10,20,'TEST-A','Synthetic','CLOSED'),
            (40,10,20,'TEST-B','Other','OPEN'),(50,10,30,'FOREIGN','Private','CLOSED'),
            (60,11,20,'OTHER-GUILD','Private','CLOSED');
        CREATE TABLE journals(id INTEGER PRIMARY KEY,thesis_id INT,guild_id INT,user_id INT,
            description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT);
        INSERT INTO journals VALUES(100,25,10,20,'Synthetic journal','yes',NULL,'Original note','2026-10-01');
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INT,guild_id INT,user_id INT,
            event TEXT,details TEXT,result_r REAL,created_at TEXT);
        ''')
        conn = self.conn
        class Adapter:
            def execute(_, sql, params=()):
                if any(x in sql for x in ('pg_advisory_xact_lock','ENABLE ROW LEVEL SECURITY','REVOKE ALL')):
                    return conn.execute('SELECT 1')
                return conn.execute(sql, params)
        @contextlib.contextmanager
        def db():
            with conn:
                yield Adapter()
        self.db = db
        init_coach(db)
        self.photo('shared', thesis=25)

    def photo(self, pid, *, thesis=None, guild=10, user=20, asset='TEST-A', tier=1):
        self.conn.execute('''INSERT INTO trade_photos
            (id,guild_id,user_id,thesis_id,source_id,mime,image_base64,asset,tier,created_at)
            VALUES(?,?,?,?,?,'image/png','c3ludGhldGlj',?,?,'2026-10-01')''',
            (pid,guild,user,thesis,pid,asset,tier))
        return pid

    def detail(self, jid=100, pid=None, metadata=None, guild=10, user=20):
        self.conn.execute('''INSERT INTO journal_details
            (journal_id,guild_id,user_id,photo_id,entry_index,metadata,updated_at)
            VALUES(?,?,?,?,1,?,'2026-10-01')''',
            (jid,guild,user,pid,json.dumps(metadata or {})))

    def event(self, kind, details, *, thesis=25, guild=10, user=20):
        self.conn.execute('''INSERT INTO thesis_events
            (thesis_id,guild_id,user_id,event,details,created_at)
            VALUES(?,?,?,?,?,'2026-10-01')''', (thesis,guild,user,kind,json.dumps(details)))

    def ids(self, **args):
        result = photos.search(self.db,10,20,args)
        self.assertTrue(result['ok'], result)
        return [p['id'] for p in result['photos']]

    def test_trade_one_thesis_25_journal_one_same_shared_photo(self):
        self.assertEqual(self.ids(trade_number=1), ['shared'])
        self.assertEqual(self.ids(journal_number=1), ['shared'])
        self.assertEqual(self.ids(journal_number=1, trade_number=1), ['shared'])
        self.assertEqual(self.ids(journal_number=1, trade_number=2), [])
        self.assertEqual(self.ids(journal_number=1, tier=2), [])
        self.assertEqual(self.ids(journal_number=1, asset='TEST-B'), [])

    def test_direct_plus_shared_union_deduplicates_and_preserves_owner(self):
        self.detail(pid='shared')
        self.photo('foreign-user', thesis=25, user=30)
        self.photo('foreign-guild', thesis=25, guild=11)
        self.assertEqual(self.ids(journal_number=1), ['shared'])
        self.assertEqual(photos.search(self.db,10,30,{'journal_number':1})['photos'], [])

    def test_all_primary_metadata_and_event_photos_remain_accessible(self):
        self.photo('primary')
        self.photo('second')
        self.photo('third')
        self.photo('unrelated')
        self.detail(pid='primary', metadata={'source_attachments':[{'photo_id':'second','entry_index':1}]})
        self.event('journal_source_v1', {'journal_id':100,'photo_id':'third','entry_index':1})
        self.event('journal_source_v1', {'journal_id':100,'photo_id':'unrelated','entry_index':1}, user=30)
        expected = ['primary','second','shared','third']
        self.assertEqual(self.ids(journal_number=1), expected)
        self.assertEqual(self.ids(trade_number=1), expected)
        self.assertEqual(self.ids(unlinked_only=True), ['unrelated'])
        self.assertEqual(self.ids(journal_number=1,unlinked_only=True), [])
        rows = photos.search(self.db,10,20,{'journal_number':1})['photos']
        self.assertTrue(all(p['trade_number'] == 1 for p in rows))
        self.assertTrue(all(p['journal']['description'] == 'Synthetic journal' for p in rows))
        self.assertEqual(self.conn.execute('SELECT photo_id FROM journal_details').fetchone()[0], 'primary')

    def test_event_with_foreign_thesis_or_foreign_journal_is_ignored(self):
        self.photo('unrelated')
        self.event('journal_source_v1', {'journal_id':100,'photo_id':'unrelated'}, thesis=50)
        self.event('journal_source_v1', {'journal_id':999,'photo_id':'unrelated'})
        self.assertEqual(self.ids(journal_number=1), ['shared'])

    def test_foreign_thesis_pointer_does_not_crash_or_leak(self):
        self.photo('corrupt-owner-photo', thesis=50)
        result = photos.search(self.db,10,20,{})
        corrupt = next(p for p in result['photos'] if p['id'] == 'corrupt-owner-photo')
        self.assertIsNone(corrupt['trade_number'])
        self.assertEqual(corrupt['trade_details'], {})
        self.assertIsNone(corrupt['journal'])
        self.assertNotIn('FOREIGN', str(result))
        self.assertEqual(self.ids(journal_number=1), ['shared'])

    def test_invalid_metadata_does_not_crash_photo_recall(self):
        self.detail(pid='shared')
        self.conn.execute("UPDATE journal_details SET metadata='[invalid'")
        self.assertEqual(self.ids(journal_number=1), ['shared'])
        self.assertEqual(self.ids(tier=2), [])

    def test_number_collision_clarifies_and_explicit_legacy_is_separate(self):
        self.conn.execute("INSERT INTO journals VALUES(1,NULL,10,20,'Old unlinked journal','',NULL,'','2025-01-01')")
        self.photo('legacy-photo')
        self.detail(1,'legacy-photo')
        ambiguous = photos.search(self.db,10,20,{'journal_number':1})
        self.assertFalse(ambiguous['ok'])
        self.assertEqual(ambiguous['status'], 'ambiguous_journal_number')
        self.assertIn('legacy', ambiguous['error'])
        self.assertEqual(self.ids(trade_number=1), ['shared'])
        self.assertEqual(self.ids(trade_number=1,journal_number=1), ['shared'])
        self.assertEqual(self.ids(legacy_journal_number=1), ['legacy-photo'])
        self.assertEqual(self.ids(legacy_journal_number=1,unlinked_only=True), ['legacy-photo'])
        self.assertEqual(self.ids(legacy_journal_number=1,trade_number=1), [])
        original = canonical_arguments('send_trade_photos', {'journal_number':1})
        legacy = canonical_arguments('send_trade_photos', {'legacy_journal_number':1})
        self.assertNotEqual(original, legacy)
        self.assertEqual(legacy['legacy_journal_number'], 1)

    def test_duplicate_legacy_rows_group_read_only_without_choosing_result(self):
        self.conn.execute("INSERT INTO journals VALUES(110,25,10,20,'Another historic report','partial',2,'Second note','2026-10-02')")
        self.detail(100, metadata={'transcription':'Source manuscript one'})
        self.detail(110, metadata={'transcription':'Source manuscript two'})
        before = self.conn.total_changes
        result = recall.history(self.db,10,20,{})
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual((result['journal_count'],result['stored_journal_entry_count']), (2,2))
        row = result['journals'][0]
        self.assertEqual((row['trade_number'],row['journal_number']), (1,1))
        self.assertIsNone(row['journal_id'])
        self.assertFalse(row['canonical_record_exists'])
        self.assertTrue(row['needs_clarification'])
        self.assertIsNone(row['result_r'])
        self.assertEqual(len(row['legacy_history']), 2)
        delivered = '\n'.join(recall.messages(result))
        for value in ('Synthetic journal','Another historic report','Source manuscript one','Source manuscript two'):
            self.assertIn(value, delivered)
        photo = photos.search(self.db,10,20,{'journal_number':1})['photos'][0]
        self.assertIsNone(photo['journal']['result_r'])
        self.assertTrue(photo['journal']['historical_projection'])

    def test_explicit_canonical_marker_keeps_history_and_prevents_latest_guess(self):
        self.conn.execute("INSERT INTO journals VALUES(110,25,10,20,'Later preserved entry','partial',2,'Second note','2026-10-02')")
        self.event('journal_canonical_v1', {'journal_id':100})
        row = recall.history(self.db,10,20,{})['journals'][0]
        self.assertEqual(row['journal_id'], 100)
        self.assertEqual(row['summary'], 'Synthetic journal')
        self.assertIsNone(row['result_r'])
        self.assertEqual(row['legacy_history'][0]['summary'], 'Later preserved entry')
        self.assertEqual(row['legacy_history'][0]['legacy_journal_number'], 2)
        self.assertIsNone(photos.search(self.db,10,20,{'trade_number':1})['photos'][0]['journal']['result_r'])

    def test_legacy_unlinked_foreign_pointer_and_grouped_pagination(self):
        self.conn.execute("INSERT INTO journals VALUES(110,25,10,20,'Same trade update','',NULL,'','2026-10-02')")
        self.conn.execute("INSERT INTO journals VALUES(120,50,10,20,'Owned journal bad old link','',NULL,'','2026-10-03')")
        self.conn.execute("INSERT INTO journals VALUES(121,50,10,30,'FOREIGN PRIVATE JOURNAL','',NULL,'','2026-10-03')")
        first = recall.history(self.db,10,20,{'limit':1})
        self.assertEqual(first['journal_count'],3)
        self.assertTrue(first['has_more'])
        old = first['journals'][0]
        self.assertTrue(old['is_legacy'])
        self.assertIsNone(old['trade_number'])
        self.assertIsNone(old['journal_number'])
        self.assertEqual(old['legacy_journal_number'],3)
        self.assertIn('Legacy journal #3', '\n'.join(recall.messages(first)))
        second = recall.history(self.db,10,20,{'limit':1,'offset':first['next_offset']})
        self.assertEqual(second['journals'][0]['trade_number'],1)
        self.assertEqual(second['journals'][0]['legacy_history_count'],2)
        self.assertTrue(second['has_more'])
        third = recall.history(self.db,10,20,{'limit':1,'offset':second['next_offset']})
        self.assertEqual(third['journals'][0]['trade_number'],2)
        self.assertTrue(third['journals'][0]['virtual_trade_record'])
        self.assertFalse(third['has_more'])
        self.assertNotIn('FOREIGN PRIVATE',str(first)+str(second))
        lines = recall.member_context_lines(self.db,10,20)
        self.assertEqual(len(lines),3)
        self.assertIn('Legacy journal #3',lines[0])
        self.assertIn('Trade #1 journal',lines[1])

    def test_long_legacy_history_bounded_for_model_but_full_text_delivery(self):
        for n in range(10):
            self.conn.execute('INSERT INTO journals VALUES(?,25,10,20,?,\'\',NULL,\'\',\'2026-10-02\')',
                              (110+n, f'original-{n} ' + 'z'*5000))
        source = recall.history(self.db,10,20,{})
        before = deepcopy(source)
        shown = journal_tool_payload('get_journal_history', source)
        self.assertEqual(source,before)
        self.assertLessEqual(len(json.dumps(shown)), JOURNAL_PRESENTATION_MAX_CHARS)
        row = shown['journals'][0]
        self.assertEqual(row['legacy_history_count'],11)
        self.assertTrue(row['legacy_history_details_omitted'])
        self.assertEqual(len(row['legacy_history']),3)
        full = recall.messages(source)
        self.assertTrue(all(len(chunk)<=1800 for chunk in full))
        self.assertEqual(''.join(full).count('z'),50000)
        for n in range(10):
            self.assertIn(f'original-{n}', ''.join(full))
        self.assertEqual(shown,journal_tool_payload('get_journal_history',shown))

    def test_specific_trade_and_legacy_history_filters_and_receipts(self):
        self.conn.execute("INSERT INTO journals VALUES(1,NULL,10,20,'Separate old original','',NULL,'','2025-01-01')")
        trade = recall.history(self.db,10,20,{'trade_number':1})
        self.assertEqual(len(trade['journals']),1)
        self.assertEqual(trade['journals'][0]['trade_number'],1)
        self.assertEqual(trade['matched_record_count'],1)
        legacy = recall.history(self.db,10,20,{'legacy_journal_number':1})
        self.assertEqual(legacy['journals'][0]['summary'],'Separate old original')
        self.assertTrue(legacy['journals'][0]['is_legacy'])
        ambiguous = recall.history(self.db,10,20,{'journal_number':1})
        self.assertEqual(ambiguous['status'],'ambiguous_journal_number')
        virtual = recall.history(self.db,10,20,{'trade_number':2})['journals'][0]
        self.assertTrue(virtual['virtual_trade_record'])
        self.assertIn('Trade #2 journal', '\n'.join(recall.messages(recall.history(self.db,10,20,{'trade_number':2}))))
        self.assertNotEqual(canonical_arguments('send_journal_history',{'trade_number':1}),
                            canonical_arguments('send_journal_history',{'legacy_journal_number':1}))
        self.assertEqual(canonical_arguments('send_journal_history',{'legacy_journal_number':1})['legacy_journal_number'],1)

    def test_update_timeline_retains_execution_management_and_prior_journal_prose(self):
        self.conn.execute("""CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY,thesis_id INT,
            guild_id INT,user_id INT,entry_model TEXT,tier INT,risk_r REAL,
            entry_invalidation TEXT,note TEXT,created_at TEXT)""")
        self.conn.execute("INSERT INTO thesis_executions VALUES(1,25,10,20,'Synthetic model',1,0.25,'Synthetic invalidation','Execution one','2026-10-01')")
        self.conn.execute("INSERT INTO thesis_executions VALUES(2,50,10,20,'FOREIGN execution',1,1,'Foreign','Private','2026-10-01')")
        self.event('partial_exit','Member reports a partial exit.')
        for index in range(6):
            self.event('journal_audit_v1', {
                'journal_id':100,
                'before':{'journal':{'description':f'Prior reflection {index}: ' + 'q'*1000},
                          'details':{'metadata':json.dumps({'transcription':f'Source text {index}',
                              'market_review':{'private_snapshot_marker':'must not expand snapshots'}})}},
                'after':{'journal':{'description':f'Next reflection {index}'},
                         'details':{'metadata':json.dumps({'transcription':f'Corrected source text {index}'})}}})
        self.event('journal_canonical_v1',{'journal_id':100})
        self.event('journal_source_v1',{'journal_id':100,'photo_id':'shared'})
        self.event('journal_audit_v1',{'journal_id':999,'after':{'journal':{'description':'FOREIGN audit'}}})
        self.event('foreign_event','FOREIGN event',thesis=50)
        original_changes = self.conn.total_changes
        result = recall.history(self.db,10,20,{'trade_number':1})
        self.assertEqual(self.conn.total_changes,original_changes)
        row = result['journals'][0]
        self.assertEqual(row['update_count'],8)
        self.assertEqual(row['updates'][0]['risk_r'],0.25)
        full = '\n'.join(recall.messages(result))
        self.assertIn('Execution one',full)
        self.assertIn('Member reports a partial exit.',full)
        for index in range(6):
            self.assertIn(f'Prior reflection {index}',full)
            self.assertIn(f'Next reflection {index}',full)
            self.assertIn(f'Source text {index}',full)
        self.assertNotIn('FOREIGN',full)
        self.assertNotIn('must not expand snapshots',full)
        shown = journal_tool_payload('get_journal_history',result)
        self.assertEqual(shown['journals'][0]['update_count'],8)
        self.assertEqual(len(shown['journals'][0]['updates']),3)
        self.assertTrue(shown['journals'][0]['updates_details_omitted'])
        self.assertLessEqual(len(json.dumps(shown)),JOURNAL_PRESENTATION_MAX_CHARS)
        self.assertEqual(shown,journal_tool_payload('get_journal_history',shown))

    def test_latest_virtual_trade_sorts_by_time_not_unrelated_database_ids(self):
        self.conn.execute('ALTER TABLE theses ADD COLUMN created_at TEXT')
        self.conn.execute("UPDATE theses SET created_at='2026-10-04' WHERE id=40")
        recent = recall.history(self.db,10,20,{'limit':1})['journals'][0]
        self.assertEqual(recent['trade_number'],2)
        self.assertTrue(recent['virtual_trade_record'])

    def test_shared_handwritten_page_uses_selected_trade_context(self):
        self.photo('shared-page')
        self.detail(100,'shared-page')
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Second page entry','',NULL,'','2026-10-02')")
        self.conn.execute("INSERT INTO journal_details VALUES(110,10,20,'shared-page',2,'{}','2026-10-02')")
        result = photos.search(self.db,10,20,{'trade_number':2})
        self.assertEqual(result['photos'][0]['id'],'shared-page')
        self.assertEqual(result['photos'][0]['trade_number'],2)
        self.assertEqual(result['photos'][0]['associated_trade_numbers'],[1,2])
        self.assertEqual(result['photos'][0]['journal']['description'],'Second page entry')
        self.assertEqual(self.ids(unlinked_only=True),[])

    def test_annotation_rechecks_revocation_after_waiting_for_write_lock(self):
        self.conn.execute('CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT)')
        self.conn.execute('INSERT INTO members VALUES(10,20,1,0)')
        original = self.db
        connection = self.conn
        class BeforeLock:
            def __init__(self, inner):
                self.inner = inner
            def execute(self, sql, params=()):
                if sql == 'SELECT pg_advisory_xact_lock(?)' and params == (20,):
                    connection.execute('UPDATE members SET revoked=1 WHERE user_id=20')
                return self.inner.execute(sql,params)
        @contextlib.contextmanager
        def db():
            with original() as inner:
                yield BeforeLock(inner)
        result = photos.annotate(db,10,20,{'photo_id':'shared','trade_number':1,'analysis':'Must not be saved'})
        self.assertFalse(result['ok'])
        self.assertIn('revoked',result['error'])
        self.assertEqual(self.conn.execute("SELECT analysis FROM trade_photos WHERE id='shared'").fetchone()[0],'')


if __name__ == '__main__':
    unittest.main()
