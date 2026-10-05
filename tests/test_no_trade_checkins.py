"""Synthetic check-ins only: no Discord connection or live journal writes."""
import ast
import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web import no_trade_checkins as nt
from gbop_voice_web.checkin_routing import save_checkin_reply
from gbop_voice_web.member_intelligence import parse_behavior_text
from gbop_voice_web import snapshots
from tests import test_checkin_routing as routing
from tests import test_snapshots as snapshot_fixtures


class ClassificationTests(unittest.TestCase):
    def test_current_shift_and_contractions(self):
        for text in ("I didn't trade this shift", "didnt trade this shift", "I didn’t trade this shift",
                     "I didn't take a trade this shift", "I haven't taken any trades this shift",
                     'No trades during this shift', 'I did not trade', 'No trades.', '0 trades this shift', 'zero trades',
                     'I took no trades this shift', 'I did not take any trades this shift',
                     "I haven't traded this shift", 'I sat out this shift', 'No trades tonight!'):
            with self.subTest(text=text):
                report = nt.no_trade_report(text)
                self.assertIsNotNone(report)
                self.assertIsNone(report['reason'])
                self.assertEqual(parse_behavior_text(text), [])

    def test_existing_reason_is_preserved_and_is_not_a_score(self):
        for text, kind in (
            ("I didn't trade this shift because no setup met my criteria", 'no_qualifying_setup'),
            ("I didn't trade this shift. I was away", 'away'),
            ('No trades, I was at work', 'away'),
            ('No trades because my internet failed', 'other'),
            ('No trades because I had a headache', 'other'),
            ('No trades because I had a family emergency', 'other'),
            ('No trades because I made dinner instead', 'other'),
            ('No trades because I took a break', 'other'),
            ('No trades because I opened my laptop too late', 'other'),
            ('No trades; not sure', 'unknown'),
            ('No trades. I did not find an A+ setup', 'no_qualifying_setup'),
            ('No trades because I was not away', 'other'),
            ("No trades because I wasn't at work", 'other'),
            ('No trades' + nt.REASON_MARKER + 'I was away', 'away'),
        ):
            with self.subTest(text=text):
                self.assertEqual(nt.no_trade_report(text)['reason'], kind)
                self.assertEqual(parse_behavior_text(text), [])

    def test_mixed_trades_other_periods_and_hypotheticals_do_not_become_no_trade(self):
        for text in ('I traded once today', 'No boredom trades', 'No trades met my criteria',
                     "I didn't trade yesterday", "I didn't trade gold", 'I will not trade this shift',
                     "If I didn't trade this shift, would that be fine?", "I didn't trade this shift?",
                     "I didn't trade this shift but I took a long", 'No trades. I entered one short.',
                     'No trades, sold gold once', "I didn't trade last shift", 'No trades tomorrow',
                     'No trades' + nt.REASON_MARKER + 'I took a trade',
                     'No trades. Actually I made one FOMO trade.',
                     'I didn’t trade this shift, except for one scalp.',
                     'No trades. I opened a long and lost 1R.',
                     'No trades. I took 10 revenge trades.',
                     'No trades. Actually, one scalp.',
                     'No trades apart from a scalp', 'No trades, I did trade NAS though',
                     'I never trade', 'I was away', "He didn't trade this shift"):
            with self.subTest(text=text):
                self.assertIsNone(nt.no_trade_report(text))

    def test_explicit_opposite_shift_does_not_match_target(self):
        self.assertIsNone(nt.no_trade_report('No trades tonight', shift='day'))
        self.assertIsNone(nt.no_trade_report('No trades this day shift', shift='night'))
        self.assertIsNotNone(nt.no_trade_report('No trades this night shift', shift='night'))

    def test_ordinary_explicit_trade_behavior_still_scores(self):
        self.assertTrue(parse_behavior_text('I took a FOMO trade'))
        self.assertTrue(parse_behavior_text('I followed my plan'))
        self.assertTrue(parse_behavior_text('No trades. Actually I made one FOMO trade.'))
        self.assertTrue(parse_behavior_text('No trades. I took 10 revenge trades.'))


class ReasonStorageTests(unittest.TestCase):
    def setUp(self):
        fixture = routing.CheckinRoutingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.conn, self.db = fixture.conn, fixture.db
        self.checkin, self.row, self.delivery = fixture.checkin, fixture.row, fixture.delivery
        self.ident = self.checkin()
        self.delivery()
        self.saved = save_checkin_reply(self.db, 1, 20, 'I didn’t trade this shift',
            reply_message_id=500, source_message_id=700, now_utc=routing.NOW)
        self.assertTrue(nt.record_reason_prompt(self.db, 1, 20, self.saved, 800, now_utc=routing.NOW))

    def save(self, text, **overrides):
        args = dict(db=self.db, guild=1, user=20, content=text,
                    reply_message_id=800, now_utc=routing.NOW)
        args.update(overrides)
        return nt.save_no_trade_reason(**args)

    def test_reason_appends_to_exact_same_shift_preserving_original_and_timestamp(self):
        before = self.row(self.ident)
        result = self.save('No setup met my criteria')
        after = self.row(self.ident)
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(result['reason'], 'no_qualifying_setup')
        self.assertEqual(after['response'], before['response'] + nt.REASON_MARKER + 'No setup met my criteria')
        for key in ('id', 'guild_id', 'user_id', 'shift_date', 'shift', 'prompt_sent_at', 'responded_at'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM post_shift_checkins').fetchone()[0], 1)

    def test_reason_reply_provenance_is_retained_separately(self):
        result = self.save('I was away', source_message_id=900)
        receipt = self.conn.execute("SELECT * FROM gbop_shift_deliveries WHERE event_key LIKE 'checkin_no_trade_reason_reply:%'").fetchone()
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(receipt['discord_message_id'], '900')
        self.assertEqual(receipt['delivered_at'], routing.NOW.isoformat())
        self.assertEqual(receipt['guild_id'], 1)
        self.assertEqual(receipt['user_id'], 20)
        self.assertIn(f":{self.ident}:2026-10-03:day", receipt['event_key'])
        self.save('No qualifying setup', source_message_id=901)
        self.assertEqual(self.conn.execute("SELECT discord_message_id FROM gbop_shift_deliveries WHERE event_key LIKE 'checkin_no_trade_reason_reply:%'").fetchone()[0], '900')

    def test_optional_skip_cancel_unknown_away_and_other(self):
        for text, kind, skipped in (('skip', 'unknown', True), ('cancel', 'unknown', True),
                ('stop', 'unknown', True), ("I don’t know", 'unknown', True),
                ('I was away', 'away', False), ('was away', 'away', False),
                ("couldn't find a setup", 'no_qualifying_setup', False),
                ('My internet went out', 'other', False),
                ('I had a headache', 'other', False), ('I made dinner instead', 'other', False),
                ('I had a family emergency', 'other', False), ('I took a break', 'other', False)):
            with self.subTest(text=text):
                self.conn.execute('UPDATE post_shift_checkins SET response=? WHERE id=?',
                                  (self.saved['response'], self.ident))
                result = self.save(text)
                self.assertEqual((result['reason'], result['skipped']), (kind, skipped))

    def test_unrelated_commands_questions_or_actual_trades_pass_through_unchanged(self):
        for text in (routing.BTC_REQUEST, 'show my journal', 'review my last shift',
                     'cancel my BTC watch', 'stop the alerts', 'Why do you ask?',
                     'I was away; also show my risk plan', 'I entered a long',
                     'I traded once actually', 'No setups?', 'thanks', 'okay', 'GBPUSD', 'the current BTC shift',
                     "I was away and I want you to check BTC",
                     'I was away; compare my last two trades',
                     'No setup; please find the current gold range',
                     "I was away and I'd like my journal reviewed",
                     'check-in: I followed my plan'):
            with self.subTest(text=text):
                before = self.row(self.ident)
                self.assertIsNone(self.save(text))
                self.assertEqual(self.row(self.ident), before)

    def test_plain_message_or_wrong_member_guild_message_never_captures_reason(self):
        for override in ({'reply_message_id': None}, {'reply_message_id': 500},
                         {'reply_message_id': 999}, {'reply_message_id': True},
                         {'user': 21}, {'guild': 2}):
            with self.subTest(override=override):
                self.assertIsNone(self.save('I was away', **override))
        self.assertEqual(self.row(self.ident)['response'], self.saved['response'])

    def test_repeated_reason_is_acknowledged_without_overwrite_or_append(self):
        self.save('I was away')
        before = self.row(self.ident)
        for answer in ('I was away', 'no setup met my criteria', 'skip'):
            self.assertEqual(self.save(answer)['status'], 'already_answered')
            self.assertEqual(self.row(self.ident), before)

    def test_durable_mapping_survives_new_helper_calls_without_memory(self):
        self.assertFalse(nt.record_reason_prompt(self.db, 1, 20, self.saved, 801, now_utc=routing.NOW))
        self.assertIsNone(self.save('away', reply_message_id=801))
        self.assertEqual(self.save('away')['status'], 'saved')

    def test_stale_future_and_new_shift_do_not_capture_old_reason(self):
        for now in (routing.NOW + timedelta(hours=24), routing.NOW - timedelta(seconds=1)):
            self.assertIsNone(self.save('away', now_utc=now))
        self.checkin(day='2026-10-03', shift='night', sent=(routing.NOW + timedelta(minutes=1)).isoformat())
        self.assertIsNone(self.save('away', now_utc=routing.NOW + timedelta(minutes=2)))
        self.assertEqual(self.row(self.ident)['response'], self.saved['response'])

    def test_shift_mutation_and_missing_checkin_fail_closed(self):
        self.conn.execute("UPDATE post_shift_checkins SET shift='night' WHERE id=?", (self.ident,))
        self.assertIsNone(self.save('away'))
        self.conn.execute('DELETE FROM post_shift_checkins WHERE id=?', (self.ident,))
        self.assertIsNone(self.save('away'))

    def test_duplicate_initial_discord_message_does_not_consume_older_prompt(self):
        older = self.checkin(day='2026-10-02', sent=(routing.NOW - timedelta(hours=3)).isoformat())
        result = save_checkin_reply(self.db, 1, 20, 'Check-in: I didn’t trade this shift',
                                   source_message_id=700, now_utc=routing.NOW)
        self.assertEqual(result, {'duplicate': True})
        self.assertIsNone(self.row(older)['response'])
        self.assertEqual(self.row(self.ident)['response'], self.saved['response'])

    def test_prompt_registration_requires_saved_original_and_valid_sent_message(self):
        for message_id in (None, '', True, -2, 'bad'):
            self.assertFalse(nt.record_reason_prompt(self.db, 1, 20, self.saved, message_id, now_utc=routing.NOW))
        self.assertFalse(nt.record_reason_prompt(self.db, 1, 21, self.saved, 899, now_utc=routing.NOW))
        self.assertFalse(nt.record_reason_prompt(self.db, 1, 20,
            {**self.saved, 'response': 'No trades'}, 899, now_utc=routing.NOW))

    def test_race_preserves_first_reason(self):
        conn, raced = self.conn, False
        class RacingConnection:
            def execute(self, sql, args=()):
                nonlocal raced
                if 'UPDATE POST_SHIFT_CHECKINS' in ' '.join(sql.upper().split()) and not raced:
                    raced = True
                    conn.execute('UPDATE post_shift_checkins SET response=? WHERE id=?',
                        ('I didn’t trade this shift' + nt.REASON_MARKER + 'away', self_ident))
                return conn.execute(sql, args)
        self_ident = self.ident
        @contextlib.contextmanager
        def racing_db():
            with conn:
                yield RacingConnection()
        result = self.save('no setup', db=racing_db)
        self.assertEqual(result['status'], 'already_answered')
        self.assertTrue(self.row(self.ident)['response'].endswith('away'))


class AcknowledgmentAndReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = snapshot_fixtures.SnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.conn, self.db = fixture.conn, fixture.db
        self.saved = dict(id=42, response="I didn't trade this shift", shift_date='2026-10-03', shift='day')

    def ack(self, **saved):
        return nt.no_trade_acknowledgment(self.db, 10, 20, {**self.saved, **saved})

    def test_matching_neutral_ack_without_prior_risk_reminder(self):
        result = self.ack()
        self.assertTrue(result['ask_reason'])
        self.assertTrue(result['text'].startswith('Recorded: no trades this shift.'))
        self.assertIn('It’s optional', result['text'])
        self.assertNotIn('risk', result['text'])
        self.assertFalse(self.ack(response='No trades because I was away')['ask_reason'])

    def test_existing_activity_warns_without_overwriting_trade_or_self_report(self):
        before = [dict(r) for r in self.conn.execute('SELECT * FROM theses')]
        result = self.ack(shift_date='2026-10-01')
        self.assertFalse(result['ask_reason'])
        self.assertIn('also trade or execution records', result['text'])
        self.assertEqual(before, [dict(r) for r in self.conn.execute('SELECT * FROM theses')])

    def test_other_member_and_other_shift_activity_do_not_conflict(self):
        self.assertTrue(nt.no_trade_acknowledgment(self.db, 10, 21,
            {**self.saved, 'shift_date': '2026-10-01'})['ask_reason'])
        self.assertTrue(self.ack(shift_date='2026-10-02', shift='night')['ask_reason'])

    def test_unknown_evidence_is_disclosed_without_false_verified_empty(self):
        with patch.object(nt, '_recorded_activity', side_effect=RuntimeError('private error')):
            result = self.ack()
        self.assertIn("couldn't check", result['text'])
        self.assertNotIn('private error', result['text'])
        self.assertFalse(result['ask_reason'])

    def test_same_shift_review_includes_reason_without_changing_stats(self):
        period = snapshots.shift_period('2026-10-01', 'day', snapshots.ZoneInfo('America/New_York'))
        before = snapshots.collect_snapshot(self.db, 10, 20, period)
        original = "I didn't trade this shift" + nt.REASON_MARKER + 'I was away'
        self.conn.execute('UPDATE post_shift_checkins SET response=? WHERE id=1', (original,))
        after = snapshots.collect_snapshot(self.db, 10, 20, period)
        self.assertEqual(before['performance'], after['performance'])
        self.assertEqual(before['process'], after['process'])
        self.assertEqual(after['no_trade_checkins'], [dict(shift_date='2026-10-01', shift='day',
            reason='away', reason_text='I was away')])
        self.assertIn('reported no trades. I was away', snapshots.format_no_trade_checkins(after))
        self.assertIn('no discipline or adherence inferred', snapshots.format_no_trade_checkins(after))
        self.assertEqual(snapshots.collect_snapshot(self.db, 10, 21, period)['no_trade_checkins'], [])


class DiscordAdapterTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        message, ns = routing.CheckinConsumerTests().fixture()
        message.reply.return_value = NS(id=800)
        message.content = "I didn't trade this shift"
        return message, ns

    async def test_no_trade_skips_unrelated_profile_and_binds_exact_ack(self):
        message, ns = self.fixture()
        saved = dict(id=42, response=message.content, shift_date='2026-10-03', shift='day')
        ns['save_checkin_reply'].return_value = saved
        ns['no_trade_acknowledgment'].return_value = dict(text='Recorded: no trades this shift.', ask_reason=True)
        ns['record_reason_prompt'].return_value = True
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['record_reason_prompt'].assert_called_once_with(ns['db'], 1, 20, saved, 800)
        ns['get_member_plan'].assert_not_called()
        ns['ingest_checkin_by_id'].assert_not_called()

    async def test_prompt_tracking_failure_discloses_saved_checkin_and_unavailable_followup(self):
        message, ns = self.fixture()
        ns['save_checkin_reply'].return_value = dict(id=42, response=message.content)
        ns['no_trade_acknowledgment'].return_value = dict(text='Recorded: no trades this shift.', ask_reason=True)
        ns['record_reason_prompt'].return_value = False
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        self.assertEqual(message.reply.await_count, 2)
        self.assertIn('could not be enabled', message.reply.await_args.args[0])
        ns['get_member_plan'].assert_not_called()

    async def test_reason_saved_does_not_start_new_checkin_or_profile_update(self):
        message, ns = self.fixture()
        ns['save_no_trade_reason'].return_value = dict(id=42, status='saved', skipped=False)
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['save_checkin_reply'].assert_not_called()
        ns['get_member_plan'].assert_not_called()
        message.reply.assert_awaited_once_with('Saved your reason with the same shift’s check-in.')

    async def test_revocation_blocks_reason_writes(self):
        message, ns = self.fixture()
        ns['member_access_error'].return_value = 'Access revoked.'
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['save_no_trade_reason'].assert_not_called()
        ns['save_checkin_reply'].assert_not_called()

    async def test_wrong_channel_blocks_reason_writes(self):
        message, ns = self.fixture()
        message.reference.channel_id = 88
        self.assertFalse(await ns['_consume_checkin_reply'](message))
        ns['save_no_trade_reason'].assert_not_called()

    async def test_duplicate_initial_message_is_consumed_without_profile_or_reprompt(self):
        message, ns = self.fixture()
        ns['save_checkin_reply'].return_value = {'duplicate': True}
        self.assertTrue(await ns['_consume_checkin_reply'](message))
        ns['no_trade_acknowledgment'].assert_not_called()
        ns['record_reason_prompt'].assert_not_called()
        ns['get_member_plan'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
