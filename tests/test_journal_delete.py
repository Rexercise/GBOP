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
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'gbop_voice_web/server.py'

class JournalDeleteTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        names = {'journal_fingerprint', 'tool_prepare_journal_delete', 'tool_delete_journal', 'run_tool'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE journals(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, description TEXT, created_at TEXT, thesis_id INTEGER);
        INSERT INTO journals VALUES(1, 10, 20, 'My journal', 'today', NULL);
        INSERT INTO journals VALUES(2, 10, 30, 'Other member', 'today', NULL);
        INSERT INTO journals VALUES(3, 11, 20, 'Other guild', 'today', NULL);
        CREATE TABLE theses(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER);
        INSERT INTO theses VALUES(41, 10, 20);
        INSERT INTO theses VALUES(42, 10, 30);
        UPDATE journals SET thesis_id=41 WHERE id=1;
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER);
        CREATE TABLE risk_flags(id INTEGER PRIMARY KEY, thesis_id INTEGER, guild_id INTEGER, user_id INTEGER);
        INSERT INTO thesis_executions VALUES(1,41,10,20),(2,42,10,30);
        INSERT INTO thesis_events VALUES(1,41,10,20),(2,42,10,30);
        INSERT INTO risk_flags VALUES(1,41,10,20),(2,42,10,30);
        ''')
        class Conn:
            def execute(_, sql, params=()):
                return self.conn.execute(sql.replace(' FOR UPDATE', ''), params)
        @contextlib.contextmanager
        def db():
            with self.conn:
                yield Conn()
        self.ns = dict(hashlib=hashlib, hmac=hmac, json=json, secrets=secrets, time=time,
                       delete_trade_records=delete_trade_records, db=db, GTOP_GUILD_ID=10, JOURNAL_DELETE_TTL=300, PENDING_JOURNAL_DELETIONS={})
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

    def test_wiring_and_request_snapshot(self):
        source = SOURCE.read_text()
        self.assertIn('result = run_tool(user_id, call.name, args, confirmation_token)', source)
        self.assertIn('"prepare_journal_delete": tool_prepare_journal_delete', source)
        self.assertIn('return tool_delete_journal(user_id, args, confirmation_token)', source)

if __name__ == '__main__':
    unittest.main()
