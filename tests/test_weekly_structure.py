import contextlib
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import weekly_structure as ss
from gbop_voice_web import member_intelligence as intel
from gbop_voice_web.journal_context import JournalBinding
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from tests.test_member_intelligence import IntelligenceTests


def epoch(value):
    return int(datetime.fromisoformat(value).timestamp())


def bar(t, high=110, low=90):
    return dict(time=t, open=100, high=high, low=low, close=102)


class WeeklyFactsTests(unittest.TestCase):
    def setUp(self):
        self.window = ss.week_window('2026-09-28')
        self.start = self.window['start_epoch']
        self.end = self.window['end_epoch']

    def test_week_window_and_dst(self):
        self.assertEqual(self.window['start_ny'], '2026-09-27T17:00:00-04:00')
        self.assertEqual(self.window['end_ny'], '2026-10-02T17:00:00-04:00')
        winter = ss.week_window('2026-11-02')
        self.assertEqual(winter['start_ny'], '2026-11-01T17:00:00-05:00')
        self.assertEqual(winter['end_ny'], '2026-11-06T17:00:00-05:00')
        with self.assertRaises(ValueError): ss.week_window('2026-10-02')
        before = epoch('2026-10-02T16:59:59-04:00')
        self.assertEqual(ss.week_window(now=before)['week_start'], '2026-09-21')
        self.assertEqual(ss.week_window(now=before+1)['week_start'], '2026-09-28')

    def test_ties_keep_candle_intervals_not_tick_times(self):
        facts = ss.summarize_week({60: [bar(self.start), bar(self.start+60), bar(self.start+120, high=109)]}, self.window)
        self.assertEqual(facts['high']['tie_count'], 2)
        self.assertEqual(len(facts['high']['occurrences']), 2)
        self.assertIn('not an exact tick', facts['high']['time_precision'])
        self.assertFalse(facts['high']['definitive_weekly_extreme'])
        self.assertFalse(facts['coverage']['nominal_final_bar_present'])

    def test_sparse_fine_data_cannot_replace_broader_coarse_week(self):
        facts = ss.summarize_week({60: [bar(self.start, high=105)],
                                  300: [bar(self.start), bar(self.start+300, high=115)]}, self.window)
        self.assertEqual(facts['coverage']['source_timeframe'], 'M5')
        self.assertEqual(facts['high']['price'], 115)

    def test_fine_coverage_wins_at_equal_duration(self):
        facts = ss.summarize_week({60: [bar(self.start+n*60) for n in range(5)],
                                  300: [bar(self.start)]}, self.window)
        self.assertEqual(facts['coverage']['source_timeframe'], 'M1')

    def test_no_bars_and_partial_last_bar_are_unknown(self):
        facts = ss.summarize_week({300: [bar(self.end)]}, self.window)
        self.assertEqual(facts['coverage']['status'], 'unavailable')
        self.assertIsNone(facts['observed_ohlc'])
        facts = ss.summarize_week({300: [bar(self.end-300)]}, self.window)
        self.assertTrue(facts['coverage']['nominal_final_bar_present'])
        self.assertFalse(facts['coverage']['full_broker_week_verified'])
        self.assertIn('not automatically missing', facts['coverage']['limit'])

    def test_many_ties_and_gaps_are_bounded_but_counted(self):
        facts = ss.summarize_week({60: [bar(self.start+n*120) for n in range(50)]}, self.window)
        self.assertEqual(facts['high']['tie_count'], 50)
        self.assertEqual(facts['high']['occurrences_omitted'], 38)
        self.assertEqual(len(facts['coverage']['unobserved_intervals']), 24)
        self.assertGreater(facts['coverage']['intervals_omitted'], 0)

    def test_seven_sources_and_crypto_pending_no_fabrication(self):
        from gbop_voice_web.market_data import ASSETS
        with patch('gbop_voice_web.market_data.read_feed', return_value={'ok': False}):
            for asset in ASSETS:
                report = ss.build_report(None, asset, '2026-09-28', now=self.end+3600)
                if asset in ss.CRYPTO:
                    self.assertEqual(report['status'], 'source_weekly_boundary_unavailable')
                else:
                    self.assertTrue(report['ok'])
                    self.assertIsNone(report['high'])
                    self.assertEqual(report['coverage']['status'], 'unavailable')
                    self.assertEqual(report['human_structure']['high_launchpad'], None)

    def test_report_version_stable_until_market_facts_change(self):
        feed = {'ok': True, 'asset': 'XAUUSD', 'symbol': 'XAUUSDm', 'bars': [], 'bars_m1': []}
        with patch('gbop_voice_web.market_data.read_feed', return_value=feed), patch('gbop_voice_web.market_data._history_sets', return_value={300:[bar(self.start)]}):
            one = ss.build_report(None, 'XAUUSD', '2026-09-28', now=self.end+3600)
            two = ss.build_report(None, 'XAUUSD', '2026-09-28', now=self.end+7200)
        self.assertEqual(one['report_version'], two['report_version'])
        with patch('gbop_voice_web.market_data.read_feed', return_value=feed), patch('gbop_voice_web.market_data._history_sets', return_value={300:[bar(self.start, high=111)]}):
            three = ss.build_report(None, 'XAUUSD', '2026-09-28', now=self.end+7200)
        self.assertNotEqual(one['report_version'], three['report_version'])

    def test_scheduler_one_asset_each_tick_after_friday_hour(self):
        checked = {}
        with patch.object(ss, 'build_report', return_value={'ok': True, 'report_version': 'v1'}) as build, patch.object(ss, 'persist_report', side_effect=lambda db,r:r):
            # Noncrypto waits for Friday's one-hour buffer; crypto has its own source window.
            checked.update({(asset,'source_w1'):self.end for asset in ss.CRYPTO})
            self.assertIsNone(ss.prepare_next_weekly(None, checked, now=self.end+3599))
            checked.clear()
            assets = [ss.prepare_next_weekly(None, checked, now=self.end+3600+n*30)['asset'] for n in range(9)]
            self.assertEqual(len(set(assets)), 9)
            self.assertIsNone(ss.prepare_next_weekly(None, checked, now=self.end+4000))
            self.assertEqual(build.call_count, 9)

    def test_crypto_boundaries_are_source_opens_not_guessed_calendar(self):
        start = epoch('2026-09-28T02:00:00+00:00')
        end = epoch('2026-10-05T02:00:00+00:00')
        periods = [{'open_time':start,'close_time':end,'source':'MT5','timeframe':'W1'}]
        window = ss.source_week_window(periods, now=end+60)
        self.assertEqual(window['start_epoch'],start)
        self.assertEqual(window['end_epoch'],end)
        self.assertEqual(window['start_ny'],'2026-09-27T22:00:00-04:00')
        self.assertTrue(window['source_boundary_verified'])
        self.assertIsNone(ss.source_week_window(periods, now=end-1))
        self.assertIsNone(ss.source_week_window(periods,'2026-09-21',now=end+60))
        self.assertIsNone(ss.source_week_window([{**periods[0],'close_time':end+7*86400}],now=end+8*86400))

    def test_crypto_source_week_dst_is_display_conversion_only(self):
        start = epoch('2026-10-26T00:00:00+00:00')
        end = epoch('2026-11-02T00:00:00+00:00')
        window = ss.source_week_window([{'open_time':start,'close_time':end,'source':'MT5','timeframe':'W1'}],now=end+60)
        self.assertEqual(window['end_epoch']-window['start_epoch'],7*86400)
        self.assertTrue(window['start_ny'].endswith('-04:00'))
        self.assertTrue(window['end_ny'].endswith('-05:00'))

    def test_crypto_report_uses_actual_window_and_previous_source_only(self):
        start = epoch('2026-09-28T02:00:00+00:00')
        end = start+7*86400
        feed = {'ok':True,'asset':'BTCUSD','symbol':'BTCUSDm','weekly_periods':[
            {'open_time':start,'close_time':end,'source':'MT5','timeframe':'W1'}]}
        with patch('gbop_voice_web.market_data.read_feed',return_value=feed), patch('gbop_voice_web.market_data._history_sets',return_value={60:[bar(start)]}):
            report = ss.build_report(None,'BTCUSD',now=end+3600)
        self.assertTrue(report['ok'])
        self.assertEqual(report['window']['start_epoch'],start)
        self.assertEqual(report['window']['end_epoch'],end)
        self.assertIsNone(report['closure_vs_previous'])
        self.assertFalse(report['coverage']['nominal_final_bar_present'])

    def test_canonical_questions_and_read_only_tool_catalogue(self):
        canon = Path('gbop_voice_web/gtop_knowledge.txt').read_text()
        for _, question in ss.QUESTIONS:
            self.assertIn(question, canon)
        tools = contextual_tools(intel.INTELLIGENCE_TOOLS)
        study = next(t for t in tools if t['name'] == 'get_weekly_structure_study')
        save = next(t for t in tools if t['name'] == 'save_ss_review')
        self.assertIn('report_version', study['parameters']['properties'])
        self.assertNotIn('market_reference', save['parameters']['properties'])


class WeeklyContributionTests(IntelligenceTests):
    def setUp(self):
        super().setUp()
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS gbop_ss_reports(asset TEXT,week_start TEXT,report_version TEXT,revision INTEGER,generated_at TEXT,payload TEXT,
                PRIMARY KEY(asset,week_start,report_version));
            CREATE TABLE IF NOT EXISTS gbop_ss_contributions(guild_id INTEGER,user_id INTEGER,asset TEXT,week_start TEXT,
                report_version TEXT,revision INTEGER,answers TEXT,created_at TEXT,
                PRIMARY KEY(guild_id,user_id,asset,week_start,report_version,revision));
        ''')
        with patch('gbop_voice_web.market_data.read_feed', return_value={'ok': False}):
            self.report = ss.build_report(self.db,'XAUUSD','2026-09-28',now=epoch('2026-10-02T18:00:00-04:00'))
        ss.persist_report(self.db,self.report)
        self.args = {'week_start':'2026-09-28','asset':'XAUUSD','report_version':self.report['report_version']}

    def test_versions_and_scoped_contribution_corrections(self):
        intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':True})
        intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':True})
        intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':False})
        own = ss.get_weekly_structure_study(self.db,10,20,self.args)
        self.assertEqual(len(own['member_contributions']),2)
        self.assertFalse(own['member_contributions'][0]['answers']['answers']['over_leverage'])
        other = ss.get_weekly_structure_study(self.db,10,21,self.args)
        self.assertEqual(other['member_contributions'],[])
        self.assertEqual(ss.get_weekly_structure_study(self.db,11,20,self.args)['member_contributions'],[])

    def test_blank_not_saved_or_completed_and_bool_not_inferred(self):
        result = intel.save_ss_review(self.db,10,20,{**self.args,'high_launchpad':'   '})
        self.assertFalse(result['saved'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbop_ss_weekly_reviews').fetchone()[0],0)
        with self.assertRaises(ValueError): intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':'no'})

    def test_unversioned_save_is_rejected(self):
        with self.assertRaises(ValueError):
            intel.save_ss_review(self.db,10,20,{'asset':'BTCUSD','week_start':'2026-10-04','over_leverage':False})

    def test_new_report_version_does_not_inherit_old_answers(self):
        intel.save_ss_review(self.db,10,20,{**self.args,'high_launchpad':'V1 only','over_leverage':True})
        revised = dict(self.report, facts_hash='different', report_version='v2')
        ss.persist_report(self.db,revised)
        intel.save_ss_review(self.db,10,20,{**self.args,'report_version':'v2','boredom_trades':False})
        contribution = ss.get_weekly_structure_study(self.db,10,20,{**self.args,'report_version':'v2'})['member_contributions'][0]
        self.assertEqual(contribution['answers']['answers'],{'boredom_trades':False})

    def test_new_version_save_and_read_do_not_claim_old_completion(self):
        complete = {**self.complete_ss_args(), **self.args}
        self.assertTrue(intel.save_ss_review(self.db,10,20,complete)['review']['structure_complete'])
        ss.persist_report(self.db,dict(self.report,facts_hash='different',report_version='v2'))
        args = {**self.args,'report_version':'v2'}
        saved = intel.save_ss_review(self.db,10,20,{**args,'boredom_trades':False})
        self.assertEqual(saved['review_scope'],'exact_report_version')
        self.assertFalse(saved['review']['structure_complete'])
        self.assertFalse(saved['review']['execution_review_complete'])
        self.assertNotIn('structural_summary',saved['review'])
        self.assertNotEqual(saved['next_step'],'SS review is complete.')
        read = intel.get_ss_review(self.db,10,20,args)
        self.assertEqual(read['review'],saved['review'])
        latest = intel.get_ss_review(self.db,10,20,{'asset':'XAUUSD','week_start':'2026-09-28'})
        self.assertEqual(latest['review_scope'],'latest_asset_week_view')
        self.assertIn('earlier report versions',latest['version_scope_warning'])

    def test_a_b_a_market_correction_keeps_latest_order(self):
        b = dict(self.report, facts_hash='different', report_version='v2', high={'price':111})
        ss.persist_report(self.db,b)
        returned = ss.persist_report(self.db,self.report)
        self.assertIn('-r', returned['report_version'])
        with self.db() as conn:
            latest = ss.read_report(conn,'XAUUSD','2026-09-28')
        self.assertIsNone(latest['high'])
        self.assertEqual(latest['report_version'],returned['report_version'])

    def test_wrong_version_and_revoked_identity_never_write(self):
        with self.assertRaises(ValueError): intel.save_ss_review(self.db,10,20,{**self.args,'report_version':'wrong','over_leverage':False})
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        with self.assertRaises(ValueError): intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':False})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbop_ss_contributions').fetchone()[0],0)

    def test_stale_conversation_never_writes(self):
        context = MarketConversation((10,20,'test'))
        generation = context.begin_turn('Save this SS answer')
        binding = JournalBinding(context,generation)
        context.begin_turn('cancel')
        with self.assertRaises(ValueError): intel.save_ss_review(self.db,10,20,{**self.args,'over_leverage':False,'_journal_binding':binding})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM gbop_ss_contributions').fetchone()[0],0)

    def test_reports_immutable_and_all_asset_catalogue_includes_unknowns(self):
        changed = dict(self.report, high={'price':999})
        ss.persist_report(self.db,changed)
        with self.db() as conn:
            stored = ss.read_report(conn,'XAUUSD','2026-09-28',self.report['report_version'])
        self.assertIsNone(stored['high'])
        index = ss.get_weekly_structure_study(self.db,10,20,{'week_start':'2026-09-28'})
        self.assertEqual(len(index['assets']),9)
        self.assertEqual(sum(x['status']=='not_prepared' for x in index['assets']),8)

    def test_migration_default_deny_and_append_only_backend(self):
        sql = next(Path('supabase/migrations').glob('*weekly_structure_reports.sql')).read_text()
        self.assertEqual(sql.count('ENABLE ROW LEVEL SECURITY'),2)
        self.assertEqual(sql.count('FROM PUBLIC, anon, authenticated, service_role'),2)
        self.assertEqual(sql.count('GRANT SELECT, INSERT'),2)
        self.assertNotIn('CREATE POLICY',sql)
        self.assertIn('FOREIGN KEY (asset,week_start,report_version)',sql)
        self.assertNotIn('ON DELETE CASCADE',sql)
