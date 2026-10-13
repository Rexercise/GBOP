"""Regression for prepared SS reports disappearing when reflection is absent."""
import copy
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from gbop_voice_web import member_intelligence as intel
from gbop_voice_web import weekly_structure as ss
from gbop_voice_web.market_data import ASSETS
from tests import test_member_intelligence as _fixture


def epoch(value):
    return int(datetime.fromisoformat(value).timestamp())


class PreShiftStructureTests(unittest.TestCase):
    setUp = _fixture.IntelligenceTests.setUp
    tearDown = _fixture.IntelligenceTests.tearDown
    NOW = epoch('2026-10-13T08:55:00-04:00')

    def report(self, asset='XAUUSD', week='2026-10-05', version='test-v1'):
        window = ss.week_window(week)
        report = dict(ok=True, asset=asset, week_start=week, report_version=version,
            facts_hash=asset+week+version, generated_at_utc='2026-10-10T22:00:00Z',
            window=window, observed_ohlc=dict(open=100, high=110, low=90, close=105, body_direction='bullish'),
            coverage=dict(status='unverified_intervals', source_timeframe='M5', unobserved_interval_count=2,
                          full_broker_week_verified=False, nominal_final_bar_present=True),
            closure_vs_previous=dict(relationship='above'))
        return ss.persist_report(self.db, report)

    def read(self):
        return ss.pre_shift_structure(self.db, 10, 20, now=self.NOW)

    def save(self, report, **answers):
        return intel.save_ss_review(self.db, 10, 20,
            {key: report[key] for key in ('asset', 'week_start', 'report_version')} | answers)

    def test_prepared_market_week_with_no_member_answers_is_visible(self):
        self.report()
        plan = intel.get_member_plan(self.db, 10, 20, {'session_date': '2026-10-13', 'shift': 'day'})
        self.assertIsNone(plan['current_ss'])
        self.assertEqual(plan['weekly_structure']['reports'][0]['asset'], 'XAUUSD')
        message = intel.build_pre_shift_message(self.db, 10, 20, 'day', '2026-10-13')
        self.assertIn('XAUUSD: bullish; last close above prior', message)
        self.assertIn('2026-10-05', message)
        self.assertIn('hypothesis is not saved', message)
        self.assertIn('Launchpad/structure review pending', message)
        self.assertNotIn('No saved SS weekly structure', message)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_ss_weekly_reviews').fetchone()[0], 0)

    def test_shared_context_does_not_tell_voice_that_ss_is_missing(self):
        self.report()
        with patch.object(ss.clock, 'time', return_value=self.NOW):
            context = intel.intelligence_context(self.db, 10, 20)
        self.assertIn('XAUUSD: bullish', context)
        self.assertIn('this does not mean the market study is missing', context)
        self.assertNotIn('Latest SS: none', context)

    def test_hypothesis_is_private_and_report_version_scoped(self):
        r = self.report()
        self.save(r, next_week_hypothesis='Hold the recorded launchpad', hypothesis_invalidation='Close below it')
        text = ss.format_pre_shift_structure(self.read())
        self.assertIn('Your saved XAUUSD hypothesis: Hold the recorded launchpad', text)
        self.assertIn('Condition/invalidation: Close below it', text)
        for guild, user in ((10, 21), (11, 20)):
            other = ss.pre_shift_structure(self.db, guild, user, now=self.NOW)
            self.assertNotIn('Hold the recorded', json.dumps(other))
        self.report(version='test-v2')
        self.assertNotIn('Hold the recorded', json.dumps(self.read()))

    def test_previous_week_and_legacy_answers_cannot_become_current_plan(self):
        old = self.report(week='2026-09-28')
        self.save(old, next_week_hypothesis='OLD WEEK ONLY')
        self.assertEqual(self.read()['reports'], [])
        self.report()
        self.assertNotIn('OLD WEEK ONLY', json.dumps(self.read()))

    def test_friday_day_uses_prior_week_and_night_uses_just_completed_week(self):
        self.report(week='2026-09-28')
        self.report()
        day = intel.get_member_plan(self.db, 10, 20, {'session_date': '2026-10-09', 'shift': 'day'})
        night = intel.get_member_plan(self.db, 10, 20, {'session_date': '2026-10-09', 'shift': 'night'})
        self.assertEqual(day['weekly_structure']['reports'][0]['week_start'], '2026-09-28')
        self.assertEqual(night['weekly_structure']['reports'][0]['week_start'], '2026-10-05')

    def test_no_candles_does_not_masquerade_as_available_structure(self):
        r = self.report()
        ss.persist_report(self.db, dict(r, facts_hash='empty', report_version='empty', observed_ohlc=None))
        state = self.read()
        self.assertEqual(state['reports'], [])
        self.assertIn('XAUUSD', state['unavailable_assets'])
        self.assertIn('market facts are unavailable', ss.format_pre_shift_structure(state))

    def test_crypto_uses_actual_closed_interval_and_rejects_stale_or_future(self):
        r = self.report('BTCUSD')
        r['window'] = dict(r['window'], end_epoch=self.NOW-60, source_boundary_verified=True)
        r.update(facts_hash='source-window', report_version='source-window')
        ss.persist_report(self.db, r)
        self.assertEqual(self.read()['reports'][0]['asset'], 'BTCUSD')
        for end in (self.NOW+60, self.NOW-7*86400):
            bad = copy.deepcopy(r)
            bad['window']['end_epoch'] = end
            bad.update(facts_hash=str(end), report_version=str(end))
            ss.persist_report(self.db, bad)
            self.assertEqual(self.read()['reports'], [])

    def test_explicit_member_analysis_survives_missing_market_candles(self):
        r = self.report()
        r = ss.persist_report(self.db, dict(r, facts_hash='empty', report_version='empty', observed_ohlc=None))
        self.save(r, structural_summary='Member chart review', next_week_hypothesis='Member conditional hypothesis')
        state = self.read()
        self.assertIn('XAUUSD', state['unavailable_assets'])
        text = ss.format_pre_shift_structure(state)
        self.assertIn('market candles unavailable', text)
        self.assertIn('Your saved XAUUSD hypothesis: Member conditional hypothesis', text)

    def test_prior_comparison_unknown_and_coverage_limits_are_preserved(self):
        r = self.report()
        r.update(facts_hash='no-comparison', report_version='no-comparison', closure_vs_previous=None)
        ss.persist_report(self.db, r)
        state = self.read()
        self.assertFalse(state['reports'][0]['coverage']['full_broker_week_verified'])
        text = ss.format_pre_shift_structure(state)
        self.assertIn('prior close unavailable', text)
        self.assertIn('not a current-week bias', text)
        self.assertIn('full-week coverage unverified', text)

    def test_all_assets_and_long_hypotheses_fit_discord_with_news_room(self):
        for asset in ASSETS:
            r = self.report(asset)
            self.save(r, next_week_hypothesis='x'*12000, hypothesis_invalidation='y'*12000)
        msg = intel.build_pre_shift_message(self.db, 10, 20, 'night', '2026-10-13')
        self.assertLess(len(msg), 1750)
        for asset in ASSETS:
            self.assertIn(asset, msg)
        self.assertIn('other asset hypotheses saved', msg)

    def test_read_path_never_prepares_or_saves_reflections(self):
        self.report()
        self.conn.execute('PRAGMA query_only=ON')
        with patch.object(ss, 'build_report', side_effect=AssertionError('not on reminder path')), \
                patch.object(ss, 'persist_report', side_effect=AssertionError('read only')), \
                patch.object(intel, 'save_ss_review', side_effect=AssertionError('never infer answers')):
            self.assertTrue(self.read()['reports'])


if __name__ == '__main__':
    unittest.main()
