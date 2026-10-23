"""Synthetic market bars in isolated SQLite; no model, network or member writes."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_conversation import MarketConversation, ConversationStore, contextual_tools
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.range_reference import TOOL, resolve
from gbop_voice_web.voice_payload import voice_tool_payload

NOW = parse_time('2026-10-03T14:00:00-04:00')


class SelectorTests(unittest.TestCase):
    def test_aliases_and_spoken_play_names_resolve_without_ids(self):
        for alias in ('gold', 'XAUUSD'):
            for play in ('9ate8', '9 ate 8', 'nine ate eight', '988'):
                result = resolve(f"today's {alias} {play} morning session", None, NOW)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['selectors']['asset'], 'XAUUSD')
                self.assertEqual(result['anchor_start_ny'], '2026-10-03T08:00:00-04:00')
                self.assertEqual(result['anchor_end_ny'], '2026-10-03T09:00:00-04:00')

    def test_gct_has_four_hour_window_not_three_hour_day_shift(self):
        result = resolve("yesterday's NAS H4 GCT range", None, NOW)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['selectors'], dict(asset='NAS100', date_ny='2026-10-02', range_name='GCT', shift=None))
        self.assertEqual(result['anchor_start_ny'], '2026-10-02T09:00:00-04:00')
        self.assertEqual(result['anchor_end_ny'], '2026-10-02T13:00:00-04:00')
        self.assertEqual(result['anchor_timeframe'], 'H4')

    def test_relative_dates_use_new_york_near_utc_midnight_and_dst(self):
        for now, expected in [('2026-10-03T01:00:00+00:00', '2026-10-02T08:00:00-04:00'),
                              ('2026-11-02T01:00:00+00:00', '2026-11-01T08:00:00-05:00')]:
            result = resolve('today gold 9ate8 morning', None, parse_time(now))
            self.assertEqual(result['anchor_start_ny'], expected)
        result = resolve('yesterday gold 9ate8 evening', None, NOW)
        self.assertEqual(result['through_ny'], '2026-10-03T00:00:00-04:00')

    def test_missing_session_is_one_question_with_other_facts_preserved(self):
        result = resolve('yesterday gold 9ate8', None, NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(result['question'], 'Do you mean the morning or evening range?')
        answer = resolve('evening', result['selectors'], NOW)
        self.assertTrue(answer['ok'], answer)
        self.assertEqual(answer['anchor_start_ny'], '2026-10-02T20:00:00-04:00')

    def test_same_utterance_corrections_override_only_changed_fields(self):
        result = resolve('today gold 9ate8 morning, no yesterday, actually evening', None, NOW)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['selectors']['asset'], 'XAUUSD')
        self.assertEqual(result['anchor_start_ny'], '2026-10-02T20:00:00-04:00')

    def test_new_market_or_date_does_not_silently_inherit_session(self):
        previous = resolve('yesterday gold 9ate8 evening', None, NOW)['selectors']
        for text in ('today gold 9ate8', 'yesterday NAS 9ate8'):
            result = resolve(text, previous, NOW)
            self.assertFalse(result['ok'])
            self.assertIn('morning or evening', result['question'])

    def test_ambiguities_and_unsupported_feed_fail_closed(self):
        for text in ('yesterday gold NAS 9ate8 morning', 'today yesterday gold 9ate8 morning',
                     'yesterday gold 9ate8 morning evening', '2026-02-30 gold GCT',
                     'yesterday gold GCT night', 'yesterday OANDA:XAUUSD GCT',
                     'yesterday NAS H1 GCT'):
            with self.subTest(text=text):
                result = resolve(text, None, NOW)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['question'].count('?'), 1)

    def test_native_chart_h4_never_becomes_gct(self):
        for text in ('yesterday NAS native H4 range', 'yesterday NAS Chart H4 GCT'):
            self.assertEqual(resolve(text, None, NOW)['status'], 'native_chart_h4_unavailable')

    def test_market_and_session_ambiguity_keep_other_known_selectors(self):
        for text, answer in [('yesterday NAS gold 9ate8 morning', 'gold'),
                             ('yesterday gold 9ate8 morning evening', 'morning')]:
            first = resolve(text, None, NOW)
            second = resolve(answer, first['selectors'], NOW)
            self.assertTrue(second['ok'], second)
            self.assertEqual(second['selectors']['asset'], 'XAUUSD')
            self.assertEqual(second['anchor_start_ny'], '2026-10-02T08:00:00-04:00')

    def test_unsupported_feed_needs_explicit_answer_and_retains_other_facts(self):
        first = resolve('yesterday gold 9ate8 morning on the OANDA feed', None, NOW)
        self.assertEqual(first['status'], 'range_feed_unsupported')
        self.assertFalse(resolve('morning', first['selectors'], NOW)['ok'])
        answer = resolve('yes', first['selectors'], NOW)
        self.assertTrue(answer['ok'], answer)
        self.assertEqual(answer['anchor_start_ny'], '2026-10-02T08:00:00-04:00')

    def test_exact_broker_symbol_constraint_survives_asset_resolution(self):
        result = resolve('yesterday MT5:XAUUSD.wrong 9ate8 morning', None, NOW)
        self.assertEqual(result['selectors']['requested_symbol'], 'XAUUSD.WRONG')

    def test_tool_schema_uses_words_not_member_or_range_ids(self):
        tools = contextual_tools(market.MARKET_TOOLS)
        schema = next(t for t in tools if t['name'] == TOOL)
        self.assertEqual(schema['parameters']['required'], ['reference'])
        self.assertEqual(set(schema['parameters']['properties']), {'reference'})


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        self.addCleanup(self.conn.close)
        self.now = NOW
        self.calls = []
        for asset, symbol in [('XAUUSD', 'XAUUSD.test'), ('NAS100', 'USTEC.test')]:
            self.store(asset, symbol)
        self.context = MarketConversation((12, 34, 'synthetic-text'))

    @contextmanager
    def db(self):
        yield self.conn

    def store(self, asset, symbol, missing=None):
        self.conn.execute('DELETE FROM gbop_market_history WHERE asset=?', (asset,))
        start = parse_time('2026-10-02T00:00:00-04:00')
        end = parse_time('2026-10-04T00:00:00-04:00')
        days = {}
        for t in range(start, end, 300):
            if t == missing:
                continue
            days.setdefault(t // 86400 * 86400, []).append(dict(time=t, open=100, high=102, low=98, close=101))
        for day, bars in days.items():
            self.conn.execute('INSERT INTO gbop_market_history VALUES (?,?,?,?,?)', (asset, symbol, 300, day, json.dumps(bars)))
        payload = dict(symbol=symbol, tick_time=NOW-600, bid=100, ask=101, bars=[])
        self.conn.execute('INSERT OR REPLACE INTO gbop_market_feed VALUES (?,?,?,?)', (asset, NOW-600, NOW-600, json.dumps(payload)))
        self.conn.commit()

    def runner(self, name, args):
        self.calls.append((name, deepcopy(args)))
        return market.market_tool(self.db, name, args, now=self.now)

    def ask(self, text, context=None):
        context = context or self.context
        generation = context.begin_turn(text, now=self.now)
        request = context.required_evidence_request()
        self.assertIsNotNone(request, text)
        self.assertEqual(request['tool'], TOOL)
        return context.run(TOOL, request['args'], self.runner, generation=generation)

    def test_real_sqlite_bridge_to_prefetch_and_spoken_identity(self):
        generation = self.context.begin_turn("today's gold 9ate8 morning session", now=self.now)
        payload = prefetch_market_evidence(self.context, self.runner, generation)
        self.assertIn('XAUUSD.test, MT5 broker feed', payload)
        result = self.context._reference_result
        self.assertTrue(result['ok'], result)
        reference = result['range_reference']
        self.assertTrue(result['review']['anchor']['complete'])
        self.assertEqual(reference['symbol'], 'XAUUSD.test')
        self.assertEqual(reference['timezone'], 'America/New_York')
        self.assertIn('8:00 AM–9:00 AM', reference['human_description'])
        self.assertTrue(reference['range_id'].startswith('range_'))
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['is_live'])  # Complete history remains usable, never called live.
        self.assertIsNotNone(result['feed_health'])
        spoken = voice_tool_payload(TOOL, result)
        self.assertEqual(spoken['range_reference'], reference)
        count = len(self.calls)
        self.assertEqual(self.context.run(TOOL, {'reference': 'wrong generated default'}, self.runner), result)
        self.assertEqual(len(self.calls), count)

    def test_gct_is_aggregated_window_with_full_four_hour_coverage(self):
        result = self.ask("yesterday's NAS H4 GCT range")
        self.assertTrue(result['ok'], result)
        anchor = result['review']['anchor']
        self.assertEqual(anchor['bar_count'], 48)
        self.assertEqual(anchor['high'], 102)
        self.assertEqual(result['range_reference']['ohlc_basis'], 'aggregated_bridge_bars')
        self.assertIsNone(self.context.selected['shift'])

    def test_one_question_reply_and_correction_keep_exact_member_context(self):
        result = self.ask('yesterday gold 9ate8')
        self.assertFalse(result['ok'])
        self.assertEqual(self.calls, [])
        result = self.ask('evening')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], '2026-10-02T20:00:00-04:00')
        first = result['range_reference']['range_id']
        result = self.ask('yesterday gold 9ate8 morning')
        self.assertTrue(result['ok'], result)
        self.assertNotEqual(first, result['range_reference']['range_id'])

    def test_missing_feed_and_reference_gap_never_become_verified_selection(self):
        self.conn.execute('DELETE FROM gbop_market_feed WHERE asset=?', ('XAUUSD',))
        result = self.ask('yesterday gold 9ate8 morning')
        self.assertEqual(result['status'], 'not_connected')
        self.assertIsNone(self.context.selected)
        self.store('XAUUSD', 'XAUUSD.test', parse_time('2026-10-02T08:15:00-04:00'))
        result = self.ask('yesterday gold 9ate8 morning')
        self.assertEqual(result['status'], 'named_range_incomplete')
        self.assertIsNone(self.context.selected)
        self.assertIsNone(self.context._journal_review)

    def test_forming_reference_not_filled_with_future_fixture_bars(self):
        self.now = parse_time('2026-10-03T08:30:00-04:00')
        result = self.ask('today gold 9ate8 morning')
        self.assertEqual(result['status'], 'named_range_not_closed')
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.context.selected)

    def test_native_chart_request_has_no_broker_read_or_gct_fallback(self):
        result = self.ask('yesterday NAS Chart H4 range')
        self.assertEqual(result['status'], 'native_chart_h4_unavailable')
        self.assertEqual(self.calls, [])

    def test_wrong_returned_symbol_source_or_candle_cannot_be_selected(self):
        self.context.begin_turn('yesterday gold 9ate8 morning', now=NOW)
        def wrong(name, args):
            result = self.runner(name, args)
            result['source'] = 'another provider'
            return result
        result = self.context.run(TOOL, {'reference': 'ignored'}, wrong)
        self.assertEqual(result['status'], 'range_feed_mismatch')
        self.assertIsNone(self.context.selected)
        result = self.ask('yesterday MT5:XAUUSD.wrong 9ate8 morning')
        self.assertEqual(result['status'], 'range_feed_mismatch')

    def test_missing_following_data_is_unknown_outcome_not_missing_reference(self):
        self.store('XAUUSD', 'XAUUSD.test', parse_time('2026-10-02T10:15:00-04:00'))
        result = self.ask('yesterday gold 9ate8 morning')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['review']['anchor']['complete'])
        self.assertFalse(result['review']['observation_coverage']['complete'])

    def test_failed_switch_does_not_allow_old_scope_to_answer_or_bind_journal(self):
        self.ask('yesterday gold 9ate8 morning')
        self.conn.execute('DELETE FROM gbop_market_feed WHERE asset=?', ('NAS100',))
        result = self.ask('yesterday NAS H4 GCT')
        self.assertFalse(result['ok'])
        self.assertIsNone(self.context._journal_review)
        self.assertNotIn('XAUUSD', self.context.prompt())
        result = self.context.run('review_market_session', {'asset': 'gold'}, self.runner)
        self.assertEqual(result['status'], 'named_range_resolution_required')

    def test_read_exceptions_do_not_expose_provider_details_or_retry(self):
        self.context.begin_turn('yesterday gold 9ate8 morning', now=NOW)
        calls = []
        def failed(name, args):
            calls.append(name)
            raise RuntimeError('secret connection information')
        result = self.context.run(TOOL, {'reference': 'ignored'}, failed)
        self.assertEqual(result['status'], 'named_range_read_failed')
        self.assertNotIn('secret', str(result))
        self.context.run(TOOL, {'reference': 'ignored'}, failed)
        self.assertEqual(len(calls), 1)

    def test_unrelated_text_does_not_continue_pending_clarification(self):
        self.ask('yesterday gold 9ate8')
        self.context.begin_turn('thanks, that is all', now=NOW)
        self.assertIsNone(self.context._reference_pending)
        self.assertIsNone(self.context.required_evidence_request())

    def test_authenticated_backend_prefetches_before_model_even_without_tool_call(self):
        from test_market_conversation import function
        create = Mock(return_value=NS(output=[], output_text='Synthetic response'))
        backend = function('run_backend', dict(PENDING_JOURNAL_DELETIONS={}, time=time,
            member_context=lambda _: '', GTOP_GUILD_ID=12,
            client=NS(responses=NS(create=create)), BACKEND_MODEL='offline-test',
            BACKEND_PROMPT='', TOOLS=market.MARKET_TOOLS, json=json,
            run_tool=lambda user, name, args, token: self.runner(name, args)))
        answer = backend([{'role': 'user', 'text': '2026-10-02 gold 9ate8 morning session'}], 34, self.context, 1)
        self.assertEqual(answer, 'Synthetic response')
        self.assertEqual([name for name, _ in self.calls], ['review_market_crt'])
        supplied = create.call_args.kwargs
        self.assertIn('XAUUSD.test, MT5 broker feed', str(supplied['input']))
        self.assertIn(TOOL, [tool['name'] for tool in supplied['tools']])
        self.assertTrue(self.context._reference_result['ok'])

    def test_auth_revocation_blocks_cached_and_new_reference_reads(self):
        self.conn.execute('CREATE TABLE members (guild_id INTEGER,user_id INTEGER,activated INTEGER,revoked INTEGER,leadership_ack INTEGER,updated_at TEXT)')
        self.conn.execute('INSERT INTO members VALUES (12,34,1,0,1,?)', ('v1',))
        self.context.auth_provider = (self.db, 12, 34)
        self.assertTrue(self.ask('yesterday gold 9ate8 morning')['ok'])
        count = len(self.calls)
        self.conn.execute('UPDATE members SET revoked=1,updated_at=?', ('v2',))
        for context in (self.context, MarketConversation((12, 34, 'reconnected'), (self.db, 12, 34))):
            if context is not self.context:
                context.begin_turn(None, now=NOW)
            result = context.run(TOOL, {'reference': 'yesterday gold 9ate8 morning'}, self.runner)
            self.assertEqual(result['status'], 'journal_auth_changed')
        self.assertEqual(len(self.calls), count)

    def test_new_human_correction_wins_over_generated_tool_reference(self):
        self.context.begin_turn('yesterday gold 9ate8 morning, no evening', now=NOW)
        result = self.context.run(TOOL, {'reference': 'today NAS GCT'}, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['asset'], 'XAUUSD')
        self.assertEqual(result['range_reference']['anchor_start_ny'], '2026-10-02T20:00:00-04:00')

    def test_short_correction_after_resolution_keeps_market_and_date(self):
        self.ask('yesterday gold 9ate8 morning')
        result = self.ask('actually evening')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['range_reference']['anchor_start_ny'], '2026-10-02T20:00:00-04:00')

    def test_existing_verified_review_supplies_omitted_member_context(self):
        self.context.begin_turn(None, now=NOW)
        result = self.context.run('review_market_session', dict(asset='gold', date_ny='2026-10-02', shift='day'), self.runner)
        self.assertTrue(result['ok'], result)
        result = self.ask('9ate8 morning')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['range_reference']['anchor_start_ny'], '2026-10-02T08:00:00-04:00')

    def test_ambiguous_comparison_cannot_supply_hidden_previous_market(self):
        self.ask('yesterday gold 9ate8 morning')
        self.context._multi_focus_required = True
        result = self.ask('yesterday GCT morning')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['question'], 'Which market do you mean?')

    def test_two_member_clarifications_are_isolated(self):
        contexts = ConversationStore()
        a = contexts.get((12, 34, 'text'))
        b = contexts.get((12, 35, 'text'))
        self.ask('yesterday gold 9ate8', a)
        self.ask('yesterday NAS 9ate8', b)
        ra, rb = self.ask('morning', a), self.ask('evening', b)
        self.assertEqual(ra['asset'], 'XAUUSD')
        self.assertEqual(rb['asset'], 'NAS100')
        self.assertNotEqual(ra['range_reference']['range_id'], rb['range_reference']['range_id'])
        self.assertNotEqual(a._journal_review['conversation_id'], b._journal_review['conversation_id'])
        self.assertNotIn('XAUUSD', b.prompt())

    def test_late_read_cannot_restore_range_after_new_correction(self):
        ticket = self.context.begin_turn('yesterday gold 9ate8 morning', now=NOW)
        def delayed(name, args):
            result = self.runner(name, args)
            self.context.begin_turn('yesterday NAS H4 GCT', now=NOW)
            return result
        result = self.context.run(TOOL, {'reference': 'ignored'}, delayed, generation=ticket)
        self.assertFalse(result['ok'])
        self.assertIsNone(self.context.selected)
        current = self.context.run(TOOL, {'reference': 'ignored'}, self.runner)
        self.assertTrue(current['ok'], current)
        self.assertEqual(self.context.selected['asset'], 'NAS100')

    def test_cancellation_between_dispatch_and_resolver_blocks_unstarted_read(self):
        from gbop_voice_web.range_reference import run
        ticket = self.context.begin_turn('yesterday gold 9ate8 morning', now=NOW)
        self.context.begin_turn('yesterday NAS H4 GCT', now=NOW)
        previous = deepcopy(self.context.requested)
        result = run(self.context, {'reference': 'ignored'}, self.runner, ticket)
        self.assertFalse(result['ok'])
        self.assertEqual(self.context.requested, previous)
        self.assertEqual(self.calls, [])

    def test_audio_tool_route_uses_same_parser_without_fabricated_transcript(self):
        self.context.begin_turn(None, now=NOW)
        result = self.context.run(TOOL, {'reference': 'yesterday NAS H4 GCT'}, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(self.context._client_text)

    def test_detail_followup_retains_feed_and_internal_range_identity(self):
        original = self.ask('yesterday gold 9ate8 morning')
        self.assertTrue(original['ok'], original)
        self.context.begin_turn(None, now=NOW)
        result = self.context.run('review_market_crt', {'asset': 'gold', 'context_action': 'continue'}, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['range_reference']['range_id'], original['range_reference']['range_id'])
        self.store('XAUUSD', 'XAUUSD.other')
        self.context.begin_turn(None, now=NOW)
        result = self.context.run('review_market_crt', {'asset': 'gold', 'context_action': 'continue'}, self.runner)
        self.assertEqual(result['status'], 'range_feed_mismatch')

    def test_explicit_other_candle_drops_old_named_identity(self):
        self.ask('yesterday gold 9ate8 morning')
        self.context.begin_turn(None, now=NOW)
        result = self.context.run('review_market_crt', dict(asset='gold', context_action='continue',
            anchor_start_ny='2026-10-02T09:00:00-04:00'), self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], '2026-10-02T09:00:00-04:00')
        self.assertNotIn('range_reference', self.context.selected)


if __name__ == '__main__':
    unittest.main()
