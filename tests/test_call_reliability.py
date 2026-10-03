import asyncio
import os
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.recovery_policy import READ_ONLY_TOOLS, recovery_options
from gbop_voice_web.journal_retrieval import journal_tool, read_records, journal_text, JOURNAL_TOOLS

NY = ZoneInfo('America/New_York')
START = int(datetime(2026, 10, 2, 8, tzinfo=NY).timestamp())


def candles(step=60, base=100):
    return [{'time': t, 'open': base, 'high': base + 1, 'low': base - 1, 'close': base}
            for t in range(START, START + 4 * 3600, step)]


class SMTEvidenceTests(unittest.TestCase):
    def compare(self, a, b, sa=60, sb=60):
        return compare_ranges(a, b, START, START + 3600, START + 4 * 3600, sa, sb)

    def test_matched_silver_purge_and_later_invalidation_do_not_erase_smt(self):
        gold, silver = candles(), candles(base=200)
        silver[62]['high'] = 202
        for b in gold[90:]:
            b.update(open=99, high=99, low=97, close=98)
        result = self.compare(gold, silver)
        event = next(e for e in result['events'] if e['direction'] == 'bearish')
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(event['context'], '9ate8 SMT')
        self.assertEqual(event['sweeping_asset'], 'XAGUSD')
        self.assertEqual(event['nonconfirming_asset'], 'XAUUSD')
        self.assertIn('09:02:00', event['start_ny'])
        self.assertEqual(event['targets']['XAUUSD']['opposing_liquidity']['status'], 'observed_after_divergence')
        self.assertFalse(result['entry_confirmed'])

    def test_later_peer_sweep_is_recorded_not_used_to_reject(self):
        a, b = candles(), candles(base=200)
        b[62]['high'], a[100]['high'] = 202, 102
        event = self.compare(a, b)['events'][0]
        self.assertIn('09:40:00', event['other_asset_later_sweep_ny'])

    def test_missing_peer_anchor_is_unknown_not_negative(self):
        a, b = candles(), candles(base=200)
        b.pop(3)
        result = self.compare(a, b)
        self.assertEqual(result['status'], 'insufficient_matched_data')
        self.assertEqual(result['events'], [])

    def test_gap_before_divergence_prevents_confirmation(self):
        a, b = candles(), candles(base=200)
        b[62]['high'] = 202
        a.pop(61)
        self.assertEqual(self.compare(a, b)['events'], [])

    def test_gap_after_divergence_preserves_observation(self):
        a, b = candles(), candles(base=200)
        b[62]['high'] = 202
        a.pop(80)
        result = self.compare(a, b)
        self.assertEqual(result['status'], 'observed')
        self.assertFalse(result['complete'])

    def test_both_sweep_same_bar_does_not_invent_intrabar_order(self):
        a, b = candles(), candles(base=200)
        a[62]['high'], b[62]['high'] = 102, 202
        self.assertEqual(self.compare(a, b)['status'], 'not_observed')

    def test_mixed_resolution_reports_five_minutes(self):
        a, b = candles(), candles(300, 200)
        b[12]['high'] = 202
        result = self.compare(a, b, 60, 300)
        self.assertEqual(result['precision_seconds'], 300)
        self.assertIn('09:00:00', result['events'][0]['start_ny'])
        self.assertIn('09:05:00', result['events'][0]['end_ny'])

    def test_same_bar_target_touch_is_not_claimed_as_after_entry(self):
        a, b = candles(), candles(base=200)
        b[62]['high'] = 202
        b[62]['low'] = 198
        for row in b[63:]:
            row['low'] = 200
        result = self.compare(a, b)
        event = next(e for e in result['events'] if e['direction'] == 'bearish')
        self.assertEqual(event['targets']['XAGUSD']['opposing_liquidity']['status'], 'same_bar_order_unknown')

    def test_duplicate_bars_rejected(self):
        a, b = candles(), candles(base=200)
        a.append(a[0])
        with self.assertRaises(ValueError):
            self.compare(a, b)

    def test_identical_instrument_rejected(self):
        with self.assertRaises(ValueError):
            compare_ranges(candles(), candles(), START, START + 3600, START + 7200,
                           left_asset='XAUUSD', right_asset='XAUUSD')


class JournalIsolationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,leadership_ack INTEGER,revoked INTEGER);
        CREATE TABLE theses(id INTEGER,guild_id INTEGER,user_id INTEGER,asset TEXT,direction TEXT,play TEXT,status TEXT);
        CREATE TABLE thesis_executions(id INTEGER,guild_id INTEGER,user_id INTEGER);
        CREATE TABLE trade_photos(id TEXT,guild_id INTEGER,user_id INTEGER);
        CREATE TABLE journals(id INTEGER,guild_id INTEGER,user_id INTEGER,thesis_id INTEGER,description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT);
        INSERT INTO members VALUES(1,10,1,1,0),(1,20,1,1,0),(2,10,1,1,0);
        INSERT INTO theses VALUES(1,1,10,'NAS100','Bearish','9ate8','CLOSED'),(2,1,20,'SECRET','Bullish','PRIVATE','OPEN'),(3,2,10,'OTHER_GUILD','Bullish','PRIVATE','OPEN');
        INSERT INTO thesis_executions VALUES(1,1,10),(2,1,20);
        INSERT INTO trade_photos VALUES('own',1,10),('other',1,20);
        INSERT INTO journals VALUES(1,1,10,1,'my exact note','yes',0.0,'study','2026-10-02T14:00:00Z'),(2,1,20,2,'PRIVATE NOTE','',8,'','today'),(3,2,10,3,'OTHER GUILD NOTE','',9,'','today');
        ''')
        self.env = patch.dict(os.environ, {'GTOP_OWNER_USER_ID': '999', 'GBOP_VOICE_USER_ID': '999'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.close()

    @contextmanager
    def db(self):
        yield self.conn

    def test_closed_trade_counts_and_only_own_journal(self):
        result = journal_tool(self.db, 1, 10, 'get_saved_records', {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['totals'], {'trades': 1, 'executions': 1, 'journals': 1, 'photos': 1, 'open_trades': 0})
        self.assertEqual(result['journals'][0]['summary'], 'my exact note')
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('OTHER_GUILD', str(result))
        self.assertIn('0.0R', journal_text(result))

    def test_revoked_member_cannot_read(self):
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=1 AND user_id=10')
        result = journal_tool(self.db, 1, 10, 'get_saved_records', {})
        self.assertEqual(result['error_code'], 'access_denied')
        self.assertNotIn('journals', result)

    def test_no_consent_cannot_read(self):
        self.conn.execute('UPDATE members SET leadership_ack=0 WHERE guild_id=1 AND user_id=10')
        self.assertFalse(journal_tool(self.db, 1, 10, 'get_saved_records', {})['ok'])

    def test_owner_still_only_gets_owner_records(self):
        with patch.dict(os.environ, {'GTOP_OWNER_USER_ID': '10'}):
            result = journal_tool(self.db, 1, 10, 'get_saved_records', {})
        self.assertEqual(result['totals']['journals'], 1)

    def test_missing_member_is_access_error_not_empty_history(self):
        result = journal_tool(self.db, 1, 99, 'get_saved_records', {})
        self.assertEqual(result['error_code'], 'access_denied')

    def test_empty_page_keeps_total(self):
        result = read_records(self.db, 1, 10, {'offset': 5})
        self.assertEqual(result['journals'], [])
        self.assertEqual(result['totals']['journals'], 1)

    def test_schema_never_accepts_another_member_or_recipient(self):
        for tool in JOURNAL_TOOLS:
            self.assertNotIn('user_id', tool['parameters']['properties'])
            self.assertNotIn('recipient_id', tool['parameters']['properties'])
            self.assertFalse(tool['parameters']['additionalProperties'])


    def test_database_failure_is_not_reported_as_empty(self):
        self.conn.execute('DROP TABLE journals')
        result = journal_tool(self.db, 1, 10, 'get_saved_records', {})
        self.assertEqual(result['error_code'], 'journal_unavailable')
        self.assertNotIn('totals', result)

    def test_private_delivery_uses_authenticated_member_only(self):
        from unittest.mock import MagicMock
        fake_photos = SimpleNamespace(search=lambda *a, **k: {'ok': True, 'photos': [], 'has_more': False})
        fake_cards = SimpleNamespace(recall_cards=lambda *a: [], text=lambda value, limit=100: str(value or '')[:limit])
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.side_effect = [SimpleNamespace(status_code=200, json=lambda: {'id': 'private-dm'}),
                                   SimpleNamespace(status_code=200)]
        with patch.dict('sys.modules', {'gbop_voice_web.trade_photos': fake_photos,
                                      'gbop_voice_web.photo_recall': fake_cards}), \
             patch('httpx.Client', return_value=client), patch.dict(os.environ, {'DISCORD_TOKEN': 'TEST-NOT-A-SECRET'}):
            result = journal_tool(self.db, 1, 10, 'send_journal_records', {'include_photos': True})
        self.assertEqual(client.post.call_args_list[0].kwargs['json']['recipient_id'], '10')
        attachment = client.post.call_args_list[1].kwargs['files']['files[0]'][1].decode()
        self.assertIn('my exact note', attachment)
        self.assertNotIn('PRIVATE NOTE', attachment)
        self.assertEqual(result['sent_count'], 1)

    def test_closed_dms_do_not_claim_delivery(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = SimpleNamespace(status_code=403)
        stubs = {'gbop_voice_web.trade_photos': SimpleNamespace(search=None),
                 'gbop_voice_web.photo_recall': SimpleNamespace(recall_cards=None, text=None)}
        with patch.dict('sys.modules', stubs), patch('httpx.Client', return_value=client), \
             patch.dict(os.environ, {'DISCORD_TOKEN': 'TEST-NOT-A-SECRET'}):
            result = journal_tool(self.db, 1, 10, 'send_journal_records', {'include_photos': False})
        self.assertEqual(result['error_code'], 'dm_unavailable')
        self.assertEqual(result['sent_count'], 0)

    def test_timeout_reports_unknown_delivery_not_success(self):
        import httpx
        from unittest.mock import MagicMock
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.side_effect = httpx.ReadTimeout('test')
        stubs = {'gbop_voice_web.trade_photos': SimpleNamespace(search=None),
                 'gbop_voice_web.photo_recall': SimpleNamespace(recall_cards=None, text=None)}
        with patch.dict('sys.modules', stubs), patch('httpx.Client', return_value=client), \
             patch.dict(os.environ, {'DISCORD_TOKEN': 'TEST-NOT-A-SECRET'}):
            result = journal_tool(self.db, 1, 10, 'send_journal_records', {'include_photos': False})
        self.assertTrue(result['delivery_status_unknown'])
        self.assertEqual(result['sent_count'], 0)


class RecoveryTests(unittest.TestCase):
    def test_reads_survive_but_writes_and_deliveries_do_not(self):
        tools = [{'type': 'function', 'name': n} for n in
                 ('get_saved_records', 'get_journal_history', 'review_market_smt',
                  'open_trade', 'delete_journal', 'send_journal_records', 'get_unreviewed_side_effect')]
        result = recovery_options(tools, {'max_output_tokens': 2200})
        self.assertEqual({t['name'] for t in result['tools']},
                         {'get_saved_records', 'get_journal_history', 'review_market_smt'})
        self.assertEqual(result['max_output_tokens'], 2200)
        self.assertEqual(result['tool_choice'], 'auto')
        self.assertNotIn('send_journal_records', READ_ONLY_TOOLS)

    def test_empty_allowlist_fails_closed(self):
        self.assertEqual(recovery_options([])['tool_choice'], 'none')

    def test_read_only_guard_survives_followups_until_new_speech(self):
        from gbop_voice_web.voice_runtime import VoiceRateLimitRecovery
        session = SimpleNamespace(_voice_turn_count=3, _voice_tools=[{'name': 'get_saved_records'}, {'name': 'delete_trade'}])
        guard = VoiceRateLimitRecovery(session)
        guard.read_only_turn = 3
        guard.cancel(reset=True)
        self.assertTrue(guard.read_only_active)
        self.assertIsNotNone(guard.tool_denial('delete_trade'))
        self.assertIsNone(guard.tool_denial('get_saved_records'))
        self.assertEqual([t['name'] for t in guard.options()['tools']], ['get_saved_records'])
        session._voice_turn_count += 1
        self.assertFalse(guard.read_only_active)
        self.assertIsNone(guard.tool_denial('delete_trade'))


if __name__ == '__main__':
    unittest.main()
