import contextlib
import sqlite3
import unittest

from gbop_voice_web import trade_assist as assist


class TradeAssistTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            asset TEXT,
            direction TEXT,
            play TEXT,
            objective TEXT,
            thesis_invalidation TEXT,
            status TEXT,
            created_at TEXT
        );
        CREATE TABLE thesis_executions (
            id INTEGER PRIMARY KEY,
            thesis_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            entry_model TEXT,
            tier INTEGER,
            risk_r REAL,
            created_at TEXT
        );
        CREATE TABLE thesis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            details TEXT,
            result_r REAL,
            created_at TEXT NOT NULL
        );
        """)

        conn = self.conn

        class Adapter:
            def execute(_, sql, params=()):
                return conn.execute(sql, params)

        @contextlib.contextmanager
        def db():
            with conn:
                yield Adapter()

        self.db = db
        self.guild = 10
        self.user = 20
        self.conn.execute(
            """INSERT INTO theses
            (id,guild_id,user_id,asset,direction,play,objective,thesis_invalidation,status,created_at)
            VALUES (1,?,?,?,?,?,?,?,?,?)""",
            (
                self.guild, self.user, "NAS", "Bearish", "Young Lefty",
                "7 AM SSL", "Close above 7 AM high", "OPEN",
                "2026-10-02T12:00:00+00:00",
            ),
        )
        self.conn.execute(
            """INSERT INTO thesis_executions
            (id,thesis_id,guild_id,user_id,entry_model,tier,risk_r,created_at)
            VALUES (1,1,?,?,?,?,?,?)""",
            (
                self.guild, self.user, "Blessed Thief", 3, 0.5,
                "2026-10-02T12:00:00+00:00",
            ),
        )

    def tearDown(self):
        self.conn.close()

    def test_management_plan_and_progress_trigger_are_member_defined(self):
        saved = assist.update_trade_plan(
            self.db,
            self.guild,
            self.user,
            {
                "trade_id": 1,
                "objective": "7 AM SSL",
                "thesis_invalidation": None,
                "management_plan": "At 80% to objective, protect +0.20R.",
                "protection_trigger_pct": 80,
            },
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(
            saved["trade"]["management"]["protection_trigger_pct"], 80.0
        )

        before = assist.record_trade_progress(
            self.db,
            self.guild,
            self.user,
            {
                "trade_id": 1,
                "progress_pct": 70,
                "current_result_r": 1.2,
                "note": "Still inside plan.",
            },
        )
        self.assertFalse(before["saved_trigger_reached"])

        reached = assist.record_trade_progress(
            self.db,
            self.guild,
            self.user,
            {
                "trade_id": 1,
                "progress_pct": 82,
                "current_result_r": 1.7,
                "note": "Objective nearly delivered.",
            },
        )
        self.assertTrue(reached["saved_trigger_reached"])
        self.assertIn("protect +0.20R", reached["management_reminder"])

    def test_no_saved_plan_means_no_invented_management_rule(self):
        result = assist.record_trade_progress(
            self.db,
            self.guild,
            self.user,
            {
                "trade_id": 1,
                "progress_pct": 85,
                "current_result_r": None,
                "note": None,
            },
        )
        self.assertFalse(result["saved_trigger_reached"])
        self.assertIsNone(result["management_reminder"])

    def test_multiple_open_trades_require_selection(self):
        self.conn.execute(
            """INSERT INTO theses
            (id,guild_id,user_id,asset,direction,play,objective,thesis_invalidation,status,created_at)
            VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (
                self.guild, self.user, "XAUUSD", "Bullish", "9ate8",
                "8 AM buy-side", "Close below range", "OPEN",
                "2026-10-02T12:10:00+00:00",
            ),
        )
        result = assist.get_trade_assist(
            self.db, self.guild, self.user, {"trade_id": None}
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"]["needs_trade_selection"])


if __name__ == "__main__":
    unittest.main()
