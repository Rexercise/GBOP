"""Run real trade handlers without starting Discord or external API clients."""
import ast
import contextlib
import math
from pathlib import Path
import sqlite3
import unittest
from gbop_voice_web.trade_numbers import trade_number, trade_record_id

ROOT = Path(__file__).resolve().parents[1]

class TradeNumberTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function('pg_advisory_xact_lock', 1, lambda _: 1)
        self.conn.executescript('''
        CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,asset TEXT,direction TEXT,play TEXT,session TEXT,crt_variant TEXT,htf_context TEXT,liquidity_purged TEXT,objective TEXT,thesis_invalidation TEXT,status TEXT,max_r REAL,created_at TEXT,final_result_r REAL);
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,entry_model TEXT,tier INTEGER,risk_r REAL,entry_invalidation TEXT,note TEXT,created_at TEXT);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,event TEXT,details TEXT,result_r REAL,created_at TEXT);
        INSERT INTO theses(id,guild_id,user_id,asset,status) VALUES (2,10,99,'OTHER','OPEN'),(21,10,20,'Bitcoin','CLOSED'),(22,10,20,'NAS','OPEN'),(23,11,20,'OTHER GUILD','OPEN');
        ''')
        @contextlib.contextmanager
        def db():
            yield self.conn
        self.db = db
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS members(guild_id INTEGER,user_id INTEGER,activated INTEGER,revoked INTEGER);
        INSERT INTO members VALUES(10,20,1,0);
        CREATE TABLE IF NOT EXISTS journals(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT,thesis_id INTEGER);
        CREATE TABLE IF NOT EXISTS journal_details(journal_id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,photo_id TEXT,entry_index INTEGER DEFAULT 1,metadata TEXT DEFAULT '{}',updated_at TEXT);
        CREATE TABLE IF NOT EXISTS thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,event TEXT,details TEXT,result_r REAL,created_at TEXT);
        """)

    def tearDown(self):
        self.conn.close()

    def handlers(self, path, names):
        tree = ast.parse((ROOT/path).read_text())
        env = dict(db=self.db, GTOP_GUILD_ID=10, trade_number=trade_number,
                   trade_record_id=trade_record_id, math=math,
                   trade_number_for_id=lambda user, id: trade_number(self.db,10,user,id),
                   trade_id_from_number=lambda user, n: trade_record_id(self.db,10,user,n),
                   thesis_used_r=lambda id: 0, now=lambda:'now', now_iso=lambda:'now',
                   get_profile=lambda *a:{}, ai_infer_tier=lambda *a:1,
                   infer_tier=lambda *a:1, member_tier_limit=lambda *a:1,
                   tier_limit=lambda *a:1)
        selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),env)
        return env

    def test_both_backends_use_member_numbers_for_read_write_and_create(self):
        for path, choose, state, event, create in [
            ('bot.py','ai_choose_open_trade','ai_get_trade_state','ai_record_trade_event','ai_open_trade'),
            ('gbop_voice_web/server.py','choose_open_trade','tool_get_trade_state','tool_record_trade_event','tool_open_trade')]:
            with self.subTest(path=path):
                env=self.handlers(path,[choose,state,event,create])
                self.assertEqual(env[choose](20,2)[0]['id'],22)
                self.assertIsNone(env[choose](20,22)[0])
                self.assertIsNone(env[choose](20,1)[0]) # closed trade still counts
                self.assertEqual(env[state](20,{'trade_id':2})['trades'][0]['trade_id'],2)
                self.assertEqual(env[state](20,{'trade_id':1})['trades'][0]['asset'],'Bitcoin')
                result=env[event](20,dict(trade_id=2,event='note',details='test',result_r=None))
                self.assertEqual(result['trade_id'],2)
                self.assertEqual(self.conn.execute('SELECT thesis_id FROM thesis_events ORDER BY id DESC LIMIT 1').fetchone()[0],22)
                result=env[create](20,dict(asset='Gold',direction='Bullish',play='9ate8',entry_model='Super Soup',risk_r=.25))
                self.assertEqual(result['trade_id'],3)
                raw_id=self.conn.execute('SELECT max(id) FROM theses').fetchone()[0]
                self.assertGreater(raw_id,23)
                self.assertEqual(self.conn.execute('SELECT thesis_id FROM thesis_executions ORDER BY id DESC LIMIT 1').fetchone()[0],raw_id)
                self.conn.execute('DELETE FROM theses WHERE id=?',(raw_id,))

    def test_invalid_and_cross_member_numbers(self):
        self.assertEqual(trade_record_id(self.db,10,20,2),22)
        self.assertEqual(trade_number(self.db,10,20,22),2)
        self.assertIsNone(trade_number(self.db,10,99,22))
        for n in (0,-1,True,'2',22):
            self.assertIsNone(trade_record_id(self.db,10,20,n))

if __name__ == '__main__':
    unittest.main()
