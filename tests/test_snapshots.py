import contextlib
from datetime import date
import sqlite3
import unittest
from zoneinfo import ZoneInfo

from gbop_voice_web.snapshots import (
    collect_snapshot,
    daily_period,
    weekly_period,
)


def dict_factory(cursor, row):
    return {
        column[0]: row[index]
        for index, column in enumerate(cursor.description)
    }


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = dict_factory
        self.conn.executescript(
            """
            CREATE TABLE theses(
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                user_id INTEGER,
                asset TEXT,
                direction TEXT,
                play TEXT,
                session TEXT,
                status TEXT,
                final_result_r REAL,
                created_at TEXT,
                closed_at TEXT
            );
            CREATE TABLE thesis_executions(
                id INTEGER PRIMARY KEY,
                thesis_id INTEGER,
                guild_id INTEGER,
                user_id INTEGER,
                entry_model TEXT,
                tier INTEGER,
                risk_r REAL,
                created_at TEXT
            );
            CREATE TABLE risk_flags(
                id INTEGER PRIMARY KEY,
                thesis_id INTEGER,
                guild_id INTEGER,
                user_id INTEGER,
                rule_code TEXT,
                message TEXT,
                created_at TEXT
            );
            CREATE TABLE journals(
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                user_id INTEGER,
                rule_adherence TEXT,
                result_r REAL,
                study_note TEXT,
                created_at TEXT
            );
            CREATE TABLE post_shift_checkins(
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                user_id INTEGER,
                shift_date TEXT,
                shift TEXT,
                response TEXT,
                responded_at TEXT
            );
            """
        )
        self.conn.executescript(
            """
            INSERT INTO theses VALUES
              (1,10,20,'Gold','Bullish','9ate8','Day Shift','CLOSED',3.0,'2026-10-01T13:00:00+00:00','2026-10-01T14:00:00+00:00'),
              (2,10,20,'NAS100','Bearish','9ate8','Night Shift','CLOSED',-1.0,'2026-10-02T00:30:00+00:00','2026-10-02T01:00:00+00:00'),
              (3,10,20,'Silver','Bullish','GCT','Day Shift','CLOSED',2.0,'2026-10-02T04:30:00+00:00','2026-10-02T05:00:00+00:00'),
              (4,10,20,'Oil','Bullish','CBDR','Day Shift','OPEN',NULL,'2026-10-01T15:00:00+00:00',NULL);

            INSERT INTO thesis_executions VALUES
              (1,1,10,20,'Super Soup',1,0.75,'2026-10-01T13:10:00+00:00'),
              (2,1,10,20,'Blessed Thief',3,0.25,'2026-10-01T13:20:00+00:00'),
              (3,2,10,20,'Model 1',2,0.50,'2026-10-02T00:45:00+00:00');

            INSERT INTO risk_flags VALUES
              (1,2,10,20,'TIER_LIMIT','Too much risk','2026-10-02T00:46:00+00:00');

            INSERT INTO journals VALUES
              (1,10,20,'Followed',3.0,'Keep it','2026-10-01T14:05:00+00:00'),
              (2,10,20,'Partial deviation',-1.0,'Wait longer','2026-10-02T01:05:00+00:00');

            INSERT INTO post_shift_checkins VALUES
              (1,10,20,'2026-10-01','day','Followed plan','2026-10-01T17:05:00+00:00'),
              (2,10,20,'2026-10-01','night',NULL,NULL);
            """
        )

        @contextlib.contextmanager
        def db():
            yield self.conn

        self.db = db

    def tearDown(self):
        self.conn.close()

    def test_daily_period_uses_eastern_calendar_day(self):
        period = daily_period(
            date(2026, 10, 1),
            ZoneInfo("America/New_York"),
        )
        self.assertEqual(
            period["start_utc"],
            "2026-10-01T04:00:00+00:00",
        )
        self.assertEqual(
            period["end_utc"],
            "2026-10-02T04:00:00+00:00",
        )

    def test_collects_tradezella_style_stats(self):
        period = daily_period(
            date(2026, 10, 1),
            ZoneInfo("America/New_York"),
        )
        stats = collect_snapshot(
            self.db,
            10,
            20,
            period,
        )

        performance = stats["performance"]
        process = stats["process"]

        self.assertEqual(performance["closed_trades"], 2)
        self.assertEqual(performance["opened_trades"], 3)
        self.assertEqual(
            (
                performance["wins"],
                performance["losses"],
                performance["breakeven"],
            ),
            (1, 1, 0),
        )
        self.assertAlmostEqual(
            performance["win_rate"],
            50.0,
        )
        self.assertAlmostEqual(
            performance["net_r"],
            2.0,
        )
        self.assertAlmostEqual(
            performance["profit_factor"],
            3.0,
        )
        self.assertEqual(
            performance["open_now"],
            1,
        )
        self.assertEqual(
            stats["execution"]["count"],
            3,
        )
        self.assertAlmostEqual(
            stats["execution"]["risk_r"],
            1.5,
        )
        self.assertEqual(
            process["risk_flags"],
            1,
        )
        self.assertEqual(
            (
                process["followed"],
                process["partial"],
                process["violated"],
            ),
            (1, 1, 0),
        )
        self.assertAlmostEqual(
            process["full_adherence_rate"],
            50.0,
        )
        self.assertEqual(
            (
                process["checkins_completed"],
                process["checkins_expected"],
            ),
            (1, 2),
        )
        self.assertIn(
            "risk-protocol",
            stats["recommendation"],
        )

    def test_weekly_period_is_monday_through_friday(self):
        period = weekly_period(
            date(2026, 10, 2),
            ZoneInfo("America/New_York"),
        )
        self.assertEqual(
            period["start_day"],
            date(2026, 9, 28),
        )
        self.assertEqual(
            period["end_day"],
            date(2026, 10, 2),
        )


if __name__ == "__main__":
    unittest.main()
