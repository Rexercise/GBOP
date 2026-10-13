"""Synthetic reminder composition and fake Discord transport; no provider calls."""
import copy
from datetime import datetime, timedelta
import re
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from gbop_voice_web import member_intelligence as intel
from gbop_voice_web import weekly_structure as ss
from gbop_voice_web.forex_news import ForexNewsEvent, format_pre_shift_news
from gbop_voice_web.market_data import ASSETS
from gbop_voice_web.ss_preferences import update_ss_symbols
from tests import test_pre_shift_structure as fixture
from tests import test_personal_reviews as delivery

NOW = datetime(2026, 10, 13, 8, 55, tzinfo=ZoneInfo('America/New_York'))


def news(count=1, *, long_title=False, earlier=False):
    events = [ForexNewsEvent('CPI release ' + str(i) + (' detail' * 1000 if long_title else ''),
        'USD', NOW + timedelta(minutes=5 + i * 20)) for i in range(count)]
    return format_pre_shift_news(events, NOW + timedelta(hours=13) if earlier else NOW, shift='day')


class ReminderBudgetTests(unittest.TestCase):
    setUp = fixture.PreShiftStructureTests.setUp
    tearDown = fixture.PreShiftStructureTests.tearDown
    report = fixture.PreShiftStructureTests.report
    save = fixture.PreShiftStructureTests.save
    read = fixture.PreShiftStructureTests.read
    NOW = fixture.PreShiftStructureTests.NOW

    def all_assets(self):
        update_ss_symbols(self.db, 10, 20, {'operation': 'replace', 'symbols': sorted(ASSETS), 'confirmed': True})
        for asset in ASSETS:
            report = self.report(asset)
            self.save(report, next_week_hypothesis='Conditional member analysis ' * 400,
                      hypothesis_invalidation='Invalidation condition ' * 400)
        state = self.read()
        for report in state['reports']:
            report['previous_observed_ohlc'].update(high=108, low=95)
        return state

    def compose(self, state, summary=''):
        plan = {'weekly_structure': state,
                'coaching_profile': {'current_focus': {'reminder': 'EXACT SAVED EXECUTION FOCUS'}}}
        with patch.object(intel, 'get_member_plan', return_value=plan):
            return intel.build_pre_shift_message(self.db, 10, 20, 'day', '2026-10-13', news_summary=summary)

    def assert_essentials(self, message):
        for asset in ASSETS:
            self.assertIn(asset + ': Both sides swept; closure sequence unverified.', message)
        self.assertIn('full-week coverage unverified', message)
        self.assertIn('not a current-week bias', message)
        self.assertIn('Launchpad/structure review pending', message)
        self.assertIn('EXACT SAVED EXECUTION FOCUS', message)
        self.assertIn('other asset hypotheses saved', message)

    def test_all_assets_actual_prior_ranges_are_bounded_without_news(self):
        message = self.compose(self.all_assets())
        self.assertLess(len(message), 1750)
        self.assert_essentials(message)
        self.assertEqual(message.count('Week 1 of October (Oct 5–Oct 9, 2026)'), 1)
        self.assertEqual(message.count('Week 4 of September (Sep 28–Oct 2, 2026)'), 1)

    def test_actual_six_event_news_keeps_time_currency_title_and_focus(self):
        summary = news(7)
        message = self.compose(self.all_assets(), summary)
        self.assertLessEqual(len(message), 1900)
        self.assert_essentials(message)
        self.assertIn('9:00 AM', message)
        self.assertIn('USD — CPI release 0', message)
        self.assertIn('CPI release 5', message)
        self.assertIn('+1 more high-impact event(s) today', message)
        self.assertEqual(message.count('red-folder news today:'), 1)
        self.assertIn('Condition/invalidation:', message)
        self.assertIn('…', message)

    def test_long_titles_preserve_first_event_and_exact_omitted_count(self):
        message = self.compose(self.all_assets(), news(7, long_title=True))
        self.assertLessEqual(len(message), 1900)
        self.assert_essentials(message)
        self.assertIn('9:00 AM ET — USD — CPI release 0', message)
        self.assertIn('+6 more event(s)', message)
        self.assertIn('title shortened', message)
        self.assertIn('https://www.forexfactory.com/calendar', message)
        self.assertEqual(message.count('**') % 2, 0)

    def test_earlier_only_news_stays_earlier_when_shortened(self):
        message = self.compose(self.all_assets(), news(3, long_title=True, earlier=True))
        self.assertLessEqual(len(message), 1900)
        self.assertIn('No upcoming releases. Earlier:', message)
        self.assertIn('9:00 AM ET — USD — CPI release 0', message)
        self.assertIn('+2 more event(s)', message)

    def test_distinct_current_prior_pairs_do_not_share_wrong_dates(self):
        state = self.all_assets()
        for report in state['reports']:
            if report['asset'] == 'BTCUSD':
                report.update(week_start='2026-09-28', previous_week_start='2026-09-21')
            elif report['asset'] == 'ETHUSD':
                report.update(previous_week_start='2026-09-21')
        formatted = ss.format_pre_shift_structure(state)
        self.assertIn('Week 4 of September (Sep 28–Oct 2, 2026) vs Week 3 of September (Sep 21–Sep 25, 2026):\nBTCUSD:', formatted)
        self.assertIn('Week 1 of October (Oct 5–Oct 9, 2026) vs Week 3 of September (Sep 21–Sep 25, 2026):\nETHUSD:', formatted)
        message = self.compose(state, news(7, long_title=True))
        self.assertLessEqual(len(message), 1900)
        self.assert_essentials(message)
        self.assertIn('USD — CPI release 0', message)
        self.assertIn('hypothesis and condition/invalidation: previews omitted', message)

    def test_unavailable_and_member_preview_scope_remain_explicit(self):
        state = self.all_assets()
        state['unavailable_assets'] = ['NAS100']
        message = self.compose(state, news(7))
        self.assertIn('Market facts unavailable: NAS100.', message)
        self.assertIn('Your saved', message)
        self.assertIn('Condition/invalidation:', message)
        self.assertLessEqual(len(message), 1900)

    def test_closure_canon_is_unchanged(self):
        previous = dict(high=100, low=90)
        cases = [(dict(high=110, low=91, close=99), 'Wick above → Lower pricing'),
                 (dict(high=99, low=80, close=91), 'Wick below → Higher pricing'),
                 (dict(high=110, low=95, close=105), 'Close above → Higher pricing'),
                 (dict(high=99, low=80, close=85), 'Close below → Lower pricing'),
                 (dict(high=110, low=80, close=95), 'Both sides swept; closure sequence unverified'),
                 (dict(high=99, low=91, close=95), 'Inside prior range; no confirmed four-way closure')]
        for current, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(ss._gtop_closure(current, previous), expected)
        self.assertEqual(ss._gtop_closure({'close': 95}, None), 'Weekly closure unverified (prior OHLC unavailable)')


class ReminderTransportTests(unittest.IsolatedAsyncioTestCase):
    functions = delivery.ScheduledReviewTests.functions
    delivery_db = delivery.ScheduledReviewTests.delivery_db

    async def test_personalized_composition_has_one_news_section_and_one_receipt(self):
        db, conn = self.delivery_db()
        member = NS(id=20, send=AsyncMock(return_value=NS(id=55)))
        summary = news(7)
        reports = [dict(asset=asset, week_start='2026-10-05', previous_week_start='2026-09-28',
            observed_ohlc=dict(high=110, low=90, close=105), previous_observed_ohlc=dict(high=108, low=95),
            member_structure=dict(next_week_hypothesis='condition ' * 200, hypothesis_invalidation='invalid ' * 200))
            for asset in sorted(ASSETS)]
        plan = dict(weekly_structure=dict(reports=reports, symbol_selection={'symbols': sorted(ASSETS)}),
                    coaching_profile={'current_focus': {'reminder': 'EXACT SAVED EXECUTION FOCUS'}})
        with patch.object(intel, 'get_member_plan', return_value=plan):
            env = self.functions({'_broadcast_gbop_dm'}, db=db, now=lambda: 'now',
                _gbop_role_members=lambda: [member], _authorized_scheduled_member=AsyncMock(return_value=member),
                build_pre_shift_message=intel.build_pre_shift_message, append_shift_news=intel.append_shift_news,
                discord=NS(Forbidden=ValueError, HTTPException=OSError))
            args = dict(event_key='key', personalized_shift='day', shift_date='2026-10-13', news_summary=summary)
            self.assertEqual(await env['_broadcast_gbop_dm']('fallback', **args), (1, 0))
            self.assertEqual(await env['_broadcast_gbop_dm']('fallback', **args), (0, 0))
        member.send.assert_awaited_once()
        message = member.send.await_args.args[0]
        self.assertLessEqual(len(message), 1900)
        self.assertEqual(message.count('red-folder news today:'), 1)
        self.assertIn('USD — CPI release 0', message)
        for asset in ASSETS:
            self.assertIn(asset + ':', message)
        self.assertIn('EXACT SAVED EXECUTION FOCUS', message)
        self.assertEqual(conn.execute('SELECT count(*) c FROM gbop_shift_deliveries').fetchone()['c'], 1)

    async def test_builder_failure_fallback_keeps_news_once(self):
        db, conn = self.delivery_db()
        member = NS(id=20, send=AsyncMock(return_value=NS(id=55)))
        env = self.functions({'_broadcast_gbop_dm'}, db=db, now=lambda: 'now',
            _gbop_role_members=lambda: [member], _authorized_scheduled_member=AsyncMock(return_value=member),
            build_pre_shift_message=Mock(side_effect=RuntimeError('unavailable')),
            append_shift_news=intel.append_shift_news, discord=NS(Forbidden=ValueError, HTTPException=OSError))
        self.assertEqual(await env['_broadcast_gbop_dm']('Fallback execution focus.', event_key='key',
            personalized_shift='day', shift_date='2026-10-13', news_summary=news(7, long_title=True)), (1, 0))
        message = member.send.await_args.args[0]
        self.assertLessEqual(len(message), 1900)
        self.assertTrue(message.startswith('Fallback execution focus.'))
        self.assertIn('USD — CPI release 0', message)
        self.assertEqual(message.count('Red-folder news today:'), 1)
        self.assertEqual(conn.execute('SELECT count(*) c FROM gbop_shift_deliveries').fetchone()['c'], 1)
