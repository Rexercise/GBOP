"""Synthetic maintenance boundary tests; no Discord/network/member mutations."""
import ast
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from gbop_voice_web.member_access import maintenance_access_error, member_access_error

ROOT = Path(__file__).resolve().parents[1]


def load_function(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    future = ast.parse('from __future__ import annotations').body
    exec(compile(ast.Module(body=future + [node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


class OwnerMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GBOP_OWNER_ONLY': '1', 'GTOP_OWNER_USER_ID': '99',
                                          'GBOP_ACCESS_MODE': 'existing', 'GBOP_PILOT_USER_IDS': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE members (guild_id INT,user_id INT,activated INT,leadership_ack INT,revoked INT)')
        self.conn.execute('INSERT INTO members VALUES (1,10,1,1,0)')
        self.addCleanup(self.conn.close)

    @contextmanager
    def db(self):
        yield self.conn

    def test_activated_member_denied_and_switch_off_restores_existing_entitlement(self):
        original = tuple(self.conn.execute('SELECT * FROM members').fetchone())
        self.assertIsNotNone(member_access_error(self.db, 1, 10, 99))
        with patch.dict(os.environ, {'GBOP_OWNER_ONLY': 'off'}):
            self.assertIsNone(member_access_error(self.db, 1, 10, 99))
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM members').fetchone()), original)

    def test_configured_owner_only_and_invalid_configuration_fails_closed(self):
        self.assertIsNone(maintenance_access_error(99, 55))
        self.assertIsNotNone(maintenance_access_error(55, 55))
        with patch.dict(os.environ, {'GTOP_OWNER_USER_ID': 'invalid', 'GBOP_OWNER_ONLY': 'typo'}):
            self.assertIsNotNone(maintenance_access_error(99, 99))

    def test_denial_before_database_and_owner_passes_without_database(self):
        db = Mock(side_effect=RuntimeError('private credentials'))
        error = member_access_error(db, 1, 10, 99)
        self.assertNotIn('credentials', error)
        self.assertNotIn('99', error)
        self.assertIsNone(member_access_error(db, 1, 99, 99))
        db.assert_not_called()

    def test_command_tree_blocks_all_nonowner_commands_before_dispatch(self):
        invoke = load_function('bot.py', 'interaction_check', {'GTOP_OWNER_USER_ID': 99})
        for uid, expected in ((10, False), (99, True)):
            reply = AsyncMock()
            interaction = NS(user=NS(id=uid), response=NS(send_message=reply))
            self.assertEqual(asyncio.run(invoke(None, interaction)), expected)
            self.assertEqual(reply.await_count, int(not expected))

    def test_pending_member_callbacks_denied_before_record_creation(self):
        ensure = Mock()
        invoke = load_function('bot.py', 'require_member', {'GTOP_OWNER_USER_ID': 99,
            'ensure_member_record': ensure})
        interaction = NS(user=NS(id=10), response=NS(send_message=AsyncMock()))
        self.assertFalse(asyncio.run(invoke(interaction)))
        ensure.assert_not_called()

    def test_new_browser_login_ignores_cached_member_role_during_maintenance(self):
        invoke = load_function('gbop_voice_web/server.py', '_member_is_allowed', {
            'OWNER_USER_ID': 99, 'GTOP_MEMBER_ROLE_ID': 123})
        self.assertFalse(invoke(10, ['123']))
        self.assertTrue(invoke(99, []))
        with patch.dict(os.environ, {'GBOP_OWNER_ONLY': '0'}):
            self.assertTrue(invoke(10, ['123']))

    def test_dm_silently_denied_before_context_or_member_lookup(self):
        seen = Mock()
        invoke = load_function('bot.py', 'on_message', {'GTOP_OWNER_USER_ID': 99,
            'GBOP_PRIVATE_ROOMS': NS(message_seen=seen)})
        asyncio.run(invoke(NS(author=NS(id=10, bot=False), channel=NS(id=123))))
        seen.assert_called_once_with(123)  # Preserve room-history retention tracking.

    def test_cached_voice_allow_cannot_bypass_gate_and_owner_passes(self):
        cached = {10: (time.monotonic(), True, None)}
        invoke = load_function('bot.py', 'gbop_voice_member_allowed', {
            'GTOP_OWNER_USER_ID': 99, 'is_owner': lambda m: m.id == 99,
            '_GBOP_VOICE_ACCESS_CACHE': cached})
        self.assertFalse(invoke(NS(id=10))[0])
        self.assertTrue(invoke(NS(id=99))[0])

    def test_cached_browser_session_denied_before_discord_refresh_and_context_closed(self):
        context = NS(close=Mock())
        sessions = {'cached': {'user_id': 10, 'expires_at': time.time()+600,
            'last_role_check': time.time(), 'roles': ['member'], 'market_context': context}}
        refresh = AsyncMock()
        invoke = load_function('gbop_voice_web/server.py', 'require_authenticated_user', {
            'OWNER_USER_ID': 99, '_cleanup_auth_state': Mock(), 'SESSION_COOKIE': 'sid',
            'AUTH_SESSIONS': sessions, 'HTTPException': HTTPException, 'time': time,
            '_refresh_member_session': refresh})
        with self.assertRaises(HTTPException) as result:
            asyncio.run(invoke(NS(cookies={'sid': 'cached'})))
        self.assertEqual(result.exception.status_code, 403)
        self.assertNotIn('cached', sessions)
        context.close.assert_called_once()
        refresh.assert_not_awaited()

    def test_owner_browser_session_passes_existing_auth_and_database_gate(self):
        session = {'user_id': 99, 'expires_at': time.time()+600}
        refresh = AsyncMock(return_value=session)
        invoke = load_function('gbop_voice_web/server.py', 'require_authenticated_user', {
            'OWNER_USER_ID': 99, '_cleanup_auth_state': Mock(), 'SESSION_COOKIE': 'sid',
            'AUTH_SESSIONS': {'owner': session}, 'HTTPException': HTTPException, 'time': time,
            '_refresh_member_session': refresh, 'asyncio': asyncio,
            'member_access_error': member_access_error, 'db': self.db, 'GTOP_GUILD_ID': 1})
        self.assertIs(asyncio.run(invoke(NS(cookies={'sid': 'owner'}))), session)

    def test_both_inflight_tool_dispatchers_deny_before_any_tool(self):
        for path, name, key in [('bot.py', 'ai_execute_tool', 'GTOP_OWNER_USER_ID'),
                                ('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')]:
            invoke = load_function(path, name, {key: 99, 'GTOP_GUILD_ID': 1,
                'member_access_error': member_access_error, 'db': self.db})
            result = invoke(10, 'get_journal_history', {'user_id': 99})
            self.assertFalse(result['ok'])
            self.assertNotIn('journals', result)


if __name__ == '__main__':
    unittest.main()
