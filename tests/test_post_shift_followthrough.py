"""Synthetic post-cutoff candles only; no broker, account or live database."""
from copy import deepcopy
import json
import sqlite3
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time, stamp, summarize
from gbop_voice_web.post_shift_followthrough import (
    build_followthrough, continuation_candidates, TOOL_NAME,
)
from gbop_voice_web.voice_payload import voice_tool_payload
from test_chronological_context import synthetic, review, ny, DAY


def later(start='12:00', end='14:00', **values):
    return [dict(time=t, open=100, high=110, low=90, close=100, **values)
            for t in range(parse_time(ny(start)), parse_time(ny(end)), 60)]


def change(bars, clock, **values):
    next(b for b in bars if b['time'] == parse_time(ny(clock))).update(values)


class PostShiftFollowthroughTests(unittest.TestCase):
    def setUp(self):
        self.original = review()
        self.candidates = continuation_candidates(self.original, 'NAS100', 'NAS100m')
        self.selected = next(c for c in self.candidates if c['phase'] == 'double_purge')

    def follow(self, bars=None, **overrides):
        args = dict(asset='NAS100', symbol='NAS100m',
            anchor_start_ny=self.selected['anchor_start_ny'], phase=self.selected['phase'],
            expected_scope_id=self.selected['source_scope_id'],
            through=parse_time(ny('14:00')), as_of=parse_time(ny('14:00')))
        args.update(overrides)
        return build_followthrough(self.original, synthetic() + (later() if bars is None else bars), **args)

    def test_only_cutoff_qualified_pending_contexts_are_candidates(self):
        self.assertEqual([(c['anchor_start_ny'], c['phase']) for c in self.candidates], [
            (ny('08:00'), 'double_purge'), (ny('09:00'), 'original'), (ny('10:00'), 'original')])
        # The just-closed 11 AM range is under review, not a qualified pending CRT.
        result = self.follow(anchor_start_ny=ny('11:00'), phase='original')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'range_not_qualified_pending_at_shift_cutoff')
        # Nor may the already-delivered original 8 AM thesis become pending.
        self.assertFalse(self.follow(phase='original')['ok'])

    def test_later_data_never_requalifies_or_mutates_frozen_shift(self):
        frozen = deepcopy(self.original)
        bars = later()
        change(bars, '12:10', high=130, low=60, close=100)
        before = deepcopy(bars)
        result = self.follow(bars)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.original, frozen)
        self.assertEqual(bars, before)
        self.assertEqual(result['review']['frozen_cutoff']['phase_outcome'], 'midpoint_only')
        self.assertEqual(result['review']['frozen_cutoff']['original_directional_outcome'], 'opposing_liquidity_delivered')

    def test_actual_full_delivery_uses_later_source_interval_without_member_result(self):
        bars = later()
        change(bars, '12:15', high=120)
        result = self.follow(bars)['review']
        appendix = result['appendix']
        self.assertEqual(appendix['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(appendix['terminal'], {'kind': 'full_objective_delivered', 'known_at_ny': ny('12:16')})
        self.assertEqual(appendix['objectives']['full_objective']['evidence']['bar_open_ny'], ny('12:15'))
        self.assertFalse(appendix['objectives']['full_objective']['evidence']['exact_tick_time_known'])
        self.assertEqual(appendix['member_execution'], 'not_assessed')
        self.assertEqual(appendix['member_result'], 'not_assessed')
        self.assertEqual(result['frozen_cutoff']['tradeability_at_cutoff'], 'not_assessed')
        self.assertEqual(appendix['objectives']['midpoint']['status'], 'already_delivered_at_shift_cutoff')

    def test_first_terminal_delivery_cannot_be_undone_by_later_outside_close(self):
        bars = later()
        change(bars, '12:05', high=121)
        change(bars, '12:59', low=70, close=70)
        result = self.follow(bars)['review']['appendix']
        self.assertEqual(result['terminal']['known_at_ny'], ny('12:06'))
        self.assertEqual(result['status'], 'full_objective_delivered_after_cutoff')
        self.assertIsNone(result['invalidation'])
        self.assertNotIn('range_invalidated_after_cutoff', [e['kind'] for e in result['events']])

    def test_invalidation_stops_path_and_later_physical_hit_is_not_success(self):
        bars = later()
        change(bars, '12:59', low=79, close=79)
        change(bars, '13:10', high=125)
        result = self.follow(bars)['review']['appendix']
        self.assertEqual(result['status'], 'invalidated_after_cutoff_without_verified_full_delivery')
        self.assertEqual(result['terminal']['known_at_ny'], ny('13:00'))
        self.assertTrue(result['invalidation']['first_invalidation_verified'])
        self.assertEqual(result['objectives']['full_objective']['status'], 'physical_touch_after_range_invalidation')
        self.assertEqual(result['objectives']['full_objective']['evidence']['known_at_ny'], ny('13:11'))

    def test_same_invalidating_source_bar_touch_order_is_unresolved(self):
        bars = later()
        change(bars, '12:59', high=125, low=79, close=79)
        result = self.follow(bars)['review']['appendix']
        self.assertEqual(result['terminal']['kind'], 'range_invalidated')
        self.assertEqual(result['objectives']['full_objective']['status'], 'touch_in_invalidating_bar_order_unresolved')

    def test_invalidating_bar_touch_does_not_hide_distinct_later_physical_touch(self):
        bars = later()
        change(bars, '12:59', high=125, low=79, close=79)
        change(bars, '13:05', high=125)
        result = self.follow(bars)['review']['appendix']
        full = result['objectives']['full_objective']
        self.assertEqual(full['status'], 'touch_in_invalidating_bar_order_unresolved')
        self.assertEqual(full['post_invalidation_physical_touch']['known_at_ny'], ny('13:06'))
        self.assertEqual(result['terminal']['kind'], 'range_invalidated')
        self.assertIn('full_objective_physical_touch_after_invalidation', [e['kind'] for e in result['events']])

    def test_missing_bar_before_touch_does_not_prove_valid_delivery(self):
        bars = [b for b in later() if b['time'] != parse_time(ny('12:02'))]
        change(bars, '12:15', high=120)
        result = self.follow(bars)['review']['appendix']
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['missing_bar_count'], 1)
        self.assertEqual(result['status'], 'unverified_later_evidence')
        self.assertEqual(result['objectives']['full_objective']['status'], 'touch_validity_unverified')
        self.assertIsNone(result['terminal'])

    def test_missing_hour_before_invalidation_preserves_actual_close_without_first_claim(self):
        bars = [b for b in later() if b['time'] != parse_time(ny('12:02'))]
        change(bars, '13:59', low=79, close=79)
        result = self.follow(bars)['review']['appendix']
        self.assertEqual(result['invalidation']['known_at_ny'], ny('14:00'))
        self.assertFalse(result['invalidation']['first_invalidation_verified'])

    def test_later_coverage_gap_cannot_erase_earlier_verified_delivery(self):
        bars = [b for b in later() if b['time'] != parse_time(ny('13:02'))]
        change(bars, '12:15', high=120)
        result = self.follow(bars)['review']['appendix']
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['status'], 'full_objective_delivered_after_cutoff')

    def test_missing_all_later_data_is_bounded_unavailable_not_never(self):
        result = self.follow([])['review']
        self.assertEqual(result['appendix']['status'], 'unverified_later_evidence')
        self.assertEqual(result['appendix']['coverage']['bar_count'], 0)
        self.assertIsNone(result['appendix']['last_observed_bar_close_ny'])
        self.assertNotIn('never', result['spoken_summary'].lower())
        self.assertIn(ny('14:00'), result['spoken_summary'])

    def test_complete_bounded_non_delivery_is_pending_not_universal_failure(self):
        result = self.follow()['review']
        self.assertEqual(result['appendix']['status'], 'pending_at_followthrough_cutoff')
        self.assertIn('complete supplied candles', result['spoken_summary'])
        self.assertIn(ny('12:00'), result['spoken_summary'])
        self.assertIn(ny('14:00'), result['spoken_summary'])
        self.assertNotIn('never', result['spoken_summary'].lower())

    def test_future_and_partially_closed_candles_are_not_used(self):
        bars = later()
        change(bars, '13:00', high=125)
        result = self.follow(bars, through=parse_time(ny('13:00')))['review']['appendix']
        self.assertEqual(result['status'], 'pending_at_followthrough_cutoff')
        self.assertEqual(result['last_observed_bar_close_ny'], ny('13:00'))
        with self.assertRaisesRegex(ValueError, 'no later than now'):
            self.follow(through=parse_time(ny('15:00')))

    def test_exact_parent_phase_symbol_asset_and_cutoff_scope_are_bound(self):
        for values in ({'symbol': 'OtherBrokerSymbol'}, {'asset': 'SPX'}, {'expected_scope_id': 'x'},
                       {'anchor_start_ny': ny('09:00'), 'phase': 'original'}):
            result = self.follow(**values)
            self.assertFalse(result['ok'], values)
            self.assertEqual(result['status'], 'frozen_followthrough_scope_changed')
        row = self.original['shift_story']['ranges'][0]
        row['anchor']['open'] += 1
        self.assertEqual(self.follow()['status'], 'frozen_followthrough_scope_changed')

    def test_changed_initial_purge_identity_invalidates_original_scope_digest(self):
        original = next(c for c in self.candidates if c['anchor_start_ny'] == ny('09:00'))
        bars = synthetic()
        change(bars, '10:01', low=76, close=76)
        modified = review(bars)
        changed = next(c for c in continuation_candidates(modified, 'NAS100', 'NAS100m')
                       if c['anchor_start_ny'] == ny('09:00'))
        self.assertNotEqual(original['source_scope_id'], changed['source_scope_id'])

    def test_night_shift_midnight_continuation_keeps_previous_shift_date(self):
        shifted = [{**b, 'time': b['time'] + 12 * 3600} for b in synthetic()]
        frozen = market.session_review(shifted, DAY, 'night', 60)
        candidate = next(c for c in continuation_candidates(frozen, 'NAS100', 'NAS100m')
                         if c['phase'] == 'double_purge')
        after = [{**b, 'time': b['time'] + 12 * 3600} for b in later()]
        after[15]['high'] = 120
        result = build_followthrough(frozen, shifted + after, asset='NAS100', symbol='NAS100m',
            anchor_start_ny=candidate['anchor_start_ny'], phase=candidate['phase'],
            expected_scope_id=candidate['source_scope_id'], through=parse_time(ny('14:00')) + 12 * 3600,
            as_of=parse_time(ny('14:00')) + 12 * 3600)['review']
        self.assertEqual(result['frozen_cutoff']['date_ny'], DAY)
        self.assertEqual(result['frozen_cutoff']['cutoff_ny'], '2026-09-18T00:00:00-04:00')
        self.assertEqual(result['appendix']['terminal']['known_at_ny'], '2026-09-18T00:16:00-04:00')

    def test_duplicate_or_nonfinite_source_evidence_is_rejected(self):
        bars = later()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.follow(bars + [deepcopy(bars[0])])
        bars[0]['high'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'malformed'):
            self.follow(bars)

    def test_native_source_conflict_blocks_touch_validity(self):
        bars = later()
        change(bars, '12:15', high=120)
        native = [dict(time=parse_time(ny('12:00')), open=100, high=130, low=90, close=100,
            provenance={'source': 'MT5', 'timeframe': 'H1', 'method': 'copy_rates_from_pos',
                        'asset': 'NAS100', 'symbol': 'NAS100m', 'captured_at': parse_time(ny('14:00'))})]
        result = self.follow(bars, native_h1=native)['review']['appendix']
        self.assertEqual(result['status'], 'unverified_later_evidence')
        self.assertEqual(result['hourly_evidence_conflict']['start_ny'], ny('12:00'))
        self.assertEqual(result['objectives']['full_objective']['status'], 'touch_validity_unverified')

    def test_native_h1_close_proves_invalidation_without_fabricating_source_path(self):
        native = [dict(time=parse_time(ny('12:00')), open=100, high=110, low=79, close=79,
            provenance={'source': 'MT5', 'timeframe': 'H1', 'method': 'copy_rates_from_pos',
                        'asset': 'NAS100', 'symbol': 'NAS100m', 'captured_at': parse_time(ny('13:00'))})]
        result = self.follow([], through=parse_time(ny('13:00')), native_h1=native)['review']['appendix']
        self.assertEqual(result['terminal']['known_at_ny'], ny('13:00'))
        self.assertTrue(result['invalidation']['first_invalidation_verified'])
        self.assertFalse(result['invalidation']['source_path_through_close_complete'])
        self.assertEqual(result['objectives']['full_objective']['status'], 'unverified_incomplete_later_coverage')

    def test_transport_helper_preserves_frozen_selection_and_discards_barge_in(self):
        from gbop_voice_web.market_conversation import MarketConversation
        from gbop_voice_web.post_shift_followthrough import run_context_followthrough
        context = MarketConversation()
        context.begin_turn()
        context.selected = {'asset': 'NAS100', 'through_ny': ny('12:00'), 'anchor_start_ny': ny('08:00')}
        original = deepcopy(context.selected)
        result = run_context_followthrough(context, {}, lambda n, a: self.follow(), context.generation)
        self.assertTrue(result['ok'])
        self.assertEqual(context.selected, original)
        def interrupted(name, args):
            context.invalidate()
            return self.follow()
        result = run_context_followthrough(context, {}, interrupted, context.generation)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(context.selected, original)


class PostShiftToolTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        self.db = lambda: self.conn

    def tearDown(self):
        self.conn.close()

    def seed(self, bars, capture, coarse=None, symbol='NAS100m', native=None):
        payload = dict(asset='NAS100', symbol=symbol, bid=100, ask=101, tick_time=capture,
                       bars=coarse or [], bars_m1=bars)
        if native is not None:
            payload.update(bars_h1=native, native_h1_source=market.NATIVE_H1_SOURCE)
        self.conn.execute('INSERT OR REPLACE INTO gbop_market_feed VALUES (?,?,?,?)',
            ('NAS100', capture, capture, json.dumps(payload)))

    def original(self, now):
        result = market.market_tool(self.db, 'review_market_session',
            {'asset': 'NAS100', 'date_ny': DAY, 'shift': 'day'}, now=now)
        self.assertTrue(result['ok'], result)
        candidate = next(c for c in result['post_shift_followthrough']['candidates'] if c['phase'] == 'double_purge')
        args = dict(asset='NAS100', date_ny=DAY, shift='day',
                    anchor_start_ny=candidate['anchor_start_ny'], phase=candidate['phase'],
                    expected_scope_id=candidate['source_scope_id'], through_ny=None)
        return result, args

    def test_tool_registered_optional_horizon_and_read_only_end_to_end(self):
        # Fix the clock and every input; the SQLite snapshot acts only as a feed.
        bars = synthetic() + later()
        change(bars, '12:15', high=120)
        self.seed(bars, parse_time(ny('14:00')))
        original, args = self.original(parse_time(ny('14:00')))
        before = list(self.conn.iterdump())
        result = market.market_tool(self.db, TOOL_NAME, args, now=parse_time(ny('14:00')))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['review']['appendix']['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertIn(TOOL_NAME, market.MARKET_NAMES)
        tool = next(t for t in market.MARKET_TOOLS if t['name'] == TOOL_NAME)
        self.assertIn('null', tool['parameters']['properties']['through_ny']['type'])
        wire = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100',
            'review': review(), 'post_shift_followthrough': original['post_shift_followthrough']})
        self.assertTrue(wire['ok'], wire)
        # Optional navigation can be omitted to preserve the original synopsis budget.
        args['expected_scope_id'] = None
        ready = market.market_tool(self.db, TOOL_NAME, args, now=parse_time(ny('14:00')))
        self.assertEqual(ready['status'], 'frozen_followthrough_ready')
        self.assertEqual(ready['next_arguments']['expected_scope_id'], result['review']['frozen_cutoff']['source_scope_id'])
        appendix_wire = voice_tool_payload(TOOL_NAME, result)
        self.assertEqual(appendix_wire['review']['frozen_cutoff'], result['review']['frozen_cutoff'])

    def test_later_feed_capture_and_future_horizon_do_not_leak_into_cutoff(self):
        bars = synthetic() + later()
        change(bars, '13:15', high=125)
        self.seed(bars, parse_time(ny('13:00')))
        _, args = self.original(parse_time(ny('14:00')))
        result = market.market_tool(self.db, TOOL_NAME, args, now=parse_time(ny('14:00')))
        self.assertTrue(result['ok'], result)
        appendix = result['review']['appendix']
        self.assertEqual(appendix['last_observed_bar_close_ny'], ny('13:00'))
        self.assertEqual(appendix['status'], 'pending_at_followthrough_cutoff')
        self.assertEqual(appendix['through_ny'], ny('13:00'))
        self.assertEqual(appendix['as_of_ny'], ny('14:00'))
        self.assertIn('captured_at_utc', result['source_snapshot'])
        args['through_ny'] = ny('15:00')
        self.assertFalse(market.market_tool(self.db, TOOL_NAME, args, now=parse_time(ny('14:00')))['ok'])

    def test_explicit_horizon_override_is_respected(self):
        bars = synthetic() + later()
        change(bars, '13:15', high=125)
        self.seed(bars, parse_time(ny('14:00')))
        _, args = self.original(parse_time(ny('14:00')))
        args['through_ny'] = ny('13:00')
        result = market.market_tool(self.db, TOOL_NAME, args, now=parse_time(ny('14:00')))
        self.assertEqual(result['review']['appendix']['status'], 'pending_at_followthrough_cutoff')
        self.assertEqual(result['review']['appendix']['through_ny'], ny('13:00'))

    def test_default_horizon_uses_latest_complete_frozen_precision_candle(self):
        source = synthetic() + later(end='12:05')
        coarse = []
        for start in range(source[0]['time'], parse_time(ny('12:05')), 300):
            value = summarize(source, start, start + 300, 60)
            coarse.append(dict(time=start, **{k: value[k] for k in ('open', 'high', 'low', 'close')}))
        now = parse_time(ny('12:07'))
        self.seed([], now, coarse=coarse)
        _, args = self.original(now)
        result = market.market_tool(self.db, TOOL_NAME, args, now=now)
        self.assertTrue(result['ok'], result)
        appendix = result['review']['appendix']
        self.assertEqual(appendix['through_ny'], ny('12:05'))
        self.assertEqual(appendix['as_of_ny'], ny('12:07'))
        self.assertTrue(appendix['coverage']['complete'])
        self.assertEqual(appendix['horizon_basis'], 'latest_available_closed_source')
        self.assertEqual(appendix['status'], 'pending_at_followthrough_cutoff')

    def test_default_with_no_later_closed_bar_has_no_fabricated_horizon(self):
        now = parse_time(ny('12:07'))
        self.seed(synthetic(), now)
        _, args = self.original(now)
        result = market.market_tool(self.db, TOOL_NAME, args, now=now)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['review']['appendix']['through_ny'], ny('12:00'))
        self.assertIsNone(result['review']['appendix']['last_observed_bar_close_ny'])
        self.assertEqual(result['review']['appendix']['status'], 'unverified_later_evidence')

    def test_default_horizon_includes_actual_native_h1_beyond_source_tail(self):
        now = parse_time(ny('14:00'))
        native = [dict(time=parse_time(ny('12:00')), open=100, high=110, low=79, close=79)]
        self.seed(synthetic(), now, native=native)
        _, args = self.original(now)
        result = market.market_tool(self.db, TOOL_NAME, args, now=now)
        self.assertTrue(result['ok'], result)
        appendix = result['review']['appendix']
        self.assertEqual(appendix['through_ny'], ny('13:00'))
        self.assertEqual(appendix['terminal']['known_at_ny'], ny('13:00'))
        self.assertTrue(appendix['invalidation']['first_invalidation_verified'])
        self.assertFalse(appendix['coverage']['complete'])
        self.assertIsNone(appendix['objectives']['full_objective']['evidence'])


class ProductionDispatchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.CREATE_SQL)
        self.conn.execute(market.HISTORY_SQL)
        self.now = parse_time(ny('14:00'))
        bars = synthetic() + later()
        change(bars, '12:15', high=120)
        for asset in ('NAS100', 'XAUUSD'):
            payload = dict(asset=asset, symbol=asset + 'm', bid=100, ask=101,
                           tick_time=self.now, bars=[], bars_m1=bars)
            self.conn.execute('INSERT INTO gbop_market_feed VALUES (?,?,?,?)',
                (asset, self.now, self.now, json.dumps(payload)))
        self.calls = []

    def tearDown(self):
        self.conn.close()

    def runner(self, name, args):
        self.calls.append((name, deepcopy(args)))
        return market.market_tool(lambda: self.conn, name, args, now=self.now)

    def arguments(self, asset='NAS100', anchor='08:00'):
        candidate = next(c for c in continuation_candidates(review(), asset, asset + 'm')
                         if c['anchor_start_ny'] == ny(anchor))
        return dict(asset=asset, date_ny=DAY, shift='day', through_ny=None,
                    anchor_start_ny=candidate['anchor_start_ny'], phase=candidate['phase'],
                    expected_scope_id=candidate['source_scope_id'])

    def context(self, text='Did that range reach its target after the shift?'):
        from gbop_voice_web.market_conversation import MarketConversation
        context = MarketConversation((10, 20, 'real-postshift-dispatch'))
        context.selected = dict(asset='NAS100', date_ny=DAY, shift='day',
            through_ny=ny('12:00'), anchor_start_ny=ny('08:00'), anchor_timeframe='H1')
        context.requested = deepcopy(context.selected)
        context.begin_turn(text, now=self.now)
        return context

    def test_real_dispatch_rejects_other_asset_date_shift_and_focused_anchor_before_read(self):
        for null_hash in (False, True):
            for values in (self.arguments('XAUUSD'),
                           {**self.arguments(), 'date_ny': '2026-09-16'},
                           {**self.arguments(), 'shift': 'night'}, self.arguments(anchor='09:00')):
                with self.subTest(null_hash=null_hash, values=values):
                    context = self.context()
                    if null_hash:
                        values['expected_scope_id'] = None
                    result = context.run(TOOL_NAME, values, self.runner)
                    self.assertEqual(result['status'], 'market_context_mismatch')
                    self.assertEqual(context.selected['asset'], 'NAS100')
                    self.assertEqual(context.selected['through_ny'], ny('12:00'))
        self.assertEqual(self.calls, [])

    def test_real_dispatch_same_scope_lookup_and_append_preserve_original_selection(self):
        context = self.context()
        before = deepcopy((context.selected, context.requested, context.evidence))
        database = list(self.conn.iterdump())
        args = {**self.arguments(), 'expected_scope_id': None}
        ready = context.run(TOOL_NAME, args, self.runner)
        self.assertEqual(ready['status'], 'frozen_followthrough_ready')
        result = context.run(TOOL_NAME, ready['next_arguments'], self.runner)
        self.assertEqual(result['review']['appendix']['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual((context.selected, context.requested, context.evidence), before)
        self.assertEqual(list(self.conn.iterdump()), database)

    def test_real_dispatch_pending_typed_switch_requires_matching_selected_evidence(self):
        context = self.context(f'Review Gold {DAY} day shift')
        for args in (self.arguments(), self.arguments('XAUUSD')):
            args['expected_scope_id'] = None
            result = context.run(TOOL_NAME, args, self.runner)
            self.assertEqual(result['status'], 'market_context_selection_required')
        self.assertEqual(self.calls, [])
        selected = context.run('review_market_session', dict(asset='XAUUSD', date_ny=DAY,
            shift='day', context_action='switch'), self.runner)
        self.assertTrue(selected['ok'], selected)
        result = context.run(TOOL_NAME, self.arguments('XAUUSD'), self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['asset'], 'XAUUSD')
        self.assertEqual(context.selected['through_ny'], ny('12:00'))

    def test_real_dispatch_audio_selection_phase_and_unresolved_focus_cannot_be_bypassed(self):
        context = self.context(None)
        self.assertEqual(context.run(TOOL_NAME, self.arguments('XAUUSD'), self.runner)['status'],
                         'market_context_mismatch')
        context.selected['phase'] = context.requested['phase'] = 'double_purge'
        wrong = {**self.arguments(), 'phase': 'original', 'expected_scope_id': None}
        self.assertEqual(context.run(TOOL_NAME, wrong, self.runner)['status'], 'market_context_mismatch')
        for field, value in (('_multi_focus_required', True), ('_comparison_asset_ambiguous', True),
                             ('_multi_request', {'requests': []}), ('_required_current', True),
                             ('_scan_request', {})):
            for null_hash in (False, True):
                context = self.context()
                setattr(context, field, value)
                args = self.arguments()
                if null_hash:
                    args['expected_scope_id'] = None
                self.assertEqual(context.run(TOOL_NAME, args, self.runner)['status'],
                                 'market_context_selection_required')
        self.assertEqual(self.calls, [])

    def test_real_dispatch_standalone_initial_lookup_and_barge_in(self):
        from gbop_voice_web.market_conversation import MarketConversation
        for text in (None, f'Follow Gold {DAY} day shift after the cutoff'):
            context = MarketConversation((10, 20, 'standalone-postshift'))
            context.begin_turn(text, now=self.now)
            ready = context.run(TOOL_NAME, {**self.arguments('XAUUSD'), 'expected_scope_id': None}, self.runner)
            self.assertEqual(ready['status'], 'frozen_followthrough_ready')
            self.assertIsNone(context.selected)
        context = self.context()
        def interrupted(name, args):
            result = self.runner(name, args)
            context.begin_turn('Change the topic.', now=self.now)
            return result
        result = context.run(TOOL_NAME, self.arguments(), interrupted)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertNotIn('review', result)

    def test_market_conversation_dispatch_preserves_cutoff_and_fences_late_result(self):
        from gbop_voice_web.market_conversation import MarketConversation
        from gbop_voice_web.voice_runtime import READ_ONLY_RECOVERY_NAMES
        context=MarketConversation((10,20,'postshift-dispatch'))
        context.begin_turn('Read the later outcome.')
        context.selected={'asset':'NAS100','date_ny':'2026-10-02','shift':'day','through_ny':'2026-10-02T12:00:00-04:00'}
        context.requested=deepcopy(context.selected)
        before=deepcopy(context.selected)
        result=context.run('review_post_shift_followthrough',{},lambda name,args:{'ok':True,'bounded':True})
        self.assertTrue(result['ok']);self.assertEqual(context.selected,before)
        def interrupted(name,args):
            context.begin_turn('Change the topic.')
            return {'ok':True,'late_evidence':True}
        stale=context.run('review_post_shift_followthrough',{},interrupted)
        self.assertEqual(stale['status'],'stale_market_context')
        self.assertNotIn('late_evidence',stale)
        self.assertIn('review_post_shift_followthrough',READ_ONLY_RECOVERY_NAMES)


if __name__ == '__main__':
    unittest.main()
