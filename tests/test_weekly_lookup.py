"""Saved SS lookup contracts; synthetic SQLite fixtures, no live services."""
import contextlib
import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import member_intelligence as intel
from gbop_voice_web import weekly_structure as ss
from gbop_voice_web.market_data import asset_name
from gbop_voice_web.market_scope_log import market_scope_log
from gbop_voice_web.voice_payload import voice_tool_payload


def epoch(value):
    return int(datetime.fromisoformat(value).timestamp())


class WeeklyLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = epoch('2026-09-27T00:00:00+00:00')
        end = epoch('2026-10-04T00:00:00+00:00')
        bars = [dict(time=t, open=85000, high=86000, low=84000, close=85001)
                for t in range(start, end, 60)]
        bars[(epoch('2026-10-02T08:31:00-04:00')-start)//60]['high'] = 87261.14
        bars[(epoch('2026-09-28T10:53:00-04:00')-start)//60]['low'] = 82512.13
        feed = {'ok': True, 'symbol': 'BTCUSDm', 'weekly_periods': [
            {'open_time': start, 'close_time': end, 'source': 'MT5', 'timeframe': 'W1'}]}
        with patch('gbop_voice_web.market_data.read_feed', return_value=feed), \
                patch('gbop_voice_web.market_data._history_sets', return_value={60: bars}):
            cls.fixture = ss.build_report(None, 'BTCUSD', now=end+3600)
        cls.fixture['report_revision'] = 1

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE gbop_ss_reports(asset TEXT,week_start TEXT,report_version TEXT,
                revision INTEGER,generated_at TEXT,payload TEXT);
            CREATE TABLE gbop_ss_contributions(guild_id INTEGER,user_id INTEGER,asset TEXT,
                week_start TEXT,report_version TEXT,revision INTEGER,answers TEXT,created_at TEXT);
        ''')
        self.insert(self.fixture)
        self.conn.commit()
        @contextlib.contextmanager
        def db():
            yield self.conn
        self.db = db
        self.now = epoch('2026-10-05T12:18:00+00:00')

    def insert(self, report):
        self.conn.execute('INSERT INTO gbop_ss_reports VALUES (?,?,?,?,?,?)', (
            report['asset'], report['week_start'], report['report_version'],
            report['report_revision'], report['generated_at_utc'], json.dumps(report)))

    def lookup(self, **args):
        with patch.object(ss.clock, 'time', return_value=self.now):
            return intel.intelligence_tool(self.db, 10, 20, 'get_weekly_structure_study', args)

    def test_bitcoin_and_ethereum_aliases_are_case_and_space_insensitive(self):
        for canonical, aliases in [('BTCUSD', ('Bitcoin', ' bitcoin ', 'BTC', 'btcusd')),
                                   ('ETHUSD', ('Ethereum', ' ethereum ', 'ETH', 'ethusd'))]:
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(asset_name(alias), canonical)
        for bad in ('BitcoinCash', 'BTCUSD/private', 'anything', None):
            with self.assertRaises(ValueError):
                asset_name(bad)

    def test_latest_null_scope_retrieves_the_same_saved_bitcoin_report(self):
        for alias in ('Bitcoin', 'BTC', 'BTCUSD'):
            with self.subTest(alias=alias):
                result = self.lookup(asset=alias, week_start=None, report_version=None)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['asset'], 'BTCUSD')
                self.assertEqual(result['week_start'], '2026-09-28')
                self.assertEqual(result['report_version'], self.fixture['report_version'])

    def test_omitted_scope_matches_null_scope(self):
        self.assertEqual(self.lookup(asset='Bitcoin'),
                         self.lookup(asset='Bitcoin', week_start=None, report_version=None))

    def test_crypto_latest_saved_week_is_not_a_guessed_calendar_week(self):
        self.now = epoch('2026-10-16T20:00:00+00:00')
        result = self.lookup(asset='Bitcoin', week_start=None, report_version=None)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['week_start'], '2026-09-28')
        self.assertEqual(result['window']['start_utc'], '2026-09-27T00:00:00+00:00')

    def test_latest_revision_and_exact_previous_revision_stay_distinct(self):
        revised = copy.deepcopy(self.fixture)
        revised.update(report_revision=2, report_version=self.fixture['report_version']+'-r2')
        self.insert(revised)
        self.assertEqual(self.lookup(asset='BTC')['report_version'], revised['report_version'])
        exact = self.lookup(asset='BTC', week_start='2026-09-28',
                            report_version=self.fixture['report_version'])
        self.assertEqual(exact['report_version'], self.fixture['report_version'])

    def test_explicit_missing_week_never_substitutes_latest(self):
        for version in (None, self.fixture['report_version']):
            result = self.lookup(asset='Bitcoin', week_start='2026-09-21', report_version=version)
            self.assertFalse(result['ok'])
            self.assertEqual(result['week_start'], '2026-09-21')
            self.assertEqual(result['status'], 'report_not_prepared')
            self.assertFalse(result['saved_report_for_week'])
            self.assertNotIn('high', result)

    def test_explicit_missing_version_never_substitutes_or_blames_boundaries(self):
        for version in ('ss-weekly-v1-'+'0'*20, 'latest', '', '   '):
            result = self.lookup(asset='Bitcoin', week_start='2026-09-28', report_version=version)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 'report_version_not_found')
            self.assertTrue(result['saved_report_for_week'])
            self.assertNotIn('high', result)

    def test_missing_version_with_null_week_remains_exact_version(self):
        result = self.lookup(asset='BTC', week_start=None, report_version='ss-weekly-v1-'+'0'*20)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'report_version_not_found')

    def test_invalid_explicit_week_is_rejected_without_fallback(self):
        for week in ('2026-09-27', 'latest', '2026-99-99'):
            result = self.lookup(asset='BTC', week_start=week, report_version=None)
            self.assertFalse(result['ok'])
            self.assertIn('error', result)
            self.assertNotIn('high', result)

    def test_missing_crypto_report_does_not_claim_missing_source_boundaries(self):
        result = self.lookup(asset='Ethereum', week_start=None, report_version=None)
        self.assertEqual(result['asset'], 'ETHUSD')
        self.assertEqual(result['status'], 'report_not_prepared')
        self.assertFalse(result['saved_report_for_week'])
        self.assertNotIn('source_weekly_boundary_unavailable', json.dumps(result))

    def test_catalogue_keeps_crypto_unknown_and_explicit_version_exact(self):
        result = self.lookup(asset=None, week_start=None, report_version=None)
        by_asset = {r['asset']: r for r in result['assets']}
        self.assertEqual(by_asset['BTCUSD']['report_version'], self.fixture['report_version'])
        self.assertEqual(by_asset['ETHUSD']['status'], 'not_prepared')
        exact = self.lookup(asset=None, week_start='2026-09-28', report_version='not-a-saved-version')
        self.assertTrue(all(r['report_version'] is None for r in exact['assets']))
        self.assertEqual(next(r for r in exact['assets'] if r['asset'] == 'BTCUSD')['status'], 'report_version_not_found')

    def test_read_only_lookup_never_prepares_saves_or_sends(self):
        self.conn.execute('PRAGMA query_only=ON')
        with patch.object(ss, 'build_report', side_effect=AssertionError('must not prepare')), \
                patch.object(ss, 'persist_report', side_effect=AssertionError('must not save')), \
                patch.object(intel, 'save_ss_review', side_effect=AssertionError('must not save answers')), \
                patch('gbop_voice_web.journal_recall.send_history', side_effect=AssertionError('must not send')), \
                patch('gbop_voice_web.photo_recall.send_photos', side_effect=AssertionError('must not send')), \
                patch('gbop_voice_web.trade_photos.send_photos', side_effect=AssertionError('must not send')):
            result = self.lookup(asset='Bitcoin', week_start=None, report_version=None)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_ss_contributions').fetchone()[0], 0)

    def test_voice_payload_preserves_exact_source_windows_extremes_and_limits(self):
        result = self.lookup(asset='Bitcoin', week_start=None, report_version=None)
        voice = voice_tool_payload('get_weekly_structure_study', result)
        for key in ('window', 'high', 'low', 'coverage', 'report_version'):
            self.assertEqual(voice[key], self.fixture[key])
        self.assertEqual(voice['high']['occurrences'][0]['bar_open_ny'], '2026-10-02T08:31:00-04:00')
        self.assertEqual(voice['low']['occurrences'][0]['bar_close_ny'], '2026-09-28T10:54:00-04:00')
        self.assertTrue(voice['window']['source_boundary_verified'])
        self.assertEqual(voice['coverage']['bar_count'], 10080)
        self.assertFalse(voice['coverage']['full_broker_week_verified'])

    def test_prompt_and_field_descriptions_require_latest_nulls(self):
        tool = next(t for t in intel.INTELLIGENCE_TOOLS if t['name'] == 'get_weekly_structure_study')
        for text in (intel.INTELLIGENCE_PROMPT, tool['description']):
            self.assertIn('week_start=null', text)
            self.assertIn('report_version=null', text)
        properties = tool['parameters']['properties']
        self.assertIn('null for latest completed', properties['week_start']['description'])
        self.assertIn('Never use the string latest', properties['report_version']['description'])
        self.assertEqual(properties['week_start']['type'], ['string', 'null'])
        self.assertIn('Preserve an', intel.INTELLIGENCE_PROMPT)


class WeeklyScopeLogTests(unittest.TestCase):
    def test_success_logs_only_requested_and_resolved_scope(self):
        version = 'ss-weekly-v1-'+'a'*20
        result = {'ok': True, 'asset': 'BTCUSD', 'week_start': '2026-09-28',
                  'report_version': version, 'member_contributions': [{'answers': 'PRIVATE'}]}
        log = market_scope_log('get_weekly_structure_study',
              {'asset': 'Bitcoin', 'week_start': None, 'report_version': None}, result, result)
        self.assertEqual(log['requested'], {'asset': 'BTCUSD'})
        self.assertEqual(log['resolved'], {'asset': 'BTCUSD', 'week_start': '2026-09-28', 'report_version': version})
        self.assertEqual(log['lookup_mode'], 'latest_completed')
        self.assertEqual(log['status'], 'prepared')
        self.assertTrue(log['voice_ok'])
        self.assertNotIn('PRIVATE', json.dumps(log))

    def test_exact_miss_logs_searched_scope_without_a_resolved_report(self):
        args = {'asset': 'BTCUSD', 'week_start': '2026-09-21', 'report_version': 'ss-weekly-v1-'+'b'*20+'-r2'}
        result = {'ok': False, 'asset': 'BTCUSD', 'week_start': args['week_start'], 'status': 'report_not_prepared'}
        log = market_scope_log('get_weekly_structure_study', args, result)
        self.assertEqual(log['lookup_mode'], 'exact_scope')
        self.assertEqual(log['requested'], args)
        self.assertEqual(log['searched'], {'asset': 'BTCUSD', 'week_start': '2026-09-21'})
        self.assertNotIn('resolved', log)
        self.assertFalse(log['ok'])

    def test_private_and_malformed_input_never_enters_diagnostics(self):
        private = 'PRIVATE REFLECTION '*1000
        args = {'asset': private, 'week_start': private, 'report_version': private,
                'answers': private, 'transcript': private, 'user_id': 123}
        result = {**args, 'ok': False, 'error': private, 'status': private,
                  'member_contributions': private, 'payload': private}
        log = market_scope_log('get_weekly_structure_study', args, result)
        encoded = json.dumps(log)
        self.assertLess(len(encoded), 600)
        self.assertNotIn('PRIVATE', encoded)
        self.assertEqual(log['requested'], {'asset_status': 'invalid',
              'week_start_status': 'invalid', 'report_version_status': 'invalid'})
        self.assertEqual(log['status'], 'lookup_failed')
        for key in ('error', 'answers', 'transcript', 'user_id', 'payload', 'member_contributions'):
            self.assertNotIn(key, log)

    def test_wrong_types_dates_and_latest_literal_are_redacted(self):
        for value in ([], {}, 123, True, '2026-09-27', '2026-99-99', 'latest'):
            log = market_scope_log('get_weekly_structure_study',
                  {'asset': value, 'week_start': value, 'report_version': value}, {'ok': False})
            self.assertEqual(log['requested'], {'asset_status': 'invalid',
                  'week_start_status': 'invalid', 'report_version_status': 'invalid'})

    def test_both_runtime_paths_already_call_shared_logger(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('bot.py', 'gbop_voice_web/server.py'):
            self.assertIn('market_scope_log(', (root/filename).read_text())


if __name__ == '__main__':
    unittest.main()
