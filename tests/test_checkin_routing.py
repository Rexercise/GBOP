"""Check-in routing regressions with synthetic SQLite and fake Discord only."""
import ast
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.checkin_routing import save_checkin_reply


NOW = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)
BTC_REQUEST = "let me know about any purges during btc's current shift"
SOURCE = Path(__file__).resolve().parents[1] / 'bot.py'


class CheckinRoutingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE post_shift_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift TEXT NOT NULL,
                prompt_sent_at TEXT NOT NULL,
                response TEXT,
                responded_at TEXT,
                UNIQUE(guild_id, user_id, shift_date, shift)
            );
            CREATE TABLE gbop_shift_deliveries (
                event_key TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                discord_message_id TEXT,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY(event_key, guild_id, user_id)
            );
        ''')

    @contextlib.contextmanager
    def db(self):
        with self.conn:
            yield self.conn

    def checkin(self, *, guild=1, user=20, day='2026-10-03', shift='day',
                sent=None, response=None):
        sent = (NOW - timedelta(hours=1)).isoformat() if sent is None else sent
        return self.conn.execute('''
            INSERT INTO post_shift_checkins
                (guild_id, user_id, shift_date, shift, prompt_sent_at, response)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (guild, user, day, shift, sent, response)).lastrowid

    def delivery(self, message_id='500', *, guild=1, user=20,
                 event='2026-10-03:day_formation'):
        self.conn.execute('''
            INSERT INTO gbop_shift_deliveries
                (event_key, guild_id, user_id, discord_message_id, delivered_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (event, guild, user, message_id, NOW.isoformat()))

    def save(self, content, *, reply=None, guild=1, user=20):
        return save_checkin_reply(self.db, guild, user, content,
                                  reply_message_id=reply, now_utc=NOW)

    def row(self, row_id):
        return dict(self.conn.execute(
            'SELECT * FROM post_shift_checkins WHERE id=?', (row_id,)).fetchone())

    def snapshot(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM post_shift_checkins ORDER BY id')]

    def assert_passes_through(self, content, **kwargs):
        before = self.snapshot()
        self.assertIsNone(self.save(content, **kwargs), content)
        self.assertEqual(self.snapshot(), before, content)

    def test_exact_btc_request_does_not_consume_multiple_pending_or_old_rows(self):
        self.checkin(day='2026-09-01', sent='2026-09-01T17:00:00+00:00')
        self.checkin(day='2026-10-02', shift='night',
                     sent=(NOW - timedelta(hours=23)).isoformat())
        self.checkin()
        self.delivery()
        self.assert_passes_through(BTC_REQUEST)
        self.assert_passes_through(BTC_REQUEST, reply=500)

    def test_random_plain_answers_are_not_captured(self):
        self.checkin()
        for content in ('yes', 'I stayed patient and followed the plan.',
                        'Thanks!', 'I traded once today.', 'check-in went well'):
            with self.subTest(content=content):
                self.assert_passes_through(content)

    def test_full_multisentence_reflection_is_saved_verbatim(self):
        target = self.checkin()
        self.delivery()
        reflection = (
            'I followed my plan and personal risk protocol. I waited for my A+ setup. '
            'I felt impatient after the first move, but I did not chase it.\n\n'
            'The useful pattern was that a pause helped me avoid unnecessary entries. '
            'My next shift adjustment is to leave more time for that pause. '
            'I want this whole reflection retained, including the final sentence.'
        )
        saved = self.save(reflection, reply=500)
        self.assertEqual(saved['response'], reflection)
        self.assertEqual(self.row(target)['response'], reflection)

    def test_prefixed_full_reflection_keeps_paragraphs_and_member_scope(self):
        target = self.checkin()
        other = self.checkin(user=30)
        reflection = 'I respected my limits. I stayed patient.\n\nI noticed FOMO and sat out.'
        saved = self.save('Check-in: ' + reflection)
        self.assertEqual(saved['response'], reflection)
        self.assertEqual(self.row(target)['response'], reflection)
        self.assertIsNone(self.row(other)['response'])

    def test_prompt_welcomes_brief_or_full_reflection_without_format_mandate(self):
        assignments = {node.targets[0].id: ast.literal_eval(node.value)
                       for node in ast.parse(SOURCE.read_text()).body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in ('GBOP_CHECKIN_PROMPT', 'GBOP_CHECKIN_REPLY_HINT')}
        prompt = assignments['GBOP_CHECKIN_PROMPT']
        self.assertIn('brief or fuller reflection', prompt)
        self.assertIn('no sentence limit', prompt)
        for constraint in ('one message', 'one sentence', 'single sentence'):
            self.assertNotIn(constraint, prompt.lower())
        hint = assignments['GBOP_CHECKIN_REPLY_HINT']
        for guard in ('within 24 hours', 'Discord Reply', 'Check-in:', 'normal conversations'):
            self.assertIn(guard, hint)

    def test_matching_day_prompt_saves_only_the_exact_older_checkin(self):
        target = self.checkin(day='2026-10-02',
                              sent=(NOW - timedelta(hours=2)).isoformat())
        newer = self.checkin()
        self.delivery(event='2026-10-02:day_formation')
        saved = self.save('  I waited for confirmation.  ', reply=500)
        self.assertEqual(saved['id'], target)
        self.assertEqual(saved['response'], 'I waited for confirmation.')
        self.assertEqual(self.row(target)['response'], saved['response'])
        self.assertIsNotNone(self.row(target)['responded_at'])
        self.assertIsNone(self.row(newer)['response'])

    def test_night_prompt_event_is_on_the_day_after_shift_date(self):
        target = self.checkin(day='2026-10-02', shift='night')
        self.delivery('501', event='2026-10-03:night_formation')
        saved = self.save('I followed my risk limit.', reply='501')
        self.assertEqual(saved['id'], target)

    def test_same_date_night_event_cannot_match_previous_nights_checkin(self):
        self.checkin(day='2026-10-02', shift='night')
        self.delivery(event='2026-10-02:night_formation')
        self.assert_passes_through('I followed my risk limit.', reply=500)

    def test_unrelated_delivery_events_are_not_checkin_prompts(self):
        self.checkin()
        for number, event in enumerate(('2026-10-03:day_pre_shift',
                                        '2026-10-03:night_formation',
                                        '2026-10-04:day_formation',
                                        '2026-10-03:day_snapshot'), start=500):
            self.delivery(str(number), event=event)
            with self.subTest(event=event):
                self.assert_passes_through('I followed my risk limit.', reply=number)

    def test_unknown_missing_or_empty_prompt_id_never_matches(self):
        self.checkin()
        self.delivery('')
        for reply in (None, '', 0, 'missing', 999):
            with self.subTest(reply=reply):
                self.assert_passes_through('I stayed patient.', reply=reply)

    def test_invalid_supplied_prompt_ids_fail_closed_even_with_prefix(self):
        self.checkin()
        self.delivery()
        for reply in ('', ' ', 0, -1, True, False, '500.0', 'not-an-id'):
            with self.subTest(reply=reply):
                self.assert_passes_through('check-in: I stayed patient.', reply=reply)

    def test_other_members_and_guilds_prompt_ids_do_not_match(self):
        self.checkin()
        self.checkin(user=21)
        self.checkin(guild=2)
        self.delivery('501', user=21)
        self.delivery('502', guild=2)
        for reply in ('501', '502'):
            with self.subTest(reply=reply):
                self.assert_passes_through('I stayed patient.', reply=reply)

    def test_explicit_prefix_with_a_reply_id_cannot_retarget_an_unrelated_prompt(self):
        self.checkin()
        self.delivery()
        self.assert_passes_through('check-in: I stayed patient.', reply=999)

    def test_explicit_prefix_variants_strip_marker_and_whitespace(self):
        target = self.checkin()
        for prefix in ('check-in:', 'check in:', 'checkin:', 'CHECK-IN:'):
            with self.subTest(prefix=prefix):
                self.conn.execute('UPDATE post_shift_checkins SET response=NULL, responded_at=NULL')
                saved = self.save('  ' + prefix + '  I stayed patient. \n')
                self.assertEqual(saved['id'], target)
                self.assertEqual(saved['response'], 'I stayed patient.')

    def test_explicit_prefix_selects_latest_eligible_prompt_not_largest_id(self):
        target = self.checkin(sent=(NOW - timedelta(minutes=15)).isoformat())
        older = self.checkin(day='2026-10-02',
                             sent=(NOW - timedelta(hours=2)).isoformat())
        stale = self.checkin(day='2026-09-01', sent='2026-09-01T17:00:00+00:00')
        future = self.checkin(day='2026-10-04', sent=(NOW + timedelta(hours=1)).isoformat())
        invalid = self.checkin(day='2026-10-05', sent='bad timestamp')
        saved = self.save('check-in: I stayed patient.')
        self.assertEqual(saved['id'], target)
        for row_id in (older, stale, future, invalid):
            self.assertIsNone(self.row(row_id)['response'])

    def test_explicit_prefix_never_crosses_member_or_guild_scope(self):
        target = self.checkin(sent=(NOW - timedelta(hours=2)).isoformat())
        other_member = self.checkin(user=21)
        other_guild = self.checkin(guild=2)
        saved = self.save('check-in: I stayed patient.')
        self.assertEqual(saved['id'], target)
        self.assertIsNone(self.row(other_member)['response'])
        self.assertIsNone(self.row(other_guild)['response'])

    def test_no_eligible_checkin_is_not_created_implicitly(self):
        self.assert_passes_through('check-in: I stayed patient.')
        self.assertEqual(self.snapshot(), [])

    def test_empty_answers_never_save(self):
        self.checkin()
        self.delivery()
        for content in ('', ' \n ', 'check-in:', 'check in:  ', 'checkin:\n'):
            for reply in (None, 500):
                with self.subTest(content=content, reply=reply):
                    self.assert_passes_through(content, reply=reply)

    def test_stale_future_naive_and_invalid_prompt_timestamps_fail_closed(self):
        target = self.checkin()
        self.delivery()
        timestamps = (
            (NOW - timedelta(hours=24, microseconds=1)).isoformat(),
            (NOW + timedelta(microseconds=1)).isoformat(),
            '2026-10-04T02:00:00', '2026-10-03', '', 'not a timestamp',
            '2026-99-99T02:00:00+00:00',
        )
        for sent in timestamps:
            self.conn.execute('UPDATE post_shift_checkins SET prompt_sent_at=? WHERE id=?',
                              (sent, target))
            with self.subTest(sent=sent):
                self.assert_passes_through('check-in: I stayed patient.')
                self.assert_passes_through('I stayed patient.', reply=500)

    def test_malformed_shift_metadata_fails_closed(self):
        target = self.checkin()
        for day, shift in (('9999-12-31', 'night'), ('not-a-date', 'day'),
                           ('2026-02-30', 'day'), ('2026-10-03', 'unknown')):
            self.conn.execute('UPDATE post_shift_checkins SET shift_date=?, shift=? WHERE id=?',
                              (day, shift, target))
            with self.subTest(day=day, shift=shift):
                self.assert_passes_through('check-in: I stayed patient.')

    def test_exact_24_hour_boundary_and_aware_offsets_are_eligible(self):
        target = self.checkin()
        for sent in ((NOW - timedelta(hours=24)).isoformat(),
                     NOW.isoformat(), '2026-10-03T22:00:00-04:00',
                     '2026-10-04T02:00:00Z'):
            self.conn.execute('''
                UPDATE post_shift_checkins
                SET response=NULL, responded_at=NULL, prompt_sent_at=? WHERE id=?
            ''', (sent, target))
            with self.subTest(sent=sent):
                saved = self.save('check-in: I stayed patient.')
                self.assertEqual(saved['id'], target)

    def test_existing_answers_are_never_overwritten(self):
        target = self.checkin(response='Original response')
        self.delivery()
        self.assert_passes_through('check-in: Replacement response')
        self.assert_passes_through('Replacement response', reply=500)
        self.assertEqual(self.row(target)['response'], 'Original response')

    def test_empty_but_nonnull_existing_answer_is_not_unanswered(self):
        self.checkin(response='')
        self.assert_passes_through('check-in: Replacement response')

    def test_duplicate_reply_does_not_fall_back_to_other_pending_checkin(self):
        target = self.checkin()
        older = self.checkin(day='2026-10-02', sent=(NOW - timedelta(hours=2)).isoformat())
        self.delivery()
        saved = self.save('I stayed patient.', reply=500)
        self.assertEqual(saved['id'], target)
        self.assert_passes_through('A duplicate reply.', reply=500)
        self.assertIsNone(self.row(older)['response'])

    def test_operational_requests_and_questions_stay_in_normal_conversation(self):
        self.checkin()
        self.delivery()
        requests = (
            BTC_REQUEST,
            'Watch BTC for purges during the current shift.',
            'Notify me when BTC purges.',
            'Please send my last journal.',
            'Show my recent trades.',
            'Get the current BTC market review.',
            'Open a trade on BTC.',
            'Close my trade.',
            'Journal this trade.',
            'Remind me when BTC purges.',
            'Ping me when BTC purges.',
            'Keep an eye on BTC during this shift.',
            'What happened during the night shift?',
            'Did BTC purge yet?',
        )
        for content in requests:
            with self.subTest(content=content):
                self.conn.execute('UPDATE post_shift_checkins SET response=NULL, responded_at=NULL')
                self.assert_passes_through(content)
                self.assert_passes_through(content, reply=500)

    def test_reflections_with_trading_vocabulary_are_still_valid_prompt_answers(self):
        target = self.checkin()
        self.delivery()
        for content in ('I watched BTC and stayed patient.',
                        'I journaled my trade and followed my risk limit.',
                        'I got confirmation before taking one trade.'):
            with self.subTest(content=content):
                self.conn.execute('UPDATE post_shift_checkins SET response=NULL, responded_at=NULL')
                saved = self.save(content, reply=500)
                self.assertEqual(saved['id'], target)
                self.assertEqual(saved['response'], content)

    def test_mixed_and_indirect_operational_requests_are_not_saved_as_reflections(self):
        self.checkin()
        self.delivery()
        requests = (
            'I stayed patient, send my last journal.',
            'I followed the plan\nshow my recent trades.',
            'I stayed patient; get the current BTC review.',
            'I followed the plan and open a trade on BTC.',
            'I stayed patient also journal this trade.',
            'I followed my rules then review BTC.',
            'I stayed patient but close my trade.',
            'For the next shift please notify me of purges.',
            'I need you to send my recent journal.',
            'I want to watch BTC for purges.',
            'I want to monitor BTC tonight.',
            'I need an alert when BTC purges.',
            'I want to cancel the BTC watch.',
            'I stayed patient. What is BTC doing now',
            'I respected risk\nHow many trades did I take',
            'I followed my plan. Remind me when BTC purges.',
            'I stayed patient. Ping me when BTC purges.',
            'I followed my plan. Keep an eye on BTC.',
        )
        for content in requests:
            with self.subTest(content=content):
                self.conn.execute('UPDATE post_shift_checkins SET response=NULL, responded_at=NULL')
                self.assert_passes_through(content, reply=500)
                self.assert_passes_through('check-in: ' + content)

    def test_response_saved_between_selection_and_update_wins_compare_and_set(self):
        target = self.checkin()
        conn = self.conn
        raced = []

        class RacingConnection:
            def __getattr__(self, name):
                return getattr(conn, name)

            def execute(self, sql, params=()):
                if 'UPDATE POST_SHIFT_CHECKINS' in ' '.join(sql.upper().split()) and not raced:
                    raced.append(True)
                    conn.execute('''UPDATE post_shift_checkins
                        SET response=?, responded_at=? WHERE id=?''',
                        ('Concurrent answer', NOW.isoformat(), target))
                return conn.execute(sql, params)

        @contextlib.contextmanager
        def racing_db():
            with conn:
                yield RacingConnection()

        saved = save_checkin_reply(racing_db, 1, 20, 'check-in: Late answer', now_utc=NOW)
        self.assertTrue(raced, 'The save must attempt an atomic conditional UPDATE.')
        self.assertIsNone(saved)
        self.assertEqual(self.row(target)['response'], 'Concurrent answer')


class CheckinMessageDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Execute only on_message's AST; never import the live bot or connect to Discord."""

    def fixture(self, *, member_exists=True, role=True, activated=True, revoked=False,
                consumes=False):
        source = ast.parse(SOURCE.read_text())
        node = next(n for n in source.body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'on_message')
        node.decorator_list = []
        member = NS(id=20)
        message = NS(id=700, author=NS(id=20, bot=False), guild=None,
                     mentions=[], attachments=[], content=BTC_REQUEST,
                     reply=AsyncMock(), channel=NS(id=77, typing=AsyncMock))
        ns = dict(
            discord=NS(Message=object), asyncio=asyncio,
            GBOP_PRIVATE_ROOMS=NS(message_seen=Mock()), client=NS(user=NS(id=999)),
            ai_resolve_member=AsyncMock(return_value=member if member_exists else None),
            ensure_member_record=Mock(),
            get_member_record=Mock(return_value={'revoked': revoked, 'activated': activated}),
            has_member_role=Mock(return_value=role), is_owner=Mock(return_value=False),
            _consume_checkin_reply=AsyncMock(return_value=consumes),
            AI_TEXT_LOCKS={}, ai_run_turn=Mock(return_value='Watch request reached normal dispatch.'),
            ai_save_message=Mock(), GTOP_GUILD_ID=1,
            db=Mock(), GTOP_OWNER_USER_ID=999, member_access_error=Mock(return_value=None),
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
        context = NS(generation=4, complete_response=Mock())
        market_module = ModuleType('gbop_voice_web.market_conversation')
        market_module.TEXT_MARKET_CONTEXTS = NS(get=Mock(return_value=context))
        return message, ns, market_module

    async def test_access_checks_block_checkin_storage_before_any_consume_attempt(self):
        for options in ({'member_exists': False}, {'role': False},
                        {'activated': False}, {'revoked': True}):
            with self.subTest(options=options):
                message, ns, _ = self.fixture(consumes=True, **options)
                await ns['on_message'](message)
                ns['_consume_checkin_reply'].assert_not_awaited()
                ns['ai_run_turn'].assert_not_called()
                message.reply.assert_awaited_once()

    async def test_current_access_denial_blocks_checkin_storage(self):
        message, ns, _ = self.fixture(consumes=True)
        ns['member_access_error'].return_value = 'Your GBOP access is currently revoked.'
        await ns['on_message'](message)
        ns['member_access_error'].assert_called_once_with(ns['db'], 1, 20, 999)
        ns['_consume_checkin_reply'].assert_not_awaited()
        ns['ai_run_turn'].assert_not_called()
        message.reply.assert_awaited_once_with('Your GBOP access is currently revoked.')

    async def test_passed_through_btc_watch_request_reaches_normal_ai_dispatch(self):
        message, ns, market_module = self.fixture()
        with patch.dict(sys.modules, {'gbop_voice_web.market_conversation': market_module}):
            await ns['on_message'](message)
        ns['_consume_checkin_reply'].assert_awaited_once_with(message)
        ns['ai_run_turn'].assert_called_once_with(20, BTC_REQUEST, [], 77)
        ns['ai_save_message'].assert_any_call(20, 'user', BTC_REQUEST)
        message.reply.assert_awaited_once_with('Watch request reached normal dispatch.')

    async def test_access_revoked_between_dispatch_and_consumer_stops_ai_fallback(self):
        message, ns, _ = self.fixture()
        denial = 'Your GBOP access is currently revoked.'
        ns['member_access_error'].side_effect = [None, denial]
        ns['save_checkin_reply'] = Mock()
        helper = next(n for n in ast.parse(SOURCE.read_text()).body
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == '_consume_checkin_reply')
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), 'exec'), ns)
        await ns['on_message'](message)
        self.assertEqual(ns['member_access_error'].call_count, 2)
        ns['save_checkin_reply'].assert_not_called()
        ns['ai_run_turn'].assert_not_called()
        ns['ai_save_message'].assert_not_called()
        message.reply.assert_awaited_once_with(denial)

    async def test_successful_checkin_does_not_dispatch_a_second_ai_turn(self):
        message, ns, _ = self.fixture(consumes=True)
        await ns['on_message'](message)
        ns['_consume_checkin_reply'].assert_awaited_once_with(message)
        ns['ai_run_turn'].assert_not_called()


class CheckinConsumerTests(unittest.IsolatedAsyncioTestCase):
    """The Discord adapter must preserve routing and save-success boundaries."""

    def fixture(self):
        node = next(n for n in ast.parse(SOURCE.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == '_consume_checkin_reply')
        ns = dict(asyncio=asyncio, db=Mock(), GTOP_GUILD_ID=1, GTOP_OWNER_USER_ID=999,
                  member_access_error=Mock(return_value=None),
                  save_checkin_reply=Mock(return_value=None),
                  save_no_trade_reason=Mock(return_value=None),
                  no_trade_acknowledgment=Mock(return_value=None), record_reason_prompt=Mock(),
                  ingest_checkin_by_id=Mock(), get_member_plan=Mock(return_value={}),
                  logger=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)
        message = NS(guild=None, author=NS(id=20), channel=NS(id=77),
                     reference=NS(message_id=500, channel_id=77),
                     content='I stayed patient.', reply=AsyncMock())
        return message, ns

    async def test_pass_through_never_ingests_coaching_or_claims_saved(self):
        message, ns = self.fixture()
        message.content = BTC_REQUEST
        self.assertFalse(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_called_once_with(ns['db'], 1, 20, BTC_REQUEST,
                                                       reply_message_id=500, source_message_id=None)
        ns['ingest_checkin_by_id'].assert_not_called()
        ns['get_member_plan'].assert_not_called()
        message.reply.assert_not_awaited()

    async def test_success_uses_saved_row_and_refreshes_only_its_members_coaching(self):
        message, ns = self.fixture()
        ns['save_checkin_reply'].return_value = {'id': 42, 'response': message.content}
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['ingest_checkin_by_id'].assert_called_once_with(ns['db'], 1, 20, 42)
        ns['get_member_plan'].assert_called_once_with(ns['db'], 1, 20, {})
        message.reply.assert_awaited_once()
        self.assertTrue(message.reply.await_args.args[0].startswith('Saved.'))

    async def test_reference_in_another_channel_never_reaches_storage(self):
        message, ns = self.fixture()
        message.reference.channel_id = 88
        self.assertFalse(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_not_called()
        message.reply.assert_not_awaited()

    async def test_reference_without_message_id_does_not_fall_back_to_prefix(self):
        message, ns = self.fixture()
        message.reference = NS(channel_id=77)
        message.content = 'check-in: I stayed patient.'
        self.assertFalse(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_not_called()
        message.reply.assert_not_awaited()

    async def test_access_denied_after_dispatch_is_handled_without_storage_or_fallback(self):
        message, ns = self.fixture()
        ns['member_access_error'].return_value = 'Access revoked.'
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_not_called()
        message.reply.assert_awaited_once_with('Access revoked.')

    async def test_server_message_is_not_a_private_checkin_answer(self):
        message, ns = self.fixture()
        message.guild = NS(id=1)
        self.assertFalse(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
