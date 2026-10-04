import ast
import contextlib
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import unittest
from gbop_voice_web.deletion import delete_trade_records
from gbop_voice_web.journal_numbers import journal_number
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'gbop_voice_web/server.py'

class JournalDeleteTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        names = {'journal_fingerprint', 'tool_prepare_journal_delete', 'tool_delete_journal', 'run_tool'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function('md5', 1, lambda value: hashlib.md5(value.encode()).hexdigest() if value is not None else None)
        self.conn.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,leadership_ack INTEGER,revoked INTEGER,updated_at TEXT);
        INSERT INTO members VALUES(10,20,1,1,0,'v1'),(10,30,1,1,0,'v1');
        CREATE TABLE journals(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, description TEXT, created_at TEXT, thesis_id INTEGER,
            result_r REAL, rule_adherence TEXT, study_note TEXT);
        INSERT INTO journals(id,guild_id,user_id,description,created_at) VALUES(1,10,20,'My journal','today'),(2,10,30,'Other member','today'),(3,11,20,'Other guild','today');
        CREATE TABLE theses(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, asset TEXT DEFAULT 'TEST', direction TEXT DEFAULT 'Bullish',
            play TEXT DEFAULT 'Synthetic', status TEXT DEFAULT 'OPEN', final_result_r REAL);
        INSERT INTO theses(id,guild_id,user_id) VALUES(41,10,20),(42,10,30);
        CREATE TABLE trade_photos(id TEXT PRIMARY KEY,guild_id INTEGER,user_id INTEGER,thesis_id INTEGER REFERENCES theses(id) ON DELETE CASCADE,
            analysis TEXT,image_base64 TEXT);
        INSERT INTO trade_photos VALUES('photo1',10,20,41,'Original chart','invented'),('photo2',10,30,42,'Other chart','invented');
        CREATE TABLE journal_details(journal_id INTEGER PRIMARY KEY REFERENCES journals(id) ON DELETE CASCADE,
            guild_id INTEGER,user_id INTEGER,photo_id TEXT REFERENCES trade_photos(id) ON DELETE SET NULL,
            entry_index INTEGER DEFAULT 1,metadata TEXT DEFAULT '{}',updated_at TEXT);
        INSERT INTO journal_details(journal_id,guild_id,user_id,photo_id,metadata) VALUES(1,10,20,'photo1','{}'),(2,10,30,'photo2','{}');
        UPDATE journals SET thesis_id=41 WHERE id=1;
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER, event TEXT, details TEXT, result_r REAL, created_at TEXT);
        CREATE TABLE risk_flags(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER);
        INSERT INTO thesis_executions VALUES(1,41,10,20),(2,42,10,30);
        INSERT INTO thesis_events(id,thesis_id,guild_id,user_id) VALUES(1,41,10,20),(2,42,10,30);
        INSERT INTO risk_flags VALUES(1,41,10,20),(2,42,10,30);
        ''')
        self.before_execute = None
        self.executed = []
        class Conn:
            def execute(_, sql, params=()):
                self.executed.append((sql, params))
                if self.before_execute:
                    self.before_execute(sql, params)
                if 'pg_advisory_xact_lock' in sql:
                    return self.conn.execute('SELECT 1')
                return self.conn.execute(sql.replace(' FOR UPDATE', ''), params)
        @contextlib.contextmanager
        def db():
            with self.conn:
                yield Conn()
        self.ns = dict(hashlib=hashlib, hmac=hmac, json=json, secrets=secrets, time=time,
                       delete_trade_records=delete_trade_records, journal_number=journal_number,
                       db=db, GTOP_GUILD_ID=10, JOURNAL_DELETE_TTL=300, PENDING_JOURNAL_DELETIONS={})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
    def tearDown(self):
        self.conn.close()
    def preview(self, user=20, journal=1):
        return self.ns['tool_prepare_journal_delete'](user, {'journal_id': journal})
    def delete(self, user=20, journal=1, token=None, confirmed=True):
        return self.ns['tool_delete_journal'](user, {'journal_id': journal, 'confirmed': confirmed}, token)
    def token(self):
        return self.ns['PENDING_JOURNAL_DELETIONS'][20]['token']
    def test_confirmed_delete_cascades_and_blocks_replay(self):
        self.assertTrue(self.preview()['requires_confirmation'])
        token = self.token()
        self.assertTrue(self.delete(token=token)['deleted'])
        self.assertFalse(self.delete(token=token)['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 2)
        for table in ('theses', 'thesis_executions', 'thesis_events', 'risk_flags'):
            self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1)
    def test_cannot_delete_without_preview_or_in_same_request(self):
        self.assertFalse(self.delete()['ok'])
        self.preview()
        self.assertFalse(self.delete()['ok'])
        self.assertFalse(self.delete(token=self.token(), confirmed=False)['ok'])
    def test_ownership_guild_and_wrong_target(self):
        self.assertFalse(self.preview(journal=2)['ok'])
        self.assertFalse(self.preview(journal=3)['ok'])
        self.preview()
        self.assertFalse(self.delete(user=30, token=self.token())['ok'])
        self.assertFalse(self.delete(journal=2, token=self.token())['ok'])
    def test_expired_and_changed_records(self):
        self.preview()
        token = self.token()
        self.ns['PENDING_JOURNAL_DELETIONS'][20]['expires_at'] = 0
        self.assertFalse(self.delete(token=token)['ok'])
        self.preview()
        self.conn.execute("UPDATE journals SET description='changed' WHERE id=1")
        self.assertFalse(self.delete(token=self.token())['ok'])
    def test_missing_invalid_and_replaced_preview(self):
        for value in (None, True, -1, '1', 999):
            self.assertFalse(self.preview(journal=value)['ok'])
        self.preview()
        old = self.token()
        self.preview()
        self.assertFalse(self.delete(token=old)['ok'])
    def test_trade_delete_removes_journal_and_leaves_other_member(self):
        with self.ns['db']() as conn:
            result = delete_trade_records(conn, 10, 20, 41)
        self.assertEqual(result['journals'], 1)
        self.assertIsNone(self.conn.execute('SELECT id FROM journals WHERE id=1').fetchone())
        self.assertEqual(self.conn.execute('SELECT id FROM theses').fetchone()[0], 42)

    def test_standalone_journal_leaves_trades(self):
        self.conn.execute('UPDATE journals SET thesis_id=NULL WHERE id=1')
        self.preview()
        self.assertTrue(self.delete(token=self.token())['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0], 2)

    def test_failure_rolls_back_all_deletes(self):
        self.conn.executescript("CREATE TRIGGER refuse_delete BEFORE DELETE ON theses BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
        self.preview()
        with self.assertRaises(sqlite3.IntegrityError):
            self.delete(token=self.token())
        for table in ('theses', 'thesis_executions', 'thesis_events', 'risk_flags'):
            self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 3)

    def test_aggregate_changes_require_new_preview(self):
        changes = [
            "UPDATE journal_details SET metadata='{\"emotion\":\"calm\"}' WHERE journal_id=1",
            "INSERT INTO thesis_executions VALUES(3,41,10,20)",
            "INSERT INTO thesis_events(id,thesis_id,guild_id,user_id,event,details) VALUES(3,41,10,20,'journal_canonical_v1','{}')",
            "INSERT INTO risk_flags VALUES(3,41,10,20)",
            "INSERT INTO journals(id,guild_id,user_id,description,thesis_id) VALUES(4,10,20,'More history',41)",
            "INSERT INTO trade_photos VALUES('photo3',10,20,41,'Added photo','invented')",
            "UPDATE trade_photos SET analysis='Updated photo' WHERE id='photo1'",
            "UPDATE theses SET status='CLOSED' WHERE id=41",
            "DELETE FROM thesis_executions WHERE id=1",
            "DELETE FROM journal_details WHERE journal_id=1",
            "DELETE FROM trade_photos WHERE id='photo1'",
        ]
        for change in changes:
            with self.subTest(change=change):
                self.preview()
                before = tuple(self.conn.execute('SELECT * FROM journals WHERE id=1').fetchone())
                self.conn.execute(change)
                result = self.delete(token=self.token())
                self.assertFalse(result['ok'], result)
                self.assertIn('changed', result['error'])
                self.assertEqual(tuple(self.conn.execute('SELECT * FROM journals WHERE id=1').fetchone()), before)
                self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_unlinked_source_photo_content_is_in_preview(self):
        self.conn.execute("INSERT INTO trade_photos VALUES('source',10,20,NULL,'Original','invented')")
        self.conn.execute('UPDATE journal_details SET metadata=? WHERE journal_id=1',
                          (json.dumps({'source_attachments': [{'photo_id': 'source', 'entry_index': 1}]}),))
        self.preview()
        self.conn.execute("UPDATE trade_photos SET analysis='New annotation' WHERE id='source'")
        self.assertFalse(self.delete(token=self.token())['ok'])

    def test_other_owner_changes_do_not_invalidate_snapshot(self):
        self.preview()
        self.conn.execute("UPDATE trade_photos SET analysis='Other change' WHERE user_id=30")
        self.conn.execute('INSERT INTO risk_flags VALUES(3,42,10,30)')
        self.assertTrue(self.delete(token=self.token())['ok'])
        self.assertIsNotNone(self.conn.execute("SELECT id FROM trade_photos WHERE id='photo2'").fetchone())

    def test_member_revocation_at_lock_blocks_confirmation(self):
        self.preview()
        def hook(sql, params):
            if 'pg_advisory_xact_lock' in sql:
                self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
        self.before_execute = hook
        result = self.delete(token=self.token())
        self.assertFalse(result['ok']); self.assertIn('revoked', result['error'])
        self.assertIsNotNone(self.conn.execute('SELECT id FROM journals WHERE id=1').fetchone())

    def test_preview_expiry_while_waiting_on_lock_blocks_confirmation(self):
        self.preview()
        def hook(sql, params):
            if 'pg_advisory_xact_lock' in sql:
                self.ns['PENDING_JOURNAL_DELETIONS'][20]['expires_at'] = 0
        self.before_execute = hook
        self.assertFalse(self.delete(token=self.token())['ok'])
        self.assertIsNotNone(self.conn.execute('SELECT id FROM journals WHERE id=1').fetchone())

    def test_aggregate_reads_and_delete_share_member_lock(self):
        self.preview(); self.executed.clear()
        self.assertTrue(self.delete(token=self.token())['ok'])
        self.assertIn('pg_advisory_xact_lock', self.executed[0][0])
        self.assertEqual(self.executed[0][1], (20,))
        self.assertIn('FROM members', self.executed[1][0])
        first_delete = next(i for i, (sql, _) in enumerate(self.executed) if sql.startswith('DELETE'))
        for table in ('journal_details', 'trade_photos', 'thesis_executions', 'thesis_events', 'risk_flags'):
            self.assertTrue(any('SELECT ' in sql and ' FROM ' + table in sql for sql, _ in self.executed[:first_delete]))

    def test_inconsistent_child_ownership_never_cascades(self):
        targets = [('trade_photos', "id='photo1'"), ('journal_details', 'journal_id=1'),
                   ('journals', 'id=1'), ('thesis_events', 'id=1'),
                   ('thesis_executions', 'id=1'), ('risk_flags', 'id=1')]
        for table, selector in targets:
            for assignment in ('guild_id=11', 'user_id=30', 'user_id=NULL'):
                with self.subTest(table=table, assignment=assignment):
                    self.assertTrue(self.preview()['ok'])
                    self.conn.execute(f'UPDATE {table} SET {assignment} WHERE {selector}')
                    self.conn.commit()
                    before = list(self.conn.iterdump())
                    self.assertFalse(self.delete(token=self.token())['ok'])
                    self.assertEqual(list(self.conn.iterdump()), before)
                    self.assertFalse(self.preview()['ok'])
                    with self.assertRaisesRegex(ValueError, 'inconsistent ownership'):
                        with self.ns['db']() as conn:
                            delete_trade_records(conn, 10, 20, 41)
                    self.assertEqual(list(self.conn.iterdump()), before)
                    self.conn.execute(f'UPDATE {table} SET guild_id=10,user_id=20 WHERE {selector}')

    def test_photo_content_digest_detects_changes_without_selecting_base64(self):
        self.preview()
        photo_selects = [sql for sql, _ in self.executed if 'SELECT ' in sql and ' FROM trade_photos' in sql]
        self.assertTrue(any('md5(image_base64)' in sql for sql in photo_selects))
        self.assertFalse(any('SELECT *' in sql for sql in photo_selects))
        self.conn.execute("UPDATE trade_photos SET image_base64='different invented bytes' WHERE id='photo1'")
        self.assertFalse(self.delete(token=self.token())['ok'])
        self.assertIsNotNone(self.conn.execute('SELECT id FROM theses WHERE id=41').fetchone())

    def test_wiring_and_request_snapshot(self):
        source = SOURCE.read_text()
        self.assertIn('result = market_context.run(call.name, args,', source)
        self.assertIn('lambda name, values: run_tool(user_id, name, values, confirmation_token)', source)
        self.assertIn('"prepare_journal_delete": tool_prepare_journal_delete', source)
        self.assertIn('return tool_delete_journal(user_id, args, confirmation_token)', source)

if __name__ == '__main__':
    unittest.main()
