"""Synthetic, credential-free persistence and authenticated transport contracts."""
import ast
import asyncio
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web import midpoint_preferences as mp
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.member_access import member_access_error
from gbop_voice_web.voice_payload import voice_tool_payload

ROOT = Path(__file__).resolve().parents[1]


def function(path, name, namespace, *, cls=None):
    body = ast.parse((ROOT / path).read_text()).body
    if cls:
        body = next(n for n in body if getattr(n, 'name', '') == cls).body
    node = next(n for n in body if getattr(n, 'name', '') == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.filename = str(Path(self.temp.name) / 'synthetic.db')
        with self.db() as conn:
            conn.executescript('''
                CREATE TABLE members(guild_id INT,user_id INT,activated INT,leadership_ack INT,revoked INT);
                INSERT INTO members VALUES(10,20,1,1,0),(10,21,1,1,0),(11,20,1,1,0),(10,22,1,1,1),(10,23,0,0,0);
                CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT NOT NULL,lease_until INT NOT NULL,last_tick INT NOT NULL,state TEXT NOT NULL);
                INSERT INTO gbop_watch_runtime VALUES('primary','worker',999,123,'{"existing":"lease"}');
                CREATE TABLE synthetic_facts(midpoint REAL,price REAL,classification TEXT);
                INSERT INTO synthetic_facts VALUES(100,105,'Model 1 / CSD');
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.filename)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def read(self, guild=10, user=20):
        return mp.get_preference(self.db, guild, user, 99)

    def save(self, label='CE', guild=10, user=20, **args):
        return mp.preference_tool(self.db, guild, user, 99, 'save_midpoint_preference',
                                  dict(midpoint_label=label, confirmed=True, **args))

    def test_no_assumed_preference_or_storage_before_choice(self):
        profile = self.read()
        self.assertFalse(profile['configured'])
        self.assertIsNone(profile['midpoint_label'])
        self.assertIn('No member preference is assumed', mp.preference_context(self.db, 10, 20, 99))
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM gbop_watch_runtime').fetchone()[0], 1)

    def test_exact_enum_confirmation_and_extra_identity_keys(self):
        self.assertEqual(mp.LABELS, ('CE', 'consequent encroachment', '50%', 'equilibrium'))
        for value in ('midpoint', 'halfway', 'eq', 'ce', 'fifty percent', '', None, 50, True, ['CE'], {'label': 'CE'}):
            self.assertFalse(self.save(value)['ok'], value)
        for confirmed in (False, None, 1, 'true'):
            result = mp.preference_tool(self.db, 10, 20, 99, 'save_midpoint_preference',
                                        {'midpoint_label': 'CE', 'confirmed': confirmed})
            self.assertFalse(result['ok'])
        for fields in ({'user_id': 21}, {'guild_id': 11}, {'owner_id': 20}, {'_midpoint_binding': {}}):
            self.assertFalse(self.save(**fields)['ok'])
        self.assertFalse(self.read()['configured'])

    def test_persistence_restart_and_all_four_labels(self):
        for label in mp.LABELS:
            result = self.save(label)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['saved'])
            importlib.reload(mp)  # New connection and module, not an in-process cache.
            self.assertEqual(self.read()['midpoint_label'], label)
        with self.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM gbop_watch_runtime').fetchone()[0], 2)
            state = json.loads(conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=?',
                              (mp.PREFIX + '10:20',)).fetchone()[0])
            self.assertEqual(set(state), {'version', 'midpoint_label', 'updated_at'})

    def test_member_and_guild_switching_never_inherits_another_choice(self):
        self.assertTrue(self.save('CE')['ok'])
        self.assertFalse(self.read(user=21)['configured'])
        self.assertFalse(self.read(guild=11)['configured'])
        self.assertTrue(self.save('50%', user=21)['ok'])
        self.assertTrue(self.save('equilibrium', guild=11)['ok'])
        self.assertEqual(self.read()['midpoint_label'], 'CE')
        self.assertEqual(self.read(user=21)['midpoint_label'], '50%')
        self.assertEqual(self.read(guild=11)['midpoint_label'], 'equilibrium')
        self.assertFalse(mp.preference_tool(self.db, 10, 20, 99, 'get_midpoint_preference', {'user_id': 21})['ok'])

    def test_fresh_revocation_and_inactive_members_block_reads_and_writes(self):
        self.assertTrue(self.save()['ok'])
        with self.db() as conn:
            conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        for user in (20, 22, 23, 24):
            for name, args in [('get_midpoint_preference', {}), ('save_midpoint_preference',
                               {'midpoint_label': 'equilibrium', 'confirmed': True})]:
                self.assertFalse(mp.preference_tool(self.db, 10, user, 99, name, args)['ok'])
        self.assertFalse(self.read(user=21)['configured'])
        with self.db() as conn:
            saved = json.loads(conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=?', (mp.PREFIX + '10:20',)).fetchone()[0])
            self.assertEqual(saved['midpoint_label'], 'CE')

    def test_invalid_scope_and_storage_failure_fail_closed(self):
        for guild, user in ((0,20),(10,0),(True,20),(10,False),('10',20),(10,'20')):
            with self.assertRaises(ValueError):
                mp.get_preference(self.db, guild, user, 99)
        broken = Mock(side_effect=RuntimeError('private credentials'))
        result = mp.preference_tool(broken, 10, 20, 99, 'get_midpoint_preference', {})
        self.assertFalse(result['ok'])
        self.assertNotIn('credentials', result['error'])
        context = mp.preference_context(broken, 10, 20, 99)
        self.assertIn('unavailable', context)
        self.assertNotIn('configured=false', context)

    def test_label_changes_do_not_touch_facts_journals_or_runtime_lease(self):
        with self.db() as conn:
            before = dict(conn.execute("SELECT * FROM gbop_watch_runtime WHERE id='primary'").fetchone())
            facts = dict(conn.execute('SELECT * FROM synthetic_facts').fetchone())
            schema = list(conn.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall())
        for label in mp.LABELS:
            self.assertTrue(self.save(label)['ok'])
        with self.db() as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM gbop_watch_runtime WHERE id='primary'").fetchone()), before)
            self.assertEqual(dict(conn.execute('SELECT * FROM synthetic_facts').fetchone()), facts)
            self.assertEqual(list(conn.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()), schema)

    def test_corrupt_profile_and_mismatched_owner_fail_closed(self):
        key = mp.PREFIX + '10:20'
        with self.db() as conn:
            conn.execute('INSERT INTO gbop_watch_runtime VALUES(?,?,0,0,?)', (key, key, '{"version":1,"midpoint_label":"nickname"}'))
        self.assertFalse(mp.preference_tool(self.db, 10, 20, 99, 'get_midpoint_preference', {})['ok'])
        with self.db() as conn:
            conn.execute('UPDATE gbop_watch_runtime SET owner=? WHERE id=?', ('another-member', key))
        self.assertFalse(self.save()['ok'])

    def test_bound_queued_requests_reject_cancel_logout_or_identity_switch(self):
        for mutation in ('cancel', 'close', 'member', 'guild', 'session'):
            with self.subTest(mutation=mutation):
                context = MarketConversation((10, 20, 'test'))
                generation = context.begin_turn('Save CE as my midpoint label')
                args = mp.bind_preference_args(context, 'save_midpoint_preference',
                    {'midpoint_label': 'CE', 'confirmed': True}, generation)
                if mutation == 'cancel':
                    context.begin_turn('cancel')
                elif mutation == 'close':
                    context.close()
                elif mutation == 'member':
                    context.owner = (10, 21, 'test')
                elif mutation == 'guild':
                    context.owner = (11, 20, 'test')
                else:
                    context.session_id = 'replacement-session'
                result = mp.preference_tool(self.db, 10, 20, 99, 'save_midpoint_preference', args)
                self.assertFalse(result['ok'], result)
        self.assertFalse(self.read()['configured'])

    def test_bound_save_checks_access_after_dispatch_admission(self):
        context = MarketConversation((10, 20, 'test'))
        generation = context.begin_turn()
        args = mp.bind_preference_args(context, 'save_midpoint_preference',
            {'midpoint_label': 'CE', 'confirmed': True}, generation)
        self.assertIsNone(member_access_error(self.db, 10, 20, 99))
        with self.db() as conn:
            conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        self.assertFalse(mp.preference_tool(self.db, 10, 20, 99, 'save_midpoint_preference', args)['ok'])

    def dispatch(self, path, name, owner_name):
        return function(path, name, dict(member_access_error=member_access_error, db=self.db,
                         GTOP_GUILD_ID=10, **{owner_name: 99}))

    def test_browser_backend_and_discord_share_authenticated_profile(self):
        browser = self.dispatch('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')
        discord = self.dispatch('bot.py', 'ai_execute_tool', 'GTOP_OWNER_USER_ID')
        self.assertTrue(browser(20, 'save_midpoint_preference', {'midpoint_label': 'CE', 'confirmed': True})['ok'])
        self.assertEqual(discord(20, 'get_midpoint_preference', {})['profile']['midpoint_label'], 'CE')
        self.assertTrue(discord(20, 'save_midpoint_preference', {'midpoint_label': 'equilibrium', 'confirmed': True})['ok'])
        self.assertEqual(browser(20, 'get_midpoint_preference', {})['profile']['midpoint_label'], 'equilibrium')
        for invoke in (browser, discord):
            self.assertFalse(invoke(21, 'get_midpoint_preference', {})['profile']['configured'])
            self.assertFalse(invoke(22, 'get_midpoint_preference', {'user_id': 99})['ok'])
            self.assertFalse(invoke(20, 'save_midpoint_preference',
                {'midpoint_label': '50%', 'confirmed': True, 'user_id': 21})['ok'])

    def test_fresh_context_changes_and_chosen_label_suppresses_onboarding(self):
        self.save('CE')
        first = mp.preference_context(self.db, 10, 20, 99)
        self.save('equilibrium')
        second = mp.preference_context(self.db, 10, 20, 99)
        self.assertIn('midpoint_label=CE', first)
        self.assertIn('midpoint_label=equilibrium', second)
        self.assertNotIn('midpoint_label=CE', second)
        self.assertIn('never repeat onboarding', second)
        self.assertNotIn(mp.QUESTION, second)

    def test_browser_backend_tool_loop_refreshes_label_after_save(self):
        self.save('CE')
        context = MarketConversation((10, 20, 'browser_voice'))
        context.begin_turn(client_turn=1)
        call = NS(type='function_call', name='save_midpoint_preference', call_id='save-1',
                  arguments=json.dumps({'midpoint_label': 'equilibrium', 'confirmed': True}))
        create = Mock(side_effect=[NS(output=[call], output_text=''), NS(output=[], output_text='Saved equilibrium.')])
        dispatch = self.dispatch('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')
        invoke = function('gbop_voice_web/server.py', 'run_backend', dict(db=self.db, GTOP_GUILD_ID=10,
            PENDING_JOURNAL_DELETIONS={}, time=time, json=json,
            member_context=lambda uid: mp.preference_context(self.db, 10, uid, 99),
            BACKEND_PROMPT=mp.MIDPOINT_PROMPT, BACKEND_MODEL='offline-test', TOOLS=mp.TOOLS,
            run_tool=dispatch, client=NS(responses=NS(create=create))))
        answer = invoke([{'role': 'user', 'text': 'Save equilibrium as my midpoint label'}], 20, context, 2)
        self.assertEqual(answer, 'Saved equilibrium.')
        self.assertEqual(self.read()['midpoint_label'], 'equilibrium')
        final_instructions = create.call_args.kwargs['instructions']
        self.assertIn('midpoint_label=equilibrium', final_instructions)
        self.assertNotIn('midpoint_label=CE', final_instructions)
        result = next(item for item in create.call_args.kwargs['input']
                      if isinstance(item, dict) and item.get('type') == 'function_call_output')
        self.assertTrue(json.loads(result['output'])['saved'])

    def test_discord_text_tool_loop_replaces_old_snapshot_then_browser_reads_update(self):
        self.save('CE')
        context = MarketConversation((10, 20, 'text', 'synthetic'))
        call = NS(type='function_call', name='save_midpoint_preference', call_id='save-2',
                  arguments=json.dumps({'midpoint_label': '50%', 'confirmed': True}))
        create = Mock(side_effect=[NS(output=[call], output_text=''), NS(output=[], output_text='Saved 50%.')])
        dispatch = self.dispatch('bot.py', 'ai_execute_tool', 'GTOP_OWNER_USER_ID')
        invoke = function('bot.py', 'ai_run_turn', dict(db=self.db, GTOP_GUILD_ID=10,
            init_ai_db=Mock(), ai_recent_messages=lambda *args, **kwargs: [],
            ai_member_context=lambda uid: mp.preference_context(self.db, 10, uid, 99),
            GTOP_AI_PROMPT=mp.MIDPOINT_PROMPT, OPENAI_MODEL='offline-test', GBOP_AI_TOOLS=mp.TOOLS,
            ai_execute_tool=dispatch, ai_client=NS(responses=NS(create=create)), json=json))
        with patch('gbop_voice_web.market_conversation.TEXT_MARKET_CONTEXTS', NS(get=lambda key: context)):
            answer = invoke(20, 'Save 50% as my midpoint label', conversation_id='synthetic')
        self.assertEqual(answer, 'Saved 50%.')
        instructions = create.call_args.kwargs['instructions']
        self.assertIn('midpoint_label=50%', instructions)
        self.assertNotIn('midpoint_label=CE', instructions)
        self.assertEqual(instructions.count('MEMBER MIDPOINT WORDING:'), 1)
        browser = self.dispatch('gbop_voice_web/server.py', 'run_tool', 'OWNER_USER_ID')
        self.assertEqual(browser(20, 'get_midpoint_preference', {})['profile']['midpoint_label'], '50%')

    def test_discord_voice_startup_uses_current_scoped_profile_on_reconnect(self):
        self.save('CE')
        self.save('equilibrium', user=21)
        invoke = function('bot.py', 'instructions', dict(db=self.db, GTOP_GUILD_ID=10,
            get_profile=lambda *args: {}, profile_context=lambda profile: 'risk snapshot',
            market_clock=lambda: 'clock', CANONICAL_KNOWLEDGE='original canon', MARKET_PROMPT='market rules',
            build_voice_instructions=lambda canon, market, state: '\n'.join((canon, market, state))),
            cls='GBOPRealtimeSession')
        a = invoke(NS(member=NS(id=20)))
        b = invoke(NS(member=NS(id=21)))
        self.assertIn('midpoint_label=CE', a)
        self.assertNotIn('midpoint_label=equilibrium', a)
        self.assertIn('midpoint_label=equilibrium', b)
        self.assertNotIn('midpoint_label=CE', b)
        self.save('50%')
        resumed = invoke(NS(member=NS(id=20)))
        self.assertIn('midpoint_label=50%', resumed)
        self.assertNotIn('midpoint_label=CE', resumed)


class TransportContracts(unittest.IsolatedAsyncioTestCase):
    def test_recovery_can_read_preferences_but_cannot_save_them(self):
        from gbop_voice_web.voice_runtime import READ_ONLY_RECOVERY_NAMES, RECOVERY_NAMES, recovery_options
        self.assertIn('get_midpoint_preference', READ_ONLY_RECOVERY_NAMES)
        self.assertNotIn('save_midpoint_preference', RECOVERY_NAMES)
        options = recovery_options(NS(recovery_tools=mp.TOOLS))
        self.assertEqual([tool['name'] for tool in options['tools']], ['get_midpoint_preference'])

    def test_shared_tool_schema_and_prompt_contracts(self):
        self.assertEqual(mp.TOOLS[1]['parameters']['properties']['midpoint_label']['enum'], list(mp.LABELS))
        for tool in mp.TOOLS:
            self.assertFalse(tool['parameters']['additionalProperties'])
            self.assertNotIn('user_id', tool['parameters']['properties'])
            self.assertNotIn('guild_id', tool['parameters']['properties'])
        self.assertIn(mp.INTRODUCTION, mp.MIDPOINT_PROMPT)
        self.assertIn(mp.QUESTION, mp.MIDPOINT_PROMPT)
        for phrase in ('at the first relevant midpoint mention', 'do not repeat the question',
                       'never repeat onboarding', 'only accepted labels', 'wording only',
                       'prices', 'classifications', 'get_midpoint_preference before each relevant answer'):
            self.assertIn(phrase, mp.MIDPOINT_PROMPT)
        self.assertIn('delegate to the backend', mp.LIVE_MIDPOINT_PROMPT)
        self.assertIn('same current member preference', mp.LIVE_MIDPOINT_PROMPT)
        for path, collection, prompt in [('bot.py', 'GBOP_AI_TOOLS', 'GTOP_AI_PROMPT'),
                                         ('gbop_voice_web/server.py', 'TOOLS', 'BACKEND_PROMPT')]:
            source = (ROOT / path).read_text()
            self.assertIn(collection + '.extend(MIDPOINT_TOOLS)', source)
            self.assertIn('+ MIDPOINT_PROMPT', source)
            self.assertIn('bind_preference_args(market_context, name, values, market_generation)', source)
        self.assertIn('+ LIVE_MIDPOINT_PROMPT', (ROOT / 'gbop_voice_web/server.py').read_text())

    async def test_discord_live_success_refreshes_current_session_before_next_reply(self):
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        result = {'ok': True, 'saved': True, 'profile': {'configured': True, 'midpoint_label': '50%'}}
        session = NS(member=NS(id=20), send_event=AsyncMock(), refresh_context=AsyncMock())
        invoke = function('bot.py', 'execute_tool', dict(json=json, time=time,
            asyncio=NS(to_thread=offload), ai_execute_tool=Mock(return_value=result),
            voice_tool_payload=voice_tool_payload), cls='GBOPRealtimeSession')
        await invoke(session, {'name': 'save_midpoint_preference', 'call_id': 'preference-1',
                               'arguments': '{"midpoint_label":"50%","confirmed":true}'})
        session.refresh_context.assert_awaited_once()
        self.assertTrue(session.tool_output_pending)
        output = json.loads(session.send_event.await_args.args[0]['item']['output'])
        self.assertEqual(output['profile']['midpoint_label'], '50%')

    async def test_discord_live_failed_save_does_not_claim_or_refresh(self):
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        session = NS(member=NS(id=20), send_event=AsyncMock(), refresh_context=AsyncMock())
        invoke = function('bot.py', 'execute_tool', dict(json=json, time=time,
            asyncio=NS(to_thread=offload), ai_execute_tool=Mock(return_value={'ok': False, 'error': 'Choice required'}),
            voice_tool_payload=voice_tool_payload), cls='GBOPRealtimeSession')
        await invoke(session, {'name': 'save_midpoint_preference', 'call_id': 'preference-2', 'arguments': '{}'})
        session.refresh_context.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
