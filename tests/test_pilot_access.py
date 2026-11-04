"""Synthetic rollout checks. No network, live member writes or message sends."""
import asyncio
from contextlib import contextmanager
import os
import sqlite3
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.member_access import maintenance_access_error, member_access_error
from test_owner_maintenance import load_function


class PilotAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GBOP_OWNER_ONLY': '0', 'GTOP_OWNER_USER_ID': '99',
            'GBOP_ACCESS_MODE': 'pilot', 'GBOP_PILOT_USER_IDS': '10,20,30,40'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE members (guild_id INT,user_id INT,activated INT,leadership_ack INT,revoked INT)')
        self.conn.executemany('INSERT INTO members VALUES (1,?,?,?,?)',
                             [(10,1,1,0),(20,1,1,1),(30,0,1,0),(40,1,0,0),(50,1,1,0)])
        self.addCleanup(self.conn.close)

    @contextmanager
    def db(self):
        yield self.conn

    def test_pilot_is_intersection_with_activation_consent_revocation_and_guild(self):
        before = list(self.conn.iterdump())
        self.assertIsNone(member_access_error(self.db, 1, 10, 99))
        self.assertIsNone(member_access_error(self.db, 1, 99, 99))
        for user in (20,30,40,50,60):
            with self.subTest(user=user):
                self.assertIsNotNone(member_access_error(self.db, 1, user, 99))
        self.assertIsNotNone(member_access_error(self.db, 2, 10, 99))
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_invalid_lists_fail_closed_without_partial_acceptance(self):
        for value in ('', '*', '10,', ',10', '10,name', '10,0', '10,-1', '10,1.0',
                      '10,18446744073709551616', '10,１２'):
            with self.subTest(value=value), patch.dict(os.environ, {'GBOP_PILOT_USER_IDS': value}):
                self.assertIsNotNone(maintenance_access_error(10, 99))

    def test_only_owner_bypasses_pilot_and_emergency_owner_only_wins(self):
        self.assertIsNone(maintenance_access_error(99, 55))
        with patch.dict(os.environ, {'GBOP_OWNER_ONLY': '1'}):
            self.assertIsNotNone(maintenance_access_error(10, 99))
            self.assertIsNone(maintenance_access_error(99, 99))

    def test_malformed_mode_and_owner_fail_closed(self):
        for values in ({'GBOP_ACCESS_MODE': 'pilto'}, {'GTOP_OWNER_USER_ID': 'invalid'},
                       {'GTOP_OWNER_USER_ID': '0'}):
            with self.subTest(values=values), patch.dict(os.environ, values):
                self.assertIsNotNone(maintenance_access_error(10, 99))

    def test_existing_mode_restores_entitlement_without_record_changes(self):
        before = list(self.conn.iterdump())
        self.assertIsNotNone(member_access_error(self.db, 1, 50, 99))
        with patch.dict(os.environ, {'GBOP_ACCESS_MODE': 'existing'}):
            self.assertIsNone(member_access_error(self.db, 1, 50, 99))
            self.assertIsNotNone(member_access_error(self.db, 1, 20, 99))
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_pilot_id_does_not_grant_discord_role(self):
        invoke = load_function('gbop_voice_web/server.py', '_member_is_allowed', {
            'OWNER_USER_ID': 99, 'GTOP_MEMBER_ROLE_ID': 123})
        self.assertFalse(invoke(10, []))
        self.assertTrue(invoke(10, ['123']))
        self.assertFalse(invoke(50, ['123']))

    def test_commands_only_pass_owner_and_pilot_to_existing_handlers(self):
        invoke = load_function('bot.py', 'interaction_check', {'GTOP_OWNER_USER_ID': 99})
        for user, allowed in ((10, True), (50, False), (99, True)):
            with self.subTest(user=user):
                response = AsyncMock()
                interaction = NS(user=NS(id=user), response=NS(send_message=response))
                self.assertEqual(asyncio.run(invoke(None, interaction)), allowed)
                self.assertEqual(response.await_count, int(not allowed))

    def test_cached_voice_approval_cannot_admit_excluded_member(self):
        invoke = load_function('bot.py', 'gbop_voice_member_allowed', {
            'GTOP_OWNER_USER_ID': 99, 'is_owner': lambda m: m.id == 99,
            '_GBOP_VOICE_ACCESS_CACHE': {50: (time.monotonic(), True, None)}})
        self.assertFalse(invoke(NS(id=50))[0])

    def test_both_tool_dispatchers_ignore_forged_pilot_identity(self):
        for path, name, key in [('bot.py', 'ai_execute_tool', 'GTOP_OWNER_USER_ID'),
                                ('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')]:
            with self.subTest(path=path):
                invoke = load_function(path, name, {key: 99, 'GTOP_GUILD_ID': 1,
                    'member_access_error': member_access_error, 'db': self.db})
                result = invoke(50, 'get_journal_history', {'user_id': 10})
                self.assertFalse(result['ok'])
                self.assertNotIn('journals', result)

    def test_scheduled_authorization_rechecks_pilot_and_revocation(self):
        member = NS(id=10, bot=False)
        guild = NS(fetch_member=AsyncMock(return_value=member))
        invoke = load_function('bot.py', '_authorized_scheduled_member', {
            'client': NS(get_guild=lambda _: guild), 'GTOP_GUILD_ID': 1,
            'GTOP_OWNER_USER_ID': 99, 'has_member_role': lambda m: True,
            'is_owner': lambda m: m.id == 99, 'asyncio': asyncio,
            'member_access_error': member_access_error, 'db': self.db, 'logger': Mock()})
        self.assertIs(asyncio.run(invoke(10)), member)
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=10')
        self.assertIsNone(asyncio.run(invoke(10)))
        guild.fetch_member.return_value = NS(id=50, bot=False)
        self.assertIsNone(asyncio.run(invoke(50)))

    def test_direct_member_gate_requires_consent_even_for_allowlisted_active_member(self):
        class Member:
            def __init__(self, user_id):
                self.id = user_id
        invoke = load_function('bot.py', 'require_member', {
            'GTOP_OWNER_USER_ID': 99, 'discord': NS(Member=Member),
            'ensure_member_record': Mock(), 'is_owner': lambda m: m.id == 99,
            'has_member_role': lambda m: True,
            'get_member_record': lambda uid: self.conn.execute(
                'SELECT * FROM members WHERE user_id=?', (uid,)).fetchone()})
        for user, allowed in ((10, True),(20, False),(30, False),(40, False),(50, False),(99, True)):
            with self.subTest(user=user):
                interaction = NS(user=Member(user), response=NS(send_message=AsyncMock()))
                self.assertEqual(asyncio.run(invoke(interaction)), allowed)

    def test_voice_gate_requires_consent_and_preserves_other_entitlement_checks(self):
        cache = {}
        invoke = load_function('bot.py', 'gbop_voice_member_allowed', {
            'GTOP_OWNER_USER_ID': 99, 'is_owner': lambda m: m.id == 99,
            'has_member_role': lambda m: True, 'time': time, 'logging': Mock(),
            '_GBOP_VOICE_ACCESS_CACHE': cache, '_GBOP_VOICE_ACCESS_CACHE_TTL': 15,
            '_GBOP_VOICE_ACCESS_CACHE_LOCK': threading.Lock(),
            'ensure_member_record': Mock(), 'get_member_record': lambda uid: self.conn.execute(
                'SELECT * FROM members WHERE user_id=?', (uid,)).fetchone()})
        for user, allowed in ((10, True),(20, False),(30, False),(40, False),(50, False),(99, True)):
            with self.subTest(user=user):
                self.assertEqual(invoke(NS(id=user))[0], allowed)
        self.assertFalse(cache[40][1])

    def test_pending_trade_forms_recheck_entitlement_before_private_read_or_write(self):
        class Member:
            def __init__(self, uid):
                self.id = uid
        gate = load_function('bot.py', 'require_member', {
            'GTOP_OWNER_USER_ID': 99, 'discord': NS(Member=Member),
            'ensure_member_record': Mock(), 'is_owner': lambda m: m.id == 99,
            'has_member_role': lambda m: True,
            'get_member_record': lambda uid: self.conn.execute(
                'SELECT * FROM members WHERE user_id=?', (uid,)).fetchone()})
        for mode, user in (('pilot',20),('pilot',30),('pilot',40),('pilot',50),('owner',10)):
            with self.subTest(mode=mode, user=user), patch.dict(os.environ, {'GBOP_ACCESS_MODE':mode}):
                interaction = NS(user=Member(user), response=NS(send_message=AsyncMock()))
                private_read = Mock(side_effect=AssertionError('private read before fresh eligibility'))
                private_write = Mock(side_effect=AssertionError('write before fresh eligibility'))
                with patch('gbop_voice_web.journal_context.journal_transaction', private_write):
                    save = load_function('bot.py', 'save_member_trade', {'require_member':gate,
                        'db':self.db, 'GTOP_GUILD_ID':1})
                    asyncio.run(save(interaction,'SYNTHETIC','Long','Study','Model 1',1,0.1))
                entry = load_function('bot.py', 'save_additional_entry', {
                    'require_member':gate, 'get_thesis':private_read})
                asyncio.run(entry(interaction,123,'Study',1,0.1))
                private_write.assert_not_called()
                private_read.assert_not_called()
                self.assertEqual(interaction.response.send_message.await_count,2)


class NewsAuthorizationTests(unittest.TestCase):
    def exercise(self, decisions):
        member = NS(id=10, send=AsyncMock(return_value=NS(id=123)))
        authorize = AsyncMock(side_effect=[member if value else None for value in decisions])
        reminders, record = Mock(return_value=('plan','risk','rule')), Mock()
        invoke = load_function('bot.py', '_send_forex_news_alerts', {
            'asyncio': asyncio, 'get_high_impact_events': Mock(return_value=[{}]),
            'due_high_impact_events': Mock(return_value=[{}]), 'FF_NEWS_LEAD_MINUTES': 15,
            '_gbop_role_members': lambda: [member], 'event_delivery_key': Mock(return_value='test-event'),
            '_snapshot_delivery_exists': Mock(return_value=False),
            '_authorized_scheduled_member': authorize, '_member_binary_event_reminders': reminders,
            'format_red_folder_alert': Mock(return_value='synthetic test'),
            '_record_snapshot_delivery': record, 'logger': Mock(),
            'discord': NS(Forbidden=ValueError, HTTPException=OSError)})
        asyncio.run(invoke(None))
        return member, authorize, reminders, record

    def test_denied_member_has_no_personal_read_or_send(self):
        member, auth, reminders, record = self.exercise([False])
        auth.assert_awaited_once_with(10)
        reminders.assert_not_called()
        member.send.assert_not_awaited()
        record.assert_not_called()

    def test_revocation_during_preparation_prevents_send(self):
        member, auth, reminders, record = self.exercise([True, False])
        self.assertEqual(auth.await_count, 2)
        reminders.assert_called_once()
        member.send.assert_not_awaited()
        record.assert_not_called()

    def test_eligible_member_gets_one_send_and_receipt(self):
        member, auth, reminders, record = self.exercise([True, True])
        self.assertEqual(auth.await_count, 2)
        member.send.assert_awaited_once_with('synthetic test')
        record.assert_called_once_with('test-event', 10, 123)


if __name__ == '__main__':
    unittest.main()
