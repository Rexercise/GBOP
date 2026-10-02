import ast
import contextlib
import math
import sqlite3
import unittest
from pathlib import Path
from gbop_voice_web import risk_profiles as rp
from gbop_voice_web.trade_numbers import trade_number

class RiskProfileTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        rp._ready = False
        @contextlib.contextmanager
        def db():
            with self.conn:
                yield self.conn
        self.db = db
        self.args = dict(account_risk_pct=2, tier1_pct=75, tier2_pct=25, tier3_pct=0)
    def tearDown(self):
        self.conn.close()
        rp._ready = False
    def test_defaults_do_not_assume_owner_risk(self):
        p = rp.get_profile(self.db, 10, 20)
        self.assertFalse(p['configured'])
        self.assertIsNone(p['account_risk_pct'])
        self.assertEqual(rp.tier_limit(p, 1), .6)
    def test_persistent_save_update_and_member_guild_isolation(self):
        rp.save_profile(self.db, 10, 20, self.args)
        rp._ready = False
        p = rp.get_profile(self.db, 10, 20)
        self.assertTrue(p['configured'])
        self.assertEqual(rp.tier_limit(p, 1), .75)
        self.assertEqual(rp.tier_limit(p, 3), 0)
        self.assertFalse(rp.get_profile(self.db, 10, 21)['configured'])
        self.assertFalse(rp.get_profile(self.db, 11, 20)['configured'])
        rp.save_profile(self.db, 10, 20, dict(self.args, tier1_pct=100, tier2_pct=0, account_risk_pct=None))
        self.assertEqual(rp.get_profile(self.db, 10, 20)['tier1_pct'], 100)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM member_risk_profiles').fetchone()[0], 1)
    def test_invalid_values_never_save(self):
        for changes in ({'tier1_pct': 80}, {'tier2_pct': -1}, {'tier1_pct': float('nan')},
                        {'account_risk_pct': float('inf')}, {'account_risk_pct': 0},
                        {'account_risk_pct': True}, {'tier1_pct': '75'}):
            with self.assertRaises(ValueError):
                rp.save_profile(self.db, 10, 20, dict(self.args, **changes))
    def test_confirmation_required(self):
        tree = ast.parse(Path('gbop_voice_web/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'tool_save_risk_profile']
        ns = dict(save_profile=rp.save_profile, db=self.db, GTOP_GUILD_ID=10)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<test>', 'exec'), ns)
        fn = ns['tool_save_risk_profile']
        self.assertFalse(fn(20, self.args)['ok'])
        self.assertTrue(fn(20, dict(self.args, confirmed=True))['ok'])
    def test_trade_warning_uses_custom_allocation_and_still_saves(self):
        self.conn.executescript('''
        CREATE TABLE theses(id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
          asset TEXT, direction TEXT, play TEXT, session TEXT, crt_variant TEXT,
          htf_context TEXT, liquidity_purged TEXT, objective TEXT, thesis_invalidation TEXT,
          status TEXT, max_r REAL, created_at TEXT);
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY, thesis_id INTEGER,
          guild_id INTEGER, user_id INTEGER, entry_model TEXT, tier INTEGER, risk_r REAL,
          entry_invalidation TEXT, note TEXT, created_at TEXT);
        ''')
        rp.save_profile(self.db, 10, 20, self.args)
        tree = ast.parse(Path('gbop_voice_web/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'tool_open_trade']
        ns = dict(get_profile=rp.get_profile, tier_limit=rp.tier_limit, db=self.db,
                  GTOP_GUILD_ID=10, trade_number=trade_number, infer_tier=lambda model, tier: tier, math=math, now_iso=lambda: 'today')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<test>', 'exec'), ns)
        args = dict(entry_model='Custom', tier=1, risk_r=.7, asset='TEST', direction='Bullish', play='Other')
        self.assertEqual(ns['tool_open_trade'](20, args)['warnings'], [])
        result = ns['tool_open_trade'](20, dict(args, risk_r=.8))
        self.assertTrue(result['ok'])
        self.assertIn('0.75R', result['warnings'][0])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0], 2)

if __name__ == '__main__':
    unittest.main()
