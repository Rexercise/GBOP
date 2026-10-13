"""SS market-report-first voice contract; synthetic SQLite and no provider calls."""
import ast
import contextlib
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from gbop_voice_web import member_intelligence as intel
from gbop_voice_web import weekly_structure as ss
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_conversation import contextual_tools
from gbop_voice_web.market_data import MARKET_PROMPT
from gbop_voice_web.midpoint_preferences import MIDPOINT_PROMPT
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_policy import build_voice_instructions
from gbop_voice_web.voice_runtime import VOICE_TRUNCATION

ROOT = Path(__file__).resolve().parents[1]


def voice_method(name, namespace):
    tree = ast.parse((ROOT / 'bot.py').read_text())
    cls = next(n for n in tree.body if getattr(n, 'name', '') == 'GBOPRealtimeSession')
    node = next(n for n in cls.body if getattr(n, 'name', '') == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), namespace)
    return namespace[name]


class SSVoiceRoutingTests(unittest.TestCase):
    def test_real_voice_instructions_require_market_report_before_reflection(self):
        prompt = build_voice_instructions(CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile')
        self.assertIn('get_weekly_structure_study FIRST, get_ss_review THEN', prompt)
        self.assertIn('returned asset/week/version', prompt)
        self.assertIn('supplied answers only', prompt)
        self.assertEqual(prompt.count(CANONICAL_KNOWLEDGE), 1)
        self.assertEqual(prompt.count(MARKET_PROMPT), 1)
        self.assertLess(len(prompt), 51000)
        self.assertLess(len(prompt + '\n\n' + MIDPOINT_PROMPT), 54000)

    def test_session_wire_retains_latest_nulls_and_missing_reflection_distinction(self):
        tools = contextual_tools(intel.INTELLIGENCE_TOOLS)
        session = NS(conversation_tools=tools, instructions=lambda: build_voice_instructions(
            CANONICAL_KNOWLEDGE, MARKET_PROMPT, 'profile'))
        namespace = dict(VOICE_TRUNCATION=VOICE_TRUNCATION, GBOP_REALTIME_MODEL='test-model',
                         GBOP_REALTIME_VOICE='test-voice', GBOP_REALTIME_MAX_OUTPUT_TOKENS=1400,
                         GBOP_VAD_EAGERNESS='high', GBOP_AI_TOOLS=tools)
        wire = voice_method('session_update', namespace)(session)['session']
        by_name = {tool['name']: tool for tool in wire['tools']}
        market = by_name['get_weekly_structure_study']
        reflection = by_name['get_ss_review']
        self.assertIn('week_start=null', market['description'])
        self.assertIn('report_version=null', market['description'])
        self.assertIn('Exact Monday reporting key', market['parameters']['properties']['week_start']['description'])
        self.assertIn('Missing reflection does not mean missing market data', reflection['description'])
        self.assertIn('get_weekly_structure_study FIRST', reflection['description'])
        self.assertIn('returned asset/week_start/report_version', reflection['description'])
        self.assertIn('get_weekly_structure_study FIRST, get_ss_review THEN', wire['instructions'])

    def test_primary_and_helper_share_the_same_session_path(self):
        source = (ROOT / 'bot.py').read_text()
        self.assertIn('GBOP_VOICE_CLIENTS = [client]', source)
        self.assertIn('GBOP_VOICE_CLIENTS.append(discord.Client(intents=intents))', source)
        self.assertIn('pick_voice_guild(GBOP_VOICE_CLIENTS, member.guild.id, channel.id)', source)
        self.assertIn('session = GBOPRealtimeSession(member, voice_client, loop)', source)
        self.assertIn('self.conversation_tools = contextual_tools(GBOP_AI_TOOLS)', source)
        self.assertIn('GBOP_AI_TOOLS.extend(INTELLIGENCE_TOOLS)', source)

    def test_text_and_browser_keep_the_same_tool_contract(self):
        for path, registry in [('bot.py', 'GBOP_AI_TOOLS'), ('gbop_voice_web/server.py', 'TOOLS')]:
            source = (ROOT / path).read_text()
            self.assertIn(registry + '.extend(INTELLIGENCE_TOOLS)', source)
            self.assertIn('+ "\\n\\n" + INTELLIGENCE_PROMPT', source)
            self.assertIn('return intelligence_tool(db, GTOP_GUILD_ID, user_id, name, args)', source)

    def test_contextual_schema_does_not_change_ss_scope_or_add_member_selector(self):
        originals = {tool['name']: tool for tool in intel.INTELLIGENCE_TOOLS}
        copied = {tool['name']: tool for tool in contextual_tools(intel.INTELLIGENCE_TOOLS)}
        for name in ('get_weekly_structure_study', 'get_ss_review'):
            self.assertEqual(copied[name], originals[name])
            self.assertNotIn('user_id', copied[name]['parameters']['properties'])
            self.assertNotIn('guild_id', copied[name]['parameters']['properties'])


class SSReportReflectionTests(unittest.TestCase):
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
        @contextlib.contextmanager
        def db():
            yield self.conn
        self.db = db
        self.now = int(datetime.fromisoformat('2026-10-12T13:00:00+00:00').timestamp())
        window = ss.week_window('2026-10-05')
        # Deliberately incomplete synthetic candles keep the coverage caveat live.
        bars = [dict(time=window['start_epoch'] + 3600, open=100, high=110, low=90, close=102)]
        with patch('gbop_voice_web.market_data.read_feed', return_value={'ok': True, 'symbol': 'synthetic'}), \
                patch('gbop_voice_web.market_data._history_sets', return_value={300: bars}):
            self.report = ss.build_report(self.db, 'XAUUSD', '2026-10-05', now=self.now)
        self.report['report_revision'] = 1
        self.conn.execute('INSERT INTO gbop_ss_reports VALUES (?,?,?,?,?,?)', (
            'XAUUSD', '2026-10-05', self.report['report_version'], 1,
            self.report['generated_at_utc'], json.dumps(self.report)))
        self.conn.commit()
        self.init = patch.object(intel, 'init_intelligence')
        self.init.start()
        self.addCleanup(self.init.stop)

    def call(self, name, args, guild=10, user=20):
        with patch.object(ss.clock, 'time', return_value=self.now):
            return intel.intelligence_tool(self.db, guild, user, name, args)

    def latest(self, **kwargs):
        return self.call('get_weekly_structure_study',
                         dict(asset='Gold', week_start=None, report_version=None), **kwargs)

    def reflect(self, report, **kwargs):
        return self.call('get_ss_review', {key: report[key] for key in
                         ('asset', 'week_start', 'report_version')}, **kwargs)

    def contribute(self, guild, user, text, version=None):
        self.conn.execute('INSERT INTO gbop_ss_contributions VALUES (?,?,?,?,?,?,?,?)', (
            guild, user, 'XAUUSD', '2026-10-05', version or self.report['report_version'],
            1, json.dumps({'answers': {'structural_summary': text}}), '2026-10-11T12:00:00Z'))
        self.conn.commit()

    def test_latest_completed_market_facts_survive_empty_personal_reflection(self):
        self.conn.execute('PRAGMA query_only=ON')
        report = self.latest()
        self.assertTrue(report['ok'])
        self.assertEqual(report['week_start'], '2026-10-05')
        self.assertEqual(report['asset'], 'XAUUSD')
        reflection = self.reflect(report)
        self.assertTrue(reflection['ok'])
        self.assertIsNone(reflection['review'])
        self.assertEqual(report['high']['price'], 110)
        self.assertEqual(report['low']['price'], 90)
        wire = voice_tool_payload('get_weekly_structure_study', report)
        for key in ('window', 'coverage', 'high', 'low', 'report_version'):
            self.assertEqual(wire[key], self.report[key])
        self.assertEqual(wire['coverage']['status'], 'unverified_intervals')
        self.assertFalse(wire['high']['definitive_weekly_extreme'])

    def test_exact_report_reflection_is_member_and_guild_isolated(self):
        self.contribute(10, 20, 'member A')
        self.contribute(10, 30, 'member B')
        self.contribute(11, 20, 'other guild')
        self.conn.execute('PRAGMA query_only=ON')
        for guild, user, expected in ((10, 20, 'member A'), (10, 30, 'member B'), (11, 20, 'other guild')):
            report = self.latest(guild=guild, user=user)
            reflection = self.reflect(report, guild=guild, user=user)
            self.assertEqual(reflection['review']['structural_summary'], expected)
            serialized = json.dumps((report, reflection))
            for foreign in {'member A', 'member B', 'other guild'} - {expected}:
                self.assertNotIn(foreign, serialized)
        self.assertIsNone(self.reflect(self.latest(user=40), user=40)['review'])

    def test_missing_exact_week_or_version_never_substitutes_latest(self):
        for args, status in ((dict(asset='Gold', week_start='2026-09-28', report_version=None), 'report_not_prepared'),
                             (dict(asset='Gold', week_start='2026-10-05', report_version='unknown'), 'report_version_not_found')):
            result = self.call('get_weekly_structure_study', args)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], status)
            self.assertNotIn('high', result)

    def test_older_version_reflection_is_not_attached_to_current_report(self):
        self.contribute(10, 20, 'old reflection', version='earlier-version')
        report = self.latest()
        self.assertEqual(report['member_contributions'], [])
        self.assertIsNone(self.reflect(report)['review'])

    def test_missing_asset_defaults_to_nas_without_substituting_gold(self):
        with self.db() as conn:
            conn.execute('CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until INT,last_tick INT,state TEXT)')
        result = self.call('get_weekly_structure_study', dict(asset=None, week_start=None, report_version=None))
        self.assertEqual(result['asset'], 'NAS100')
        self.assertEqual(result['status'], 'report_not_prepared')
        self.assertNotIn('XAUUSD', json.dumps(result))
        self.assertNotIn('review', result)


if __name__ == '__main__':
    unittest.main()
