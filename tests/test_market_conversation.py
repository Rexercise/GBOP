"""Synthetic utterances and public market facts only; no live provider calls."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import threading
import shutil
import subprocess
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException
from gbop_voice_web.market_conversation import MarketConversation, ConversationStore, contextual_tools
from gbop_voice_web.market_data import MARKET_TOOLS
from gbop_voice_web.market_watch import WATCH_TOOLS
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.voice_work import VoiceToolWork

NOW = datetime.fromisoformat('2026-10-03T06:30:00-04:00').timestamp()
NIGHT = {'asset': 'BTCUSD', 'date_ny': '2026-10-02', 'shift': 'night'}


def evidence(args):
    night = args.get('shift') == 'night'
    anchor = args['date_ny'] + ('T20:00:00-04:00' if night else 'T08:00:00-04:00')
    result = {'ok': True, 'asset': args['asset'], 'review': {
        'date_ny': args['date_ny'], 'shift': args['shift'],
        'shift_story': {'ranges': [{'anchor_start_ny': anchor, 'role': 'selected_range',
            'label': '9ate8', 'direction_observed': 'bearish',
            'objectives': [{'objective': 'midpoint', 'status': 'observed_after_purge'},
                           {'objective': 'opposing_liquidity', 'status': 'not_observed_before_invalidation' if night else 'observed_after_purge'}],
            'invalidated_at_ny': args['date_ny'] + 'T23:00:00-04:00' if night else None}],
            'recap': {'headline': 'Midpoint only' if night else 'Full delivery',
                      'spoken_summary': 'Night range reached midpoint only.' if night else 'Day range fully delivered.'}}}}
    return result


class Provider:
    def __init__(self):
        self.calls = []
        self.days = [('2026-10-02', 'day'), ('2026-10-02', 'night')]

    def __call__(self, name, args):
        self.calls.append((name, deepcopy(args)))
        if name == 'list_market_shifts':
            return {'ok': True, 'available_shifts': [
                {'asset': args['asset'], 'date_ny': day, 'shift': shift,
                 'temporal_status': 'completed', 'review_scope': 'full'}
                for day, shift in self.days if not args.get('date_ny') or args['date_ny'] == day]}
        if name in {'review_market_session', 'get_prepared_market_brief'}:
            result = evidence(args)
            if name == 'get_prepared_market_brief':
                result = {'ok': True, **{key: args[key] for key in ('asset', 'date_ny', 'shift')}, 'review': result}
            return result
        return {'ok': True, 'asset': args.get('asset'), 'review': {'anchor': {'start_ny': args.get('anchor_start_ny')}}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.context = MarketConversation((10, 20, 'session-a'))
        self.provider = Provider()
        self.context.begin_turn()
        self.initial = self.context.run('review_market_session', NIGHT, self.provider)
        self.assertTrue(self.initial['ok'], self.initial)

    def test_omitted_and_wrong_day_defaults_cannot_replace_night(self):
        for name in ('review_market_session', 'get_prepared_market_brief'):
            self.context.begin_turn()
            result = self.context.run(name, {'asset': 'BTCUSD', 'date_ny': None, 'shift': 'day'}, self.provider)
            self.assertTrue(result['ok'], result)
            self.assertEqual(self.provider.calls[-1][1], NIGHT)
            self.assertEqual(result['market_context']['selection']['shift'], 'night')
            self.assertIn('Midpoint only', self.context.prompt())
            self.assertNotIn('Full delivery', self.context.prompt())

    def test_spoken_explicit_switch_is_separate_from_continue(self):
        self.context.begin_turn()
        result = self.context.run('review_market_session', {**NIGHT, 'asset': 'NAS', 'shift': 'day', 'context_action': 'switch'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['asset'], 'NAS100')
        self.assertEqual(self.context.selected['shift'], 'day')
        self.assertNotEqual(result['market_context']['scope_id'], self.initial['market_context']['scope_id'])

    def test_text_switch_uses_actual_utterance_not_generated_arguments(self):
        for text, expected in [('What about NAS?', {'asset': 'NAS100'}),
                               ('Now oil', {'asset': 'WTI'}),
                               ('Use 2026-10-01 instead', {'date_ny': '2026-10-01'}),
                               ('The day shift please', {'shift': 'day'})]:
            self.context.begin_turn(text, now=NOW)
            result = self.context.run('review_market_session', NIGHT, self.provider)
            self.assertTrue(result['ok'], result)
            for key, value in expected.items():
                self.assertEqual(self.context.selected[key], value)

    def test_text_cannot_claim_switch_without_user_switch(self):
        self.context.begin_turn('So did it finish its target?', now=NOW)
        result = self.context.run('review_market_session', {**NIGHT, 'shift': 'day', 'context_action': 'switch'}, self.provider)
        self.assertEqual(result['market_context']['selection']['shift'], 'night')

    def test_last_night_then_asset_keeps_yesterday_not_latest_available(self):
        context = MarketConversation((1, 2, 'new'))
        context.begin_turn('Review last night shift', now=NOW)
        context.begin_turn('NAS', now=NOW)
        provider = Provider()
        provider.days = [('2026-10-01', 'night'), ('2026-10-02', 'day')]
        actual = []
        def unavailable(name, args):
            actual.append((name, dict(args)))
            if name == 'list_market_shifts':
                return provider(name, args)
            if args['date_ny'] == '2026-10-02' and args['shift'] == 'night':
                return {'ok': False, 'status': 'shift_unavailable', 'date_ny': '2026-10-02',
                        'message': 'No night data for that date. Review the same-date day shift?'}
            return evidence(args)
        result = context.run('review_market_session',
            {'asset': 'NAS100', 'date_ny': None, 'shift': 'night', 'context_action': 'latest'}, unavailable)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'shift_unavailable')
        self.assertEqual(actual[-1][1]['date_ny'], '2026-10-02')
        self.assertEqual(actual[-1][1]['asset'], 'NAS100')
        self.assertIsNone(context.selected)
        context.begin_turn('Was it boneless?', now=NOW)
        result = context.run('get_prepared_market_brief', {'asset': 'NAS100', 'date_ny': None, 'shift': 'night'}, unavailable)
        self.assertFalse(result['ok'])
        self.assertEqual(actual[-1][1]['date_ny'], '2026-10-02')
        self.assertIn('no matching verified evidence', context.prompt())

    def test_spoken_last_night_never_resolves_earlier_retained_night(self):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo
        fixed = datetime.fromtimestamp(NOW, tz=ZoneInfo('America/New_York'))
        self.context.begin_turn()
        with patch('gbop_voice_web.market_conversation.datetime') as clock:
            clock.now.return_value = fixed
            clock.fromtimestamp.side_effect = datetime.fromtimestamp
            clock.fromisoformat.side_effect = datetime.fromisoformat
            result = self.context.run('review_market_session', {
                'asset': 'NAS100', 'date_ny': None, 'shift': 'night', 'context_action': 'last_night'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['date_ny'], '2026-10-02')
        self.assertEqual(self.provider.calls[-1][1]['date_ny'], '2026-10-02')

    def test_changed_shift_rebuilds_its_anchor_and_midnight_cutoff(self):
        self.context.begin_turn('The day shift instead', now=NOW)
        result = self.context.run('review_market_session', NIGHT, self.provider)
        scope = result['market_context']['selection']
        self.assertEqual(scope['anchor_start_ny'], '2026-10-02T08:00:00-04:00')
        self.assertEqual(scope['through_ny'], '2026-10-02T12:00:00-04:00')

    def test_rate_limit_cancellation_retains_verified_scope_and_fact_snapshot(self):
        before = deepcopy(self.context.evidence)
        self.context.invalidate()
        result = self.context.run('get_prepared_market_brief', {'asset': 'BTC', 'date_ny': None, 'shift': 'day'}, self.provider)
        self.assertEqual(result['market_context']['scope_id'], before['scope_id'])
        self.assertIn('Midpoint only', self.context.prompt())

    def test_fresh_latest_resolves_current_completed_shift(self):
        self.provider.days += [('2026-10-03', 'day')]
        self.context.begin_turn('Review the last shift', now=NOW)
        result = self.context.run('review_market_session', {**NIGHT, 'shift': 'night'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['date_ny'], '2026-10-03')
        self.assertEqual(self.context.selected['shift'], 'day')

    def test_voice_latest_null_shift_compares_day_and_night(self):
        self.context.begin_turn()
        result = self.context.run('review_market_session', {'asset': 'OIL', 'date_ny': None, 'shift': None, 'context_action': 'latest'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['asset'], 'WTI')
        self.assertEqual(self.context.selected['shift'], 'night')

    def test_latest_available_shift_is_fresh_resolution_not_ellipsis(self):
        self.provider.days += [('2026-10-03', 'day')]
        self.context.begin_turn('Review the latest available shift', now=NOW)
        result = self.context.run('review_market_session', NIGHT, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['date_ny'], '2026-10-03')
        self.assertEqual(self.context.selected['shift'], 'day')

    def test_latest_catalogue_then_review_does_not_fall_back_to_old_scope(self):
        self.provider.days += [('2026-10-03', 'day')]
        self.context.begin_turn('Last shift on NAS', now=NOW)
        catalog = self.context.run('list_market_shifts', {'asset': 'BTCUSD', 'date_ny': None}, self.provider)
        self.assertEqual(catalog['resolved_context']['asset'], 'NAS100')
        result = self.context.run('review_market_session', NIGHT, self.provider)
        self.assertEqual(result['market_context']['selection']['asset'], 'NAS100')
        self.assertEqual(result['market_context']['selection']['date_ny'], '2026-10-03')
        self.assertEqual(result['market_context']['selection']['shift'], 'day')

    def test_original_range_and_cutoff_bound_elliptical_detail(self):
        self.context.begin_turn('Show its candle evidence', now=NOW)
        result = self.context.run('review_market_crt', {'asset': 'BTCUSD',
            'anchor_start_ny': '2026-10-02T08:00:00-04:00', 'anchor_timeframe': 'H1',
            'through_ny': '2026-10-02T12:00:00-04:00'}, self.provider)
        args = self.provider.calls[-1][1]
        self.assertEqual(args['anchor_start_ny'], '2026-10-02T20:00:00-04:00')
        self.assertEqual(args['through_ny'], '2026-10-03T00:00:00-04:00')
        self.assertEqual(result['market_context']['scope_id'], self.initial['market_context']['scope_id'])

    def test_explicit_range_change_and_later_followup_preserve_anchor(self):
        self.context.begin_turn('Look at the 9 PM range', now=NOW)
        self.context.run('review_market_crt', {'asset': 'BTCUSD',
            'anchor_start_ny': '2026-10-02T08:00:00-04:00', 'anchor_timeframe': 'H1',
            'through_ny': '2026-10-02T12:00:00-04:00'}, self.provider)
        self.assertEqual(self.context.selected['anchor_start_ny'], '2026-10-02T21:00:00-04:00')
        self.context.begin_turn('Did it return inside?', now=NOW)
        self.context.run('review_market_crt', {'asset': 'BTCUSD', 'anchor_start_ny': '2026-10-02T20:00:00-04:00'}, self.provider)
        self.assertEqual(self.provider.calls[-1][1]['anchor_start_ny'], '2026-10-02T21:00:00-04:00')

    def test_wrong_result_is_rejected_not_saved(self):
        old = deepcopy(self.context.selected)
        self.context.begin_turn()
        result = self.context.run('review_market_session', NIGHT,
                                 lambda name, args: evidence({**args, 'shift': 'day'}))
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(self.context.selected, old)

    def test_narrow_inspection_preserves_context_wrong_date_is_blocked(self):
        self.context.begin_turn()
        args = {'asset': 'BTCUSD', 'start_ny': '2026-10-02T21:10:00-04:00',
                'end_ny': '2026-10-02T21:20:00-04:00', 'timeframe': 'M1'}
        self.assertTrue(self.context.run('inspect_market_candles', args, self.provider)['ok'])
        self.context.begin_turn()
        args.update(start_ny='2026-10-02T09:10:00-04:00', end_ny='2026-10-02T09:20:00-04:00')
        result = self.context.run('inspect_market_candles', args, self.provider)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(self.context.selected['shift'], 'night')

    def test_failed_explicit_switch_does_not_poison_verified_selection(self):
        self.context.begin_turn('Now oil', now=NOW)
        result = self.context.run('review_market_session', NIGHT, lambda *a: {'ok': False, 'error': 'missing feed'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.context.selected['asset'], 'BTCUSD')

    def test_stale_synchronous_result_cannot_overwrite_after_cancellation(self):
        entered, finish = threading.Event(), threading.Event()
        def blocked(name, args):
            entered.set()
            self.assertTrue(finish.wait(3))
            return evidence(args)
        self.context.begin_turn()
        old = deepcopy(self.context.selected)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.context.run, 'review_market_session',
                                 {**NIGHT, 'shift': 'day', 'context_action': 'switch'}, blocked)
            self.assertTrue(entered.wait(3))
            self.context.invalidate()
            self.context.begin_turn('What about NAS?', now=NOW)
            newer = self.context.run('review_market_session', NIGHT, self.provider)
            finish.set()
            result = future.result(3)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(self.context.selected['asset'], 'NAS100')
        self.assertEqual(self.context.selected['shift'], old['shift'])
        self.assertEqual(newer['market_context']['selection'], self.context.selected)

    def test_old_turn_is_rejected_before_tools_run(self):
        generation = self.context.generation
        self.context.invalidate()
        count = len(self.provider.calls)
        result = self.context.run('review_market_session', NIGHT, self.provider, generation=generation)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(len(self.provider.calls), count)

    def test_evidence_survives_compaction_and_has_no_member_identity(self):
        result = compact_voice_tool_result('review_market_session', self.initial)
        self.assertEqual(result['market_context'], self.initial['market_context'])
        self.assertNotIn('owner', json.dumps(result['market_context']))
        self.assertIn('not_observed_before_invalidation', self.context.prompt())

    def test_store_member_and_channel_isolation_bounded_and_expiring(self):
        store = ConversationStore(limit=2, ttl=10)
        a = store.get((1, 2, 'text', 3), now=0)
        b = store.get((1, 4, 'text', 3), now=0)
        a.begin_turn(); a.run('review_market_session', NIGHT, self.provider)
        self.assertIsNone(b.selected)
        other_channel = store.get((1, 2, 'text', 4), now=1)
        self.assertIsNone(other_channel.selected)
        self.assertIsNot(store.get((1, 2, 'text', 3), now=2), a)
        self.assertIsNone(store.get((1, 4, 'text', 3), now=20).selected)
        self.assertEqual(len(store.entries), 1)

    def test_existing_schemas_unmodified_context_action_is_explicit(self):
        original = deepcopy(MARKET_TOOLS + WATCH_TOOLS)
        tools = contextual_tools(MARKET_TOOLS + WATCH_TOOLS)
        self.assertEqual(MARKET_TOOLS + WATCH_TOOLS, original)
        for tool in tools:
            if tool['name'] == 'review_market_session':
                self.assertIn('context_action', tool['parameters']['required'])
                self.assertIn(None, tool['parameters']['properties']['shift']['enum'])
            if tool['name'] == 'manage_market_watch':
                self.assertNotIn('context_action', tool['parameters']['properties'])

    def test_browser_cancel_order_and_old_client_turn(self):
        self.context.advance_client_turn(2)
        current = self.context.begin_turn('Show it', client_turn=2)
        self.context.advance_client_turn(2)  # Late same-speech cancellation event.
        self.assertTrue(self.context.current(current))
        self.context.advance_client_turn(3)
        self.assertFalse(self.context.current(current))
        self.assertIsNone(self.context.begin_turn('stale request', client_turn=2))
        self.assertEqual(self.context.selected['shift'], 'night')

    def test_last_week_weekday_means_previous_monday_sunday_ny_week(self):
        for phrase in ("last week Wednesday", "Wednesday last week", "last week's Wednesday"):
            with self.subTest(phrase=phrase):
                context = MarketConversation()
                context.begin_turn('Review BTC ' + phrase + ' night shift', now=NOW)
                self.assertEqual(context.requested['date_ny'], '2026-09-23')
                self.assertEqual(context.requested['shift'], 'night')
        context.begin_turn('Wednesday night shift', now=NOW)
        self.assertEqual(context.requested['date_ny'], '2026-09-30')
        context.begin_turn('Last night', now=NOW)
        self.assertEqual(context.requested['date_ny'], '2026-10-02')

    def test_short_day_reply_resolves_pending_shift_choice(self):
        context = MarketConversation()
        context.begin_turn('Review NAS yesterday', now=NOW)
        args = {'asset': 'NAS', 'date_ny': '2026-10-02', 'shift': None}
        result = context.run('review_market_session', args, self.provider)
        self.assertEqual(result['status'], 'choose_shift')
        context.begin_turn('day', now=NOW)
        result = context.run('review_market_session', {**args, 'shift': 'day', 'context_action': 'switch'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(context.selected['shift'], 'day')

    def test_detailed_anchor_cannot_be_replaced_by_whole_shift_evidence(self):
        self.context.begin_turn('Look at the 9 PM range', now=NOW)
        self.context.run('review_market_crt', {'asset': 'BTCUSD',
            'anchor_start_ny': '2026-10-02T21:00:00-04:00', 'anchor_timeframe': 'H1',
            'through_ny': '2026-10-03T00:00:00-04:00'}, self.provider)
        before = deepcopy(self.context.evidence)
        self.context.begin_turn('Did it reach its target?', now=NOW)
        calls = len(self.provider.calls)
        result = self.context.run('review_market_session', NIGHT, self.provider)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'selected_range_requires_detail')
        self.assertEqual(result['next_arguments']['anchor_start_ny'], '2026-10-02T21:00:00-04:00')
        self.assertEqual(self.context.evidence, before)
        self.assertEqual(len(self.provider.calls), calls)
        self.context.begin_turn('Review the whole shift', now=NOW)
        result = self.context.run('review_market_session', NIGHT, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], '2026-10-02T20:00:00-04:00')

    def test_standalone_inspection_pagination_retains_original_window(self):
        context = MarketConversation()
        context.begin_turn('Inspect BTC candles October 2', now=NOW)
        args = {'asset': 'BTCUSD', 'start_ny': '2026-10-02T09:00:00-04:00',
                'end_ny': '2026-10-02T10:00:00-04:00', 'timeframe': 'M1'}
        first = context.run('inspect_market_candles', args, self.provider)
        self.assertTrue(first['ok'], first)
        context.begin_turn('Show next page', now=NOW)
        second = context.run('inspect_market_candles', {**args, 'start_ny': '2026-10-02T09:30:00-04:00'}, self.provider)
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['market_context']['selection']['anchor_start_ny'], args['start_ny'])
        self.assertEqual(self.provider.calls[-1][1]['start_ny'], '2026-10-02T09:30:00-04:00')

    def test_same_asset_mention_and_explicit_shift_switch_cannot_bypass_window(self):
        self.context.begin_turn('Show BTC candles for that setup', now=NOW)
        args = {'asset': 'BTCUSD', 'start_ny': '2026-10-02T09:10:00-04:00',
                'end_ny': '2026-10-02T09:20:00-04:00', 'timeframe': 'M1'}
        calls = len(self.provider.calls)
        result = self.context.run('inspect_market_candles', args, self.provider)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(len(self.provider.calls), calls)
        self.context.begin_turn('Now NAS day shift', now=NOW)
        result = self.context.run('inspect_market_candles', {**args, 'asset': 'NAS100',
            'start_ny': '2026-10-02T21:10:00-04:00', 'end_ny': '2026-10-02T21:20:00-04:00'}, self.provider)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(len(self.provider.calls), calls)

    def test_closed_context_cannot_be_revived_or_dispatch_unstarted_action(self):
        generation = self.context.generation
        self.context.close()
        self.assertIsNone(self.context.begin_turn('Resume', client_turn=100))
        calls = len(self.provider.calls)
        result = self.context.run('open_trade', {}, self.provider, generation=generation)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(len(self.provider.calls), calls)
        result = self.context.run('open_trade', {}, self.provider)
        self.assertEqual(result['status'], 'stale_market_context')

    def test_superseded_unstarted_nonmarket_action_is_fenced(self):
        generation = self.context.generation
        self.context.begin_turn('Never mind', now=NOW)
        calls = len(self.provider.calls)
        result = self.context.run('open_trade', {}, self.provider, generation=generation)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(len(self.provider.calls), calls)


def function(name, namespace):
    path = Path(__file__).resolve().parents[1] / 'gbop_voice_web/server.py'
    node = next(n for n in ast.parse(path.read_text()).body if getattr(n, 'name', '') == name)
    node.decorator_list = []
    namespace.update(Request=object, DelegateRequest=object, LiveContextRequest=object)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class AuthenticatedHooksTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_voice_switch_pins_requested_scope_warning(self):
        from test_voice_latency import method
        from gbop_voice_web.voice_payload import voice_tool_payload
        from unittest.mock import Mock
        import time
        context = MarketConversation((1, 2))
        context.begin_turn(); context.run('review_market_session', NIGHT, Provider())
        context.begin_turn()
        dispatch = Mock(return_value={'ok': False, 'status': 'shift_unavailable', 'error': 'No NAS night data'})
        session = NS(member=NS(id=2), market_context=context, _market_base_instructions='base',
                     send_event=AsyncMock(return_value=True), _tool_response_options={})
        execute = method('execute_tool', dict(asyncio=asyncio, json=json, time=time,
            ai_execute_tool=dispatch, voice_tool_payload=voice_tool_payload,
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        await execute(session, {'name': 'review_market_session', 'call_id': 'c',
            'arguments': json.dumps({**NIGHT, 'asset': 'NAS100', 'context_action': 'switch'})})
        updates = [call.args[0] for call in session.send_event.await_args_list
                   if call.args[0]['type'] == 'session.update']
        self.assertEqual(len(updates), 1)
        self.assertIn('NAS100', updates[0]['session']['instructions'])
        self.assertIn('no matching verified evidence', updates[0]['session']['instructions'])
        self.assertEqual(context.selected['asset'], 'BTCUSD')
        dispatch.assert_called_once()

    async def test_older_live_session_completion_cannot_replace_newer_connection(self):
        from fastapi.responses import JSONResponse
        from unittest.mock import Mock
        state = {'user_id': 2}
        entered = {key: asyncio.Event() for key in ('older', 'newer')}
        release = {key: asyncio.Event() for key in entered}
        class Request:
            cookies = {'cookie': 'sid'}
            def __init__(self, key):
                self.key = key
            async def body(self):
                return self.key.encode()
        class Http:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, *, headers, json):
                key = json['transport']['sdp']
                entered[key].set()
                await release[key].wait()
                return NS(status_code=200, json=lambda: {'session': {'id': key}, 'transport': {'sdp': 'answer'}})
        create = function('live_session', dict(
            require_authenticated_user=AsyncMock(return_value=state),
            secrets=NS(token_urlsafe=Mock(side_effect=['old-token', 'new-token'])),
            AUTH_SESSIONS={'sid': state}, SESSION_COOKIE='cookie', HTTPException=HTTPException,
            asyncio=asyncio, get_profile=lambda *args: {'configured': True}, db=object(), GTOP_GUILD_ID=1,
            LIVE_INSTRUCTIONS='', market_clock=lambda: '', profile_context=lambda _: '',
            LIVE_MODEL='offline-model', LIVE_VOICE='offline-voice', OPENAI_API_KEY='offline-placeholder',
            safety_id=lambda _: 'offline-id', httpx=NS(AsyncClient=lambda **kwargs: Http()), JSONResponse=JSONResponse))
        older = asyncio.create_task(create(Request('older')))
        await entered['older'].wait()
        newer = asyncio.create_task(create(Request('newer')))
        await entered['newer'].wait()
        release['newer'].set()
        await newer
        current_context = state['market_context']
        release['older'].set()
        with self.assertRaises(HTTPException) as error:
            await older
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(state['live_session_id'], 'newer')
        self.assertIs(state['market_context'], current_context)
        self.assertFalse(current_context.closed)

    @unittest.skipUnless(shutil.which('node'), 'Browser harness requires Node.js')
    def test_browser_source_generations_and_failed_connection_retry(self):
        script = Path(__file__).with_name('test_browser_market_context.js')
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_queued_backend_request_cannot_restart_after_terminal_close(self):
        from unittest.mock import Mock
        context = MarketConversation((1, 2))
        context.begin_turn(client_turn=1)
        context.close()  # Logout/replacement before queued backend work starts.
        create = Mock(side_effect=AssertionError('No provider call after close'))
        backend = function('run_backend', dict(GTOP_GUILD_ID=1,
            client=NS(responses=NS(create=create))))
        result = backend([{'role': 'user', 'text': 'Open a trade'}], 2, context, 1)
        self.assertIn('superseded', result)
        create.assert_not_called()

    def test_auth_expiry_closes_detached_market_context(self):
        context = MarketConversation((1, 2))
        sessions = {'sid': {'expires_at': 10, 'market_context': context}}
        cleanup = function('_cleanup_auth_state', dict(time=NS(time=lambda: 11), AUTH_SESSIONS=sessions))
        cleanup()
        self.assertEqual(sessions, {})
        self.assertIsNone(context.begin_turn('stale queued request'))

    def test_backend_cancellation_during_call_blocks_later_unstarted_actions(self):
        from unittest.mock import Mock
        import time
        for first_name in ('review_market_session', 'get_trade_state'):
            with self.subTest(first_name=first_name):
                context = MarketConversation((1, 2))
                executed = []
                def dispatch(user_id, name, args, confirmation_token):
                    executed.append(name)
                    context.invalidate()  # New speech while this first call runs.
                    return evidence(args) if name == 'review_market_session' else {'ok': True}
                calls = [NS(type='function_call', name=first_name, arguments=json.dumps(NIGHT), call_id='first'),
                         NS(type='function_call', name='record_trade_event', arguments='{}', call_id='second')]
                create = Mock(return_value=NS(output=calls, output_text=''))
                backend = function('run_backend', dict(
                    PENDING_JOURNAL_DELETIONS={}, time=time, member_context=lambda _: '',
                    GTOP_GUILD_ID=1, client=NS(responses=NS(create=create)),
                    BACKEND_MODEL='offline-test', BACKEND_PROMPT='', TOOLS=[], json=json, run_tool=dispatch))
                result = backend([{'role': 'user', 'text': 'Review BTC 2026-10-02 night shift'}], 2, context, 1)
                self.assertIn('superseded', result)
                self.assertEqual(executed, [first_name])
                create.assert_called_once()

    async def test_browser_session_id_cannot_select_another_members_context(self):
        a, b = MarketConversation((1, 2)), MarketConversation((1, 3))
        auth = AsyncMock(return_value={'user_id': 2, 'live_session_id': 'session-a', 'market_context': a})
        run = AsyncMock(return_value='ok')
        ns = dict(require_authenticated_user=auth, HTTPException=HTTPException,
                  asyncio=NS(to_thread=run), run_backend=object())
        delegate = function('delegate', ns)
        with self.assertRaises(HTTPException) as error:
            await delegate(object(), NS(session_id='session-b', turn_id=1, history=[], delegation_id='d'))
        self.assertEqual(error.exception.status_code, 409)
        run.assert_not_awaited()
        with self.assertRaises(HTTPException):
            await delegate(object(), NS(session_id=None, turn_id=1, history=[], delegation_id='legacy'))
        run.assert_not_awaited()
        await delegate(object(), NS(session_id='session-a', turn_id=1, history=[], delegation_id='d'))
        self.assertIs(run.await_args.args[3], a)
        self.assertIsNone(b.selected)

    async def test_browser_cancel_only_current_authenticated_session(self):
        context = MarketConversation((1, 2))
        generation = context.begin_turn()
        state = {'user_id': 2, 'live_session_id': 'current', 'market_context': context}
        cancel = function('cancel_live_context', dict(require_authenticated_user=AsyncMock(return_value=state)))
        await cancel(object(), NS(session_id='other-member', turn_id=10, closed=True))
        self.assertTrue(context.current(generation))
        await cancel(object(), NS(session_id='current', turn_id=1, closed=False))
        self.assertFalse(context.current(generation))
        await cancel(object(), NS(session_id='current', turn_id=2, closed=True))
        self.assertNotIn('market_context', state)
        self.assertNotIn('live_session_id', state)

    async def test_voice_work_cancellation_invalidates_pending_not_verified_scope(self):
        context = MarketConversation((1, 2))
        context.begin_turn(); context.run('review_market_session', NIGHT, Provider())
        generation = context.generation
        session = NS(market_context=context, _voice_turn_count=1, websocket=object(), closed=False)
        work = VoiceToolWork(session)
        work.cancel()
        self.assertFalse(context.current(generation))
        self.assertEqual(context.selected['shift'], 'night')


if __name__ == '__main__':
    unittest.main()
