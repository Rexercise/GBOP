import contextlib
import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone

from gbop_voice_web import member_intelligence as intel


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE gbop_ss_weekly_reviews (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            week_start TEXT NOT NULL,
            asset TEXT NOT NULL DEFAULT 'General',
            weekly_candle TEXT,
            closure_vs_previous TEXT,
            high_day TEXT,
            high_time TEXT,
            high_launchpad TEXT,
            high_details TEXT,
            low_day TEXT,
            low_time TEXT,
            low_launchpad TEXT,
            low_details TEXT,
            structural_summary TEXT,
            next_week_hypothesis TEXT,
            hypothesis_invalidation TEXT,
            over_leverage INTEGER,
            trade_limit_exceeded INTEGER,
            boredom_trades INTEGER,
            closed_too_early INTEGER,
            exited_too_late INTEGER,
            prediction_correct INTEGER,
            prediction_miss_reason TEXT,
            structure_complete INTEGER NOT NULL DEFAULT 0,
            execution_review_complete INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id,user_id,week_start,asset)
        );
        CREATE TABLE gbop_coaching_observations (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            source_key TEXT NOT NULL,
            theme TEXT NOT NULL,
            polarity INTEGER NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            note TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (guild_id,user_id,source_key,theme,polarity)
        );
        CREATE TABLE gbop_coaching_controls (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            theme TEXT NOT NULL,
            retired_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id,user_id,theme)
        );
        CREATE TABLE post_shift_checkins (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            shift_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            prompt_sent_at TEXT NOT NULL,
            response TEXT,
            responded_at TEXT
        );
        CREATE TABLE journals (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            description TEXT,
            rule_adherence TEXT,
            result_r REAL,
            study_note TEXT,
            created_at TEXT NOT NULL,
            thesis_id INTEGER
        );
        CREATE TABLE risk_flags (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rule_code TEXT,
            message TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE gbop_shift_plans (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            session_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            plan TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id,user_id,session_date,shift)
        );
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            asset TEXT,
            direction TEXT,
            play TEXT,
            session TEXT,
            status TEXT,
            final_result_r REAL,
            created_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE TABLE thesis_executions (
            id INTEGER PRIMARY KEY,
            thesis_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            entry_model TEXT,
            tier INTEGER,
            risk_r REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE member_risk_profiles (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            account_risk_pct REAL,
            tier1_pct REAL NOT NULL,
            tier2_pct REAL NOT NULL,
            tier3_pct REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id,user_id)
        );
        """)

        conn = self.conn

        class Adapter:
            def execute(_, sql, params=()):
                compact = " ".join(str(sql).split()).upper()
                if (
                    "PG_ADVISORY_XACT_LOCK" in compact
                    or compact.startswith("CREATE TABLE IF NOT EXISTS GBOP_")
                    or compact.startswith("CREATE INDEX IF NOT EXISTS GBOP_")
                    or compact.startswith("ALTER TABLE GBOP_")
                    or compact.startswith("REVOKE ALL ON GBOP_")
                    or compact.startswith("GRANT ALL ON GBOP_")
                ):
                    return conn.execute("SELECT 1")
                return conn.execute(sql, params)

        @contextlib.contextmanager
        def db():
            with conn:
                yield Adapter()

        self.db = db
        self.guild = 10
        self.user = 20

    def tearDown(self):
        self.conn.close()

    def complete_ss_args(self, **overrides):
        args = {
            "week_start": "2026-09-28",
            "asset": "XAUUSD",
            "weekly_candle": "Bullish weekly candle with a lower wick.",
            "closure_vs_previous": "Closed above the prior weekly candle.",
            "high_day": "Thursday",
            "high_time": "10:00 AM",
            "high_launchpad": "Tuesday sell-side purge",
            "high_details": "Expansion began after the launchpad held.",
            "low_day": "Tuesday",
            "low_time": "9:00 AM",
            "low_launchpad": "Monday low",
            "low_details": "Low formed after the Monday range purge.",
            "structural_summary": "Tuesday formed LOW, then price expanded into Thursday HOW.",
            "next_week_hypothesis": "If Tuesday's launchpad holds, continuation toward opposing weekly liquidity remains possible.",
            "hypothesis_invalidation": "A decisive close through Tuesday's launchpad weakens the idea.",
            "over_leverage": False,
            "trade_limit_exceeded": False,
            "boredom_trades": False,
            "closed_too_early": True,
            "exited_too_late": False,
            "prediction_correct": False,
            "prediction_miss_reason": "I expected immediate expansion and ignored the earlier weekly purge.",
        }
        args.update(overrides)
        return args

    def test_ss_persists_resumes_and_is_json_safe(self):
        first = intel.save_ss_review(
            self.db,
            self.guild,
            self.user,
            {
                "week_start": "2026-09-28",
                "asset": "XAUUSD",
                "weekly_candle": "Bullish weekly candle",
            },
        )
        self.assertTrue(first["ok"])
        self.assertFalse(first["review"]["structure_complete"])
        self.assertIn("close", first["next_step"].lower())

        complete = intel.save_ss_review(
            self.db, self.guild, self.user, self.complete_ss_args()
        )
        self.assertTrue(complete["review"]["structure_complete"])
        self.assertTrue(complete["review"]["execution_review_complete"])
        self.assertEqual(complete["next_step"], "SS review is complete.")

        resumed = intel.get_ss_review(
            self.db, self.guild, self.user, {"asset": "XAUUSD"}
        )
        self.assertEqual(resumed["review"]["high_day"], "Thursday")
        self.assertEqual(
            intel._rowdict({"d": date(2026, 9, 28), "t": datetime(2026, 9, 28, tzinfo=timezone.utc)}),
            {"d": "2026-09-28", "t": "2026-09-28T00:00:00+00:00"},
        )

    def test_prediction_miss_reason_is_required_only_when_prediction_is_wrong(self):
        wrong = self.complete_ss_args(prediction_miss_reason=None)
        saved = intel.save_ss_review(self.db, self.guild, self.user, wrong)
        self.assertFalse(saved["review"]["execution_review_complete"])
        self.assertIn("why", saved["next_step"].lower())

        corrected = intel.save_ss_review(
            self.db,
            self.guild,
            self.user,
            {
                "week_start": "2026-09-28",
                "asset": "XAUUSD",
                "prediction_miss_reason": "I ignored the prior weekly purge.",
            },
        )
        self.assertTrue(corrected["review"]["execution_review_complete"])

    def test_ss_execution_review_becomes_coaching_evidence(self):
        intel.save_ss_review(self.db, self.guild, self.user, self.complete_ss_args())
        rows = self.conn.execute(
            """SELECT theme,polarity FROM gbop_coaching_observations
            WHERE guild_id=? AND user_id=? ORDER BY theme""",
            (self.guild, self.user),
        ).fetchall()
        evidence = {(row["theme"], row["polarity"]) for row in rows}
        self.assertIn(("exit_timing_early", -1), evidence)
        self.assertIn(("boredom", 1), evidence)
        self.assertIn(("weekly_prediction", -1), evidence)

    def test_shared_negation_does_not_turn_fomo_into_an_issue(self):
        observations = intel.parse_behavior_text(
            "I followed my plan. I did not take any boredom or FOMO trades."
        )
        states = {(theme, polarity) for theme, polarity, _, _ in observations}
        self.assertIn(("plan_adherence", 1), states)
        self.assertIn(("boredom", 1), states)
        self.assertIn(("fomo", 1), states)
        self.assertNotIn(("fomo", -1), states)
        self.assertNotIn(("boredom", -1), states)

    def test_checkins_update_profile_and_retirement_clears_old_theme(self):
        now = datetime.now(timezone.utc)
        self.conn.execute(
            """INSERT INTO post_shift_checkins
            (id,guild_id,user_id,shift_date,shift,prompt_sent_at,response,responded_at)
            VALUES (1,?,?,?,?,?,?,?)""",
            (
                self.guild,
                self.user,
                now.date().isoformat(),
                "day",
                now.isoformat(),
                "I took a boredom trade and closed too early.",
                now.isoformat(),
            ),
        )
        profile = intel.coaching_profile(
            self.db, self.guild, self.user, now_utc=now + timedelta(seconds=1)
        )
        issue_themes = {item["theme"] for item in profile["issues"]}
        self.assertIn("boredom", issue_themes)
        self.assertIn("exit_timing_early", issue_themes)

        retired = intel.set_coaching_theme(
            self.db,
            self.guild,
            self.user,
            {"theme": "boredom", "active": False, "note": "No longer an issue."},
        )
        self.assertTrue(retired["ok"])
        refreshed = intel.coaching_profile(
            self.db, self.guild, self.user, now_utc=now + timedelta(seconds=2)
        )
        self.assertNotIn("boredom", {item["theme"] for item in refreshed["issues"]})

    def test_pre_shift_message_combines_ss_and_personal_focus(self):
        intel.save_ss_review(
            self.db,
            self.guild,
            self.user,
            self.complete_ss_args(week_start=intel.default_ss_week_start()),
        )
        msg = intel.build_pre_shift_message(
            self.db,
            self.guild,
            self.user,
            "day",
            "2026-10-02",
        )
        self.assertIn("Day Shift begins in 5 minutes", msg)
        self.assertIn("Tuesday's launchpad", msg)
        self.assertIn("planned objective", msg)

    def test_member_dashboard_summarizes_evidence_without_assuming_preferences(self):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO theses
            (id,guild_id,user_id,asset,direction,play,session,status,final_result_r,created_at,closed_at)
            VALUES (1,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.guild, self.user, "NAS", "Bearish", "Young Lefty", "day",
                "CLOSED", 2.0, now, now,
            ),
        )
        self.conn.execute(
            """INSERT INTO thesis_executions
            (id,thesis_id,guild_id,user_id,entry_model,tier,risk_r,created_at)
            VALUES (1,1,?,?,?,?,?,?)""",
            (self.guild, self.user, "Blessed Thief", 3, 0.5, now),
        )
        self.conn.execute(
            """INSERT INTO journals
            (id,guild_id,user_id,description,rule_adherence,result_r,study_note,created_at,thesis_id)
            VALUES (1,?,?,?,?,?,?,?,1)""",
            (
                self.guild, self.user, "Good read; exit was early.", "partial",
                2.0, "Let objective work.", now,
            ),
        )
        self.conn.execute(
            """INSERT INTO member_risk_profiles
            (guild_id,user_id,account_risk_pct,tier1_pct,tier2_pct,tier3_pct,updated_at)
            VALUES (?,?,?,?,?,?,?)""",
            (self.guild, self.user, 20.0, 75.0, 25.0, 0.0, now),
        )

        dashboard = intel.get_member_dashboard(
            self.db, self.guild, self.user, {"days": 30}
        )

        self.assertTrue(dashboard["ok"])
        self.assertEqual(dashboard["performance"]["net_r"], 2.0)
        self.assertEqual(dashboard["most_traded_plays"][0]["play"], "Young Lefty")
        self.assertEqual(
            dashboard["strongest_execution_model"]["entry_model"],
            "Blessed Thief",
        )
        self.assertIn("frequency-based", dashboard["evidence_note"])

    def test_default_ss_week_uses_only_completed_week(self):
        friday_morning = datetime(2026, 10, 2, 9, 0, tzinfo=intel.EASTERN)
        friday_evening = datetime(2026, 10, 2, 18, 0, tzinfo=intel.EASTERN)
        saturday = datetime(2026, 10, 3, 9, 0, tzinfo=intel.EASTERN)
        self.assertEqual(intel.default_ss_week_start(friday_morning), "2026-09-21")
        self.assertEqual(intel.default_ss_week_start(friday_evening), "2026-09-28")
        self.assertEqual(intel.default_ss_week_start(saturday), "2026-09-28")

    def test_member_data_is_isolated(self):
        intel.save_ss_review(self.db, self.guild, self.user, self.complete_ss_args())
        other = intel.get_ss_review(self.db, self.guild, 30, {})
        self.assertIsNone(other["review"])


if __name__ == "__main__":
    unittest.main()
