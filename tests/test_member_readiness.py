"""No credentials or live journal writes: release security and voice regressions."""
import ast
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException
from gbop_voice_web.member_access import member_access_error
from gbop_voice_web.voice_policy import build_voice_instructions
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_data import MARKET_PROMPT
from test_voice_latency import method

ROOT = Path(__file__).resolve().parents[1]


def function(path, name, ns):
    node = next(n for n in ast.parse((ROOT / path).read_text()).body
                if getattr(n, 'name', '') == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return ns[name]


class MemberAccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE members (guild_id INT,user_id INT,activated INT,leadership_ack INT,revoked INT)')
        self.conn.executemany('INSERT INTO members VALUES (?,?,?,?,?)', [
            (1, 10, 1, 1, 0), (1, 11, 1, 1, 0), (2, 12, 1, 1, 0),
            (1, 13, 0, 0, 0), (1, 14, 1, 1, 1), (1, 15, 1, 0, 0)])
        self.addCleanup(self.conn.close)

    @contextmanager
    def db(self):
        yield self.conn

    def test_active_members_and_owner_allowed(self):
        for uid in (10, 11, 99):
            self.assertIsNone(member_access_error(self.db, 1, uid, 99))

    def test_missing_cross_guild_inactive_revoked_and_unacknowledged_denied(self):
        for uid in (12, 13, 14, 15, 16):
            self.assertIsNotNone(member_access_error(self.db, 1, uid, 99))

    def test_revocation_is_fresh_and_does_not_affect_other_member(self):
        self.assertIsNone(member_access_error(self.db, 1, 10, 99))
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=1 AND user_id=10')
        self.assertIn('revoked', member_access_error(self.db, 1, 10, 99))
        self.assertIsNone(member_access_error(self.db, 1, 11, 99))

    def test_database_failure_fails_closed_and_hides_exception(self):
        broken = Mock(side_effect=RuntimeError('private database address/password'))
        error = member_access_error(broken, 1, 10, 99)
        self.assertIn('could not be verified', error)
        self.assertNotIn('password', error)
        self.assertIsNotNone(member_access_error(broken, 1, 0, 0))

    def test_both_dispatchers_deny_before_any_tool_and_ignore_forged_arguments(self):
        for path, name, owner_name in [('bot.py', 'ai_execute_tool', 'GTOP_OWNER_USER_ID'),
                                     ('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')]:
            tool = Mock(return_value={'ok': True})
            ns = dict(member_access_error=member_access_error, db=self.db, GTOP_GUILD_ID=1,
                      MARKET_NAMES={'get_market_price'}, market_tool=tool,
                      WATCH_NAMES={'manage_market_watch','get_prepared_market_brief'},
                      watch_tool=tool, **{owner_name: 99})
            invoke = function(path, name, ns)
            result = invoke(14, 'get_market_price', {'user_id': 99, 'guild_id': 2})
            self.assertFalse(result['ok'])
            tool.assert_not_called()
            self.assertTrue(invoke(10, 'get_market_price', {'asset': 'NAS'})['ok'])
            tool.assert_called_once()


class BrowserGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_role_session_still_checks_persistent_revocation(self):
        session = {'user_id': 10, 'expires_at': time.time() + 60, 'is_owner': False}
        sessions = {'session': session}
        checker = Mock(return_value='Your GBOP access is revoked.')
        async def offload(fn, *args):
            return fn(*args)
        ns = dict(Request=object, time=time, _cleanup_auth_state=Mock(), SESSION_COOKIE='cookie',
                  AUTH_SESSIONS=sessions, _refresh_member_session=AsyncMock(return_value=session),
                  HTTPException=HTTPException, asyncio=NS(to_thread=offload),
                  member_access_error=checker, db=object(), GTOP_GUILD_ID=1, OWNER_USER_ID=99)
        auth = function('gbop_voice_web/server.py', 'require_authenticated_user', ns)
        with self.assertRaises(HTTPException) as caught:
            await auth(NS(cookies={'cookie': 'session'}))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertNotIn('session', sessions)
        self.assertEqual(checker.call_args.args[1:], (1, 10, 99))

    async def test_missing_cookie_denied_before_database(self):
        ns = dict(Request=object, _cleanup_auth_state=Mock(), SESSION_COOKIE='cookie',
                  HTTPException=HTTPException)
        auth = function('gbop_voice_web/server.py', 'require_authenticated_user', ns)
        with self.assertRaises(HTTPException) as caught:
            await auth(NS(cookies={}))
        self.assertEqual(caught.exception.status_code, 401)


class VoiceReleaseTests(unittest.TestCase):
    def test_full_canon_and_market_evidence_policy_are_preserved_verbatim_once(self):
        prompt = build_voice_instructions(CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile A')
        self.assertEqual(prompt.count(CANONICAL_KNOWLEDGE), 1)
        self.assertEqual(prompt.count(MARKET_PROMPT), 1)
        self.assertIn('explicit member confirmation', prompt)
        self.assertIn('WARN + SAVE', prompt)
        self.assertIn('Recovery retains approved read tools', prompt)
        self.assertIn('not trade/journal writes', prompt)
        self.assertIn('get_ss_review', prompt)
        self.assertIn('sent_count', prompt)

    def test_startup_fetches_only_own_profile_not_full_journal_context(self):
        profile = Mock(side_effect=lambda db, guild, uid: {'uid': uid})
        legacy = Mock(side_effect=AssertionError('Do not preload journal history'))
        ns = dict(get_profile=profile, db=object(), GTOP_GUILD_ID=1,
                  profile_context=lambda p: 'private profile ' + str(p['uid']),
                  market_clock=lambda: 'NY clock', CANONICAL_KNOWLEDGE=CANONICAL_KNOWLEDGE,
                  MARKET_PROMPT=MARKET_PROMPT, build_voice_instructions=build_voice_instructions,
                  ai_member_context=legacy)
        instructions = method('instructions', ns)
        one = instructions(NS(member=NS(id=10)))
        two = instructions(NS(member=NS(id=11)))
        self.assertIn('private profile 10', one)
        self.assertNotIn('private profile 11', one)
        self.assertIn('private profile 11', two)
        self.assertNotIn('private profile 10', two)
        legacy.assert_not_called()
        self.assertEqual([c.args[2] for c in profile.call_args_list], [10, 11])

    def test_compact_operations_budget_preserves_expanded_canon(self):
        prompt = build_voice_instructions(CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile')
        # Owner-approved canon grows independently of compact voice operations.
        # Bound overhead separately rather than forcing new definitions to be cut.
        overhead = len(prompt) - len(CANONICAL_KNOWLEDGE) - len(MARKET_PROMPT)
        self.assertGreater(overhead, 0)
        self.assertLess(overhead, 10000)
        # The owner-approved range-language canon adds bounded definitions/examples.
        # Operations overhead stays separately bounded; response payload caps are unchanged.
        self.assertLess(len(prompt), 51000)
        self.assertEqual(prompt.count(CANONICAL_KNOWLEDGE), 1)
        self.assertEqual(prompt.count(MARKET_PROMPT), 1)

    def test_all_existing_tools_remain_available_with_contextual_schema_copy(self):
        source = ast.parse((ROOT/'bot.py').read_text())
        cls = next(n for n in source.body if getattr(n,'name','')=='GBOPRealtimeSession')
        update = next(n for n in cls.body if getattr(n,'name','')=='session_update')
        text = ast.unparse(update)
        self.assertIn("getattr(self, 'conversation_tools', GBOP_AI_TOOLS)", text)
        self.assertNotIn('[:', text)
        self.assertIn('interrupt_response', text)


if __name__ == '__main__':
    unittest.main()
