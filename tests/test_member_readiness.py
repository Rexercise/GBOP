import os
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from gbop_voice_web.smt_review import compare_smt, aligned, NY
from gbop_voice_web.journal_recall import get_history, journal_tool, journal_text, send_history, configure_journal_tools


class SMTTests(unittest.TestCase):
    def setUp(self):
        self.start = int(datetime(2026, 10, 2, 8, tzinfo=NY).timestamp())
        self.end = self.start + 4 * 3600
        self.gold = [dict(time=t, open=95, high=99, low=91, close=95)
                     for t in range(self.start, self.end, 60)]
        self.silver = [dict(time=t, open=9.5, high=9.9, low=9.1, close=9.5)
                       for t in range(self.start, self.end, 60)]
        self.gold[0].update(high=100, low=90)
        self.silver[0].update(high=10, low=9)

    def review(self, **kw):
        return compare_smt(self.gold, self.silver, self.start, self.end, **kw)

    def test_bearish_smt_survives_later_invalidations(self):
        self.silver[62]['high'] = 10.1
        self.gold[120].update(open=89, high=90, low=88, close=89)
        result = self.review()
        event = result['events'][0]
        self.assertEqual(result['status'], 'observed_divergence')
        self.assertEqual(event['direction'], 'bearish')
        self.assertEqual(event['sweeping_asset'], 'XAGUSD')
        self.assertEqual(event['nonconfirming_asset'], 'XAUUSD')
        self.assertIn('09:02:00', event['start_ny'])
        self.assertFalse(result['entry_confirmed'])

    def test_later_catchup_does_not_delete_event(self):
        self.silver[62]['high'] = 10.1
        self.gold[66]['high'] = 101
        result = self.review()
        self.assertIn('09:06:00', result['events'][0]['later_both_breached_at_ny'])
        self.assertEqual(result['status'], 'observed_divergence')

    def test_matching_highs_same_bar_not_invented_divergence(self):
        self.silver[62]['high'] = 10.1
        self.gold[62]['high'] = 101
        self.assertEqual(self.review()['status'], 'no_divergence_observed')

    def test_missing_peer_anchor_is_insufficient_not_false(self):
        del self.silver[10]
        self.assertEqual(self.review()['status'], 'insufficient_paired_data')

    def test_gap_before_signal_prevents_confirmation(self):
        self.silver[62]['high'] = 10.1
        del self.gold[61]
        self.assertEqual(self.review()['status'], 'insufficient_paired_data')

    def test_gap_after_signal_preserves_prior_fact(self):
        self.silver[62]['high'] = 10.1
        del self.gold[65]
        result = self.review()
        self.assertEqual(result['status'], 'observed_divergence')
        self.assertFalse(result['paired_9_oclock_complete'])

    def test_equal_boundary_not_a_sweep(self):
        self.silver[62]['high'] = 10
        self.assertEqual(self.review()['status'], 'no_divergence_observed')

    def test_bullish_case(self):
        self.gold[62]['low'] = 89
        self.assertEqual(self.review()['events'][0]['direction'], 'bullish')

    def test_after_ten_not_called_nine_ate_eight(self):
        self.silver[122]['high'] = 10.1
        self.assertEqual(self.review()['status'], 'no_divergence_observed')

    def test_coarse_source_preserves_interval_precision(self):
        self.silver[62]['high'] = 10.1
        coarse = list(aligned(self.silver, self.start, self.end, 60, 300).values())
        result = compare_smt(self.gold, coarse, self.start, self.end, second_step=300)
        self.assertEqual(result['source_resolution_seconds'], 300)
        self.assertIn('09:00:00', result['events'][0]['start_ny'])
        self.assertIn('09:05:00', result['events'][0]['end_ny'])

    def test_same_bar_objective_has_unknown_order(self):
        self.silver[62].update(high=10.1, low=8.9)
        event = self.review()['events'][0]
        touches = [t for t in event['objective_touches'] if t['asset'] == 'XAGUSD']
        self.assertTrue(touches)
        self.assertTrue(all(t['sequence'] == 'same_bar_order_unknown' for t in touches))

    def test_night_shift_anchor(self):
        offset = 12 * 3600
        for bar in self.gold + self.silver:
            bar['time'] += offset
        self.silver[62]['high'] = 10.1
        result = compare_smt(self.gold, self.silver, self.start + offset, self.end + offset)
        self.assertIn('21:02:00', result['events'][0]['start_ny'])


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.db = lambda: self.conn
        self.conn.executescript('''
            CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,leadership_ack INTEGER,revoked INTEGER);
            CREATE TABLE theses(id INTEGER,guild_id INTEGER,user_id INTEGER,asset TEXT,direction TEXT,play TEXT,status TEXT);
            CREATE TABLE journals(id INTEGER,guild_id INTEGER,user_id INTEGER,thesis_id INTEGER,description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT);
            CREATE TABLE trade_photos(id TEXT,guild_id INTEGER,user_id INTEGER,thesis_id INTEGER);
            CREATE TABLE journal_details(journal_id INTEGER,guild_id INTEGER,user_id INTEGER,photo_id TEXT);
            INSERT INTO members VALUES (1,10,1,1,0),(1,20,1,1,0),(2,10,1,1,0);
            INSERT INTO theses VALUES (20,1,10,'GOLD','Bearish','9ate8','CLOSED'),(21,1,20,'BTC','Bullish','PRIVATE','OPEN'),(22,2,10,'SILVER','Bullish','OTHER GUILD','OPEN');
            INSERT INTO journals VALUES (30,1,10,20,'Own closed trade','Yes',2,'Own note','2026-10-02'),(31,1,20,21,'OTHER MEMBER SECRET','Yes',3,'Secret','2026-10-02'),(32,2,10,22,'OTHER GUILD SECRET','Yes',4,'Secret','2026-10-02'),(33,1,10,NULL,'Own handwritten entry','',NULL,'','2026-10-03');
            INSERT INTO trade_photos VALUES ('own-trade',1,10,20),('own-page',1,10,NULL),('foreign',1,20,20);
            INSERT INTO journal_details VALUES (33,1,10,'own-page'),(33,1,20,'foreign');
        ''')

    def tearDown(self):
        self.conn.close()

    def test_history_counts_scope_and_closed_trade(self):
        result = get_history(self.db, 1, 10, {'limit': 10})
        self.assertEqual(result['total_journals'], 2)
        self.assertEqual(result['total_trades'], 1)
        self.assertEqual([r['journal_number'] for r in result['journals']], [2, 1])
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(result['journals'][1]['trade_id'], 1)
        self.assertEqual([r['photo_count'] for r in result['journals']], [1, 1])

    def test_pagination(self):
        first = get_history(self.db, 1, 10, {'limit': 1})
        second = get_history(self.db, 1, 10, {'limit': 1, 'offset': first['next_offset']})
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['journals'][0]['journal_id'], second['journals'][0]['journal_id'])

    def test_empty_is_success_not_access_error(self):
        result = get_history(self.db, 1, 999, {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'empty')

    def test_revoked_member_denied(self):
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=1 AND user_id=10')
        result = journal_tool(self.db, 1, 10, 999, 'get_journal_history', {})
        self.assertEqual(result['status'], 'access_denied')
        self.assertNotIn('journals', result)

    def test_owner_inactive_flag_is_not_a_retrieval_failure(self):
        self.conn.execute('UPDATE members SET activated=0 WHERE guild_id=1 AND user_id=10')
        result = journal_tool(self.db, 1, 10, 10, 'get_journal_history', {})
        self.assertEqual(result['total_journals'], 2)

    def test_storage_error_is_not_empty(self):
        def broken():
            raise RuntimeError('private db error')
        result = journal_tool(broken, 1, 10, 10, 'get_journal_history', {})
        self.assertEqual(result['status'], 'storage_unavailable')
        self.assertNotIn('private db error', str(result))

    def test_null_result_preserved_and_mentions_disabled(self):
        result = get_history(self.db, 1, 10, {})
        result['journals'][0]['summary'] = '@everyone not an instruction'
        text = journal_text(result)
        self.assertIn('Not recorded', text)
        self.assertNotIn('@everyone', text)

    def test_tool_configuration_is_idempotent_and_no_recipient(self):
        tools = [{'name': 'get_journal_history', 'parameters': {'properties': {'limit': {}}, 'required': ['limit']}}]
        configure_journal_tools(tools)
        configure_journal_tools(tools)
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]['parameters']['required'].count('offset'), 1)
        self.assertNotIn('user_id', tools[1]['parameters']['properties'])

    @patch.dict(os.environ, {'DISCORD_TOKEN': 'unit-test-only'})
    @patch('httpx.Client')
    def test_dm_uses_authenticated_member_only(self, factory):
        client = factory.return_value.__enter__.return_value
        client.post.return_value.status_code = 200
        client.post.return_value.json.return_value = {'id': 'test-channel'}
        result = send_history(get_history(self.db, 1, 10, {}), 10)
        self.assertGreater(result['sent_count'], 0)
        self.assertEqual(client.post.call_args_list[0].kwargs['json']['recipient_id'], '10')
        self.assertEqual(client.post.call_args_list[1].kwargs['json']['allowed_mentions'], {'parse': []})

    @patch.dict(os.environ, {'DISCORD_TOKEN': 'unit-test-only'})
    @patch('httpx.Client')
    def test_dm_failure_does_not_claim_delivery(self, factory):
        client = factory.return_value.__enter__.return_value
        client.post.return_value.status_code = 403
        result = send_history(get_history(self.db, 1, 10, {}), 10)
        self.assertFalse(result['ok'])
        self.assertEqual(result['sent_count'], 0)


if __name__ == '__main__':
    unittest.main()
