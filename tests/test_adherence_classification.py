"""Synthetic regressions for neutral unknown adherence and explicit reports."""
import unittest
from datetime import date, datetime, timedelta, timezone

from gbop_voice_web.adherence import adherence_bucket
from gbop_voice_web import member_intelligence as intel, snapshots
from tests import test_member_intelligence as fixtures, test_snapshots as snapshot_fixtures


class AdherenceClassificationTests(unittest.TestCase):
    def test_explicit_labels_and_complete_statements_have_consistent_buckets(self):
        cases = {
            'followed': ('yes', 'Y', 'true', 'full', 'Clean', 'followed', 'adhered',
                         'followed_plan', '  FOLLOWED  the\tplan! ', 'I followed my plan.',
                         'I adhered to my rules', 'I stuck to the plan', 'plan followed'),
            'partial': ('partial', 'Partial deviation', 'partial adherence', 'deviated',
                        'I partially followed my plan', 'partially followed',
                        'I mostly followed the rules', 'I deviated from my plan'),
            'violated': ('no', 'N', 'false', 'violated', 'off-plan', 'off_plan',
                         'not followed', 'did not follow my plan', 'I did not adhere to the rules',
                         "I didn't follow my plan", 'I didn’t follow the plan',
                         'I broke my rules', 'the plan was not followed'),
        }
        for bucket, values in cases.items():
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(adherence_bucket(value), bucket)
                    self.assertEqual(intel._adherence_bucket(value), bucket)
                    self.assertEqual(snapshots._adherence_bucket(value), bucket)

    def test_unknown_missing_ambiguous_and_unrelated_substrings_stay_neutral(self):
        values = (None, '', ' ', 'unknown', ' UNKNOWN. ', 'unspecified', 'not specified',
                  'n/a', 'not applicable', 'no assessment', 'not sure', 'not known',
                  'no violations', 'never violated', 'did not violate my plan',
                  'yes and no', 'yes?', 'I may have followed my plan',
                  'I am not sure I followed my plan', 'If I followed my plan',
                  'not partially followed', 'not clean', 'unfollowed', 'yesterday',
                  'cleaning', 'notes only', 'partiality', 'violations unknown',
                  'plan adherence unknown', 'nonadherence unknown',
                  'study of plan violations', 'adherence not assessed', True, False, 1)
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(adherence_bucket(value), 'unknown')
                self.assertEqual(intel._adherence_bucket(value), 'unknown')
                self.assertEqual(snapshots._adherence_bucket(value), 'unknown')


class AdherenceIngestionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.IntelligenceTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.conn, self.db = fixture.conn, fixture.db
        self.now = datetime.now(timezone.utc)

    def journal(self, ident, adherence, *, user=20, guild=10, created_at=None,
                description='Synthetic structure study.', study_note='Historical note retained.'):
        self.conn.execute('''INSERT INTO journals
            (id,guild_id,user_id,description,rule_adherence,result_r,study_note,created_at)
            VALUES (?,?,?,?,?,NULL,?,?)''',
            (ident, guild, user, description, adherence, study_note,
             (created_at or self.now).isoformat()))

    def refresh(self):
        intel.refresh_coaching_sources(self.db, 10, 20, now_utc=self.now)

    def observations(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM gbop_coaching_observations ORDER BY source_key,theme,polarity')]

    def profile(self):
        return intel.coaching_profile(self.db, 10, 20, now_utc=self.now)

    def test_unknown_study_creates_no_plan_issue_or_strength_and_preserves_notes(self):
        for ident, value in enumerate(('unknown', ' UNKNOWN ', None, '', 'not specified',
                                      'no assessment', 'plan adherence unknown'), 1):
            self.journal(ident, value)
        before = [dict(row) for row in self.conn.execute('SELECT * FROM journals')]
        self.refresh()
        self.refresh()
        self.assertEqual(self.observations(), [])
        profile = self.profile()
        self.assertEqual(profile['issues'], [])
        self.assertEqual(profile['strengths'], [])
        self.assertIsNone(profile['current_focus'])
        self.assertEqual(before, [dict(row) for row in self.conn.execute('SELECT * FROM journals')])

    def test_explicit_positive_negative_and_partial_have_correct_polarity_and_raw_note(self):
        values = ('yes', 'no', 'partial', 'I did not adhere to my rules',
                  'Partial adherence', 'not followed', '  FOLLOWED  my plan! ')
        for ident, value in enumerate(values, 1):
            self.journal(ident, value)
        self.refresh()
        self.refresh()  # Source-scoped retries remain idempotent.
        rows = self.observations()
        self.assertEqual(len(rows), len(values))
        for row, value in zip(rows, values):
            followed = adherence_bucket(value) == 'followed'
            self.assertEqual(row['theme'], 'plan_adherence')
            self.assertEqual(row['polarity'], 1 if followed else -1)
            self.assertEqual(row['weight'], 0.9 if followed else 1.0)
            self.assertEqual(row['note'], value)

    def test_unknown_does_not_suppress_independent_explicit_negative_coaching(self):
        self.journal(1, 'unknown')
        self.journal(2, 'no', description='Synthetic trade reflection.')
        profile = self.profile()
        self.assertEqual(len(self.observations()), 1)
        self.assertEqual(self.observations()[0]['source_key'], 'journal:2')
        self.assertEqual(profile['current_focus']['theme'], 'plan_adherence')
        self.assertEqual(profile['issues'][0]['latest_note'], 'no')
        self.assertEqual(profile['strengths'], [])

    def test_unknown_adherence_keeps_separate_explicit_behavior_evidence(self):
        self.journal(1, 'unknown', study_note='I took a boredom trade.')
        self.refresh()
        self.assertEqual([(r['theme'], r['polarity']) for r in self.observations()],
                         [('boredom', -1)])

    def test_only_requested_member_guild_and_lookback_are_ingested(self):
        self.journal(1, 'unknown')
        self.journal(2, 'no', user=21)
        self.journal(3, 'no', guild=11)
        self.journal(4, 'no', created_at=self.now - timedelta(days=46))
        self.refresh()
        self.assertEqual(self.observations(), [])

    def test_prevention_does_not_silently_repair_or_delete_existing_observations(self):
        self.journal(1, 'unknown')
        self.conn.execute('''INSERT INTO gbop_coaching_observations
            (guild_id,user_id,source_key,theme,polarity,weight,note,observed_at)
            VALUES (10,20,'journal:1','plan_adherence',-1,1.0,'unknown',?)''',
            (self.now.isoformat(),))
        before = self.observations()
        self.refresh()
        self.assertEqual(self.observations(), before)

    def test_dashboard_keeps_unknown_outside_known_adherence_denominator(self):
        for ident, value in enumerate(('unknown', 'yes', 'no', 'partial', 'not followed'), 1):
            self.journal(ident, value)
        dashboard = intel.get_member_dashboard(self.db, 10, 20, {'days': 30})
        self.assertEqual(dashboard['adherence'], {
            'followed': 1, 'partial': 1, 'violated': 2, 'unknown': 1,
            'full_adherence_rate_pct': 25.0,
        })


class SnapshotAdherenceTests(unittest.TestCase):
    def setUp(self):
        fixture = snapshot_fixtures.SnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.conn, self.db = fixture.conn, fixture.db
        self.period = snapshots.daily_period(date(2026, 10, 1), snapshots.ZoneInfo('America/New_York'))

    def test_review_distinguishes_unknown_from_negative_without_changing_performance(self):
        before = snapshots.collect_snapshot(self.db, 10, 20, self.period)
        self.conn.execute("UPDATE journals SET rule_adherence='unknown' WHERE id=1")
        self.conn.execute("UPDATE journals SET rule_adherence='not followed' WHERE id=2")
        after = snapshots.collect_snapshot(self.db, 10, 20, self.period)
        self.assertEqual(after['performance'], before['performance'])
        self.assertEqual({key: after['process'][key] for key in
                          ('followed', 'partial', 'violated', 'unknown', 'full_adherence_rate')},
                         dict(followed=0, partial=0, violated=1, unknown=1, full_adherence_rate=0.0))
        self.conn.execute("UPDATE journals SET rule_adherence='unknown'")
        unknown = snapshots.collect_snapshot(self.db, 10, 20, self.period)
        self.assertIsNone(unknown['process']['full_adherence_rate'])
        self.assertEqual(unknown['process']['unknown'], 2)
        self.assertEqual(unknown['process']['violated'], 0)

    def test_explicit_metadata_unknown_does_not_fall_back_to_affirmative_legacy_field(self):
        for value, expected in (('unknown', 'unknown'), ('no', 'violated'),
                                ('partial', 'partial'), ('yes', 'followed')):
            with self.subTest(value=value):
                self.assertEqual(snapshots._record_adherence({
                    'metadata': {'adherence': value}, 'rule_adherence': 'yes',
                }), expected)


if __name__ == '__main__':
    unittest.main()
